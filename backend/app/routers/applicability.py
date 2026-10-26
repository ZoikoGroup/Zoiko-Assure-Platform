"""Facts, applicability evaluations, determinations, challenges and fact requests (ZA-APP-001)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..engines import applicability as appl
from ..engines.facts import assert_fact, assertion_value, resolve_fact
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, DeterminationChallenge,
                      FactAssertion, FactConflict, FactDefinition, FactRequest, ObligationDefinition, RuleVersion, Subject)
from ..platform import current_watermark, emit, ensure_utc, next_record_seq, to_dict
from ..security import Ctx, get_ctx

router = APIRouter()


def _fact_row(db, a: FactAssertion, fd: FactDefinition) -> dict:
    d = to_dict(a)
    d["value"] = assertion_value(a) if fd.sensitivity not in ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED") else "••• (redacted)"
    d["fact_key"] = fd.key
    d["fact_label"] = fd.label
    return d


@router.get("/subjects/{subject_id}/facts")
def fact_profile(subject_id: str, valid_at: datetime | None = None, known_seq: int | None = None, ctx: Ctx = Depends(get_ctx)):
    """Fact profile at an explicit bitemporal coordinate (CURRENT if both omitted; echoed back)."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    wm = current_watermark(ctx.db)
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
def fact_history(subject_id: str, ctx: Ctx = Depends(get_ctx)):
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
    valid_from: datetime
    valid_to: datetime | None = None
    source_ref: str | None = None
    change_kind: str = "CREATE"
    reason: str | None = None


