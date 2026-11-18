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
    # Lock the exception row, then decide on the state read under the lock (populate_existing: the caller's earlier
    # db.get may have cached a stale copy). Two concurrent decisions serialise here: the second one sees the first
    # one's committed state and is refused as an invalid transition (409) instead of also succeeding. No lock is held
    # before this one; the record-sequence row is locked after it (next_record_seq), so the order is always the same.
    x = db.execute(select(ExceptionRecord).where(ExceptionRecord.exception_id == exception_id,
                                                 ExceptionRecord.tenant_id == tenant_id)
                   .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if not x:
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
        .order_by(SatisfactionExpressionVersion.version.desc(),
                  SatisfactionExpressionVersion.expression_version_id)).scalars().first()


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
    from .satisfaction import hash_intact
    if cur is None:
        out.append((409, "NO_EXPRESSION_TO_COMPENSATE", "the obligation has no SatisfactionExpressionVersion"))
    elif not hash_intact(cur):
        # Approval copies the current AST into version N+1 under a freshly computed hash; an AST altered in place after
        # release would be re-released as if it had been reviewed (STA-REQ-019/021; STA §19 "Released/adopted version
        # immutable"). The evaluator refuses such a version (SATISFACTION_EXPRESSION_HASH_MISMATCH); so does this.
        out.append((409, "SATISFACTION_EXPRESSION_HASH_MISMATCH",
                    f"the obligation's current SatisfactionExpressionVersion (version {cur.version}) no longer matches "
                    "its recorded hash"))
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


# Issue types whose closure STA §17 lets this module verify: they are raised from an AssuranceSnapshot (engines/assurance.py
# _issue_hooks), and "the resulting new ... AssuranceSnapshot provides the actual updated state".
SNAPSHOT_VERIFIED_ISSUE_TYPES = frozenset({"OBLIGATION_FAILING", "PROOF_GAP", "ASSURANCE_REVIEW"})
# Control-level issues (raise_control_issue): verified by "the resulting new ControlEvaluation" (STA §17).
CONTROL_ISSUE_TYPES = {"FAILING": "CONTROL_FAILING", "REQUIRES_REVIEW": "CONTROL_REVIEW"}
EVALUATION_VERIFIED_ISSUE_TYPES = frozenset(CONTROL_ISSUE_TYPES.values())
# Issue types whose closure criteria are owned or decided elsewhere; their closure is unchanged here:
#   INTEGRITY_INCIDENT  - evidence/ledger integrity (engines/evidence.py; evidence & security ownership)
#   DETERMINATION_REVIEW - applicability re-review; its resolution path is the open R-02 decision (an adopted
#                          determination cannot currently be re-attested after a changed evaluation).
UNVERIFIED_CLOSURE_ISSUE_TYPES = frozenset({"INTEGRITY_INCIDENT", "DETERMINATION_REVIEW"})
TERMINAL_ISSUE_STATES = frozenset({"CLOSED", "CANCELLED"})


def lock_issue_raising(db: Session, tenant_id: str) -> None:
    """The organisation's issue-raising lock: held by everything that decides whether an active issue covers a new
    condition, and by everything that can end an issue.

    Holders: raise_control_issue (control-level, per new FAILING / REQUIRES_REVIEW ControlEvaluation),
    assurance.compute_snapshot for a persisted snapshot that raises an obligation-level issue (_issue_hooks), and
    close_issue / transition_issue to CLOSED or CANCELLED. STA §17 "Create/link issue unless an existing open issue
    already covers ...": without the lock a closure or cancellation committing concurrently could leave the new
    condition linked to an issue that is no longer active, covered by no active issue at all. Always taken first,
    before any issue row lock and before the record-sequence row (one global lock order). The key name predates
    obligation-level use and is kept so that every holder shares one lock."""
    from .locks import xact_lock
    xact_lock(db, "control-issue", tenant_id)


def _lock_issue(db: Session, tenant_id: str, issue_id: str) -> Issue:
    """Row-lock the issue for this transaction and read its committed state, so concurrent transitions, assignments
    and closures of one issue serialise and none is lost (a waiting request sees the other's result)."""
    i = db.execute(select(Issue).where(Issue.issue_id == issue_id).with_for_update()
                   .execution_options(populate_existing=True)).scalars().first()
    if not i or i.tenant_id != tenant_id:
        raise HTTPException(404, "Issue not found")
    return i


