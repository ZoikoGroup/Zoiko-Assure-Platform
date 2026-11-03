"""Evidence vault, ledger, checkpoints, holds/erasure and signed packages (ZA-EVD-001)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..db import BASE_DIR
from ..engines import evidence as evd
from ..engines import governance
from ..engines import ipe as ipe_engine
from ..engines import keys
from ..models import (ApplicabilityEvaluation, AssuranceSnapshot, EvidenceArtifact, EvidenceBinding, EvidenceConflict,
                      EvidenceConflictEvent, EvidenceLedgerEvent, EvidencePackage, LedgerCheckpoint, LegalEntity,
                      LegalHold, LegalHoldEvent, ObligationInstance, PopulationManifest, SourceRun, TenantLedgerHead)
from ..platform import audit, current_watermark, emit, ensure_utc, next_record_seq, to_dict
from ..security import DOSSIER_READ, Ctx, authorize, entity_of, system_scope

router = APIRouter()
# Stage 4: how evidence and packages resolve to their legal entity, so entity-scoped roles reach their entity only.
EVIDENCE = entity_of(EvidenceArtifact, "evidence_id", "evidence")
PACKAGE = entity_of(EvidencePackage, "package_id", "package")


def _load_verifier():
    spec = importlib.util.spec_from_file_location("za_verifier", BASE_DIR / "verifier" / "verify_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _view(db, a: EvidenceArtifact, lc: str | None = None, held: bool | None = None, ipe_results: dict | None = None) -> dict:
    """One artifact as the API shows it. The list passes what it read once for all artifacts (lifecycles, legal holds,
    IPE result per source run) instead of querying per artifact; the values are the same."""
    d = evd._art_meta(a)
    d["lifecycle"] = lc = evd.lifecycle(db, a) if lc is None else lc
    d["condition"] = evd.condition_from(a, lc)
    d["legal_hold"] = bool(evd.active_hold(db, a)) if held is None else held
    d["payload_available"] = a.object_key is not None
    if a.source_run_id:
        ipe_results = {} if ipe_results is None else ipe_results
        if a.source_run_id not in ipe_results:
            ipe = ipe_engine.bound_evaluation(db, db.get(SourceRun, a.source_run_id))
            ipe_results[a.source_run_id] = ipe.overall_result if ipe else None
        d["ipe_result"] = ipe_results[a.source_run_id]
    return d


@router.get("/evidence")
def list_evidence(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    arts = ctx.db.execute(select(EvidenceArtifact).where(EvidenceArtifact.tenant_id == ctx.tenant)
                          .order_by(EvidenceArtifact.recorded_at.desc())).scalars().all()
    lcs, held, ipe_results = evd.lifecycles(ctx.db, ctx.tenant, arts), evd.held_evidence_ids(ctx.db, ctx.tenant), {}
    return [_view(ctx.db, a, lcs[a.evidence_id], a.evidence_id in held, ipe_results) for a in arts]


@router.post("/evidence/upload")
async def upload(file: UploadFile = File(...), entity_id: str = Form(...), title: str = Form(...),
                 coverage_from: datetime = Form(...), coverage_to: datetime = Form(...), validity_days: int = Form(90),
                 data_classification: str = Form("CUSTOMER_CONFIDENTIAL"), idempotency_key: str | None = Form(None),
                 ctx: Ctx = Depends(authorize("submit_evidence"))):
    """Manual upload: visibly lower provenance (P4/HUMAN_UPLOAD); cannot alone satisfy automation-required controls."""
    ctx.scoped(ctx.db.get(LegalEntity, entity_id), "entity")  # the evidence's entity must belong to this organisation
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Payload too large")
    art = evd.capture_evidence(ctx.db, tenant_id=ctx.tenant, entity_id=entity_id, content=content, title=title,
                               content_type=file.content_type or "application/octet-stream", origin_class="HUMAN_UPLOAD",
                               acquisition_method="FILE_UPLOAD", provenance_class="P4_MANUAL_UNATTESTED",
                               coverage_from=coverage_from, coverage_to=coverage_to,
                               valid_until=ensure_utc(coverage_to) + timedelta(days=validity_days), actor_id=ctx.actor.actor_id,
                               data_classification=data_classification, source_system=f"upload:{file.filename}",
                               idempotency_key=idempotency_key)
    ctx.db.commit()  # acknowledgement only after manifest + ledger commit (EVD-REQ-001)
    return {"evidence_id": art.evidence_id, "ledger_seq": art.ledger_seq, "acknowledged": True}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EVIDENCE))):
    """WF-EVD-01 Evidence Record & Chain Explorer."""
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    d = _view(ctx.db, a)
    d["history"] = [to_dict(e) for e in ctx.db.execute(select(EvidenceLedgerEvent).where(
        EvidenceLedgerEvent.tenant_id == ctx.tenant, EvidenceLedgerEvent.evidence_id == evidence_id)
        .order_by(EvidenceLedgerEvent.ledger_seq)).scalars()]
    d["bindings"] = [to_dict(b) for b in ctx.db.execute(select(EvidenceBinding).where(EvidenceBinding.evidence_id == evidence_id)).scalars()]
    d["holds"] = [to_dict(h) for h in ctx.db.execute(select(LegalHold).where(LegalHold.evidence_id == evidence_id)).scalars()]
    head = ctx.db.get(TenantLedgerHead, ctx.tenant)
    cp = None
    if head and head.last_checkpointed_seq >= a.ledger_seq:
        for c in ctx.db.execute(select(LedgerCheckpoint).order_by(LedgerCheckpoint.created_at)).scalars():
            if any(l["tenant_id"] == ctx.tenant and l["head_seq"] >= a.ledger_seq for l in c.leaves):
                cp = c
                break
    d["checkpoint"] = {"checkpoint_id": cp.checkpoint_id, "merkle_root": cp.merkle_root, "tsa_status": cp.tsa_status,
                       "created_at": cp.created_at.isoformat()} if cp else None
    if a.source_run_id:
        run = ctx.db.get(SourceRun, a.source_run_id)
        pm = ctx.db.execute(select(PopulationManifest).where(PopulationManifest.source_run_id == a.source_run_id)).scalars().first()
        d["source_run"] = to_dict(run) if run else None
        d["population_manifest"] = to_dict(pm) if pm else None
    return d


@router.get("/evidence/{evidence_id}/download")
def download(evidence_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EVIDENCE))):
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")  # authorised by the route guard
    data = evd.read_payload(a)
    from ..platform import audit
    audit(ctx.db, action="evidence.download", object_type="EvidenceArtifact", object_id=evidence_id, actor=ctx.actor,
          tenant_id=ctx.tenant)
    ctx.db.commit()
    return Response(content=data, media_type=a.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{evidence_id}"'})


@router.post("/evidence/{evidence_id}/verify")
def verify(evidence_id: str, ctx: Ctx = Depends(authorize("read", "verify", entity=EVIDENCE))):
    # Integrity verification changes no protected state: it appends a VERIFIED ledger record of the check (and an
    # integrity incident if the check fails). Readers, auditors included, may run it.
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    r = evd.verify_evidence(ctx.db, a, ctx.actor.actor_id)
    ctx.db.commit()
    return r


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    reason: str
    authority: str | None = None
    successor_evidence_id: str | None = None


@router.post("/evidence/{evidence_id}/supersede")
def supersede(evidence_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    """Supersession appends an event referencing a NEW evidence id; bytes are never replaced (EVD-REQ-018)."""
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    if not body.successor_evidence_id or body.successor_evidence_id == evidence_id:
        raise HTTPException(422, "A distinct successor evidence id is required")
    ctx.scoped(ctx.db.get(EvidenceArtifact, body.successor_evidence_id), "successor")
    evd.append_ledger_event(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id, event_type="SUPERSEDED",
                            actor_id=ctx.actor.actor_id, reason=body.reason, body={"successor": body.successor_evidence_id})
    emit(ctx.db, event_type="evidence.superseded", aggregate_type="EvidenceArtifact", aggregate_id=evidence_id,
         tenant_id=ctx.tenant, payload={"successor": body.successor_evidence_id})
    ctx.db.commit()
    return _view(ctx.db, a)


@router.post("/evidence/{evidence_id}/invalidate")
def invalidate(evidence_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    evd.append_ledger_event(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id, event_type="INVALIDATED",
                            actor_id=ctx.actor.actor_id, reason=body.reason, body={})
    emit(ctx.db, event_type="evidence.invalidated", aggregate_type="EvidenceArtifact", aggregate_id=evidence_id,
         tenant_id=ctx.tenant, payload={"reason": body.reason})
    ctx.db.commit()
    return _view(ctx.db, a)


@router.post("/evidence/{evidence_id}/hold")
def place_hold(evidence_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("attest", human=True))):
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    h, _ = governance.place_legal_hold(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id,
                                       actor_id=ctx.actor.actor_id, authority=body.authority or "General Counsel",
                                       reason=body.reason)
    evd.append_ledger_event(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id, event_type="HOLD_PLACED",
                            actor_id=ctx.actor.actor_id, reason=body.reason, body={"authority": h.authority})
    ctx.db.commit()  # one transaction: projection, hold event, audit, outbox (engine) and ledger entry
    return _view(ctx.db, a)


@router.post("/evidence/{evidence_id}/release-hold")
def release_hold(evidence_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("attest", human=True))):
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    h, _ = governance.release_legal_hold(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id,
                                         actor_id=ctx.actor.actor_id, reason=body.reason)
    evd.append_ledger_event(ctx.db, tenant_id=ctx.tenant, evidence_id=evidence_id, event_type="HOLD_RELEASED",
                            actor_id=ctx.actor.actor_id, reason=body.reason, body={"hold_id": h.hold_id})
    ctx.db.commit()  # one transaction: projection, hold event, audit, outbox (engine) and ledger entry
    return _view(ctx.db, a)


@router.get("/evidence/{evidence_id}/holds/events")
def hold_events(evidence_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EVIDENCE))):
    ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(LegalHoldEvent).where(
            LegalHoldEvent.tenant_id == ctx.tenant,
            LegalHoldEvent.evidence_id == evidence_id,
        ).order_by(LegalHoldEvent.record_seq.asc())
    ).scalars()]


@router.post("/evidence/{evidence_id}/erase")
def erase(evidence_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("attest", human=True))):
    a = ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    evd.erase_payload(ctx.db, a, ctx.actor.actor_id, body.reason)
    ctx.db.commit()
    return _view(ctx.db, a)


class BindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    target_type: str
    target_id: str
    rationale: str | None = None


@router.post("/evidence/{evidence_id}/bindings")
def bind(evidence_id: str, body: BindIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    ctx.scoped(ctx.db.get(EvidenceArtifact, evidence_id), "evidence")
    b = EvidenceBinding(tenant_id=ctx.tenant, evidence_id=evidence_id, record_seq=next_record_seq(ctx.db), **body.model_dump())
    ctx.db.add(b)
    ctx.db.commit()
    return to_dict(b)


class ConflictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    member_evidence_ids: list[str]
    description: str


@router.post("/evidence-conflicts")
def open_conflict(body: ConflictIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    for e in body.member_evidence_ids:
        ctx.scoped(ctx.db.get(EvidenceArtifact, e), "evidence")
    c, _ = governance.open_evidence_conflict(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                             member_evidence_ids=body.member_evidence_ids, description=body.description)
    ctx.db.commit()
    return to_dict(c)


@router.post("/evidence-conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, body: ReasonIn, ctx: Ctx = Depends(authorize("assurance_review", human=True))):
    """Governed disposition of a source conflict (ZA-EVD-001: resolution is appended, history preserved).
    The conflict row's status still changes in place until the record-time revision model (audit F3) lands; the audit
    event below keeps who resolved it, when, from which state and why."""
    ctx.scoped(ctx.db.get(EvidenceConflict, conflict_id), "conflict")
    c, _ = governance.resolve_evidence_conflict(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                                conflict_id=conflict_id, reason=body.reason)
    ctx.db.commit()
    return to_dict(c)


@router.get("/evidence-conflicts/{conflict_id}/events")
def conflict_events(conflict_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    ctx.scoped(ctx.db.get(EvidenceConflict, conflict_id), "conflict")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(EvidenceConflictEvent).where(
            EvidenceConflictEvent.tenant_id == ctx.tenant,
            EvidenceConflictEvent.conflict_id == conflict_id,
        ).order_by(EvidenceConflictEvent.record_seq.asc())
    ).scalars()]


@router.get("/evidence-conflicts")
def conflicts(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(c) for c in ctx.db.execute(select(EvidenceConflict).where(EvidenceConflict.tenant_id == ctx.tenant)).scalars()]


# ------------------------------------------------------------------ ledger / checkpoints (WF-EVD-03)
@router.get("/ledger")
def ledger(limit: int = 200, ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    head = ctx.db.get(TenantLedgerHead, ctx.tenant)
    events = ctx.db.execute(select(EvidenceLedgerEvent).where(EvidenceLedgerEvent.tenant_id == ctx.tenant)
                            .order_by(EvidenceLedgerEvent.ledger_seq.desc()).limit(limit)).scalars().all()
    cps = ctx.db.execute(select(LedgerCheckpoint).order_by(LedgerCheckpoint.created_at.desc()).limit(20)).scalars().all()
    return {"head": to_dict(head) if head else None, "events": [to_dict(e) for e in events],
            "checkpoints": [{**{k: v for k, v in to_dict(c).items() if k != "leaves"},
                             "includes_tenant": any(l["tenant_id"] == ctx.tenant for l in c.leaves)} for c in cps],
            "signer_key_id": keys.signer_key_id()}


@router.post("/ledger/verify")
def verify_ledger(ctx: Ctx = Depends(authorize("read", "verify"))):  # an integrity check; changes no protected state
    r = evd.verify_chain(ctx.db, ctx.tenant)
    ctx.db.commit()
    return r


@router.post("/ledger/checkpoint")
def checkpoint(ctx: Ctx = Depends(authorize("checkpoint", "package"))):
    with system_scope(ctx.db, "cell ledger checkpoint over all tenant heads"):
        cp = evd.create_checkpoint(ctx.db)
    ctx.db.commit()
    return {k: v for k, v in to_dict(cp).items() if k != "leaves"} if cp else {"message": "No changed tenant heads since last checkpoint"}


# ------------------------------------------------------------------ packages (WF-EVD-05/06)
class PackageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    entity_id: str
    period_from: datetime
    period_to: datetime
    obligation_lineage_ids: list[str]
    include_payloads: bool = True


@router.post("/packages")
def create_package(body: PackageIn, ctx: Ctx = Depends(authorize("package"))):
    ctx.scoped(ctx.db.get(LegalEntity, body.entity_id), "entity")
    snaps, evs, ipe, ev_ids = [], [], [], set()
    for lin in body.obligation_lineage_ids:
        s = ctx.db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == ctx.tenant,
                                                           AssuranceSnapshot.obligation_lineage_id == lin)
                           .order_by(AssuranceSnapshot.created_at.desc())).scalars().first()
        if not s:
            raise HTTPException(409, f"No frozen assurance snapshot for obligation {lin}; create one first")
        snaps.append(to_dict(s))
        ev_ids |= set(s.dependency_manifest.get("evidence", []))
        o = ctx.db.execute(select(ObligationInstance).where(ObligationInstance.lineage_id == lin)).scalars().first()
        for e in ctx.db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == o.subject_id,
                                                                      ApplicabilityEvaluation.obligation_definition_id == o.obligation_definition_id)).scalars():
            evs.append(to_dict(e))
    arts = [ctx.scoped(ctx.db.get(EvidenceArtifact, e), "evidence") for e in sorted(ev_ids)]
    for a in arts:
        if a.source_run_id:
            run = ctx.db.get(SourceRun, a.source_run_id)
            pm = ctx.db.execute(select(PopulationManifest).where(PopulationManifest.source_run_id == a.source_run_id)).scalars().first()
            ie = ipe_engine.bound_evaluation(ctx.db, run)  # the result assurance relied on, not the newest one
            if ie is None:
                raise HTTPException(409, f"Source run {run.source_run_id} has no IPE evaluation under its own policy")
            ipe.append({"source_run": to_dict(run), "population_manifest": to_dict(pm), "ipe_evaluation": to_dict(ie),
                        "statement": f"{ie.overall_result} for SourceRun {run.source_run_id} under IPEPolicy {ie.ipe_policy_version_id} "
                                     f"and MappingContract {ie.mapping_contract_version_id}."})
    pkg = evd.build_package(ctx.db, tenant_id=ctx.tenant, entity_id=body.entity_id, period_from=body.period_from,
                            period_to=body.period_to, evidence=arts, snapshots=snaps, evaluations=evs, ipe=ipe,
                            include_payloads=body.include_payloads, actor_id=ctx.actor.actor_id,
                            record_cutoff_seq=current_watermark(ctx.db)["record_seq"])
    ctx.db.commit()
    return to_dict(pkg)


@router.get("/packages")
def packages(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(p) for p in ctx.db.execute(select(EvidencePackage).where(EvidencePackage.tenant_id == ctx.tenant)
                                               .order_by(EvidencePackage.created_at.desc())).scalars()]


@router.get("/packages/{package_id}/download")
def download_package(package_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=PACKAGE))):
    p = ctx.scoped(ctx.db.get(EvidencePackage, package_id), "package")
    return FileResponse(evd.PACKAGE_DIR / p.file_ref, media_type="application/zip", filename=f"zoiko-assure-package-{package_id}.zip")


@router.post("/packages/{package_id}/verify")
def verify_stored_package(package_id: str, ctx: Ctx = Depends(authorize("read", "verify", entity=PACKAGE))):  # read-only verification
    p = ctx.scoped(ctx.db.get(EvidencePackage, package_id), "package")
    return _load_verifier().verify((evd.PACKAGE_DIR / p.file_ref).read_bytes(), keys.public_key_pem().encode())


MULTIPART_OVERHEAD = 64 * 1024  # boundaries and part headers around the uploaded file


def verify_upload_body_limit() -> int:
    """Request-body limit for verify-upload (app.limits.BodyLimit): the verifier's archive limit plus form overhead."""
    return _load_verifier().limits()["archive"] + MULTIPART_OVERHEAD


@router.post("/packages/verify-upload")
async def verify_upload(file: UploadFile = File(...)):
    """Public on purpose: ZA-EVD-001 §18 lets auditors and regulators validate a package without logging into Assure.
    It runs the same logic as the standalone verifier and touches no tenant data. The request body is capped while it
    streams in (app.limits), the file is read here in bounded chunks, and the verifier bounds decompression."""
    verifier = _load_verifier()
    cap = verifier.limits()["archive"]
    data = bytearray()
    try:
        while chunk := await file.read(64 * 1024):
            data += chunk
            if len(data) > cap:
                raise HTTPException(413, f"Package exceeds the {cap} byte limit")
    finally:
        await file.close()
    # Hashing and decompression are CPU-bound: run them off the event loop so one upload cannot stall the API.
    return await run_in_threadpool(verifier.verify, bytes(data), keys.public_key_pem().encode())


@router.get("/trust/public-key")
def public_key():
    return {"signer_key_id": keys.signer_key_id(), "public_key_pem": keys.public_key_pem()}