@router.post("/subjects/{subject_id}/facts")
def assert_fact_ep(subject_id: str, body: FactIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_facts")
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
    created = assert_fact(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, value=body.value, valid_from=body.valid_from,
                          valid_to=body.valid_to, provenance_class=provenance, source_ref=body.source_ref,
                          actor_id=ctx.actor.actor_id, change_kind=body.change_kind, reason=body.reason)
    reeval = appl.reevaluate_for_fact(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd, actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return {"revisions": [_fact_row(ctx.db, a, fd) for a in created], "impact_reevaluations": reeval}


class EvalIn(BaseModel):
    subject_id: str
    obligation_definition_id: str
    effective_at: datetime | None = None
    record_cutoff_seq: int | None = None
    trigger: str = "EXPLICIT_REQUEST"


def _eval_view(db, e: ApplicabilityEvaluation) -> dict:
    d = to_dict(e)
    od = db.get(ObligationDefinition, e.obligation_definition_id)
    s = db.get(Subject, e.subject_id)
    rv = db.get(RuleVersion, e.rule_version_id)
    d["obligation_definition"] = {"key": od.key, "title": od.title, "material": od.material}
    d["subject"] = {"display_name": s.display_name, "subject_type": s.subject_type}
    d["rule_version"] = {"version": rv.version, "ast_hash": rv.ast_hash, "content_release_id": rv.content_release_id,
                         "source_mappings": rv.source_mappings}
    det = appl.current_determination(db, e.tenant_id, e.subject_id, e.obligation_definition_id)
    d["current_determination"] = to_dict(det) if det else None
    return d


@router.post("/applicability/evaluations")
def request_evaluation(body: EvalIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("request_evaluation")
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, body.obligation_definition_id)
    if not od:
        raise HTTPException(404, "obligation definition")
    ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od,
                       effective_at=body.effective_at or datetime.now(timezone.utc),
                       record_cutoff_seq=body.record_cutoff_seq, trigger=body.trigger, actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return _eval_view(ctx.db, ev)


@router.post("/applicability/evaluate-all")
def evaluate_all(subject_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require("request_evaluation")
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    out = []
    for od in ctx.db.execute(select(ObligationDefinition).where(ObligationDefinition.subject_type == s.subject_type)).scalars():
        try:
            ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=datetime.now(timezone.utc),
                               record_cutoff_seq=None, trigger="EXPLICIT_REQUEST", actor_id=ctx.actor.actor_id)
            out.append({"obligation": od.key, "outcome": ev.outcome, "evaluation_id": ev.evaluation_id})
        except HTTPException as e:
            out.append({"obligation": od.key, "error": e.detail})
    ctx.db.commit()
    return out


@router.get("/applicability/evaluations")
def list_evaluations(subject_id: str | None = None, ctx: Ctx = Depends(get_ctx)):
    q = select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.tenant_id == ctx.tenant)
    if subject_id:
        q = q.where(ApplicabilityEvaluation.subject_id == subject_id)
    return [_eval_view(ctx.db, e) for e in ctx.db.execute(q.order_by(ApplicabilityEvaluation.record_seq.desc()).limit(200)).scalars()]


@router.get("/applicability/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, ctx: Ctx = Depends(get_ctx)):
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
    d["replay"] = _replay(ctx, e)
    return d


def _replay(ctx: Ctx, e: ApplicabilityEvaluation) -> dict:
    """Deterministic reproducibility check: re-run the frozen bundle (TIM-REQ-012)."""
    s = ctx.db.get(Subject, e.subject_id)
    od = ctx.db.get(ObligationDefinition, e.obligation_definition_id)
    rv = ctx.db.get(RuleVersion, e.rule_version_id)
    out = appl.evaluate(ctx.db, tenant_id=e.tenant_id, subject=s, odef=od, effective_at=e.effective_at,
                        record_cutoff_seq=e.record_cutoff_seq, trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id,
                        rule_version=rv, persist=False)
    return {"reproduced_outcome": out["outcome"], "matches": out["outcome"] == e.outcome and out["input_bundle_hash"] == e.input_bundle_hash,
            "input_bundle_hash": out["input_bundle_hash"]}


@router.get("/applicability/determinations")
def determinations(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == ctx.tenant,
                                                                   ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()
    out = []
    for r in rows:
        d = to_dict(r)
        d["subject"] = ctx.db.get(Subject, r.subject_id).display_name
        od = ctx.db.get(ObligationDefinition, r.obligation_definition_id)
        d["obligation_definition"] = od.key
        d["obligation_title"] = od.title
        out.append(d)
    return out


class AttestIn(BaseModel):
    decision: str  # ADOPT | REJECT
    authority_basis: str
    reason: str | None = None


@router.post("/applicability/determinations/{lineage_id}/attest")
def attest(lineage_id: str, body: AttestIn, ctx: Ctx = Depends(get_ctx)):
    if body.decision not in ("ADOPT", "REJECT"):
        raise HTTPException(422, "decision must be ADOPT or REJECT")
    r = appl.attest(ctx.db, ctx=ctx, determination_lineage_id=lineage_id, decision=body.decision,
                    authority_basis=body.authority_basis, reason=body.reason)
    ctx.db.commit()
    return {"determination": to_dict(r["determination"]), "attestation": to_dict(r["attestation"]),
            "obligation": to_dict(r["obligation"]) if r["obligation"] else None}


class ChallengeIn(BaseModel):
    challenge_class: str  # FACT_WRONG, FACT_MISSING, RULE_WRONG, INTERPRETATION
    detail: str


@router.post("/applicability/determinations/{lineage_id}/challenge")
def challenge(lineage_id: str, body: ChallengeIn, ctx: Ctx = Depends(get_ctx)):
    """Challenge creates a governed record; never rewrites the evaluation (FR-APP-005)."""
    ctx.require("challenge")
    cur = ctx.db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == ctx.tenant,
                                                                  ApplicabilityDetermination.lineage_id == lineage_id,
                                                                  ApplicabilityDetermination.record_to_seq.is_(None))).scalars().first()
    if not cur:
        raise HTTPException(404)
    c = DeterminationChallenge(tenant_id=ctx.tenant, determination_lineage_id=lineage_id, evaluation_id=cur.evaluation_id,
                               challenge_class=body.challenge_class, detail=body.detail, actor_id=ctx.actor.actor_id,
                               disposition={"RULE_WRONG": "Routed to regulatory-content operations; released rule unchanged until corrected via governance.",
                                            "FACT_WRONG": "Record a corrected FactAssertion; a new evaluation will be created.",
                                            "FACT_MISSING": "Resolve the linked FactRequest.",
                                            "INTERPRETATION": "Requires governed legal disposition; evaluation remains unchanged."}.get(body.challenge_class))
    ctx.db.add(c)
    if cur.state in ("PROPOSED",):
        appl._new_det_revision(ctx.db, cur, tenant_id=ctx.tenant, subject_id=cur.subject_id, odef_id=cur.obligation_definition_id,
                               evaluation=ctx.db.get(ApplicabilityEvaluation, cur.evaluation_id), state="IN_REVIEW",
                               seq=next_record_seq(ctx.db), actor_id=ctx.actor.actor_id,
                               basis="Challenged: " + body.challenge_class)
    emit(ctx.db, event_type="determination.challenged", aggregate_type="ApplicabilityDetermination", aggregate_id=lineage_id,
         tenant_id=ctx.tenant, actor=ctx.actor_ref(), payload={"challenge_class": body.challenge_class})
    ctx.db.commit()
    return to_dict(c)


@router.get("/fact-requests")
def fact_requests(ctx: Ctx = Depends(get_ctx)):
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
def compare(subject_id: str, obligation_definition_id: str, valid_at: datetime, left_seq: int, right_seq: int,
            ctx: Ctx = Depends(get_ctx)):
    """Same valid time, two record cutoffs: AS_KNOWN_THEN vs RECONSTRUCTED_NOW (ZA-TIM-001 §12)."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, obligation_definition_id)
    sides = {}
    for name, seq in (("left", left_seq), ("right", right_seq)):
        try:
            r = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=valid_at, record_cutoff_seq=seq,
                              trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id, persist=False)
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