def transition_issue(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor: Any,
    issue_id: str,
    to_state: str,
    reason: str | None = None,
    owner_actor_id: str | None = None,
    _verified_closure: bool = False,
) -> tuple[Issue, IssueEvent]:
    if to_state in TERMINAL_ISSUE_STATES:
        lock_issue_raising(db, tenant_id)
    i = _lock_issue(db, tenant_id, issue_id)
    if to_state not in ISSUE_FLOW.get(i.state, set()):
        raise HTTPException(409, f"Invalid transition {i.state} -> {to_state}")
    if to_state == "CANCELLED" and not reason:
        raise HTTPException(422, "Cancellation reason required")
    # ZA-GLS-001 §07: TRIAGED = "Ownership, severity and treatment path are established".
    if to_state == "TRIAGED" and not (owner_actor_id or i.owner_actor_id):
        raise HTTPException(409, {"error": "ISSUE_OWNER_REQUIRED",
                                  "message": "Assign an accountable owner before triage (POST /issues/{id}/assign)."})
    # ZA-STA-001 §17 / STA-REQ-023: closure needs remediation plus a new test/evidence cycle and the resulting new
    # AssuranceSnapshot; ZA-GLS-001 CLOSED = "Verification confirms closure criteria were met".
    if to_state == "CLOSED" and not _verified_closure and i.issue_type not in UNVERIFIED_CLOSURE_ISSUE_TYPES:
        raise HTTPException(409, {"error": "VERIFICATION_REQUIRED",
                                  "message": "Close with POST /issues/{id}/close and the verifying AssuranceSnapshot."})
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


def assign_issue(db: Session, *, tenant_id: str, actor: Any, issue_id: str, owner_actor_id: str,
                 reason: str) -> tuple[Issue, IssueEvent]:
    """Record the accountable owner of an open issue (STA §17 "owner"; GLS TRIAGED "Ownership ... established").

    The owner is a human principal of the same organisation. The change is an ASSIGNED event (append-only, 0008),
    the projection's owner column and an audit record, in one transaction. Re-assigning the same owner is refused,
    so a retried request records nothing twice. No due date or escalation is set: ZA-WRK-001 §06 requires a versioned
    deadline policy/calendar reference, which does not exist yet. No outbox event: ZA-EVT-001 catalogues none."""
    from ..models import Actor
    i = _lock_issue(db, tenant_id, issue_id)
    if i.state in TERMINAL_ISSUE_STATES:
        raise HTTPException(409, {"error": "ISSUE_TERMINAL", "message": f"A {i.state} issue cannot be reassigned."})
    owner = db.get(Actor, owner_actor_id)
    if owner is None or owner.tenant_id != tenant_id:  # another organisation's principal reads as unknown
        raise HTTPException(422, {"error": "OWNER_NOT_FOUND", "message": "The owner is not a principal of this organisation."})
    if owner.actor_type != "HUMAN":
        raise HTTPException(422, {"error": "OWNER_NOT_HUMAN",
                                  "message": "An accountable issue owner is a human principal."})
    if i.owner_actor_id == owner_actor_id:
        raise HTTPException(409, {"error": "ISSUE_OWNER_UNCHANGED", "message": "The issue already has this owner."})
    prior_owner = i.owner_actor_id
    i.owner_actor_id = owner_actor_id
    seq = next_record_seq(db)
    actor_id = getattr(actor, "actor_id", actor)
    ev = IssueEvent(issue_id=i.issue_id, tenant_id=tenant_id, event_type="ASSIGNED", from_state=i.state,
                    to_state=i.state, actor_id=actor_id, reason=reason, owner_actor_id=owner_actor_id, record_seq=seq)
    db.add(ev)
    audit(db, action="issue.assign", object_type="Issue", object_id=issue_id, actor=actor, tenant_id=tenant_id,
          prior=prior_owner, new=owner_actor_id, reason=reason, record_seq=seq)
    return i, ev


def _remediation_started_seq(db: Session, issue: Issue) -> int | None:
    """record_seq of the event that opened the current remediation cycle: the latest event reaching IN_REMEDIATION.
    A migration-0008 baseline event in IN_REMEDIATION counts too: it is the recorded start for an issue that was
    already in remediation, so a verifying retest must still come after it."""
    return db.execute(select(IssueEvent.record_seq).where(
        IssueEvent.tenant_id == issue.tenant_id, IssueEvent.issue_id == issue.issue_id,
        IssueEvent.to_state == "IN_REMEDIATION")
        .order_by(IssueEvent.record_seq.desc()).limit(1)).scalar()


