"""Regulatory requirement and traceability engine (Source -> RequirementVersion -> Provision -> ObligationDefinition ->
RuleVersion), on the existing catalogue schema (no migration).

Contract:

* ZA-GLS-001 §04 regulatory content lifecycle: the only lifecycle moves are the ones in ``TRANSITIONS``; every move is
  recorded as an append-only audit record (platform_audit_event, trigger-protected since 0002/0003), so a version's
  lifecycle history is reconstructable (DOM-INV-15). The audit table is the history store until catalogue lifecycle
  events get their own table (roadmap Stage 3c, audit I-04).
* ZA-DOM-001 §4/§13: RegulatorySource 1:N SourceSnapshot (immutable, digest); Requirement 1:N RequirementVersion;
  RequirementVersion 1:N Provision; Provision -> ObligationDefinition explicit links. In the physical model (ZA-DAT-001
  §05) a ``catalog_source_record`` row is one snapshot (its fingerprint), and the source's stable identity is its
  authority + jurisdiction + authoritative locator (DOM §4.1 "Authority body ... canonical URI/reference,
  jurisdiction"); a requirement's stable identity is ``requirement_key`` within one instrument.
* ZA-DAT-001 §05: "Fingerprint + authoritative reference required before VERIFIED/PUBLISHED content";
  requirement_version "exact source fingerprint FK". ARCH INV-06 / PRD FR-REG-001: every released provision links to an
  authoritative source reference and fingerprint ("FK + non-null release gate"). INV-09 / FR-REG-002: AI-extracted
  content cannot be released without a VERIFIED human transition.
* ZA-RUL-001 §02: a ContentRelease is the signed package of RequirementVersion(s) and RuleVersion(s); §08 source
  mappings and "release tooling must show coverage and orphaned mappings"; §11 stage 2 "source mappings resolve".
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from ..models import (ContentRelease, FactDefinition, Instrument, ObligationDefinition, Provision, RegulatorySource,
                      RequirementVersion, RuleVersion)
from ..platform import audit, emit, next_record_seq, sha256_hex, to_dict

# ZA-GLS-001 §04: machine code -> allowed next states.
TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVERED": frozenset({"INGESTED", "WITHDRAWN"}),
    "INGESTED": frozenset({"CLASSIFIED", "WITHDRAWN"}),
    "CLASSIFIED": frozenset({"AI_EXTRACTED", "REVIEW_REQUIRED"}),
    "AI_EXTRACTED": frozenset({"REVIEW_REQUIRED"}),
    "REVIEW_REQUIRED": frozenset({"VERIFIED", "WITHDRAWN"}),
    "VERIFIED": frozenset({"PUBLISHED", "REVIEW_REQUIRED"}),
    "PUBLISHED": frozenset({"SUPERSEDED", "WITHDRAWN"}),
    "SUPERSEDED": frozenset(),
    "WITHDRAWN": frozenset({"REVIEW_REQUIRED"}),
}
RELEASABLE = frozenset({"VERIFIED", "PUBLISHED"})
# A source can ground new requirement content once classified, and until it is withdrawn or superseded.
DERIVABLE_SOURCE = frozenset({"CLASSIFIED", "AI_EXTRACTED", "REVIEW_REQUIRED", "VERIFIED", "PUBLISHED"})
EDITABLE_PROVISION = frozenset({"AI_EXTRACTED", "REVIEW_REQUIRED"})
UNREVIEWED = "TBD_BY_REVIEWER"


def _conflict(code: str, message: str, **detail: Any) -> HTTPException:
    return HTTPException(409, {"error": code, "message": message, **detail})


# ---------------------------------------------------------------- lifecycle (GLS-001 §04)
def move(db, obj, *, object_type: str, object_id: str, to: str, actor, action: str, reason: str | None = None,
         record_seq: int | None = None) -> bool:
    """Apply one allowed lifecycle transition and record it. False when `obj` is already in `to` (no record)."""
    prior = obj.lifecycle
    if prior == to:
        return False
    if to not in TRANSITIONS.get(prior, frozenset()):
        raise _conflict("CONTENT_TRANSITION_NOT_ALLOWED",
                        f"{object_type} {object_id} cannot move from {prior} to {to} (ZA-GLS-001 §04).",
                        object_type=object_type, object_id=object_id, **{"from": prior, "to": to},
                        allowed=sorted(TRANSITIONS.get(prior, frozenset())))
    obj.lifecycle = to
    audit(db, action=action, object_type=object_type, object_id=object_id, actor=actor, prior=prior, new=to,
          reason=reason, record_seq=record_seq)
    return True


# ---------------------------------------------------------------- sources
def fingerprint_intact(s: RegulatorySource) -> bool:
    """The stored fingerprint still matches the stored text (sources whose text is not stored are checked by
    reference only)."""
    return s.raw_text is None or sha256_hex(s.raw_text) == s.fingerprint


def grounded(s: RegulatorySource | None) -> bool:
    """DAT §05: fingerprint and authoritative reference present."""
    return s is not None and bool((s.fingerprint or "").strip()) and bool((s.locator or "").strip())


def source_versions(db, s: RegulatorySource) -> list[RegulatorySource]:
    """Every snapshot of the same source (authority + jurisdiction + authoritative locator), oldest first."""
    return db.execute(select(RegulatorySource).where(
        RegulatorySource.authority == s.authority, RegulatorySource.jurisdiction == s.jurisdiction,
        RegulatorySource.locator == s.locator).order_by(RegulatorySource.record_seq, RegulatorySource.source_id)).scalars().all()


def source_ref(s: RegulatorySource | None) -> dict | None:
    """The source facts a trace needs: identity, snapshot digest, rights and lifecycle; never the text itself."""
    if s is None:
        return None
    return {"source_id": s.source_id, "authority": s.authority, "jurisdiction": s.jurisdiction, "title": s.title,
            "source_type": s.source_type, "locator": s.locator, "fingerprint": s.fingerprint,
            "fingerprint_intact": fingerprint_intact(s), "rights_class": s.rights_class, "lifecycle": s.lifecycle,
            "publication_date": s.publication_date, "effective_date": s.effective_date, "record_seq": s.record_seq}


def source_view(s: RegulatorySource) -> dict:
    """The full source record, with text withheld where the rights record does not permit display (FR-REG-005)."""
    d = to_dict(s)
    if s.rights_class == "REFERENCE_ONLY":  # the same rule GET /catalog/sources applies
        d["raw_text"] = None
        d["display_note"] = "Text withheld under rights record; locator and fingerprint retained."
    d["fingerprint_intact"] = fingerprint_intact(s)
    return d


def find_duplicate_snapshot(db, *, authority: str, jurisdiction: str, locator: str,
                            fingerprint: str) -> RegulatorySource | None:
    return db.execute(select(RegulatorySource).where(
        RegulatorySource.authority == authority, RegulatorySource.jurisdiction == jurisdiction,
        RegulatorySource.locator == locator, RegulatorySource.fingerprint == fingerprint,
        RegulatorySource.lifecycle != "WITHDRAWN")).scalars().first()


def require_derivable(s: RegulatorySource | None) -> RegulatorySource:
    """A source new requirement content may be grounded in: classified, not withdrawn or superseded, derivation
    permitted by its rights record, fingerprint and reference present and intact."""
    if s is None:
        raise HTTPException(404, "source not found")
    if s.lifecycle not in DERIVABLE_SOURCE:
        code = "SOURCE_NOT_CLASSIFIED" if s.lifecycle in ("DISCOVERED", "INGESTED") else "SOURCE_NOT_USABLE"
        raise _conflict(code, f"Source {s.source_id} is {s.lifecycle}; requirement content needs a CLASSIFIED source "
                              "that is not withdrawn or superseded (ZA-GLS-001 §04).", source_id=s.source_id)
    if not (s.rights_record or {}).get("derive", True):
        raise _conflict("SOURCE_RIGHTS_PROHIBIT_DERIVATION", "Rights record prohibits derivation", source_id=s.source_id)
    if not grounded(s):
        raise _conflict("SOURCE_NOT_GROUNDED", "The source has no fingerprint or authoritative reference (ZA-DAT-001 §05).",
                        source_id=s.source_id)
    if not fingerprint_intact(s):
        raise _conflict("SOURCE_FINGERPRINT_MISMATCH", "The stored source text no longer matches its fingerprint.",
                        source_id=s.source_id)
    return s


def classify(ctx, s: RegulatorySource, instrument: Instrument) -> None:
    """INGESTED -> CLASSIFIED against an existing instrument (GLS §04: "Instrument, jurisdiction, authority and source
    type classified"). The schema has no source -> instrument column, so the classification is recorded in the audit
    record; requirement versions carry the binding instrument_id."""
    move(ctx.db, s, object_type="RegulatorySource", object_id=s.source_id, to="CLASSIFIED", actor=ctx.actor,
         action="source.classify", reason=f"Classified under instrument {instrument.identifier} "
                                          f"({instrument.instrument_id}, {instrument.jurisdiction})")


# ---------------------------------------------------------------- requirement versions
def versions_of(db, requirement_key: str) -> list[RequirementVersion]:
    return db.execute(select(RequirementVersion).where(RequirementVersion.requirement_key == requirement_key)
                      .order_by(RequirementVersion.record_seq, RequirementVersion.requirement_version_id)).scalars().all()


def version_number(db, rv: RequirementVersion) -> int:
    ids = [v.requirement_version_id for v in versions_of(db, rv.requirement_key)]
    return ids.index(rv.requirement_version_id) + 1


def lock_instrument(db, identifier: str) -> Instrument:
    """The instrument, row-locked: creations of versions under one instrument are serialised, so the identity and
    duplicate checks below cannot race (PostgreSQL; SQLite serialises writers anyway)."""
    inst = db.execute(select(Instrument).where(Instrument.identifier == identifier).with_for_update()).scalars().first()
    if inst is None:
        raise HTTPException(404, "instrument not found")
    return inst


def check_identity(db, *, requirement_key: str, instrument: Instrument, source: RegulatorySource, locator: str,
                   valid_from: date) -> None:
    """A requirement's stable identity belongs to one instrument (DOM §4.1, §13); the same version is not recorded
    twice (same source snapshot, locator and effective date, not withdrawn)."""
    rows = versions_of(db, requirement_key)
    elsewhere = sorted({r.instrument_id for r in rows if r.instrument_id != instrument.instrument_id})
    if elsewhere:
        raise _conflict("REQUIREMENT_KEY_CONFLICT", f"Requirement {requirement_key} belongs to another instrument; a "
                                                    "requirement identity cannot span instruments.",
                        requirement_key=requirement_key, instrument_ids=elsewhere)
    dup = [r for r in rows if r.source_id == source.source_id and r.locator == locator and r.valid_from == valid_from
           and r.lifecycle != "WITHDRAWN"]
    if dup:
        raise _conflict("DUPLICATE_REQUIREMENT_VERSION", f"Requirement {requirement_key} already has this version (same "
                                                         "source snapshot, locator and effective date).",
                        requirement_version_id=dup[0].requirement_version_id)


def create_version(ctx, *, source: RegulatorySource, instrument: Instrument, requirement_key: str, title: str,
                   locator: str, summary: str | None, valid_from: date, valid_to: date | None, provisions: list[dict],
                   origin: str, provision_origin: str, provision_lifecycle: str) -> tuple[RequirementVersion, list[Provision]]:
    """A new RequirementVersion in REVIEW_REQUIRED with its provisions; earlier versions are never touched (DOM §1.1:
    revisions are immutable once closed). Validation, lock, identity and duplicate checks first; one transaction."""
    db = ctx.db
    require_derivable(source)
    if valid_to is not None and valid_to <= valid_from:
        raise HTTPException(422, "valid_to must be later than valid_from (half-open legal date range).")
    check_identity(db, requirement_key=requirement_key, instrument=instrument, source=source, locator=locator,
                   valid_from=valid_from)
    seq = next_record_seq(db)
    rv = RequirementVersion(requirement_key=requirement_key, instrument_id=instrument.instrument_id,
                            source_id=source.source_id, title=title, locator=locator, summary=summary,
                            valid_from=valid_from, valid_to=valid_to, lifecycle="REVIEW_REQUIRED", origin=origin,
                            record_seq=seq)
    db.add(rv)
    db.flush()
    made = []
    for p in provisions:
        prov = Provision(requirement_version_id=rv.requirement_version_id, semantic_origin=provision_origin,
                         lifecycle=provision_lifecycle, **p)
        db.add(prov)
        made.append(prov)
    db.flush()
    if source.lifecycle == "CLASSIFIED":
        move(db, source, object_type="RegulatorySource", object_id=source.source_id, to="REVIEW_REQUIRED",
             actor=ctx.actor, action="source.lifecycle", record_seq=seq,
             reason=f"Requirement content proposed from this source ({requirement_key})")
    n = version_number(db, rv)
    audit(db, action="requirement.create", object_type="RequirementVersion", object_id=rv.requirement_version_id,
          actor=ctx.actor, new="REVIEW_REQUIRED", record_seq=seq,
          reason=(f"{origin} version {n} of {requirement_key} under {instrument.identifier}, grounded in source "
                  f"{source.source_id} (fingerprint {source.fingerprint}) at {locator}; {len(made)} provision(s)")[:2000])
    return rv, made


def lock_version(db, rv_id: str) -> RequirementVersion:
    rv = db.execute(select(RequirementVersion).where(RequirementVersion.requirement_version_id == rv_id)
                    .with_for_update()).scalars().first()
    if rv is None:
        raise HTTPException(404, "requirement version not found")
    return rv


def verify(ctx, rv_id: str) -> RequirementVersion:
    """Qualified human review: REVIEW_REQUIRED -> VERIFIED for the version and all its provisions (INV-09, FR-REG-002).
    Row-locked, so concurrent reviews of one version give exactly one transition."""
    db = ctx.db
    rv = lock_version(db, rv_id)
    if rv.lifecycle != "REVIEW_REQUIRED":
        raise _conflict("CONTENT_TRANSITION_NOT_ALLOWED", f"Cannot verify from {rv.lifecycle}",
                        object_type="RequirementVersion", object_id=rv_id, **{"from": rv.lifecycle, "to": "VERIFIED"})
    src = db.get(RegulatorySource, rv.source_id)
    if not grounded(src) or src.lifecycle in ("WITHDRAWN", "SUPERSEDED") or not fingerprint_intact(src):
        raise _conflict("SOURCE_NOT_GROUNDED", "Verified content needs an intact, current source snapshot with a "
                                               "fingerprint and authoritative reference (ZA-DAT-001 §05, FR-REG-001).",
                        source_id=rv.source_id)
    provs = db.execute(select(Provision).where(Provision.requirement_version_id == rv_id)
                       .order_by(Provision.locator, Provision.provision_id)).scalars().all()
    if not provs:
        raise _conflict("REQUIREMENT_HAS_NO_PROVISIONS", "A requirement version is verified with its atomic provisions "
                                                         "(DOM §13: RequirementVersion 1:N ProvisionVersion).")
    unreviewed = [p.provision_id for p in provs if p.lifecycle != "REVIEW_REQUIRED"
                  or UNREVIEWED in (p.duty_bearer, p.regulated_object)]
    if unreviewed:
        raise _conflict("PROVISIONS_NOT_REVIEWED", "All proposed provisions must be reviewed/encoded (duty bearer etc.) "
                                                   "before VERIFIED; AI-extracted proposals are never verified directly "
                                                   "(INV-09).", provision_ids=unreviewed)
    seq = next_record_seq(db)
    move(db, rv, object_type="RequirementVersion", object_id=rv_id, to="VERIFIED", actor=ctx.actor,
         action="requirement.verify", record_seq=seq)
    rv.reviewed_by = ctx.actor.actor_id
    for p in provs:
        move(db, p, object_type="Provision", object_id=p.provision_id, to="VERIFIED", actor=ctx.actor,
             action="provision.lifecycle", record_seq=seq, reason=f"Verified with requirement version {rv_id}")
    return rv


PROVISION_FIELDS = ("duty_bearer", "modality", "action", "regulated_object", "timing", "normalized_meaning",
                    "evidence_expectation")


def revise_provision(ctx, provision_id: str, values: dict, reason: str | None) -> Provision:
    """Edit a provision under review. Each edit is recorded with the prior values of the fields it changed, so the
    draft's change history is kept (DOM-INV-15); verified and published provisions are never edited in place."""
    db = ctx.db
    p = db.execute(select(Provision).where(Provision.provision_id == provision_id).with_for_update()).scalars().first()
    if p is None:
        raise HTTPException(404, "provision not found")
    rv = db.get(RequirementVersion, p.requirement_version_id)
    if p.lifecycle not in EDITABLE_PROVISION or (rv is not None and rv.lifecycle != "REVIEW_REQUIRED"):
        raise _conflict("PROVISION_NOT_EDITABLE", "Released/verified provisions are immutable; create a new version",
                        lifecycle=p.lifecycle, requirement_lifecycle=rv.lifecycle if rv else None)
    changed = {k: getattr(p, k) for k in PROVISION_FIELDS if values.get(k) != getattr(p, k)}
    seq = next_record_seq(db)
    for k in PROVISION_FIELDS:
        setattr(p, k, values.get(k))
    p.semantic_origin = "ZOIKO_ENCODED"
    move(db, p, object_type="Provision", object_id=provision_id, to="REVIEW_REQUIRED", actor=ctx.actor,
         action="provision.lifecycle", record_seq=seq, reason="Proposal reviewed and encoded by a content author")
    import json
    audit(db, action="provision.revise", object_type="Provision", object_id=provision_id, actor=ctx.actor,
          new=p.lifecycle, record_seq=seq,
          reason=json.dumps({"changed": sorted(changed), "prior": {k: (v[:500] if isinstance(v, str) else v)
                                                                   for k, v in changed.items()},
                             "reason": reason}, sort_keys=True)[:4000])
    return p


# ---------------------------------------------------------------- release binding (INV-06, INV-09, RUL §02)
# The digest that lets a verifier detect a released requirement version or provision whose text was changed afterwards
# (DAT-REQ-012; until migration 0014 the database does not stop that). Same convention as the manifest's `ast_sha256`:
# SHA-256 (the field name carries the algorithm, RUL §12) over the platform canonical JSON profile v1. `CONTENT_PROFILE`
# versions the hashed field set; lifecycle codes and the release reference are excluded because publication and
# supersession change them by design.
CONTENT_PROFILE = "za-requirement-content-v1"
REQUIREMENT_CONTENT_FIELDS = ("requirement_version_id", "requirement_key", "instrument_id", "source_id", "title",
                              "locator", "summary", "valid_from", "valid_to", "origin")
PROVISION_CONTENT_FIELDS = ("provision_id", "requirement_version_id", "locator", "duty_bearer", "modality", "action",
                            "regulated_object", "timing", "normalized_meaning", "evidence_expectation", "semantic_origin")


def requirement_content_digest(rv: RequirementVersion, provisions: list[Provision]) -> str:
    """SHA-256 of one requirement version's content and the provisions a release binds from it."""
    from ..platform import canonical_json
    payload = {"profile": CONTENT_PROFILE,
               "requirement": {k: getattr(rv, k) for k in REQUIREMENT_CONTENT_FIELDS},
               "provisions": [{k: getattr(p, k) for k in PROVISION_CONTENT_FIELDS}
                              for p in sorted(provisions, key=lambda p: p.provision_id)]}
    return sha256_hex(canonical_json(payload))


# ---------------------------------------------------------------- source-mapping release binding (RUL §12, RUL-REQ-008)
# ZA-RUL-001 §12: source mappings are outside the executable (AST) hash, but "source mappings required for legal
# interpretation remain release-bound", and anything outside the executable hash must be "separately integrity-protected
# in release manifest". The RuleVersion row keeps the exact records the release was built from (written once; immutable
# from migration 0004); each manifest rule-version entry binds them, so the release signature covers them:
#   source_mappings_profile  the hashed form below (versioned like CONTENT_PROFILE);
#   source_mappings          [{source_mapping_id, sha256}] per record, sorted by ID: §12 "Whitespace/order: Never legally
#                            meaningful", so the order records are stored in does not change the binding;
#   source_mappings_sha256   one digest over that list.
# Every stored field of a record is bound (§08 fields, reviewer notes included: §12 lets reviewer notes sit outside the
# executable hash only if integrity-protected in the manifest). Same convention as `ast_sha256` / `content_sha256`:
# SHA-256 over the platform canonical JSON profile v1; no other algorithm or format is assumed.
SOURCE_MAPPINGS_PROFILE = "za-source-mappings-v1"


def source_mapping_digest(record: Any) -> str:
    from ..platform import canonical_json
    return sha256_hex(canonical_json({"profile": SOURCE_MAPPINGS_PROFILE, "source_mapping": record}))


def source_mapping_binding(records: list | None) -> dict:
    """The manifest fields binding these source-mapping records (deterministic for the same records in any order)."""
    from ..platform import canonical_json
    entries = sorted(({"source_mapping_id": r.get("source_mapping_id") if isinstance(r, dict) else None,
                       "sha256": source_mapping_digest(r)} for r in (records or [])),
                     key=lambda e: (str(e["source_mapping_id"]), e["sha256"]))
    return {"source_mappings_profile": SOURCE_MAPPINGS_PROFILE, "source_mappings": entries,
            "source_mappings_sha256": sha256_hex(canonical_json({"profile": SOURCE_MAPPINGS_PROFILE,
                                                                 "source_mappings": entries}))}


def source_mapping_binding_check(entry: dict, records: list | None) -> dict:
    """Compare a manifest rule-version entry's binding with the records stored now. status:
      NOT_BOUND        the release predates the binding (nothing to compare; cannot be proven);
      UNKNOWN_PROFILE  bound under a profile this code does not implement;
      MATCH            the stored records are exactly the bound ones;
      MISMATCH         with missing (bound, no longer stored), unbound (stored, never bound) and altered (same ID,
                       different content: an altered or substituted record) mapping IDs, or a malformed binding."""
    if not any(k in entry for k in ("source_mappings_profile", "source_mappings", "source_mappings_sha256")):
        return {"status": "NOT_BOUND"}
    if entry.get("source_mappings_profile") != SOURCE_MAPPINGS_PROFILE:
        return {"status": "UNKNOWN_PROFILE", "profile": entry.get("source_mappings_profile")}
    bound, now = entry.get("source_mappings"), source_mapping_binding(records)
    if not isinstance(bound, list) or not all(isinstance(b, dict) for b in bound):
        return {"status": "MISMATCH", "malformed": True, "missing": [], "unbound": [], "altered": []}
    by_id = lambda rows: {r.get("source_mapping_id"): sorted(x.get("sha256") for x in rows
                                                             if x.get("source_mapping_id") == r.get("source_mapping_id"))
                          for r in rows}
    b, n = by_id(bound), by_id(now["source_mappings"])
    missing = sorted(str(k) for k in b.keys() - n.keys())
    unbound = sorted(str(k) for k in n.keys() - b.keys())
    altered = sorted(str(k) for k in b.keys() & n.keys() if b[k] != n[k])
    aggregate = entry.get("source_mappings_sha256") == now["source_mappings_sha256"]
    ok = aggregate and not (missing or unbound or altered)
    return {"status": "MATCH" if ok else "MISMATCH", "missing": missing, "unbound": unbound, "altered": altered,
            "aggregate_matches": aggregate}


def release_binding(db, odef: ObligationDefinition) -> list[dict]:
    """The exact requirement versions, source snapshots and provisions a rule release of `odef` is built on. Refuses
    (409, nothing written) when any is missing, not VERIFIED/PUBLISHED, or not grounded in an intact source snapshot."""
    ids = list(odef.provision_ids or [])
    problems: list[dict] = []
    if not ids:
        problems.append({"code": "OBLIGATION_HAS_NO_PROVISIONS", "obligation_definition_id": odef.obligation_definition_id})
    provs = {p.provision_id: p for p in db.execute(select(Provision).where(Provision.provision_id.in_(ids or ["-"]))).scalars()}
    by_rv: dict[str, list[str]] = {}
    for pid in ids:
        p = provs.get(pid)
        if p is None:
            problems.append({"code": "PROVISION_MISSING", "provision_id": pid})
            continue
        if p.lifecycle not in RELEASABLE:
            problems.append({"code": "PROVISION_NOT_VERIFIED", "provision_id": pid, "lifecycle": p.lifecycle})
        by_rv.setdefault(p.requirement_version_id, []).append(pid)
    binding = []
    for rv_id, pids in sorted(by_rv.items()):
        rv = db.get(RequirementVersion, rv_id)
        if rv is None:
            problems.append({"code": "REQUIREMENT_VERSION_MISSING", "requirement_version_id": rv_id})
            continue
        if rv.lifecycle not in RELEASABLE:
            problems.append({"code": "REQUIREMENT_NOT_VERIFIED", "requirement_version_id": rv_id, "lifecycle": rv.lifecycle})
        if rv.instrument_id != odef.instrument_id:
            problems.append({"code": "REQUIREMENT_INSTRUMENT_MISMATCH", "requirement_version_id": rv_id})
        src = db.get(RegulatorySource, rv.source_id)
        if not grounded(src) or not fingerprint_intact(src):
            problems.append({"code": "SOURCE_NOT_GROUNDED", "requirement_version_id": rv_id, "source_id": rv.source_id})
        elif src.lifecycle == "WITHDRAWN":
            problems.append({"code": "SOURCE_WITHDRAWN", "requirement_version_id": rv_id, "source_id": rv.source_id})
        binding.append({"requirement_version_id": rv_id, "requirement_key": rv.requirement_key,
                        "source_id": rv.source_id, "source_fingerprint": src.fingerprint if src else None,
                        "source_locator": src.locator if src else None, "locator": rv.locator,
                        "provision_ids": sorted(pids), "content_profile": CONTENT_PROFILE,
                        "content_sha256": requirement_content_digest(rv, [provs[p] for p in sorted(pids)])})
    if problems:
        raise _conflict("RELEASE_CONTENT_NOT_VERIFIED", "Every provision a release binds must belong to a VERIFIED or "
                                                        "PUBLISHED requirement version grounded in an intact source "
                                                        "snapshot (INV-06, INV-09, FR-REG-001/002).", problems=problems)
    return binding


# ZA-RUL-001 §08 "SourceMapping field / Requirement", enforced at publication (RUL-REQ-020 fail closed; §15 "Source
# completeness"). mapping_type: "DIRECT_TEXT, INTERPRETIVE, DEFINitional, CROSS_REFERENCE, TRANSITIONAL or approved
# controlled code" (the specification prints DEFINitional; no further code is approved, so none is accepted).
# interpretation_rationale: "Required when mapping is not a direct mechanical encoding". authoritative_reference:
# "Article/section/paragraph locator or equivalent authoritative citation". reviewer, review_date, scope_note and
# rights_class are deliberately not checked here: their per-mapping meaning awaits the RUL specification owner.
MAPPING_TYPES = ("DIRECT_TEXT", "INTERPRETIVE", "DEFINITIONAL", "CROSS_REFERENCE", "TRANSITIONAL")


def _blank(v: Any) -> bool:
    return not isinstance(v, str) or not v.strip()


def mapping_field_problems(m: dict, index: int) -> list[dict]:
    """The §08 field problems of one mapping record, all of them (never only the first)."""
    ref = {"source_mapping_id": m.get("source_mapping_id"), "mapping_index": index}
    out = []
    t = m.get("mapping_type")
    if t is None or (isinstance(t, str) and not t.strip()):
        out.append({"code": "RULE_SOURCE_MAPPING_TYPE_MISSING", "field": "mapping_type", **ref,
                    "message": f"mapping_type is required; one of {', '.join(MAPPING_TYPES)}"})
    elif t not in MAPPING_TYPES:
        out.append({"code": "RULE_SOURCE_MAPPING_TYPE_INVALID", "field": "mapping_type", "value": t if isinstance(t, str) else repr(t),
                    **ref, "message": f"mapping_type must be one of {', '.join(MAPPING_TYPES)}"})
    elif t != "DIRECT_TEXT" and _blank(m.get("interpretation_rationale")):
        # Only for a valid non-direct type: whether an unknown or missing type needs a rationale is not decidable.
        out.append({"code": "RULE_SOURCE_MAPPING_RATIONALE_REQUIRED", "field": "interpretation_rationale",
                    "mapping_type": t, **ref,
                    "message": f"{t} is not a direct mechanical encoding: interpretation_rationale is required"})
    if _blank(m.get("authoritative_reference")):
        out.append({"code": "RULE_SOURCE_MAPPING_REFERENCE_REQUIRED", "field": "authoritative_reference", **ref,
                    "message": "authoritative_reference (article/section/paragraph locator) is required"})
    return out


def validate_draft_source_mappings(db, draft: RuleDraft, canonical_ast: dict) -> None:
    """ZA-RUL-001 §08, §11: validate source mappings on a draft before release.
    Refuses unresolved mappings, duplicate mapping IDs, missing requirement versions/provisions,
    provision mismatches, instrument mismatches, tampered fingerprints, and §08 field violations (mapping_type,
    interpretation_rationale, authoritative_reference). Every problem found is reported in one 409; nothing is written
    (the publication calls this before it takes a record sequence)."""
    problems = draft_source_mapping_problems(db, draft, canonical_ast)
    if problems:
        raise _conflict("RELEASE_CONTENT_NOT_VERIFIED", "Rule source mappings failed verification (ZA-RUL-001 §08, §11).",
                        problems=problems)


def draft_source_mapping_problems(db, draft: RuleDraft, ast: dict, *, release: bool = True) -> list[dict]:
    """Every source-mapping problem of a draft (the checks validate_draft_source_mappings refuses on), as data. Reads only.

    ZA-RUL-001 §11 stage 2 "Reference resolution" (source mappings "resolve") is static validation, and §18 an ERROR
    "Blocks review/release": the §08 fields, IDs, AST references, grounding, requirement version / provision existence,
    their relationship, the instrument, and the cited source snapshot (grounded and intact, and the fingerprint the
    mapping cites; reached through the provision when no requirement version is named) are checked at every stage
    (release=False).
    Whether the cited requirement version / provision is VERIFIED or PUBLISHED is checked with release=True only: INV-09
    ties VERIFIED review to reaching RELEASED, and the established workflow reviews a rule while its requirement is still
    under verification, the publication refusing until it is verified (an open question for the RUL owner: whether
    §11 stage 2 "release-compatible" means this state at static validation). With release=True the result is exactly
    the publication gate's. A malformed reference in the AST is rules.validate's RUL_SCHEMA_INVALID, not looked up here."""
    seen_ids: set[str] = set()
    problems = []
    records = {}
    for index, m in enumerate(draft.source_mappings or []):
        if not isinstance(m, dict):
            problems.append({"code": "RULE_SOURCE_MAPPING_MALFORMED", "mapping_index": index,
                             "message": "Source mapping record is not a dict"})
            continue
        problems += mapping_field_problems(m, index)
        mid = m.get("source_mapping_id")
        if not mid:
            problems.append({"code": "RULE_SOURCE_MAPPING_ID_MISSING", "message": "Source mapping missing ID"})
            continue
        if mid in seen_ids:
            problems.append({"code": "RULE_SOURCE_MAPPING_DUPLICATE", "source_mapping_id": mid,
                             "message": f"Duplicate source mapping ID: {mid}"})
        seen_ids.add(mid)
        records[mid] = m

    used_refs: set[str] = set()
    for node, refs in _walk(ast):
        own = node.get("source_mappings")
        if isinstance(own, list):
            used_refs |= {r for r in own if isinstance(r, str) and r.strip()}
    unresolved = sorted(used_refs - seen_ids)
    for ref in unresolved:
        problems.append({"code": "RULE_SOURCE_MAPPING_UNRESOLVED", "source_mapping_id": ref,
                         "message": f"Executable AST node cites source mapping '{ref}' not carried by rule"})

    for mid, m in records.items():
        rv_id = m.get("requirement_version_id")
        pid = m.get("provision_id")
        if not (rv_id or pid):
            problems.append({"code": "RULE_SOURCE_MAPPING_UNGROUNDED", "source_mapping_id": mid,
                             "message": f"Source mapping '{mid}' has no requirement_version_id or provision_id"})
        if rv_id:
            rv = db.get(RequirementVersion, rv_id)
            if rv is None:
                problems.append({"code": "RULE_SOURCE_MAPPING_REQUIREMENT_MISSING", "source_mapping_id": mid,
                                 "requirement_version_id": rv_id, "message": f"Linked requirement version '{rv_id}' not found"})
            else:
                # INV-06 / INV-09: binding content rests on verified requirement versions only. A mapping that cites a
                # version still under review, superseded or withdrawn would ground a released rule in content that is
                # not (or no longer) the verified basis.
                if release and rv.lifecycle not in RELEASABLE:
                    problems.append({"code": "RULE_SOURCE_MAPPING_NOT_VERIFIED", "source_mapping_id": mid,
                                     "requirement_version_id": rv_id, "lifecycle": rv.lifecycle,
                                     "message": f"Linked requirement version '{rv_id}' is {rv.lifecycle}, not VERIFIED/PUBLISHED"})
                odef = db.get(ObligationDefinition, draft.obligation_definition_id)
                if odef and rv.instrument_id != odef.instrument_id:
                    problems.append({"code": "RULE_SOURCE_MAPPING_INSTRUMENT_MISMATCH", "source_mapping_id": mid,
                                     "requirement_version_id": rv_id, "message": "Source mapping instrument mismatch"})
                problems += _cited_source_problems(db, mid, rv, m.get("source_fingerprint"))
        if pid:
            p = db.get(Provision, pid)
            if p is None:
                problems.append({"code": "RULE_SOURCE_MAPPING_PROVISION_MISSING", "source_mapping_id": mid,
                                 "provision_id": pid, "message": f"Linked provision '{pid}' not found"})
            elif release and p.lifecycle not in RELEASABLE:
                problems.append({"code": "RULE_SOURCE_MAPPING_NOT_VERIFIED", "source_mapping_id": mid, "provision_id": pid,
                                 "lifecycle": p.lifecycle, "message": f"Linked provision '{pid}' is {p.lifecycle}, not VERIFIED/PUBLISHED"})
            elif rv_id and p.requirement_version_id != rv_id:
                problems.append({"code": "RULE_SOURCE_MAPPING_PROVISION_MISMATCH", "source_mapping_id": mid,
                                 "provision_id": pid, "requirement_version_id": rv_id,
                                 "message": f"Provision '{pid}' does not belong to requirement '{rv_id}'"})
            if p is not None and not rv_id:
                # Grounded by the provision alone: its source snapshot is the one of the requirement version the provision
                # belongs to, so the cited fingerprint and the grounding are checked against that snapshot as well.
                problems += _cited_source_problems(db, mid, db.get(RequirementVersion, p.requirement_version_id),
                                                   m.get("source_fingerprint"))
    return problems


def _cited_source_problems(db, mid: str, rv: RequirementVersion | None, cited_fingerprint) -> list[dict]:
    """The source snapshot behind a mapping's cited content (ZA-RUL-001 §08: requirement_version_id / provision_id are
    "Exact source-grounded content objects"; source_fingerprint is the "fingerprint of source version"):
      RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED    the snapshot is missing, has no fingerprint or authoritative locator,
                                                  or its stored text no longer hashes to its fingerprint (grounded /
                                                  fingerprint_intact, as verify() and release_binding() require);
      RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH   the mapping cites a fingerprint the snapshot does not have.
    Nothing is reported when the requirement version itself is missing (that is reported on its own)."""
    if rv is None:
        return []
    src = db.get(RegulatorySource, rv.source_id)
    if not grounded(src) or not fingerprint_intact(src):
        return [{"code": "RULE_SOURCE_MAPPING_SOURCE_NOT_GROUNDED", "source_mapping_id": mid,
                 "requirement_version_id": rv.requirement_version_id, "source_id": rv.source_id,
                 "message": "The cited content rests on a source snapshot that is missing, ungrounded or whose stored "
                            "text no longer matches its fingerprint"}]
    if cited_fingerprint and src.fingerprint != cited_fingerprint:
        return [{"code": "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH", "source_mapping_id": mid,
                 "message": "Source mapping cited fingerprint does not match source snapshot"}]
    return []


def publish_bound(ctx, binding: list[dict], *, content_release_id: str, record_seq: int) -> dict:
    """With the release: each bound VERIFIED version and its provisions become PUBLISHED, and older PUBLISHED versions
    of the same requirement become SUPERSEDED (GLS §04 "A newer published version replaces this version"). Their
    content is not altered; the successor's valid_from marks the change."""
    db, published, superseded = ctx.db, [], []
    for b in binding:
        rv = lock_version(db, b["requirement_version_id"])
        if rv.lifecycle != "VERIFIED":
            continue
        move(db, rv, object_type="RequirementVersion", object_id=rv.requirement_version_id, to="PUBLISHED",
             actor=ctx.actor, action="requirement.lifecycle", record_seq=record_seq,
             reason=f"Released in content release {content_release_id}")
        rv.content_release_id = content_release_id
        for p in db.execute(select(Provision).where(Provision.requirement_version_id == rv.requirement_version_id)
                            .order_by(Provision.locator, Provision.provision_id)).scalars():  # deterministic, as verify()
            if p.lifecycle == "VERIFIED":
                move(db, p, object_type="Provision", object_id=p.provision_id, to="PUBLISHED", actor=ctx.actor,
                     action="provision.lifecycle", record_seq=record_seq,
                     reason=f"Released in content release {content_release_id}")
        published.append(rv.requirement_version_id)
        emit(db, event_type="requirement.version_published", aggregate_type="RequirementVersion",
             aggregate_id=rv.requirement_version_id, record_seq=record_seq,
             payload={"requirement_key": rv.requirement_key, "content_release_id": content_release_id,
                      "source_fingerprint": b["source_fingerprint"]})
        for old in versions_of(db, rv.requirement_key):
            if old.requirement_version_id != rv.requirement_version_id and old.lifecycle == "PUBLISHED":
                move(db, old, object_type="RequirementVersion", object_id=old.requirement_version_id, to="SUPERSEDED",
                     actor=ctx.actor, action="requirement.lifecycle", record_seq=record_seq,
                     reason=f"Superseded by {rv.requirement_version_id} in content release {content_release_id}")
                # Its provisions are that version's atomic units (DOM §13) and are superseded with it.
                for p in db.execute(select(Provision).where(Provision.requirement_version_id == old.requirement_version_id)
                                    .order_by(Provision.locator, Provision.provision_id)).scalars():
                    if p.lifecycle == "PUBLISHED":
                        move(db, p, object_type="Provision", object_id=p.provision_id, to="SUPERSEDED", actor=ctx.actor,
                             action="provision.lifecycle", record_seq=record_seq,
                             reason=f"Requirement version {old.requirement_version_id} superseded by {rv.requirement_version_id}")
                superseded.append(old.requirement_version_id)
                emit(db, event_type="requirement.version_superseded", aggregate_type="RequirementVersion",
                     aggregate_id=old.requirement_version_id, record_seq=record_seq,
                     payload={"requirement_key": old.requirement_key, "superseded_by": rv.requirement_version_id,
                              "content_release_id": content_release_id})
    return {"published": published, "superseded": superseded}


# ---------------------------------------------------------------- traceability
def _walk(node: Any, inherited: tuple[str, ...] = ()):
    """(node, effective source-mapping refs) for every node of a canonical AST."""
    if isinstance(node, dict):
        refs = tuple(node.get("source_mappings") or ()) or inherited
        if "node_type" in node:
            yield node, refs
        for k, v in node.items():
            if k != "source_mappings":
                yield from _walk(v, refs)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, inherited)


