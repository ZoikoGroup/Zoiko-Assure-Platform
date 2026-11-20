"""Audit compare through the API: POST /obligations/{lineage}/historical-comparisons (read-only).

Controlling text:
  * ZA-TIM-001 §16 "POST /historical-comparisons: Compares same valid period across two record cutoffs/snapshots";
    §12 (same valid time; left / right known_at + snapshot_id); WF-TIM-02 (two explicit record watermarks / snapshot
    IDs; exact dependency references; outcome difference with a deterministic reason); TIM-REQ-017 / TIM-REQ-019.
  * ZA-STA-001 §20 WF-STA-01 History "compare-current-reconstruction action"; §21 WF-STA-05 "Audit compare: Compare
    AS_KNOWN_THEN vs RECONSTRUCTED_NOW for same period"; STA-REQ-019 (historical snapshots are immutable).
  * ZA-API-001 §10 (explicit periods and record cutoffs), API-REQ-014 (unknown fields refused); QA-REQ-019,
    IAM-REQ-009/010.

The comparison itself is pinned by tests/test_assurance_comparison.py (engine). Pinned here: the route answers exactly
what the engine computes for the same inputs (the request instant echoed as evaluation_instant), a frozen snapshot
compares with the current reconstruction as WF-STA-05 asks, the TIM §12 classes stay unassigned, requests are validated
as specified, nothing is written, and access is the obligation preview's.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-hcmp-{TAG}", f"tenant-hcmpb-{TAG}"
ENT, ENT2, ENT_B = f"entity-hcmp-{TAG}", f"entity-hcmp2-{TAG}", f"entity-hcmpb-{TAG}"
CCO, OWNER, AUDITOR, TSA, READER, B_CCO, B_OWNER = (
    f"hcmp-{r}-{TAG}" for r in ("cco", "owner", "aud", "tsa", "ent", "b-cco", "b-owner"))
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
PERIOD = {"period_from": SEP.isoformat(), "period_to": OCT.isoformat()}
S: dict = {}


# ------------------------------------------------------------------------------------------------------------ records
def _subject(tenant, ent):
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=ent, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _obligation(subject, tenant, ent) -> str:
    """An adopted obligation whose satisfaction expression is one control (as tests/test_assurance_comparison.py)."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": KEY}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")
                          ).scalar_one()
        db.add(ObligationInstance(revision_id=new_id(), lineage_id=lineage, tenant_id=tenant, entity_id=ent,
                                  subject_id=subject, obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _control(client, subject, actor) -> str:
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": KEY, "subject_id": subject, "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == r.json()["control_implementation_id"])).scalar_one()
        return db.execute(select(TestDefinition.test_definition_id).where(
            TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()


def _run(client, td, result, ent, actor) -> str:
    evidence = []
    if result == "PASS":
        ev = client.post("/api/evidence/upload", headers=H(actor), files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                         data={"entity_id": ent, "title": "Test output", "coverage_from": SEP.isoformat(),
                               "coverage_to": OCT.isoformat(), "validity_days": "400"})
        assert ev.status_code == 200, ev.text
        evidence = [ev.json()["evidence_id"]]
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": SEP.isoformat(), "window_from": SEP.isoformat(),
        "window_to": OCT.isoformat(), "result": result, "source_system": "probe", "evidence_ids": evidence})
    assert r.status_code == 200, r.text
    return r.json()["test_run_id"]


def _world(client, tenant=TA, ent=ENT, owner=OWNER, snapshot_by=CCO) -> dict:
    """An obligation proven by a PASS run, frozen in a snapshot, then contradicted by a later FAIL run."""
    subject = _subject(tenant, ent)
    lineage = _obligation(subject, tenant, ent)
    td = _control(client, subject, owner)
    passed = _run(client, td, "PASS", ent, owner)
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(snapshot_by), json=PERIOD)
    assert snap.status_code == 200 and snap.json()["state"] == "PROVEN", snap.text
    failed = _run(client, td, "FAIL", ent, owner)
    return {"lineage": lineage, "snapshot": snap.json(), "pass": passed, "fail": failed}