def closure_problems(db: Session, issue: Issue, snapshot_id: str) -> list[tuple[int, str, str]]:
    """Why ``snapshot_id`` does not verify closure of ``issue`` (empty: it does), as (status, code, message).

    ZA-STA-001 §17 closure rule: "Closure requires remediation plus a new test/evidence cycle ... The resulting new
    ... AssuranceSnapshot provides the actual updated state"; STA-REQ-023; ZA-GLS-001 CLOSED "Verification confirms
    closure criteria were met". The verifying snapshot must:
      1. belong to this organisation and the same obligation as the snapshot that raised the issue;
      2. know about the remediation (record cutoff at or after the start of the current remediation cycle);
      3. depend on a TestRun or evidence recorded after remediation started (the new test/evidence cycle);
      4. show the obligation PROVEN (proof now exists; a FAILING, REQUIRES_REVIEW or NOT_PROVEN result does not
         confirm that the condition was remediated)."""
    from ..models import AssuranceSnapshot, EvidenceArtifact, TestRun
    out: list[tuple[int, str, str]] = []
    source = db.get(AssuranceSnapshot, issue.source_ref_id) if issue.source_ref_type == "AssuranceSnapshot" else None
    if source is None or source.tenant_id != issue.tenant_id:
        return [(409, "ISSUE_SOURCE_UNRESOLVABLE", "The snapshot that raised this issue cannot be found.")]
    snap = db.get(AssuranceSnapshot, snapshot_id)
    if snap is None or snap.tenant_id != issue.tenant_id:
        return [(404, "VERIFICATION_NOT_FOUND", "Verification snapshot not found.")]
    if snap.obligation_lineage_id != source.obligation_lineage_id:
        out.append((422, "VERIFICATION_SCOPE_MISMATCH", "The verification snapshot is for a different obligation."))
    started = _remediation_started_seq(db, issue)
    if started is None:
        return out + [(409, "REMEDIATION_NOT_RECORDED", "No remediation cycle is recorded for this issue.")]
    if snap.record_cutoff_seq < started:
        out.append((409, "VERIFICATION_PREDATES_REMEDIATION",
                    "The verification snapshot was computed from knowledge recorded before remediation started."))
    deps = snap.dependency_manifest or {}
    run_ids, evidence_ids = list(deps.get("test_runs") or []), list(deps.get("evidence") or [])
    newer_runs = db.execute(select(TestRun.test_run_id).where(
        TestRun.tenant_id == issue.tenant_id, TestRun.test_run_id.in_(run_ids or ["-"]),
        TestRun.record_seq > started)).scalars().all()
    newer_evidence = db.execute(select(EvidenceArtifact.evidence_id).where(
        EvidenceArtifact.tenant_id == issue.tenant_id, EvidenceArtifact.evidence_id.in_(evidence_ids or ["-"]),
        EvidenceArtifact.record_seq > started)).scalars().all()
    if not newer_runs and not newer_evidence:
        out.append((409, "RETEST_REQUIRED", "The verification snapshot uses no TestRun or evidence recorded after "
                                            "remediation started; a new test/evidence cycle is required."))
    if snap.state != "PROVEN":
        out.append((409, "VERIFICATION_NOT_PROVEN", f"The verification snapshot is {snap.state}, not PROVEN."))
    return out


def _new_inputs_after(db: Session, tenant_id: str, run_ids, evidence_ids, started: int) -> bool:
    from ..models import EvidenceArtifact, TestRun
    runs = db.execute(select(TestRun.test_run_id).where(
        TestRun.tenant_id == tenant_id, TestRun.test_run_id.in_(list(run_ids) or ["-"]),
        TestRun.record_seq > started)).first()
    evidence = db.execute(select(EvidenceArtifact.evidence_id).where(
        EvidenceArtifact.tenant_id == tenant_id, EvidenceArtifact.evidence_id.in_(list(evidence_ids) or ["-"]),
        EvidenceArtifact.record_seq > started)).first()
    return runs is not None or evidence is not None


def control_closure_problems(db: Session, issue: Issue, evaluation_id: str) -> list[tuple[int, str, str]]:
    """Why ``evaluation_id`` does not verify closure of a control-level ``issue`` (empty: it does).

    STA §17: "Closure requires remediation plus a new test/evidence cycle ... The resulting new ControlEvaluation ...
    provides the actual updated state"; STA-REQ-023; GLS CLOSED. The verifying ControlEvaluation must be of the same
    control implementation (control + scope) in this organisation, computed at a record cutoff at or after the start of
    the current remediation cycle, from a TestRun or evidence recorded after remediation started, and OPERATING
    (STA §06: DEGRADED and NOT_EVALUATED are not affirmative proof; FAILING and REQUIRES_REVIEW are not remediated)."""
    from ..models import ControlEvaluation
    source = db.get(ControlEvaluation, issue.source_ref_id) if issue.source_ref_type == "ControlEvaluation" else None
    if source is None or source.tenant_id != issue.tenant_id:
        return [(409, "ISSUE_SOURCE_UNRESOLVABLE", "The control evaluation that raised this issue cannot be found.")]
    ce = db.get(ControlEvaluation, evaluation_id)
    if ce is None or ce.tenant_id != issue.tenant_id:
        return [(404, "VERIFICATION_NOT_FOUND", "Verification control evaluation not found.")]
    out: list[tuple[int, str, str]] = []
    if ce.control_implementation_id != source.control_implementation_id:
        out.append((422, "VERIFICATION_SCOPE_MISMATCH", "The verification evaluation is of a different control."))
    started = _remediation_started_seq(db, issue)
    if started is None:
        return out + [(409, "REMEDIATION_NOT_RECORDED", "No remediation cycle is recorded for this issue.")]
    if ce.record_cutoff_seq < started:
        out.append((409, "VERIFICATION_PREDATES_REMEDIATION",
                    "The verification evaluation was computed from knowledge recorded before remediation started."))
    deps = (ce.inputs or {}).get("deps") or {}
    if not _new_inputs_after(db, issue.tenant_id, deps.get("runs") or [], deps.get("evidence") or [], started):
        out.append((409, "RETEST_REQUIRED", "The verification evaluation uses no TestRun or evidence recorded after "
                                            "remediation started; a new test/evidence cycle is required."))
    if ce.state != "OPERATING":
        out.append((409, "VERIFICATION_NOT_OPERATING", f"The verification evaluation is {ce.state}, not OPERATING."))
    return out