def mapping_coverage(rule: RuleVersion) -> dict:
    """RUL §08: which source-mapping references the executable AST uses, which resolve to a mapping record, which
    records are grounded in an exact requirement version/provision, and which records nothing uses."""
    return coverage_of(rule.canonical_ast, rule.source_mappings)


def coverage_of(ast: Any, source_mappings: list | None) -> dict:
    """mapping_coverage for any AST and its mapping records, e.g. a draft's: §08 "the release tooling must show coverage
    and orphaned mappings". Reports only; whether an orphaned record blocks publication is not decided here. A malformed
    record (not an object, or without a string ID) or reference (not a string) is left out, as validation reports it."""
    records = {m.get("source_mapping_id"): m for m in (source_mappings or [])
               if isinstance(m, dict) and isinstance(m.get("source_mapping_id"), str)}
    used: set[str] = set()
    facts: dict[str, set[str]] = {}

    def own_refs(node: dict) -> tuple:
        own = node.get("source_mappings")
        return tuple(r for r in own if isinstance(r, str)) if isinstance(own, list) else ()

    def walk(node: Any, inherited: tuple = ()):  # as _walk, with malformed references ignored
        if isinstance(node, dict):
            refs = own_refs(node) or inherited
            if "node_type" in node:
                yield node, refs
            for k, v in node.items():
                if k != "source_mappings":
                    yield from walk(v, refs)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v, inherited)

    for node, refs in walk(ast):
        used |= set(own_refs(node))
        if node.get("node_type") == "PREDICATE":
            for arg, _ in walk(node.get("args") or []):
                if arg.get("node_type") == "FACT_REF":
                    fact = arg.get("fact")
                    facts.setdefault(fact if isinstance(fact, str) and fact else "?", set()).update(refs)
    return {
        "referenced": sorted(used),
        "unresolved": sorted(used - set(records)),
        "orphaned_records": sorted(set(records) - used),
        "ungrounded_records": sorted(k for k, m in records.items()
                                     if not (m.get("requirement_version_id") or m.get("provision_id"))),
        "fact_mappings": [{"fact_key": f, "source_mapping_ids": sorted(r),
                           "resolved": [records[x] for x in sorted(r) if x in records]} for f, r in sorted(facts.items())],
    }


