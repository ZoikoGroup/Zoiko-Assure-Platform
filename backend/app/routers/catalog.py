"""Regulatory catalogue & content operations (ZA-ARCH-001 §7, ZA-DOM-001 §4)."""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import codes
from ..engines import keys
from ..models import (ApplicabilityEvaluation, ContentRelease, ControlDefinition, FactDefinition, FrameworkMapping,
                      Instrument, ObligationDefinition, Provision, RegulatorySource, RequirementVersion, RuleVersion,
                      SetVersion)
from ..platform import audit, canonical_json, emit, next_record_seq, sha256_hex, to_dict
from ..security import Ctx, get_ctx

router = APIRouter(prefix="/catalog")


@router.get("/instruments")
def instruments(ctx: Ctx = Depends(get_ctx)):
    out = []
    for i in ctx.db.execute(select(Instrument)).scalars():
        d = to_dict(i)
        d["obligation_definitions"] = [to_dict(o) for o in ctx.db.execute(
            select(ObligationDefinition).where(ObligationDefinition.instrument_id == i.instrument_id)).scalars()]
        d["requirements"] = [to_dict(r) for r in ctx.db.execute(
            select(RequirementVersion).where(RequirementVersion.instrument_id == i.instrument_id)).scalars()]
        out.append(d)
    return out