def control_issue_identity(ci, evaluation) -> tuple[str | None, str | None]:
    """(issue type, dedupe key) of the control-level condition an evaluation records, or (None, None) when its state
    raises no control issue. The identity raise_control_issue deduplicates on: issue type + control implementation +
    subject + the evaluation's own reason codes as the root cause."""
    import hashlib
    kind = CONTROL_ISSUE_TYPES.get(evaluation.state)
    if kind is None:
        return None, None
    causes = sorted(set(evaluation.reason_codes or []))
    cause_id = hashlib.sha256("|".join(causes).encode()).hexdigest()[:16]
    return kind, f"{kind}:{ci.control_implementation_id}:{ci.subject_id}:{cause_id}"


def raise_control_issue(db: Session, *, ci, evaluation) -> Issue | None:
    """Create or link the issue for a newly stored ControlEvaluation (STA §17):

      FAILING          "Create/link issue unless an existing open issue already covers exact control/scope/root cause."
      REQUIRES_REVIEW  "Create review issue/task with conflicting source references and owner."

    Any other state raises nothing here (NOT_EVALUATED: STA §17 requires a source/cadence policy that does not exist).

    Identity of the condition (the dedupe key): issue type + control implementation (the control and its governed scope;
    ControlImplementation is "unique per control + governed scope + implementation variant", DOM §7.1) + subject + the
    evaluation's own controlled reason codes, as recorded, as the root cause. No new classification is applied.
    An active (not CLOSED / CANCELLED) issue with the same identity is linked: an audit record names the new evaluation,
    and no second issue is created. Runs in the evaluation's transaction, so the issue exists exactly when the
    evaluation is committed; serialised per organisation (engines/locks.py), so concurrent evaluations of one condition
    never create two active issues. The issue references the immutable ControlEvaluation, which holds the conflicting
    source/evidence references and the full trace. Severity and priority keep the model defaults: no control-issue
    severity policy is specified."""
    import hashlib

    from ..models import Actor, ControlDefinition
    from .locks import xact_lock
    kind, key = control_issue_identity(ci, evaluation)
    if kind is None:
        return None
    causes = sorted(set(evaluation.reason_codes or []))
    # One lock per organisation, taken before any record-sequence write, so every path acquires the same locks in the
    # same order (several control issues raised by one snapshot cannot deadlock with another snapshot).
    xact_lock(db, "control-issue", ci.tenant_id)
    active = db.execute(select(Issue).where(Issue.tenant_id == ci.tenant_id, Issue.dedupe_key == key,
                                            Issue.state.notin_(sorted(TERMINAL_ISSUE_STATES)))
                        .order_by(Issue.created_at, Issue.issue_id)).scalars().first()
    if active is not None:
        audit(db, action="issue.evaluation_linked", object_type="Issue", object_id=active.issue_id, actor="system",
              tenant_id=ci.tenant_id, new=active.state, record_seq=next_record_seq(db),
              reason=f"ControlEvaluation {evaluation.evaluation_id} ({evaluation.state}; {', '.join(causes)}) is "
                     f"covered by this open issue")
        return active
    cd = db.get(ControlDefinition, ci.control_definition_id)
    owner = None
    if kind == "CONTROL_REVIEW":  # STA §17: review issue "with ... owner": the control implementation's owner
        a = db.get(Actor, ci.owner_actor_id) if ci.owner_actor_id else None
        owner = a.actor_id if a is not None and a.tenant_id == ci.tenant_id and a.actor_type == "HUMAN" else None
    title = f"{'Control FAILING' if kind == 'CONTROL_FAILING' else 'Control requires review'}: {cd.title if cd else ci.control_definition_id}"
    iss = Issue(tenant_id=ci.tenant_id, title=title[:300], issue_type=kind, source_ref_type="ControlEvaluation",
                source_ref_id=evaluation.evaluation_id, dedupe_key=key, owner_actor_id=owner)
    db.add(iss)
    db.flush()
    refs = ""
    if kind == "CONTROL_REVIEW":
        conflicting = sorted({e["evidence_id"] for t in (evaluation.inputs or {}).get("tests", [])
                              for e in t.get("evidence", []) if e.get("condition") == "CONFLICTING"})
        refs = f"; conflicting evidence {conflicting}" if conflicting else ""
    ev = record_issue_created(db, issue=iss, actor_id="system",
                              reason=(f"ControlEvaluation {evaluation.evaluation_id} {evaluation.state} for control "
                                      f"implementation {ci.control_implementation_id}; reason codes {causes}{refs}")[:2000])
    emit(db, event_type="issue.created", aggregate_type="Issue", aggregate_id=iss.issue_id, tenant_id=ci.tenant_id,
         record_seq=ev.record_seq, payload={"issue_id": iss.issue_id, "issue_type": kind,
                                            "control_implementation_id": ci.control_implementation_id,
                                            "control_evaluation_id": evaluation.evaluation_id})
    return iss