def provision_ref(p: Provision) -> dict:
    return {"provision_id": p.provision_id, "locator": p.locator, "duty_bearer": p.duty_bearer, "modality": p.modality,
            "action": p.action, "regulated_object": p.regulated_object, "semantic_origin": p.semantic_origin,
            "lifecycle": p.lifecycle}


def requirement_ref(db, rv: RequirementVersion) -> dict:
    inst = db.get(Instrument, rv.instrument_id)
    return {"requirement_version_id": rv.requirement_version_id, "requirement_key": rv.requirement_key,
            "version_number": version_number(db, rv), "title": rv.title, "locator": rv.locator,
            "instrument": {"instrument_id": rv.instrument_id, "identifier": inst.identifier if inst else None,
                           "jurisdiction": inst.jurisdiction if inst else None},
            "valid_from": rv.valid_from, "valid_to": rv.valid_to, "lifecycle": rv.lifecycle, "origin": rv.origin,
            "reviewed_by": rv.reviewed_by, "content_release_id": rv.content_release_id, "record_seq": rv.record_seq}


def current_rule_version(db, odef_id: str) -> RuleVersion | None:
    """The latest PUBLISHED RuleVersion of a definition: the head of its released lineage (the base a new draft starts
    from). Not necessarily the version in force: see effective_rule_version and rule_effective_status."""
    return db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == odef_id,
                                                RuleVersion.status == "PUBLISHED")
                      .order_by(RuleVersion.version.desc())).scalars().first()


