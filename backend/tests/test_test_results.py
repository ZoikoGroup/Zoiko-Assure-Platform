"""Governed test-result ingestion and its consumption by control evaluation (app/engines/test_results.py,
POST /api/test-runs, engines/assurance.py evaluate_control).

Requirements exercised: ZA-STA-001 §01 (TestRun result PASS / FAIL / ERROR / INCONCLUSIVE / SKIPPED), §04 (result
treatment: PASS only with evidence/provenance/coverage; FAIL decisive; ERROR / INCONCLUSIVE not a pass; SKIPPED "Not a
pass. Allowed only with reason"), §05 (CONFLICTING / STALE evidence), §17 closure (a retest is a new committed result),
STA-REQ-008; ZA-DOM-001 §7.1 (TestRun immutable; source provenance and evaluated period required); ZA-DAT-001 §07,
DAT-REQ-035; ZA-TIM-001 §02/§03 (half-open windows; record time is server-assigned); ZA-TEN-001 §14 (no cross-entity
proof without a governed shared reference); ZA-API-001 §08 (Idempotency-Key), API-REQ-008/014.

Every test builds its own organisation, controls and evidence; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-tr-{TAG}", f"tenant-trb-{TAG}"
E1, S1, E2, S2 = f"entity-tr1-{TAG}", f"subj-tr1-{TAG}", f"entity-tr2-{TAG}", f"subj-tr2-{TAG}"
EB, SB = f"entity-trb-{TAG}", f"subj-trb-{TAG}"
OWNER, CCO, B_OWNER = f"tr-owner-{TAG}", f"tr-cco-{TAG}", f"trb-owner-{TAG}"
KEY = "C_ACCESS_REVIEW"


@pytest.fixture(scope="module")
def org(client):
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
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _control(client, entity=E1, actor=OWNER, tenant=TA):
    """A fresh control implementation (one required manual test) on a new system of ``entity``; returns (ci, td)."""
    from app.db import AdminSession
    from app.models import ControlTestPolicy, Subject, TestDefinition
    sid = f"sys-tr-{secrets.token_hex(6)}"
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=entity, subject_type="AI_ASSET", display_name=sid))
        db.commit()
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": KEY, "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "access review test", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalars().first()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalars().first()
    return ci, td.test_definition_id


@pytest.fixture(scope="module")
def target(client, org):
    return _control(client)


def _evidence(client, entity=E1, actor=OWNER, validity_days=120):
    r = client.post("/api/evidence/upload", headers=H(actor), files={"file": ("e.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Test evidence", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": str(validity_days)})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _body(td, result="PASS", start="2026-09-01", end="2026-10-01", **extra):
    return {"test_definition_id": td, "occurred_at": f"{start}T01:00:00Z", "window_from": f"{start}T00:00:00Z",
            "window_to": f"{end}T00:00:00Z", "result": result, "source_system": "probe", **extra}


def _post(client, body, actor=OWNER, key=None):
    headers = {**H(actor), **({"Idempotency-Key": key} if key else {})}
    return client.post("/api/test-runs", headers=headers, json=body)


def _evaluate(client, ci, start="2026-09-01", end="2026-10-01"):
    r = client.post(f"/api/controls/{ci}/evaluate", headers=H(OWNER),
                    json={"window_from": f"{start}T00:00:00Z", "window_to": f"{end}T00:00:00Z"})
    assert r.status_code == 200, r.text
    return r.json()


def _count(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalar()


# ------------------------------------------------------------------------------------------------ creation
def test_a_valid_result_is_recorded_with_provenance_audit_and_outbox(client, org):
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent
    ci, td = _control(client)
    r = _post(client, _body(td, "FAIL", external_status="VENDOR_FAIL", detail="vendor export 42"))
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["result"], run["source_system"], run["external_status"], run["tenant_id"]) == ("FAIL", "probe", "VENDOR_FAIL", TA)
    with AdminSession() as db:
        [a] = db.execute(select(AuditEvent).where(AuditEvent.action == "test_run.record",
                                                  AuditEvent.object_id == run["test_run_id"])).scalars().all()
        assert a.tenant_id == TA and ci in a.reason and "VENDOR_FAIL" in a.reason
        [o] = db.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == run["test_run_id"])).scalars().all()
        assert o.event_type == "test.run_recorded" and o.record_seq == run["record_seq"]
    runs = client.get(f"/api/controls/{ci}", headers=H(OWNER)).json()["tests"][0]["runs"]
    assert [x["test_run_id"] for x in runs] == [run["test_run_id"]]  # historical results stay visible


@pytest.mark.parametrize("change", [
    {"result": "PASSED"}, {"result": "pass"}, {"result": None},                     # GLS codes only
    {"source_system": ""}, {"source_system": "   "}, {"source_system": None},      # provenance required
    {"external_status": "x" * 65}, {"source_system": "s" * 81},                    # never a database error
    {"result": "SKIPPED"}, {"result": "SKIPPED", "detail": "  "},                   # SKIPPED needs a reason
    {"window_to": "2026-09-01T00:00:00Z"}, {"window_to": "2026-08-01T00:00:00Z"},  # empty / inverted window
    {"occurred_at": "not a date"}, {"state": "OPERATING"}, {"test_definition_id": ""},
])
def test_malformed_results_are_refused_and_write_nothing(client, org, target, change):
    from app.models import TestRun
    td = target[1]
    before = _count(TestRun, tenant_id=TA)
    body = {**_body(td), **change}
    if change.get("source_system") is None and "source_system" in change:
        body.pop("source_system")
    assert _post(client, body).status_code == 422
    assert _count(TestRun, tenant_id=TA) == before


def test_duplicate_evidence_references_are_refused(client, org):
    ci, td = _control(client)
    e = _evidence(client)
    r = _post(client, _body(td, evidence_ids=[e, e]))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "DUPLICATE_EVIDENCE"


def test_references_must_belong_to_the_organisation_and_the_controls_entity(client, org):
    ci, td = _control(client)
    b_ci, b_td = _control(client, entity=EB, actor=B_OWNER, tenant=TB)
    other_entity = _evidence(client, entity=E2)
    b_evidence = _evidence(client, entity=EB, actor=B_OWNER)
    cases = [(_body(b_td), 404, "TEST_DEFINITION_NOT_FOUND"),          # another organisation's test
             (_body("no-such-test"), 404, "TEST_DEFINITION_NOT_FOUND"),
             (_body(td, evidence_ids=[b_evidence]), 404, "EVIDENCE_NOT_FOUND"),
             (_body(td, evidence_ids=["no-such-evidence"]), 404, "EVIDENCE_NOT_FOUND"),
             (_body(td, evidence_ids=[other_entity]), 422, "EVIDENCE_SCOPE_MISMATCH"),  # TEN §14
             (_body(td, source_run_id="no-such-run"), 404, "SOURCE_RUN_NOT_FOUND")]
    for body, status, code in cases:
        r = _post(client, body)
        assert r.status_code == status and r.json()["detail"]["error"] == code, (code, r.text)
    assert _post(client, _body(td, evidence_ids=[_evidence(client)])).status_code == 200  # same entity: accepted


def _source_run(entity, tenant=TA):
    """A source run of ``entity`` in ``tenant``: a copy of a seeded run's row (its contract-version foreign keys are real
    since migration 0013) under a new ID and record sequence, written below the API as a connector import would."""
    from app.db import AdminSession
    from app.models import SourceRun, new_id
    from app.platform import next_record_seq
    with AdminSession() as db:
        seed = db.execute(select(SourceRun).order_by(SourceRun.record_seq, SourceRun.source_run_id)).scalars().first()
        assert seed is not None, "the seeded fixture provides at least one source run"
        row = {c.key: getattr(seed, c.key) for c in SourceRun.__mapper__.column_attrs}
        row.update(source_run_id=new_id(), tenant_id=tenant, entity_id=entity, evidence_ids=[],
                   record_seq=next_record_seq(db))
        db.add(SourceRun(**row))
        db.commit()
        return row["source_run_id"]


def _writes(key=None) -> dict:
    """Everything a TestRun submission can write: runs, their audit and outbox rows, idempotency records and the cell
    record sequence (any committed write in this cell advances it)."""
    from app.db import AdminSession
    from app.models import AuditEvent, IdempotencyRecord, OutboxEvent, TestRun
    from app.platform import current_watermark
    with AdminSession() as db:
        seq = current_watermark(db)["record_seq"]
    return {"runs": _count(TestRun), "audit": _count(AuditEvent, action="test_run.record"),
            "outbox": _count(OutboxEvent, event_type="test.run_recorded"),
            "idempotency": _count(IdempotencyRecord, idempotency_key=key) if key else 0, "record_seq": seq}


def test_a_source_run_of_the_controls_own_entity_is_accepted(client, org):
    ci, td = _control(client)
    run_id = _source_run(E1)
    r = _post(client, _body(td, source_run_id=run_id))
    assert r.status_code == 200, r.text
    assert (r.json()["source_run_id"], r.json()["tenant_id"]) == (run_id, TA)


def test_a_source_run_of_another_entity_of_the_same_organisation_is_refused_and_writes_nothing(client, org):
    """The F1 defect: entity E2's run cannot prove a control of entity E1 (TEN-REQ-008; TEN §14 shared proof only
    through a governed SharedEvidenceReference, which does not exist). Structured 422, nothing written, with or without
    an Idempotency-Key, and the key stays usable."""
    ci, td = _control(client)
    other = _source_run(E2)  # same organisation TA, different legal entity
    key = f"scope-{secrets.token_hex(8)}"
    for k in (None, key):
        before = _writes(key)
        r = _post(client, _body(td, source_run_id=other), key=k)
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == {
            "error": "SOURCE_RUN_SCOPE_MISMATCH", "source_run_id": other,
            "message": "A source run of another legal entity cannot prove this control: cross-entity proof needs a "
                       "governed shared-evidence reference (ZA-TEN-001 §14)."}
        assert _writes(key) == before  # no TestRun, audit, outbox, idempotency record or record sequence
    ok = _post(client, _body(td, source_run_id=_source_run(E1)), key=key)  # the refused request did not claim the key
    assert ok.status_code == 200, ok.text
    assert _count_runs_citing(other) == 0


def _count_runs_citing(source_run_id) -> int:
    from app.models import TestRun
    return _count(TestRun, source_run_id=source_run_id)


def test_another_organisations_or_a_missing_source_run_stays_not_found_and_writes_nothing(client, org):
    ci, td = _control(client)
    b_run = _source_run(EB, tenant=TB)  # exists, but in organisation TB
    for run_id in (b_run, "no-such-run"):
        before = _writes()
        r = _post(client, _body(td, source_run_id=run_id))
        assert r.status_code == 404, r.text
        # The same refusal for both: nothing tells the caller that TB's run exists (no ID echoed, no entity hint).
        assert r.json()["detail"] == {"error": "SOURCE_RUN_NOT_FOUND", "message": "source run not found"}
        assert _writes() == before
    assert _count_runs_citing(b_run) == 0


def test_idempotency_is_unchanged_by_the_source_run_scope_check(client, org):
    from app.models import TestRun
    ci, td = _control(client)
    own, other = _source_run(E1), _source_run(E2)
    key = f"scope-idem-{secrets.token_hex(8)}"
    first = _post(client, _body(td, source_run_id=own), key=key)
    again = _post(client, _body(td, source_run_id=own), key=key)  # replay: the original run, nothing new
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert _count(TestRun, test_definition_id=td) == 1
    # The same key with a different request is still the idempotency conflict, decided before any validation.
    before = _writes(key)
    clash = _post(client, _body(td, source_run_id=other), key=key)
    assert clash.status_code == 409 and clash.json()["detail"]["error"] == "idempotency_conflict"
    assert _writes(key) == before


def test_access_needs_authority_and_stays_in_the_organisation(client, org):
    from app.models import TestRun
    ci, td = _control(client)
    before = _count(TestRun, tenant_id=TA)
    assert client.post("/api/test-runs", json=_body(td)).status_code == 401
    for actor in ("actor-ext-auditor", "actor-audit", CCO):  # read-only or no submit_evidence permission
        assert _post(client, _body(td), actor=actor).status_code == 403, actor
    assert _post(client, _body(td), actor=B_OWNER).status_code == 404  # another organisation's test definition
    assert _count(TestRun, tenant_id=TA) == before


# ------------------------------------------------------------------------------------------------ lifecycle
def test_results_are_immutable_and_corrections_are_new_runs(client, org):
    ci, td = _control(client)
    first = _post(client, _body(td, "FAIL")).json()
    for method in ("PUT", "PATCH", "DELETE"):
        r = client.request(method, f"/api/test-runs/{first['test_run_id']}", headers=H(OWNER), json={"result": "PASS"})
        assert r.status_code in (404, 405), method  # no route rewrites or removes a result
    corrected = _post(client, _body(td, "PASS", detail="Re-run after configuration fix")).json()
    runs = client.get(f"/api/controls/{ci}", headers=H(OWNER)).json()["tests"][0]["runs"]
    assert {x["test_run_id"]: x["result"] for x in runs} == {first["test_run_id"]: "FAIL", corrected["test_run_id"]: "PASS"}
    assert corrected["record_seq"] > first["record_seq"]


# ------------------------------------------------------------------------------------------------ evaluation
def test_only_an_evidenced_pass_can_prove_a_control(client, org):
    """STA §04: ERROR / INCONCLUSIVE / SKIPPED never prove a pass; a PASS without evidence is not proof; a FAIL is
    decisive over a PASS in the same window; the evaluation keeps the exact runs it used."""
    from app.db import AdminSession
    from app.models import ControlEvaluation
    cases = [("SKIPPED", {"detail": "Maintenance window"}, "NOT_EVALUATED", "TEST_NOT_RUN"),
             ("ERROR", {}, "NOT_EVALUATED", "TEST_ERROR"),
             ("INCONCLUSIVE", {}, "NOT_EVALUATED", "TEST_INCONCLUSIVE"),
             ("PASS", {}, "NOT_EVALUATED", "MISSING_EVIDENCE")]
    for result, extra, state, reason in cases:
        ci, td = _control(client)
        run = _post(client, _body(td, result, **extra)).json()
        ev = _evaluate(client, ci)
        assert (ev["state"], reason in ev["reason_codes"]) == (state, True), (result, ev)
        with AdminSession() as db:
            stored = db.get(ControlEvaluation, ev["evaluation_id"])
            assert stored.inputs["deps"]["runs"] == [run["test_run_id"]]
    ci, td = _control(client)
    _post(client, _body(td, "PASS", evidence_ids=[_evidence(client)]))
    assert _evaluate(client, ci)["state"] == "OPERATING"
    _post(client, _body(td, "FAIL"))
    assert _evaluate(client, ci)["state"] == "FAILING"  # decisive failure wins


def test_conflicting_or_stale_evidence_never_proves_a_control(client, org):
    ci, td = _control(client)
    e1, e2 = _evidence(client), _evidence(client)
    assert client.post("/api/evidence-conflicts", headers=H(OWNER), json={
        "member_evidence_ids": [e1, e2], "description": "Disagreeing records"}).status_code == 200
    _post(client, _body(td, "PASS", evidence_ids=[e1]))
    assert _evaluate(client, ci)["state"] == "REQUIRES_REVIEW"
    ci2, td2 = _control(client)
    stale = _evidence(client, validity_days=1)  # expires long before the window below starts
    _post(client, _body(td2, "PASS", "2027-06-01", "2027-07-01", evidence_ids=[stale]))
    ev = _evaluate(client, ci2, "2027-06-01", "2027-07-01")
    assert ev["state"] == "NOT_EVALUATED" and "STALE_EVIDENCE" in ev["reason_codes"]


def test_a_retest_counts_by_record_time_not_by_claimed_time(client, org):
    """STA §17 / WRK §17: closure needs a TestRun committed after remediation started. A result recorded before
    remediation does not verify closure, even if it is PASS and evidenced; one recorded after does, even if its
    occurred_at is backdated (record time is server-assigned, TIM §03)."""
    ci, td = _control(client)
    _post(client, _body(td, "FAIL"))
    _evaluate(client, ci)
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        [iid] = db.execute(select(Issue.issue_id).where(Issue.dedupe_key.like(f"%:{ci}:%"))).scalars().all()
    early = _post(client, _body(td, "PASS", "2026-10-01", "2026-11-01", evidence_ids=[_evidence(client)])).json()
    for step, body in (("assign", {"owner_actor_id": OWNER, "reason": "owner"}), ("transition", {"state": "TRIAGED"}),
                       ("transition", {"state": "IN_REMEDIATION", "reason": "fixing"}),
                       ("transition", {"state": "PENDING_VERIFICATION", "reason": "fixed"})):
        assert client.post(f"/api/issues/{iid}/{step}", headers=H(OWNER), json=body).status_code == 200
    stale_proof = _evaluate(client, ci, "2026-10-01", "2026-11-01")
    assert stale_proof["state"] == "OPERATING"
    r = client.post(f"/api/issues/{iid}/close", headers=H(OWNER), json={
        "verification_control_evaluation_id": stale_proof["evaluation_id"], "reason": "early pass"})
    assert r.status_code == 409 and "RETEST_REQUIRED" in [p["code"] for p in r.json()["detail"]["problems"]]
    late = _post(client, {**_body(td, "PASS", "2026-10-01", "2026-11-01", evidence_ids=[_evidence(client)]),
                          "occurred_at": "2026-10-02T00:00:00Z"}).json()  # backdated claim, new record time
    assert late["record_seq"] > early["record_seq"]
    proof = _evaluate(client, ci, "2026-10-01", "2026-11-01")
    r = client.post(f"/api/issues/{iid}/close", headers=H(OWNER), json={
        "verification_control_evaluation_id": proof["evaluation_id"], "reason": "retest passed"})
    assert r.status_code == 200 and r.json()["state"] == "CLOSED", r.text


# ------------------------------------------------------------------------------------------------ idempotency
def test_a_retry_with_the_same_key_returns_the_original_result(client, org):
    from app.models import AuditEvent, IdempotencyRecord, TestRun
    ci, td = _control(client)
    key = f"retry-{secrets.token_hex(8)}"
    first = _post(client, _body(td, "FAIL"), key=key)
    second = _post(client, _body(td, "FAIL"), key=key)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert _count(TestRun, test_definition_id=td) == 1
    assert _count(AuditEvent, action="test_run.record", object_id=first.json()["test_run_id"]) == 1
    conflict = _post(client, _body(td, "PASS"), key=key)  # same key, different request
    assert conflict.status_code == 409 and conflict.json()["detail"]["error"] == "idempotency_conflict"
    assert _post(client, _body(td, "FAIL"), key=f"other-{secrets.token_hex(8)}").status_code == 200  # a new key: new run
    assert _count(TestRun, test_definition_id=td) == 2
    assert _post(client, _body(td), key="k" * 201).status_code == 422
    b_ci, b_td = _control(client, entity=EB, actor=B_OWNER, tenant=TB)  # keys are scoped to the organisation
    assert _post(client, _body(b_td, "FAIL"), actor=B_OWNER, key=key).status_code == 200
    assert _count(IdempotencyRecord, idempotency_key=key) == 2


def test_a_failure_mid_write_leaves_nothing_and_the_key_reusable(client, org, monkeypatch):
    from app.engines import test_results
    from app.models import IdempotencyRecord, TestRun
    ci, td = _control(client)
    key = f"rollback-{secrets.token_hex(8)}"
    real = test_results.emit

    def broken(db, **kw):
        raise RuntimeError("outbox write failed")
    monkeypatch.setattr(test_results, "emit", broken)
    with pytest.raises(RuntimeError):
        _post(client, _body(td), key=key)
    monkeypatch.setattr(test_results, "emit", real)
    assert _count(TestRun, test_definition_id=td) == 0 and _count(IdempotencyRecord, idempotency_key=key) == 0
    assert _post(client, _body(td), key=key).status_code == 200
    assert _count(TestRun, test_definition_id=td) == 1


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _race(*bodies, key):
    from datetime import datetime

    from app.db import SessionLocal, set_principal
    from app.engines import test_results
    from app.models import Actor
    barrier, out = threading.Barrier(len(bodies)), []

    def worker(body):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                actor = db.get(Actor, OWNER)
                b = {**body, **{k: datetime.fromisoformat(body[k].replace("Z", "+00:00"))
                                for k in ("occurred_at", "window_from", "window_to")},
                     "external_status": None, "evidence_ids": [], "source_run_id": None, "detail": body.get("detail")}
                barrier.wait()
                run, replayed = test_results.record_test_run(db, tenant_id=TA, actor=actor, body=b, idempotency_key=key)
                db.commit()
                out.append((run.test_run_id, replayed))
        except HTTPException as e:
            out.append((e.status_code, e.detail.get("error") if isinstance(e.detail, dict) else e.detail))
        except Exception as e:  # noqa: BLE001
            out.append((repr(e), None))
    threads = [threading.Thread(target=worker, args=(b,)) for b in bodies]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_concurrent_identical_submissions_create_one_result(client, org, pg):
    from app.models import TestRun
    ci, td = _control(client)
    out = _race(_body(td, "FAIL"), _body(td, "FAIL"), _body(td, "FAIL"), key=f"race-{secrets.token_hex(8)}")
    assert len({r[0] for r in out}) == 1 and sorted(r[1] for r in out) == [False, True, True], out
    assert _count(TestRun, test_definition_id=td) == 1


def test_concurrent_conflicting_submissions_with_one_key_record_one(client, org, pg):
    from app.models import TestRun
    ci, td = _control(client)
    out = _race(_body(td, "FAIL"), _body(td, "PASS"), key=f"clash-{secrets.token_hex(8)}")
    assert sorted(str(r[0]) for r in out if r[0] == 409) == ["409"] and len([r for r in out if r[0] != 409]) == 1, out
    assert _count(TestRun, test_definition_id=td) == 1
