"""Facts, applicability evaluations, determinations, challenges and fact requests (ZA-APP-001)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..engines import applicability as appl
from ..engines import catalog, idempotency, rules
from ..engines.facts import assert_fact, assertion_value, resolve_fact
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, DeterminationChallenge,
                      FactAssertion, FactConflict, FactDefinition, FactRequest, ObligationDefinition, RuleVersion, Subject)
from ..platform import audit, current_watermark, emit, ensure_utc, next_record_seq, to_dict
from ..security import DOSSIER_READ, READ, Ctx, authorize, entity_of

router = APIRouter()
# Stage 4: how subjects and evaluations resolve to their legal entity, so entity-scoped roles reach their entity only.
SUBJECT = entity_of(Subject, "subject_id", "subject")
EVALUATION = entity_of(ApplicabilityEvaluation, "evaluation_id", "evaluation")


def _utc_representable(v: datetime | None) -> datetime | None:
    """An instant whose UTC conversion leaves the calendar (year 1 east of UTC, year 9999 west of it) has no UTC instant
    to store, freeze or read a legal date from. It used to be accepted here and then fail in that conversion with an
    unhandled OverflowError (a 500); it is a 422 now. Every other value is accepted and read exactly as before."""
    if v is not None:
        try:
            ensure_utc(v)
        except OverflowError:
            raise ValueError("this instant has no UTC equivalent within years 1-9999") from None
    return v


# The instants this router takes (effective_at, valid_at, fact validity): the same parsing as a plain datetime.
Instant = Annotated[datetime, AfterValidator(_utc_representable)]
# Record-sequence coordinates are compared with BIGINT record_seq columns. Below that range a value could not be bound to
# the query (PostgreSQL: "bigint out of range", SQLite: OverflowError; a 500 either way), so it is a 422; every value in
# the range is answered as before, and one beyond the watermark is still RECORD_SEQ_IN_FUTURE.
RECORD_SEQ_MIN = -(2 ** 63)


def _fact_row(db, a: FactAssertion, fd: FactDefinition) -> dict:
    d = to_dict(a)
    d["value"] = assertion_value(a) if fd.sensitivity not in ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED") else "••• (redacted)"
    d["fact_key"] = fd.key
    d["fact_label"] = fd.label
    return d


@router.get("/subjects/{subject_id}/facts")
def fact_profile(subject_id: str, valid_at: Instant | None = None,
                 known_seq: int | None = Query(default=None, ge=RECORD_SEQ_MIN),
                 ctx: Ctx = Depends(authorize(*READ, entity=SUBJECT))):
    """Fact profile at an explicit bitemporal coordinate (CURRENT if both omitted; echoed back)."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    wm = current_watermark(ctx.db)
    if known_seq is not None and known_seq > wm["record_seq"]:
        # TIM-REQ-019: never answered from newer knowledge than the requested cutoff can have had (the same refusal as
        # every other historical read and as applicability evaluation).
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"known_seq is beyond the current record sequence {wm['record_seq']}"})
    valid_at = ensure_utc(valid_at) or datetime.now(timezone.utc)
    known = known_seq if known_seq is not None else wm["record_seq"]
    fds = {f.fact_definition_id: f for f in ctx.db.execute(select(FactDefinition)).scalars()}
    rows = []
    for fd in fds.values():
        if fd.subject_type != s.subject_type:
            continue
        r = resolve_fact(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, effective_at=valid_at, known_seq=known,
                         create_conflicts=False)
        rows.append({"fact_key": fd.key, "label": fd.label, "data_type": fd.data_type, "enum_domain": fd.enum_domain,
                     "sensitivity": fd.sensitivity, "allowed_provenance": fd.allowed_provenance,
                     "manual_attestation_policy": fd.manual_attestation_policy, "expiry_days": fd.expiry_days,
                     "resolution": {k: v for k, v in r.items() if k != "value"} | (
                         {"value": r["value"] if fd.sensitivity not in ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED") else "••• (redacted)"}
                         if r["known"] else {})})
    conflicts = [to_dict(c) for c in ctx.db.execute(select(FactConflict).where(FactConflict.tenant_id == ctx.tenant,
                                                                               FactConflict.subject_id == subject_id)).scalars()]
    return {"subject": to_dict(s), "perspective": {"valid_at": valid_at, "known_seq": known, "cell_id": wm["cell_id"],
                                                   "perspective": "CURRENT" if known_seq is None else "CUSTOM_BITEMPORAL"},
            "facts": rows, "conflicts": conflicts}


@router.get("/subjects/{subject_id}/facts/history")
def fact_history(subject_id: str, ctx: Ctx = Depends(authorize(*READ, entity=SUBJECT))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    fds = {f.fact_definition_id: f for f in ctx.db.execute(select(FactDefinition)).scalars()}
    rows = ctx.db.execute(select(FactAssertion).where(FactAssertion.tenant_id == ctx.tenant,
                                                      FactAssertion.subject_id == s.subject_id)
                          .order_by(FactAssertion.record_from_seq)).scalars().all()
    return [_fact_row(ctx.db, a, fds[a.fact_definition_id]) for a in rows]


class FactIn(BaseModel):
    # Provenance is server-derived (APP-REQ-005, API-REQ-014): a client-supplied provenance_class is rejected.
    model_config = ConfigDict(extra="forbid")
    fact_key: str
    value: object
    valid_from: Instant
    valid_to: Instant | None = None
    source_ref: str | None = Field(default=None, max_length=300)  # API-REQ-033: applicability_fact_assertion_revision.source_ref
    change_kind: str = "CREATE"
    reason: str | None = None


@router.post("/subjects/{subject_id}/facts")
def assert_fact_ep(subject_id: str, body: FactIn, ctx: Ctx = Depends(authorize("manage_facts"))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    fd = ctx.db.execute(select(FactDefinition).where(FactDefinition.key == body.fact_key)).scalars().first()
    if not fd:
        raise HTTPException(404, "FactDefinition not found")
    if fd.subject_type != s.subject_type:
        raise HTTPException(422, f"{fd.key} attaches to {fd.subject_type}")
    if ctx.actor.actor_type == "AGENT":
        provenance = "P5_INFERRED_AI"  # agent-proposed facts are never binding
    elif ctx.actor.actor_type == "HUMAN":
        provenance = "P3_CUSTOMER_ATTESTED"
        if fd.manual_attestation_policy == "DISALLOWED":
            raise HTTPException(422, f"{fd.key} does not allow manual attestation; use a certified source")
    else:
        raise HTTPException(403, "System-sourced facts must arrive through the certified connector path, not this endpoint")
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        # The dependents' scopes are locked before the fact write takes the record sequence (lock order:
        # engines/applicability.lock_decisions), so this cannot deadlock with an attestation of one of them.
        plan = appl.fact_impact_plan(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd)
        appl.lock_fact_impact(ctx.db, ctx.tenant, plan)
        created = assert_fact(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, value=body.value, valid_from=body.valid_from,
                              valid_to=body.valid_to, provenance_class=provenance, source_ref=body.source_ref,
                              actor_id=ctx.actor.actor_id, change_kind=body.change_kind, reason=body.reason)
        reeval = appl.reevaluate_for_fact(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd, actor_id=ctx.actor.actor_id,
                                          plan=plan)
        ctx.db.commit()
    return {"revisions": [_fact_row(ctx.db, a, fd) for a in created], "impact_reevaluations": reeval}


class EvalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    subject_id: str
    obligation_definition_id: str
    effective_at: Instant | None = None
    record_cutoff_seq: int | None = Field(default=None, ge=RECORD_SEQ_MIN)
    # ZA-APP-001 §06 EvaluationRequest.trigger: "Enum; required ONBOARDING, FACT_CHANGED, RULE_CHANGED, CORRECTION,
    # RECLASSIFICATION, EXPLICIT_REQUEST, SCHEDULED_REVIEW, RECONSTRUCTION" (stored on the immutable evaluation).
    trigger: Literal["ONBOARDING", "FACT_CHANGED", "RULE_CHANGED", "CORRECTION", "RECLASSIFICATION",
                     "EXPLICIT_REQUEST", "SCHEDULED_REVIEW", "RECONSTRUCTION"] = "EXPLICIT_REQUEST"


_LOOK_UP = object()


def _eval_view(db, e: ApplicabilityEvaluation, det=_LOOK_UP, traces: dict | None = None) -> dict:
    """`traces`: provenance already traced in this request, by rule version (a list shares a few rule versions)."""
    d = to_dict(e)
    # Which APP-001 §06 key definition the stored evaluation_key was made under (recomputed from the frozen bundle; a
    # record made before the full key is reported as such, never as if made under it).
    d["evaluation_key_profile"] = appl.evaluation_key_profile(e)
    od = db.get(ObligationDefinition, e.obligation_definition_id)
    s = db.get(Subject, e.subject_id)
    rv = db.get(RuleVersion, e.rule_version_id)
    # The definition and subject are referenced by ID only (no foreign key across these domains): one that cannot be
    # found is reported as missing, so one broken reference never fails the whole view or list.
    d["obligation_definition"] = ({"key": od.key, "title": od.title, "material": od.material} if od else
                                  missing_ref("obligation_definition_id", e.obligation_definition_id, "key", "title"))
    d["subject"] = ({"display_name": s.display_name, "subject_type": s.subject_type} if s else
                    missing_ref("subject_id", e.subject_id, "display_name", "subject_type"))
    # A rule version the evaluation names but that cannot be found is reported as missing, never replaced by another
    # version (APP-REQ-003; no foreign key stops it, so the read must not assume it).
    d["rule_version"] = {"version": rv.version, "ast_hash": rv.ast_hash, "content_release_id": rv.content_release_id,
                         "source_mappings": rv.source_mappings} if rv else {"rule_version_id": e.rule_version_id,
                                                                            "missing": True}
    if det is _LOOK_UP:
        det = appl.current_determination(db, e.tenant_id, e.subject_id, e.obligation_definition_id)
    d["current_determination"] = to_dict(det) if det else None
    if rv is None:
        d["regulatory_provenance"] = None
    elif traces is not None:
        if rv.rule_version_id not in traces:
            traces[rv.rule_version_id] = catalog.trace_rule_version(db, rv)
        d["regulatory_provenance"] = evaluation_provenance(e, rv, traces[rv.rule_version_id])
    else:
        d["regulatory_provenance"] = evaluation_provenance(e, rv, catalog.trace_rule_version(db, rv))
    return d


def missing_ref(id_field: str, value: str | None, *fields: str) -> dict:
    """A referenced record that cannot be found: its ID, the display fields as null and ``missing: true``."""
    return {id_field: value, **dict.fromkeys(fields), "missing": True}


def evaluation_provenance(e: ApplicabilityEvaluation, rule: RuleVersion, trace: dict) -> dict:
    """The rule trace as this evaluation's provenance: plus whether the rule belongs to the evaluated definition."""
    return catalog.with_problems(trace, catalog.evaluation_rule_problems(e, rule))


@router.post("/applicability/evaluations")
def request_evaluation(body: EvalIn, ctx: Ctx = Depends(authorize("request_evaluation"))):
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, body.obligation_definition_id)
    if not od:
        raise HTTPException(404, "obligation definition")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if body.record_cutoff_seq is not None and body.record_cutoff_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"record_cutoff_seq is beyond the current record sequence {now_seq}"})
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od,
                           effective_at=body.effective_at or datetime.now(timezone.utc),
                           record_cutoff_seq=body.record_cutoff_seq, trigger=body.trigger, actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return _eval_view(ctx.db, ev)