def _today() -> date:
    """Today's legal date for catalogue effective-version selection (and the obligation-definition cache key): the same
    conversion as evaluation (Option U, rules.legal_date: the UTC calendar date of the instant)."""
    from . import rules  # pure module (no I/O); imported here like this module's other engine imports
    return rules.legal_date(datetime.now(timezone.utc))


def effective_rule_version(db, odef_id: str, on: date | None = None) -> RuleVersion | None:
    """The PUBLISHED RuleVersion in force on ``on`` (today, UTC, by default): the highest version whose valid period
    [valid_from, valid_to) contains the date. The same selection applicability makes before binding a rule
    (engines/applicability.resolve_rule), so a catalogue view names the version evaluations actually bind. DOM §13:
    "exactly one released rule version is effective for a valid instant"; TIM-REQ-026: future-effective PUBLISHED
    content does not affect current binding applicability before its valid time. Read-only."""
    from sqlalchemy import or_
    on = on or _today()
    return db.execute(select(RuleVersion).where(
        RuleVersion.obligation_definition_id == odef_id, RuleVersion.status == "PUBLISHED",
        RuleVersion.valid_from <= on, or_(RuleVersion.valid_to.is_(None), RuleVersion.valid_to > on))
        .order_by(RuleVersion.version.desc())).scalars().first()


def rule_effective_status(rule: RuleVersion, effective: RuleVersion | None, on: date | None = None) -> str:
    """Whether a PUBLISHED rule version is the one in force on ``on``: EFFECTIVE, NOT_YET_EFFECTIVE (its valid period
    starts later), NO_LONGER_EFFECTIVE (its valid period has ended) or NOT_SELECTED (in its valid period, but a higher
    version in force at the same date is the one bound)."""
    on = on or _today()
    if effective is not None and effective.rule_version_id == rule.rule_version_id:
        return "EFFECTIVE"
    if rule.valid_from > on:
        return "NOT_YET_EFFECTIVE"
    if rule.valid_to is not None and rule.valid_to <= on:
        return "NO_LONGER_EFFECTIVE"
    return "NOT_SELECTED"


def _rule_ref(rule: RuleVersion | None, on: date) -> dict | None:
    """A rule version as an exact reference with its valid period, as of the date it was resolved for."""
    if rule is None:
        return None
    return {"rule_version_id": rule.rule_version_id, "version": rule.version, "content_release_id": rule.content_release_id,
            "valid_from": rule.valid_from, "valid_to": rule.valid_to, "effective_on": on}


