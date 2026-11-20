"""Open the exact TestRun: GET /test-runs/{test_run_id} (read-only).

Controlling text:
  * ZA-STA-001 §21 WF-STA-03 Evidence trace: "Leaf opens exact ControlEvaluation TestRun Evidence references, without
    exposing unauthorized payload"; WF-STA-02 Test table (required/optional tests, result, source, run time, evidence
    refs); §19 ControlEvaluationInput "exact TestRun/Evidence revision refs".
  * ZA-DOM-001 §7.1 TestRun: "One execution/imported result. Immutable; source provenance and evaluated period
    required"; STA §17 a retest or correction is a new result, never an edit of the old one.
  * QA-REQ-019 (cross-tenant reads), IAM-REQ-009/010 (server-resolved entity scope).

Pinned here: the record named is the record returned, field for field as stored, never a later run, retest or
correction of the same test; every run a stored ControlEvaluation names opens to that run; a related record that
cannot be found is reported missing, never substituted; evidence and source runs are referenced by ID only; another
organisation's run is not found, an entity-scoped reader reaches its own entity's runs only; nothing is written.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-trv-{TAG}", f"tenant-trvb-{TAG}"
E1, S1, E2, S2, EB, SB = (f"{k}-trv-{TAG}" for k in ("entity1", "subj1", "entity2", "subj2", "entityb", "subjb"))
OWNER, CCO, AUDITOR, TSA, READER, B_OWNER = (f"trv-{r}-{TAG}" for r in ("owner", "cco", "aud", "tsa", "ent", "b-owner"))
KEY = "C_ACCESS_REVIEW"
TOP_KEYS = {"test_run", "test_definition", "policy", "implementation", "definition", "subject"}
S: dict = {}


def _control(client, entity, actor=OWNER, tenant=TA):
    """A fresh control implementation (one required manual test) on a new system of `entity`; returns (ci, td)."""
    from app.db import AdminSession
    from app.models import ControlTestPolicy, Subject, TestDefinition
    sid = f"sys-trv-{secrets.token_hex(6)}"
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=entity, subject_type="AI_ASSET", display_name=sid))
        db.commit()
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": KEY, "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "access review test", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalars().one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalars().one()
    return ci, td.test_definition_id


def _evidence(client, entity, actor=OWNER):
    r = client.post("/api/evidence/upload", headers=H(actor), files={"file": ("e.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Run evidence", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _run(client, td, result, actor=OWNER, **extra) -> dict:
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": "2026-09-01T01:00:00Z", "window_from": "2026-09-01T00:00:00Z",
        "window_to": "2026-10-01T00:00:00Z", "result": result, "source_system": "probe", **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def world(client):
    """Organisation A: control `one` (entity E1) with a FAIL run and then a PASS retest of the same test over the same
    window (with evidence); control `two` (entity E2) with one run. Organisation B: control `b` with one run."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        for tid, eid, sid in ((TA, E1, S1), (TA, E2, S2), (TB, EB, SB)):
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{eid} AG", country="DE", home_cell_id=CELL_ID,
                               subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=eid))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (AUDITOR, TA, ["EXTERNAL_AUDITOR"]),
                                (TSA, TA, ["TENANT_SECURITY_ADMIN"]), (READER, TA, []), (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    r = client.post("/api/role-assignments", headers=H(TSA), json={
        "principal_id": READER, "role_code": "INTERNAL_AUDIT", "scope_type": "LEGAL_ENTITY", "scope_id": E1,
        "reason": "test run entity probe"})
    assert r.status_code == 201, r.text
    S["one"] = _control(client, E1)
    S["fail"] = _run(client, S["one"][1], "FAIL", detail="Two dormant accounts found")
    S["evidence"] = _evidence(client, E1)
    S["pass"] = _run(client, S["one"][1], "PASS", detail="Re-run after removing the accounts",
                     evidence_ids=[S["evidence"]])
    S["two"] = _control(client, E2)
    S["two_run"] = _run(client, S["two"][1], "PASS")
    S["b"] = _control(client, EB, actor=B_OWNER, tenant=TB)
    S["b_run"] = _run(client, S["b"][1], "FAIL", actor=B_OWNER)
    return True


def _get(client, rid, actor=CCO):
    return client.get(f"/api/test-runs/{rid}", headers=H(actor))


def _ok(r) -> dict:
    assert r.status_code == 200, r.text
    return r.json()


def _stored(model, key):
    from app.db import AdminSession
    from app.platform import to_dict
    with AdminSession() as db:
        row = db.get(model, key)
        return to_dict(row) if row is not None else None


# ------------------------------------------------------------------------------------------------------------ identity
def test_the_named_run_is_returned_exactly_as_stored_never_its_retest(client, world):
    from app.models import ControlDefinition, ControlImplementation, ControlTestPolicy, Subject, TestDefinition, TestRun
    fail, retest = _ok(_get(client, S["fail"]["test_run_id"])), _ok(_get(client, S["pass"]["test_run_id"]))
    assert set(fail) == TOP_KEYS
    assert fail["test_run"] == _stored(TestRun, S["fail"]["test_run_id"]) == S["fail"]
    assert retest["test_run"] == _stored(TestRun, S["pass"]["test_run_id"]) == S["pass"]
    # Same test, same window: the earlier FAIL stays the FAIL it recorded, though a later PASS retest exists.
    assert (fail["test_run"]["result"], retest["test_run"]["result"]) == ("FAIL", "PASS")
    assert retest["test_run"]["record_seq"] > fail["test_run"]["record_seq"]
    assert fail["test_run"]["evidence_ids"] == [] and retest["test_run"]["evidence_ids"] == [S["evidence"]]
    # What it was recorded against, as stored.
    ci, td = S["one"]
    tdef = _stored(TestDefinition, td)
    assert fail["test_definition"] == tdef and tdef["required"] is True
    assert fail["policy"] == _stored(ControlTestPolicy, tdef["policy_version_id"])
    assert fail["implementation"] == _stored(ControlImplementation, ci)
    assert fail["definition"] == _stored(ControlDefinition, fail["implementation"]["control_definition_id"])
    assert fail["definition"]["key"] == KEY
    assert fail["subject"] == _stored(Subject, fail["implementation"]["subject_id"])


def test_every_run_a_stored_evaluation_names_opens_to_that_run(client, world):
    """WF-STA-03: control evaluation -> its exact TestRuns."""
    ci, _ = S["one"]
    ev = client.post(f"/api/controls/{ci}/evaluate", headers=H(OWNER),
                     json={"window_from": "2026-09-01T00:00:00Z", "window_to": "2026-10-01T00:00:00Z"})
    assert ev.status_code == 200 and ev.json()["evaluation_id"], ev.text
    stored = _ok(client.get(f"/api/control-evaluations/{ev.json()['evaluation_id']}", headers=H(CCO)))["evaluation"]
    named = stored["inputs"]["deps"]["runs"]
    assert sorted(named) == sorted([S["fail"]["test_run_id"], S["pass"]["test_run_id"]])
    recorded = {r["test_run_id"]: r for t in stored["inputs"]["tests"] for r in t["runs"]}
    for rid in named:
        run = _ok(_get(client, rid))["test_run"]
        assert run["test_run_id"] == rid
        assert (run["result"], run["source_system"]) == (recorded[rid]["result"], recorded[rid]["source_system"])


def test_the_same_run_reads_the_same_after_later_results(client, world):
    before = _ok(_get(client, S["fail"]["test_run_id"]))
    _run(client, S["one"][1], "ERROR", detail="Executor crashed")  # yet another later result of the same test
    assert _ok(_get(client, S["fail"]["test_run_id"])) == before


def test_no_route_rewrites_or_removes_a_run(client, world):
    from app.models import TestRun
    rid = S["fail"]["test_run_id"]
    before = _stored(TestRun, rid)
    for method in ("PUT", "PATCH", "DELETE"):
        r = client.request(method, f"/api/test-runs/{rid}", headers=H(OWNER), json={"result": "PASS"})
        assert r.status_code == 405, (method, r.status_code)
    assert _stored(TestRun, rid) == before


def test_a_related_record_that_cannot_be_found_is_reported_missing(client, world):
    """A run whose test definition cannot be followed: shown with the definition missing and nothing substituted;
    with no resolvable entity, an entity-scoped reader cannot reach it (fail closed)."""
    from app.db import AdminSession
    from app.models import TestRun, new_id
    from app.platform import next_record_seq
    with AdminSession() as db:
        row = {c.name: getattr(db.get(TestRun, S["fail"]["test_run_id"]), c.name) for c in TestRun.__table__.columns}
        rid = new_id()
        row.update(test_run_id=rid, test_definition_id="no-such-definition", record_seq=next_record_seq(db))
        db.add(TestRun(**row))
        db.commit()
    body = _ok(_get(client, rid))
    assert body["test_run"]["test_definition_id"] == "no-such-definition"
    assert body["test_definition"] == {"test_definition_id": "no-such-definition", "name": None, "required": None,
                                       "policy_version_id": None, "missing": True}
    assert (body["policy"], body["implementation"], body["definition"], body["subject"]) == (None, None, None, None)
    assert _get(client, rid, READER).status_code == 404


# ------------------------------------------------------------------------------------------------------------ access
def test_unknown_and_other_organisations_runs_are_not_found(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    assert _get(client, "no-such-run").status_code == 404
    assert _get(client, S["b_run"]["test_run_id"]).status_code == 404
    assert _get(client, S["fail"]["test_run_id"], B_OWNER).status_code == 404
    assert _ok(_get(client, S["b_run"]["test_run_id"], B_OWNER))["test_run"]["test_run_id"] == S["b_run"]["test_run_id"]
    assert db_fingerprint() == before


def test_an_entity_scoped_reader_reaches_its_own_entitys_runs_only(client, world):
    assert _ok(_get(client, S["fail"]["test_run_id"], READER))["implementation"]["entity_id"] == E1
    assert _get(client, S["two_run"]["test_run_id"], READER).status_code == 404


def test_read_authorization_follows_the_control_reads(client, world):
    from test_authorization import db_fingerprint, denials
    rid = S["fail"]["test_run_id"]
    # The external-auditor allowlist reads controls and their stored evaluations, so their runs too.
    assert _ok(_get(client, rid, AUDITOR))["test_run"]["test_run_id"] == rid
    before, n = db_fingerprint(), denials(TSA)
    assert _get(client, rid, TSA).status_code == 403  # a role administrator reads no assurance records
    assert denials(TSA) == n + 1
    assert client.get(f"/api/test-runs/{rid}").status_code == 401
    assert db_fingerprint() == before


def test_reads_write_nothing(client, world):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    for rid in (S["fail"]["test_run_id"], S["pass"]["test_run_id"], S["two_run"]["test_run_id"]):
        _ok(_get(client, rid))
    _ok(_get(client, S["fail"]["test_run_id"], READER))
    assert db_fingerprint() == before