@router.post("/applicability/evaluate-all")
def evaluate_all(subject_id: str, ctx: Ctx = Depends(authorize("request_evaluation"))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    out = []
    odefs = ctx.db.execute(select(ObligationDefinition).where(ObligationDefinition.subject_type == s.subject_type)
                           .order_by(ObligationDefinition.obligation_definition_id)).scalars().all()
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):  # no partial batch on tampering
        appl.lock_decisions(ctx.db, [(ctx.tenant, s.subject_id, od.obligation_definition_id) for od in odefs])
        for od in odefs:
            try:
                ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=datetime.now(timezone.utc),
                                   record_cutoff_seq=None, trigger="EXPLICIT_REQUEST", actor_id=ctx.actor.actor_id)
                out.append({"obligation": od.key, "outcome": ev.outcome, "evaluation_id": ev.evaluation_id})
            except HTTPException as e:
                out.append({"obligation": od.key, "error": e.detail})
    ctx.db.commit()
    return out


@router.get("/applicability/evaluations")
def list_evaluations(subject_id: str | None = None, ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    q = select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.tenant_id == ctx.tenant)
    if subject_id:
        q = q.where(ApplicabilityEvaluation.subject_id == subject_id)
    evs = ctx.db.execute(q.order_by(ApplicabilityEvaluation.record_seq.desc()).limit(200)).scalars().all()
    # What the rows refer to, read once instead of per row: the subjects, obligation definitions and rule versions are
    # loaded into the session (and held here) so db.get() in _eval_view finds them without a query.
    refs = [*ctx.db.execute(select(Subject).where(Subject.subject_id.in_({e.subject_id for e in evs}))).scalars(),
            *ctx.db.execute(select(ObligationDefinition).where(
                ObligationDefinition.obligation_definition_id.in_({e.obligation_definition_id for e in evs}))).scalars(),
            *ctx.db.execute(select(RuleVersion).where(RuleVersion.rule_version_id.in_({e.rule_version_id for e in evs}))).scalars()]
    dets = appl.current_determinations(ctx.db, ctx.tenant, {(e.subject_id, e.obligation_definition_id) for e in evs})
    traces: dict = {}
    out = [_eval_view(ctx.db, e, dets.get((e.subject_id, e.obligation_definition_id)), traces) for e in evs]
    del refs
    return out