@router.get("/sources")
def sources(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.execute(select(RegulatorySource).order_by(RegulatorySource.fetched_at.desc())).scalars().all()
    out = []
    for s in rows:
        d = to_dict(s)
        if s.rights_class == "REFERENCE_ONLY":  # source-rights metadata controls display (FR-REG-005)
            d["raw_text"] = None
            d["display_note"] = "Text withheld under rights record; locator and fingerprint retained."
        out.append(d)
    return out


class SourceIn(BaseModel):
    authority: str
    jurisdiction: str
    source_type: str
    title: str
    locator: str
    rights_class: str = "OFFICIAL_REUSE_PERMITTED"
    raw_text: str
    publication_date: date | None = None
    effective_date: date | None = None
    instrument_identifier: str


@router.post("/sources")
def ingest_source(body: SourceIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("content_author")
    seq = next_record_seq(ctx.db)
    s = RegulatorySource(authority=body.authority, jurisdiction=body.jurisdiction, source_type=body.source_type,
                         title=body.title, locator=body.locator, fingerprint=sha256_hex(body.raw_text),
                         rights_class=body.rights_class, rights_record={"class": body.rights_class,
                                                                        "store": True, "display": body.rights_class != "REFERENCE_ONLY",
                                                                        "derive": True, "model_use": body.rights_class == "OFFICIAL_REUSE_PERMITTED"},
                         raw_text=body.raw_text, publication_date=body.publication_date,
                         effective_date=body.effective_date, lifecycle="INGESTED", record_seq=seq)
    ctx.db.add(s)
    ctx.db.flush()
    inst = ctx.db.execute(select(Instrument).where(Instrument.identifier == body.instrument_identifier)).scalars().first()
    if inst:
        s.lifecycle = "CLASSIFIED"
    emit(ctx.db, event_type="regulatory.source_ingested", aggregate_type="RegulatorySource", aggregate_id=s.source_id,
         record_seq=seq, payload={"fingerprint": s.fingerprint, "jurisdiction": s.jurisdiction, "lifecycle": s.lifecycle})
    audit(ctx.db, action="source.ingest", object_type="RegulatorySource", object_id=s.source_id, actor=ctx.actor, new=s.lifecycle)
    ctx.db.commit()
    return to_dict(s)


MODAL = re.compile(r"\b(shall|must|is required to|are required to|shall not|must not)\b", re.I)


class ExtractIn(BaseModel):
    instrument_identifier: str
    requirement_key: str
    requirement_title: str
    locator: str
    valid_from: date


@router.post("/sources/{source_id}/extract")
def extract(source_id: str, body: ExtractIn, ctx: Ctx = Depends(get_ctx)):
    """Governed extractor proposal (non-binding). Output is AI_EXTRACTED -> REVIEW_REQUIRED (INV-09)."""
    ctx.require("content_author")
    s = ctx.db.get(RegulatorySource, source_id)
    if not s:
        raise HTTPException(404, "source not found")
    if not (s.rights_record or {}).get("derive", True):
        raise HTTPException(409, "Rights record prohibits derivation")
    inst = ctx.db.execute(select(Instrument).where(Instrument.identifier == body.instrument_identifier)).scalars().first()
    if not inst:
        raise HTTPException(404, "instrument not found")
    seq = next_record_seq(ctx.db)
    rv = RequirementVersion(requirement_key=body.requirement_key, instrument_id=inst.instrument_id, source_id=s.source_id,
                            title=body.requirement_title, locator=body.locator, valid_from=body.valid_from,
                            lifecycle="REVIEW_REQUIRED", origin="AI_PROPOSED", record_seq=seq)
    ctx.db.add(rv)
    ctx.db.flush()
    sentences = [x.strip() for x in re.split(r"(?<=[.;])\s+", s.raw_text or "") if MODAL.search(x)]
    props = []
    for i, sent in enumerate(sentences[:20], start=1):
        m = MODAL.search(sent)
        modality = "MUST_NOT" if "not" in m.group(0).lower() else "MUST"
        p = Provision(requirement_version_id=rv.requirement_version_id, locator=f"{body.locator}({i})",
                      duty_bearer="TBD_BY_REVIEWER", modality=modality, action=sent[m.end():].strip()[:110] or "TBD",
                      regulated_object="TBD_BY_REVIEWER", normalized_meaning=sent, semantic_origin="AI_PROPOSED",
                      lifecycle="AI_EXTRACTED")
        ctx.db.add(p)
        props.append(p)
    s.lifecycle = "REVIEW_REQUIRED"
    ctx.db.flush()
    emit(ctx.db, event_type="regulatory.extraction_proposed", aggregate_type="RequirementVersion",
         aggregate_id=rv.requirement_version_id, record_seq=seq,
         payload={"proposals": len(props), "extractor": "governed_extractor:modal-heuristic-v1", "binding": False})
    ctx.db.commit()
    return {"requirement_version": to_dict(rv), "proposed_provisions": [to_dict(p) for p in props],
            "note": "Proposals are non-binding; a qualified human reviewer must VERIFY before any release."}


class ProvisionEdit(BaseModel):
    duty_bearer: str
    modality: str
    action: str
    regulated_object: str
    timing: str | None = None
    normalized_meaning: str
    evidence_expectation: str | None = None


@router.put("/provisions/{provision_id}")
def edit_provision(provision_id: str, body: ProvisionEdit, ctx: Ctx = Depends(get_ctx)):
    ctx.require("content_author")
    p = ctx.db.get(Provision, provision_id)
    if not p:
        raise HTTPException(404)
    if p.lifecycle in ("VERIFIED", "PUBLISHED"):
        raise HTTPException(409, "Released/verified provisions are immutable; create a new version")
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    p.semantic_origin = "ZOIKO_ENCODED"
    p.lifecycle = "REVIEW_REQUIRED"
    ctx.db.commit()
    return to_dict(p)


@router.post("/requirements/{rv_id}/verify")
def verify_requirement(rv_id: str, ctx: Ctx = Depends(get_ctx)):
    """Qualified human legal review: REVIEW_REQUIRED -> VERIFIED (INV-09; AI cannot)."""
    ctx.require_human("Content verification")
    ctx.require("content_legal_review")
    rv = ctx.db.get(RequirementVersion, rv_id)
    if not rv:
        raise HTTPException(404)
    if rv.lifecycle != "REVIEW_REQUIRED":
        raise HTTPException(409, f"Cannot verify from {rv.lifecycle}")
    provs = ctx.db.execute(select(Provision).where(Provision.requirement_version_id == rv_id)).scalars().all()
    if any(p.duty_bearer == "TBD_BY_REVIEWER" for p in provs):
        raise HTTPException(409, "All proposed provisions must be reviewed/encoded (duty bearer etc.) before VERIFIED")
    seq = next_record_seq(ctx.db)
    rv.lifecycle = "VERIFIED"
    rv.reviewed_by = ctx.actor.actor_id
    for p in provs:
        p.lifecycle = "VERIFIED"
    audit(ctx.db, action="requirement.verify", object_type="RequirementVersion", object_id=rv_id, actor=ctx.actor,
          prior="REVIEW_REQUIRED", new="VERIFIED", record_seq=seq)
    ctx.db.commit()
    return to_dict(rv)


@router.get("/requirements/{rv_id}")
def requirement(rv_id: str, ctx: Ctx = Depends(get_ctx)):
    rv = ctx.db.get(RequirementVersion, rv_id)
    if not rv:
        raise HTTPException(404)
    d = to_dict(rv)
    d["provisions"] = [to_dict(p) for p in ctx.db.execute(select(Provision).where(Provision.requirement_version_id == rv_id)).scalars()]
    src = ctx.db.get(RegulatorySource, rv.source_id)
    d["source"] = {k: v for k, v in to_dict(src).items() if k != "raw_text" or src.rights_class != "REFERENCE_ONLY"}
    return d


@router.get("/obligation-definitions")
def odefs(ctx: Ctx = Depends(get_ctx)):
    out = []
    for o in ctx.db.execute(select(ObligationDefinition)).scalars():
        d = to_dict(o)
        inst = ctx.db.get(Instrument, o.instrument_id)
        d["instrument"] = inst.identifier
        rv = ctx.db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == o.obligation_definition_id,
                                                      RuleVersion.status == "PUBLISHED").order_by(RuleVersion.version.desc())).scalars().first()
        d["current_rule_version"] = to_dict(rv) if rv else None
        d["provisions"] = [to_dict(p) for p in ctx.db.execute(select(Provision).where(Provision.provision_id.in_(o.provision_ids or ["-"]))).scalars()]
        out.append(d)
    return out


