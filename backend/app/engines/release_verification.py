"""Content-release verification (roadmap A9b).

What a published ContentRelease is checked against, and why:

* ZA-RUL-001 §12: "ContentRelease signs manifest containing every RuleVersion hash and referenced SetVersion hash";
  the signed payload is the canonical JSON profile v1 of the manifest (app/platform.py ``canonical_json``).
* RUL-REQ-027: manifests "MUST bind exact RuleVersion and SetVersion hashes and be signed by authorized release
  infrastructure". ZA-RUL-001 §02: the release is the signed package of its RequirementVersion(s) and RuleVersion(s).
* ZA-DAT-001 §05: ``catalog.content_release`` (manifest hash, signature refs) — "Cells verify before activation";
  DAT-REQ-012: PUBLISHED catalogue artifacts immutable; PRD FR-REG-001: released requirement versions link to the
  authoritative source reference and fingerprint.
* SEC-REQ-048: a content-signature verification failure raises a security signal; ZA-SEC-001 §18 key/signing telemetry:
  "Key ID/purpose, caller workload, operation class, result; never key bytes".

What is NOT established, and therefore never reported as VALID: that the signing key is a trusted, purpose-separated
content-release authority key. Releases are signed by this cell's single mixed-purpose Ed25519 key (engines/keys.py;
audit I-05), which ZA-ADR-001 C-07 and SEC-REQ-026 do not allow as that authority; RUL-ADR-06 (T0) is unapproved and
ZA-KEY-001, which RUL §12 names for the cryptographic profile, does not exist. Until decisions K1-K6
(docs/backend-decision-brief-content-signature-trust.md) are made, a release whose every check passes is UNVERIFIABLE,
with the reason stated, and a release with any failed check is INVALID.

The engine is read-only on catalogue content and deterministic: the same stored state gives the same report.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db import CELL_ID
from ..models import AuditEvent, ContentRelease, Provision, RegulatorySource, RequirementVersion, RuleVersion
from ..platform import audit, canonical_json, emit, next_record_seq, sha256_hex
from . import catalog as cat
from . import keys

VERIFIER_VERSION = "za-release-verifier:1.0"
PASS, FAIL, UNVERIFIABLE = "PASS", "FAIL", "UNVERIFIABLE"
VALID, INVALID = "VALID", "INVALID"
# The only signature algorithm the platform implements (engines/keys.py). A release naming any other is not verifiable
# here; no other algorithm is assumed (SEC-REQ-049, no ZA-KEY-001 profile).
SUPPORTED_ALGORITHMS = {"ed25519": 64}  # algorithm -> signature length in bytes
RELEASED_LIFECYCLES = frozenset({"PUBLISHED", "SUPERSEDED", "WITHDRAWN"})
TRUST = {
    "status": "NOT_ESTABLISHED",
    "reason": ("No trust anchor for content-release signatures is defined. Releases are signed with this cell's single "
               "mixed-purpose key (audit I-05), which ZA-ADR-001 C-07 and SEC-REQ-026 do not permit as the content-"
               "release signing authority; RUL-ADR-06 (T0) is not approved and ZA-KEY-001 does not exist. A valid "
               "signature proves only that this cell's key signed the manifest."),
    "required_decisions": ["K1 trusted content-release key and its distribution", "K2 key identity, versioning and "
                           "rotation", "K3 algorithm/signature profile (ZA-KEY-001)", "K4 where verification runs "
                           "(activation, binding evaluation)", "K5 disposition of releases signed with the cell key",
                           "K6 KMS/HSM provider"],
    "references": ["RUL-REQ-027", "RUL-ADR-06", "ZA-ADR-001 C-07", "SEC-REQ-026", "SEC-REQ-049", "ZA-RUL-001 §12"],
}


def outcome_of(checks: list[dict]) -> str:
    """INVALID if any check failed; otherwise UNVERIFIABLE if any could not be decided; otherwise VALID."""
    if any(c["status"] == FAIL for c in checks):
        return INVALID
    if any(c["status"] == UNVERIFIABLE for c in checks):
        return UNVERIFIABLE
    return VALID


def _add(checks: list, dimension: str, check: str, status: str, message: str, **ids: Any) -> None:
    checks.append({"dimension": dimension, "check": check, "status": status, "message": message, **ids})


def _signature(cr: ContentRelease, payload: bytes | None, checks: list) -> dict:
    """Signature present, well-formed, of a supported algorithm, under a key this cell holds, and cryptographically
    valid over the exact canonical manifest bytes. Never treats a stored signature as valid without checking it."""
    key_id = (cr.signer_key_id or "").strip()
    algorithm = key_id.split(":", 1)[0].lower() if ":" in key_id else None
    available = keys.existing_public_key()
    available_id = keys.key_id_of(available) if available is not None else None
    out = {"status": None, "algorithm": algorithm, "key_id": key_id or None, "verification_key_id": available_id,
           "key_purpose": "CELL_KEY_NOT_PURPOSE_SEPARATED"}
    sig_text = (cr.signature or "").strip()
    if not sig_text:
        _add(checks, "signature", "SIGNATURE_PRESENT", FAIL, "The release carries no signature.")
        out["status"] = "MISSING"
        return out
    _add(checks, "signature", "SIGNATURE_PRESENT", PASS, "A signature is recorded (not yet checked).")
    if algorithm not in SUPPORTED_ALGORITHMS:
        _add(checks, "signature", "SIGNATURE_ALGORITHM", UNVERIFIABLE,
             f"The release names signature algorithm {algorithm!r}; this verifier implements only "
             f"{sorted(SUPPORTED_ALGORITHMS)}.", key_id=key_id or None)
        out["status"] = "UNSUPPORTED_ALGORITHM"
        return out
    _add(checks, "signature", "SIGNATURE_ALGORITHM", PASS, f"Algorithm {algorithm} is supported.")
    try:
        sig = bytes.fromhex(sig_text)
    except ValueError:
        sig = None
    if sig is None or len(sig) != SUPPORTED_ALGORITHMS[algorithm]:
        _add(checks, "signature", "SIGNATURE_FORMAT", FAIL, "The signature is not a well-formed "
             f"{SUPPORTED_ALGORITHMS[algorithm]}-byte {algorithm} signature.")
        out["status"] = "MALFORMED"
        return out
    _add(checks, "signature", "SIGNATURE_FORMAT", PASS, "Well-formed signature.")
    if available is None or available_id != key_id:
        _add(checks, "signature", "SIGNER_KEY_AVAILABLE", UNVERIFIABLE,
             f"No verification key for {key_id} is available to this cell (it holds {available_id or 'no key'}); "
             "there is no key registry or rotation record (decision K2).", key_id=key_id)
        out["status"] = "KEY_UNAVAILABLE"
        return out
    _add(checks, "signature", "SIGNER_KEY_AVAILABLE", PASS, f"Verification key {key_id} is available.", key_id=key_id)
    if payload is None:
        _add(checks, "signature", "SIGNATURE", FAIL, "No signed payload can be reconstructed from the manifest.")
        out["status"] = INVALID
        return out
    ok = keys.verify_with(available, payload, sig)
    _add(checks, "signature", "SIGNATURE", PASS if ok else FAIL,
         "The signature verifies over the exact canonical manifest." if ok else
         "The signature does not verify over the canonical manifest: the manifest or the signature was altered or "
         "substituted.", key_id=key_id)
    out["status"] = VALID if ok else INVALID
    return out


def _rule_versions(db, cr: ContentRelease, manifest: dict, checks: list) -> None:
    from . import applicability as appl
    listed = []
    for entry in manifest.get("rule_versions") or []:
        rid = entry.get("rule_version_id") if isinstance(entry, dict) else None
        listed.append(rid)
        rv = db.get(RuleVersion, rid) if rid else None
        if rv is None:
            _add(checks, "integrity", "RULE_VERSION_PRESENT", FAIL, "The manifest names a rule version that does not exist.",
                 rule_version_id=rid)
            continue
        if rv.content_release_id != cr.content_release_id:
            _add(checks, "integrity", "RULE_VERSION_BINDS_RELEASE", FAIL, "The rule version names a different release.",
                 rule_version_id=rid, names_release=rv.content_release_id)
            continue
        try:  # A9a's checks: set pins, AST hash under declared versions, release status/hash, exact manifest entry
            failure = appl._integrity_failure(db, rv)
        except (KeyError, TypeError, AttributeError, ValueError):
            failure = ("MALFORMED_CONTENT", rid)
        _add(checks, "integrity", "RULE_VERSION_INTEGRITY", FAIL if failure else PASS,
             f"{failure[0]} ({failure[1]})" if failure else "AST hash, SetVersion pins and manifest entry match.",
             rule_version_id=rid)
    claiming = db.execute(select(RuleVersion.rule_version_id).where(RuleVersion.content_release_id == cr.content_release_id)
                          .order_by(RuleVersion.rule_version_id)).scalars().all()
    extra = sorted(set(claiming) - set(listed))
    _add(checks, "integrity", "RELEASE_COVERS_RULE_VERSIONS", FAIL if extra or not listed else PASS,
         (f"Rule versions name this release but the signed manifest does not list them: {extra}" if extra else
          "The manifest lists no rule version." if not listed else
          "Every rule version naming this release is in its signed manifest."), **({"rule_version_ids": extra} if extra else {}))


def _requirements(db, manifest: dict, checks: list) -> None:
    bound = manifest.get("requirement_versions")
    if bound is None:
        _add(checks, "integrity", "REQUIREMENT_BINDING", UNVERIFIABLE, "The release predates requirement binding: its "
             "manifest does not name the requirement versions and source fingerprints it rests on (ZA-RUL-001 §02).")
        return
    if not isinstance(bound, list) or not all(isinstance(b, dict) for b in bound):
        _add(checks, "integrity", "REQUIREMENT_BINDING", FAIL, "The manifest's requirement binding is malformed.")
        return
    _add(checks, "integrity", "REQUIREMENT_BINDING", PASS, f"The manifest binds {len(bound)} requirement version(s).")
    for b in bound:
        rid = b.get("requirement_version_id")
        ids = {"requirement_version_id": rid}
        rv = db.get(RequirementVersion, rid) if rid else None
        if rv is None:
            _add(checks, "integrity", "REQUIREMENT_VERSION_PRESENT", FAIL, "A bound requirement version no longer exists.", **ids)
        else:
            changed = [k for k in ("requirement_key", "source_id", "locator") if getattr(rv, k) != b.get(k)]
            _add(checks, "integrity", "REQUIREMENT_VERSION_IDENTITY", FAIL if changed else PASS,
                 f"Changed since release: {changed}" if changed else "Key, source and locator match the release.", **ids)
            _add(checks, "integrity", "REQUIREMENT_VERSION_LIFECYCLE",
                 PASS if rv.lifecycle in RELEASED_LIFECYCLES else FAIL,
                 f"Lifecycle {rv.lifecycle}" + ("" if rv.lifecycle in RELEASED_LIFECYCLES else
                                                ": a released version cannot return to a pre-release state."), **ids)
            pids = list(b.get("provision_ids") or [])
            provs = {p.provision_id: p for p in db.execute(select(Provision).where(Provision.provision_id.in_(pids or ["-"]))).scalars()}
            bad = sorted(p for p in pids if p not in provs or provs[p].requirement_version_id != rid)
            _add(checks, "integrity", "PROVISIONS_PRESENT", FAIL if bad else PASS,
                 f"Bound provisions missing or moved: {bad}" if bad else f"All {len(pids)} bound provision(s) present.", **ids)
            if "content_sha256" not in b:
                _add(checks, "integrity", "REQUIREMENT_CONTENT_DIGEST", UNVERIFIABLE, "The release records no content "
                     "digest for this requirement version (bound before content digests).", **ids)
            elif b.get("content_profile") != cat.CONTENT_PROFILE:
                _add(checks, "integrity", "REQUIREMENT_CONTENT_DIGEST", UNVERIFIABLE,
                     f"Unknown content digest profile {b.get('content_profile')!r}.", **ids)
            elif not bad:
                digest = cat.requirement_content_digest(rv, [provs[p] for p in pids])
                _add(checks, "integrity", "REQUIREMENT_CONTENT_DIGEST", PASS if digest == b["content_sha256"] else FAIL,
                     "Requirement and provision content match the release." if digest == b["content_sha256"] else
                     "Requirement or provision content changed after release.", **ids)
        sid = b.get("source_id")
        src = db.get(RegulatorySource, sid) if sid else None
        sids = {**ids, "source_id": sid}
        if src is None:
            _add(checks, "integrity", "SOURCE_PRESENT", FAIL, "The bound source snapshot no longer exists.", **sids)
            continue
        _add(checks, "integrity", "SOURCE_FINGERPRINT", PASS if src.fingerprint == b.get("source_fingerprint") else FAIL,
             "Source fingerprint matches the release." if src.fingerprint == b.get("source_fingerprint") else
             "The source record's fingerprint differs from the one the release bound.", **sids)
        if "source_locator" in b:
            _add(checks, "integrity", "SOURCE_LOCATOR", PASS if src.locator == b["source_locator"] else FAIL,
                 "Authoritative reference matches." if src.locator == b["source_locator"] else
                 "The source's authoritative reference changed after release.", **sids)
        if src.raw_text is None:
            _add(checks, "integrity", "SOURCE_CONTENT", UNVERIFIABLE, "The source text is not stored; the fingerprint is "
                 "checked by reference only.", **sids)
        else:
            intact = cat.fingerprint_intact(src)
            _add(checks, "integrity", "SOURCE_CONTENT", PASS if intact else FAIL,
                 "Stored source text re-hashes to its fingerprint." if intact else
                 "Stored source text no longer matches its fingerprint.", **sids)


def verify_release(db, cr: ContentRelease) -> dict:
    """The verification report for one release. Read-only; deterministic for a given stored state."""
    checks: list[dict] = []
    manifest = cr.manifest if isinstance(cr.manifest, dict) else None
    if manifest is None or not isinstance(manifest.get("rule_versions"), list):
        _add(checks, "integrity", "MANIFEST_WELL_FORMED", FAIL, "The manifest is missing or malformed.")
        payload = None
    else:
        _add(checks, "integrity", "MANIFEST_WELL_FORMED", PASS, "The manifest is well formed.")
        payload = canonical_json(manifest)
    actual = sha256_hex(payload) if payload is not None else None
    hash_ok = actual is not None and actual == cr.manifest_hash
    _add(checks, "integrity", "MANIFEST_HASH", PASS if hash_ok else FAIL,
         "The manifest re-hashes to the recorded manifest hash." if hash_ok else
         "The manifest does not re-hash to the recorded manifest hash.", recorded=cr.manifest_hash, recomputed=actual)
    if manifest is not None:
        same = manifest.get("label") == cr.label
        _add(checks, "integrity", "MANIFEST_MATCHES_RELEASE_RECORD", PASS if same else FAIL,
             "The signed manifest names this release's label." if same else
             "The release record's label is not the one in its signed manifest (substituted content).")
    _add(checks, "integrity", "RELEASE_STATUS", PASS if cr.status == "PUBLISHED" else FAIL, f"Release status {cr.status}.")
    signature = _signature(cr, payload, checks)
    _add(checks, "trust", "SIGNER_TRUST", UNVERIFIABLE, TRUST["reason"])
    if manifest is not None:
        _rule_versions(db, cr, manifest, checks)
        _requirements(db, manifest, checks)
    altered = any(c["status"] == FAIL and c["dimension"] == "integrity" for c in checks)
    public = keys.existing_public_key()
    return {
        "content_release_id": cr.content_release_id, "label": cr.label, "published_at": cr.published_at,
        "verifier_version": VERIFIER_VERSION, "outcome": outcome_of(checks),
        "integrity": "ALTERED" if altered else "INTACT", "signature": signature, "trust": TRUST,
        "manifest_hash": cr.manifest_hash, "checks": checks,
        "summary": {s.lower(): sum(1 for c in checks if c["status"] == s) for s in (PASS, FAIL, UNVERIFIABLE)},
        # The response fields this route returned before A9b, with unchanged meaning (frontend consumers).
        "manifest_hash_ok": hash_ok, "signature_ok": signature["status"] == VALID, "signer_key_id": cr.signer_key_id,
        "public_key_pem": keys.public_pem_of(public) if public is not None else None,
    }


def result_fingerprint(report: dict) -> str:
    """Identity of one verification *result*: what was concluded, by which verifier, with which failed and undecided
    checks. Two verifications with the same fingerprint observed the same thing."""
    failed = [c["check"] for c in report["checks"] if c["status"] == FAIL]
    undecided = [c["check"] for c in report["checks"] if c["status"] == UNVERIFIABLE]
    return sha256_hex(canonical_json({
        "release": report["content_release_id"], "verifier": VERIFIER_VERSION, "outcome": report["outcome"],
        "integrity": report["integrity"], "signature": report["signature"]["status"],
        "key_id": report["signature"]["key_id"], "trust": report["trust"]["status"], "failed": failed,
        "unverifiable": undecided}))[:32]


def record(db, report: dict, *, actor) -> bool:
    """Record a verification result as a key/signing security record (ZA-SEC-001 §18 telemetry: key ID/purpose,
    caller, operation class, result; never key bytes) in the caller's own audit trail. An INVALID result also raises
    SEC-REQ-048's security signal, ``security.content_signature_failed``, as the A9a integrity check does.

    Bounded (audit finding F-15). The route is a read that any catalogue reader may repeat, so a record is written when
    this principal observes a result it has not observed as its latest one: the first verification, and every change of
    outcome, integrity, signature, trust or failed/undecided checks. An identical repeat writes nothing. Records are
    therefore bounded by principals x result changes, not by request count. Every distinct failure is still recorded
    and still raises the signal once; a release that is altered, restored and altered again is recorded each time.
    The fingerprint is kept in the audit text. Returns whether a record was written. Records are serialised per release
    and principal (engines/locks.py), so concurrent identical first reads write one record."""
    from .locks import xact_lock
    tenant_id = getattr(actor, "tenant_id", None)
    actor_id = getattr(actor, "actor_id", None)
    cr_id = report["content_release_id"]
    fp = result_fingerprint(report)
    xact_lock(db, "content.verify", cr_id, tenant_id or "-", actor_id or "-")
    last = db.execute(select(AuditEvent.reason).where(
        AuditEvent.action == "content.verify", AuditEvent.object_id == cr_id,
        AuditEvent.tenant_id.is_(None) if tenant_id is None else AuditEvent.tenant_id == tenant_id,
        AuditEvent.actor_id.is_(None) if actor_id is None else AuditEvent.actor_id == actor_id)
        .order_by(AuditEvent.record_seq.desc(), AuditEvent.id.desc()).limit(1)).scalar()
    if last is not None and f"fingerprint={fp}" in last:
        return False
    failed = [c["check"] for c in report["checks"] if c["status"] == FAIL]
    undecided = [c["check"] for c in report["checks"] if c["status"] == UNVERIFIABLE]
    seq = next_record_seq(db)
    audit(db, action="content.verify", object_type="ContentRelease", object_id=cr_id, actor=actor,
          tenant_id=tenant_id, new=report["outcome"], record_seq=seq,
          reason=(f"operation=verify purpose=content_release key_id={report['signature']['key_id']} "
                  f"verifier={VERIFIER_VERSION} result={report['outcome']} integrity={report['integrity']} "
                  f"signature={report['signature']['status']} trust={report['trust']['status']} "
                  f"fingerprint={fp} failed={failed} unverifiable={undecided}")[:2000])
    _signal(db, report, failed, tenant_id, seq)
    return True


def _signal(db, report: dict, failed: list, tenant_id, seq: int) -> None:
    if report["outcome"] == INVALID:
        first = next(c for c in report["checks"] if c["status"] == FAIL)
        emit(db, event_type="security.content_signature_failed", aggregate_type="ContentRelease",
             aggregate_id=report["content_release_id"], tenant_id=tenant_id, record_seq=seq,
             payload={"content_release_id": report["content_release_id"], "cell_id": CELL_ID,
                      "verification_result": first["check"], "failed_checks": failed,
                      "artifact_id": first.get("rule_version_id") or first.get("requirement_version_id")
                      or first.get("source_id") or report["content_release_id"]})
