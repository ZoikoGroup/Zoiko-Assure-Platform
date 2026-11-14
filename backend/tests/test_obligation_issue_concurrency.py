"""Obligation-level issue raising (engines/assurance.py _issue_hooks, from a persisted AssuranceSnapshot) under
concurrency, and its interaction with assignment, verified closure and cancellation (engines/governance.py).

Requirements exercised: ZA-STA-001 §17 ("Obligation NOT_PROVEN: Create proof-gap work item"; issues are created or
linked unless an open one already covers the condition; closure "requires remediation plus a new test/evidence cycle
... The resulting new ... AssuranceSnapshot provides the actual updated state"), STA-REQ-023; ZA-GLS-001 §07 issue
workflow (no new states: a closed issue is never reopened, a new condition is a new issue); ZA-TIM-001 TIM-REQ-005
(monotonic record sequence) and TIM-REQ-020 (revision and outbox event in one transaction); DAT-REQ-014.

Lock order (engines/locks.py; every scope lock before the first record-sequence allocation): the organisation's
issue-raising lock -> issue row -> record-sequence row. Every test builds its own organisation records.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA = f"tenant-oic-{TAG}"
ENT, SUBJ = f"entity-oic-{TAG}", f"subj-oic-{TAG}"
OWNER, CCO = f"oic-owner-{TAG}", f"oic-cco-{TAG}"
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV, DEC = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11, 12))


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=ENT, tenant_id=TA, legal_name="OIC AG", country="DE", home_cell_id=CELL_ID,
                           subject_id=SUBJ))
        db.add(Subject(subject_id=SUBJ, tenant_id=TA, entity_id=ENT, subject_type="LEGAL_ENTITY", display_name="OIC AG"))
        for aid, roles in ((OWNER, ["CONTROL_OWNER"]), (CCO, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=TA, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _subject():
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _obligation(subject, definition="DORA_ICT_RISK_FRAMEWORK"):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": KEY}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == definition)).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle="ADOPTED", valid_from=T0,
                                  record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _control(client, subject):
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": KEY, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == r.json()["control_implementation_id"])).scalar_one()
        return db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                          ).scalar_one().test_definition_id


def _pass(client, td, start, end):
    ev = client.post("/api/evidence/upload", headers=H(OWNER), files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                     data={"entity_id": ENT, "title": "Retest", "coverage_from": start.isoformat(),
                           "coverage_to": end.isoformat(), "validity_days": "400"})
    assert ev.status_code == 200, ev.text
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": "PASS", "source_system": "probe",
        "evidence_ids": [ev.json()["evidence_id"]]})
    assert r.status_code == 200, r.text


def _snapshot(client, lineage, start, end):
    r = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                    json={"period_from": start.isoformat(), "period_to": end.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _issues(lineage):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        return db.execute(select(Issue).where(Issue.tenant_id == TA, Issue.dedupe_key.like(f"%:{lineage}"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _move(client, iid, state, **extra):
    return client.post(f"/api/issues/{iid}/transition", headers=H(OWNER), json={"state": state, **extra})


def _to_pending_verification(client, iid):
    assert client.post(f"/api/issues/{iid}/assign", headers=H(OWNER),
                       json={"owner_actor_id": OWNER, "reason": "Owner"}).status_code == 200
    for state in ("TRIAGED", "IN_REMEDIATION", "PENDING_VERIFICATION"):
        r = _move(client, iid, state, reason="step")
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ sequential rules
def test_one_issue_per_condition_with_its_audit_outbox_and_ordered_history(client, org):
    from app.models import AuditEvent, IssueEvent, OutboxEvent
    s = _subject()
    lineage = _obligation(s)
    first = _snapshot(client, lineage, SEP, OCT)   # no test result: NOT_PROVEN -> proof-gap issue
    second = _snapshot(client, lineage, SEP, OCT)  # the same open condition: no second issue
    assert first["state"] == second["state"] == "NOT_PROVEN"
    [issue] = _issues(lineage)
    assert (issue.issue_type, issue.state, issue.dedupe_key) == ("PROOF_GAP", "OPEN", f"PROOF_GAP:{lineage}")
    assert issue.source_ref_id == first["snapshot_id"]
    assert len(_rows(AuditEvent, action="issue.create", object_id=issue.issue_id)) == 1
    assert len(_rows(OutboxEvent, event_type="assurance.issue_created", aggregate_id=issue.issue_id)) == 1
    gaps = [_rows(OutboxEvent, event_type="assurance.proof_gap_detected", aggregate_id=s_["snapshot_id"])
            for s_ in (first, second)]
    # Existing behaviour, preserved: proof_gap_detected accompanies the creation of the proof-gap work item only.
    assert [len(g) for g in gaps] == [1, 0]
    [created] = _rows(IssueEvent, issue_id=issue.issue_id)
    [snap_created] = _rows(OutboxEvent, event_type="assurance.snapshot_created", aggregate_id=first["snapshot_id"])
    assert created.record_seq > snap_created.record_seq  # the issue follows the snapshot that raised it


def test_issues_are_not_merged_across_subjects_or_obligation_definitions(client, org):
    s1, s2 = _subject(), _subject()
    lineages = [_obligation(s1), _obligation(s2), _obligation(s1, "DORA_ICT_INCIDENT_PROCESS")]
    for lineage in lineages:
        _snapshot(client, lineage, SEP, OCT)
    issues = [_issues(lineage) for lineage in lineages]
    assert [len(i) for i in issues] == [1, 1, 1] and len({i[0].issue_id for i in issues}) == 3


def test_a_closed_issue_is_not_reopened_and_a_new_gap_is_a_new_issue(client, org):
    s = _subject()
    lineage = _obligation(s)
    td = _control(client, s)
    _snapshot(client, lineage, SEP, OCT)
    [issue] = _issues(lineage)
    _to_pending_verification(client, issue.issue_id)
    _pass(client, td, OCT, NOV)
    proven = _snapshot(client, lineage, OCT, NOV)
    assert proven["state"] == "PROVEN"
    r = client.post(f"/api/issues/{issue.issue_id}/close", headers=H(OWNER),
                    json={"verification_snapshot_id": proven["snapshot_id"], "reason": "Retest passed"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/issues/{issue.issue_id}/assign", headers=H(OWNER),
                       json={"owner_actor_id": CCO, "reason": "late"}).json()["detail"]["error"] == "ISSUE_TERMINAL"
    _snapshot(client, lineage, NOV, DEC)  # a new proof gap after closure
    assert [i.state for i in _issues(lineage)] == ["CLOSED", "OPEN"]


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _threads(*targets):
    errors = []

    def wrap(fn):
        def run():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        return run
    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for t in threads:
        t.start()
    return threads, errors


def _snapshot_in_session(lineage, start, end):
    from app.db import SessionLocal, set_principal
    from app.engines import assurance as asr
    with SessionLocal() as db:
        set_principal(db, TA, CCO)
        out = asr.compute_snapshot(db, tenant_id=TA, obligation_lineage_id=lineage, period_from=start, period_to=end,
                                   record_cutoff_seq=None, perspective="RECONSTRUCTED_NOW", actor_id=CCO)
        db.commit()
        return out


def test_concurrent_failing_snapshots_raise_one_issue(client, org, pg):
    from app.models import AuditEvent, IssueEvent, OutboxEvent
    lineage = _obligation(_subject())
    barrier = threading.Barrier(3)

    def snap():
        barrier.wait(20)
        _snapshot_in_session(lineage, SEP, OCT)
    threads, errors = _threads(snap, snap, snap)
    for t in threads:
        t.join(60)
    assert not errors, errors
    [issue] = _issues(lineage)
    assert len(_rows(IssueEvent, issue_id=issue.issue_id)) == 1
    assert len(_rows(AuditEvent, action="issue.create", object_id=issue.issue_id)) == 1
    assert len(_rows(OutboxEvent, event_type="assurance.issue_created", aggregate_id=issue.issue_id)) == 1


def _paused_issue_lock(monkeypatch):
    """Pause the first issue-row lock taken (by the closing/cancelling transaction) until released."""
    from app.engines import governance
    entered, go, real, calls = threading.Event(), threading.Event(), governance._lock_issue, []

    def paused(db, tenant_id, issue_id):
        i = real(db, tenant_id, issue_id)
        if not calls:
            calls.append(1)
            entered.set()
            go.wait(20)
        return i
    monkeypatch.setattr(governance, "_lock_issue", paused)
    return entered, go


def _race_terminal_with_new_gap(client, monkeypatch, terminal):
    """``terminal`` closes or cancels the issue in its own transaction while a new NOT_PROVEN snapshot of the same
    obligation is persisted. Whatever the order, the new gap must end up covered by an active issue."""
    from app.db import SessionLocal, set_principal
    from app.engines import governance
    from app.models import IssueEvent
    s = _subject()
    lineage = _obligation(s)
    td = _control(client, s)
    _snapshot(client, lineage, SEP, OCT)
    [issue] = _issues(lineage)
    verifying = None
    if terminal == "close":
        _to_pending_verification(client, issue.issue_id)
        _pass(client, td, OCT, NOV)
        verifying = _snapshot(client, lineage, OCT, NOV)["snapshot_id"]
    entered, go = _paused_issue_lock(monkeypatch)

    def end_issue():
        with SessionLocal() as db:
            set_principal(db, TA, OWNER)
            if terminal == "close":
                governance.close_issue(db, tenant_id=TA, actor=OWNER, issue_id=issue.issue_id, reason="Retest passed",
                                       verification_snapshot_id=verifying)
            else:
                governance.transition_issue(db, tenant_id=TA, actor_id=OWNER, actor=OWNER, issue_id=issue.issue_id,
                                            to_state="CANCELLED", reason="Duplicate of a programme issue")
            db.commit()
    (t1,), errors = _threads(end_issue)
    assert entered.wait(20)
    (t2,), errors2 = _threads(lambda: _snapshot_in_session(lineage, NOV, DEC))  # no test result: NOT_PROVEN
    t2.join(2)
    go.set()
    t1.join(60)
    t2.join(60)
    assert not errors and not errors2, errors + errors2
    issues = _issues(lineage)
    expected = "CLOSED" if terminal == "close" else "CANCELLED"
    assert sorted(i.state for i in issues) == sorted([expected, "OPEN"]), [i.state for i in issues]
    for i in issues:  # each issue's history has strictly increasing record sequences
        seqs = [e.record_seq for e in sorted(_rows(IssueEvent, issue_id=i.issue_id), key=lambda e: e.record_seq)]
        assert len(seqs) == len(set(seqs))


def test_a_gap_found_while_the_issue_is_being_closed_opens_a_new_issue(client, org, pg, monkeypatch):
    _race_terminal_with_new_gap(client, monkeypatch, "close")


def test_a_gap_found_while_the_issue_is_being_cancelled_opens_a_new_issue(client, org, pg, monkeypatch):
    _race_terminal_with_new_gap(client, monkeypatch, "cancel")


def test_concurrent_assignment_closure_and_cancellation_serialise(client, org, pg):
    from app.db import SessionLocal, set_principal
    from app.engines import governance
    from app.models import IssueEvent
    s = _subject()
    lineage = _obligation(s)
    td = _control(client, s)
    _snapshot(client, lineage, SEP, OCT)
    [issue] = _issues(lineage)
    _to_pending_verification(client, issue.issue_id)
    _pass(client, td, OCT, NOV)
    verifying = _snapshot(client, lineage, OCT, NOV)["snapshot_id"]
    barrier, outcomes = threading.Barrier(3), []

    def attempt(fn):
        def run():
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                barrier.wait(20)
                try:
                    fn(db)
                    db.commit()
                    outcomes.append("ok")
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    outcomes.append(getattr(e, "status_code", repr(e)))
        return run
    close = attempt(lambda db: governance.close_issue(db, tenant_id=TA, actor=OWNER, issue_id=issue.issue_id,
                                                      reason="Retest passed", verification_snapshot_id=verifying))
    reopen = attempt(lambda db: governance.transition_issue(db, tenant_id=TA, actor_id=OWNER, actor=OWNER,
                                                            issue_id=issue.issue_id, to_state="IN_REMEDIATION",
                                                            reason="Retest inconclusive"))
    reassign = attempt(lambda db: governance.assign_issue(db, tenant_id=TA, actor=OWNER, issue_id=issue.issue_id,
                                                          owner_actor_id=CCO, reason="Handover"))
    threads, errors = _threads(close, reopen, reassign)
    for t in threads:
        t.join(60)
    assert not errors and all(o in ("ok", 409) for o in outcomes), outcomes
    events = sorted(_rows(IssueEvent, issue_id=issue.issue_id), key=lambda e: e.record_seq)
    # The recorded history is one valid chain whatever the interleaving, and the projection is its last state.
    for prev, e in zip(events, events[1:]):
        assert e.from_state == prev.to_state, [(x.event_type, x.from_state, x.to_state) for x in events]
    [final] = _issues(lineage)[:1]
    assert final.state == events[-1].to_state and outcomes.count("ok") == len(events) - 5