def rule_currency(db, odef_id: str, on: date | None = None) -> dict:
    """The latest published (head) and the in-force rule versions of a definition, and whether they are the same."""
    on = on or _today()
    head, effective = current_rule_version(db, odef_id), effective_rule_version(db, odef_id, on)
    return {"head": head, "effective": effective, "on": on,
            "head_status": rule_effective_status(head, effective, on) if head is not None else None}


def release_bound_requirements(db, rule: RuleVersion) -> list[dict] | None:
    """The requirement binding a rule's signed release manifest carries, or None for releases made before binding."""
    cr = db.get(ContentRelease, rule.content_release_id) if rule.content_release_id else None
    return None if cr is None else (cr.manifest or {}).get("requirement_versions")


def trace_rule_version(db, rule: RuleVersion, as_of_record_seq: int | None = None) -> dict:
    """Rule version -> obligation definition -> provisions -> requirement versions -> source snapshots, plus how the
    rule's predicates and required facts map to source content. RELEASE_BOUND: the signed release names the exact
    requirement versions and source fingerprints. DEFINITION_DERIVED: the release predates that binding, so the chain
    is read from the definition's provision links as they are now."""
    odef = db.get(ObligationDefinition, rule.obligation_definition_id)
    bound = release_bound_requirements(db, rule)
    if bound is not None:
        basis, entries = "RELEASE_BOUND", bound
    else:
        basis, entries = "DEFINITION_DERIVED", _derive(db, odef)
    requirements, problems = [], []
    for b in entries:
        rv_id = b["requirement_version_id"]
        rv = db.get(RequirementVersion, rv_id)
        src = db.get(RegulatorySource, b["source_id"]) if b.get("source_id") else None
        bound_pids = list(b.get("provision_ids") or [])
        provs = db.execute(select(Provision).where(Provision.provision_id.in_(bound_pids or ["-"]))
                           .order_by(Provision.locator, Provision.provision_id)).scalars().all()
        # The exact content this release bound, recomputed from the rows now stored (DAT-REQ-012; RUL-REQ-026): a later
        # correction is a new requirement version, so the bound version's text must still hash to the bound digest.
        # None: the binding predates content digests, or the chain is DEFINITION_DERIVED (nothing was bound).
        content_matches = None
        if basis == "RELEASE_BOUND" and b.get("content_sha256") and rv is not None and len(provs) == len(bound_pids):
            content_matches = requirement_content_digest(rv, list(provs)) == b["content_sha256"]
        fingerprint_matches = bool(src and b.get("source_fingerprint") == src.fingerprint)
        missing_pids = sorted(set(bound_pids) - {p.provision_id for p in provs})
        if rv is None:
            problems.append(_problem("REQUIREMENT_VERSION_MISSING", "INCOMPLETE", requirement_version_id=rv_id))
        if src is None:
            problems.append(_problem("SOURCE_SNAPSHOT_MISSING", "INCOMPLETE", requirement_version_id=rv_id,
                                     source_id=b.get("source_id")))
        elif not fingerprint_matches:
            problems.append(_problem("SOURCE_FINGERPRINT_MISMATCH", "MISMATCH", requirement_version_id=rv_id,
                                     source_id=src.source_id))
        if missing_pids:
            problems.append(_problem("PROVISION_MISSING", "INCOMPLETE", requirement_version_id=rv_id,
                                     provision_ids=missing_pids))
        if content_matches is False:
            problems.append(_problem("REQUIREMENT_CONTENT_MISMATCH", "MISMATCH", requirement_version_id=rv_id))
        if basis == "RELEASE_BOUND" and not b.get("content_sha256"):
            problems.append(_problem("CONTENT_DIGEST_NOT_BOUND", "UNVERIFIABLE", requirement_version_id=rv_id))
        requirements.append({
            "requirement": requirement_ref(db, rv) if rv else {"requirement_version_id": rv_id, "missing": True},
            "source": source_ref(src), "bound_source_fingerprint": b.get("source_fingerprint"),
            "fingerprint_matches_binding": fingerprint_matches,
            "bound_content_sha256": b.get("content_sha256"), "content_matches_binding": content_matches,
            "provisions": [provision_ref(p) for p in provs], "missing_provision_ids": missing_pids})
    if basis == "DEFINITION_DERIVED":
        # Read from the definition's provision links as they are now, so it cannot prove what the rule was built on.
        problems.append(_problem("NOT_RELEASE_BOUND", "UNVERIFIABLE", content_release_id=rule.content_release_id))
    elif (diff := links_binding_difference(odef, entries)) is not None:
        # The definition's links are not the ones its release bound: the definition changed after release, so its
        # forward trace (links) and this reverse trace (binding) no longer agree. A bound provision that cannot be
        # found at all is already reported (PROVISION_MISSING) and is not reported twice.
        gone = {p for r in requirements for p in r["missing_provision_ids"]}
        diff["bound_not_linked"] = [p for p in diff["bound_not_linked"] if p not in gone]
        if diff["linked_not_bound"] or diff["bound_not_linked"]:
            problems.append(_problem("DEFINITION_LINKS_DIFFER_FROM_RELEASE", "MISMATCH",
                                     obligation_definition_id=odef.obligation_definition_id, **diff))
    cr = db.get(ContentRelease, rule.content_release_id) if rule.content_release_id else None
    problems += _rule_problems(db, rule)  # includes SOURCE_MAPPINGS_MISMATCH against the release's mapping binding
    entry = next((e for e in ((cr.manifest or {}).get("rule_versions") or []) if isinstance(e, dict)
                  and e.get("rule_version_id") == rule.rule_version_id), None) if cr and isinstance(cr.manifest, dict) else None
    if entry is not None and source_mapping_binding_check(entry, rule.source_mappings)["status"] in ("NOT_BOUND",
                                                                                                  "UNKNOWN_PROFILE"):
        # The release predates source-mapping binding (ZA-RUL-001 §12): the records cannot be proven to be the released ones.
        problems.append(_problem("SOURCE_MAPPINGS_NOT_BOUND", "UNVERIFIABLE", content_release_id=rule.content_release_id))
    fact_keys = list(rule.required_facts or [])
    fdefs = {f.key: f for f in db.execute(select(FactDefinition).where(FactDefinition.key.in_(fact_keys or ["-"]))).scalars()}
    return {"binding_basis": basis, "rule_version_id": rule.rule_version_id, "rule_version": rule.version,
            "content_release_id": rule.content_release_id, "ast_hash": rule.ast_hash,
            "content_release": release_ref(db, cr, as_of_record_seq),
            "obligation_definition": {"obligation_definition_id": odef.obligation_definition_id, "key": odef.key,
                                      "title": odef.title, "coverage_mode": odef.coverage_mode} if odef else None,
            "requirements": requirements,
            "required_facts": [{"key": k, "fact_definition_id": fdefs[k].fact_definition_id if k in fdefs else None,
                                "version": fdefs[k].version if k in fdefs else None} for k in fact_keys],
            "source_mapping_coverage": mapping_coverage(rule),
            "integrity": integrity_verdict(problems),
            **({"perspective": {"as_of_record_seq": as_of_record_seq,
                                "note": "Requirement and provision lifecycle codes are their current values; their "
                                        "changes are recorded in the audit trail."}} if as_of_record_seq is not None else {})}


# ---------------------------------------------------------------- historical integrity (read-only verdicts)
# How a historical chain that cannot be fully proven is reported, never repaired or substituted (TIM-REQ-007,
# APP-REQ-012, DAT-REQ-012). A problem's `class` is one of:
#   MISMATCH      a stored record no longer matches what the decision recorded (hash, fingerprint, reference);
#   INCOMPLETE    a record the decision depends on cannot be found;
#   UNVERIFIABLE  the decision predates the binding needed to prove it (reported, not an error).
# The verdict is the most severe class present, else INTACT. INTACT never asserts that a release signature is
# trusted: signature trust is undecided (decision K1; engines/release_verification.py), so it is stated separately.
_SEVERITY = ("MISMATCH", "INCOMPLETE", "UNVERIFIABLE")
SIGNATURE_NOTE = ("Release signatures are present but not cryptographically verified: the content-release trust anchor "
                  "is undecided (decision K1).")


def _problem(code: str, cls: str, **ids) -> dict:
    return {"code": code, "class": cls, **{k: v for k, v in ids.items() if v is not None}}


def integrity_verdict(problems: list[dict]) -> dict:
    status = next((s for s in _SEVERITY if any(p["class"] == s for p in problems)), "INTACT")
    return {"status": status, "problems": problems, "signature_verified": False, "signature_note": SIGNATURE_NOTE}


def with_problems(trace: dict, extra: list[dict]) -> dict:
    """`trace` with `extra` problems added to its verdict; the trace itself is returned unchanged when there are none
    (a trace may be shared by several evaluations of one rule version, so it is never modified in place)."""
    if not extra:
        return trace
    return {**trace, "integrity": integrity_verdict([*trace["integrity"]["problems"], *extra])}


def links_binding_difference(odef: ObligationDefinition | None, bound: list | None) -> dict | None:
    """How a definition's provision links differ from what one of its releases bound, or None if they agree.

    A release binds exactly the definition's links at publication (release_binding), and the links cannot change once a
    rule of the definition is PUBLISHED (_require_unreleased; DAT-REQ-012; no RuleVersion leaves PUBLISHED). So for a
    release that binds requirements, any difference means released content changed after release (ZA-DOM-001 §13 "each
    link is explicit"; §1 "Every link preserves the exact versions used")."""
    if odef is None or bound is None:
        return None
    bound_pids = {p for b in bound if isinstance(b, dict) for p in (b.get("provision_ids") or []) if isinstance(p, str)}
    linked = {p for p in (odef.provision_ids or []) if isinstance(p, str)}
    if bound_pids == linked:
        return None
    return {"linked_not_bound": sorted(linked - bound_pids), "bound_not_linked": sorted(bound_pids - linked)}


def evaluation_rule_problems(ev, rule: RuleVersion | None) -> list[dict]:
    """An evaluation runs a RuleVersion of the obligation definition it evaluates (ZA-DOM-001 §13 ObligationDefinition ->
    RuleVersion -> ApplicabilityEvaluation; APP-REQ-003 "exact RuleVersion"). One naming another definition's rule
    would show that other definition's legal basis as this decision's, so it is reported, never traced as intact."""
    if ev is None or rule is None or rule.obligation_definition_id == ev.obligation_definition_id:
        return []
    return [_problem("RULE_DEFINITION_MISMATCH", "MISMATCH", evaluation_id=ev.evaluation_id,
                     rule_version_id=rule.rule_version_id, obligation_definition_id=ev.obligation_definition_id,
                     rule_obligation_definition_id=rule.obligation_definition_id)]


def _rule_problems(db, rule: RuleVersion) -> list[dict]:
    """The RuleVersion's own hash and release binding, checked as the evaluator checks them before input freeze, but as a
    read: the result is reported and nothing is refused or signalled here (the evaluation paths fail closed)."""
    from .applicability import _integrity_failure  # local import: applicability imports nothing from this module
    try:
        failure = _integrity_failure(db, rule)
    except (KeyError, TypeError, AttributeError, ValueError):
        failure = ("MALFORMED_CONTENT", rule.rule_version_id)
    if failure is None:
        return []
    check, artifact = failure
    cls = "INCOMPLETE" if check in ("SET_VERSION_MISSING",) or (check == "RELEASE_NOT_PUBLISHED" and
                                                                db.get(ContentRelease, artifact) is None) else "MISMATCH"
    return [_problem("RULE_INTEGRITY_FAILED", cls, check=check, artifact_id=artifact, rule_version_id=rule.rule_version_id)]