def close_issue(db: Session, *, tenant_id: str, actor: Any, issue_id: str, verification_snapshot_id: str | None = None,
                reason: str, verification_control_evaluation_id: str | None = None) -> tuple[Issue, IssueEvent]:
    """PENDING_VERIFICATION -> CLOSED, verified by the result STA §17 names for the issue's level: a new
    AssuranceSnapshot for obligation issues (closure_problems), a new ControlEvaluation for control issues
    (control_closure_problems). Closure changes the issue only: no control, obligation, snapshot, test or evidence
    record is written (STA-REQ-023). The verifying result is recorded in the event, the audit record and the catalogued
    ``issue.closed`` outbox event."""
    lock_issue_raising(db, tenant_id)
    i = _lock_issue(db, tenant_id, issue_id)
    if i.issue_type in SNAPSHOT_VERIFIED_ISSUE_TYPES:
        kind, ref = "AssuranceSnapshot", verification_snapshot_id
    elif i.issue_type in EVALUATION_VERIFIED_ISSUE_TYPES:
        kind, ref = "ControlEvaluation", verification_control_evaluation_id
    else:
        raise HTTPException(409, {"error": "VERIFICATION_NOT_DEFINED",
                                  "message": f"Verified closure is not defined for {i.issue_type} issues."})
    if not ref:
        raise HTTPException(422, {"error": "VERIFICATION_KIND_MISMATCH",
                                  "message": f"{i.issue_type} issues are verified by a {kind}."})
    if i.state != "PENDING_VERIFICATION":
        raise HTTPException(409, f"Invalid transition {i.state} -> CLOSED")
    problems = closure_problems(db, i, ref) if kind == "AssuranceSnapshot" else control_closure_problems(db, i, ref)
    if problems:
        status = 404 if any(p[0] == 404 for p in problems) else 422 if any(p[0] == 422 for p in problems) else 409
        raise HTTPException(status, {"error": problems[0][1], "message": "; ".join(p[2] for p in problems),
                                     "problems": [{"code": c, "message": m} for _, c, m in problems]})
    actor_id = getattr(actor, "actor_id", actor)
    i, ev = transition_issue(db, tenant_id=tenant_id, actor_id=actor_id, actor=actor, issue_id=issue_id,
                             to_state="CLOSED", reason=f"{reason} [verified by {kind} {ref}]", _verified_closure=True)
    key = "verification_snapshot_id" if kind == "AssuranceSnapshot" else "verification_control_evaluation_id"
    emit(db, event_type="issue.closed", aggregate_type="Issue", aggregate_id=issue_id, tenant_id=tenant_id,
         record_seq=ev.record_seq, payload={"issue_id": issue_id, "issue_type": i.issue_type, key: ref})
    return i, ev


ISSUE_TRANSITIONS = frozenset((f, t) for f, ts in ISSUE_FLOW.items() for t in ts)


