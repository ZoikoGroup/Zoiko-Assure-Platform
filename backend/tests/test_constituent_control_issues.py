"""Traceability from an obligation-level issue to its constituent control-level issues (STA §17 "Obligation FAILING:
... link constituent control issues").

No relationship table exists (DAT §11 assurance.issue has "source evaluation refs" only), so nothing is stored: the
relationship is derived, read-only, from immutable records that already exist:
  obligation issue --source_ref--> AssuranceSnapshot --segments[].leaf_evaluations[].control_evaluation_id-->
  ControlEvaluation --(the deterministic condition identity raise_control_issue uses)--> the control issue that was
  active for that condition at the snapshot's record sequence (assurance_issue_event).
It is fixed by records written before the obligation issue existed, so it is the same at every later cutoff; only the
control issue's state is reconstructed at the requested cutoff (TIM-REQ-006/015).

Requirements exercised: ZA-STA-001 §17, STA-REQ-001 (issue state is its own dimension), STA-REQ-023; ZA-TIM-001
TIM-REQ-006/015/017; DOM-INV-11 (another organisation's records are never shown). Every test builds its own records.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-cci-{TAG}", f"tenant-ccib-{TAG}"
ENT, SUBJ, ENT_B, SUBJ_B = f"entity-cci-{TAG}", f"subj-cci-{TAG}", f"entity-ccib-{TAG}", f"subj-ccib-{TAG}"
OWNER, CCO, B_CCO = f"cci-owner-{TAG}", f"cci-cco-{TAG}", f"ccib-cco-{TAG}"
INC, MON = "C_ICT_INCIDENT_PROCESS", "C_AI_MONITORING_LOGS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11))


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        for tid, eid, sid in ((TA, ENT, SUBJ), (TB, ENT_B, SUBJ_B)):
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{eid} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"{eid} AG"))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
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


def _obligation(subject, keys, op="ALL"):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": op, "node_id": "root", "children": [
        {"node_type": "CONTROL", "node_id": f"leaf{i}", "control_key": k} for i, k in enumerate(keys)]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle="ADOPTED", valid_from=T0,
                                  record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _control(client, key, subject):
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
    return ci, td.test_definition_id


def _run(client, td, result, start=SEP, end=OCT):
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": []})
    assert r.status_code == 200, r.text


def _snapshot(client, lineage, start=SEP, end=OCT):
    r = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                    json={"period_from": start.isoformat(), "period_to": end.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _control_issue(ci):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        return db.execute(select(Issue).where(Issue.tenant_id == TA, Issue.dedupe_key.like(f"%:{ci}:%"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()


def _seq():
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _obligation_issue(client, lineage, as_of=None, actor=CCO):
    params = {} if as_of is None else {"as_of_record_seq": as_of}
    r = client.get(f"/api/obligations/{lineage}", headers=H(actor), params=params)
    assert r.status_code == 200, r.text
    [issue] = r.json()["issues"]
    return issue


def _leaf_evaluations(snapshot, ci):
    return {leaf["control_evaluation_id"] for seg in snapshot["segments"] for leaf in seg["leaf_evaluations"]
            if leaf.get("control_implementation_id") == ci}


# ------------------------------------------------------------------------------------------------ derivation
def test_an_obligation_failure_traces_to_the_control_issues_of_its_failing_controls(client, org):
    s = _subject()
    lineage = _obligation(s, [INC, MON])
    failing_ci, failing_td = _control(client, INC, s)
    other_ci, _ = _control(client, MON, s)  # no test result: NOT_EVALUATED, raises no control issue
    _run(client, failing_td, "FAIL")
    snap = _snapshot(client, lineage)
    assert snap["state"] == "FAILING"
    [control_issue] = _control_issue(failing_ci)
    assert _control_issue(other_ci) == []
    issue = _obligation_issue(client, lineage)
    assert issue["issue_type"] == "OBLIGATION_FAILING"
    trace = issue["constituent_control_issues"]
    assert (trace["basis"], trace["snapshot_id"], trace["problems"]) == ("DERIVED", snap["snapshot_id"], [])
    [link] = trace["control_issues"]
    assert (link["control_implementation_id"], link["control_issue_id"], link["issue_type"], link["state"]) == \
        (failing_ci, control_issue.issue_id, "CONTROL_FAILING", "OPEN")
    assert link["control_evaluation_id"] in _leaf_evaluations(snap, failing_ci)
    assert link["control_state"] == "FAILING"


def test_a_failure_already_covered_by_an_open_control_issue_traces_to_that_issue(client, org):
    s = _subject()
    lineage = _obligation(s, [INC])
    ci, td = _control(client, INC, s)
    _run(client, td, "FAIL")
    r = client.post(f"/api/controls/{ci}/evaluate", headers=H(OWNER),
                    json={"window_from": SEP.isoformat(), "window_to": OCT.isoformat()})
    assert r.status_code == 200 and r.json()["state"] == "FAILING"
    [existing] = _control_issue(ci)
    snap = _snapshot(client, lineage)  # its leaf is a new evaluation of the same condition: linked, not duplicated
    assert len(_control_issue(ci)) == 1
    [link] = _obligation_issue(client, lineage)["constituent_control_issues"]["control_issues"]
    assert link["control_issue_id"] == existing.issue_id
    assert link["control_evaluation_id"] in _leaf_evaluations(snap, ci) and link["control_evaluation_id"] != existing.source_ref_id


def test_a_proof_gap_with_no_failing_control_has_no_constituent_control_issue(client, org):
    s = _subject()
    lineage = _obligation(s, [INC])
    _control(client, INC, s)
    assert _snapshot(client, lineage)["state"] == "NOT_PROVEN"
    trace = _obligation_issue(client, lineage)["constituent_control_issues"]
    assert (trace["control_issues"], trace["problems"]) == ([], [])


# ------------------------------------------------------------------------------------------------ history
def test_the_relationship_is_stable_and_control_issue_state_is_reconstructed_at_the_cutoff(client, org):
    s = _subject()
    lineage = _obligation(s, [INC])
    ci, td = _control(client, INC, s)
    _run(client, td, "FAIL")
    _snapshot(client, lineage)
    [control_issue] = _control_issue(ci)
    opened = _seq()
    cid = control_issue.issue_id
    for path, body in ((f"/api/issues/{cid}/assign", {"owner_actor_id": OWNER, "reason": "Owner"}),
                       (f"/api/issues/{cid}/transition", {"state": "TRIAGED"}),
                       (f"/api/issues/{cid}/transition", {"state": "CANCELLED", "reason": "Duplicate of a programme issue"})):
        assert client.post(path, headers=H(OWNER), json=body).status_code == 200
    # Ending the obligation issue changes neither the relationship nor the control issue.
    oid = _obligation_issue(client, lineage)["issue_id"]
    assert client.post(f"/api/issues/{oid}/transition", headers=H(OWNER),
                       json={"state": "CANCELLED", "reason": "Tracked under the programme issue"}).status_code == 200
    then = _obligation_issue(client, lineage, as_of=opened)
    [link_then] = then["constituent_control_issues"]["control_issues"]
    assert (link_then["control_issue_id"], link_then["state"]) == (cid, "OPEN")
    assert then["state"] == "OPEN"
    now = _obligation_issue(client, lineage)
    [link_now] = now["constituent_control_issues"]["control_issues"]
    assert (link_now["control_issue_id"], link_now["state"], now["state"]) == (cid, "CANCELLED", "CANCELLED")
    assert {k: v for k, v in link_then.items() if k != "state"} == {k: v for k, v in link_now.items() if k != "state"}


def test_a_trace_that_cannot_be_established_is_reported(client, org):
    from app.db import AdminSession
    from app.models import Issue
    s = _subject()
    lineage = _obligation(s, [INC])
    ci, td = _control(client, INC, s)
    _run(client, td, "FAIL")
    _snapshot(client, lineage)
    trace = _obligation_issue(client, lineage)["constituent_control_issues"]
    assert trace["problems"] == [] and len(trace["control_issues"]) == 1
    # An issue whose source is not a snapshot of this organisation names the gap instead of a guess.
    with AdminSession() as db:
        db.add(Issue(tenant_id=TA, title="legacy", issue_type="OBLIGATION_FAILING", source_ref_type="AssuranceSnapshot",
                     source_ref_id="no-such-snapshot", dedupe_key=f"OBLIGATION_FAILING:{lineage}:legacy"))
        db.commit()
    r = client.get(f"/api/obligations/{lineage}", headers=H(CCO)).json()
    [legacy] = [i for i in r["issues"] if i["source_ref_id"] == "no-such-snapshot"]
    assert legacy["constituent_control_issues"]["control_issues"] == []
    assert [p["code"] for p in legacy["constituent_control_issues"]["problems"]] == ["SOURCE_SNAPSHOT_NOT_FOUND"]


# ------------------------------------------------------------------------------------------------ isolation, read-only
def test_the_trace_never_crosses_organisations_and_reads_write_nothing(client, org):
    from app.db import AdminSession
    from app.models import AuditEvent, Issue, IssueEvent, OutboxEvent
    s = _subject()
    lineage = _obligation(s, [INC])
    ci, td = _control(client, INC, s)
    _run(client, td, "FAIL")
    _snapshot(client, lineage)
    [control_issue] = _control_issue(ci)
    with AdminSession() as db:  # another organisation's issue with the same condition identity
        db.add(Issue(tenant_id=TB, title="foreign", issue_type="CONTROL_FAILING", source_ref_type="ControlEvaluation",
                     source_ref_id="foreign", dedupe_key=control_issue.dedupe_key))
        db.commit()

    def counts():
        with AdminSession() as db:
            return (_seq(), *(db.execute(select(func.count()).select_from(m)).scalar()
                              for m in (AuditEvent, OutboxEvent, Issue, IssueEvent)))
    before = counts()
    for as_of in (None, _seq()):
        [link] = _obligation_issue(client, lineage, as_of)["constituent_control_issues"]["control_issues"]
        assert link["control_issue_id"] == control_issue.issue_id
    assert counts() == before
    assert client.get(f"/api/obligations/{lineage}", headers=H(B_CCO)).status_code in (403, 404)
