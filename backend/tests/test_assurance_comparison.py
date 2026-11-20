"""Historical assurance comparison (engines/assurance_comparison.compare): one obligation and valid period as known at two
record cutoffs, or frozen in two snapshots.

Requirements exercised: ZA-TIM-001 §11 (every dependency read as recorded by the side's own cutoff; correction join),
§12 (same valid time, two record cutoffs; the compare response's valid_period and left / right known_at + snapshot_id),
§16 (POST /historical-comparisons semantics), TIM-REQ-002 (half-open intervals), TIM-REQ-017 (each side echoes its
watermark), TIM-REQ-019 (no cutoff beyond the data); ZA-STA-001 §20/§21 WF-STA-01 History and WF-STA-05 Audit compare,
STA-REQ-014 (an exception never changes the assurance state), STA-REQ-019 (historical snapshots are immutable);
WF-TIM-02 (exact dependency references, deterministic outcome difference); QA-REQ-019 (another organisation's
identifiers answer as unknown).

Pinned: the ZA-TIM-001 §12 difference classes are NOT assigned (``differences`` is None with the reason): no controlled
document maps assurance dependency changes onto them. What is compared instead are recorded facts, so "evidence erased"
(still referenced, lifecycle ERASED) is told apart from "evidence no longer in the result" (no longer referenced,
lifecycle unchanged). Every test builds its own records.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-acmp-{TAG}", f"tenant-acmpb-{TAG}"
ENT, ENT_B = f"entity-acmp-{TAG}", f"entity-acmpb-{TAG}"
CCO, OWNER, RISK, B_CCO = (f"acmp-{r}-{TAG}" for r in ("cco", "owner", "risk", "b-cco"))
FACT = f"acmp{TAG}.flag"
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, MID, OCT, NOV = (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 16, tzinfo=timezone.utc),
                      datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))
AT = datetime(2026, 11, 15, tzinfo=timezone.utc)  # the one evaluation instant both reconstructions use


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import FactDefinition, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tenant, ent in ((TA, ENT), (TB, ENT_B)):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=f"s-{ent}"))
            db.add(Subject(subject_id=f"s-{ent}", tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                           display_name=ent))
        for aid, tenant, roles in ((CCO, TA, ["CCO"]), (OWNER, TA, ["CONTROL_OWNER"]), (RISK, TA, ["RISK_APPROVER"]),
                                   (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.add(FactDefinition(key=FACT, label=FACT, data_type="BOOLEAN", subject_type="AI_ASSET",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ records
def _subject(tenant=TA, ent=ENT):
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=ent, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _obligation(subject, tenant=TA, ent=ENT):
    """An adopted obligation whose satisfaction expression is one control: (lineage, revision)."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage, revision = new_id(), new_id()
    ast = {"node_type": "ALL", "node_id": "root",
           "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": KEY}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")
                          ).scalar_one()
        db.add(ObligationInstance(revision_id=revision, lineage_id=lineage, tenant_id=tenant, entity_id=ent,
                                  subject_id=subject, obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage, revision


def _control(client, subject):
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": KEY, "subject_id": subject, "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                        ).scalar_one()
        return ci, td.test_definition_id, pol.policy_version_id


def _run(client, td, result, start=SEP, end=OCT):
    evidence = []
    if result == "PASS":
        ev = client.post("/api/evidence/upload", headers=H(OWNER),
                         files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                         data={"entity_id": ENT, "title": "Test output", "coverage_from": start.isoformat(),
                               "coverage_to": end.isoformat(), "validity_days": "400"})
        assert ev.status_code == 200, ev.text
        evidence = [ev.json()["evidence_id"]]
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": evidence})
    assert r.status_code == 200, r.text
    return r.json()["test_run_id"], (evidence[0] if evidence else None)


def _snapshot(client, lineage, start=SEP, end=OCT):
    r = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                    json={"period_from": start.isoformat(), "period_to": end.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _watermark():
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _world(client):
    """A subject, its obligation (lineage, revision), its control (ci, test definition, policy)."""
    s = _subject()
    lineage, revision = _obligation(s)
    ci, td, policy = _control(client, s)
    return {"subject": s, "lineage": lineage, "revision": revision, "ci": ci, "td": td, "policy": policy}


def _compare(lineage, left, right, start=SEP, end=OCT, tenant=TA, actor=CCO):
    """The comparison as the principal's own session computes it (row-level security on PostgreSQL); then rolled back."""
    from fastapi.encoders import jsonable_encoder
    from app.db import SessionLocal, set_principal
    from app.engines.assurance_comparison import compare
    with SessionLocal() as db:
        set_principal(db, tenant, actor)
        try:
            return jsonable_encoder(compare(db, tenant_id=tenant, obligation_lineage_id=lineage, period_from=start,
                                            period_to=end, left=left, right=right, actor_id=actor, evaluated_at=AT))
        finally:
            db.rollback()


def _refusal(lineage, left, right, **kw):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _compare(lineage, left, right, **kw)
    return e.value.status_code, e.value.detail


def _ids(entries):
    return [e["id"] if isinstance(e, dict) else e for e in entries]


def _changed_classes(body):
    return {c: d for c, d in body["dependencies"]["classes"].items() if d["added"] or d["removed"]}


# ------------------------------------------------------------------------------------------------ identity
def test_a_snapshot_and_the_reconstruction_at_its_cutoff_compare_as_identical(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": snap["record_cutoff_seq"]})
    assert body["valid_period"] == {"kind": "INSTANT_RANGE", "from": SEP.isoformat(), "until_exclusive": OCT.isoformat()}
    left, right = body["left"], body["right"]
    assert (left["snapshot_id"], left["known_at"]["record_seq"], left["lifecycle"]) == (
        snap["snapshot_id"], snap["record_cutoff_seq"], "FROZEN")
    assert (right["snapshot_id"], right["known_at"]["record_seq"], right["perspective"]) == (
        None, snap["record_cutoff_seq"], "AS_KNOWN_THEN")
    assert left["state"] == right["state"] == "PROVEN"
    out = body["outcome"]
    assert not out["state"]["changed"] and out["rollup_engine_version"]["equal"]
    assert left["dependency_digest"] == right["dependency_digest"]  # same cutoff, same inputs: the same digest
    assert out["reason_codes"] == {"added": [], "removed": []}
    assert [t["changed"] for t in out["timeline"]] == [False]
    assert _changed_classes(body) == {} and body["dependencies"]["changes_between_cutoffs"] == {}
    assert body["left"]["control_test_policies"] == body["right"]["control_test_policies"] == {w["ci"]: w["policy"]}
    assert body["differences"] is None and "TIM-REQ-027" in body["differences_note"]
    same = _compare(w["lineage"], {"record_seq": snap["record_cutoff_seq"]}, {"record_seq": snap["record_cutoff_seq"]})
    assert same["left"] == same["right"] and _changed_classes(same) == {}


# ------------------------------------------------------------------------------------------------ dependency changes
def test_a_later_fail_run_and_evidence_no_longer_in_the_result(client, org):
    w = _world(client)
    _, evidence = _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    fail, _ = _run(client, w["td"], "FAIL")
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("PROVEN", "FAILING")
    assert "TEST_FAIL" in body["outcome"]["reason_codes"]["added"]
    changed = _changed_classes(body)
    [added_run] = changed["test_runs"]["added"]
    assert added_run["id"] == fail and added_run["first_recorded_seq"] > snap["record_cutoff_seq"]
    # The PASS run's evidence is no longer part of the result (a decisive FAIL makes it moot), yet it was not erased,
    # invalidated or superseded: its lifecycle is the same at both cutoffs.
    [dropped] = changed["evidence"]["removed"]
    assert dropped["id"] == evidence and dropped["first_recorded_seq"] <= snap["record_cutoff_seq"]
    assert "evidence_lifecycle" not in body["dependencies"]["changes_between_cutoffs"]
    assert body["right"]["perspective"] == "RECONSTRUCTED_NOW"


def test_evidence_recorded_later_is_a_dependency_added_to_the_result(client, org):
    w = _world(client)
    before = _watermark()  # the control exists; no test result yet
    run, evidence = _run(client, w["td"], "PASS")
    body = _compare(w["lineage"], {"record_seq": before}, {"record_seq": _watermark()})
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("NOT_PROVEN", "PROVEN")
    changed = _changed_classes(body)
    assert _ids(changed["evidence"]["added"]) == [evidence] and _ids(changed["test_runs"]["added"]) == [run]
    assert all(e["first_recorded_seq"] > before for e in changed["evidence"]["added"] + changed["test_runs"]["added"])
    assert changed["evidence"]["removed"] == [] and changed["test_runs"]["removed"] == []


def test_erased_evidence_stays_referenced_with_its_lifecycle_changed(client, org):
    w = _world(client)
    _, evidence = _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    r = client.post(f"/api/evidence/{evidence}/erase", headers=H(CCO), json={"reason": "Privacy erasure request"})
    assert r.status_code == 200, r.text
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("PROVEN", "NOT_PROVEN")
    assert "MISSING_EVIDENCE" in body["outcome"]["reason_codes"]["added"]
    assert "evidence" not in _changed_classes(body)  # still referenced by both results: not "removed"
    assert body["dependencies"]["changes_between_cutoffs"]["evidence_lifecycle"] == [
        {"id": evidence, "left": "ACTIVE", "right": "ERASED"}]


def test_a_fact_correction_is_not_an_assurance_dependency(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS")
    before = _watermark()
    for value in (True, False):  # an assertion and its correction, both recorded after the left cutoff
        r = client.post(f"/api/subjects/{w['subject']}/facts", headers=H(CCO),
                        json={"fact_key": FACT, "value": value, "valid_from": SEP.isoformat()})
        assert r.status_code == 200, r.text
    after = _watermark()
    assert after > before
    body = _compare(w["lineage"], {"record_seq": before}, {"record_seq": after})
    assert not body["outcome"]["state"]["changed"]
    assert _changed_classes(body) == {} and body["dependencies"]["changes_between_cutoffs"] == {}
    # The digests differ only because each control evaluation digest includes its cutoff; no dependency differs.
    assert "control_evaluation_digests" not in body["dependencies"]["classes"]


def test_a_later_obligation_revision_changes_which_segments_apply(client, org):
    from app.db import AdminSession
    from app.models import ObligationInstance, new_id
    from app.platform import next_record_seq
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    second = new_id()
    with AdminSession() as db:  # recorded later: the obligation now ends mid-period (valid time), prior slice closed
        prior = db.get(ObligationInstance, w["revision"])
        seq = next_record_seq(db)
        prior.record_to_seq = seq
        db.add(ObligationInstance(revision_id=second, lineage_id=w["lineage"], tenant_id=TA, entity_id=ENT,
                                  subject_id=w["subject"], obligation_definition_id=prior.obligation_definition_id,
                                  determination_revision_id="probe-2", attestation_id="probe-2", lifecycle="ADOPTED",
                                  valid_from=T0, valid_to=MID, record_from_seq=seq))
        db.commit()
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert (body["left"]["obligation_revision_id"], body["right"]["obligation_revision_id"]) == (w["revision"], second)
    revisions = _changed_classes(body)["obligation_revisions"]
    assert _ids(revisions["added"]) == [second] and _ids(revisions["removed"]) == [w["revision"]]
    assert revisions["added"][0]["first_recorded_seq"] == seq
    assert [(t["from"], t["until_exclusive"], t["left"], t["right"], t["changed"]) for t in body["outcome"]["timeline"]] == [
        (SEP.isoformat(), MID.isoformat(), "PROVEN", "PROVEN", False),
        (MID.isoformat(), OCT.isoformat(), "PROVEN", None, True)]


def test_a_test_policy_version_recorded_later_is_traced(client, org):
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    from app.platform import next_record_seq
    w = _world(client)
    run, _ = _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    with AdminSession() as db:  # version 2 of the control's test policy, with its own test definition
        p2 = ControlTestPolicy(tenant_id=TA, control_implementation_id=w["ci"], version=2, aggregation_mode="ALL_REQUIRED",
                               automation_requirement="NONE", record_seq=next_record_seq(db))
        db.add(p2)
        db.flush()
        db.add(TestDefinition(tenant_id=TA, policy_version_id=p2.policy_version_id, name="probe v2", source_class="MANUAL"))
        db.commit()
        p2_id = p2.policy_version_id
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert (body["left"]["control_test_policies"], body["right"]["control_test_policies"]) == ({w["ci"]: w["policy"]},
                                                                                              {w["ci"]: p2_id})
    changed = _changed_classes(body)
    assert _ids(changed["control_test_policies"]["added"]) == [p2_id]
    assert _ids(changed["control_test_policies"]["removed"]) == [w["policy"]]
    assert _ids(changed["test_runs"]["removed"]) == [run]  # the run belongs to version 1's test definition
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("PROVEN", "NOT_PROVEN")


def test_an_exception_approved_later_changes_its_state_not_the_assurance_state(client, org):
    w = _world(client)
    _run(client, w["td"], "FAIL")
    x = client.post("/api/exceptions", headers=H(OWNER), json={
        "target_type": "OBLIGATION", "target_id": w["lineage"], "reason": "Remediation scheduled",
        "valid_from": SEP.isoformat(), "valid_to": NOV.isoformat()})
    assert x.status_code == 200, x.text
    requested = _watermark()
    r = client.post(f"/api/exceptions/{x.json()['exception_id']}/decide", headers=H(RISK),
                    json={"decision": "APPROVE", "reason": "Accepted for the quarter"})
    assert r.status_code == 200, r.text
    body = _compare(w["lineage"], {"record_seq": requested}, {"record_seq": _watermark()})
    assert body["outcome"]["state"]["left"] == body["outcome"]["state"]["right"] == "FAILING"  # STA-REQ-014
    assert "exceptions" not in _changed_classes(body)
    assert body["dependencies"]["changes_between_cutoffs"]["exception_states"] == [
        {"id": x.json()["exception_id"], "left": {"state": "PENDING_APPROVAL", "problem": None},
         "right": {"state": "APPROVED", "problem": None}}]


def test_an_evidence_conflict_opened_and_resolved_later_is_reported_as_recorded(client, org):
    w = _world(client)
    _, evidence = _run(client, w["td"], "PASS")
    c = client.post("/api/evidence-conflicts", headers=H(OWNER),
                    json={"member_evidence_ids": [evidence], "description": "Source disagrees with the register"})
    assert c.status_code == 200, c.text
    opened = _watermark()
    r = client.post(f"/api/evidence-conflicts/{c.json()['conflict_id']}/resolve", headers=H(CCO),
                    json={"reason": "Register confirmed against the source"})
    assert r.status_code == 200, r.text
    body = _compare(w["lineage"], {"record_seq": opened}, {"record_seq": _watermark()})
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("REQUIRES_REVIEW", "PROVEN")
    assert "SOURCE_CONFLICT" in body["outcome"]["reason_codes"]["removed"]
    assert body["dependencies"]["changes_between_cutoffs"]["evidence_conflicts"] == [
        {"id": evidence, "left": "OPEN", "right": None}]
    assert body["differences"] is None  # the TIM §12 class is not assigned, though the facts are reported


def test_invalidated_and_superseded_evidence_are_reported_with_the_state_the_evaluator_derives(client, org):
    results = {}
    for action in ("invalidate", "supersede"):
        w = _world(client)
        _, evidence = _run(client, w["td"], "PASS")
        snap = _snapshot(client, w["lineage"])
        body = {"reason": f"{action} probe"}
        if action == "supersede":
            successor = client.post("/api/evidence/upload", headers=H(OWNER),
                                    files={"file": ("s.txt", secrets.token_bytes(16), "text/plain")},
                                    data={"entity_id": ENT, "title": "Successor", "coverage_from": SEP.isoformat(),
                                          "coverage_to": OCT.isoformat(), "validity_days": "400"})
            body["successor_evidence_id"] = successor.json()["evidence_id"]
        r = client.post(f"/api/evidence/{evidence}/{action}", headers=H(OWNER), json=body)
        assert r.status_code == 200, r.text
        results[action] = (evidence, _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]},
                                              {"record_seq": _watermark()}))
    for action, lifecycle, right_state in (("invalidate", "INVALIDATED", "NOT_PROVEN"),
                                           ("supersede", "SUPERSEDED", "PROVEN")):
        evidence, body = results[action]
        assert body["dependencies"]["changes_between_cutoffs"]["evidence_lifecycle"] == [
            {"id": evidence, "left": "ACTIVE", "right": lifecycle}]
        assert "evidence" not in _changed_classes(body)  # still referenced by both results
        # As the evaluator derives it today: invalidated evidence no longer proves; superseded evidence still does.
        assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == ("PROVEN", right_state)


# ------------------------------------------------------------------------------------------------ record and valid time
def test_a_record_is_known_from_the_cutoff_that_recorded_it(client, org):
    from app.db import AdminSession
    from app.models import TestRun
    w = _world(client)
    _run(client, w["td"], "PASS")
    fail, _ = _run(client, w["td"], "FAIL")
    with AdminSession() as db:
        k = db.get(TestRun, fail).record_seq
    before = _compare(w["lineage"], {"record_seq": k - 1}, {"record_seq": k - 1})
    across = _compare(w["lineage"], {"record_seq": k - 1}, {"record_seq": k})
    at = _compare(w["lineage"], {"record_seq": k}, {"record_seq": k})
    assert _changed_classes(before) == {} and _changed_classes(at) == {}
    assert before["left"]["state"] == "PROVEN" and at["left"]["state"] == "FAILING"
    assert _ids(_changed_classes(across)["test_runs"]["added"]) == [fail]
    assert (across["outcome"]["state"]["left"], across["outcome"]["state"]["right"]) == ("PROVEN", "FAILING")


def test_valid_time_is_half_open_and_both_sides_compare_one_period(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS", SEP, NOV)
    snap = _snapshot(client, w["lineage"])
    before = _watermark()
    _run(client, w["td"], "FAIL", OCT, NOV)  # starts exactly where [SEP, OCT) ends
    now = _watermark()
    inside = _compare(w["lineage"], {"record_seq": before}, {"record_seq": now})
    assert not inside["outcome"]["state"]["changed"] and _changed_classes(inside) == {}
    wider = _compare(w["lineage"], {"record_seq": before}, {"record_seq": now}, end=NOV)
    assert [(t["from"], t["until_exclusive"], t["changed"]) for t in wider["outcome"]["timeline"]] == [
        (SEP.isoformat(), OCT.isoformat(), False), (OCT.isoformat(), NOV.isoformat(), True)]
    status, detail = _refusal(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": now}, end=NOV)
    assert status == 422 and "another period" in detail
    assert _refusal(w["lineage"], {"record_seq": now}, {"record_seq": now}, start=OCT, end=OCT)[0] == 422


# ------------------------------------------------------------------------------------------------ validation and isolation
def test_sides_are_validated(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    now = _watermark()
    for bad in ({}, {"snapshot_id": snap["snapshot_id"], "record_seq": now}, {"record_seq": -1}, {"record_seq": "7"},
                {"record_seq": True}, {"other": 1}):
        assert _refusal(w["lineage"], bad, {"record_seq": now})[0] == 422, bad
    status, detail = _refusal(w["lineage"], {"record_seq": now + 1000}, {"record_seq": now})
    assert (status, detail["error"]) == (422, "RECORD_SEQ_IN_FUTURE")
    assert _refusal(w["lineage"], {"snapshot_id": "no-such-snapshot"}, {"record_seq": now}) == (404, "snapshot not found")
    other = _world(client)
    assert _refusal(other["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": now})[0] == 422
    assert _refusal("no-such-obligation", {"record_seq": now}, {"record_seq": now}) == (404, "obligation not found")


def test_an_obligation_not_yet_known_at_the_left_cutoff_is_that_side_s_answer(client, org):
    before = _watermark()
    w = _world(client)
    _run(client, w["td"], "PASS")
    body = _compare(w["lineage"], {"record_seq": before}, {"record_seq": _watermark()})
    assert body["left"]["error"] == "Obligation not known at this record cutoff" and "state" not in body["left"]
    assert (body["outcome"]["state"]["left"], body["outcome"]["state"]["right"]) == (None, "PROVEN")
    assert body["left"]["segments"] == [] and "dependency_digest" not in body["left"]


def test_another_organisations_records_answer_as_unknown(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    now = _watermark()
    # B's principal names A's obligation: exactly what an unknown obligation gets.
    assert _refusal(w["lineage"], {"record_seq": now}, {"record_seq": now}, tenant=TB, actor=B_CCO) == \
        _refusal("no-such-obligation", {"record_seq": now}, {"record_seq": now}, tenant=TB, actor=B_CCO) == \
        (404, "obligation not found")
    b_lineage, _ = _obligation(_subject(TB, ENT_B), tenant=TB, ent=ENT_B)
    assert _refusal(b_lineage, {"snapshot_id": snap["snapshot_id"]}, {"record_seq": now}, tenant=TB, actor=B_CCO) == \
        _refusal(b_lineage, {"snapshot_id": "no-such-snapshot"}, {"record_seq": now}, tenant=TB, actor=B_CCO) == \
        (404, "snapshot not found")


def test_repeated_comparisons_give_the_same_answer(client, org):
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    _run(client, w["td"], "FAIL")
    args = (w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert _compare(*args) == _compare(*args)


def test_comparing_writes_nothing_and_leaves_every_historical_record_as_it_was(client, org):
    from app.db import AdminSession
    from app.models import AssuranceSnapshot, ControlEvaluation, Issue
    w = _world(client)
    _run(client, w["td"], "PASS")
    snap = _snapshot(client, w["lineage"])
    _run(client, w["td"], "FAIL")  # a reconstruction now is FAILING: persisted, it would store and raise an issue
    with AdminSession() as db:
        stored = dict(db.get(AssuranceSnapshot, snap["snapshot_id"]).__dict__)
    before = db_fingerprint()
    body = _compare(w["lineage"], {"snapshot_id": snap["snapshot_id"]}, {"record_seq": _watermark()})
    assert body["outcome"]["state"]["right"] == "FAILING"
    assert db_fingerprint() == before
    with AdminSession() as db:
        row = db.get(AssuranceSnapshot, snap["snapshot_id"])
        assert {k: getattr(row, k) for k in ("state", "segments", "dependency_manifest", "snapshot_hash",
                                             "record_cutoff_seq", "lifecycle")} == \
            {k: stored[k] for k in ("state", "segments", "dependency_manifest", "snapshot_hash", "record_cutoff_seq",
                                    "lifecycle")}
        assert len(db.execute(select(AssuranceSnapshot).where(
            AssuranceSnapshot.obligation_lineage_id == w["lineage"])).scalars().all()) == 1
        assert not db.execute(select(Issue).where(Issue.tenant_id == TA, Issue.dedupe_key.like(f"%{w['ci']}%"))).first()
        assert not db.execute(select(ControlEvaluation).where(
            ControlEvaluation.control_implementation_id == w["ci"], ControlEvaluation.state == "FAILING")).first()