def issue_states_at(db: Session, tenant_id: str, issues, cutoff: int) -> dict[str, dict]:
    """Each issue's workflow state and owner as known at record sequence ``cutoff``, from assurance_issue_event only
    (append-only, migration 0008), never from the assurance_issue projection, which is updated in place.

    TIM-REQ-006/015 (a historical answer uses the record cutoff, no "latest" joins), STA-REQ-001 (issue state is its own
    dimension), ZA-GLS-001 §07 workflow. Returns {issue_id: {"known", "state", "owner_actor_id", "source", "problem",
    "event_ids", "created_seq"}}:
      * ``known``: whether the issue had been recorded by the cutoff. True when its CREATED event, or any event other
        than the migration baseline, is at or before the cutoff; False when its first such event is after it; None when
        that cannot be shown (no events, or only the migration-0008 BASELINE, which 0008 stamped record_seq 0 with the
        state at migration time, so it places neither the issue's creation nor its state at any cutoff).
      * ``state`` / ``owner_actor_id``: from the last event recorded by the cutoff, or None with ``problem``:
        ISSUE_HISTORY_MISSING (known is None), ISSUE_HISTORY_INCOMPLETE (only the baseline is known at the cutoff), or
        ISSUE_HISTORY_INCONSISTENT (ambiguous order, a first event that is not CREATED/BASELINE, a broken from_state
        chain, a transition the workflow does not allow, or a projection that disagrees with a fully known history).
    Nothing is written; the current projection is never substituted for a missing history."""
    ids = [i.issue_id for i in issues]
    events = db.execute(select(IssueEvent).where(IssueEvent.tenant_id == tenant_id, IssueEvent.issue_id.in_(ids or ["-"]))
                        .order_by(IssueEvent.record_seq, IssueEvent.event_id)).scalars().all()
    out: dict[str, dict] = {}
    for i in issues:
        own = [e for e in events if e.issue_id == i.issue_id]
        recorded = [e for e in own if e.event_type != MIGRATION_BASELINE_EVENT]
        known = [e for e in own if e.record_seq <= cutoff]
        res = {"known": None, "state": None, "owner_actor_id": None, "source": None, "problem": None,
               "event_ids": [e.event_id for e in known],
               "created_seq": next((e.record_seq for e in own if e.event_type == "CREATED"), None)}
        out[i.issue_id] = res
        if not own:
            res["problem"] = "ISSUE_HISTORY_MISSING"
            continue
        if recorded and recorded[0].record_seq <= cutoff:
            res["known"] = True
        elif own[0].event_type != MIGRATION_BASELINE_EVENT:
            res["known"] = False  # its history starts after the cutoff: not yet recorded then
            continue
        else:  # only the migration baseline is at or before the cutoff: neither existence nor state is placed there
            res["problem"] = "ISSUE_HISTORY_INCOMPLETE"
            continue
        seqs = [e.record_seq for e in own]
        ok = len(seqs) == len(set(seqs)) and own[0].from_state is None and \
            own[0].event_type in ("CREATED", MIGRATION_BASELINE_EVENT) and \
            all(e.event_type not in ("CREATED", MIGRATION_BASELINE_EVENT) for e in own[1:])
        for prev, e in zip(own, own[1:]):
            if e.event_type == "ASSIGNED":
                ok = ok and e.from_state == e.to_state == prev.to_state
            else:
                ok = ok and e.from_state == prev.to_state and (e.from_state, e.to_state) in ISSUE_TRANSITIONS \
                    and e.event_type == e.to_state
        if ok and own[-1].to_state != i.state:
            ok = False  # the full history is recorded, yet the projection says otherwise
        if not ok:
            res["problem"] = "ISSUE_HISTORY_INCONSISTENT"
            continue
        res["state"], res["owner_actor_id"], res["source"] = known[-1].to_state, known[-1].owner_actor_id, "EVENT_HISTORY"
    return out