@router.get("/applicability/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EVALUATION))):
    e = ctx.scoped(ctx.db.get(ApplicabilityEvaluation, evaluation_id), "evaluation")
    d = _eval_view(ctx.db, e)
    d["determination_history"] = [to_dict(x) for x in ctx.db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == ctx.tenant, ApplicabilityDetermination.subject_id == e.subject_id,
        ApplicabilityDetermination.obligation_definition_id == e.obligation_definition_id)
        .order_by(ApplicabilityDetermination.record_from_seq)).scalars()]
    d["challenges"] = [to_dict(c) for c in ctx.db.execute(select(DeterminationChallenge).where(
        DeterminationChallenge.tenant_id == ctx.tenant, DeterminationChallenge.evaluation_id == evaluation_id)).scalars()]
    d["attestations"] = [to_dict(a) for a in ctx.db.execute(select(AdoptionAttestation).where(
        AdoptionAttestation.tenant_id == ctx.tenant,
        AdoptionAttestation.target_revision_id.in_([x["revision_id"] for x in d["determination_history"]] or ["-"]))).scalars()]
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        d["replay"] = _replay(ctx, e)
    # The exact RuleVersion this evaluation ran, traced to the requirement versions, provisions and source snapshots it
    # rests on and to how each required fact maps to source content (DOM §1 canonical chain; ZA-RUL-001 §02/§08).
    rule = ctx.db.get(RuleVersion, e.rule_version_id)
    d["regulatory_provenance"] = (evaluation_provenance(e, rule, catalog.trace_rule_version(ctx.db, rule)) if rule
                                  else _missing_rule_provenance(e))
    d["record_watermark"] = _watermark(ctx.db)
    return d