def release_ref(db, cr: ContentRelease | None, as_of_record_seq: int | None = None) -> dict | None:
    """A content release and its place in the correction chain: the release it corrects, and the later releases that
    name it as corrected, as known at `as_of_record_seq` (received by the cell by then), or now if None."""
    if cr is None:
        return None
    q = select(ContentRelease.content_release_id).where(ContentRelease.correction_of_release_id == cr.content_release_id)
    if as_of_record_seq is not None:
        q = q.where(ContentRelease.cell_received_seq <= as_of_record_seq)
    corrected_by = db.execute(q.order_by(ContentRelease.published_at, ContentRelease.content_release_id)).scalars().all()
    return {"content_release_id": cr.content_release_id, "label": cr.label, "status": cr.status,
            "published_at": cr.published_at, "manifest_hash": cr.manifest_hash,
            "correction_of_release_id": cr.correction_of_release_id, "corrected_by_release_ids": list(corrected_by)}


def _derive(db, odef: ObligationDefinition | None) -> list[dict]:
    if odef is None:
        return []
    by_rv: dict[str, list[str]] = {}
    for p in db.execute(select(Provision).where(Provision.provision_id.in_(odef.provision_ids or ["-"]))).scalars():
        by_rv.setdefault(p.requirement_version_id, []).append(p.provision_id)
    out = []
    for rv_id, pids in sorted(by_rv.items()):
        rv = db.get(RequirementVersion, rv_id)
        src = db.get(RegulatorySource, rv.source_id) if rv else None
        out.append({"requirement_version_id": rv_id, "source_id": rv.source_id if rv else None,
                    "source_fingerprint": src.fingerprint if src else None, "provision_ids": sorted(pids)})
    return out


def obligation_definitions_for(db, provision_ids: set[str]) -> list[ObligationDefinition]:
    """Obligation definitions linking any of these provisions (the link is a JSON list, so it is matched here)."""
    return [o for o in db.execute(select(ObligationDefinition).order_by(ObligationDefinition.key)).scalars()
            if provision_ids & set(o.provision_ids or [])]


def _downstream(db, provs: list[Provision], on: date) -> list[dict]:
    """Forward from provisions: the obligation definitions using them and each one's current rule/release. `on` is the
    one legal date the whole response resolves in-force versions on (read once by the caller: a clock read per
    definition could put two rows of one response on two dates across a UTC midnight)."""
    pids = {p.provision_id for p in provs}
    out = []
    for o in obligation_definitions_for(db, pids):
        cur = rule_currency(db, o.obligation_definition_id, on)
        rule = cur["head"]
        bound = release_bound_requirements(db, rule) if rule else None
        out.append({"obligation_definition_id": o.obligation_definition_id, "key": o.key, "title": o.title,
                    "coverage_mode": o.coverage_mode, "provision_ids": sorted(pids & set(o.provision_ids or [])),
                    "current_rule_version": None if rule is None else {
                        "rule_version_id": rule.rule_version_id, "version": rule.version,
                        "content_release_id": rule.content_release_id,
                        "binding_basis": "RELEASE_BOUND" if bound is not None else "DEFINITION_DERIVED",
                        "release_binds": sorted({b["requirement_version_id"] for b in bound or []}),
                        # Whether this forward link (the definition's provisions) is what the rule's release bound,
                        # i.e. agrees with the rule's reverse trace; None when the release predates binding.
                        "links_match_release": None if bound is None else links_binding_difference(o, bound) is None,
                        # The latest published version is not necessarily the one in force (TIM-REQ-026).
                        "effective_status": cur["head_status"]},
                    "effective_rule_version": _rule_ref(cur["effective"], cur["on"]),
                    "control_keys": sorted({r["control_key"] for r in _template_roles(db, o)})})
    return out


def trace_requirement(db, rv: RequirementVersion) -> dict:
    """Reverse to the exact source snapshot (and any newer snapshot of the same source), forward to provisions,
    obligation definitions and their current rule releases."""
    src = db.get(RegulatorySource, rv.source_id)
    snapshots = source_versions(db, src) if src else []
    provs = db.execute(select(Provision).where(Provision.requirement_version_id == rv.requirement_version_id)
                       .order_by(Provision.locator, Provision.provision_id)).scalars().all()
    return {"requirement": requirement_ref(db, rv), "source": source_ref(src),
            "newer_source_snapshots": [source_ref(s) for s in snapshots if src and s.record_seq > src.record_seq],
            "provisions": [provision_ref(p) for p in provs], "obligation_definitions": _downstream(db, provs, _today()),
            "issues": [i for i in validate(db)["issues"] if i.get("requirement_version_id") == rv.requirement_version_id]}


def trace_source(db, s: RegulatorySource) -> dict:
    rvs = db.execute(select(RequirementVersion).where(RequirementVersion.source_id == s.source_id)
                     .order_by(RequirementVersion.requirement_key, RequirementVersion.record_seq)).scalars().all()
    on = _today()  # one legal date for every requirement's downstream definitions in this response
    out = []
    for rv in rvs:
        provs = db.execute(select(Provision).where(Provision.requirement_version_id == rv.requirement_version_id)
                           .order_by(Provision.locator, Provision.provision_id)).scalars().all()
        out.append({"requirement": requirement_ref(db, rv), "provisions": [provision_ref(p) for p in provs],
                    "obligation_definitions": _downstream(db, provs, on)})
    return {"source": source_ref(s), "snapshots": [source_ref(x) for x in source_versions(db, s)], "requirements": out}


def trace_obligation_definition(db, odef: ObligationDefinition) -> dict:
    cur = rule_currency(db, odef.obligation_definition_id)
    rule = cur["head"]
    derived = _derive(db, odef)
    missing = sorted(set(odef.provision_ids or []) - {p for d in derived for p in d["provision_ids"]})
    return {"obligation_definition": {"obligation_definition_id": odef.obligation_definition_id, "key": odef.key,
                                      "title": odef.title, "coverage_mode": odef.coverage_mode,
                                      "provision_ids": list(odef.provision_ids or [])},
            "missing_provision_ids": missing,
            "requirements": [{"requirement": requirement_ref(db, rv) if (rv := db.get(RequirementVersion, d["requirement_version_id"])) else None,
                              "source": source_ref(db.get(RegulatorySource, d["source_id"])) if d["source_id"] else None,
                              "provision_ids": d["provision_ids"]} for d in derived],
            "current_rule": {**trace_rule_version(db, rule), "effective_status": cur["head_status"]} if rule else None,
            # The version applicability binds today, which differs from current_rule (the latest published) when that
            # one is not yet, or no longer, in force (TIM-REQ-026; DOM §13).
            "effective_rule_version": _rule_ref(cur["effective"], cur["on"]),
            # Requirement -> definition -> control roles: the controls the default SatisfactionExpression template
            # maps this obligation to (DOM §6.1 SatisfactionExpressionTemplate "over control roles"; STA §07).
            "control_roles": _template_roles(db, odef)}


def _template_roles(db, odef: ObligationDefinition) -> list[dict]:
    from .satisfaction import control_roles
    return control_roles(db, odef.satisfaction_template) if odef.satisfaction_template else []


# ---------------------------------------------------------------- obligation definition authoring
# ZA-DOM-001 §6.1: an ObligationDefinition is "Versioned reusable obligation semantics derived from one or more provision
# versions", owned by Regulatory Content; GLS: "Zoiko-authored structured obligation derived from one or more provisions".
# DOM §13: ProvisionVersion -> ObligationDefinition 1:N ("each link is explicit"); ObligationDefinition -> RuleVersion
# 1:N over time. The schema has no definition version or lifecycle (ZA-DAT-001 §05 lists provision refs, subject type,
# coverage mode, rule lineage), so: links are authored only while no RuleVersion of the definition is PUBLISHED. After
# that the definition is released content (DAT-REQ-012) and a change would need the definition versioning that the
# specifications require but the schema does not have (a migration decision; see the implementation report).
LINKABLE = frozenset({"REVIEW_REQUIRED", "VERIFIED", "PUBLISHED"})  # human-controlled, release-eligible content
DEFINITION_SUBJECT_TYPES = frozenset({"LEGAL_ENTITY", "BUSINESS_UNIT", "REGULATED_ACTIVITY", "PRODUCT_SERVICE",
                                      "ENVIRONMENT", "AI_ASSET", "CONTRACT_THIRD_PARTY"})  # the subject registry's types
SCHEDULE_KINDS = frozenset({"CONTINUOUS", "RECURRING", "EVENT_TRIGGERED"})  # the values the schema already carries
TEMPLATE_NODES = frozenset({"CONTROL", "ALL", "ANY", "K_OF_N", "OCCURRENCES", "OPTIONAL_EVIDENCE"})
MAX_TEMPLATE_NODES = 200


def is_released(db, odef_id: str) -> bool:
    """A definition is released once any of its RuleVersions is PUBLISHED (it is then part of a signed release)."""
    return db.execute(select(RuleVersion.rule_version_id).where(RuleVersion.obligation_definition_id == odef_id,
                                                                RuleVersion.status == "PUBLISHED").limit(1)).first() is not None


def validate_template(db, template: dict | None) -> None:
    """A SatisfactionExpression template (ZA-STA-001 STA-REQ-009/010, §07): the node types the evaluator executes
    (engines/assurance.py expression_problems), the §07 required metadata, unique node IDs and distinct sibling leaves
    (engines/satisfaction.py metadata_problems, shared with organisation expression versions), and CONTROL leaves naming
    catalogue control definitions as control roles. Global content cannot reference one organisation's records
    (DOM-INV-11), so COMPENSATION (an organisation's approval) and exact control_implementation_id bindings are refused.
    Every problem is reported at once: 422 {error, message, problems[]}."""
    from ..models import ControlDefinition
    from .assurance import expression_problems
    from .satisfaction import leaves, metadata_problems
    if template is None:
        return
    problems: list[dict] = []

    def walk(n, path, count):
        count[0] += 1
        if count[0] > MAX_TEMPLATE_NODES:
            problems.append({"code": "TEMPLATE_TOO_LARGE", "path": None,
                             "message": f"Satisfaction template exceeds {MAX_TEMPLATE_NODES} nodes."})
            return
        if isinstance(n, dict) and n.get("node_type") == "COMPENSATION":
            problems.append({"code": "COMPENSATION_NOT_ALLOWED", "path": path,
                             "message": "COMPENSATION is tenant-governed and cannot appear in catalogue content"})
            return
        if isinstance(n, dict) and n.get("node_type") not in TEMPLATE_NODES:
            problems.append({"code": "UNSUPPORTED_NODE", "path": path,
                             "message": f"node_type must be one of {sorted(TEMPLATE_NODES)}"})
            return
        for i, c in enumerate((n.get("children") or []) if isinstance(n, dict) else []):
            walk(c, f"{path}.children[{i}]", count)
    walk(template, "$", [0])
    if not problems:
        problems += [{"code": "TEMPLATE_STRUCTURE_INVALID", "path": None, "message": m}
                     for m in expression_problems(template)]
    if not problems:
        problems += metadata_problems(template, bindings_allowed=False)
        keys = sorted({n["control_key"] for _, n in leaves(template)})
        known = {k for (k,) in db.execute(select(ControlDefinition.key).where(ControlDefinition.key.in_(keys or ["-"])))}
        if set(keys) - known:
            problems.append({"code": "UNKNOWN_CONTROL_DEFINITION", "path": None,
                             "message": f"Unknown control definition key(s): {sorted(set(keys) - known)}"})
    if problems:
        raise HTTPException(422, {"error": problems[0]["code"], "message": "; ".join(p["message"] for p in problems[:10]),
                                  "problems": problems})


