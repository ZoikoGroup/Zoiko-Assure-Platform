"""WF-STA-02 Control Evaluation: current/historical control state for an explicit window and retrieval of stored
evaluations (GET /api/controls/{ci_id}/state, GET /api/control-evaluations/{control_evaluation_id}).

Requirements exercised: ZA-STA-001 §18 ("GET /controls/{id}/state: Current/historical control state. Never accepts
manual state mutation"; "GET /control-evaluations/{id}: Retrieve immutable evaluation and authorized explanation.
Includes exact tests/evidence/policy version"), FR-CTL-005 (every control state is period-queryable and reproducible
from stored test/evidence revisions), STA-REQ-019/020/022/028/030, ZA-TIM-001 §11 correction join (records first
recorded after the cutoff are not read), §16 (no ambiguous historical query), TIM-REQ-017/019 (echo the resolved
watermark; never answer beyond it), WF-STA-03 (a snapshot leaf opens its exact ControlEvaluation), QA-REQ-019 and
IAM-REQ-010 (another organisation's or another entity's records answer as unknown). Every test builds its own records.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint, denials

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-csh-{TAG}", f"tenant-cshb-{TAG}"
ENT, ENT2, ENT_B = f"entity-csh-{TAG}", f"entity-csh2-{TAG}", f"entity-cshb-{TAG}"
CCO, OWNER, AUDIT, EXT, TSA = (f"csh-{r}-{TAG}" for r in ("cco", "owner", "audit", "ext", "tsa"))
EU_READER, OTHER_READER, B_CCO = f"csh-eu-{TAG}", f"csh-other-{TAG}", f"csh-b-cco-{TAG}"
KEY = "C_ICT_INCIDENT_PROCESS"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11))
STATE_KEYS = {"control_implementation_id", "window", "state", "reason_codes", "tests", "precedence_rule",
              "policy_version_id", "aggregation_mode", "automation_requirement", "dependency_digest", "evaluation_id",
              "evaluator_version", "record_watermark"}
EVALUATION_KEYS = {"evaluation", "implementation", "definition", "subject", "policy", "test_definitions"}


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, RoleAssignment, Subject, Tenant
    from app.platform import next_record_seq
    with AdminSession() as db:
        for tenant, ents in ((TA, (ENT, ENT2)), (TB, (ENT_B,))):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
            db.flush()
            for ent in ents:
                db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                                   home_cell_id=CELL_ID, subject_id=f"s-{ent}"))
                db.add(Subject(subject_id=f"s-{ent}", tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                               display_name=ent))
        for aid, tenant, roles in ((CCO, TA, ["CCO"]), (OWNER, TA, ["CONTROL_OWNER"]), (AUDIT, TA, ["INTERNAL_AUDIT"]),
                                   (EXT, TA, ["EXTERNAL_AUDITOR"]), (TSA, TA, ["TENANT_SECURITY_ADMIN"]),
                                   (EU_READER, TA, []), (OTHER_READER, TA, []), (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        for principal, ent in ((EU_READER, ENT), (OTHER_READER, ENT2)):  # INTERNAL_AUDIT for one legal entity only
            db.add(RoleAssignment(tenant_id=TA, principal_id=principal, role_code="INTERNAL_AUDIT",
                                  scope_type="LEGAL_ENTITY", scope_id=ent, status="ACTIVE", source="TEST",
                                  valid_from=datetime.now(timezone.utc) - timedelta(hours=1), reason="entity probe",
                                  record_seq=next_record_seq(db)))
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


def _control(client, subject, actor=OWNER):
    """A control with one required manual test: (control implementation, test definition)."""
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": KEY, "subject_id": subject, "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        return ci, db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                              ).scalar_one().test_definition_id


def _run(client, td, result, start=SEP, end=OCT, actor=OWNER, ent=ENT):
    evidence = []
    if result == "PASS":
        ev = client.post("/api/evidence/upload", headers=H(actor),
                         files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                         data={"entity_id": ent, "title": "Test output", "coverage_from": start.isoformat(),
                               "coverage_to": end.isoformat(), "validity_days": "400"})
        assert ev.status_code == 200, ev.text
        evidence = [ev.json()["evidence_id"]]
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": evidence})
    assert r.status_code == 200, r.text
    return r.json()["test_run_id"]


def _watermark():
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _state(client, ci, start=SEP, end=OCT, as_of=None, actor=CCO):
    params = {"window_from": start.isoformat(), "window_to": end.isoformat()}
    if as_of is not None:
        params["as_of_record_seq"] = as_of
    return client.get(f"/api/controls/{ci}/state", headers=H(actor), params=params)


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ------------------------------------------------------------------------------------------------ current state
def test_current_state_for_an_explicit_window_is_derived_echoed_and_stores_nothing(client, org):
    from app import codes
    from app.models import ControlEvaluation, Issue
    ci, td = _control(client, _subject())
    run = _run(client, td, "PASS")
    before, seq = db_fingerprint(), _watermark()
    r = _state(client, ci)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == STATE_KEYS
    assert (body["state"], body["reason_codes"], body["evaluation_id"], body["control_implementation_id"]) == (
        "OPERATING", [], None, ci)
    assert [_at(x) for x in body["window"]] == [SEP, OCT]
    assert body["record_watermark"] == {"cell_id": body["record_watermark"]["cell_id"], "record_seq": seq,
                                        "perspective": "CURRENT"}
    assert body["evaluator_version"] == codes.CONTROL_EVALUATOR_VERSION
    [test] = body["tests"]
    assert test["test_definition_id"] == td and [x["test_run_id"] for x in test["runs"]] == [run]
    assert db_fingerprint() == before  # a read: no evaluation stored, no issue, no sequence used
    assert _rows(ControlEvaluation, control_implementation_id=ci) == []
    assert not [i for i in _rows(Issue, tenant_id=TA) if ci in i.dedupe_key]


def test_a_failing_control_is_read_without_storing_an_evaluation_or_raising_an_issue(client, org):
    from app.models import ControlEvaluation, Issue
    ci, td = _control(client, _subject())
    _run(client, td, "FAIL")
    before = db_fingerprint()
    body = _state(client, ci).json()
    assert (body["state"], body["reason_codes"]) == ("FAILING", ["TEST_FAIL"])
    assert db_fingerprint() == before
    assert _rows(ControlEvaluation, control_implementation_id=ci) == []
    assert not [i for i in _rows(Issue, tenant_id=TA) if ci in i.dedupe_key]
    # The governed action still stores the evaluation and raises its issue (STA §17); the read never does.
    assert client.post(f"/api/controls/{ci}/evaluate", headers=H(CCO), json={
        "window_from": SEP.isoformat(), "window_to": OCT.isoformat()}).json()["state"] == "FAILING"
    assert len(_rows(ControlEvaluation, control_implementation_id=ci)) == 1
    assert len([i for i in _rows(Issue, tenant_id=TA) if ci in i.dedupe_key]) == 1


# ------------------------------------------------------------------------------------------------ history
def test_a_historical_read_ignores_records_first_recorded_after_its_cutoff(client, org):
    before_control = _watermark()
    ci, td = _control(client, _subject())
    no_run = _watermark()
    _run(client, td, "PASS")
    passing = _watermark()
    _run(client, td, "FAIL")
    now = _state(client, ci).json()
    then = _state(client, ci, as_of=passing).json()
    earlier = _state(client, ci, as_of=no_run).json()
    assert (now["state"], then["state"], earlier["state"]) == ("FAILING", "OPERATING", "NOT_EVALUATED")
    assert earlier["reason_codes"] == ["TEST_NOT_RUN"]
    assert [len(x["tests"][0]["runs"]) for x in (now, then, earlier)] == [2, 1, 0]
    assert (then["record_watermark"]["record_seq"], then["record_watermark"]["perspective"]) == (passing,
                                                                                                "AS_OF_RECORD_SEQ")
    assert len({now["dependency_digest"], then["dependency_digest"], earlier["dependency_digest"]}) == 3
    assert _state(client, ci, as_of=passing).json() == then  # the same question gets the same answer
    unknown = _state(client, ci, as_of=before_control)
    assert (unknown.status_code, unknown.json()) == (404, {"detail": "control not found as of requested record sequence"})


def test_a_test_policy_recorded_after_the_cutoff_is_not_read(client, org):
    from app.db import AdminSession
    from app.models import ControlTestPolicy
    from app.platform import next_record_seq
    ci, td = _control(client, _subject())
    _run(client, td, "PASS")
    valid_then = _watermark()
    with AdminSession() as db:  # a later policy version that cannot be evaluated (K_OF_N without K)
        db.add(ControlTestPolicy(tenant_id=TA, control_implementation_id=ci, version=2, aggregation_mode="K_OF_N",
                                 k_threshold=None, automation_requirement="NONE", record_seq=next_record_seq(db)))
        db.commit()
    then = _state(client, ci, as_of=valid_then)
    assert then.status_code == 200 and then.json()["state"] == "OPERATING", then.text
    now = _state(client, ci)  # STA §16 / STA-REQ-022: a configuration error, never a control state
    assert now.status_code == 409 and now.json()["detail"]["error"] == "ASSURANCE_CONFIGURATION_INVALID", now.text
    assert now.json()["detail"]["code"] == "CONTROL_TEST_POLICY_INVALID"


def test_a_stored_evaluation_is_reproduced_and_named_by_its_window_and_cutoff(client, org):
    from app.models import ControlEvaluation
    ci, td = _control(client, _subject())
    _run(client, td, "PASS")
    stored = client.post(f"/api/controls/{ci}/evaluate", headers=H(CCO),
                         json={"window_from": SEP.isoformat(), "window_to": OCT.isoformat()}).json()
    [row] = _rows(ControlEvaluation, control_implementation_id=ci)
    assert stored["evaluation_id"] == row.evaluation_id
    again = _state(client, ci, as_of=row.record_cutoff_seq).json()  # FR-CTL-005: reproducible from stored revisions
    assert (again["evaluation_id"], again["state"], again["dependency_digest"], again["reason_codes"]) == (
        row.evaluation_id, row.state, row.dependency_digest, row.reason_codes)
    assert again["policy_version_id"] == row.policy_version_id
    # Another window or another cutoff is another derivation: no stored evaluation is named for it.
    assert _state(client, ci, start=SEP, end=NOV, as_of=row.record_cutoff_seq).json()["evaluation_id"] is None
    assert _state(client, ci).json()["evaluation_id"] is None  # the watermark has moved past the stored cutoff


# ------------------------------------------------------------------------------------------------ stored evaluations
def test_a_stored_evaluation_is_returned_with_its_exact_policy_and_inputs(client, org):
    from app.models import ControlEvaluation, ControlImplementation, ControlTestPolicy
    subject = _subject()
    ci, td = _control(client, subject)
    run = _run(client, td, "PASS")
    client.post(f"/api/controls/{ci}/evaluate", headers=H(CCO),
                json={"window_from": SEP.isoformat(), "window_to": OCT.isoformat()})
    [row] = _rows(ControlEvaluation, control_implementation_id=ci)
    before = db_fingerprint()
    r = client.get(f"/api/control-evaluations/{row.evaluation_id}", headers=H(AUDIT))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == EVALUATION_KEYS
    ev = body["evaluation"]
    assert (ev["evaluation_id"], ev["state"], ev["record_cutoff_seq"], ev["dependency_digest"], ev["precedence_rule"],
            ev["evaluator_version"]) == (row.evaluation_id, "OPERATING", row.record_cutoff_seq, row.dependency_digest,
                                         row.precedence_rule, row.evaluator_version)
    assert [_at(ev["window_from"]), _at(ev["window_to"])] == [SEP, OCT]
    assert ev["inputs"]["deps"]["runs"] == [run] and ev["inputs"]["tests"][0]["evidence"][0]["condition"] == "CURRENT"
    [impl] = _rows(ControlImplementation, control_implementation_id=ci)
    [policy] = _rows(ControlTestPolicy, control_implementation_id=ci)
    assert body["implementation"]["control_implementation_id"] == ci and body["definition"]["key"] == KEY
    assert body["subject"]["subject_id"] == subject == impl.subject_id
    assert body["policy"]["policy_version_id"] == policy.policy_version_id == ev["policy_version_id"]
    assert [t["test_definition_id"] for t in body["test_definitions"]] == [td]
    assert db_fingerprint() == before


def test_a_snapshot_leaf_opens_its_exact_control_evaluation(client, org):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    subject = _subject()
    ci, td = _control(client, subject)
    _run(client, td, "PASS")
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root",
           "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": KEY}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")
                          ).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="p",
                                  attestation_id="p", lifecycle="ADOPTED", valid_from=T0,
                                  record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                       json={"period_from": SEP.isoformat(), "period_to": OCT.isoformat()}).json()
    assert snap["state"] == "PROVEN", snap
    [leaf] = [x for s in snap["segments"] for x in s["leaf_evaluations"]]
    r = client.get(f"/api/control-evaluations/{leaf['control_evaluation_id']}", headers=H(CCO))
    assert r.status_code == 200, r.text
    ev = r.json()["evaluation"]
    assert (ev["control_implementation_id"], ev["state"], ev["record_cutoff_seq"]) == (ci, "OPERATING",
                                                                                      snap["record_cutoff_seq"])
    assert [_at(ev["window_from"]), _at(ev["window_to"])] == [SEP, OCT]
    # The same question asked of the state read names the same authoritative evaluation (STA-REQ-028).
    assert _state(client, ci, as_of=snap["record_cutoff_seq"]).json()["evaluation_id"] == ev["evaluation_id"]


# ------------------------------------------------------------------------------------------------ validation
def test_window_and_cutoff_are_validated_and_nothing_is_written(client, org):
    ci, _ = _control(client, _subject())
    url = f"/api/controls/{ci}/state"
    seq = _watermark()
    cases = [({}, "Required: window_from, window_to"), ({"window_from": SEP.isoformat()}, "Required: window_to"),
             ({"window_to": OCT.isoformat()}, "Required: window_from"),
             ({"window_from": OCT.isoformat(), "window_to": SEP.isoformat()}, "non-empty half-open"),
             ({"window_from": SEP.isoformat(), "window_to": SEP.isoformat()}, "non-empty half-open")]
    before = db_fingerprint()
    for params, text in cases:
        r = client.get(url, headers=H(CCO), params=params)
        assert r.status_code == 422 and text in r.json()["detail"], (params, r.text)
    future = _state(client, ci, as_of=seq + 1000)
    assert future.status_code == 422 and future.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"
    for params in ({"window_from": "not-a-time", "window_to": OCT.isoformat()},
                   {"window_from": "0001-01-01T00:00:00+05:00", "window_to": OCT.isoformat()},
                   {"window_from": SEP.isoformat(), "window_to": OCT.isoformat(), "as_of_record_seq": -1}):
        assert client.get(url, headers=H(CCO), params=params).status_code == 422, params
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ authority and isolation
def test_reads_follow_the_control_dossier_read_policy_and_entity_scope(client, org):
    from app.models import ControlEvaluation
    ci, td = _control(client, _subject())
    _run(client, td, "PASS")
    client.post(f"/api/controls/{ci}/evaluate", headers=H(CCO),
                json={"window_from": SEP.isoformat(), "window_to": OCT.isoformat()})
    [row] = _rows(ControlEvaluation, control_implementation_id=ci)
    paths = (f"/api/controls/{ci}/state?window_from={SEP.isoformat()}&window_to={OCT.isoformat()}".replace("+", "%2B"),
             f"/api/control-evaluations/{row.evaluation_id}")
    before = db_fingerprint()
    for actor in (CCO, OWNER, AUDIT, EXT, EU_READER):  # read / audit_read, organisation-wide or for this entity
        for path in paths:
            assert client.get(path, headers=H(actor)).status_code == 200, (actor, path)
    n = denials(OTHER_READER)
    for path in paths:  # another legal entity's reader: answered as unknown, and the refusal is recorded
        r = client.get(path, headers=H(OTHER_READER))
        assert r.status_code == 404, (path, r.text)
    assert denials(OTHER_READER) == n + 2
    for path in paths:  # no read permission at all
        assert client.get(path, headers=H(TSA)).status_code == 403, path
    assert db_fingerprint() == before


def test_another_organisations_records_answer_as_unknown(client, org):
    ci, td = _control(client, _subject())
    _run(client, td, "PASS")
    evaluated = client.post(f"/api/controls/{ci}/evaluate", headers=H(CCO),
                            json={"window_from": SEP.isoformat(), "window_to": OCT.isoformat()}).json()
    window = {"window_from": SEP.isoformat(), "window_to": OCT.isoformat()}
    before = db_fingerprint()
    for path, unknown_path, params in ((f"/api/controls/{ci}/state", "/api/controls/no-such-control/state", window),
                                       (f"/api/controls/{ci}/state", "/api/controls/no-such-control/state", {}),
                                       (f"/api/control-evaluations/{evaluated['evaluation_id']}",
                                        "/api/control-evaluations/no-such-evaluation", {})):
        foreign = client.get(path, headers=H(B_CCO), params=params)
        unknown = client.get(unknown_path, headers=H(B_CCO), params=params)
        assert foreign.status_code == 404 and (foreign.status_code, foreign.json()) == (unknown.status_code,
                                                                                       unknown.json()), path
    assert db_fingerprint() == before