def constituent_control_issues(db: Session, tenant_id: str, issue: Issue, *, snapshot_seq: int | None,
                               as_of: int | None = None) -> dict | None:
    """The control-level issues behind an obligation-level issue (STA §17 "Obligation FAILING: ... link constituent
    control issues"), derived read-only from records that already exist; nothing is stored or changed.

    No relationship table or link event exists (DAT §11 assurance.issue: "source evaluation refs"), so the relationship
    is reconstructed from immutable references, by the rules the writers already apply:
      1. the obligation issue's source AssuranceSnapshot (raised by assurance._issue_hooks);
      2. each FAILING / REQUIRES_REVIEW leaf of its segments names the exact ControlEvaluation it used;
      3. that evaluation's condition identity (control_issue_identity: the key raise_control_issue deduplicates on);
      4. the one control issue with that identity that was recorded and active (not CLOSED / CANCELLED) at
         ``snapshot_seq``, the record sequence at which the snapshot was created (issue_states_at). raise_control_issue
         ran in the snapshot's own transaction, before that sequence, so this is the issue it created or linked.
    Every input predates the obligation issue, so the relationship is the same at every later cutoff; with ``as_of``
    only each control issue's state is the one recorded by then. Closing or cancelling either issue changes neither the
    relationship nor the other issue. Only this organisation's records are read; a link that cannot be established is
    reported in ``problems`` (catalog._problem classes), never guessed.

    Returns None for an issue that is not raised from an obligation snapshot."""
    from ..models import AssuranceSnapshot, ControlEvaluation, ControlImplementation
    from .catalog import _problem
    if issue.issue_type not in SNAPSHOT_VERIFIED_ISSUE_TYPES:
        return None
    out = {"basis": "DERIVED", "snapshot_id": None, "snapshot_record_seq": snapshot_seq, "control_issues": [],
           "problems": []}
    snap = db.get(AssuranceSnapshot, issue.source_ref_id) if issue.source_ref_type == "AssuranceSnapshot" else None
    if snap is None or snap.tenant_id != tenant_id:
        out["problems"].append(_problem("SOURCE_SNAPSHOT_NOT_FOUND", "INCOMPLETE", snapshot_id=issue.source_ref_id))
        return out
    out["snapshot_id"] = snap.snapshot_id
    leaves: dict[str, dict] = {}
    for seg in snap.segments or []:
        for leaf in seg.get("leaf_evaluations") or []:
            if leaf.get("control_state") in CONTROL_ISSUE_TYPES and leaf.get("control_evaluation_id"):
                leaves.setdefault(leaf["control_evaluation_id"], leaf)
    if leaves and snapshot_seq is None:
        out["problems"].append(_problem("SNAPSHOT_RECORD_SEQ_UNKNOWN", "INCOMPLETE", snapshot_id=snap.snapshot_id))
        return out
    for evaluation_id, leaf in leaves.items():
        ce = db.get(ControlEvaluation, evaluation_id)
        ci = db.get(ControlImplementation, ce.control_implementation_id) if ce is not None else None
        if ce is None or ce.tenant_id != tenant_id or ci is None or ci.tenant_id != tenant_id:
            out["problems"].append(_problem("CONTROL_EVALUATION_NOT_FOUND", "INCOMPLETE", control_evaluation_id=evaluation_id))
            continue
        if ce.state != leaf["control_state"] or ci.control_implementation_id != leaf.get("control_implementation_id"):
            out["problems"].append(_problem("CONTROL_EVALUATION_MISMATCH", "MISMATCH", control_evaluation_id=evaluation_id))
            continue
        if ci.entity_id != snap.entity_id:
            out["problems"].append(_problem("CONTROL_SCOPE_MISMATCH", "MISMATCH", control_evaluation_id=evaluation_id,
                                            control_implementation_id=ci.control_implementation_id))
            continue
        kind, key = control_issue_identity(ci, ce)
        candidates = db.execute(select(Issue).where(Issue.tenant_id == tenant_id, Issue.dedupe_key == key)
                                .order_by(Issue.created_at, Issue.issue_id)).scalars().all()
        then = issue_states_at(db, tenant_id, candidates, snapshot_seq)
        active = [c for c in candidates if then[c.issue_id]["known"] and then[c.issue_id]["state"] is not None
                  and then[c.issue_id]["state"] not in TERMINAL_ISSUE_STATES]
        if len(active) != 1:
            out["problems"].append(_problem("CONTROL_ISSUE_AMBIGUOUS" if active else "CONTROL_ISSUE_NOT_FOUND",
                                            "MISMATCH" if active else "INCOMPLETE", control_evaluation_id=evaluation_id))
            continue
        linked = active[0]
        entry = {"control_implementation_id": ci.control_implementation_id, "control_evaluation_id": evaluation_id,
                 "control_state": ce.state, "control_issue_id": linked.issue_id, "issue_type": kind,
                 "state": linked.state, "history_problem": None}
        if as_of is not None:
            h = issue_states_at(db, tenant_id, [linked], as_of)[linked.issue_id]
            entry["state"], entry["history_problem"] = h["state"], h["problem"]
        out["control_issues"].append(entry)
    return out


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
# The event_type migration 0008 gave its backfilled events. Read-only marker: no application code writes it
# (tests/test_governance_projections.py); readers such as assurance.exception_states_at refer to it by this name.
MIGRATION_BASELINE_EVENT = "BASELINE"


def event_view(e) -> dict:
    """One governance event as the history endpoints return it, with its provenance stated.

    BASELINE events were not observed: migration 0008 created them from each record's state at migration time. Their
    actor_id copies the record's own actor field (requester, approver, owner or placer) or names the migration, and
    their record_seq and timestamps are copied from the record or set at migration time. They are labelled so that
    nobody reads them as an action by that person at that time (DAT-REQ-024). The rows themselves are append-only and
    are not rewritten; any correction of them needs an approved forward correction."""
    from ..platform import to_dict
    d = to_dict(e)
    if d.get("event_type") == MIGRATION_BASELINE_EVENT:
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
# Waiver, extension and a governed dispute workflow (STA §13 "WAIVER / EXTENSION: Governed valid-time object ...;
# never an ad hoc due-date edit"; "disputed waiver" -> REQUIRES_REVIEW) are not implemented: no table, lifecycle,
# approval authority or event contract for them exists in ZA-DAT-001, ZA-GLS-001, ZA-IAM-001 or ZA-EVT-001, and the
# WRK §07 deadline-policy inputs (rule/calendar versions) do not exist. Scheduling and fulfilment are the only occurrence
# mutations; due dates are never edited.