@router.get("/fact-definitions")
def fdefs(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(f) for f in ctx.db.execute(select(FactDefinition).order_by(FactDefinition.key)).scalars()]


@router.get("/sets")
def sets(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(s) for s in ctx.db.execute(select(SetVersion)).scalars()]


@router.get("/controls")
def control_defs(ctx: Ctx = Depends(get_ctx)):
    out = []
    for c in ctx.db.execute(select(ControlDefinition)).scalars():
        d = to_dict(c)
        d["framework_mappings"] = [to_dict(m) for m in ctx.db.execute(select(FrameworkMapping).where(
            FrameworkMapping.control_definition_id == c.control_definition_id)).scalars()]
        out.append(d)
    return out


@router.get("/releases")
def releases(ctx: Ctx = Depends(get_ctx)):
    out = []
    for r in ctx.db.execute(select(ContentRelease).order_by(ContentRelease.published_at.desc())).scalars():
        d = to_dict(r)
        if ctx.actor.tenant_id:  # tenant-scoped impact view: their own impacted subjects only
            rvs = r.manifest.get("rule_versions", [])
            ids = [x["rule_version_id"] for x in rvs]
            d["tenant_impacted_evaluations"] = ctx.db.query(ApplicabilityEvaluation).filter(
                ApplicabilityEvaluation.tenant_id == ctx.actor.tenant_id,
                ApplicabilityEvaluation.rule_version_id.in_(ids or ["-"])).count()
        out.append(d)
    return out


@router.get("/releases/{release_id}/verify")
def verify_release(release_id: str, ctx: Ctx = Depends(get_ctx)):
    r = ctx.db.get(ContentRelease, release_id)
    if not r:
        raise HTTPException(404)
    mb = canonical_json(r.manifest)
    return {"content_release_id": release_id, "manifest_hash_ok": sha256_hex(mb) == r.manifest_hash,
            "signature_ok": keys.verify(mb, r.signature), "signer_key_id": r.signer_key_id,
            "public_key_pem": keys.public_key_pem()}


@router.get("/coverage")
def coverage(ctx: Ctx = Depends(get_ctx)):
    return {"dimension": codes.DIMENSIONS["coverage_mode"],
            "instruments": [{"identifier": i.identifier, "title": i.title, "coverage_mode": i.coverage_mode,
                             "jurisdiction": i.jurisdiction} for i in ctx.db.execute(select(Instrument)).scalars()]}