def _linkable(db, instrument_id: str, requirement_version_id: str, provision_id: str) -> Provision:
    """The provision, if it exists, belongs to the named requirement version, and both are human-controlled content of
    the definition's instrument that is still release-eligible."""
    rv = db.get(RequirementVersion, requirement_version_id)
    if rv is None:
        raise HTTPException(404, {"error": "REQUIREMENT_VERSION_NOT_FOUND", "message": "requirement version not found",
                                  "requirement_version_id": requirement_version_id})
    p = db.get(Provision, provision_id)
    if p is None:
        raise HTTPException(404, {"error": "PROVISION_NOT_FOUND", "message": "provision not found", "provision_id": provision_id})
    if p.requirement_version_id != requirement_version_id:
        raise _conflict("PROVISION_NOT_IN_REQUIREMENT_VERSION", "The provision belongs to a different requirement version.",
                        provision_id=provision_id, requirement_version_id=requirement_version_id,
                        belongs_to=p.requirement_version_id)
    if rv.instrument_id != instrument_id:
        raise _conflict("REQUIREMENT_INSTRUMENT_MISMATCH", "The requirement version belongs to another instrument than the "
                                                           "obligation definition.", requirement_version_id=requirement_version_id)
    for kind, obj, oid in (("RequirementVersion", rv, requirement_version_id), ("Provision", p, provision_id)):
        if obj.lifecycle not in LINKABLE:
            raise _conflict("CONTENT_NOT_LINKABLE", f"{kind} {oid} is {obj.lifecycle}: only reviewed, release-eligible "
                            "content can be linked (AI proposals need human review; withdrawn and superseded content is "
                            "out of release eligibility; ZA-GLS-001 §04, INV-09, DOM-INV-13).", object_type=kind,
                            object_id=oid, lifecycle=obj.lifecycle)
    return p


def create_definition(ctx, *, key: str, title: str, instrument: Instrument, subject_type: str, coverage_mode: str,
                      schedule_kind: str, recurrence_days: int | None, material: bool, satisfaction_template: dict | None,
                      links: list[tuple[str, str]], reason: str | None) -> ObligationDefinition:
    """A new, unreleased obligation definition with explicit provision links (DOM §6.1, §13). The key is unique (database
    constraint); every link is validated against the catalogue. Becomes binding only through a signed release."""
    from sqlalchemy.exc import IntegrityError
    db = ctx.db
    if not links:
        raise HTTPException(422, "An obligation definition derives from one or more provision versions (ZA-DOM-001 §6.1).")
    pids = [p for _, p in links]
    if len(set(pids)) != len(pids):
        raise HTTPException(422, {"error": "DUPLICATE_LINK", "message": "A provision is linked more than once."})
    validate_template(db, satisfaction_template)
    for rv_id, pid in links:
        _linkable(db, instrument.instrument_id, rv_id, pid)
    if db.execute(select(ObligationDefinition.obligation_definition_id).where(ObligationDefinition.key == key)).first():
        raise _conflict("OBLIGATION_DEFINITION_KEY_EXISTS", f"Obligation definition {key} already exists.", key=key)
    o = ObligationDefinition(key=key, title=title, instrument_id=instrument.instrument_id, provision_ids=list(pids),
                             subject_type=subject_type, coverage_mode=coverage_mode, schedule_kind=schedule_kind,
                             recurrence_days=recurrence_days, material=material, satisfaction_template=satisfaction_template)
    db.add(o)
    try:
        db.flush()
    except IntegrityError:  # a concurrent creation took the key between the check and the insert
        db.rollback()
        raise _conflict("OBLIGATION_DEFINITION_KEY_EXISTS", f"Obligation definition {key} already exists.", key=key) from None
    seq = next_record_seq(db)
    audit(db, action="obligation_definition.create", object_type="ObligationDefinition",
          object_id=o.obligation_definition_id, actor=ctx.actor, record_seq=seq,
          reason=(f"{key} under {instrument.identifier}; subject {subject_type}; coverage {coverage_mode}; links "
                  + ", ".join(f"{p}@{rv}" for rv, p in links) + (f"; {reason}" if reason else ""))[:2000])
    return o


