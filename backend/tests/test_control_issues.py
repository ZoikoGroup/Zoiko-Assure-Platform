"""Control-level issue generation from stored ControlEvaluations (engines/governance.py raise_control_issue, called by
engines/assurance.py evaluate_control) and its ControlEvaluation-verified closure.

Requirements exercised: ZA-STA-001 §17 (Control FAILING: "Create/link issue unless an existing open issue already
covers exact control/scope/root cause"; Control REQUIRES_REVIEW: "Create review issue/task with conflicting source
references and owner"; NOT_EVALUATED needs a source/cadence policy, so no issue; closure "requires remediation plus a new
test/evidence cycle ... The resulting new ControlEvaluation ... provides the actual updated state"), §06 (only OPERATING
is affirmative), STA-REQ-023; ZA-DOM-001 §7.1 (ControlImplementation unique per control + governed scope + variant);
ZA-GLS-001 §07 issue workflow; ZA-EVT-001 issue.created / issue.closed; ZA-API-001 API-REQ-008/014.

Every test builds its own organisation, controls and evidence; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-ci-{TAG}", f"tenant-cib-{TAG}"
ENT, SUBJ, SYS = f"entity-ci-{TAG}", f"subj-ci-{TAG}", f"subj-ci-sys-{TAG}"
ENT_B, SUBJ_B = f"entity-cib-{TAG}", f"subj-cib-{TAG}"
OWNER, CCO, B_OWNER, B_CCO = f"ci-owner-{TAG}", f"ci-cco-{TAG}", f"cib-owner-{TAG}", f"cib-cco-{TAG}"
INC, HUM, MON, DEV, NOTICE = ("C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT", "C_AI_MONITORING_LOGS",
                              "C_DEVICE_ATTESTATION", "C_AI_TRANSPARENCY_NOTICE")
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
        db.add(Subject(subject_id=SYS, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name="Probe system"))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_OWNER, TB, ["CONTROL_OWNER"]),
                                (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _control(client, key, subject=SUBJ, actor=OWNER, tests=1):
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE",
        "tests": [{"name": f"{key} test {n}", "source_class": "MANUAL"} for n in range(tests)]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalars().first()
        tds = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                         .order_by(TestDefinition.name)).scalars().all()
    return ci, [t.test_definition_id for t in tds]


def _run(client, td, result, start="2026-09-01", end="2026-10-01", evidence=(), actor=OWNER):
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": f"{start}T01:00:00Z", "window_from": f"{start}T00:00:00Z",
        "window_to": f"{end}T00:00:00Z", "result": result, "source_system": "probe", "evidence_ids": list(evidence)})
    assert r.status_code == 200, r.text


def _evidence(client, entity=ENT, actor=OWNER):
    r = client.post("/api/evidence/upload", headers=H(actor),
                    files={"file": ("retest.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Test record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _evaluate(client, ci, start="2026-09-01", end="2026-10-01", actor=OWNER):
    r = client.post(f"/api/controls/{ci}/evaluate", headers=H(actor),
                    json={"window_from": f"{start}T00:00:00Z", "window_to": f"{end}T00:00:00Z"})
    assert r.status_code == 200, r.text
    return r.json()


def _issues(ci, tenant=TA):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        return db.execute(select(Issue).where(Issue.tenant_id == tenant, Issue.dedupe_key.like(f"%:{ci}:%"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()


def _audits(action, object_id):
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        return db.execute(select(AuditEvent).where(AuditEvent.action == action, AuditEvent.object_id == object_id)
                          .order_by(AuditEvent.record_seq)).scalars().all()


def _outbox(event_type, aggregate_id):
    from app.db import AdminSession
    from app.models import OutboxEvent
    with AdminSession() as db:
        return db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type,
                                                    OutboxEvent.aggregate_id == aggregate_id)).scalars().all()


# ------------------------------------------------------------------------------------------------ generation
def test_a_failing_evaluation_raises_one_control_issue_with_its_exact_identity(client, org):
    ci, (td,) = _control(client, INC)
    _run(client, td, "FAIL")
    ev = _evaluate(client, ci)
    assert ev["state"] == "FAILING" and ev["evaluation_id"]
    [issue] = _issues(ci)
    assert (issue.issue_type, issue.state, issue.source_ref_type, issue.source_ref_id) == \
        ("CONTROL_FAILING", "OPEN", "ControlEvaluation", ev["evaluation_id"])
    assert issue.dedupe_key.startswith(f"CONTROL_FAILING:{ci}:{SUBJ}:") and issue.owner_actor_id is None
    # History, audit and the outbox are written with the issue (catalogued issue.created + the existing event).
    [created] = _audits("issue.create", issue.issue_id)
    assert created.tenant_id == TA and ev["evaluation_id"] in created.reason and "TEST_FAIL" in created.reason
    [out] = _outbox("issue.created", issue.issue_id)
    assert out.tenant_id == TA and out.payload["control_evaluation_id"] == ev["evaluation_id"]
    assert len(_outbox("assurance.issue_created", issue.issue_id)) == 1
    events = client.get(f"/api/issues/{issue.issue_id}/events", headers=H(CCO)).json()
    assert [(e["event_type"], e["to_state"]) for e in events] == [("CREATED", "OPEN")]


def test_retries_and_repeated_failures_link_the_open_issue(client, org):
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlEvaluation, ControlImplementation
    ci, (td,) = _control(client, HUM)
    _run(client, td, "FAIL")
    first = _evaluate(client, ci)
    second = _evaluate(client, ci)  # a later watermark: a new evaluation of the same condition
    assert second["evaluation_id"] != first["evaluation_id"] and second["state"] == "FAILING"
    [issue] = _issues(ci)
    links = _audits("issue.evaluation_linked", issue.issue_id)
    assert len(links) == 1 and second["evaluation_id"] in links[0].reason
    # An identical retry (same inputs and cutoff) reuses the stored evaluation: nothing new, nothing linked.
    with AdminSession() as db:
        c = db.get(ControlImplementation, ci)
        cutoff = db.get(ControlEvaluation, second["evaluation_id"]).record_cutoff_seq
        a, b = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
        again = asr.evaluate_control(db, c, a, b, cutoff, persist=True)
        db.commit()
    assert again["evaluation_id"] == second["evaluation_id"]
    assert len(_issues(ci)) == 1 and len(_audits("issue.evaluation_linked", issue.issue_id)) == 1


def test_different_controls_scopes_and_root_causes_are_different_issues(client, org):
    from app.db import AdminSession
    from app.engines import governance
    from app.models import ControlImplementation
    a, (ta,) = _control(client, MON)                 # control MON on the legal entity
    b, (tb,) = _control(client, MON, subject=SYS)    # the same control on another subject (scope)
    c, (tc,) = _control(client, DEV)                 # another control
    for ci, td in ((a, ta), (b, tb), (c, tc)):
        _run(client, td, "FAIL")
        _evaluate(client, ci)
    issues = [_issues(x) for x in (a, b, c)]
    assert [len(i) for i in issues] == [1, 1, 1]
    assert len({i[0].issue_id for i in issues}) == 3 and len({i[0].dedupe_key for i in issues}) == 3
    # Root cause = the evaluation's recorded reason codes: another cause on the same control is another issue,
    # the same cause links.
    with AdminSession() as db:
        impl = db.get(ControlImplementation, a)
        other = governance.raise_control_issue(db, ci=impl, evaluation=SimpleNamespace(
            evaluation_id="probe-cause", state="FAILING", reason_codes=["IPE_FAILED"], inputs={}))
        same = governance.raise_control_issue(db, ci=impl, evaluation=SimpleNamespace(
            evaluation_id="probe-same", state="FAILING", reason_codes=["TEST_FAIL"], inputs={}))
        db.commit()
        other_id, same_id = other.issue_id, same.issue_id
    assert other_id != issues[0][0].issue_id and same_id == issues[0][0].issue_id
    assert len(_issues(a)) == 2


def test_requires_review_raises_a_review_issue_with_the_conflicting_sources_and_owner(client, org):
    ci, (td,) = _control(client, NOTICE)
    e1, e2 = _evidence(client), _evidence(client)
    conflict = client.post("/api/evidence-conflicts", headers=H(OWNER), json={
        "member_evidence_ids": [e1, e2], "description": "Two records disagree on the notice text"})
    assert conflict.status_code == 200, conflict.text
    _run(client, td, "PASS", evidence=[e1])
    ev = _evaluate(client, ci)
    assert ev["state"] == "REQUIRES_REVIEW"
    [issue] = _issues(ci)
    assert (issue.issue_type, issue.owner_actor_id, issue.source_ref_id) == ("CONTROL_REVIEW", OWNER, ev["evaluation_id"])
    [created] = _audits("issue.create", issue.issue_id)
    assert e1 in created.reason and "SOURCE_CONFLICT" in created.reason
    # The review issue already has its owner, so it can be triaged at once.
    assert client.post(f"/api/issues/{issue.issue_id}/transition", headers=H(OWNER),
                       json={"state": "TRIAGED"}).status_code == 200


def test_unevaluated_and_operating_results_raise_no_issue(client, org):
    ci, (td,) = _control(client, INC, subject=SYS)
    assert _evaluate(client, ci)["state"] == "NOT_EVALUATED"   # no test result: needs a cadence policy, not an issue
    _run(client, td, "PASS", evidence=[_evidence(client)])
    assert _evaluate(client, ci)["state"] == "OPERATING"
    assert _issues(ci) == []
    preview = client.get(f"/api/controls/{ci}", headers=H(OWNER))  # previews store no evaluation and raise nothing
    assert preview.status_code == 200 and _issues(ci) == []


# ------------------------------------------------------------------------------------------------ isolation, auth, rollback
def test_issues_stay_in_their_organisation_and_need_authority(client, org):
    ci, (td,) = _control(client, DEV, subject=SUBJ_B, actor=B_OWNER)
    _run(client, td, "FAIL", actor=B_OWNER)
    _evaluate(client, ci, actor=B_OWNER)
    [issue] = _issues(ci, tenant=TB)
    assert _issues(ci, tenant=TA) == []
    assert issue.issue_id not in [i["issue_id"] for i in client.get("/api/issues", headers=H(CCO)).json()]
    assert issue.issue_id in [i["issue_id"] for i in client.get("/api/issues", headers=H(B_CCO)).json()]
    for path, body in ((f"/api/issues/{issue.issue_id}/assign", {"owner_actor_id": OWNER, "reason": "x"}),
                       (f"/api/issues/{issue.issue_id}/transition", {"state": "CANCELLED", "reason": "x"})):
        assert client.post(path, headers=H(OWNER), json=body).status_code in (403, 404)
    a_ci, (a_td,) = _control(client, HUM, subject=SYS)
    _run(client, a_td, "FAIL")
    before = len(_issues(a_ci))
    for actor in ("actor-ext-auditor", B_OWNER):  # no authority to evaluate this organisation's control
        r = client.post(f"/api/controls/{a_ci}/evaluate", headers=H(actor),
                        json={"window_from": "2026-09-01T00:00:00Z", "window_to": "2026-10-01T00:00:00Z"})
        assert r.status_code in (403, 404), actor
    assert len(_issues(a_ci)) == before == 0


def test_a_failure_after_the_evaluation_rolls_back_the_issue_with_it(client, org, monkeypatch):
    from app.db import AdminSession
    from app.engines import governance
    from app.models import ControlEvaluation
    ci, (td,) = _control(client, NOTICE, subject=SYS)
    _run(client, td, "FAIL")
    real = governance.emit

    def broken(db, **kw):
        if kw.get("event_type") == "issue.created":
            raise RuntimeError("outbox write failed")
        return real(db, **kw)
    monkeypatch.setattr(governance, "emit", broken)
    with pytest.raises(RuntimeError):
        _evaluate(client, ci)
    monkeypatch.undo()
    with AdminSession() as db:
        assert db.execute(select(func.count()).select_from(ControlEvaluation)
                          .where(ControlEvaluation.control_implementation_id == ci)).scalar() == 0
    assert _issues(ci) == []
    _evaluate(client, ci)
    assert len(_issues(ci)) == 1


# ------------------------------------------------------------------------------------------------ verified closure
def _work(client, iid):
    for step in (lambda: client.post(f"/api/issues/{iid}/assign", headers=H(OWNER),
                                     json={"owner_actor_id": OWNER, "reason": "Control owner"}),
                 lambda: client.post(f"/api/issues/{iid}/transition", headers=H(OWNER), json={"state": "TRIAGED"}),
                 lambda: client.post(f"/api/issues/{iid}/transition", headers=H(OWNER),
                                     json={"state": "IN_REMEDIATION", "reason": "Fixing the incident process"}),
                 lambda: client.post(f"/api/issues/{iid}/transition", headers=H(OWNER),
                                     json={"state": "PENDING_VERIFICATION", "reason": "Fix deployed"})):
        r = step()
        assert r.status_code == 200, r.text


def _close(client, iid, **ref):
    return client.post(f"/api/issues/{iid}/close", headers=H(OWNER), json={"reason": "Retest passed", **ref})


def _fingerprint():
    from app.db import AdminSession
    from app.models import AssuranceSnapshot, ControlEvaluation, EvidenceArtifact, TestRun
    with AdminSession() as db:
        return tuple(db.execute(select(func.count()).select_from(m)).scalar()
                     for m in (AssuranceSnapshot, ControlEvaluation, TestRun, EvidenceArtifact))


def test_a_control_issue_closes_only_on_a_new_operating_evaluation_of_the_same_control(client, org):
    ci, (td,) = _control(client, DEV, subject=SYS)
    _run(client, td, "FAIL")
    failing = _evaluate(client, ci)
    [issue] = _issues(ci)
    iid = issue.issue_id
    _work(client, iid)
    assert client.post(f"/api/issues/{iid}/transition", headers=H(OWNER),
                       json={"state": "CLOSED", "reason": "x"}).json()["detail"]["error"] == "VERIFICATION_REQUIRED"
    for body in ({}, {"verification_snapshot_id": "a", "verification_control_evaluation_id": "b"}):
        assert _close(client, iid, **body).status_code == 422  # exactly one verification reference
    r = _close(client, iid, verification_snapshot_id="any")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "VERIFICATION_KIND_MISMATCH"
    r = _close(client, iid, verification_control_evaluation_id=failing["evaluation_id"])
    assert r.status_code == 409 and {"VERIFICATION_PREDATES_REMEDIATION", "RETEST_REQUIRED",
                                     "VERIFICATION_NOT_OPERATING"} <= {p["code"] for p in r.json()["detail"]["problems"]}
    stale = _evaluate(client, ci)  # new evaluation, no new test result
    r = _close(client, iid, verification_control_evaluation_id=stale["evaluation_id"])
    assert r.status_code == 409 and "RETEST_REQUIRED" in [p["code"] for p in r.json()["detail"]["problems"]]
    other, (otd,) = _control(client, "C_ACCESS_REVIEW", subject=SUBJ)
    _run(client, otd, "PASS", start="2026-10-01", end="2026-11-01", evidence=[_evidence(client)])
    r = _close(client, iid, verification_control_evaluation_id=_evaluate(client, other, "2026-10-01", "2026-11-01")["evaluation_id"])
    assert r.status_code == 422 and "VERIFICATION_SCOPE_MISMATCH" in [p["code"] for p in r.json()["detail"]["problems"]]
    b_ci, (b_td,) = _control(client, HUM, subject=SUBJ_B, actor=B_OWNER)
    _run(client, b_td, "PASS", start="2026-10-01", end="2026-11-01",
         evidence=[_evidence(client, entity=ENT_B, actor=B_OWNER)], actor=B_OWNER)
    b_eval = _evaluate(client, b_ci, "2026-10-01", "2026-11-01", actor=B_OWNER)
    r = _close(client, iid, verification_control_evaluation_id=b_eval["evaluation_id"])
    assert r.status_code == 404
    # Retest through the owning domains, then the new OPERATING evaluation verifies closure.
    _run(client, td, "PASS", start="2026-10-01", end="2026-11-01", evidence=[_evidence(client)])
    operating = _evaluate(client, ci, "2026-10-01", "2026-11-01")
    assert operating["state"] == "OPERATING"
    before = _fingerprint()
    r = _close(client, iid, verification_control_evaluation_id=operating["evaluation_id"])
    assert r.status_code == 200 and r.json()["state"] == "CLOSED", r.text
    assert _fingerprint() == before  # closure wrote no evaluation, snapshot, test or evidence (STA-REQ-023)
    [closed] = _outbox("issue.closed", iid)
    assert closed.payload["verification_control_evaluation_id"] == operating["evaluation_id"]
    # A new failure after closure is a new issue; the closed one is not reopened.
    _run(client, td, "FAIL", start="2026-11-01", end="2026-11-02")
    _evaluate(client, ci, "2026-11-01", "2026-11-02")
    assert sorted(i.state for i in _issues(ci)) == ["CLOSED", "OPEN"]


def test_obligation_and_control_issues_stay_separate(client, org):
    """A failing obligation snapshot raises its obligation-level issue; the control it evaluates raises its own
    control-level issue. The identities are never merged (STA §17's linking needs a relationship model: blocked)."""
    from app.db import AdminSession
    from app.models import Issue, ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    ci, (td,) = _control(client, MON, subject=SUBJ_B, actor=B_OWNER)
    _run(client, td, "FAIL", actor=B_OWNER)
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "children": [{"node_type": "CONTROL", "node_id": "l", "control_key": MON}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TB, entity_id=ENT_B, subject_id=SUBJ_B,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="p",
                                  attestation_id="p", lifecycle="ADOPTED", valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TB, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(B_CCO),
                       json={"period_from": "2026-09-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z"})
    assert snap.status_code == 200 and snap.json()["state"] == "FAILING"
    with AdminSession() as db:
        obligation_issue = db.execute(select(Issue).where(Issue.tenant_id == TB, Issue.dedupe_key.like(f"%{lineage}%"))).scalars().all()
    control_issue = _issues(ci, tenant=TB)
    assert [i.issue_type for i in obligation_issue] == ["OBLIGATION_FAILING"]
    assert [i.issue_type for i in control_issue] == ["CONTROL_FAILING"]
    assert obligation_issue[0].issue_id != control_issue[0].issue_id


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_concurrent_evaluations_of_one_condition_raise_one_issue(client, org, pg):
    from app.db import SessionLocal, set_principal
    from app.engines import assurance as asr
    from app.models import ControlImplementation
    from app.platform import current_watermark
    ci, (td,) = _control(client, "C_MFA_STANDARD")
    _run(client, td, "FAIL")
    a, b = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
    barrier, results = threading.Barrier(3), []

    def worker(offset):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                c = db.get(ControlImplementation, ci)
                cutoff = current_watermark(db)["record_seq"] + offset  # distinct digests: three new evaluations
                barrier.wait()
                results.append(asr.evaluate_control(db, c, a, b, cutoff, persist=True)["evaluation_id"])
                db.commit()
        except Exception as e:  # noqa: BLE001
            results.append(repr(e))
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(set(results)) == 3 and not [r for r in results if r.startswith("<") or "Error" in r], results
    [issue] = _issues(ci)
    assert issue.state == "OPEN" and len(_audits("issue.evaluation_linked", issue.issue_id)) == 2
    assert len(_outbox("issue.created", issue.issue_id)) == 1
