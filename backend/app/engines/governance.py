"""Governance event and transition lifecycle engine (Roadmap Stage 3c; audit I-04; DAT-REQ-009/037, TIM-REQ-007).

Preserves durable, append-only, auditable governance history for mutable governance records:
- Exceptions (assurance_exception_event)
- Compensations (assurance_compensation_event)
- Issues (assurance_issue_event)
- Legal holds (evidence_legal_hold_event)
- Evidence conflicts (evidence_conflict_event)
- Obligation occurrences (assurance_obligation_occurrence_event)
- Agent authority grants (ai_authority_grant_event; migration 0011)
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuthorityGrant,
    AuthorityGrantEvent,
    Compensation,
    CompensationEvent,
    EvidenceArtifact,
    EvidenceConflict,
    EvidenceConflictEvent,
    ExceptionEvent,
    ExceptionRecord,
    Issue,
    IssueEvent,
    LegalHold,
    LegalHoldEvent,
    ObligationOccurrence,
    ObligationOccurrenceEvent,
    SatisfactionExpressionVersion,
)
from ..platform import audit, digest, emit, ensure_utc, next_record_seq


# ==============================================================================
# 1. Exceptions (assurance_exception_event)
# ==============================================================================

def request_exception(
    db: Session,
    tenant_id: str,
    actor_id: str,
    target_type: str,
    target_id: str,
    reason: str,
    valid_from: datetime,
    valid_to: datetime,
) -> tuple[ExceptionRecord, ExceptionEvent]:
    seq = next_record_seq(db)
    x = ExceptionRecord(
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        state="PENDING_APPROVAL",
        requested_by=actor_id,
        valid_from=valid_from,
        valid_to=valid_to,
        record_seq=seq,
    )
    db.add(x)
    db.flush()
    ev = ExceptionEvent(
        exception_id=x.exception_id,
        tenant_id=tenant_id,
        event_type="REQUESTED",
        from_state=None,
        to_state="PENDING_APPROVAL",
        actor_id=actor_id,
        reason=reason,
        target_type=target_type,
        target_id=target_id,
        valid_from=valid_from,
        valid_to=valid_to,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="exception.request", object_type="Exception", object_id=x.exception_id,
          actor=actor_id, tenant_id=tenant_id, new="PENDING_APPROVAL", reason=reason, record_seq=seq)
    emit(db, event_type="exception.requested", aggregate_type="Exception", aggregate_id=x.exception_id,
         tenant_id=tenant_id, payload={"target_type": target_type, "target_id": target_id})
    return x, ev


def decide_exception(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    exception_id: str,
    decision: str,
    reason: str | None = None,
) -> tuple[ExceptionRecord, ExceptionEvent]:
    x = db.get(ExceptionRecord, exception_id)
    if not x or x.tenant_id != tenant_id:
        raise HTTPException(404, "Exception not found")
    if x.requested_by == actor_id and decision == "APPROVE":
        raise HTTPException(403, "Requester cannot approve own exception")
    prior = x.state
    nxt = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REVOKE": "REVOKED"}.get(decision)
    if not nxt or (nxt in ("APPROVED", "REJECTED") and prior != "PENDING_APPROVAL") or (nxt == "REVOKED" and prior != "APPROVED"):
        raise HTTPException(409, f"Invalid transition {prior} -> {decision}")

    seq = next_record_seq(db)
    x.state = nxt
    if nxt == "APPROVED":
        x.approved_by = actor_id

    ev = ExceptionEvent(
        exception_id=x.exception_id,
        tenant_id=tenant_id,
        event_type=nxt,
        from_state=prior,
        to_state=nxt,
        actor_id=actor_id,
        reason=reason,
        target_type=x.target_type,
        target_id=x.target_id,
        valid_from=x.valid_from,
        valid_to=x.valid_to,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="exception.decide", object_type="Exception", object_id=exception_id,
          actor=actor, tenant_id=tenant_id, prior=prior, new=nxt, reason=reason, record_seq=seq)
    emit(db, event_type=f"exception.{nxt.lower()}", aggregate_type="Exception", aggregate_id=exception_id,
         tenant_id=tenant_id, record_seq=seq, payload={"target": x.target_id, "note": "no assurance-state mutation"})
    return x, ev


# ==============================================================================
# 2. Compensations (assurance_compensation_event)
# ==============================================================================

def propose_compensation(
    db: Session,
    tenant_id: str,
    actor_id: str,
    obligation_lineage_id: str,
    primary_control_key: str,
    compensating_expression: dict,
    equivalence_rationale: str,
    valid_from: datetime,
    valid_to: datetime | None = None,
) -> tuple[Compensation, CompensationEvent]:
    problems = compensation_problems(db, tenant_id=tenant_id, obligation_lineage_id=obligation_lineage_id,
                                     primary_control_key=primary_control_key,
                                     compensating_expression=compensating_expression, valid_from=valid_from,
                                     valid_to=valid_to)
    if len((equivalence_rationale or "").strip()) < 20:  # STA §09: "source/control rationale required"
        problems.insert(0, (422, "EQUIVALENCE_RATIONALE_REQUIRED", "an equivalence rationale is required"))
    _refuse(problems)
    seq = next_record_seq(db)
    c = Compensation(
        tenant_id=tenant_id,
        obligation_lineage_id=obligation_lineage_id,
        primary_control_key=primary_control_key,
        compensating_expression=compensating_expression,
        equivalence_rationale=equivalence_rationale,
        status="PENDING_APPROVAL",
        valid_from=valid_from,
        valid_to=valid_to,
        record_seq=seq,
    )
    db.add(c)
    db.flush()
    ev = CompensationEvent(
        compensation_id=c.compensation_id,
        tenant_id=tenant_id,
        event_type="SUBMITTED",
        from_status=None,
        to_status="PENDING_APPROVAL",
        actor_id=actor_id,
        reason=equivalence_rationale,
        obligation_lineage_id=obligation_lineage_id,
        primary_control_key=primary_control_key,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="compensation.propose", object_type="Compensation", object_id=c.compensation_id,
          actor=actor_id, tenant_id=tenant_id, new="PENDING_APPROVAL", reason=equivalence_rationale, record_seq=seq)
    emit(db, event_type="compensation.submitted", aggregate_type="Compensation", aggregate_id=c.compensation_id,
         tenant_id=tenant_id, payload={"primary_control_key": primary_control_key})
    return c, ev


def _current_expression(db: Session, tenant_id: str, lineage_id: str) -> SatisfactionExpressionVersion | None:
    return db.execute(select(SatisfactionExpressionVersion).where(
        SatisfactionExpressionVersion.tenant_id == tenant_id,
        SatisfactionExpressionVersion.obligation_lineage_id == lineage_id)
        .order_by(SatisfactionExpressionVersion.version.desc())).scalars().first()


def _wrappable(node: dict, key: str) -> bool:
    """Whether ``_wrap_comp_node`` would find a CONTROL leaf for ``key`` (the same traversal: the root and ``children``).
    A leaf already inside a COMPENSATION branch is not re-wrapped."""
    if not isinstance(node, dict):
        return False
    if node.get("node_type") == "CONTROL" and node.get("control_key") == key:
        return True
    return any(_wrappable(c, key) for c in node.get("children") or [])


def _control_keys_of(node) -> list[str]:
    if not isinstance(node, dict):
        return []
    own = [node["control_key"]] if node.get("node_type") == "CONTROL" and node.get("control_key") else []
    return own + [k for c in node.get("children") or [] for k in _control_keys_of(c)]


def compensation_problems(db: Session, *, tenant_id: str, obligation_lineage_id: str, primary_control_key: str,
                          compensating_expression: dict, valid_from: datetime, valid_to: datetime | None
                          ) -> list[tuple[int, str, str]]:
    """Why a compensation cannot be proposed or approved (audit F-03), as (status, code, message) triples.

    ZA-STA-001 §09: compensating_expression is "One or more controls expressed through normal SatisfactionExpression
    nodes"; primary_scope is the "Exact control/obligation branch being compensated"; "compensation cannot apply outside
    approved interval". STA-REQ-013: explicitly modelled, scoped, bitemporal, independently evidenced. Checked at
    proposal and again at approval, so a record that predates these checks cannot be approved into an expression."""
    from ..models import ControlDefinition
    from .assurance import expression_problems
    out: list[tuple[int, str, str]] = []
    structure = expression_problems(compensating_expression)
    keys = _control_keys_of(compensating_expression)
    if structure or not keys:
        out.append((422, "COMPENSATING_EXPRESSION_INVALID",
                    "; ".join(structure) or "the compensating expression names no control (ZA-STA-001 §09)"))
    if len(set(keys)) != len(keys):
        out.append((422, "DUPLICATE_COMPENSATING_CONTROL", "a compensating control is named more than once"))
    if primary_control_key in keys:
        out.append((422, "PRIMARY_CONTROL_COMPENSATES_ITSELF", "the primary control cannot be its own compensation"))
    named = sorted({primary_control_key, *keys})
    known = {k for (k,) in db.execute(select(ControlDefinition.key).where(ControlDefinition.key.in_(named)))}
    if set(named) - known:
        out.append((422, "UNKNOWN_CONTROL_DEFINITION", f"unknown control definition key(s): {sorted(set(named) - known)}"))
    if valid_to is not None and ensure_utc(valid_to) <= ensure_utc(valid_from):
        out.append((422, "COMPENSATION_PERIOD_EMPTY", "valid_to must be later than valid_from (half-open, non-empty)"))
    cur = _current_expression(db, tenant_id, obligation_lineage_id)
    if cur is None:
        out.append((409, "NO_EXPRESSION_TO_COMPENSATE", "the obligation has no SatisfactionExpressionVersion"))
    elif not _wrappable(cur.ast, primary_control_key):
        out.append((409, "PRIMARY_CONTROL_NOT_IN_EXPRESSION",
                    f"{primary_control_key} is not a compensable CONTROL leaf of the obligation's current expression "
                    f"(version {cur.version})"))
    return out


def _refuse(problems: list[tuple[int, str, str]]) -> None:
    if problems:
        status = 422 if any(p[0] == 422 for p in problems) else 409
        raise HTTPException(status, {"error": problems[0][1], "message": "; ".join(p[2] for p in problems),
                                     "problems": [{"code": c, "message": m} for _, c, m in problems]})


def _wrap_comp_node(node: dict, key: str, comp: Compensation) -> dict:
    if node.get("node_type") == "CONTROL" and node.get("control_key") == key:
        return {"node_type": "COMPENSATION", "node_id": f"compensation_{comp.compensation_id[:8]}", "primary": node,
                "compensating": comp.compensating_expression, "compensation_id": comp.compensation_id}
    n = dict(node)
    if "children" in n:
        n["children"] = [_wrap_comp_node(c, key, comp) for c in n["children"]]
    return n


def decide_compensation(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    comp_id: str,
    decision: str,
    reason: str | None = None,
) -> tuple[Compensation, CompensationEvent]:
    c = db.get(Compensation, comp_id)
    if not c or c.tenant_id != tenant_id:
        raise HTTPException(404, "Compensation not found")
    # One decision at a time per obligation (engines/locks.py), then re-read: two approvals cannot both write expression
    # version N+1, and a decision that waited sees the other's committed status.
    from .locks import xact_lock
    xact_lock(db, "satisfaction-expression", tenant_id, c.obligation_lineage_id)
    c = db.execute(select(Compensation).where(Compensation.compensation_id == comp_id)
                   .execution_options(populate_existing=True)).scalars().first()
    prior = c.status
    new_ast = cur = None
    if decision == "APPROVE":
        if prior != "PENDING_APPROVAL":
            raise HTTPException(409, "not pending")
        # Re-validated at approval, before anything is written: a proposal recorded before these checks, or whose
        # primary branch has since left the expression, cannot be approved into a vacuous or unanchored branch.
        _refuse(compensation_problems(db, tenant_id=tenant_id, obligation_lineage_id=c.obligation_lineage_id,
                                      primary_control_key=c.primary_control_key,
                                      compensating_expression=c.compensating_expression, valid_from=c.valid_from,
                                      valid_to=c.valid_to))
        from .assurance import expression_problems
        cur = _current_expression(db, tenant_id, c.obligation_lineage_id)
        new_ast = _wrap_comp_node(copy.deepcopy(cur.ast), c.primary_control_key, c)
        problems = expression_problems(new_ast)
        if problems or new_ast == cur.ast:
            raise HTTPException(409, {"error": "COMPENSATED_EXPRESSION_INVALID",
                                      "message": "; ".join(problems) or "approval would not change the expression"})
    seq = next_record_seq(db)
    if decision == "APPROVE":
        c.status = "APPROVED"
        c.approved_by = actor_id
        if cur:
            db.add(SatisfactionExpressionVersion(
                tenant_id=tenant_id,
                obligation_lineage_id=c.obligation_lineage_id,
                version=cur.version + 1,
                ast=new_ast,
                ast_hash=digest(new_ast),
                rationale=f"Compensation {comp_id}: {c.equivalence_rationale}",
                valid_from=c.valid_from,
                valid_to=None,
                record_seq=seq,
            ))
        nxt = "APPROVED"
    elif decision in ("REVOKE", "REJECT"):
        if decision == "REVOKE" and prior != "APPROVED":
            raise HTTPException(409, "only approved compensations can be revoked")
        if decision == "REJECT" and prior != "PENDING_APPROVAL":
            raise HTTPException(409, "only pending compensations can be rejected")
        nxt = "REVOKED" if decision == "REVOKE" else "REJECTED"
        c.status = nxt
    else:
        raise HTTPException(422, "Invalid decision")

    ev = CompensationEvent(
        compensation_id=c.compensation_id,
        tenant_id=tenant_id,
        event_type=nxt,
        from_status=prior,
        to_status=nxt,
        actor_id=actor_id,
        reason=reason or c.equivalence_rationale,
        obligation_lineage_id=c.obligation_lineage_id,
        primary_control_key=c.primary_control_key,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="compensation.decide", object_type="Compensation", object_id=comp_id,
          actor=actor, tenant_id=tenant_id, prior=prior, new=nxt, reason=reason, record_seq=seq)
    emit(db, event_type=f"compensation.{nxt.lower()}", aggregate_type="Compensation", aggregate_id=comp_id,
         tenant_id=tenant_id, record_seq=seq, payload={"scope": c.primary_control_key})
    return c, ev


# ==============================================================================
# 3. Issues (assurance_issue_event)
# ==============================================================================

ISSUE_FLOW = {
    "OPEN": {"TRIAGED", "CANCELLED"},
    "TRIAGED": {"IN_REMEDIATION", "CANCELLED"},
    "IN_REMEDIATION": {"PENDING_VERIFICATION"},
    "PENDING_VERIFICATION": {"CLOSED", "IN_REMEDIATION"},
}

def record_issue_created(
    db: Session,
    issue: Issue,
    actor_id: str = "system",
    reason: str | None = None,
) -> IssueEvent:
    seq = next_record_seq(db)
    ev = IssueEvent(
        issue_id=issue.issue_id,
        tenant_id=issue.tenant_id,
        event_type="CREATED",
        from_state=None,
        to_state=issue.state,
        actor_id=actor_id,
        reason=reason or issue.title,
        owner_actor_id=issue.owner_actor_id,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="issue.create", object_type="Issue", object_id=issue.issue_id,
          actor=actor_id, tenant_id=issue.tenant_id, new=issue.state, reason=reason or issue.title, record_seq=seq)
    emit(db, event_type="assurance.issue_created", aggregate_type="Issue", aggregate_id=issue.issue_id,
         tenant_id=issue.tenant_id, payload={"title": issue.title, "severity": issue.severity})
    return ev


def transition_issue(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    issue_id: str,
    to_state: str,
    reason: str | None = None,
    owner_actor_id: str | None = None,
) -> tuple[Issue, IssueEvent]:
    i = db.get(Issue, issue_id)
    if not i or i.tenant_id != tenant_id:
        raise HTTPException(404, "Issue not found")
    if to_state not in ISSUE_FLOW.get(i.state, set()):
        raise HTTPException(409, f"Invalid transition {i.state} -> {to_state}")
    if to_state == "CANCELLED" and not reason:
        raise HTTPException(422, "Cancellation reason required")
    prior = i.state
    i.state = to_state
    if owner_actor_id is not None:
        i.owner_actor_id = owner_actor_id
    seq = next_record_seq(db)
    ev = IssueEvent(
        issue_id=i.issue_id,
        tenant_id=tenant_id,
        event_type=to_state,
        from_state=prior,
        to_state=to_state,
        actor_id=actor_id,
        reason=reason,
        owner_actor_id=i.owner_actor_id,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="issue.transition", object_type="Issue", object_id=issue_id,
          actor=actor, tenant_id=tenant_id, prior=prior, new=to_state, reason=reason, record_seq=seq)
    emit(db, event_type="assurance.issue_transitioned", aggregate_type="Issue", aggregate_id=issue_id,
         tenant_id=tenant_id, record_seq=seq, payload={"from_state": prior, "to_state": to_state})
    return i, ev


# ==============================================================================
# 4. Legal Holds (evidence_legal_hold_event)
# ==============================================================================

def place_legal_hold(
    db: Session,
    tenant_id: str,
    evidence_id: str,
    actor_id: str,
    authority: str,
    reason: str,
) -> tuple[LegalHold, LegalHoldEvent]:
    seq = next_record_seq(db)
    h = LegalHold(
        tenant_id=tenant_id,
        evidence_id=evidence_id,
        authority=authority,
        reason=reason,
        status="ACTIVE",
        placed_by=actor_id,
    )
    db.add(h)
    db.flush()
    ev = LegalHoldEvent(
        hold_id=h.hold_id,
        tenant_id=tenant_id,
        evidence_id=evidence_id,
        event_type="HOLD_PLACED",
        status="ACTIVE",
        authority=authority,
        actor_id=actor_id,
        reason=reason,
        record_seq=seq,
    )
    db.add(ev)
    # Security audit and outbox in the same transaction as the event and the projection (ZA-EVT-001 §18
    # evidence.hold_placed; IAM §21 / SEC-REQ-031: attributable, reasoned record of a legal-hold decision).
    audit(db, action="legal_hold.placed", object_type="LegalHold", object_id=h.hold_id, actor=actor_id,
          tenant_id=tenant_id, new="ACTIVE", reason=f"{authority}: {reason}", record_seq=seq)
    emit(db, event_type="evidence.hold_placed", aggregate_type="EvidenceArtifact", aggregate_id=evidence_id,
         tenant_id=tenant_id, record_seq=seq, payload={"hold_id": h.hold_id, "authority": authority})
    return h, ev


def release_legal_hold(
    db: Session,
    tenant_id: str,
    evidence_id: str,
    actor_id: str,
    reason: str,
) -> tuple[LegalHold, LegalHoldEvent]:
    h = db.execute(select(LegalHold).where(
        LegalHold.tenant_id == tenant_id,
        LegalHold.evidence_id == evidence_id,
        LegalHold.status == "ACTIVE",
    )).scalars().first()
    if not h:
        raise HTTPException(409, "No active hold")
    seq = next_record_seq(db)
    h.status = "RELEASED"
    h.released_at = datetime.now(timezone.utc)
    ev = LegalHoldEvent(
        hold_id=h.hold_id,
        tenant_id=tenant_id,
        evidence_id=evidence_id,
        event_type="HOLD_RELEASED",
        status="RELEASED",
        authority=h.authority,
        actor_id=actor_id,
        reason=reason,
        record_seq=seq,
    )
    db.add(ev)
    # ZA-EVT-001 privacy.legal_hold_released (SECURITY_RESTRICTED) and the security audit record, atomically with the
    # event and the projection; the payload carries identifiers only, never the evidence or the reason text.
    audit(db, action="legal_hold.released", object_type="LegalHold", object_id=h.hold_id, actor=actor_id,
          tenant_id=tenant_id, prior="ACTIVE", new="RELEASED", reason=reason, record_seq=seq)
    emit(db, event_type="privacy.legal_hold_released", aggregate_type="LegalHold", aggregate_id=h.hold_id,
         tenant_id=tenant_id, record_seq=seq, payload={"hold_id": h.hold_id, "evidence_id": evidence_id})
    return h, ev


# ==============================================================================
# Event provenance (history endpoints)
# ==============================================================================

MIGRATION_BASELINE_ACTOR = "system:migration:0008"


def event_view(e) -> dict:
    """One governance event as the history endpoints return it, with its provenance stated.

    BASELINE events were not observed: migration 0008 created them from each record's state at migration time. Their
    actor_id copies the record's own actor field (requester, approver, owner or placer) or names the migration, and
    their record_seq and timestamps are copied from the record or set at migration time. They are labelled so that
    nobody reads them as an action by that person at that time (DAT-REQ-024). The rows themselves are append-only and
    are not rewritten; any correction of them needs an approved forward correction."""
    from ..platform import to_dict
    d = to_dict(e)
    if d.get("event_type") == "BASELINE":
        d["provenance"] = {"kind": "MIGRATION_BASELINE", "recorded_by": MIGRATION_BASELINE_ACTOR,
                           "note": "Created by migration 0008 from the record's state at migration time; actor_id, "
                                   "record_seq and timestamps are copied from that record, not observed as an event."}
    else:
        d["provenance"] = {"kind": "RECORDED"}
    return d


# ==============================================================================
# 5. Evidence Conflicts (evidence_conflict_event)
# ==============================================================================

def open_evidence_conflict(
    db: Session,
    tenant_id: str,
    actor_id: str,
    member_evidence_ids: list[str],
    description: str,
) -> tuple[EvidenceConflict, EvidenceConflictEvent]:
    seq = next_record_seq(db)
    c = EvidenceConflict(
        tenant_id=tenant_id,
        member_evidence_ids=member_evidence_ids,
        description=description,
        status="OPEN",
    )
    db.add(c)
    db.flush()
    ev = EvidenceConflictEvent(
        conflict_id=c.conflict_id,
        tenant_id=tenant_id,
        event_type="OPENED",
        status="OPEN",
        actor_id=actor_id,
        reason=description,
        member_evidence_ids=member_evidence_ids,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="evidence_conflict.opened", object_type="EvidenceConflict", object_id=c.conflict_id,
          actor=actor_id, tenant_id=tenant_id, new="OPEN", reason=description, record_seq=seq)
    emit(db, event_type="evidence.conflict_opened", aggregate_type="EvidenceConflict", aggregate_id=c.conflict_id,
         tenant_id=tenant_id, payload={"member_evidence_ids": member_evidence_ids})
    return c, ev


def resolve_evidence_conflict(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    conflict_id: str,
    reason: str,
) -> tuple[EvidenceConflict, EvidenceConflictEvent]:
    c = db.get(EvidenceConflict, conflict_id)
    if not c or c.tenant_id != tenant_id:
        raise HTTPException(404, "Conflict not found")
    if c.status != "OPEN":
        raise HTTPException(409, f"Conflict is {c.status}; only an OPEN conflict can be resolved")
    seq = next_record_seq(db)
    prior = c.status
    c.status = "RESOLVED"
    c.disposition = reason
    ev = EvidenceConflictEvent(
        conflict_id=c.conflict_id,
        tenant_id=tenant_id,
        event_type="RESOLVED",
        status="RESOLVED",
        actor_id=actor_id,
        reason=reason,
        member_evidence_ids=c.member_evidence_ids,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="evidence_conflict.resolved", object_type="EvidenceConflict", object_id=conflict_id,
          actor=actor, tenant_id=tenant_id, prior=prior, new="RESOLVED", reason=reason, record_seq=seq)
    emit(db, event_type="evidence.conflict_resolved", aggregate_type="EvidenceConflict", aggregate_id=conflict_id,
         tenant_id=tenant_id, record_seq=seq, payload={"disposition": reason})
    return c, ev


# ==============================================================================
# 6. Obligation Occurrences (assurance_obligation_occurrence_event)
# ==============================================================================

def schedule_occurrence(
    db: Session,
    tenant_id: str,
    actor_id: str,
    obligation_lineage_id: str,
    window_from: datetime,
    due_at: datetime,
) -> tuple[ObligationOccurrence, ObligationOccurrenceEvent]:
    seq = next_record_seq(db)
    o = ObligationOccurrence(
        tenant_id=tenant_id,
        obligation_lineage_id=obligation_lineage_id,
        window_from=window_from,
        due_at=due_at,
        record_seq=seq,
    )
    db.add(o)
    db.flush()
    ev = ObligationOccurrenceEvent(
        occurrence_id=o.occurrence_id,
        tenant_id=tenant_id,
        obligation_lineage_id=obligation_lineage_id,
        event_type="SCHEDULED",
        actor_id=actor_id,
        window_from=window_from,
        due_at=due_at,
        fulfilled_at=None,
        evidence_ids=[],
        is_late=False,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="occurrence.schedule", object_type="ObligationOccurrence", object_id=o.occurrence_id,
          actor=actor_id, tenant_id=tenant_id, new="SCHEDULED", record_seq=seq)
    emit(db, event_type="obligation.occurrence_scheduled", aggregate_type="ObligationOccurrence", aggregate_id=o.occurrence_id,
         tenant_id=tenant_id, payload={"obligation_lineage_id": obligation_lineage_id})
    return o, ev


def fulfil_occurrence(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    occurrence_id: str,
    fulfilled_at: datetime,
    evidence_ids: list[str],
) -> tuple[ObligationOccurrence, ObligationOccurrenceEvent]:
    o = db.get(ObligationOccurrence, occurrence_id)
    if not o or o.tenant_id != tenant_id:
        raise HTTPException(404, "Occurrence not found")
    if o.fulfilled_at:
        raise HTTPException(409, "Occurrence already fulfilled; history is immutable")
    for e in evidence_ids:
        ev_art = db.get(EvidenceArtifact, e)
        if not ev_art or ev_art.tenant_id != tenant_id:
            raise HTTPException(404, f"Evidence {e} not found")
    seq = next_record_seq(db)
    is_late = ensure_utc(fulfilled_at) > ensure_utc(o.due_at)
    o.fulfilled_at = fulfilled_at
    o.evidence_ids = evidence_ids
    ev = ObligationOccurrenceEvent(
        occurrence_id=o.occurrence_id,
        tenant_id=tenant_id,
        obligation_lineage_id=o.obligation_lineage_id,
        event_type="FULFILLED",
        actor_id=actor_id,
        window_from=o.window_from,
        due_at=o.due_at,
        fulfilled_at=fulfilled_at,
        evidence_ids=evidence_ids,
        is_late=is_late,
        record_seq=seq,
    )
    db.add(ev)
    audit(db, action="occurrence.fulfil", object_type="ObligationOccurrence", object_id=occurrence_id,
          actor=actor, tenant_id=tenant_id, prior="UNFULFILLED", new="FULFILLED", reason="Fulfilled with evidence", record_seq=seq)
    emit(db, event_type="obligation.occurrence_fulfilled", aggregate_type="ObligationOccurrence",
         aggregate_id=occurrence_id, tenant_id=tenant_id, record_seq=seq, payload={"late": is_late})
    return o, ev



# ==============================================================================
# 7. Agent authority grants (ai_authority_grant_event; migration 0011)
# ==============================================================================
# IAM-INV-09 (grant changes versioned and auditable), IAM-REQ-030 (grant per action class + scope + expiry + level),
# IAM-REQ-015 / IAM-INV-07 (revocation stops new authorisation at once; REVOKED is final: IAM §05 "prior grants are not
# automatically restored"), IAM §21 (audit content), ZA-EVT-001 authority_grant.issued / authority_grant.revoked,
# DAT-REQ-014 (projection, event, audit and outbox in one transaction), EVT-REQ-025 (minimised outbox payload).

def _grant_audit_text(g: AuthorityGrant) -> str:
    """IAM §21 minimum content for authority_grant.issued / revoked: grant, action/authority, scope, expiry (the
    grantor or revoker is the audit record's actor)."""
    return (f"action_class={g.action_class}; authority={g.authority_level}; scope={g.scope}; "
            f"expires_at={ensure_utc(g.expires_at).isoformat()}")


def _grant_event(g: AuthorityGrant, *, event_type: str, from_status: str | None, actor_id: str, reason: str | None,
                 seq: int) -> AuthorityGrantEvent:
    return AuthorityGrantEvent(
        grant_id=g.grant_id, tenant_id=g.tenant_id, agent_subject_id=g.agent_subject_id, event_type=event_type,
        from_status=from_status, to_status=g.status, action_class=g.action_class, authority_level=g.authority_level,
        scope=g.scope, expires_at=g.expires_at, actor_id=actor_id, reason=reason, record_seq=seq)


def _grant_payload(g: AuthorityGrant) -> dict:
    # Identifiers and controlled values only: action_class and scope are free text in this version (EVT-REQ-025).
    return {"grant_id": g.grant_id, "agent_subject_id": g.agent_subject_id, "authority_level": g.authority_level,
            "expires_at": ensure_utc(g.expires_at).isoformat()}


def issue_authority_grant(
    db: Session,
    *,
    tenant_id: str,
    agent_subject_id: str,
    action_class: str,
    authority_level: str,
    scope: str,
    reversibility: str,
    expires_at: datetime,
    fail_mode: str,
    actor: Any,
    actor_ref: dict | None = None,
) -> tuple[AuthorityGrant, AuthorityGrantEvent]:
    """Issue one grant: the projection row, its ISSUED event, the security audit record and the outbox event, with one
    record sequence. The caller authorises (a HUMAN with grant_authority) and commits."""
    seq = next_record_seq(db)
    actor_id = getattr(actor, "actor_id", actor)
    g = AuthorityGrant(tenant_id=tenant_id, agent_subject_id=agent_subject_id, action_class=action_class,
                       authority_level=authority_level, scope=scope, reversibility=reversibility, expires_at=expires_at,
                       granted_by=actor_id, status="ACTIVE", fail_mode=fail_mode)
    db.add(g)
    db.flush()
    ev = _grant_event(g, event_type="ISSUED", from_status=None, actor_id=actor_id, reason=None, seq=seq)
    db.add(ev)
    audit(db, action="authority_grant.issued", object_type="AuthorityGrant", object_id=g.grant_id, actor=actor,
          tenant_id=tenant_id, new="ACTIVE", reason=_grant_audit_text(g), record_seq=seq)
    emit(db, event_type="authority_grant.issued", aggregate_type="AuthorityGrant", aggregate_id=g.grant_id,
         tenant_id=tenant_id, actor=actor_ref, record_seq=seq, payload=_grant_payload(g))
    return g, ev


def revoke_authority_grant(
    db: Session,
    *,
    tenant_id: str,
    grant_id: str,
    actor: Any,
    actor_ref: dict | None = None,
    reason: str | None = None,
) -> tuple[AuthorityGrant, AuthorityGrantEvent | None]:
    """Revoke a grant: ACTIVE -> REVOKED with its event, audit record and outbox event, in the caller's transaction.

    Idempotent: revoking a grant that is already REVOKED changes and records nothing and returns (grant, None), so a
    client retrying after an unknown outcome cannot create a second revocation. The row is locked first, so two
    concurrent revocations produce exactly one event. An expired grant that is still ACTIVE can be revoked."""
    g = db.execute(select(AuthorityGrant).where(AuthorityGrant.grant_id == grant_id, AuthorityGrant.tenant_id == tenant_id)
                   .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if g is None:
        raise HTTPException(404, "grant not found")
    if g.status == "REVOKED":
        return g, None
    if g.status != "ACTIVE":
        raise HTTPException(409, f"A grant in status {g.status} cannot be revoked")
    seq = next_record_seq(db)
    actor_id = getattr(actor, "actor_id", actor)
    reason = (reason or "").strip() or None
    g.status = "REVOKED"
    ev = _grant_event(g, event_type="REVOKED", from_status="ACTIVE", actor_id=actor_id, reason=reason, seq=seq)
    db.add(ev)
    audit(db, action="authority_grant.revoked", object_type="AuthorityGrant", object_id=g.grant_id, actor=actor,
          tenant_id=tenant_id, prior="ACTIVE", new="REVOKED",
          reason=_grant_audit_text(g) + (f"; reason={reason}" if reason else ""), record_seq=seq)
    emit(db, event_type="authority_grant.revoked", aggregate_type="AuthorityGrant", aggregate_id=g.grant_id,
         tenant_id=tenant_id, actor=actor_ref, record_seq=seq, payload=_grant_payload(g))
    return g, ev
