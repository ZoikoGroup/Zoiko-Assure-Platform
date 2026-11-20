"""Regulatory catalogue & content operations (ZA-ARCH-001 §7, ZA-DOM-001 §4, ZA-DAT-001 §05, ZA-GLS-001 §04).

Sources, requirement versions and provisions follow the content lifecycle in app/engines/catalog.py; every lifecycle move
and every provision edit is recorded (append-only audit). Traceability: source -> requirement versions -> provisions ->
obligation definitions -> rule releases, and back (``/trace`` routes), with an end-to-end link check
(``/traceability/validation``).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, tuple_

from .. import codes, pagination
from ..cache import cached_catalog_read
from ..db import CELL_ID
from ..engines import catalog as cat
from ..engines import corrections, release_verification
from ..models import (ApplicabilityEvaluation, AuditEvent, ContentRelease, ControlDefinition, FactDefinition,
                      FrameworkMapping, Instrument, ObligationDefinition, Provision, RegulatorySource,
                      RequirementVersion, SetVersion)
from ..platform import audit, canonical_json, current_watermark, emit, next_record_seq, sha256_hex, to_dict
from ..security import CONTENT_READERS, DOSSIER_READ, Ctx, authorize

router = APIRouter(prefix="/catalog")

CATALOG_READ = (*DOSSIER_READ, *CONTENT_READERS)
LIFECYCLES = Literal["DISCOVERED", "INGESTED", "CLASSIFIED", "AI_EXTRACTED", "REVIEW_REQUIRED", "VERIFIED", "PUBLISHED",
                     "SUPERSEDED", "WITHDRAWN"]
# Strict request models: unknown fields refused (API-REQ-014), surrounding whitespace removed, lengths = column sizes
# (API-REQ-033).
STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


@router.get("/instruments")
def instruments(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    # Every list in a stable total order (the same orders /obligation-definitions and /requirements use), never
    # PostgreSQL's physical row order, which a lifecycle UPDATE (verification, publication, a link edit) can change.
    # Three reads in all, not two per instrument: each instrument's definitions and requirement versions are its share of
    # one ordered read of each table, so their order within an instrument is the order the per-instrument reads had.
    odefs_by: dict[str, list] = {}
    for o in ctx.db.execute(select(ObligationDefinition).order_by(ObligationDefinition.key,
                                                                  ObligationDefinition.obligation_definition_id)).scalars():
        odefs_by.setdefault(o.instrument_id, []).append(o)
    reqs_by: dict[str, list] = {}
    for r in ctx.db.execute(select(RequirementVersion).order_by(RequirementVersion.requirement_key,
                                                                RequirementVersion.record_seq,
                                                                RequirementVersion.requirement_version_id)).scalars():
        reqs_by.setdefault(r.instrument_id, []).append(r)
    out = []
    for i in ctx.db.execute(select(Instrument).order_by(Instrument.identifier, Instrument.instrument_id)).scalars():
        d = to_dict(i)
        d["obligation_definitions"] = [to_dict(o) for o in odefs_by.get(i.instrument_id, [])]
        d["requirements"] = [to_dict(r) for r in reqs_by.get(i.instrument_id, [])]
        out.append(d)
    return out


# ---------------------------------------------------------------- sources
@router.get("/sources")
def sources(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    # Newest first, as before; snapshots fetched at the same instant are ordered by record sequence, then ID.
    rows = ctx.db.execute(select(RegulatorySource).order_by(
        RegulatorySource.fetched_at.desc(), RegulatorySource.record_seq.desc(), RegulatorySource.source_id)).scalars().all()
    out = []
    for s in rows:
        d = to_dict(s)
        if s.rights_class == "REFERENCE_ONLY":  # source-rights metadata controls display (FR-REG-005)
            d["raw_text"] = None
            d["display_note"] = "Text withheld under rights record; locator and fingerprint retained."
        out.append(d)
    return out


class SourceIn(BaseModel):
    model_config = STRICT
    authority: str = Field(min_length=2, max_length=200)
    jurisdiction: str = Field(min_length=2, max_length=32)
    source_type: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=2, max_length=300)
    locator: str = Field(min_length=1, max_length=500)  # the authoritative reference (DAT §05)
    rights_class: str = Field(default="OFFICIAL_REUSE_PERMITTED", min_length=2, max_length=48)
    raw_text: str = Field(min_length=1)  # the fingerprint is taken over it; an empty source proves nothing
    publication_date: date | None = None
    effective_date: date | None = None
    # Optional (blank = none): without it the source stays INGESTED until classified (POST /sources/{id}/classify).
    # A given identifier must name a registered instrument.
    instrument_identifier: str | None = Field(default=None, max_length=120)


@router.post("/sources")
def ingest_source(body: SourceIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """Register one source snapshot (DOM §4.1 SourceSnapshot: content digest + rights basis). The same snapshot of the
    same source is not registered twice; a changed text of the same source is a new snapshot (see /versions)."""
    db = ctx.db
    fp = sha256_hex(body.raw_text)
    inst = cat.lock_instrument(db, body.instrument_identifier) if body.instrument_identifier else None
    dup = cat.find_duplicate_snapshot(db, authority=body.authority, jurisdiction=body.jurisdiction, locator=body.locator,
                                      fingerprint=fp)
    if dup:
        raise HTTPException(409, {"error": "DUPLICATE_SOURCE_SNAPSHOT", "source_id": dup.source_id,
                                  "message": "This snapshot of this source is already registered."})
    seq = next_record_seq(db)
    s = RegulatorySource(authority=body.authority, jurisdiction=body.jurisdiction, source_type=body.source_type,
                         title=body.title, locator=body.locator, fingerprint=fp,
                         rights_class=body.rights_class, rights_record={"class": body.rights_class,
                                                                        "store": True, "display": body.rights_class != "REFERENCE_ONLY",
                                                                        "derive": True, "model_use": body.rights_class == "OFFICIAL_REUSE_PERMITTED"},
                         raw_text=body.raw_text, publication_date=body.publication_date,
                         effective_date=body.effective_date, lifecycle="INGESTED", record_seq=seq)
    db.add(s)
    db.flush()
    prior = [v for v in cat.source_versions(db, s) if v.source_id != s.source_id]
    audit(db, action="source.ingest", object_type="RegulatorySource", object_id=s.source_id, actor=ctx.actor,
          new="INGESTED", record_seq=seq,
          reason=(f"Snapshot {len(prior) + 1} of {s.authority} {s.locator}; fingerprint {fp}"
                  + (f"; previous snapshot {prior[-1].source_id}" if prior else ""))[:2000])
    if inst:
        cat.classify(ctx, s, inst)
    emit(db, event_type="regulatory.source_ingested", aggregate_type="RegulatorySource", aggregate_id=s.source_id,
         record_seq=seq, payload={"fingerprint": s.fingerprint, "jurisdiction": s.jurisdiction, "lifecycle": s.lifecycle})
    db.commit()
    return to_dict(s)


@router.get("/sources/{source_id}")
def source(source_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    s = ctx.db.get(RegulatorySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    d = cat.source_view(s)
    rvs = ctx.db.execute(select(RequirementVersion).where(RequirementVersion.source_id == source_id)
                         .order_by(RequirementVersion.requirement_key, RequirementVersion.record_seq,
                                   RequirementVersion.requirement_version_id)).scalars().all()
    numbers = cat.version_numbers(ctx.db, rvs)  # one read for every row, not one per row
    held = _instruments(ctx.db, rvs)  # requirement_ref's db.get finds them loaded
    d["requirement_versions"] = [cat.requirement_ref(ctx.db, rv, numbers=numbers) for rv in rvs]
    del held
    return d


def _instruments(db, rvs) -> list:
    """The instruments these requirement versions name, read in one query and returned so the caller holds them in the
    session (its identity map holds rows weakly) while requirement_ref looks them up with db.get."""
    ids = sorted({r.instrument_id for r in rvs})
    return db.execute(select(Instrument).where(Instrument.instrument_id.in_(ids))).scalars().all() if ids else []


@router.get("/sources/{source_id}/versions")
def source_versions(source_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Every snapshot of this source (same authority, jurisdiction and authoritative locator), oldest first, with
    whether its fingerprint differs from the snapshot before it."""
    s = ctx.db.get(RegulatorySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    out, prev = [], None
    snapshots = cat.source_versions(ctx.db, s)
    counts = dict(ctx.db.execute(select(RequirementVersion.source_id, func.count()).where(
        RequirementVersion.source_id.in_([v.source_id for v in snapshots])).group_by(RequirementVersion.source_id)).all())
    for i, v in enumerate(snapshots, start=1):
        out.append({**cat.source_ref(v), "snapshot_number": i, "fetched_at": v.fetched_at,
                    "fingerprint_changed": prev is not None and prev.fingerprint != v.fingerprint,
                    "requirement_versions": counts.get(v.source_id, 0)})
        prev = v
    return {"source_identity": {"authority": s.authority, "jurisdiction": s.jurisdiction, "locator": s.locator},
            "snapshots": out}


@router.get("/sources/{source_id}/diff")
def source_diff(source_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """WF-01 Regulatory Change Inbox "view diff" (ZA-PRD-001 §13): this source snapshot against the previous snapshot of
    the same source (cat.snapshot_diff: rights-gated, integrity-checked line difference of the stored texts), and the
    requirement versions grounded in each of the two snapshots, by requirement key (a requirement's stable identity
    within its instrument). Provisions are counted, not paired: a provision has no identity that carries across
    requirement versions in the stored model, so which provision "became" which is not derived. Read-only; the same
    readers as the source record itself (GET /catalog/sources/{id}), which already shows each text the diff can show."""
    db = ctx.db
    s = db.get(RegulatorySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    out = cat.snapshot_diff(db, s)
    sides = {"current": source_id, **({"previous": out["previous"]["source_id"]} if out["previous"] else {})}
    rvs = db.execute(select(RequirementVersion).where(RequirementVersion.source_id.in_(list(sides.values())))
                     .order_by(RequirementVersion.requirement_key, RequirementVersion.record_seq,
                               RequirementVersion.requirement_version_id)).scalars().all()
    numbers = cat.version_numbers(db, rvs)
    held = _instruments(db, rvs)
    counts = dict(db.execute(select(Provision.requirement_version_id, func.count()).where(
        Provision.requirement_version_id.in_([r.requirement_version_id for r in rvs] or ["-"]))
        .group_by(Provision.requirement_version_id)).all())
    grouped: dict[str, dict] = {}
    for rv in rvs:
        entry = grouped.setdefault(rv.requirement_key, {"requirement_key": rv.requirement_key, "previous": [],
                                                        "current": []})
        side = "current" if rv.source_id == source_id else "previous"
        entry[side].append({**cat.requirement_ref(db, rv, numbers=numbers),
                            "provision_count": counts.get(rv.requirement_version_id, 0)})
    del held
    out["requirements"] = [grouped[k] for k in sorted(grouped)]
    return out


@router.get("/sources/{source_id}/trace")
def source_trace(source_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Forward traceability: this snapshot -> requirement versions -> provisions -> obligation definitions -> rules."""
    s = ctx.db.get(RegulatorySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    return cat.trace_source(ctx.db, s)


class ClassifyIn(BaseModel):
    model_config = STRICT
    instrument_identifier: str = Field(min_length=1, max_length=120)


@router.post("/sources/{source_id}/classify")
def classify_source(source_id: str, body: ClassifyIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """INGESTED -> CLASSIFIED against a registered instrument (ZA-GLS-001 §04)."""
    s = ctx.db.execute(select(RegulatorySource).where(RegulatorySource.source_id == source_id).with_for_update()).scalars().first()
    if s is None:
        raise HTTPException(404, "source not found")
    cat.classify(ctx, s, cat.lock_instrument(ctx.db, body.instrument_identifier))
    ctx.db.commit()
    return to_dict(s)


# ---------------------------------------------------------------- requirement versions
# Longest alternatives first: "must not" was matched as "must", so a prohibition was proposed as MUST.
MODAL = re.compile(r"\b(shall not|must not|is required to|are required to|shall|must)\b", re.I)


class ExtractIn(BaseModel):
    model_config = STRICT
    instrument_identifier: str = Field(min_length=1, max_length=120)
    requirement_key: str = Field(min_length=2, max_length=120)
    requirement_title: str = Field(min_length=2, max_length=300)
    locator: str = Field(min_length=1, max_length=195)  # proposals append "(n)"; the provision locator holds 200
    valid_from: date


@router.post("/sources/{source_id}/extract")
def extract(source_id: str, body: ExtractIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """Governed extractor proposal (non-binding). Output is AI_EXTRACTED -> REVIEW_REQUIRED (INV-09)."""
    db = ctx.db
    s = db.get(RegulatorySource, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    inst = cat.lock_instrument(db, body.instrument_identifier)
    proposals = []
    for i, sent in enumerate([x.strip() for x in re.split(r"(?<=[.;])\s+", s.raw_text or "") if MODAL.search(x)][:20], start=1):
        m = MODAL.search(sent)
        proposals.append({"locator": f"{body.locator}({i})", "duty_bearer": cat.UNREVIEWED,
                          "modality": "MUST_NOT" if "not" in m.group(0).lower() else "MUST",
                          "action": sent[m.end():].strip()[:110] or "TBD", "regulated_object": cat.UNREVIEWED,
                          "normalized_meaning": sent})
    rv, props = cat.create_version(ctx, source=s, instrument=inst, requirement_key=body.requirement_key,
                                   title=body.requirement_title, locator=body.locator, summary=None,
                                   valid_from=body.valid_from, valid_to=None, provisions=proposals, origin="AI_PROPOSED",
                                   provision_origin="AI_PROPOSED", provision_lifecycle="AI_EXTRACTED")
    emit(db, event_type="regulatory.extraction_proposed", aggregate_type="RequirementVersion",
         aggregate_id=rv.requirement_version_id, record_seq=rv.record_seq,
         payload={"proposals": len(props), "extractor": "governed_extractor:modal-heuristic-v1", "binding": False})
    db.commit()
    return {"requirement_version": to_dict(rv), "proposed_provisions": [to_dict(p) for p in props],
            "note": "Proposals are non-binding; a qualified human reviewer must VERIFY before any release."}


class ProvisionIn(BaseModel):
    model_config = STRICT
    locator: str = Field(min_length=1, max_length=200)
    duty_bearer: str = Field(min_length=2, max_length=64)
    modality: str = Field(min_length=2, max_length=32)
    action: str = Field(min_length=2, max_length=120)
    regulated_object: str = Field(min_length=2, max_length=200)
    timing: str | None = Field(default=None, max_length=120)
    normalized_meaning: str = Field(min_length=2, max_length=4000)
    evidence_expectation: str | None = Field(default=None, max_length=4000)


class RequirementIn(BaseModel):
    model_config = STRICT
    source_id: str = Field(min_length=1, max_length=36)
    instrument_identifier: str = Field(min_length=1, max_length=120)
    requirement_key: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=300)
    locator: str = Field(min_length=1, max_length=200)  # the authoritative locator within the source
    summary: str | None = Field(default=None, max_length=4000)
    valid_from: date
    valid_to: date | None = None
    provisions: list[ProvisionIn] = Field(min_length=1, max_length=50)


@router.post("/requirements", status_code=201)
def create_requirement(body: RequirementIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """A Zoiko-encoded requirement version with its atomic provisions, in REVIEW_REQUIRED (ZA-GLS-001 §04: "Content
    analyst"). A new version of an existing requirement uses the same requirement_key under the same instrument; earlier
    versions are never changed."""
    db = ctx.db
    s = db.get(RegulatorySource, body.source_id)
    inst = cat.lock_instrument(db, body.instrument_identifier)
    rv, props = cat.create_version(ctx, source=s, instrument=inst, requirement_key=body.requirement_key, title=body.title,
                                   locator=body.locator, summary=body.summary, valid_from=body.valid_from,
                                   valid_to=body.valid_to, provisions=[p.model_dump() for p in body.provisions],
                                   origin="ZOIKO_ENCODED", provision_origin="ZOIKO_ENCODED",
                                   provision_lifecycle="REVIEW_REQUIRED")
    db.commit()
    return {**to_dict(rv), "version_number": cat.version_number(db, rv), "provisions": [to_dict(p) for p in props]}


REQ_PAGE_DEFAULT, REQ_PAGE_MAX = 50, 200


@router.get("/requirements")
def list_requirements(instrument: str | None = Query(None, min_length=1, max_length=120),
                      requirement_key: str | None = Query(None, min_length=1, max_length=120),
                      lifecycle: LIFECYCLES | None = None,
                      origin: str | None = Query(None, min_length=2, max_length=32, pattern=r"^[A-Z][A-Z_]*$"),
                      source_id: str | None = Query(None, min_length=1, max_length=36),
                      jurisdiction: str | None = Query(None, min_length=2, max_length=32),
                      valid_at: date | None = None,
                      limit: int = Query(REQ_PAGE_DEFAULT, ge=1, le=REQ_PAGE_MAX),
                      cursor: str | None = Query(None, min_length=1, max_length=2048),
                      ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Requirement versions, filtered by allowlisted typed parameters (API-REQ-023) and cursor-paginated in a stable
    order (requirement_key, record_seq, id) with the record watermark pinned on the first page (API-REQ-012)."""
    db = ctx.db
    filters = {"instrument": instrument, "requirement_key": requirement_key, "lifecycle": lifecycle, "origin": origin,
               "source_id": source_id, "jurisdiction": jurisdiction, "valid_at": valid_at and valid_at.isoformat(),
               "limit": limit, "sort": "requirement_key,record_seq,id"}
    fp, scope = pagination.query_fingerprint(filters), "catalog-requirements"
    q = select(RequirementVersion)
    if instrument or jurisdiction:
        q = q.join(Instrument, Instrument.instrument_id == RequirementVersion.instrument_id)
        if instrument:
            q = q.where(Instrument.identifier == instrument)
        if jurisdiction:
            q = q.where(Instrument.jurisdiction == jurisdiction)
    for col, val in ((RequirementVersion.requirement_key, requirement_key), (RequirementVersion.lifecycle, lifecycle),
                     (RequirementVersion.origin, origin), (RequirementVersion.source_id, source_id)):
        if val:
            q = q.where(col == val)
    if valid_at:
        q = q.where(RequirementVersion.valid_from <= valid_at,
                    or_(RequirementVersion.valid_to.is_(None), RequirementVersion.valid_to > valid_at))
    if cursor:
        c = pagination.decode_cursor(cursor, scope=scope, query=fp)
        pinned = c["watermark"]
        try:
            k, seq, rid = str(c["position"][0]), int(c["position"][1]), str(c["position"][2])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, {"error": "invalid_cursor", "message": "The cursor is not valid."}) from None
        # The rows after the cursor in the page order, as one row-value comparison over the exact ordering tuple: the
        # same rows as "key > k OR (key = k AND seq > s) OR (key = k AND seq = s AND id > r)" (all three columns are
        # NOT NULL), but a form the planner can seek to through the requirement_key index instead of reading every row
        # before the cursor (catalogue index readiness assessment, step 0).
        q = q.where(tuple_(RequirementVersion.requirement_key, RequirementVersion.record_seq,
                           RequirementVersion.requirement_version_id) > (k, seq, rid))
    else:
        pinned = current_watermark(db)["record_seq"]
    q = q.where(RequirementVersion.record_seq <= pinned)
    rows = db.execute(q.order_by(RequirementVersion.requirement_key, RequirementVersion.record_seq,
                                 RequirementVersion.requirement_version_id).limit(limit + 1)).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    # A fixed number of reads per page, not three per row: version numbers, instruments, the sources' displayed fields
    # and provision counts, each read once for the whole page.
    numbers = cat.version_numbers(db, rows)
    held = _instruments(db, rows)
    page_sources = {s.source_id: s for s in db.execute(select(
        RegulatorySource.source_id, RegulatorySource.fingerprint, RegulatorySource.locator, RegulatorySource.lifecycle)
        .where(RegulatorySource.source_id.in_(sorted({r.source_id for r in rows}) or ["-"])))}
    counts = dict(db.execute(select(Provision.requirement_version_id, func.count()).where(
        Provision.requirement_version_id.in_([r.requirement_version_id for r in rows] or ["-"]))
        .group_by(Provision.requirement_version_id)).all())
    items = []
    for rv in rows:
        d = cat.requirement_ref(db, rv, numbers=numbers)
        src = page_sources.get(rv.source_id)
        d["source"] = {"source_id": rv.source_id, "fingerprint": src.fingerprint if src else None,
                       "locator": src.locator if src else None, "lifecycle": src.lifecycle if src else None}
        d["provision_count"] = counts.get(rv.requirement_version_id, 0)
        items.append(d)
    del held
    last = rows[-1] if rows else None
    nxt = (pagination.encode_cursor(scope=scope, query=fp, position=[last.requirement_key, last.record_seq,
                                                                     last.requirement_version_id], watermark=pinned)
           if more else None)
    return pagination.page(items, next_cursor=nxt, limit=limit, watermark={"cell_id": CELL_ID, "record_seq": pinned})


@router.get("/inbox")
def regulatory_change_inbox(lifecycle: LIFECYCLES | None = None,
                            requirement_lifecycle: LIFECYCLES | None = None,
                            instrument: str | None = Query(None, min_length=1, max_length=120),
                            jurisdiction: str | None = Query(None, min_length=2, max_length=32),
                            authority: str | None = Query(None, min_length=2, max_length=200),
                            limit: int = Query(REQ_PAGE_DEFAULT, ge=1, le=REQ_PAGE_MAX),
                            cursor: str | None = Query(None, min_length=1, max_length=2048),
                            ctx: Ctx = Depends(authorize(*CONTENT_READERS, platform=True))):
    """Regulatory Change Inbox (ZA-PRD-001 §13 WF-01): one item per source snapshot, newest first, with its regime,
    authority, publication/effective dates, ingestion time, requirement versions and their release status, linked
    obligation definitions, the signed releases binding them and the recorded shadow impact (subjects evaluated,
    outcome transitions, missing-fact counts, adopted-determination conflicts) of those releases and of pending rule
    drafts (engines/catalog.change_inbox_items).

    Content operations only (Zoiko platform principals with a content role): the inbox shows unreleased content and
    cross-organisation aggregate shadow counts (API-001 §17; corrections.shadow_summary_for), so organisation principals
    are refused. Read-only: nothing is evaluated, shadowed, recorded or changed by reading it.

    Filters are allowlisted and typed (API-REQ-023): the snapshot's lifecycle, jurisdiction and authority; ``instrument``
    and ``requirement_lifecycle`` select snapshots with a requirement version under that instrument / in that lifecycle.
    Cursor pagination in a stable total order (record_seq descending, then source_id), with the record watermark pinned
    on the first page and carried by the cursor (API-REQ-012), as GET /catalog/requirements. Severity and review
    assignment are not part of the inbox: no persisted field holds either."""
    db = ctx.db
    filters = {"lifecycle": lifecycle, "requirement_lifecycle": requirement_lifecycle, "instrument": instrument,
               "jurisdiction": jurisdiction, "authority": authority, "limit": limit, "sort": "-record_seq,source_id"}
    fp, scope = pagination.query_fingerprint(filters), "catalog-inbox"
    if cursor:
        c = pagination.decode_cursor(cursor, scope=scope, query=fp)
        pinned = c["watermark"]
        try:
            seq, sid = int(c["position"][0]), str(c["position"][1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, {"error": "invalid_cursor", "message": "The cursor is not valid."}) from None
    else:
        pinned, seq, sid = current_watermark(db)["record_seq"], None, None
    q = select(RegulatorySource).where(RegulatorySource.record_seq <= pinned)
    for col, val in ((RegulatorySource.lifecycle, lifecycle), (RegulatorySource.jurisdiction, jurisdiction),
                     (RegulatorySource.authority, authority)):
        if val:
            q = q.where(col == val)
    if instrument or requirement_lifecycle:
        sub = select(RequirementVersion.source_id).where(RequirementVersion.record_seq <= pinned)
        if instrument:
            sub = sub.join(Instrument, Instrument.instrument_id == RequirementVersion.instrument_id).where(
                Instrument.identifier == instrument)
        if requirement_lifecycle:
            sub = sub.where(RequirementVersion.lifecycle == requirement_lifecycle)
        q = q.where(RegulatorySource.source_id.in_(sub))
    if seq is not None:
        # record_seq <= seq (the cursor position's record sequence) is implied by the disjunction, so it excludes no row
        # the cursor admits; it is the bound an index on (record_seq DESC, source_id) can seek on. The mixed order
        # (record_seq descending, source_id ascending) cannot be one row-value comparison.
        q = q.where(RegulatorySource.record_seq <= seq,
                    or_(RegulatorySource.record_seq < seq,
                        and_(RegulatorySource.record_seq == seq, RegulatorySource.source_id > sid)))
    rows = db.execute(q.order_by(RegulatorySource.record_seq.desc(), RegulatorySource.source_id)
                      .limit(limit + 1)).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = cat.change_inbox_items(db, rows, pinned)
    nxt = (pagination.encode_cursor(scope=scope, query=fp, position=[rows[-1].record_seq, rows[-1].source_id],
                                    watermark=pinned) if more else None)
    return pagination.page(items, next_cursor=nxt, limit=limit, watermark={"cell_id": CELL_ID, "record_seq": pinned})


class ProvisionEdit(BaseModel):
    model_config = STRICT
    duty_bearer: str = Field(min_length=2, max_length=64)
    modality: str = Field(min_length=2, max_length=32)
    action: str = Field(min_length=2, max_length=120)
    regulated_object: str = Field(min_length=2, max_length=200)
    timing: str | None = Field(default=None, max_length=120)
    normalized_meaning: str = Field(min_length=2, max_length=4000)
    evidence_expectation: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=500)  # recorded with the edit


@router.put("/provisions/{provision_id}")
def edit_provision(provision_id: str, body: ProvisionEdit, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    p = cat.revise_provision(ctx, provision_id, body.model_dump(exclude={"reason"}), body.reason)
    ctx.db.commit()
    return to_dict(p)


@router.post("/requirements/{rv_id}/verify")
def verify_requirement(rv_id: str, ctx: Ctx = Depends(authorize("content_legal_review", human=True, platform=True))):
    """Qualified human legal review: REVIEW_REQUIRED -> VERIFIED (INV-09; AI cannot)."""
    rv = cat.verify(ctx, rv_id)
    ctx.db.commit()
    return to_dict(rv)


@router.get("/requirements/{rv_id}")
def requirement(rv_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    rv = ctx.db.get(RequirementVersion, rv_id)
    if not rv:  # named like every other catalogue 404, so it is not mistaken for an unknown route
        raise HTTPException(404, "requirement version not found")
    d = to_dict(rv)
    # A stable order, as /history and verify use: without ORDER BY PostgreSQL returns rows in physical order, which any
    # UPDATE (a lifecycle move such as publication or supersession) can change, so an unchanged version would be listed
    # differently from one read to the next.
    d["provisions"] = [to_dict(p) for p in ctx.db.execute(
        select(Provision).where(Provision.requirement_version_id == rv_id)
        .order_by(Provision.locator, Provision.provision_id)).scalars()]
    src = ctx.db.get(RegulatorySource, rv.source_id)
    d["source"] = {k: v for k, v in to_dict(src).items() if k != "raw_text" or src.rights_class != "REFERENCE_ONLY"}
    d["version_number"] = cat.version_number(ctx.db, rv)
    d["versions_total"] = len(cat.versions_of(ctx.db, rv.requirement_key))
    return d


HISTORY_OBJECTS = ("RequirementVersion", "Provision", "RegulatorySource")


@router.get("/requirements/{rv_id}/history")
def requirement_history(rv_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Every version of this requirement (same requirement_key), oldest first, each with the exact source snapshot it
    rests on. Zoiko content staff also get the recorded lifecycle and edit history of those versions, their provisions
    and sources (platform audit records are not visible to organisations)."""
    db = ctx.db
    rv = db.get(RequirementVersion, rv_id)
    if rv is None:
        raise HTTPException(404, "requirement version not found")
    versions = cat.versions_of(db, rv.requirement_key)
    # The versions are already in version_number order (versions_of), and their provisions, sources and instruments are
    # each read once for all of them, not once per version.
    numbers = {v.requirement_version_id: n for n, v in enumerate(versions, start=1)}
    provs_by: dict[str, list] = {}
    for p in db.execute(select(Provision).where(Provision.requirement_version_id.in_(
            [v.requirement_version_id for v in versions])).order_by(Provision.locator, Provision.provision_id)).scalars():
        provs_by.setdefault(p.requirement_version_id, []).append(p)
    held = [*_instruments(db, versions), *db.execute(select(RegulatorySource).where(
        RegulatorySource.source_id.in_(sorted({v.source_id for v in versions})))).scalars()]
    out, object_ids = [], set()
    for v in versions:
        provs = provs_by.get(v.requirement_version_id, [])
        object_ids |= {v.requirement_version_id, v.source_id, *(p.provision_id for p in provs)}
        out.append({**cat.requirement_ref(db, v, numbers=numbers),
                    "source": cat.source_ref(db.get(RegulatorySource, v.source_id)),
                    "provisions": [cat.provision_ref(p) for p in provs]})
    del held
    events = None
    if ctx.actor.tenant_id is None:
        events = [{"record_seq": e.record_seq, "recorded_at": e.recorded_at, "action": e.action,
                   "object_type": e.object_type, "object_id": e.object_id, "actor_id": e.actor_id,
                   "prior_state": e.prior_state, "new_state": e.new_state, "reason": e.reason}
                  for e in db.execute(select(AuditEvent).where(
                      AuditEvent.tenant_id.is_(None), AuditEvent.object_type.in_(HISTORY_OBJECTS),
                      AuditEvent.object_id.in_(sorted(object_ids) or ["-"]))
                      .order_by(AuditEvent.record_seq, AuditEvent.id)).scalars()]
    return {"requirement_key": rv.requirement_key, "versions": out, "events": events}


@router.get("/requirements/{rv_id}/trace")
def requirement_trace(rv_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Reverse traceability to the exact source snapshot, forward to provisions, obligation definitions and rules."""
    rv = ctx.db.get(RequirementVersion, rv_id)
    if rv is None:
        raise HTTPException(404, "requirement version not found")
    return cat.trace_requirement(ctx.db, rv)


@router.get("/traceability/validation")
def traceability_validation(ctx: Ctx = Depends(authorize(*CONTENT_READERS))):
    """Missing and broken links across the whole catalogue (content-operations tool; read-only)."""
    return cat.validate(ctx.db)


# ---------------------------------------------------------------- obligation definitions and other catalogue reads
@router.get("/obligation-definitions")
def odefs(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Global catalogue content, identical for every authorised caller. Authorised first (the dependency); then
    optionally served from the record-watermark-keyed cache (app/cache.py, ZA_CATALOG_CACHE)."""
    on = cat._today()

    def build():
        out = []
        # Stable order (definitions by key, as obligation_definitions_for; each one's provisions in its link order),
        # never PostgreSQL's physical row order, which any UPDATE can change.
        definitions = ctx.db.execute(select(ObligationDefinition).order_by(
            ObligationDefinition.key, ObligationDefinition.obligation_definition_id)).scalars().all()
        # A fixed number of reads, not four per definition: instruments, rule versions and linked provisions are each
        # read once for every definition (cat.currency_of / cat.definitions_provisions select exactly as
        # rule_currency / definition_provisions do, per definition and in the same order).
        held = ctx.db.execute(select(Instrument).where(Instrument.instrument_id.in_(
            sorted({o.instrument_id for o in definitions}) or ["-"]))).scalars().all()
        published = cat.published_rule_versions(ctx.db, [o.obligation_definition_id for o in definitions])
        provisions = cat.definitions_provisions(ctx.db, definitions)
        for o in definitions:
            d = to_dict(o)
            inst = ctx.db.get(Instrument, o.instrument_id)
            d["instrument"] = inst.identifier
            # current_rule_version: the latest published version (unchanged meaning: the base of a new draft), now with
            # whether it is the one in force; effective_rule_version: the one applicability binds today (TIM-REQ-026).
            cur = cat.currency_of(published.get(o.obligation_definition_id, []), on)
            rv = cur["head"]
            d["current_rule_version"] = {**to_dict(rv), "effective_status": cur["head_status"]} if rv else None
            d["effective_rule_version"] = cat._rule_ref(cur["effective"], on)
            d["provisions"] = [to_dict(p) for p in provisions[o.obligation_definition_id]]
            out.append(d)
        del held
        return out
    # Which version is in force depends on the date as well as on the records, so the date is part of the cache key.
    return cached_catalog_read(ctx.db, f"obligation_definitions.{on.isoformat()}", build)


@router.get("/obligation-definitions/{odef_id}/trace")
def odef_trace(odef_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Reverse traceability for an obligation definition: provisions -> requirement versions -> source snapshots, and
    its current rule's binding, required facts and source-mapping coverage."""
    o = ctx.db.get(ObligationDefinition, odef_id)
    if o is None:
        raise HTTPException(404, "obligation definition not found")
    return cat.trace_obligation_definition(ctx.db, o)


class LinkIn(BaseModel):
    model_config = STRICT
    requirement_version_id: str = Field(min_length=1, max_length=36)
    provision_id: str = Field(min_length=1, max_length=36)


class LinkAddIn(LinkIn):
    reason: str | None = Field(default=None, max_length=500)


class UnlinkIn(BaseModel):
    model_config = STRICT
    reason: str = Field(min_length=3, max_length=500)


class DefinitionIn(BaseModel):
    # Server-owned fields (ID, release state, rule lineage) are not accepted (API-REQ-014); lengths = column sizes.
    model_config = STRICT
    key: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=300)
    instrument_identifier: str = Field(min_length=1, max_length=120)
    subject_type: str = Field(min_length=2, max_length=40)
    coverage_mode: str = Field(default="ENCODED", min_length=2, max_length=32)
    schedule_kind: str = Field(default="CONTINUOUS", min_length=2, max_length=32)
    recurrence_days: int | None = Field(default=None, ge=1, le=3660)
    material: bool = True
    satisfaction_template: dict | None = None
    links: list[LinkIn] = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/obligation-definitions", status_code=201)
def create_obligation_definition(body: DefinitionIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """A Zoiko-authored obligation definition derived from one or more provision versions (ZA-DOM-001 §6.1, §13), with
    explicit, validated provision links. It stays unreleased (non-binding) until a rule for it is published in a signed
    release, whose gate re-checks every link (INV-06, INV-09)."""
    if body.subject_type not in cat.DEFINITION_SUBJECT_TYPES:
        raise HTTPException(422, f"subject_type must be one of {sorted(cat.DEFINITION_SUBJECT_TYPES)}")
    if body.coverage_mode not in codes.DIMENSIONS["coverage_mode"]:  # ZA-GLS-001 coverage mode codes
        raise HTTPException(422, f"coverage_mode must be one of {sorted(codes.DIMENSIONS['coverage_mode'])}")
    if body.schedule_kind not in cat.SCHEDULE_KINDS:
        raise HTTPException(422, f"schedule_kind must be one of {sorted(cat.SCHEDULE_KINDS)}")
    if (body.schedule_kind == "RECURRING") != (body.recurrence_days is not None):
        raise HTTPException(422, "recurrence_days is required for RECURRING definitions and only for them.")
    inst = cat.lock_instrument(ctx.db, body.instrument_identifier)
    o = cat.create_definition(ctx, key=body.key, title=body.title, instrument=inst, subject_type=body.subject_type,
                              coverage_mode=body.coverage_mode, schedule_kind=body.schedule_kind,
                              recurrence_days=body.recurrence_days, material=body.material,
                              satisfaction_template=body.satisfaction_template,
                              links=[(x.requirement_version_id, x.provision_id) for x in body.links], reason=body.reason)
    ctx.db.commit()
    return cat.definition_view(ctx.db, o)


@router.get("/obligation-definitions/{odef_id}")
def obligation_definition(odef_id: str, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """One definition (ZA-API-001 §16 GET /obligation-definitions/{id}) with each link resolved to its provision,
    requirement version and source snapshot, whether it is released, and its current rule."""
    o = ctx.db.get(ObligationDefinition, odef_id)
    if o is None:
        raise HTTPException(404, "obligation definition not found")
    return cat.definition_view(ctx.db, o)


@router.post("/obligation-definitions/{odef_id}/links", status_code=201)
def link_provision(odef_id: str, body: LinkAddIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """Add one explicit provision link to an unreleased definition (409 OBLIGATION_DEFINITION_RELEASED otherwise)."""
    o = cat.add_link(ctx, odef_id, body.requirement_version_id, body.provision_id, body.reason)
    ctx.db.commit()
    return cat.definition_view(ctx.db, o)


@router.post("/obligation-definitions/{odef_id}/links/{provision_id}/remove")
def unlink_provision(odef_id: str, provision_id: str, body: UnlinkIn,
                     ctx: Ctx = Depends(authorize("content_author", platform=True))):
    """Remove one link from an unreleased definition, with a reason; the last link cannot be removed."""
    o = cat.remove_link(ctx, odef_id, provision_id, body.reason)
    ctx.db.commit()
    return cat.definition_view(ctx.db, o)


@router.get("/fact-definitions")
def fdefs(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    return [to_dict(f) for f in ctx.db.execute(select(FactDefinition).order_by(FactDefinition.key)).scalars()]


@router.get("/sets")
def sets(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    return [to_dict(s) for s in ctx.db.execute(select(SetVersion).order_by(
        SetVersion.set_key, SetVersion.valid_from, SetVersion.set_version_id)).scalars()]


@router.get("/controls")
def control_defs(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Global control definitions and framework mappings; authorised first, optionally cached (app/cache.py). Stable
    order (definitions by key; mappings by framework and reference), so a cached and a fresh build are identical."""
    def build():
        # Two reads, not one per control: each control's mappings are its share of one read in the same order.
        mappings: dict[str, list] = {}
        for m in ctx.db.execute(select(FrameworkMapping).order_by(FrameworkMapping.framework, FrameworkMapping.reference,
                                                                  FrameworkMapping.id)).scalars():
            mappings.setdefault(m.control_definition_id, []).append(m)
        out = []
        for c in ctx.db.execute(select(ControlDefinition).order_by(ControlDefinition.key,
                                                                   ControlDefinition.control_definition_id)).scalars():
            d = to_dict(c)
            d["framework_mappings"] = [to_dict(m) for m in mappings.get(c.control_definition_id, [])]
            out.append(d)
        return out
    return cached_catalog_read(ctx.db, "control_definitions", build)


def shadow_summary_for(ctx: Ctx, summary: dict | None) -> dict | None:
    """The cross-organisation shadow counts, withheld from organisation principals (corrections.shadow_summary_for)."""
    return corrections.shadow_summary_for(ctx.actor.tenant_id, summary)


@router.get("/releases")
def releases(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Customer-visible changelog of signed releases with correction flags (API-001 §17). The cross-organisation
    ``shadow_summary`` is withheld from organisation readers (shadow_summary_for)."""
    out = []
    rows = ctx.db.execute(select(ContentRelease).order_by(ContentRelease.published_at.desc(),
                                                          ContentRelease.cell_received_seq.desc(),
                                                          ContentRelease.content_release_id)).scalars().all()
    # A fixed number of reads, not one or two per release: the correcting releases of every release, and (for an
    # organisation reader) its evaluations counted per rule version once. An evaluation names one rule version, so a
    # release's count is the sum over the distinct rule versions it carries, as the per-release count was.
    fixes = corrections.corrections_by_release(ctx.db)
    per_rule: dict[str, int] = {}
    if ctx.actor.tenant_id:
        carried = {r.content_release_id: [x["rule_version_id"] for x in r.manifest.get("rule_versions", [])] for r in rows}
        per_rule = dict(ctx.db.execute(select(ApplicabilityEvaluation.rule_version_id, func.count()).where(
            ApplicabilityEvaluation.tenant_id == ctx.actor.tenant_id,
            ApplicabilityEvaluation.rule_version_id.in_(list({i for ids in carried.values() for i in ids}) or ["-"]))
            .group_by(ApplicabilityEvaluation.rule_version_id)).all())
    for r in rows:
        d = to_dict(r) | corrections.correction_flags(ctx.db, r, corrections=fixes)
        d["shadow_summary"] = shadow_summary_for(ctx, r.shadow_summary)
        if ctx.actor.tenant_id:  # tenant-scoped impact view: their own impacted subjects only
            d["tenant_impacted_evaluations"] = sum(per_rule.get(i, 0) for i in set(carried[r.content_release_id]))
        out.append(d)
    return out


@router.get("/releases/{release_id}/impact")
def release_impact(release_id: str, as_of_record_seq: int | None = Query(default=None, ge=0),
                   ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Correction notice and tenant-specific impact report for one signed release (API-001 §17 "Tenant-authorized
    impact summary after release/shadow processing. No other-tenant aggregate detail"; FR-WRK-003; GLS-001 Correction
    Notice; app/engines/corrections.py).

    Everyone with catalogue read sees the release's global part: correction flags, the signed correction statement and
    the content it carries. An organisation reader also gets its own impact, computed read-only from its own records as
    known at ``as_of_record_seq`` (default: now), so an earlier report can be reproduced. Content and platform staff get no
    tenant impact at all (INV-14: no standing tenant data access); the release's ``shadow_summary`` is their aggregate
    view, and it is null for an organisation reader, because it counts every organisation's subjects (§17 "No
    other-tenant aggregate detail"; shadow_summary_for). Nothing is changed by reading a notice."""
    r = ctx.db.get(ContentRelease, release_id)
    if not r:
        raise HTTPException(404, "content release not found")
    scope, problems = corrections.release_scope(ctx.db, r)
    out = {"content_release_id": r.content_release_id, "label": r.label, "status": r.status,
           "manifest_hash": r.manifest_hash, "published_at": to_dict(r)["published_at"],
           "release_record_seq": r.cell_received_seq, **corrections.correction_flags(ctx.db, r),
           "scope": scope, "scope_problems": problems, "shadow_summary": shadow_summary_for(ctx, r.shadow_summary),
           "tenant_impact": None}
    if ctx.actor.tenant_id:
        now = current_watermark(ctx.db)["record_seq"]
        seq = now if as_of_record_seq is None else as_of_record_seq
        if seq > now:
            raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                      "message": f"as_of_record_seq is beyond the current record sequence {now}"})
        if seq < r.cell_received_seq:
            raise HTTPException(422, {"error": "RECORD_SEQ_BEFORE_RELEASE",
                                      "message": "The release was not yet known at that record sequence."})
        out["tenant_impact"] = corrections.tenant_impact(ctx.db, tenant_id=ctx.actor.tenant_id, release=r,
                                                         as_of_seq=seq)
    return out


@router.get("/releases/{release_id}/verify")
def verify_release(release_id: str, response: Response, ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    """Verify one published release (A9b; app/engines/release_verification.py): signed payload, signature, rule
    versions, requirement versions and source fingerprints. The outcome is VALID, INVALID or UNVERIFIABLE with every
    check and its reason; while no content-release trust anchor exists (RUL-ADR-06, C-07) an intact release is
    UNVERIFIABLE, never VALID. The fields this route returned before (manifest_hash_ok, signature_ok, signer_key_id,
    public_key_pem) keep their meaning. A result is recorded (content.verify; ZA-SEC-001 §18) when it is new for the
    caller: the first verification and every change of result. An identical repeat is a plain read and writes nothing.
    The ``Za-Verification-Recorded`` response header says which; the body stays identical for every caller (global
    content). An INVALID result that is recorded raises security.content_signature_failed (SEC-REQ-048), so every
    distinct failure is signalled. Nothing about the release is changed or re-signed."""
    r = ctx.db.get(ContentRelease, release_id)
    if not r:
        raise HTTPException(404, "content release not found")
    report = release_verification.verify_release(ctx.db, r)
    recorded = release_verification.record(ctx.db, report, actor=ctx.actor)
    ctx.db.commit()
    response.headers["Za-Verification-Recorded"] = "true" if recorded else "false"
    return report


@router.get("/coverage")
def coverage(ctx: Ctx = Depends(authorize(*CATALOG_READ))):
    return {"dimension": codes.DIMENSIONS["coverage_mode"],
            "instruments": [{"identifier": i.identifier, "title": i.title, "coverage_mode": i.coverage_mode,
                             "jurisdiction": i.jurisdiction}
                            for i in ctx.db.execute(select(Instrument).order_by(Instrument.identifier,
                                                                                Instrument.instrument_id)).scalars()]}