def _missing_rule_provenance(e: ApplicabilityEvaluation) -> dict:
    """The provenance of an evaluation whose RuleVersion cannot be found: explicitly incomplete, never derived from
    another (newer or older) version of the rule."""
    return {"binding_basis": "INCOMPLETE", "rule_version_id": e.rule_version_id, "missing": True,
            "integrity": catalog.integrity_verdict([catalog._problem("RULE_VERSION_MISSING", "INCOMPLETE",
                                                                     rule_version_id=e.rule_version_id)])}


def _watermark(db) -> dict:
    """The record watermark this response was read at (TIM-REQ-017)."""
    wm = current_watermark(db)
    return {"cell_id": wm["cell_id"], "record_seq": wm["record_seq"]}


def _replay(ctx: Ctx, e: ApplicabilityEvaluation) -> dict:
    """Deterministic reproducibility check (TIM-REQ-012, APP-REQ-003): re-resolve the exact fact revisions known at the
    evaluation's record cutoff under its own RuleVersion and evaluator, and compare with what was recorded. Nothing is
    written. `matches` keeps its meaning (same outcome and same input bundle hash); `checks` and `status` add the
    stored-record checks:

      RULE_VERSION_PRESENT  the RuleVersion the evaluation names still exists (never substituted by another);
      RUNTIME_SUPPORTS_VERSIONS / RUNTIME_WITHIN_LIMITS  this runtime may execute it at all (RUL-REQ-028 / -029); if
                            not, nothing is run and the status is NOT_REPRODUCED;
      BUNDLE_BINDS_RULE     the stored bundle names that RuleVersion and its AST hash, and the evaluator build the row
                            records (the build its inputs were frozen and hashed under: a row naming another build, or
                            none, does not establish which evaluator produced it);
      STORED_BUNDLE_HASH    the stored frozen bundle still hashes to the recorded input_bundle_hash;
      EVALUATOR_BUILD       the running evaluator build is the one recorded (replay across builds is a governed
                            regression, STA-REQ-032);
      REDERIVED_BUNDLE      the fact revisions known at the cutoff re-derive the recorded inputs. Compared under one
                            build: the bundle hash includes the build, so comparing the running build's hash with the
                            record's would fail for every evaluation of another build and hide altered inputs there;
      OUTCOME, TRACE        the evaluator returns the recorded outcome and the same trace tree.

    status: REPRODUCED (every check passed); BUILD_CHANGED (the evaluation was recorded by another evaluator build and its
    stored and re-derived inputs are intact: ZA-APP-001 §14 makes the result deterministic only for "Same evaluator build +
    RuleVersion + EvaluationInputBundle hash", so whatever this build answers, including a refusal, neither reproduces nor
    refutes the record); NOT_REPRODUCED (a stored-record or input check failed, or this build gives another answer for
    the build the record names); INCOMPLETE (a referenced record is missing)."""
    from ..codes import EVALUATOR_BUILD
    from ..platform import digest
    s = ctx.db.get(Subject, e.subject_id)
    od = ctx.db.get(ObligationDefinition, e.obligation_definition_id)
    rv = ctx.db.get(RuleVersion, e.rule_version_id)
    if rv is None or s is None or od is None:
        missing = "RULE_VERSION_MISSING" if rv is None else "SUBJECT_MISSING" if s is None else "OBLIGATION_DEFINITION_MISSING"
        return {"reproduced_outcome": None, "matches": False, "error": missing, "rule_version_id": e.rule_version_id,
                "status": "INCOMPLETE", "checks": [{"check": "RULE_VERSION_PRESENT", "passed": rv is not None}]}
    stored = e.input_bundle or {}
    unsupported = rules.unsupported_versions(rv.rule_schema_version, rv.operator_set_version)
    if unsupported:
        # RUL-REQ-028: this runtime cannot run the rule version, so it cannot reproduce the evaluation (and does not
        # try an approximate run). The stored record is unchanged and still readable.
        return {"reproduced_outcome": None, "matches": False, "error": "SCHEMA_INCOMPATIBLE", "unsupported": unsupported,
                "status": "NOT_REPRODUCED", "failed_checks": ["RUNTIME_SUPPORTS_VERSIONS"],
                "checks": [{"check": "RULE_VERSION_PRESENT", "passed": True},
                           {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": False, **unsupported}]}
    over = rules.resource_limit_violations(rv.canonical_ast)
    if over:
        # RUL-REQ-029: the rule version exceeds this runtime's complexity limits, so it is not run (a budget breach is
        # an infrastructure error) and the evaluation cannot be reproduced here; no approximate or partial run.
        return {"reproduced_outcome": None, "matches": False, "error": "EVALUATOR_UNAVAILABLE",
                "reason": "RUL_RESOURCE_LIMIT", "violations": [{k: v for k, v in x.items() if k != "message"} for x in over],
                "status": "NOT_REPRODUCED", "failed_checks": ["RUNTIME_WITHIN_LIMITS"],
                "checks": [{"check": "RULE_VERSION_PRESENT", "passed": True},
                           {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": True},
                           {"check": "RUNTIME_WITHIN_LIMITS", "passed": False}]}
    checks = [{"check": "RULE_VERSION_PRESENT", "passed": True},
              {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": True},
              {"check": "RUNTIME_WITHIN_LIMITS", "passed": True},
              {"check": "BUNDLE_BINDS_RULE", "passed": stored.get("rule_version_id") == e.rule_version_id
               and stored.get("rule_ast_hash") == rv.ast_hash and stored.get("evaluator_build") == e.evaluator_build},
              {"check": "STORED_BUNDLE_HASH", "passed": digest(stored) == e.input_bundle_hash},
              {"check": "EVALUATOR_BUILD", "passed": e.evaluator_build == EVALUATOR_BUILD,
               "recorded": e.evaluator_build, "running": EVALUATOR_BUILD}]
    # The recorded inputs as this build would have frozen them: the record's own hash when it names this build, else the
    # stored bundle (whose integrity STORED_BUNDLE_HASH checks) re-hashed with this build's identifier.
    recorded_inputs = e.input_bundle_hash if stored.get("evaluator_build") == EVALUATOR_BUILD \
        else digest({**stored, "evaluator_build": EVALUATOR_BUILD})

    def classified(result: dict) -> dict:
        failed = [c["check"] for c in checks if not c["passed"]]
        if not failed:
            status = "REPRODUCED"
        elif "EVALUATOR_BUILD" in failed and set(failed) <= {"EVALUATOR_BUILD", "OUTCOME", "TRACE"}:
            status = "BUILD_CHANGED"
        else:
            status = "NOT_REPRODUCED"
        return {**result, "status": status, "checks": checks, "failed_checks": failed}

    try:
        out = appl.evaluate(ctx.db, tenant_id=e.tenant_id, subject=s, odef=od, effective_at=e.effective_at,
                            record_cutoff_seq=e.record_cutoff_seq, trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id,
                            rule_version=rv, persist=False)
    except HTTPException as x:
        if not isinstance(x.__cause__, rules.RuleError):
            raise
        # The evaluator refuses the stored inputs it was given (e.g. a non-finite DECIMAL stored before fact writes
        # refused it): this runtime cannot reproduce the recorded outcome. NOT_REPRODUCED with the evaluator's code, or
        # BUILD_CHANGED when another build recorded it from the same intact inputs; the stored record stays readable and
        # nothing is substituted.
        checks.append({"check": "REDERIVED_BUNDLE", "passed": getattr(x, "input_bundle_hash", None) == recorded_inputs})
        checks.append({"check": "OUTCOME", "passed": False, "error": x.__cause__.code, "node_id": x.__cause__.node_id})
        return classified({"reproduced_outcome": None, "matches": False, "error": x.__cause__.code})
    checks += [{"check": "REDERIVED_BUNDLE", "passed": out["input_bundle_hash"] == recorded_inputs},
               {"check": "OUTCOME", "passed": out["outcome"] == e.outcome},
               {"check": "TRACE", "passed": digest(out["trace"]) == digest(e.trace)}]
    return classified({"reproduced_outcome": out["outcome"],
                       "matches": out["outcome"] == e.outcome and out["input_bundle_hash"] == e.input_bundle_hash,
                       "input_bundle_hash": out["input_bundle_hash"]})


@router.get("/applicability/determinations")
def determinations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    rows = ctx.db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == ctx.tenant,
                                                                   ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()
    out = []
    for r in rows:
        d = to_dict(r)
        s = ctx.db.get(Subject, r.subject_id)
        od = ctx.db.get(ObligationDefinition, r.obligation_definition_id)
        d["subject"] = s.display_name if s else None
        d["obligation_definition"] = od.key if od else None
        d["obligation_title"] = od.title if od else None
        # A reference that cannot be found is named, never a failure of the whole register (no foreign key covers it).
        d["missing_references"] = [k for k, v in (("subject_id", s), ("obligation_definition_id", od)) if v is None]
        out.append(d)
    return out


class AttestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    decision: str  # ADOPT | REJECT
    authority_basis: str = Field(max_length=200)  # column size (API-REQ-033)
    reason: str | None = None
    # The exact determination revision reviewed and the evaluation it rests on (APP §12 "exact determination revision";
    # APP §16 "stale/superseded revision cannot be silently attested"). Optional, so existing callers keep working;
    # when sent, a decision on any other revision or evaluation is refused with 409.
    revision_id: str | None = Field(default=None, max_length=36)
    evaluation_id: str | None = Field(default=None, max_length=36)


# ZA-API-001 §08: an optional Idempotency-Key on the two determination actions (engines/idempotency.py).
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/applicability/determinations/{lineage_id}/attest")
def attest(lineage_id: str, body: AttestIn, ctx: Ctx = Depends(authorize("attest", human=True)),
           idempotency_key: IdempotencyKey = None):
    if body.decision not in ("ADOPT", "REJECT"):
        raise HTTPException(422, "decision must be ADOPT or REJECT")
    r = appl.attest(ctx.db, ctx=ctx, determination_lineage_id=lineage_id, decision=body.decision,
                    authority_basis=body.authority_basis, reason=body.reason, expected_revision_id=body.revision_id,
                    expected_evaluation_id=body.evaluation_id, idempotency_key=idempotency_key)
    ctx.db.commit()
    out = {"determination": to_dict(r["determination"]), "attestation": to_dict(r["attestation"]),
           "obligation": to_dict(r["obligation"]) if r["obligation"] else None}
    return appl.as_committed(out) if r.get("replayed") else out


class ChallengeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    challenge_class: str  # one of CHALLENGE_DISPOSITIONS: FACT_WRONG, FACT_MISSING, RULE_WRONG, INTERPRETATION, DECLINE
    detail: str


# APP §09 "Challenge types", one code each (the five rows of that table; the codes are this API's names for them).
CHALLENGE_DISPOSITIONS = {
    "FACT_WRONG": "Record a corrected FactAssertion; a new evaluation will be created.",
    "FACT_MISSING": "Resolve the linked FactRequest.",
    "RULE_WRONG": "Routed to regulatory-content operations; released rule unchanged until corrected via governance.",
    "INTERPRETATION": "Requires governed legal disposition; evaluation remains unchanged.",
    "DECLINE": "Customer declines to adopt: an authorised attestor records REJECTED with a reason; the evaluation is "
               "not changed.",
}


CHALLENGE_OPERATION = "POST /applicability/determinations/{lineage_id}/challenge"


@router.post("/applicability/determinations/{lineage_id}/challenge")
def challenge(lineage_id: str, body: ChallengeIn, ctx: Ctx = Depends(authorize("challenge")),
              idempotency_key: IdempotencyKey = None):
    """Challenge creates a governed record; never rewrites the evaluation (FR-APP-005, APP-REQ-011). A PROPOSED
    determination moves to IN_REVIEW (GLS: PROPOSED "May move to IN_REVIEW"); in any other state the challenge is
    recorded without a state change. Serialised with evaluation and attestation of the same scope. Without an
    Idempotency-Key every request is a new challenge; with one, a retry returns the challenge it recorded (API-001 §08)."""
    request = {"determination_lineage_id": lineage_id, "challenge_class": body.challenge_class,
               "detail": body.detail.strip()}
    done = idempotency.replay(ctx.db, tenant_id=ctx.tenant, operation=CHALLENGE_OPERATION, key=idempotency_key,
                              request=request)
    if done is not None:
        prior = ctx.db.get(DeterminationChallenge, done)
        if prior is not None and prior.tenant_id == ctx.tenant:
            return to_dict(prior)
    if body.challenge_class not in CHALLENGE_DISPOSITIONS:
        raise HTTPException(422, {"error": "CHALLENGE_CLASS_INVALID", "allowed": sorted(CHALLENGE_DISPOSITIONS)})
    detail = body.detail.strip()
    if not detail:
        raise HTTPException(422, {"error": "CHALLENGE_DETAIL_REQUIRED", "message": "Describe the challenge."})

    def current():
        return ctx.db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == ctx.tenant, ApplicabilityDetermination.lineage_id == lineage_id,
            ApplicabilityDetermination.record_to_seq.is_(None)).execution_options(populate_existing=True)).scalars().first()
    cur = current()
    if not cur:
        raise HTTPException(404)
    appl.lock_decision(ctx.db, ctx.tenant, cur.subject_id, cur.obligation_definition_id)
    cur = current()  # read again under the lock: the revision challenged is the one current now
    if not cur:
        raise HTTPException(404)
    seq = next_record_seq(ctx.db)
    c = DeterminationChallenge(tenant_id=ctx.tenant, determination_lineage_id=lineage_id, evaluation_id=cur.evaluation_id,
                               challenge_class=body.challenge_class, detail=detail, actor_id=ctx.actor.actor_id,
                               disposition=CHALLENGE_DISPOSITIONS[body.challenge_class])
    ctx.db.add(c)
    ctx.db.flush()
    idempotency.remember(ctx.db, tenant_id=ctx.tenant, operation=CHALLENGE_OPERATION, key=idempotency_key,
                         request=request, result_ref=c.challenge_id)
    new_state = cur.state
    if cur.state == "PROPOSED":
        appl._new_det_revision(ctx.db, cur, tenant_id=ctx.tenant, subject_id=cur.subject_id, odef_id=cur.obligation_definition_id,
                               evaluation=ctx.db.get(ApplicabilityEvaluation, cur.evaluation_id), state="IN_REVIEW",
                               seq=seq, actor_id=ctx.actor.actor_id, basis="Challenged: " + body.challenge_class)
        new_state = "IN_REVIEW"
    # APP §14: "Every human adoption/challenge ... is separate from operational telemetry and retained under audit policy".
    audit(ctx.db, action="determination.challenge", object_type="ApplicabilityDetermination", object_id=lineage_id,
          actor=ctx.actor, tenant_id=ctx.tenant, prior=cur.state, new=new_state, record_seq=seq,
          reason=f"{body.challenge_class} (challenge {c.challenge_id})")
    # APP §12 minimum payload: "challenge ID, determination/evaluation IDs, challenge class, actor".
    emit(ctx.db, event_type="determination.challenged", aggregate_type="ApplicabilityDetermination", aggregate_id=lineage_id,
         tenant_id=ctx.tenant, actor=ctx.actor_ref(), record_seq=seq,
         payload={"challenge_id": c.challenge_id, "challenge_class": body.challenge_class,
                  "determination_revision_id": cur.revision_id, "evaluation_id": cur.evaluation_id})
    ctx.db.commit()
    return to_dict(c)


@router.get("/fact-requests")
def fact_requests(ctx: Ctx = Depends(authorize(*READ))):
    out = []
    for fr in ctx.db.execute(select(FactRequest).where(FactRequest.tenant_id == ctx.tenant)
                             .order_by(FactRequest.created_at.desc())).scalars():
        d = to_dict(fr)
        fd = ctx.db.get(FactDefinition, fr.fact_definition_id)
        d["fact_key"], d["fact_label"], d["data_type"], d["enum_domain"] = fd.key, fd.label, fd.data_type, fd.enum_domain
        d["manual_attestation_policy"] = fd.manual_attestation_policy
        d["subject"] = ctx.db.get(Subject, fr.subject_id).display_name
        out.append(d)
    return out


@router.get("/history/compare")
def compare(subject_id: str, obligation_definition_id: str, valid_at: Instant,
            left_seq: int = Query(ge=0), right_seq: int = Query(ge=0),
            ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SUBJECT))):
    """Same valid time, two record cutoffs: AS_KNOWN_THEN vs RECONSTRUCTED_NOW (ZA-TIM-001 §12).

    Each side is answered as known at its record cutoff and echoes it (TIM-REQ-017), so a cutoff that has not been
    reached yet is refused (422 RECORD_SEQ_IN_FUTURE, as every other historical read): answering it with today's
    records would label a later answer with a sequence nothing was recorded at. An unknown definition is 404."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, obligation_definition_id)
    if od is None:
        raise HTTPException(404, "obligation definition")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if max(left_seq, right_seq) > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"left_seq/right_seq is beyond the current record sequence {now_seq}"})
    sides = {}
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        for name, seq in (("left", left_seq), ("right", right_seq)):
            try:
                r = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=valid_at,
                                  record_cutoff_seq=seq, trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id, persist=False)
                sides[name] = {"known_seq": seq, **r}
            except HTTPException as e:
                sides[name] = {"known_seq": seq, "error": e.detail}
    diffs = []
    if "rule_version_id" in sides["left"] and "rule_version_id" in sides["right"]:
        if sides["left"]["rule_version_id"] != sides["right"]["rule_version_id"]:
            diffs.append({"type": "LEGAL_VERSION_CHANGED", "from": sides["left"]["rule_version_id"], "to": sides["right"]["rule_version_id"]})
        lf = _fact_ids(sides["left"]["trace"])
        rf = _fact_ids(sides["right"]["trace"])
        for k in sorted(set(lf) | set(rf)):
            if lf.get(k) != rf.get(k):
                diffs.append({"type": "KNOWLEDGE_ADDED" if lf.get(k) in (None, "UNKNOWN") else "KNOWLEDGE_CORRECTED",
                              "fact": k, "left": lf.get(k), "right": rf.get(k)})
        if not diffs:
            diffs.append({"type": "NO_SEMANTIC_CHANGE"})
    return {"valid_at": valid_at, "left": sides["left"], "right": sides["right"], "differences": diffs}


def _fact_ids(trace, out=None):
    out = {} if out is None else out
    if trace.get("node_type") == "FACT_REF":
        out[trace["fact"]] = trace.get("fact_revision_id") or "UNKNOWN"
    for c in trace.get("children", []) or []:
        _fact_ids(c, out)
    return out
