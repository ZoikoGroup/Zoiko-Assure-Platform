"""Issue remediation lifecycle: assignment and retest-verified closure (engines/governance.py; routers/assurance.py).

Requirements exercised: ZA-STA-001 §17 (FAILING creates/links an issue unless an open one covers the exact scope;
closure "requires remediation plus a new test/evidence cycle ... The resulting new ControlEvaluation/AssuranceSnapshot
provides the actual updated state"), STA-REQ-023 (closure never changes assurance state); ZA-GLS-001 §07 issue workflow
(TRIAGED = ownership established; CLOSED = verification confirms closure criteria); ZA-EVT-001 issue.closed;
ZA-API-001 API-REQ-008/014; DAT-REQ-009 (append-only history).

Every test builds its own organisation, control, obligation and evidence; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-ir-{TAG}", f"tenant-irb-{TAG}"
ENT, SUBJ, ENT_B, SUBJ_B = f"entity-ir-{TAG}", f"subj-ir-{TAG}", f"entity-irb-{TAG}", f"subj-irb-{TAG}"
OWNER, CCO, AGENT = f"ir-owner-{TAG}", f"ir-cco-{TAG}", f"ir-agent-{TAG}"
B_OWNER, B_CCO = f"irb-owner-{TAG}", f"irb-cco-{TAG}"
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid, eid, sid in ((TA, ENT, SUBJ), (TB, ENT_B, SUBJ_B)):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{tid} Bank AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Bank AG"))
        for aid, tid, kind, roles in ((OWNER, TA, "HUMAN", ["CONTROL_OWNER"]), (CCO, TA, "HUMAN", ["CCO"]),
                                      (AGENT, TA, "AGENT", []), (B_OWNER, TB, "HUMAN", ["CONTROL_OWNER"]),
                                      (B_CCO, TB, "HUMAN", ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type=kind, display_name=aid, roles=roles)
        db.commit()
    return True


def _obligation(tenant=TA, entity=ENT, subject=SUBJ, key=KEY):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "children": [
        {"node_type": "CONTROL", "node_id": "leaf", "control_key": key}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=tenant, entity_id=entity, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _control(client, subject=SUBJ, actor=OWNER, key=KEY):
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": f"{key} probe test", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalars().first()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalars().first()
    return ci, td.test_definition_id


def _run(client, td, result, start, end, evidence=(), actor=OWNER):
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": f"{start}T01:00:00Z", "window_from": f"{start}T00:00:00Z",
        "window_to": f"{end}T00:00:00Z", "result": result, "source_system": "probe", "evidence_ids": list(evidence)})
    assert r.status_code == 200, r.text
    return r.json()


def _evidence(client, entity=ENT, actor=OWNER):
    r = client.post("/api/evidence/upload", headers=H(actor), files={"file": ("retest.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Retest record", "coverage_from": "2026-10-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _snapshot(client, lineage, start, end, actor=CCO):
    r = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(actor), json={
        "period_from": f"{start}T00:00:00Z", "period_to": f"{end}T00:00:00Z"})
    assert r.status_code == 200, r.text
    return r.json()


def _issues(client, lineage, actor=CCO):
    return [i for i in client.get("/api/issues", headers=H(actor)).json() if lineage in i["dedupe_key"]]


def _move(client, iid, state, actor=OWNER, **extra):
    return client.post(f"/api/issues/{iid}/transition", headers=H(actor), json={"state": state, **extra})


def _assign(client, iid, owner=OWNER, actor=OWNER, reason="Accountable control owner"):
    return client.post(f"/api/issues/{iid}/assign", headers=H(actor), json={"owner_actor_id": owner, "reason": reason})


def _close(client, iid, snapshot_id, actor=OWNER, reason="Retest passed"):
    return client.post(f"/api/issues/{iid}/close", headers=H(actor),
                       json={"verification_snapshot_id": snapshot_id, "reason": reason})


def _codes(r):
    return [p["code"] for p in r.json()["detail"]["problems"]]


def _assurance_fingerprint():
    """Counts and states of every assurance record closure must never write."""
    from app.db import AdminSession
    from app.models import AssuranceSnapshot, ControlEvaluation, EvidenceArtifact, ObligationInstance, TestRun
    with AdminSession() as db:
        return tuple(db.execute(select(func.count()).select_from(m)).scalar() for m in
                     (AssuranceSnapshot, ControlEvaluation, TestRun, EvidenceArtifact, ObligationInstance)) + (
            tuple(db.execute(select(AssuranceSnapshot.snapshot_id, AssuranceSnapshot.state, AssuranceSnapshot.snapshot_hash)
                             .order_by(AssuranceSnapshot.snapshot_id)).all()),)


@pytest.fixture(scope="module")
def failing(client, org):
    """A FAILING obligation: the snapshot hook raises one OBLIGATION_FAILING issue."""
    ci, td = _control(client)
    lineage = _obligation()
    _run(client, td, "FAIL", "2026-09-01", "2026-10-01")
    snap = _snapshot(client, lineage, "2026-09-01", "2026-10-01")
    assert snap["state"] == "FAILING"
    [issue] = _issues(client, lineage)
    assert (issue["issue_type"], issue["state"], issue["source_ref_id"]) == ("OBLIGATION_FAILING", "OPEN", snap["snapshot_id"])
    return {"ci": ci, "td": td, "lineage": lineage, "snapshot": snap, "issue": issue}


# ------------------------------------------------------------------------------------------------ trigger and dedupe
def test_a_repeated_failing_result_links_the_open_issue_instead_of_duplicating_it(client, failing):
    again = _snapshot(client, failing["lineage"], "2026-09-01", "2026-10-01")
    assert again["state"] == "FAILING"
    assert [i["issue_id"] for i in _issues(client, failing["lineage"])] == [failing["issue"]["issue_id"]]


# ------------------------------------------------------------------------------------------------ assignment
def test_triage_needs_an_accountable_owner_and_assignment_is_recorded(client, failing):
    iid = failing["issue"]["issue_id"]
    r = _move(client, iid, "TRIAGED")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "ISSUE_OWNER_REQUIRED"
    for owner, code in ((B_OWNER, "OWNER_NOT_FOUND"), ("no-such-actor", "OWNER_NOT_FOUND"), (AGENT, "OWNER_NOT_HUMAN")):
        r = _assign(client, iid, owner=owner)
        assert r.status_code == 422 and r.json()["detail"]["error"] == code, (owner, r.text)
    r = _assign(client, iid)
    assert r.status_code == 200 and r.json()["owner_actor_id"] == OWNER and r.json()["state"] == "OPEN"
    again = _assign(client, iid)  # a retried request records nothing twice
    assert again.status_code == 409 and again.json()["detail"]["error"] == "ISSUE_OWNER_UNCHANGED"
    assert _assign(client, iid, owner=CCO, reason="Escalated to the CCO").status_code == 200
    events = client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()
    assert [(e["event_type"], e["owner_actor_id"], e["actor_id"]) for e in events] == [
        ("CREATED", None, "system"), ("ASSIGNED", OWNER, OWNER), ("ASSIGNED", CCO, OWNER)]
    assert _assign(client, iid, owner=OWNER, reason="Back to the control owner").status_code == 200
    assert _move(client, iid, "TRIAGED").status_code == 200


@pytest.mark.parametrize("body", [
    {"owner_actor_id": OWNER},                                                    # reason missing
    {"owner_actor_id": OWNER, "reason": " "},                                     # blank reason
    {"owner_actor_id": OWNER, "reason": "x", "due_at": "2026-12-01T00:00:00Z"},   # no invented due-date policy
    {"owner_actor_id": OWNER, "reason": "x", "state": "CLOSED"},                  # no mass assignment
])
def test_malformed_assignments_are_refused(client, org, body):
    lineage = _obligation()
    from app.db import AdminSession
    from app.engines import governance
    from app.models import Issue
    with AdminSession() as db:
        i = Issue(tenant_id=TA, title="probe", issue_type="PROOF_GAP", severity="MEDIUM", priority="P2",
                  source_ref_type="AssuranceSnapshot", source_ref_id="probe", dedupe_key=f"probe:{lineage}")
        db.add(i)
        db.flush()
        governance.record_issue_created(db, issue=i, actor_id="system")
        db.commit()
        iid = i.issue_id
    assert client.post(f"/api/issues/{iid}/assign", headers=H(OWNER), json=body).status_code == 422
    assert client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()[-1]["event_type"] == "CREATED"


def test_issue_actions_are_authorised_and_tenant_isolated(client, failing):
    iid = failing["issue"]["issue_id"]
    snap = failing["snapshot"]["snapshot_id"]
    for path, body in ((f"/api/issues/{iid}/assign", {"owner_actor_id": OWNER, "reason": "x"}),
                       (f"/api/issues/{iid}/close", {"verification_snapshot_id": snap, "reason": "x"})):
        assert client.post(path, json=body).status_code == 401
        for actor in ("actor-ext-auditor", "actor-audit", AGENT):
            assert client.post(path, headers=H(actor), json=body).status_code == 403, (actor, path)
        for actor in (B_OWNER, B_CCO):  # another organisation's issue reads as not found
            assert client.post(path, headers=H(actor), json=body).status_code in (403, 404), (actor, path)
    assert client.get(f"/api/issues/{iid}/events", headers=H(B_CCO)).status_code in (403, 404)


# ------------------------------------------------------------------------------------------------ retest and closure
def test_closure_requires_a_new_test_cycle_and_a_proven_result_and_never_changes_assurance(client, failing, monkeypatch):
    iid, lineage, td = failing["issue"]["issue_id"], failing["lineage"], failing["td"]
    issue = client.get("/api/issues", headers=H(CCO)).json()
    if next(i for i in issue if i["issue_id"] == iid)["state"] == "OPEN":  # independent of test order
        assert _assign(client, iid).status_code in (200, 409)
        assert _move(client, iid, "TRIAGED").status_code == 200
    assert _move(client, iid, "IN_REMEDIATION", reason="Patched the incident runbook").status_code == 200
    assert _move(client, iid, "PENDING_VERIFICATION", reason="Remediation claimed complete").status_code == 200
    # The plain transition can no longer close an assurance issue without verification.
    r = _move(client, iid, "CLOSED", reason="looks fine")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "VERIFICATION_REQUIRED"
    # The snapshot that raised the issue predates remediation, used no new test and is FAILING.
    r = _close(client, iid, failing["snapshot"]["snapshot_id"])
    assert r.status_code == 409
    assert {"VERIFICATION_PREDATES_REMEDIATION", "RETEST_REQUIRED", "VERIFICATION_NOT_PROVEN"} <= set(_codes(r))
    # A new snapshot with no new test result is still not a retest.
    stale = _snapshot(client, lineage, "2026-09-01", "2026-10-01")
    r = _close(client, iid, stale["snapshot_id"])
    assert r.status_code == 409 and "RETEST_REQUIRED" in _codes(r)
    # Missing evidence: a PASS without evidence is NOT_PROVEN, so it does not verify closure.
    _run(client, td, "PASS", "2026-10-01", "2026-11-01")
    unproven = _snapshot(client, lineage, "2026-10-01", "2026-11-01")
    assert unproven["state"] == "NOT_PROVEN"
    r = _close(client, iid, unproven["snapshot_id"])
    assert r.status_code == 409 and _codes(r) == ["VERIFICATION_NOT_PROVEN"]
    # Another obligation's PROVEN snapshot, or another organisation's, never verifies this issue.
    other = _obligation(key="C_HUMAN_OVERSIGHT")
    _, td2 = _control(client, key="C_HUMAN_OVERSIGHT")
    _run(client, td2, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client)])
    other_snap = _snapshot(client, other, "2026-10-01", "2026-11-01")
    assert other_snap["state"] == "PROVEN"
    r = _close(client, iid, other_snap["snapshot_id"])
    assert r.status_code == 422 and "VERIFICATION_SCOPE_MISMATCH" in _codes(r)
    b_line = _obligation(tenant=TB, entity=ENT_B, subject=SUBJ_B)
    _, b_td = _control(client, subject=SUBJ_B, actor=B_OWNER)
    _run(client, b_td, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client, entity=ENT_B, actor=B_OWNER)],
         actor=B_OWNER)
    b_snap = _snapshot(client, b_line, "2026-10-01", "2026-11-01", actor=B_CCO)
    r = _close(client, iid, b_snap["snapshot_id"])
    assert r.status_code == 404 and _codes(r) == ["VERIFICATION_NOT_FOUND"]
    # Retest through the owning domains: new evidence, a new PASS TestRun, a new PROVEN snapshot.
    _run(client, td, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client)])
    proven = _snapshot(client, lineage, "2026-10-01", "2026-11-01")
    assert proven["state"] == "PROVEN"
    before = _assurance_fingerprint()
    r = _close(client, iid, proven["snapshot_id"])
    assert r.status_code == 200 and r.json()["state"] == "CLOSED", r.text
    assert _assurance_fingerprint() == before  # closure wrote no assurance, test or evidence record (STA-REQ-023)
    events = client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()
    assert events[-1]["event_type"] == "CLOSED" and proven["snapshot_id"] in events[-1]["reason"]
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent
    with AdminSession() as db:
        [closed] = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "issue.closed",
                                                        OutboxEvent.aggregate_id == iid)).scalars().all()
        assert closed.tenant_id == TA and closed.payload["verification_snapshot_id"] == proven["snapshot_id"]
        assert db.execute(select(AuditEvent).where(AuditEvent.action == "issue.transition", AuditEvent.object_id == iid,
                                                   AuditEvent.new_state == "CLOSED")).scalars().first() is not None
    # Closed is terminal: no reopening, reassignment or second closure; the history is intact.
    assert _move(client, iid, "IN_REMEDIATION").status_code == 409
    assert _assign(client, iid, owner=CCO).json()["detail"]["error"] == "ISSUE_TERMINAL"
    assert _close(client, iid, proven["snapshot_id"]).status_code == 409
    assert [e["to_state"] for e in events if e["event_type"] != "ASSIGNED"] == [
        "OPEN", "TRIAGED", "IN_REMEDIATION", "PENDING_VERIFICATION", "CLOSED"]
    # The obligation's assurance comes from snapshots alone: the old FAILING period is still FAILING.
    assert _snapshot(client, lineage, "2026-09-01", "2026-10-01")["state"] == "FAILING"


def test_a_new_failure_after_closure_raises_a_new_issue(client, org):
    ci, td = _control(client, key="C_AI_MONITORING_LOGS")
    lineage = _obligation(key="C_AI_MONITORING_LOGS")
    _run(client, td, "FAIL", "2026-09-01", "2026-10-01")
    _snapshot(client, lineage, "2026-09-01", "2026-10-01")
    [first] = _issues(client, lineage)
    iid = first["issue_id"]
    for step in (lambda: _assign(client, iid), lambda: _move(client, iid, "TRIAGED"),
                 lambda: _move(client, iid, "IN_REMEDIATION"), lambda: _move(client, iid, "PENDING_VERIFICATION")):
        assert step().status_code == 200
    _run(client, td, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client)])
    assert _close(client, iid, _snapshot(client, lineage, "2026-10-01", "2026-11-01")["snapshot_id"]).status_code == 200
    _snapshot(client, lineage, "2026-09-01", "2026-10-01")  # the closed issue no longer covers a new failing result
    states = sorted(i["state"] for i in _issues(client, lineage))
    assert states == ["CLOSED", "OPEN"]


def test_other_issue_types_keep_their_closure_and_cancellation_still_needs_a_reason(client, org):
    from app.db import AdminSession
    from app.engines import governance
    from app.models import Issue
    ids = {}
    with AdminSession() as db:
        for kind in ("INTEGRITY_INCIDENT", "PROOF_GAP"):
            i = Issue(tenant_id=TA, title=f"{kind} probe", issue_type=kind, severity="HIGH", priority="P1",
                      source_ref_type="Integrity" if kind == "INTEGRITY_INCIDENT" else "AssuranceSnapshot",
                      source_ref_id="probe", dedupe_key=f"{kind}:{TAG}:{secrets.token_hex(3)}")
            db.add(i)
            db.flush()
            governance.record_issue_created(db, issue=i, actor_id="system")
            ids[kind] = i.issue_id
        db.commit()
    iid = ids["INTEGRITY_INCIDENT"]  # integrity incidents: closure criteria owned by the evidence domain, unchanged
    for state in ("TRIAGED", "IN_REMEDIATION", "PENDING_VERIFICATION", "CLOSED"):
        if state == "TRIAGED":
            assert _assign(client, iid).status_code == 200
        assert _move(client, iid, state, reason="probe").status_code == 200, state
    r = _close(client, iid, "any")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "VERIFICATION_NOT_DEFINED"
    gap = ids["PROOF_GAP"]
    assert _move(client, gap, "CANCELLED").status_code == 422  # GLS CANCELLED: reason required
    assert _move(client, gap, "CANCELLED", reason="Duplicate of an existing proof-gap issue").status_code == 200


def test_a_failure_mid_closure_rolls_everything_back(client, org, monkeypatch):
    from app.engines import governance
    ci, td = _control(client, key="C_AI_TRANSPARENCY_NOTICE")
    lineage = _obligation(key="C_AI_TRANSPARENCY_NOTICE")
    _run(client, td, "FAIL", "2026-09-01", "2026-10-01")
    _snapshot(client, lineage, "2026-09-01", "2026-10-01")
    [issue] = _issues(client, lineage)
    iid = issue["issue_id"]
    for step in (lambda: _assign(client, iid), lambda: _move(client, iid, "TRIAGED"),
                 lambda: _move(client, iid, "IN_REMEDIATION"), lambda: _move(client, iid, "PENDING_VERIFICATION")):
        assert step().status_code == 200
    _run(client, td, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client)])
    proven = _snapshot(client, lineage, "2026-10-01", "2026-11-01")
    events_before = client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()
    real_emit = governance.emit

    def failing_emit(db, **kw):
        if kw.get("event_type") == "issue.closed":
            raise RuntimeError("outbox write failed")
        return real_emit(db, **kw)
    monkeypatch.setattr(governance, "emit", failing_emit)
    with pytest.raises(RuntimeError):
        _close(client, iid, proven["snapshot_id"])
    monkeypatch.undo()
    after = [i for i in client.get("/api/issues", headers=H(CCO)).json() if i["issue_id"] == iid][0]
    assert after["state"] == "PENDING_VERIFICATION"
    assert client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json() == events_before
    assert _close(client, iid, proven["snapshot_id"]).status_code == 200


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _race(*calls):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.security import Ctx
    barrier, results = threading.Barrier(len(calls)), []

    def worker(fn):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                ctx = Ctx(db=db, actor=db.get(Actor, OWNER))
                barrier.wait()
                fn(ctx)
                db.commit()
                results.append("ok")
        except HTTPException as e:
            results.append(e.status_code)
        except Exception as e:  # noqa: BLE001
            results.append(repr(e))
    threads = [threading.Thread(target=worker, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return sorted(results, key=str)


def _new_issue(lineage):
    from app.db import AdminSession
    from app.engines import governance
    from app.models import Issue
    with AdminSession() as db:
        i = Issue(tenant_id=TA, title="race probe", issue_type="PROOF_GAP", severity="MEDIUM", priority="P2",
                  source_ref_type="AssuranceSnapshot", source_ref_id="probe", dedupe_key=f"race:{lineage}")
        db.add(i)
        db.flush()
        governance.record_issue_created(db, issue=i, actor_id="system")
        db.commit()
        return i.issue_id


def test_concurrent_assignments_and_transitions_serialise(client, org, pg):
    from app.engines import governance as g
    iid = _new_issue(_obligation())
    same = lambda ctx: g.assign_issue(ctx.db, tenant_id=TA, actor=ctx.actor, issue_id=iid, owner_actor_id=OWNER, reason="race")
    assert _race(same, same) == [409, "ok"]  # one assignment, one ISSUE_OWNER_UNCHANGED
    triage = lambda ctx: g.transition_issue(ctx.db, tenant_id=TA, actor_id=OWNER, actor=ctx.actor, issue_id=iid,
                                            to_state="TRIAGED", reason="race")
    assert _race(triage, triage) == [409, "ok"]  # the second sees TRIAGED and is refused
    events = client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()
    assert [e["event_type"] for e in events] == ["CREATED", "ASSIGNED", "TRIAGED"]


def test_concurrent_closures_close_once(client, org, pg):
    from app.engines import governance as g
    ci, td = _control(client, key="C_DEVICE_ATTESTATION")
    lineage = _obligation(key="C_DEVICE_ATTESTATION")
    _run(client, td, "FAIL", "2026-09-01", "2026-10-01")
    _snapshot(client, lineage, "2026-09-01", "2026-10-01")
    [issue] = _issues(client, lineage)
    iid = issue["issue_id"]
    for step in (lambda: _assign(client, iid), lambda: _move(client, iid, "TRIAGED"),
                 lambda: _move(client, iid, "IN_REMEDIATION"), lambda: _move(client, iid, "PENDING_VERIFICATION")):
        assert step().status_code == 200
    _run(client, td, "PASS", "2026-10-01", "2026-11-01", evidence=[_evidence(client)])
    proven = _snapshot(client, lineage, "2026-10-01", "2026-11-01")["snapshot_id"]
    close = lambda ctx: g.close_issue(ctx.db, tenant_id=TA, actor=ctx.actor, issue_id=iid,
                                      verification_snapshot_id=proven, reason="race")
    assert _race(close, close) == [409, "ok"]
    from app.db import AdminSession
    from app.models import OutboxEvent
    with AdminSession() as db:
        assert len(db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "issue.closed",
                                                        OutboxEvent.aggregate_id == iid)).all()) == 1
    assert [e["event_type"] for e in client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()].count("CLOSED") == 1