def _current_obligation(db: Session, tenant_id: str, lineage_id: str):
    """The current revision of one of this organisation's obligations (404 otherwise, for another organisation's too).
    Two current revisions are refused, never one chosen by row order (selection.AmbiguousSelection)."""
    from ..models import ObligationInstance
    from .selection import one_or_none
    obl = one_or_none(db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant_id,
                                                                  ObligationInstance.lineage_id == lineage_id,
                                                                  ObligationInstance.record_to_seq.is_(None))).scalars(),
                      record_type="ObligationInstance", selection="obligation_current", id_attr="revision_id")
    if obl is None:
        raise HTTPException(404, "obligation not found")
    return obl


def schedule_occurrence(
    db: Session,
    tenant_id: str,
    actor_id: str,
    obligation_lineage_id: str,
    window_from: datetime,
    due_at: datetime,
) -> tuple[ObligationOccurrence, ObligationOccurrenceEvent]:
    """Record one required occurrence (STA §13 REQUIRED_WINDOW) of an obligation of this organisation.

    Refused before anything is written:
      * 422 OCCURRENCE_WINDOW_EMPTY: the required window [window_from, due_at) is empty (TIM-REQ-002: canonical
        half-open, non-empty ranges);
      * 404: no obligation with this lineage in this organisation;
      * 409 OBLIGATION_CLOSED: the obligation's current revision is CLOSED and the occurrence would fall due at or after
        the end of its validity (DOM §6.3 CLOSED: "Duty ended and no future occurrence remains"). An occurrence that
        fell due while the duty applied can still be recorded."""
    if ensure_utc(due_at) <= ensure_utc(window_from):
        raise HTTPException(422, {"error": "OCCURRENCE_WINDOW_EMPTY",
                                  "message": "due_at must be later than window_from (a non-empty required window)"})
    obl = _current_obligation(db, tenant_id, obligation_lineage_id)
    if obl.lifecycle == "CLOSED" and obl.valid_to is not None and ensure_utc(due_at) >= ensure_utc(obl.valid_to):
        raise HTTPException(409, {"error": "OBLIGATION_CLOSED",
                                  "message": "The obligation is closed; no occurrence can fall due after "
                                             f"{ensure_utc(obl.valid_to).isoformat()}."})
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
    # Lock, then re-read (as decide_exception): a concurrent second fulfilment waits for the first to commit, sees it
    # fulfilled and is refused with 409, rather than passing the check on a stale read and then failing on the
    # lifecycle_final trigger (migration 0010) with a raw database error.
    o = db.execute(select(ObligationOccurrence).where(ObligationOccurrence.occurrence_id == occurrence_id,
                                                      ObligationOccurrence.tenant_id == tenant_id)
                   .with_for_update().execution_options(populate_existing=True)).scalars().first()
    if not o:
        raise HTTPException(404, "Occurrence not found")
    if o.fulfilled_at:
        raise HTTPException(409, "Occurrence already fulfilled; history is immutable")
    # A fulfilment is a completion event with its proof (STA §13 FULFILLMENT_EVENT "completion event with evidence";
    # ZA-EVT-001 obligation.occurrence_completed "recorded with evidence refs"; DAT §07 "fulfillment refs"), and that
    # proof belongs to the obligation's legal entity: cross-entity proof needs a governed shared-evidence reference
    # (TEN-REQ-008, TEN §14), as for test results (test_results._validate). Checked before anything is written.
    if not evidence_ids:
        raise HTTPException(422, {"error": "FULFILMENT_EVIDENCE_REQUIRED",
                                  "message": "A fulfilment needs at least one evidence reference (ZA-STA-001 §13)."})
    if len(set(evidence_ids)) != len(evidence_ids):
        raise HTTPException(422, {"error": "DUPLICATE_EVIDENCE", "message": "an evidence reference is listed more than once"})
    entity_id = _current_obligation(db, tenant_id, o.obligation_lineage_id).entity_id
    for e in evidence_ids:
        ev_art = db.get(EvidenceArtifact, e)
        if not ev_art or ev_art.tenant_id != tenant_id:
            raise HTTPException(404, f"Evidence {e} not found")
        if ev_art.entity_id != entity_id:
            raise HTTPException(422, {"error": "EVIDENCE_SCOPE_MISMATCH", "evidence_id": e,
                                      "message": "Evidence of another legal entity cannot fulfil this obligation: "
                                                 "cross-entity proof needs a governed shared-evidence reference "
                                                 "(ZA-TEN-001 §14)."})
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
