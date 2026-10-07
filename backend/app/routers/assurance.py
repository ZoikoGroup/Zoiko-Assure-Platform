"""Obligations, controls, tests, period assurance, exceptions, compensation, issues (ZA-STA-001)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..engines import assurance as asr
from ..models import (AdoptionAttestation, ApplicabilityDetermination, AssuranceSnapshot, Compensation,
                      ControlDefinition, ControlEvaluation, ControlImplementation, ControlTestPolicy, EvidenceArtifact,
                      ExceptionRecord, Issue, ObligationDefinition, ObligationInstance, ObligationOccurrence, Provision,
                      RiskAcceptance, SatisfactionExpressionVersion, Subject, TestDefinition, TestRun)
from ..platform import audit, digest, emit, ensure_utc, next_record_seq, to_dict
from ..security import Ctx, get_ctx

router = APIRouter()


def _current_obl(db, tenant, lineage) -> ObligationInstance:
    o = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant,
                                                    ObligationInstance.lineage_id == lineage,
                                                    ObligationInstance.record_to_seq.is_(None))).scalars().first()
    if not o:
        raise HTTPException(404, "obligation not found")
    return o


@router.get("/obligations")
def obligations(ctx: Ctx = Depends(get_ctx)):
    out = []
    for o in ctx.db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == ctx.tenant,
                                                             ObligationInstance.record_to_seq.is_(None))).scalars():
        d = to_dict(o)
        od = ctx.db.get(ObligationDefinition, o.obligation_definition_id)
        d["definition"] = {"key": od.key, "title": od.title, "schedule_kind": od.schedule_kind}
        d["subject"] = to_dict(ctx.db.get(Subject, o.subject_id))
        snap = ctx.db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.obligation_lineage_id == o.lineage_id)
                              .order_by(AssuranceSnapshot.created_at.desc())).scalars().first()
        d["latest_snapshot"] = {k: v for k, v in to_dict(snap).items() if k not in ("segments", "dependency_manifest")} if snap else None
        d["open_exceptions"] = ctx.db.query(ExceptionRecord).filter(ExceptionRecord.target_id == o.lineage_id,
                                                                     ExceptionRecord.state == "APPROVED").count()
        out.append(d)
    return out


@router.get("/obligations/{lineage}")
def dossier(lineage: str, ctx: Ctx = Depends(get_ctx)):
    """WF-STA-01 Obligation Assurance Dossier."""
    o = _current_obl(ctx.db, ctx.tenant, lineage)
    od = ctx.db.get(ObligationDefinition, o.obligation_definition_id)
    det = ctx.db.get(ApplicabilityDetermination, o.determination_revision_id)
    att = ctx.db.get(AdoptionAttestation, o.attestation_id)
    exprs = ctx.db.execute(select(SatisfactionExpressionVersion).where(
        SatisfactionExpressionVersion.obligation_lineage_id == lineage).order_by(SatisfactionExpressionVersion.version)).scalars().all()
    keys = set()
    for e in exprs:
        keys |= asr._control_keys(e.ast)
    subject = ctx.db.get(Subject, o.subject_id)
    controls = []
    now = datetime.now(timezone.utc)
    for k in sorted(keys):
        ci = asr._resolve_impl(ctx.db, ctx.tenant, k, subject, now - timedelta(days=1), now, 10**15)
        cd = ctx.db.execute(select(ControlDefinition).where(ControlDefinition.key == k)).scalars().first()
        controls.append({"control_key": k, "title": cd.title if cd else k,
                         "implementation": to_dict(ci) if ci else None})
    return {
        "obligation": to_dict(o), "definition": to_dict(od), "subject": to_dict(subject),
        "provisions": [to_dict(p) for p in ctx.db.execute(select(Provision).where(Provision.provision_id.in_(od.provision_ids or ["-"]))).scalars()],
        "determination": to_dict(det) if det else None, "attestation": to_dict(att) if att else None,
        "revisions": [to_dict(r) for r in ctx.db.execute(select(ObligationInstance).where(ObligationInstance.lineage_id == lineage)
                                                         .order_by(ObligationInstance.record_from_seq)).scalars()],
        "expression_versions": [to_dict(e) for e in exprs], "controls": controls,
        "snapshots": [{k: v for k, v in to_dict(s).items() if k != "dependency_manifest"} for s in ctx.db.execute(
            select(AssuranceSnapshot).where(AssuranceSnapshot.obligation_lineage_id == lineage)
            .order_by(AssuranceSnapshot.created_at.desc())).scalars()],
        "exceptions": [to_dict(x) for x in ctx.db.execute(select(ExceptionRecord).where(ExceptionRecord.target_id == lineage)).scalars()],
        "compensations": [to_dict(c) for c in ctx.db.execute(select(Compensation).where(Compensation.obligation_lineage_id == lineage)).scalars()],
        "occurrences": [to_dict(x) for x in ctx.db.execute(select(ObligationOccurrence).where(ObligationOccurrence.obligation_lineage_id == lineage)
                                                           .order_by(ObligationOccurrence.due_at)).scalars()],
        "issues": [to_dict(i) for i in ctx.db.execute(select(Issue).where(Issue.tenant_id == ctx.tenant,
                                                                         Issue.dedupe_key.like(f"%{lineage}%"))).scalars()],
    }


class PeriodIn(BaseModel):
    period_from: datetime
    period_to: datetime
    record_cutoff_seq: int | None = None
    perspective: str = "RECONSTRUCTED_NOW"


@router.post("/obligations/{lineage}/snapshots")
def create_snapshot(lineage: str, body: PeriodIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("read")
    persp = body.perspective if body.record_cutoff_seq is None else "AS_KNOWN_THEN"
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq, perspective=persp,
                             actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return r


@router.post("/obligations/{lineage}/preview")
def preview(lineage: str, body: PeriodIn, ctx: Ctx = Depends(get_ctx)):
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq,
                             perspective=body.perspective, actor_id=ctx.actor.actor_id, persist=False)
    ctx.db.rollback()
    return r


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, ctx: Ctx = Depends(get_ctx)):
    return to_dict(ctx.scoped(ctx.db.get(AssuranceSnapshot, snapshot_id), "snapshot"))


@router.get("/snapshots/{snapshot_id}/reproduce")
def reproduce(snapshot_id: str, ctx: Ctx = Depends(get_ctx)):
    """Rebuild from the pinned record cutoff and compare (TIM-REQ-015/016)."""
    s = ctx.scoped(ctx.db.get(AssuranceSnapshot, snapshot_id), "snapshot")
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=s.obligation_lineage_id,
                             period_from=s.period_from, period_to=s.period_to, record_cutoff_seq=s.record_cutoff_seq,
                             perspective=s.perspective, actor_id=ctx.actor.actor_id, persist=False)
    ctx.db.rollback()
    ok_digest = r["dependency_digest"] == s.dependency_digest
    return {"snapshot_id": snapshot_id, "original_state": s.state, "reproduced_state": r["state"],
            "dependency_digest_match": ok_digest, "reproducible": ok_digest and r["state"] == s.state,
            "record_cutoff_seq": s.record_cutoff_seq}


# ------------------------------------------------------------------ occurrences
class OccIn(BaseModel):
    window_from: datetime
    due_at: datetime


@router.post("/obligations/{lineage}/occurrences")
def add_occurrence(lineage: str, body: OccIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_controls") if "manage_controls" in ctx.perms() else ctx.require("manage_facts")
    _current_obl(ctx.db, ctx.tenant, lineage)
    o = ObligationOccurrence(tenant_id=ctx.tenant, obligation_lineage_id=lineage, window_from=body.window_from,
                             due_at=body.due_at, record_seq=next_record_seq(ctx.db))
    ctx.db.add(o)
    ctx.db.commit()
    return to_dict(o)


class FulfilIn(BaseModel):
    fulfilled_at: datetime
    evidence_ids: list[str]


@router.post("/occurrences/{occurrence_id}/fulfil")
def fulfil(occurrence_id: str, body: FulfilIn, ctx: Ctx = Depends(get_ctx)):
    """Records fulfilment as a new occurrence fact; a missed occurrence is never overwritten by later success."""
    o = ctx.scoped(ctx.db.get(ObligationOccurrence, occurrence_id), "occurrence")
    if o.fulfilled_at:
        raise HTTPException(409, "Occurrence already fulfilled; history is immutable")
    for e in body.evidence_ids:
        ctx.scoped(ctx.db.get(EvidenceArtifact, e), "evidence")
    o.fulfilled_at = body.fulfilled_at
    o.evidence_ids = body.evidence_ids
    emit(ctx.db, event_type="obligation.occurrence_fulfilled", aggregate_type="ObligationOccurrence",
         aggregate_id=occurrence_id, tenant_id=ctx.tenant, payload={"late": ensure_utc(body.fulfilled_at) > ensure_utc(o.due_at)})
    ctx.db.commit()
    return to_dict(o)


# ------------------------------------------------------------------ controls
@router.get("/controls")
def controls(ctx: Ctx = Depends(get_ctx)):
    out = []
    now = datetime.now(timezone.utc)
    for ci in ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant)).scalars():
        d = to_dict(ci)
        cd = ctx.db.get(ControlDefinition, ci.control_definition_id)
        d["definition"] = to_dict(cd)
        d["subject"] = ctx.db.get(Subject, ci.subject_id).display_name
        ce = asr.evaluate_control(ctx.db, ci, now - timedelta(days=1), now, 10**15, persist=False)
        d["current_state"] = {"state": ce["state"], "reason_codes": ce["reason_codes"], "window": "last 24h",
                              "note": "Convenience view; authoritative state is the immutable ControlEvaluation."}
        out.append(d)
    return out


@router.get("/controls/{ci_id}")
def control_detail(ci_id: str, days: int = 30, ctx: Ctx = Depends(get_ctx)):
    """WF-STA-02 Control Evaluation."""
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    now = datetime.now(timezone.utc)
    ce = asr.evaluate_control(ctx.db, ci, now - timedelta(days=days), now, 10**15, persist=False)
    pols = ctx.db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci_id)).scalars().all()
    tests = []
    for p in pols:
        for td in ctx.db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == p.policy_version_id)).scalars():
            t = to_dict(td)
            t["runs"] = [to_dict(r) for r in ctx.db.execute(select(TestRun).where(TestRun.test_definition_id == td.test_definition_id)
                                                            .order_by(TestRun.occurred_at.desc())).scalars()]
            tests.append(t)
    return {"implementation": to_dict(ci), "definition": to_dict(ctx.db.get(ControlDefinition, ci.control_definition_id)),
            "subject": to_dict(ctx.db.get(Subject, ci.subject_id)), "policies": [to_dict(p) for p in pols], "tests": tests,
            "evaluation_preview": ce, "window_days": days,
            "evaluations": [to_dict(e) for e in ctx.db.execute(select(ControlEvaluation).where(
                ControlEvaluation.control_implementation_id == ci_id).order_by(ControlEvaluation.created_at.desc()).limit(20)).scalars()]}


class WindowIn(BaseModel):
    window_from: datetime
    window_to: datetime


@router.post("/controls/{ci_id}/evaluate")
def evaluate_control(ci_id: str, body: WindowIn, ctx: Ctx = Depends(get_ctx)):
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    from ..platform import current_watermark
    r = asr.evaluate_control(ctx.db, ci, body.window_from, body.window_to, current_watermark(ctx.db)["record_seq"])
    emit(ctx.db, event_type="control.evaluated", aggregate_type="ControlEvaluation", aggregate_id=r.get("evaluation_id") or ci_id,
         tenant_id=ctx.tenant, payload={"state": r["state"], "reason_codes": r["reason_codes"]})
    ctx.db.commit()
    return r


class TestDefIn(BaseModel):
    name: str
    source_class: str = "CONNECTOR"
    cadence_days: int = 30
    required: bool = True
    primary: bool = False
    degrades_on_fail: bool = False
    ipe_required: bool = False
    criterion: dict | None = None


class ControlIn(BaseModel):
    control_key: str
    subject_id: str
    method: str
    valid_from: datetime
    aggregation_mode: str = "ALL_REQUIRED"
    k_threshold: int | None = None
    automation_requirement: str = "PREFERRED"
    tests: list[TestDefIn]


@router.post("/controls")
def create_control(body: ControlIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_controls")
    cd = ctx.db.execute(select(ControlDefinition).where(ControlDefinition.key == body.control_key)).scalars().first()
    if not cd:
        raise HTTPException(404, "control definition")
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    dupe = ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant,
                                                              ControlImplementation.control_definition_id == cd.control_definition_id,
                                                              ControlImplementation.subject_id == s.subject_id)).scalars().first()
    if dupe:
        raise HTTPException(409, "Implementation already exists for this control/scope; frameworks are mappings, not clones (INV-08)")
    seq = next_record_seq(ctx.db)
    ci = ControlImplementation(tenant_id=ctx.tenant, entity_id=s.entity_id, subject_id=s.subject_id,
                               control_definition_id=cd.control_definition_id, owner_actor_id=ctx.actor.actor_id,
                               method=body.method, valid_from=body.valid_from, record_seq=seq)
    ctx.db.add(ci)
    ctx.db.flush()
    pol = ControlTestPolicy(tenant_id=ctx.tenant, control_implementation_id=ci.control_implementation_id,
                            aggregation_mode=body.aggregation_mode, k_threshold=body.k_threshold,
                            automation_requirement=body.automation_requirement, record_seq=seq)
    ctx.db.add(pol)
    ctx.db.flush()
    for t in body.tests:
        ctx.db.add(TestDefinition(tenant_id=ctx.tenant, policy_version_id=pol.policy_version_id, **t.model_dump()))
    ctx.db.commit()
    return to_dict(ci)


class TestRunIn(BaseModel):
    test_definition_id: str
    occurred_at: datetime
    window_from: datetime
    window_to: datetime
    result: str
    source_system: str
    external_status: str | None = None
    evidence_ids: list[str] = []
    source_run_id: str | None = None
    detail: str | None = None


@router.post("/test-runs")
def ingest_test_run(body: TestRunIn, ctx: Ctx = Depends(get_ctx)):
    """External GRC/vendor results are inputs with provenance — never direct control state (STA-REQ-008)."""
    ctx.require("submit_evidence")
    if body.result not in ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"):
        raise HTTPException(422, "Invalid test result code")
    td = ctx.scoped(ctx.db.get(TestDefinition, body.test_definition_id), "test definition")
    for e in body.evidence_ids:
        ctx.scoped(ctx.db.get(EvidenceArtifact, e), "evidence")
    tr = TestRun(tenant_id=ctx.tenant, record_seq=next_record_seq(ctx.db), **body.model_dump())
    ctx.db.add(tr)
    ctx.db.flush()
    emit(ctx.db, event_type="test.run_recorded", aggregate_type="TestRun", aggregate_id=tr.test_run_id, tenant_id=ctx.tenant,
         payload={"result": tr.result, "external_status": tr.external_status, "test": td.name})
    ctx.db.commit()
    return to_dict(tr)


# ------------------------------------------------------------------ exceptions (separate governance treatment)
class ExceptionIn(BaseModel):
    target_type: str
    target_id: str
    reason: str
    valid_from: datetime
    valid_to: datetime


@router.get("/exceptions")
def list_exceptions(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(x) for x in ctx.db.execute(select(ExceptionRecord).where(ExceptionRecord.tenant_id == ctx.tenant)
                                               .order_by(ExceptionRecord.valid_from.desc())).scalars()]


@router.post("/exceptions")
def create_exception(body: ExceptionIn, ctx: Ctx = Depends(get_ctx)):
    if ensure_utc(body.valid_to) <= ensure_utc(body.valid_from):
        raise HTTPException(422, "Exception must be time-bounded")
    x = ExceptionRecord(tenant_id=ctx.tenant, requested_by=ctx.actor.actor_id, state="PENDING_APPROVAL",
                        record_seq=next_record_seq(ctx.db), **body.model_dump())
    ctx.db.add(x)
    ctx.db.commit()
    return to_dict(x)


class DecisionIn(BaseModel):
    decision: str  # APPROVE | REJECT | REVOKE
    reason: str | None = None


@router.post("/exceptions/{exception_id}/decide")
def decide_exception(exception_id: str, body: DecisionIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require_human("Exception approval")
    ctx.require("approve_exception")
    x = ctx.scoped(ctx.db.get(ExceptionRecord, exception_id), "exception")
    if x.requested_by == ctx.actor.actor_id and body.decision == "APPROVE":
        raise HTTPException(403, "Requester cannot approve own exception")
    prior = x.state
    nxt = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REVOKE": "REVOKED"}.get(body.decision)
    if not nxt or (nxt in ("APPROVED", "REJECTED") and prior != "PENDING_APPROVAL") or (nxt == "REVOKED" and prior != "APPROVED"):
        raise HTTPException(409, f"Invalid transition {prior} -> {body.decision}")
    x.state = nxt
    x.approved_by = ctx.actor.actor_id if nxt == "APPROVED" else x.approved_by
    seq = next_record_seq(ctx.db)
    audit(ctx.db, action="exception.decide", object_type="Exception", object_id=exception_id, actor=ctx.actor,
          tenant_id=ctx.tenant, prior=prior, new=nxt, reason=body.reason, record_seq=seq)
    emit(ctx.db, event_type=f"exception.{nxt.lower()}", aggregate_type="Exception", aggregate_id=exception_id,
         tenant_id=ctx.tenant, record_seq=seq, payload={"target": x.target_id, "note": "no assurance-state mutation"})
    ctx.db.commit()
    return to_dict(x)


# ------------------------------------------------------------------ compensation (explicit expression branch)
class CompIn(BaseModel):
    obligation_lineage_id: str
    primary_control_key: str
    compensating_control_keys: list[str]
    equivalence_rationale: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/compensations")
def propose_compensation(body: CompIn, ctx: Ctx = Depends(get_ctx)):
    _current_obl(ctx.db, ctx.tenant, body.obligation_lineage_id)
    if len(body.equivalence_rationale.strip()) < 20:
        raise HTTPException(422, "Equivalence rationale required")
    kids = [{"node_type": "CONTROL", "node_id": f"comp_{k}", "control_key": k} for k in body.compensating_control_keys]
    expr = kids[0] if len(kids) == 1 else {"node_type": "ALL", "node_id": "comp_all", "children": kids}
    c = Compensation(tenant_id=ctx.tenant, obligation_lineage_id=body.obligation_lineage_id,
                     primary_control_key=body.primary_control_key, compensating_expression=expr,
                     equivalence_rationale=body.equivalence_rationale, valid_from=body.valid_from, valid_to=body.valid_to,
                     record_seq=next_record_seq(ctx.db))
    ctx.db.add(c)
    ctx.db.commit()
    return to_dict(c)


@router.get("/compensations")
def list_compensations(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(c) for c in ctx.db.execute(select(Compensation).where(Compensation.tenant_id == ctx.tenant)
                                               .order_by(Compensation.valid_from.desc())).scalars()]


def _wrap(node, key, comp):
    if node.get("node_type") == "CONTROL" and node.get("control_key") == key:
        return {"node_type": "COMPENSATION", "node_id": f"compensation_{comp.compensation_id[:8]}", "primary": node,
                "compensating": comp.compensating_expression, "compensation_id": comp.compensation_id}
    n = dict(node)
    if "children" in n:
        n["children"] = [_wrap(c, key, comp) for c in n["children"]]
    return n


@router.post("/compensations/{comp_id}/decide")
def decide_compensation(comp_id: str, body: DecisionIn, ctx: Ctx = Depends(get_ctx)):
    """Approval creates a NEW SatisfactionExpressionVersion (versioned, never a manual green flag)."""
    ctx.require_human("Compensation approval")
    ctx.require("approve_compensation")
    c = ctx.scoped(ctx.db.get(Compensation, comp_id), "compensation")
    seq = next_record_seq(ctx.db)
    if body.decision == "APPROVE":
        if c.status != "PENDING_APPROVAL":
            raise HTTPException(409, "not pending")
        c.status = "APPROVED"
        c.approved_by = ctx.actor.actor_id
        cur = ctx.db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == c.obligation_lineage_id)
            .order_by(SatisfactionExpressionVersion.version.desc())).scalars().first()
        new_ast = _wrap(copy.deepcopy(cur.ast), c.primary_control_key, c)
        ctx.db.add(SatisfactionExpressionVersion(tenant_id=ctx.tenant, obligation_lineage_id=c.obligation_lineage_id,
                                                 version=cur.version + 1, ast=new_ast, ast_hash=digest(new_ast),
                                                 rationale=f"Compensation {comp_id}: {c.equivalence_rationale}",
                                                 valid_from=c.valid_from, valid_to=None, record_seq=seq))
    elif body.decision in ("REVOKE", "REJECT"):
        c.status = "REVOKED" if body.decision == "REVOKE" else "REJECTED"
    else:
        raise HTTPException(422)
    emit(ctx.db, event_type=f"compensation.{c.status.lower()}", aggregate_type="Compensation", aggregate_id=comp_id,
         tenant_id=ctx.tenant, record_seq=seq, payload={"scope": c.primary_control_key})
    ctx.db.commit()
    return to_dict(c)


# ------------------------------------------------------------------ issues
@router.get("/issues")
def issues(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(i) for i in ctx.db.execute(select(Issue).where(Issue.tenant_id == ctx.tenant)
                                               .order_by(Issue.created_at.desc())).scalars()]


ISSUE_FLOW = {"OPEN": {"TRIAGED", "CANCELLED"}, "TRIAGED": {"IN_REMEDIATION", "CANCELLED"},
              "IN_REMEDIATION": {"PENDING_VERIFICATION"}, "PENDING_VERIFICATION": {"CLOSED", "IN_REMEDIATION"}}


class IssueMove(BaseModel):
    state: str
    reason: str | None = None


@router.post("/issues/{issue_id}/transition")
def move_issue(issue_id: str, body: IssueMove, ctx: Ctx = Depends(get_ctx)):
    """Closure never changes assurance state; a new evaluation must prove remediation (STA-REQ-023)."""
    i = ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    if body.state not in ISSUE_FLOW.get(i.state, set()):
        raise HTTPException(409, f"Invalid transition {i.state} -> {body.state}")
    if body.state == "CANCELLED" and not body.reason:
        raise HTTPException(422, "Cancellation reason required")
    prior = i.state
    i.state = body.state
    audit(ctx.db, action="issue.transition", object_type="Issue", object_id=issue_id, actor=ctx.actor, tenant_id=ctx.tenant,
          prior=prior, new=body.state, reason=body.reason)
    ctx.db.commit()
    return to_dict(i)


class RiskIn(BaseModel):
    scope_ref: str
    residual_risk_statement: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/risk-acceptances")
def accept_risk(body: RiskIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require_human("Risk acceptance")
    ctx.require("approve_exception")
    r = RiskAcceptance(tenant_id=ctx.tenant, approver_id=ctx.actor.actor_id, **body.model_dump())
    ctx.db.add(r)
    ctx.db.commit()
    return to_dict(r)


@router.get("/risk-acceptances")
def risks(ctx: Ctx = Depends(get_ctx)):
    return [to_dict(r) for r in ctx.db.execute(select(RiskAcceptance).where(RiskAcceptance.tenant_id == ctx.tenant)).scalars()]