def lock_definition(db, odef_id: str) -> ObligationDefinition:
    """Row-lock one definition for the rest of the transaction and return its committed state.

    Link edits (add_link / remove_link) and rule publication (routers/rules.py publish) both take this lock before they
    read the links, so a publication signs exactly the links committed before it, and a link edit that waited behind a
    publication sees the release and is refused (OBLIGATION_DEFINITION_RELEASED). ``populate_existing`` refreshes an
    instance this session already loaded, so a stale in-memory copy of ``provision_ids`` is never signed."""
    o = db.execute(select(ObligationDefinition).where(ObligationDefinition.obligation_definition_id == odef_id)
                   .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if o is None:
        raise HTTPException(404, "obligation definition not found")
    return o


def lock_release_content(db, odef: ObligationDefinition, draft: RuleDraft) -> dict:
    """Row-lock, for the rest of the publication's transaction, every requirement version and provision the release
    validates or changes, and return their committed state (populate_existing refreshes copies this session holds).

    Locked: the definition's linked provisions and the provisions the draft's mappings cite; their requirement versions
    and those the mappings cite; every other version of the same requirement keys, with all their provisions (the
    publication supersedes older PUBLISHED versions in publish_bound, after it has taken the record sequence, so it must
    already hold them). Requirement versions first, then provisions, each in ID order: a deterministic order shared by
    every publication, taken after the draft and definition rows and before the rule scope, the decision scopes and the
    record sequence (the established lock order). Content review (verify: version, then sequence) and provision edits
    (provision, then sequence) take no lock against this order, so no cycle can form.

    Effect: a concurrent change to these rows (a supersession by another publication, or any lifecycle change made
    below the API) either commits before this publication reads them, so the validation below sees it, or waits until
    this publication has committed. READ COMMITTED alone gives neither."""
    from sqlalchemy import or_
    pids = {p for p in (odef.provision_ids or []) if isinstance(p, str)}
    rv_ids: set[str] = set()
    for m in draft.source_mappings or []:
        if isinstance(m, dict):
            if isinstance(m.get("requirement_version_id"), str):
                rv_ids.add(m["requirement_version_id"])
            if isinstance(m.get("provision_id"), str):
                pids.add(m["provision_id"])
    rv_ids |= set(db.execute(select(Provision.requirement_version_id).where(Provision.provision_id.in_(pids or ["-"]))).scalars())
    keys = set(db.execute(select(RequirementVersion.requirement_key)
                          .where(RequirementVersion.requirement_version_id.in_(rv_ids or ["-"]))).scalars())
    versions = db.execute(select(RequirementVersion).where(or_(RequirementVersion.requirement_version_id.in_(rv_ids or ["-"]),
                                                               RequirementVersion.requirement_key.in_(keys or ["-"])))
                          .order_by(RequirementVersion.requirement_version_id).with_for_update()
                          .execution_options(populate_existing=True)).scalars().all()
    locked_rv = [v.requirement_version_id for v in versions]
    provisions = db.execute(select(Provision).where(or_(Provision.provision_id.in_(pids or ["-"]),
                                                        Provision.requirement_version_id.in_(locked_rv or ["-"])))
                            .order_by(Provision.provision_id).with_for_update()
                            .execution_options(populate_existing=True)).scalars().all()
    return {"requirement_version_ids": locked_rv, "provision_ids": [p.provision_id for p in provisions]}


def _require_unreleased(db, o: ObligationDefinition) -> None:
    if is_released(db, o.obligation_definition_id):
        raise _conflict("OBLIGATION_DEFINITION_RELEASED", "The definition is part of a signed release; its links are "
                        "released content and cannot change in place (DAT-REQ-012). Definition versioning is not yet "
                        "available.", obligation_definition_id=o.obligation_definition_id)


def add_link(ctx, odef_id: str, requirement_version_id: str, provision_id: str, reason: str | None) -> ObligationDefinition:
    db = ctx.db
    o = lock_definition(db, odef_id)  # serialises concurrent link changes to one definition
    _require_unreleased(db, o)
    if provision_id in (o.provision_ids or []):
        raise _conflict("DUPLICATE_LINK", "The provision is already linked.", provision_id=provision_id)
    _linkable(db, o.instrument_id, requirement_version_id, provision_id)
    o.provision_ids = [*(o.provision_ids or []), provision_id]
    audit(db, action="obligation_definition.link", object_type="ObligationDefinition", object_id=odef_id, actor=ctx.actor,
          record_seq=next_record_seq(db),
          reason=(f"Linked provision {provision_id} of requirement version {requirement_version_id}"
                  + (f"; {reason}" if reason else ""))[:2000])
    return o


def remove_link(ctx, odef_id: str, provision_id: str, reason: str) -> ObligationDefinition:
    db = ctx.db
    o = lock_definition(db, odef_id)
    _require_unreleased(db, o)
    current = list(o.provision_ids or [])
    if provision_id not in current:
        raise HTTPException(404, {"error": "LINK_NOT_FOUND", "message": "The provision is not linked to this definition.",
                                  "provision_id": provision_id})
    if len(current) == 1:
        raise _conflict("LAST_LINK", "An obligation definition derives from one or more provision versions "
                                     "(ZA-DOM-001 §6.1); its last link cannot be removed.")
    p = db.get(Provision, provision_id)
    o.provision_ids = [x for x in current if x != provision_id]
    audit(db, action="obligation_definition.unlink", object_type="ObligationDefinition", object_id=odef_id,
          actor=ctx.actor, record_seq=next_record_seq(db),
          reason=(f"Removed provision {provision_id}" + (f" of requirement version {p.requirement_version_id}" if p else "")
                  + f"; {reason}")[:2000])
    return o


def definition_provisions(db, o: ObligationDefinition) -> list[Provision]:
    """The definition's linked provisions in its own link order (``provision_ids``, as definition_view lists them),
    never in the database's physical row order, which an UPDATE of a provision row can change. A link whose provision
    does not exist is left out here (definition_view reports it as missing)."""
    ids = [p for p in (o.provision_ids or []) if isinstance(p, str)]
    found = {p.provision_id: p for p in db.execute(select(Provision).where(Provision.provision_id.in_(ids or ["-"]))).scalars()}
    return [found[p] for p in dict.fromkeys(ids) if p in found]


def definition_view(db, o: ObligationDefinition) -> dict:
    """The definition with each explicit link resolved to its provision, requirement version and source snapshot."""
    inst = db.get(Instrument, o.instrument_id)
    cur = rule_currency(db, o.obligation_definition_id)
    rule = cur["head"]
    links = []
    for pid in o.provision_ids or []:
        p = db.get(Provision, pid)
        rv = db.get(RequirementVersion, p.requirement_version_id) if p else None
        src = db.get(RegulatorySource, rv.source_id) if rv else None
        links.append({"provision_id": pid, "provision": provision_ref(p) if p else None,
                      "requirement_version": requirement_ref(db, rv) if rv else None,
                      "source": {"source_id": src.source_id, "fingerprint": src.fingerprint, "locator": src.locator}
                      if src else None, "missing": p is None})
    return {**to_dict(o), "instrument": {"instrument_id": o.instrument_id, "identifier": inst.identifier if inst else None},
            "links": links, "control_roles": _template_roles(db, o), "released": rule is not None,
            "current_rule_version": None if rule is None else {
                "rule_version_id": rule.rule_version_id, "version": rule.version,
                "content_release_id": rule.content_release_id, "effective_status": cur["head_status"]},
            "effective_rule_version": _rule_ref(cur["effective"], cur["on"])}


# ---------------------------------------------------------------- traceability validation (missing/broken links)
def _issue(code: str, severity: str, message: str, **ids) -> dict:
    return {"code": code, "severity": severity, "message": message, **ids}


def validate(db) -> dict:
    """Every catalogue link checked end to end. ERROR: a broken or unproven link the specifications require; WARNING:
    a gap that does not by itself make released content wrong. Read-only."""
    issues: list[dict] = []
    sources = {s.source_id: s for s in db.execute(select(RegulatorySource)).scalars()}
    insts = {i.instrument_id: i for i in db.execute(select(Instrument)).scalars()}
    rvs = {r.requirement_version_id: r for r in db.execute(select(RequirementVersion)).scalars()}
    provs = list(db.execute(select(Provision)).scalars())
    prov_ids = {p.provision_id: p for p in provs}
    for s in sources.values():
        if not fingerprint_intact(s):
            issues.append(_issue("SOURCE_FINGERPRINT_MISMATCH", "ERROR", "Stored source text no longer matches its "
                                 "fingerprint.", source_id=s.source_id))
    keys: dict[str, set[str]] = {}
    for rv in rvs.values():
        ids = {"requirement_version_id": rv.requirement_version_id, "requirement_key": rv.requirement_key}
        keys.setdefault(rv.requirement_key, set()).add(rv.instrument_id)
        src = sources.get(rv.source_id)
        released = rv.lifecycle in RELEASABLE
        if src is None:
            issues.append(_issue("REQUIREMENT_SOURCE_MISSING", "ERROR", "Requirement version names a source that does "
                                 "not exist.", source_id=rv.source_id, **ids))
        else:
            if not grounded(src):
                issues.append(_issue("SOURCE_NOT_GROUNDED", "ERROR" if released else "WARNING", "Source snapshot has no "
                                     "fingerprint or authoritative reference (ZA-DAT-001 §05).", source_id=src.source_id, **ids))
            if released and src.lifecycle == "WITHDRAWN":
                issues.append(_issue("RELEASED_REQUIREMENT_ON_WITHDRAWN_SOURCE", "ERROR", "Verified or published "
                                     "content rests on a withdrawn source.", source_id=src.source_id, **ids))
            if rv.lifecycle == "PUBLISHED" and src.lifecycle == "SUPERSEDED":
                issues.append(_issue("PUBLISHED_REQUIREMENT_ON_SUPERSEDED_SOURCE", "WARNING", "A newer source snapshot "
                                     "replaces the one this published version rests on.", source_id=src.source_id, **ids))
        if rv.instrument_id not in insts:
            issues.append(_issue("REQUIREMENT_INSTRUMENT_MISSING", "ERROR", "Requirement version names an instrument that "
                                 "does not exist.", instrument_id=rv.instrument_id, **ids))
        if rv.origin == "AI_PROPOSED" and rv.lifecycle in RELEASABLE and not rv.reviewed_by:
            issues.append(_issue("AI_CONTENT_WITHOUT_REVIEW", "ERROR", "AI-proposed content is verified or published "
                                 "without a recorded human reviewer (INV-09).", **ids))
        if released and not any(p.requirement_version_id == rv.requirement_version_id for p in provs):
            issues.append(_issue("REQUIREMENT_WITHOUT_PROVISIONS", "WARNING", "Verified or published requirement "
                                 "version has no atomic provisions.", **ids))
    for key, inst_ids in keys.items():
        if len(inst_ids) > 1:
            issues.append(_issue("REQUIREMENT_KEY_MULTIPLE_INSTRUMENTS", "ERROR", "One requirement identity spans several "
                                 "instruments.", requirement_key=key, instrument_ids=sorted(inst_ids)))
    for p in provs:
        rv = rvs.get(p.requirement_version_id)
        if rv is None:
            issues.append(_issue("PROVISION_ORPHANED", "ERROR", "Provision names a requirement version that does not "
                                 "exist.", provision_id=p.provision_id))
        elif p.lifecycle in RELEASABLE and rv.lifecycle not in RELEASABLE | {"SUPERSEDED"}:
            issues.append(_issue("PROVISION_LIFECYCLE_AHEAD_OF_REQUIREMENT", "WARNING", "Provision is verified or "
                                 "published while its requirement version is not.", provision_id=p.provision_id,
                                 requirement_version_id=rv.requirement_version_id))
    for o in db.execute(select(ObligationDefinition)).scalars():
        oid = {"obligation_definition_id": o.obligation_definition_id, "obligation_key": o.key}
        rule = current_rule_version(db, o.obligation_definition_id)
        if not o.provision_ids:
            issues.append(_issue("OBLIGATION_WITHOUT_PROVISIONS", "ERROR" if rule else "WARNING", "Obligation definition "
                                 "links no provision, so it has no source grounding (INV-06).", **oid))
        for pid in o.provision_ids or []:
            p = prov_ids.get(pid)
            if p is None:
                issues.append(_issue("OBLIGATION_PROVISION_MISSING", "ERROR", "Obligation definition links a provision "
                                     "that does not exist.", provision_id=pid, **oid))
                continue
            rv = rvs.get(p.requirement_version_id)
            if rule and (p.lifecycle not in RELEASABLE | {"SUPERSEDED"}):
                issues.append(_issue("OBLIGATION_PROVISION_NOT_RELEASED", "ERROR", "A released rule's obligation links "
                                     "a provision that was never verified (INV-09).", provision_id=pid, **oid))
            if rv is not None and rv.instrument_id != o.instrument_id:
                issues.append(_issue("OBLIGATION_INSTRUMENT_MISMATCH", "ERROR", "Obligation definition and the "
                                     "requirement version of one of its provisions name different instruments.",
                                     provision_id=pid, requirement_version_id=rv.requirement_version_id, **oid))
    for rule in db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars():
        rid = {"rule_version_id": rule.rule_version_id, "obligation_definition_id": rule.obligation_definition_id}
        cov = mapping_coverage(rule)
        for ref in cov["unresolved"]:
            issues.append(_issue("RULE_SOURCE_MAPPING_UNRESOLVED", "ERROR", "An executable rule node cites a source "
                                 "mapping the rule version does not carry (ZA-RUL-001 §08, §11 stage 2).",
                                 source_mapping_id=ref, **rid))
        for ref in cov["ungrounded_records"]:
            issues.append(_issue("RULE_SOURCE_MAPPING_UNGROUNDED", "WARNING", "Source mapping has no exact "
                                 "requirement_version_id/provision_id (ZA-RUL-001 §08).", source_mapping_id=ref, **rid))
        for ref in cov["orphaned_records"]:
            issues.append(_issue("RULE_SOURCE_MAPPING_ORPHANED", "WARNING", "Source mapping record no rule node uses.",
                                 source_mapping_id=ref, **rid))
        seen_sm: set[str] = set()
        for m in (rule.source_mappings or []):
            if isinstance(m, dict) and (sm_id := m.get("source_mapping_id")):
                if sm_id in seen_sm:
                    issues.append(_issue("RULE_SOURCE_MAPPING_DUPLICATE", "ERROR",
                                         "Rule carries duplicate source mapping ID.",
                                         source_mapping_id=sm_id, **rid))
                seen_sm.add(sm_id)
                rv_id = m.get("requirement_version_id")
                pid = m.get("provision_id")
                src_fp = m.get("source_fingerprint")
                if rv_id is not None:
                    if rv_id not in rvs:
                        issues.append(_issue("RULE_SOURCE_MAPPING_REQUIREMENT_MISSING", "ERROR",
                                             "Source mapping links a requirement version that does not exist.",
                                             source_mapping_id=sm_id, requirement_version_id=rv_id, **rid))
                    else:
                        m_rv = rvs[rv_id]
                        rule_odef = db.get(ObligationDefinition, rule.obligation_definition_id)
                        if rule_odef and m_rv.instrument_id != rule_odef.instrument_id:
                            issues.append(_issue("RULE_SOURCE_MAPPING_INSTRUMENT_MISMATCH", "ERROR",
                                                 "Source mapping links a requirement version from a different instrument.",
                                                 source_mapping_id=sm_id, requirement_version_id=rv_id, **rid))
                        if src_fp is not None:
                            src_rec = sources.get(m_rv.source_id)
                            if src_rec and src_rec.fingerprint != src_fp:
                                issues.append(_issue("RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH", "ERROR",
                                                     "Source mapping fingerprint does not match source snapshot.",
                                                     source_mapping_id=sm_id, requirement_version_id=rv_id, **rid))
                if pid is not None:
                    if pid not in prov_ids:
                        issues.append(_issue("RULE_SOURCE_MAPPING_PROVISION_MISSING", "ERROR",
                                             "Source mapping links a provision that does not exist.",
                                             source_mapping_id=sm_id, provision_id=pid, **rid))
                    elif rv_id is not None and prov_ids[pid].requirement_version_id != rv_id:
                        issues.append(_issue("RULE_SOURCE_MAPPING_PROVISION_MISMATCH", "ERROR",
                                             "Source mapping provision does not belong to the requirement version named.",
                                             source_mapping_id=sm_id, provision_id=pid, requirement_version_id=rv_id, **rid))
        bound = release_bound_requirements(db, rule)
        if bound is None:
            issues.append(_issue("RELEASE_REQUIREMENTS_UNBOUND", "WARNING", "The rule's signed release does not name the "
                                 "requirement versions and source fingerprints it was built on (ZA-RUL-001 §02).",
                                 content_release_id=rule.content_release_id, **rid))
        elif (diff := links_binding_difference(db.get(ObligationDefinition, rule.obligation_definition_id), bound)):
            issues.append(_issue("RELEASE_BINDING_DEFINITION_MISMATCH", "ERROR", "The obligation definition's provision "
                                 "links differ from those this rule's signed release bound: released content changed "
                                 "after release (DAT-REQ-012).", content_release_id=rule.content_release_id, **diff, **rid))
        for b in bound or []:
            src = sources.get(b.get("source_id"))
            if src is None or src.fingerprint != b.get("source_fingerprint"):
                issues.append(_issue("RELEASE_BINDING_FINGERPRINT_MISMATCH", "ERROR", "The release binds a source "
                                     "fingerprint the source record no longer has.", content_release_id=rule.content_release_id,
                                     requirement_version_id=b.get("requirement_version_id"), **rid))
            if b.get("requirement_version_id") not in rvs:
                issues.append(_issue("RELEASE_BINDING_REQUIREMENT_MISSING", "ERROR", "The release binds a requirement "
                                     "version that does not exist.", content_release_id=rule.content_release_id,
                                     requirement_version_id=b.get("requirement_version_id"), **rid))
    issues.sort(key=lambda i: (i["severity"] != "ERROR", i["code"], str(sorted(i.items()))))
    by_code: dict[str, int] = {}
    for i in issues:
        by_code[i["code"]] = by_code.get(i["code"], 0) + 1
    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    return {"ok": errors == 0, "errors": errors, "warnings": len(issues) - errors, "by_code": by_code,
            "checked": {"sources": len(sources), "requirement_versions": len(rvs), "provisions": len(provs),
                        "instruments": len(insts)}, "issues": issues}
