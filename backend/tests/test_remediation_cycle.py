"""The remediation cycle an issue's verified closure is measured against, and owner reassignment during it.

Controlling text: ZA-STA-001 §17 closure rule ("Closing an issue does not directly change control or obligation status.
Closure requires remediation plus a new test/evidence cycle ... The resulting new ControlEvaluation/AssuranceSnapshot
provides the actual updated state"), STA-REQ-023, and the ZA-GLS-001 issue workflow (IN_REMEDIATION is entered from
TRIAGED, or from PENDING_VERIFICATION when verification sends the issue back).

A remediation cycle begins when the issue enters IN_REMEDIATION. An owner reassignment made while it stays there is an
ASSIGNED event recorded IN_REMEDIATION -> IN_REMEDIATION; it is not an entry. Before this fix,
governance._remediation_started_seq took the latest event whose to_state was IN_REMEDIATION, so a reassignment became
the cycle start: a passing retest recorded earlier in the same cycle no longer counted and the closure was refused with
RETEST_REQUIRED. The cycle start is now the latest event that entered IN_REMEDIATION from another state (from_state
null for a migration-0008 BASELINE, or not IN_REMEDIATION). Nothing is written by the rule; no state, event type,
payload or lock changes, so no new concurrency case arises (the closure paths keep their locks and order).

The shared vectors (tests/remediation_cycle_vectors.json) are the contract frontend/lib/issues.test.mjs checks
remediationStartSeq against, replacing a comparison with this module's query text. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import json
import pathlib
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-rmc-{TAG}", f"tenant-rmcb-{TAG}"
ENT, ENT_B = f"entity-rmc-{TAG}", f"entity-rmcb-{TAG}"
OWNER, OWNER2, B_OWNER = f"rmc-owner-{TAG}", f"rmc-owner2-{TAG}", f"rmcb-owner-{TAG}"
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11))
VECTORS = json.loads((pathlib.Path(__file__).parent / "remediation_cycle_vectors.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tenant, ent in ((TA, ENT), (TB, ENT_B)):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=f"s-{ent}"))
            db.add(Subject(subject_id=f"s-{ent}", tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                           display_name=ent))
        for aid, tenant in ((OWNER, TA), (OWNER2, TA), (B_OWNER, TB)):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid,
                          roles=["CONTROL_OWNER"])
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ the contract
@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=[v["name"] for v in VECTORS["vectors"]])
def test_the_cycle_start_is_the_latest_entry_into_remediation(client, org, vector):
    """Each shared vector, recorded as an issue's events, gives the expected cycle start from the query closure uses."""
    from app.db import AdminSession
    from app.engines import governance
    from app.models import IssueEvent, new_id
    issue = SimpleNamespace(tenant_id=TA, issue_id=new_id())
    with AdminSession() as db:
        for event_type, from_state, to_state, seq in vector["events"]:
            db.add(IssueEvent(issue_id=issue.issue_id, tenant_id=TA, event_type=event_type, from_state=from_state,
                              to_state=to_state, actor_id=OWNER, record_seq=seq))
        db.commit()
        assert governance._remediation_started_seq(db, issue) == vector["expected"], vector["name"]


# ------------------------------------------------------------------------------------------------ through the API
def _subject():
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _control(client):
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": KEY, "subject_id": _subject(), "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": [{"name": "retest", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
    return ci, td.test_definition_id


def _run(client, td, result, start=OCT, end=NOV, evidence=()):
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": list(evidence)})
    assert r.status_code == 200, r.text