def _watermark() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tenant in (TA, TB):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
        db.flush()
        for tenant, ent in ((TA, ENT), (TA, ENT2), (TB, ENT_B)):
            db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=f"s-{ent}"))
            db.add(Subject(subject_id=f"s-{ent}", tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                           display_name=ent))
        for aid, tenant, roles in ((CCO, TA, ["CCO"]), (OWNER, TA, ["CONTROL_OWNER"]), (AUDITOR, TA, ["EXTERNAL_AUDITOR"]),
                                   (TSA, TA, ["TENANT_SECURITY_ADMIN"]), (READER, TA, []), (B_CCO, TB, ["CCO"]),
                                   (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    r = client.post("/api/role-assignments", headers=H(TSA), json={
        "principal_id": READER, "role_code": "INTERNAL_AUDIT", "scope_type": "LEGAL_ENTITY", "scope_id": ENT,
        "reason": "historical comparison entity probe"})
    assert r.status_code == 201, r.text
    S["one"] = _world(client)
    S["two"] = _world(client, ent=ENT2)
    S["b"] = _world(client, tenant=TB, ent=ENT_B, owner=B_OWNER, snapshot_by=B_CCO)
    return True


def _post(client, lineage, left, right, actor=CCO, period=PERIOD, **extra):
    return client.post(f"/api/obligations/{lineage}/historical-comparisons", headers=H(actor),
                       json={**period, "left": left, "right": right, **extra})


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _engine(lineage, left, right, instant: str, tenant=TA, actor=CCO) -> dict:
    """The engine's answer for the same inputs and the instant the route echoed, in the principal's own session."""
    from fastapi.encoders import jsonable_encoder
    from app.db import SessionLocal, set_principal
    from app.engines.assurance_comparison import compare
    with SessionLocal() as db:
        set_principal(db, tenant, actor)
        try:
            spec = lambda side: ({"record_seq": side["record_cutoff_seq"]} if "record_cutoff_seq" in side  # noqa: E731
                                  else side)  # the API's side, as the engine takes it
            return jsonable_encoder(compare(db, tenant_id=tenant, obligation_lineage_id=lineage, period_from=SEP,
                                            period_to=OCT, left=spec(left), right=spec(right), actor_id=actor,
                                            evaluated_at=datetime.fromisoformat(instant.replace("Z", "+00:00"))))
        finally:
            db.rollback()


# ------------------------------------------------------------------------------------------------------------ compare
def test_audit_compare_a_frozen_snapshot_with_the_current_reconstruction(client, org):
    w = S["one"]
    now = _watermark()
    body = _ok(_post(client, w["lineage"], {"snapshot_id": w["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": now}))
    assert body["valid_period"] == {"kind": "INSTANT_RANGE", "from": SEP.isoformat(), "until_exclusive": OCT.isoformat()}
    left, right = body["left"], body["right"]
    assert (left["snapshot_id"], left["known_at"]["record_seq"], left["state"]) == (
        w["snapshot"]["snapshot_id"], w["snapshot"]["record_cutoff_seq"], "PROVEN")
    assert (right["snapshot_id"], right["known_at"]["record_seq"], right["perspective"], right["state"]) == (
        None, now, "RECONSTRUCTED_NOW", "FAILING")
    assert body["outcome"]["state"] == {"left": "PROVEN", "right": "FAILING", "changed": True}
    added = body["dependencies"]["classes"]["test_runs"]["added"]
    assert [a["id"] for a in added] == [w["fail"]] and added[0]["first_recorded_seq"] > left["known_at"]["record_seq"]
    # TIM §12 classes stay unassigned: null with the reason, never an empty list.
    assert body["differences"] is None and "TIM-001 §12" in body["differences_note"]


def test_the_route_answers_exactly_what_the_engine_computes(client, org):
    w = S["one"]
    cases = [({"snapshot_id": w["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": _watermark()}),
             ({"record_cutoff_seq": w["snapshot"]["record_cutoff_seq"]}, {"record_cutoff_seq": _watermark()}),
             ({"snapshot_id": w["snapshot"]["snapshot_id"]}, {"snapshot_id": w["snapshot"]["snapshot_id"]})]
    for left, right in cases:
        body = _ok(_post(client, w["lineage"], left, right))
        assert body == _engine(w["lineage"], left, right, body["evaluation_instant"]), (left, right)


def test_the_snapshot_and_its_own_cutoff_compare_as_identical(client, org):
    w = S["one"]
    body = _ok(_post(client, w["lineage"], {"snapshot_id": w["snapshot"]["snapshot_id"]},
                     {"record_cutoff_seq": w["snapshot"]["record_cutoff_seq"]}))
    assert body["outcome"]["state"]["changed"] is False
    assert not any(t["changed"] for t in body["outcome"]["timeline"])
    assert all(not d["added"] and not d["removed"] for d in body["dependencies"]["classes"].values())


def test_comparing_writes_nothing(client, org):
    from test_authorization import db_fingerprint
    w = S["one"]
    before = db_fingerprint()
    for right in ({"record_cutoff_seq": _watermark()}, {"snapshot_id": w["snapshot"]["snapshot_id"]}):
        _ok(_post(client, w["lineage"], {"record_cutoff_seq": w["snapshot"]["record_cutoff_seq"]}, right))
    _ok(_post(client, w["lineage"], {"snapshot_id": w["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": _watermark()},
              actor=READER))
    assert db_fingerprint() == before  # no snapshot persisted, no issue raised for the FAILING reconstruction


# ------------------------------------------------------------------------------------------------------------ requests
@pytest.mark.parametrize("left, right, extra, status", [
    ({"snapshot_id": "x", "record_cutoff_seq": 1}, {"record_cutoff_seq": 1}, {}, 422),       # both on one side
    ({}, {"record_cutoff_seq": 1}, {}, 422),                                          # neither
    ({"record_cutoff_seq": -1}, {"record_cutoff_seq": 1}, {}, 422),                          # negative cutoff
    ({"record_cutoff_seq": "5"}, {"record_cutoff_seq": 1}, {}, 422),                         # not a strict integer
    ({"record_cutoff_seq": True}, {"record_cutoff_seq": 1}, {}, 422),
    ({"record_cutoff_seq": 1, "perspective": "CURRENT"}, {"record_cutoff_seq": 1}, {}, 422),  # unknown side field
    ({"record_cutoff_seq": 1}, {"record_cutoff_seq": 1}, {"as_of": "2026-09-01"}, 422),      # unknown body field
    ({"snapshot_id": "no-such-snapshot"}, {"record_cutoff_seq": 1}, {}, 404),
])
def test_requests_are_validated(client, org, left, right, extra, status):
    r = _post(client, S["one"]["lineage"], left, right, **extra)
    assert r.status_code == status, r.text


def test_refusals_for_time_and_scope(client, org):
    one, two = S["one"], S["two"]
    future = _post(client, one["lineage"], {"record_cutoff_seq": 1}, {"record_cutoff_seq": _watermark() + 1000})
    assert future.status_code == 422 and future.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"
    empty = _post(client, one["lineage"], {"record_cutoff_seq": 1}, {"record_cutoff_seq": 1},
                  period={"period_from": OCT.isoformat(), "period_to": SEP.isoformat()})
    assert empty.status_code == 422
    other_obligation = _post(client, one["lineage"], {"snapshot_id": two["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": 1})
    assert other_obligation.status_code == 422, other_obligation.text
    other_period = _post(client, one["lineage"], {"snapshot_id": one["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": 1},
                         period={"period_from": SEP.isoformat(), "period_to": "2026-09-20T00:00:00+00:00"})
    assert other_period.status_code == 422, other_period.text


# ------------------------------------------------------------------------------------------------------------ access
def test_another_organisations_obligation_and_snapshot_are_not_found(client, org):
    from test_authorization import db_fingerprint
    one, b_world = S["one"], S["b"]
    before = db_fingerprint()
    assert _post(client, one["lineage"], {"record_cutoff_seq": 1}, {"record_cutoff_seq": 2}, actor=B_CCO).status_code == 404
    # A's snapshot against B's own obligation: unknown to B.
    r = _post(client, b_world["lineage"], {"snapshot_id": one["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": 1}, actor=B_CCO)
    assert r.status_code == 404, r.text
    assert _post(client, b_world["lineage"], {"snapshot_id": b_world["snapshot"]["snapshot_id"]},
                 {"record_cutoff_seq": _watermark()}, actor=B_CCO).status_code == 200
    assert db_fingerprint() == before


def test_an_entity_scoped_reader_compares_its_own_entitys_obligations_only(client, org):
    one, two = S["one"], S["two"]
    assert _post(client, one["lineage"], {"snapshot_id": one["snapshot"]["snapshot_id"]},
                 {"record_cutoff_seq": _watermark()}, actor=READER).status_code == 200
    assert _post(client, two["lineage"], {"snapshot_id": two["snapshot"]["snapshot_id"]},
                 {"record_cutoff_seq": _watermark()}, actor=READER).status_code == 404


def test_access_is_the_obligation_previews(client, org):
    from test_authorization import db_fingerprint, denials
    one = S["one"]
    args = ({"snapshot_id": one["snapshot"]["snapshot_id"]}, {"record_cutoff_seq": _watermark()})
    assert _post(client, one["lineage"], *args, actor=AUDITOR).status_code == 200  # WF-STA-05: the auditor compares
    before, n = db_fingerprint(), denials(TSA)
    assert _post(client, one["lineage"], *args, actor=TSA).status_code == 403
    assert denials(TSA) == n + 1
    assert client.post(f"/api/obligations/{one['lineage']}/historical-comparisons",
                       json={**PERIOD, "left": args[0], "right": args[1]}).status_code == 401
    assert db_fingerprint() == before
