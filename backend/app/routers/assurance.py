"""Obligations, controls, tests, period assurance, exceptions, compensation, issues (ZA-STA-001)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select

from ..engines import assurance as asr
from ..engines import catalog, governance
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot,
                      Compensation, RuleVersion,
                      CompensationEvent, ControlDefinition, ControlEvaluation, ControlImplementation, ControlTestPolicy,
                      EvidenceArtifact, ExceptionEvent, ExceptionRecord, Issue, IssueEvent, ObligationDefinition,
                      ObligationInstance, ObligationOccurrence, ObligationOccurrenceEvent, Provision, RiskAcceptance,
                      SatisfactionExpressionVersion, SourceRun, Subject, TestDefinition, TestRun)
from ..platform import audit, digest, emit, ensure_utc, next_record_seq, to_dict
from ..security import DOSSIER_READ, Ctx, EntityOf, authorize, entity_of

router = APIRouter()


def _find_obl(db, tenant, lineage) -> ObligationInstance | None:
    return db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant,
                                                       ObligationInstance.lineage_id == lineage,
                                                       ObligationInstance.record_to_seq.is_(None))).scalars().first()


def _current_obl(db, tenant, lineage) -> ObligationInstance:
    o = _find_obl(db, tenant, lineage) if lineage else None
    if not o:
        raise HTTPException(404, "obligation not found")
    return o


def _lineage_entity(db, tenant, lineage) -> str | None:
    o = _find_obl(db, tenant, lineage) if lineage else None
    return o.entity_id if o else None


def _exception_entity(db, x: ExceptionRecord) -> str | None:
    # An exception on an obligation belongs to that obligation's legal entity; any other target resolves to no single
    # entity, so only organisation-wide roles reach it (fail closed).
    return _lineage_entity(db, x.tenant_id, x.target_id) if x.target_type == "OBLIGATION" else None


# Stage 4: how each dossier resource resolves to its legal entity, so entity-scoped roles reach their entity only.
OBLIGATION = EntityOf("obligation", lambda db, tenant, p: _find_obl(db, tenant, p["lineage"]) if p.get("lineage") else None)
SNAPSHOT = entity_of(AssuranceSnapshot, "snapshot_id", "snapshot")
CONTROL = entity_of(ControlImplementation, "ci_id", "control")
OCCURRENCE = entity_of(ObligationOccurrence, "occurrence_id", "occurrence",
                       entity=lambda db, o: _lineage_entity(db, o.tenant_id, o.obligation_lineage_id))
COMPENSATION = entity_of(Compensation, "comp_id", "compensation",
                         entity=lambda db, c: _lineage_entity(db, c.tenant_id, c.obligation_lineage_id))
EXCEPTION = entity_of(ExceptionRecord, "exception_id", "exception", entity=_exception_entity)


@router.get("/obligations")
def obligations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
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
def dossier(lineage: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):
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
        "regulatory_provenance": _provenance(ctx, det),
    }


def _provenance(ctx: Ctx, det) -> dict | None:
    """Obligation -> adopted determination -> its evaluation -> the exact RuleVersion -> requirement versions,
    provisions and source snapshots (DOM §1 canonical chain; DOM §16 end-to-end trace)."""
    ev = ctx.db.get(ApplicabilityEvaluation, det.evaluation_id) if det else None
    if ev is None or ev.tenant_id != ctx.tenant:
        return None
    rule = ctx.db.get(RuleVersion, ev.rule_version_id)
    out = catalog.trace_rule_version(ctx.db, rule) if rule else None
    if out is not None:
        out["evaluation"] = {"evaluation_id": ev.evaluation_id, "outcome": ev.outcome, "effective_at": ev.effective_at,
                             "record_cutoff_seq": ev.record_cutoff_seq, "input_bundle_hash": ev.input_bundle_hash,
                             "evaluator_build": ev.evaluator_build}
        out["determination_revision_id"] = det.revision_id
    return out


class PeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    period_from: datetime
    period_to: datetime
    record_cutoff_seq: int | None = None
    perspective: str = "RECONSTRUCTED_NOW"


@router.post("/obligations/{lineage}/snapshots")
def create_snapshot(lineage: str, body: PeriodIn, ctx: Ctx = Depends(authorize("request_evaluation", "package"))):
    # A persisted snapshot is an assurance record and can open issues; read-only roles may only preview.
    persp = body.perspective if body.record_cutoff_seq is None else "AS_KNOWN_THEN"
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq, perspective=persp,
                             actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return r


@router.post("/obligations/{lineage}/preview")
def preview(lineage: str, body: PeriodIn, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):  # computes and rolls back
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq,
                             perspective=body.perspective, actor_id=ctx.actor.actor_id, persist=False)
    ctx.db.rollback()
    return r


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SNAPSHOT))):
    return to_dict(ctx.scoped(ctx.db.get(AssuranceSnapshot, snapshot_id), "snapshot"))


@router.get("/snapshots/{snapshot_id}/reproduce")
def reproduce(snapshot_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SNAPSHOT))):
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    window_from: datetime
    due_at: datetime


@router.post("/obligations/{lineage}/occurrences")
def add_occurrence(lineage: str, body: OccIn, ctx: Ctx = Depends(authorize("manage_controls", "manage_facts"))):
    _current_obl(ctx.db, ctx.tenant, lineage)
    o, _ = governance.schedule_occurrence(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                          obligation_lineage_id=lineage, window_from=body.window_from,
                                          due_at=body.due_at)
    ctx.db.commit()
    return to_dict(o)


class FulfilIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    fulfilled_at: datetime
    evidence_ids: list[str]


@router.post("/occurrences/{occurrence_id}/fulfil")
def fulfil(occurrence_id: str, body: FulfilIn, ctx: Ctx = Depends(authorize("manage_controls", "manage_facts"))):
    """Records fulfilment as a new occurrence fact; a missed occurrence is never overwritten by later success."""
    ctx.scoped(ctx.db.get(ObligationOccurrence, occurrence_id), "occurrence")
    o, _ = governance.fulfil_occurrence(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                        occurrence_id=occurrence_id, fulfilled_at=body.fulfilled_at,
                                        evidence_ids=body.evidence_ids)
    ctx.db.commit()
    return to_dict(o)


@router.get("/occurrences/{occurrence_id}/events")
def occurrence_events(occurrence_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OCCURRENCE))):
    ctx.scoped(ctx.db.get(ObligationOccurrence, occurrence_id), "occurrence")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(ObligationOccurrenceEvent).where(
            ObligationOccurrenceEvent.tenant_id == ctx.tenant,
            ObligationOccurrenceEvent.occurrence_id == occurrence_id,
        ).order_by(ObligationOccurrenceEvent.record_seq.asc())
    ).scalars()]


# ------------------------------------------------------------------ controls
@router.get("/controls")
def controls(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    out = []
    now = datetime.now(timezone.utc)
    cis = ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant)).scalars().all()
    # The definitions and subjects the rows refer to, read once and held here so db.get() below finds them in the
    # session instead of querying for every control.
    refs = [*ctx.db.execute(select(ControlDefinition).where(
                ControlDefinition.control_definition_id.in_({ci.control_definition_id for ci in cis}))).scalars(),
            *ctx.db.execute(select(Subject).where(Subject.subject_id.in_({ci.subject_id for ci in cis}))).scalars()]
    for ci in cis:
        d = to_dict(ci)
        cd = ctx.db.get(ControlDefinition, ci.control_definition_id)
        d["definition"] = to_dict(cd)
        d["subject"] = ctx.db.get(Subject, ci.subject_id).display_name
        try:
            ce = asr.evaluate_control(ctx.db, ci, now - timedelta(days=1), now, 10**15, persist=False)
            d["current_state"] = {"state": ce["state"], "reason_codes": ce["reason_codes"], "window": "last 24h",
                                  "note": "Convenience view; authoritative state is the immutable ControlEvaluation."}
        except asr.AssuranceConfigurationError as e:  # shown as an error, never as a control state (STA §16)
            d["current_state"] = {"state": None, "configuration_error": e.detail, "window": "last 24h"}
        out.append(d)
    del refs
    return out


@router.get("/controls/{ci_id}")
def control_detail(ci_id: str, days: int = 30, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=CONTROL))):
    """WF-STA-02 Control Evaluation."""
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    now = datetime.now(timezone.utc)
    try:
        ce = asr.evaluate_control(ctx.db, ci, now - timedelta(days=days), now, 10**15, persist=False)
    except asr.AssuranceConfigurationError as e:  # an error object, never a control state (STA §16)
        ce = {"state": None, "configuration_error": e.detail}
    pols =ctx.db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci_id)).scalars().all()
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    window_from: datetime
    window_to: datetime


@router.post("/controls/{ci_id}/evaluate")
def evaluate_control(ci_id: str, body: WindowIn,
                     ctx: Ctx = Depends(authorize("manage_controls", "request_evaluation", "package"))):
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    from ..platform import current_watermark
    r = asr.evaluate_control(ctx.db, ci, body.window_from, body.window_to, current_watermark(ctx.db)["record_seq"])
    emit(ctx.db, event_type="control.evaluated", aggregate_type="ControlEvaluation", aggregate_id=r.get("evaluation_id") or ci_id,
         tenant_id=ctx.tenant, payload={"state": r["state"], "reason_codes": r["reason_codes"]})
    ctx.db.commit()
    return r


class TestDefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    name: str
    source_class: str = "CONNECTOR"
    cadence_days: int = 30
    required: bool = True
    primary: bool = False
    degrades_on_fail: bool = False
    ipe_required: bool = False
    criterion: dict | None = None


class ControlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    control_key: str
    subject_id: str
    method: str
    valid_from: datetime
    aggregation_mode: str = "ALL_REQUIRED"
    k_threshold: StrictInt | None = None  # strict: "3", 3.0 or true are refused, never coerced
    automation_requirement: str = "PREFERRED"
    tests: list[TestDefIn]

    @model_validator(mode="after")
    def _policy(self):
        # ZA-STA-001 §03 vocabularies and the K/primary invariants; the evaluator enforces the same rules on stored data.
        problems = asr.policy_problems(self.aggregation_mode, self.k_threshold, self.automation_requirement,
                                       [(t.required, t.primary) for t in self.tests])
        if problems:
            raise ValueError("; ".join(problems))
        return self


@router.post("/controls")
def create_control(body: ControlIn, ctx: Ctx = Depends(authorize("manage_controls"))):
    from sqlalchemy.exc import IntegrityError
    cd = ctx.db.execute(select(ControlDefinition).where(ControlDefinition.key == body.control_key)).scalars().first()
    if not cd:
        raise HTTPException(404, "control definition")
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    dupe_msg = "Implementation already exists for this control/scope; frameworks are mappings, not clones (INV-08)"
    dupe = ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant,
                                                              ControlImplementation.control_definition_id == cd.control_definition_id,
                                                              ControlImplementation.subject_id == s.subject_id)).scalars().first()
    if dupe:
        raise HTTPException(409, dupe_msg)
    seq = next_record_seq(ctx.db)
    ci = ControlImplementation(tenant_id=ctx.tenant, entity_id=s.entity_id, subject_id=s.subject_id,
                               control_definition_id=cd.control_definition_id, owner_actor_id=ctx.actor.actor_id,
                               method=body.method, valid_from=body.valid_from, record_seq=seq)
    ctx.db.add(ci)
    try:
        ctx.db.flush()
    except IntegrityError:  # a concurrent creation took the (tenant, definition, subject, variant) key (INV-08)
        ctx.db.rollback()
        raise HTTPException(409, dupe_msg) from None
    pol = ControlTestPolicy(tenant_id=ctx.tenant, control_implementation_id=ci.control_implementation_id,
                            aggregation_mode=body.aggregation_mode, k_threshold=body.k_threshold,
                            automation_requirement=body.automation_requirement, record_seq=seq)
    ctx.db.add(pol)
    ctx.db.flush()
    for t in body.tests:
        ctx.db.add(TestDefinition(tenant_id=ctx.tenant, policy_version_id=pol.policy_version_id, **t.model_dump()))
    audit(ctx.db, action="control_implementation.create", object_type="ControlImplementation",
          object_id=ci.control_implementation_id, actor=ctx.actor, tenant_id=ctx.tenant, record_seq=seq, reason=(f"{cd.key} for subject {s.subject_id}; policy {pol.policy_version_id} "
                                  f"{body.aggregation_mode} k={body.k_threshold} automation={body.automation_requirement}; "
                                  f"{len(body.tests)} test(s)"))
    ctx.db.commit()
    return to_dict(ci)


class TestRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
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
def ingest_test_run(body: TestRunIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    """External GRC/vendor results are inputs with provenance — never direct control state (STA-REQ-008)."""
    if body.result not in ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"):
        raise HTTPException(422, "Invalid test result code")
    # ZA-TIM-001 §02: half-open, non-empty ranges; an empty or inverted window is not an assertion about any period.
    if ensure_utc(body.window_to) <= ensure_utc(body.window_from):
        raise HTTPException(422, {"error": "TEST_RUN_WINDOW_EMPTY", "message": "window_to must be later than window_from"})
    td = ctx.scoped(ctx.db.get(TestDefinition, body.test_definition_id), "test definition")
    for e in body.evidence_ids:
        ctx.scoped(ctx.db.get(EvidenceArtifact, e), "evidence")
    if body.source_run_id:  # assurance reads this run's IPE result, so it must be one of this organisation's runs
        ctx.scoped(ctx.db.get(SourceRun, body.source_run_id), "source run")
    seq = next_record_seq(ctx.db)
    tr = TestRun(tenant_id=ctx.tenant, record_seq=seq, **body.model_dump())
    ctx.db.add(tr)
    ctx.db.flush()
    audit(ctx.db, action="test_run.record", object_type="TestRun", object_id=tr.test_run_id, actor=ctx.actor,
          tenant_id=ctx.tenant, new=tr.result, record_seq=seq,
          reason=f"test {td.test_definition_id} from {tr.source_system}; window [{tr.window_from}, {tr.window_to})")
    emit(ctx.db, event_type="test.run_recorded", aggregate_type="TestRun", aggregate_id=tr.test_run_id, tenant_id=ctx.tenant,
         payload={"result": tr.result, "external_status": tr.external_status, "test": td.name})
    ctx.db.commit()
    return to_dict(tr)


# ------------------------------------------------------------------ exceptions (separate governance treatment)
class ExceptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    target_type: str
    target_id: str
    reason: str
    valid_from: datetime
    valid_to: datetime


@router.get("/exceptions")
def list_exceptions(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(x) for x in ctx.db.execute(select(ExceptionRecord).where(ExceptionRecord.tenant_id == ctx.tenant)
                                               .order_by(ExceptionRecord.valid_from.desc())).scalars()]


@router.post("/exceptions")
def create_exception(body: ExceptionIn, ctx: Ctx = Depends(authorize("request_exception"))):
    if ensure_utc(body.valid_to) <= ensure_utc(body.valid_from):
        raise HTTPException(422, "Exception must be time-bounded")
    # The target must be one of this organisation's obligations or control implementations.
    if body.target_type == "OBLIGATION":
        _current_obl(ctx.db, ctx.tenant, body.target_id)
    elif body.target_type == "CONTROL":
        ctx.scoped(ctx.db.get(ControlImplementation, body.target_id), "control")
    else:
        raise HTTPException(422, "target_type must be OBLIGATION or CONTROL")
    x, _ = governance.request_exception(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                        target_type=body.target_type, target_id=body.target_id,
                                        reason=body.reason, valid_from=body.valid_from, valid_to=body.valid_to)
    ctx.db.commit()
    return to_dict(x)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    decision: str  # APPROVE | REJECT | REVOKE
    reason: str | None = None


@router.post("/exceptions/{exception_id}/decide")
def decide_exception(exception_id: str, body: DecisionIn, ctx: Ctx = Depends(authorize("approve_exception", human=True))):
    ctx.scoped(ctx.db.get(ExceptionRecord, exception_id), "exception")
    x, _ = governance.decide_exception(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                       exception_id=exception_id, decision=body.decision, reason=body.reason)
    ctx.db.commit()
    return to_dict(x)


@router.get("/exceptions/{exception_id}/events")
def exception_events(exception_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EXCEPTION))):
    ctx.scoped(ctx.db.get(ExceptionRecord, exception_id), "exception")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(ExceptionEvent).where(
            ExceptionEvent.tenant_id == ctx.tenant,
            ExceptionEvent.exception_id == exception_id,
        ).order_by(ExceptionEvent.record_seq.asc())
    ).scalars()]


# ------------------------------------------------------------------ compensation (explicit expression branch)
class CompIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    obligation_lineage_id: str
    primary_control_key: str
    # ZA-STA-001 §09: "One or more controls". Existence, duplicates and scope are checked by the domain layer
    # (governance.compensation_problems), which also re-checks at approval.
    compensating_control_keys: list[str] = Field(min_length=1)
    equivalence_rationale: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/compensations")
def propose_compensation(body: CompIn, ctx: Ctx = Depends(authorize("request_exception"))):
    _current_obl(ctx.db, ctx.tenant, body.obligation_lineage_id)
    if len(body.equivalence_rationale.strip()) < 20:
        raise HTTPException(422, "Equivalence rationale required")
    kids = [{"node_type": "CONTROL", "node_id": f"comp_{k}", "control_key": k} for k in body.compensating_control_keys]
    expr = kids[0] if len(kids) == 1 else {"node_type": "ALL", "node_id": "comp_all", "children": kids}
    c, _ = governance.propose_compensation(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                           obligation_lineage_id=body.obligation_lineage_id,
                                           primary_control_key=body.primary_control_key, compensating_expression=expr,
                                           equivalence_rationale=body.equivalence_rationale, valid_from=body.valid_from,
                                           valid_to=body.valid_to)
    ctx.db.commit()
    return to_dict(c)


@router.get("/compensations")
def list_compensations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(c) for c in ctx.db.execute(select(Compensation).where(Compensation.tenant_id == ctx.tenant)
                                               .order_by(Compensation.valid_from.desc())).scalars()]


@router.post("/compensations/{comp_id}/decide")
def decide_compensation(comp_id: str, body: DecisionIn,
                        ctx: Ctx = Depends(authorize("approve_compensation", human=True))):
    """Approval creates a NEW SatisfactionExpressionVersion (versioned, never a manual green flag)."""
    ctx.scoped(ctx.db.get(Compensation, comp_id), "compensation")
    c, _ = governance.decide_compensation(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                          comp_id=comp_id, decision=body.decision, reason=body.reason)
    ctx.db.commit()
    return to_dict(c)


@router.get("/compensations/{comp_id}/events")
def compensation_events(comp_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=COMPENSATION))):
    ctx.scoped(ctx.db.get(Compensation, comp_id), "compensation")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(CompensationEvent).where(
            CompensationEvent.tenant_id == ctx.tenant,
            CompensationEvent.compensation_id == comp_id,
        ).order_by(CompensationEvent.record_seq.asc())
    ).scalars()]


# ------------------------------------------------------------------ issues
@router.get("/issues")
def issues(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(i) for i in ctx.db.execute(select(Issue).where(Issue.tenant_id == ctx.tenant)
                                               .order_by(Issue.created_at.desc())).scalars()]


ISSUE_FLOW = {"OPEN": {"TRIAGED", "CANCELLED"}, "TRIAGED": {"IN_REMEDIATION", "CANCELLED"},
              "IN_REMEDIATION": {"PENDING_VERIFICATION"}, "PENDING_VERIFICATION": {"CLOSED", "IN_REMEDIATION"}}


class IssueMove(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    state: str
    reason: str | None = None


@router.post("/issues/{issue_id}/transition")
def move_issue(issue_id: str, body: IssueMove, ctx: Ctx = Depends(authorize("manage_issues"))):
    """Closure never changes assurance state; a new evaluation must prove remediation (STA-REQ-023)."""
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    i, _ = governance.transition_issue(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                       issue_id=issue_id, to_state=body.state, reason=body.reason)
    ctx.db.commit()
    return to_dict(i)


@router.get("/issues/{issue_id}/events")
def issue_events(issue_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(IssueEvent).where(
            IssueEvent.tenant_id == ctx.tenant,
            IssueEvent.issue_id == issue_id,
        ).order_by(IssueEvent.record_seq.asc())
    ).scalars()]


class RiskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    scope_ref: str
    residual_risk_statement: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/risk-acceptances")
def accept_risk(body: RiskIn, ctx: Ctx = Depends(authorize("approve_exception", human=True))):
    if body.valid_to is not None and ensure_utc(body.valid_to) <= ensure_utc(body.valid_from):
        raise HTTPException(422, {"error": "RISK_ACCEPTANCE_PERIOD_EMPTY", "message": "valid_to must be later than valid_from"})
    r = RiskAcceptance(tenant_id=ctx.tenant, approver_id=ctx.actor.actor_id, **body.model_dump())
    ctx.db.add(r)
    ctx.db.flush()
    # STA-REQ-024: recorded separately from assurance state, but recorded (audit finding F-13).
    audit(ctx.db, action="risk_acceptance.create", object_type="RiskAcceptance",
          object_id=r.risk_acceptance_id, actor=ctx.actor,
          tenant_id=ctx.tenant, record_seq=next_record_seq(ctx.db), reason=f"scope {body.scope_ref}")
    ctx.db.commit()
    return to_dict(r)


@router.get("/risk-acceptances")
def risks(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(r) for r in ctx.db.execute(select(RiskAcceptance).where(RiskAcceptance.tenant_id == ctx.tenant)).scalars()]