def _evidence(client):
    r = client.post("/api/evidence/upload", headers=H(OWNER),
                    files={"file": ("retest.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": ENT, "title": "Retest record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _evaluate(client, ci, start=OCT, end=NOV) -> dict:
    r = client.post(f"/api/controls/{ci}/evaluate", headers=H(OWNER),
                    json={"window_from": start.isoformat(), "window_to": end.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _failing_issue(client) -> tuple[str, str, str]:
    """A control evaluated FAILING over September: its CONTROL_FAILING issue. (control, test definition, issue)."""
    from app.db import AdminSession
    from app.models import Issue
    ci, td = _control(client)
    _run(client, td, "FAIL", SEP, OCT)
    assert _evaluate(client, ci, SEP, OCT)["state"] == "FAILING"
    with AdminSession() as db:
        [issue] = db.execute(select(Issue).where(Issue.tenant_id == TA, Issue.dedupe_key.like(f"%:{ci}:%"))).scalars().all()
    return ci, td, issue.issue_id


def _post(client, iid, path, body, actor=OWNER):
    return client.post(f"/api/issues/{iid}/{path}", headers=H(actor), json=body)


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _into_remediation(client, iid):
    _ok(_post(client, iid, "assign", {"owner_actor_id": OWNER, "reason": "Control owner"}))
    _ok(_post(client, iid, "transition", {"state": "TRIAGED"}))
    _ok(_post(client, iid, "transition", {"state": "IN_REMEDIATION", "reason": "Fixing the control"}))


def _reassign(client, iid, owner):
    _ok(_post(client, iid, "assign", {"owner_actor_id": owner, "reason": "Handover"}))


def _to_verification(client, iid):
    _ok(_post(client, iid, "transition", {"state": "PENDING_VERIFICATION", "reason": "Fix deployed"}))


def _close(client, iid, evaluation_id, actor=OWNER):
    return _post(client, iid, "close", {"reason": "Retest passed", "verification_control_evaluation_id": evaluation_id},
                 actor)


def _codes(response) -> list[str]:
    return [p["code"] for p in response.json()["detail"]["problems"]]


def _events(client, iid) -> list[tuple]:
    return [(e["event_type"], e["from_state"], e["to_state"], e["record_seq"], e["event_id"])
            for e in _ok(client.get(f"/api/issues/{iid}/events", headers=H(OWNER)))]


def _started(iid) -> int | None:
    from app.db import AdminSession
    from app.engines import governance
    from app.models import Issue
    with AdminSession() as db:
        return governance._remediation_started_seq(db, db.get(Issue, iid))


def _entry_seq(client, iid) -> int:
    return max(seq for t, f, to, seq, _ in _events(client, iid) if to == "IN_REMEDIATION" and f != "IN_REMEDIATION")


def test_a_reassignment_during_remediation_keeps_the_cycle_and_the_passing_retest_closes_the_issue(client, org):
    """Scenarios 1-3: entering remediation starts the cycle; the retest (PASS with evidence) is recorded in it; the owner is
    reassigned while the issue stays IN_REMEDIATION. The cycle start is still the entry, so the OPERATING evaluation of
    that retest verifies closure. Before, the reassignment became the cycle start: 409 RETEST_REQUIRED."""
    ci, td, iid = _failing_issue(client)
    _into_remediation(client, iid)
    entry = _entry_seq(client, iid)
    assert _started(iid) == entry
    _run(client, td, "PASS", evidence=[_evidence(client)])
    _reassign(client, iid, OWNER2)
    assert _started(iid) == entry
    _to_verification(client, iid)
    operating = _evaluate(client, ci)
    assert operating["state"] == "OPERATING"
    closed = _close(client, iid, operating["evaluation_id"])
    assert closed.status_code == 200 and closed.json()["state"] == "CLOSED", closed.text


def test_the_same_flow_without_a_reassignment_still_closes(client, org):
    """Scenario 4: unchanged behaviour."""
    ci, td, iid = _failing_issue(client)
    _into_remediation(client, iid)
    _run(client, td, "PASS", evidence=[_evidence(client)])
    _to_verification(client, iid)
    closed = _close(client, iid, _evaluate(client, ci)["evaluation_id"])
    assert closed.status_code == 200 and closed.json()["state"] == "CLOSED", closed.text


def test_repeated_reassignments_do_not_reset_the_cycle(client, org):
    """Scenario 6: two handovers (and back) during remediation; the cycle start is the entry throughout."""
    ci, td, iid = _failing_issue(client)
    _into_remediation(client, iid)
    entry = _entry_seq(client, iid)
    _run(client, td, "PASS", evidence=[_evidence(client)])
    for owner in (OWNER2, OWNER, OWNER2):
        _reassign(client, iid, owner)
        assert _started(iid) == entry
    _to_verification(client, iid)
    assert _close(client, iid, _evaluate(client, ci)["evaluation_id"]).status_code == 200


def test_returning_to_remediation_starts_a_genuinely_new_cycle(client, org):
    """Scenario 5: verification sends the issue back (PENDING_VERIFICATION -> IN_REMEDIATION). That entry is a new cycle:
    the retest of the earlier cycle and the evaluation made before the re-entry no longer verify closure; a retest
    recorded after the re-entry does, also with a reassignment in the new cycle."""
    ci, td, iid = _failing_issue(client)
    _into_remediation(client, iid)
    _run(client, td, "PASS", evidence=[_evidence(client)])
    earlier = _evaluate(client, ci)
    assert earlier["state"] == "OPERATING"
    _to_verification(client, iid)
    _ok(_post(client, iid, "transition", {"state": "IN_REMEDIATION", "reason": "Verification found gaps"}))
    reentry = _entry_seq(client, iid)
    assert _started(iid) == reentry
    _reassign(client, iid, OWNER2)
    assert _started(iid) == reentry
    _to_verification(client, iid)
    before = db_fingerprint()
    refused = _close(client, iid, earlier["evaluation_id"])
    assert refused.status_code == 409 and {"VERIFICATION_PREDATES_REMEDIATION", "RETEST_REQUIRED"} <= set(_codes(refused))
    assert db_fingerprint() == before
    _run(client, td, "PASS", evidence=[_evidence(client)])
    closed = _close(client, iid, _evaluate(client, ci)["evaluation_id"])
    assert closed.status_code == 200 and closed.json()["state"] == "CLOSED", closed.text


def test_failed_stale_and_early_retests_still_prevent_closure_after_a_reassignment(client, org):
    """Scenario 7: the fix moves no closure rule. With a reassignment in the cycle: an evaluation made before the
    cycle began predates it; one made in the cycle from no new test or evidence needs a retest; a failing retest is not
    OPERATING. Refusals write nothing."""
    ci, td, iid = _failing_issue(client)
    early = _evaluate(client, ci)  # NOT_EVALUATED over October, before the cycle
    _into_remediation(client, iid)
    _reassign(client, iid, OWNER2)
    stale = _evaluate(client, ci)  # in the cycle, but nothing new was recorded for it
    _run(client, td, "FAIL")
    failing = _evaluate(client, ci)
    assert failing["state"] == "FAILING"
    _to_verification(client, iid)
    before = db_fingerprint()
    expectations = ((early, "VERIFICATION_PREDATES_REMEDIATION"), (stale, "RETEST_REQUIRED"),
                    (failing, "VERIFICATION_NOT_OPERATING"))
    for evaluation, code in expectations:
        r = _close(client, iid, evaluation["evaluation_id"])
        assert r.status_code == 409 and code in _codes(r), (code, r.text)
    assert db_fingerprint() == before


def test_another_organisation_cannot_reassign_or_close_and_the_history_is_kept(client, org):
    """Scenarios 8-9: another organisation's principal gets 404 for this issue whatever it sends and writes nothing; the
    issue's recorded events are never altered (closure only appends its own CLOSED event)."""
    ci, td, iid = _failing_issue(client)
    _into_remediation(client, iid)
    _run(client, td, "PASS", evidence=[_evidence(client)])
    _reassign(client, iid, OWNER2)
    _to_verification(client, iid)
    operating = _evaluate(client, ci)
    history = _events(client, iid)
    before = db_fingerprint()
    attempts = [_post(client, iid, "assign", {"owner_actor_id": B_OWNER, "reason": "Take over"}, actor=B_OWNER),
                _close(client, iid, operating["evaluation_id"], actor=B_OWNER),
                client.get(f"/api/issues/{iid}/events", headers=H(B_OWNER))]
    assert [a.status_code for a in attempts] == [404, 404, 404], [a.text for a in attempts]
    assert db_fingerprint() == before and _events(client, iid) == history
    assert _close(client, iid, operating["evaluation_id"]).status_code == 200
    after = _events(client, iid)
    assert after[:-1] == history and after[-1][:3] == ("CLOSED", "PENDING_VERIFICATION", "CLOSED")
