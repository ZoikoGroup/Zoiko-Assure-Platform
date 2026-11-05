"""Regression tests for the backend pre-push audit findings (docs/backend-pre-push-audit-report.md,
docs/backend-remediation-report.md).

  F-02 / F-07  control test policy vocabularies and invariants (ZA-STA-001 §03; STA-REQ-006/022/025; STA §16)
  F-03         compensation needs one or more real controls on an expression branch (ZA-STA-001 §09; STA-REQ-013)
  F-04         rule publication and definition link edits serialise on one lock (DAT-REQ-012)       [PostgreSQL]
  F-05         concurrent adoption creates one obligation (ZA-DOM-001 §13)                          [PostgreSQL]
  F-06         re-adoption after closure never back-dates over the closed interval (DOM §9.2; TIM-REQ-007/008)
  F-08         compensation status is read as of the record cutoff (STA-REQ-021; TIM-REQ-006)
  F-09         only the SatisfactionExpressionVersion in force is used (STA-REQ-021; TIM-REQ-015)

Every test builds its own organisation, catalogue content and obligations; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_regulatory_traceability import LEGAL, RELEASE, _requirement, _reviewed_draft, _source

TAG = secrets.token_hex(3).upper()
TEN, ENT, SUBJ = f"tenant-rem-{TAG.lower()}", f"entity-rem-{TAG.lower()}", f"subj-rem-{TAG.lower()}"
OWNER, RISK, CCO = f"rem-owner-{TAG.lower()}", f"rem-risk-{TAG.lower()}", f"rem-cco-{TAG.lower()}"
AUTHOR = "actor-content-analyst"
INSTR = f"REM-{TAG}"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    """One probe organisation: a legal entity subject, a control owner, a risk approver and a CCO."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        db.add(Tenant(tenant_id=TEN, legal_name=f"{TEN} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=ENT, tenant_id=TEN, legal_name=f"{TEN} Bank AG", country="DE",
                           home_cell_id=CELL_ID, subject_id=SUBJ))
        db.add(Subject(subject_id=SUBJ, tenant_id=TEN, entity_id=ENT, subject_type="LEGAL_ENTITY",
                       display_name=f"{TEN} Bank AG"))
        for aid, roles in ((OWNER, ["CONTROL_OWNER"]), (RISK, ["RISK_APPROVER"]), (CCO, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=TEN, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _obligation(versions, *, valid_from=T0):
    """A probe obligation lineage on the seeded DORA definition with the given expression versions
    [(version, ast, valid_from, valid_to)]. Returns the lineage id."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TEN, entity_id=ENT, subject_id=SUBJ,
                                  obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", owner_actor_id=CCO,
                                  lifecycle="ADOPTED", valid_from=valid_from, record_from_seq=next_record_seq(db)))
        for version, ast, vf, vt in versions:
            db.add(SatisfactionExpressionVersion(tenant_id=TEN, obligation_lineage_id=lineage, version=version, ast=ast,
                                                 ast_hash=digest(ast), rationale="probe", valid_from=vf, valid_to=vt,
                                                 record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def C(key, nid=None):
    return {"node_type": "CONTROL", "node_id": nid or f"n_{key.lower()}", "control_key": key}


def ALL(*kids):
    return {"node_type": "ALL", "node_id": "root", "children": list(kids)}


def _preview(client, lineage, start="2026-01-01", end="2026-07-01", **extra):
    return client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO),
                       json={"period_from": f"{start}T00:00:00Z", "period_to": f"{end}T00:00:00Z", **extra})


# ------------------------------------------------------------------------------------------------ F-02 / F-07: policies
def _control(key, **policy):
    tests = policy.pop("tests", [{"name": "t1"}, {"name": "t2"}])
    return {"control_key": key, "subject_id": SUBJ, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
            "tests": tests, **policy}


INVALID_POLICIES = [
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": -5},              # negative
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": 0},               # zero
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": 3},               # above the number of required tests (2)
    {"aggregation_mode": "K_OF_N_TESTS"},                                 # missing
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": "2"},             # malformed: string
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": 2.0},             # malformed: float
    {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": True},            # malformed: bool
    {"aggregation_mode": "ALL_REQUIRED", "k_threshold": 1},               # k outside K_OF_N_TESTS
    {"aggregation_mode": "NOT_A_MODE"},                                   # unsupported
    {"aggregation_mode": "CUSTOM_VERSIONED_POLICY"},                      # STA v1 mode this evaluator does not implement
    {"aggregation_mode": 5},                                              # malformed
    {"automation_requirement": "WHATEVER"},                               # unsupported
    {"automation_requirement": "required"},                               # codes are case-sensitive
    {"tests": [{"name": "o1", "required": False}, {"name": "o2", "required": False}]},  # optional tests only
    {"aggregation_mode": "PRIMARY_PLUS_CORROBORATION"},                   # no named primary
    {"aggregation_mode": "PRIMARY_PLUS_CORROBORATION",
     "tests": [{"name": "p1", "primary": True}, {"name": "p2", "primary": True}]},  # two primaries
]


@pytest.mark.parametrize("policy", INVALID_POLICIES)
def test_invalid_control_policies_are_refused_at_the_api(client, org, policy):
    from app.db import AdminSession
    from app.models import ControlImplementation
    with AdminSession() as db:
        before = len(db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == TEN)).all())
    r = client.post("/api/controls", headers=H(OWNER), json=_control("C_ICT_INCIDENT_PROCESS", **dict(policy)))
    assert r.status_code == 422, r.text
    with AdminSession() as db:
        assert len(db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == TEN)).all()) == before


def test_valid_policies_at_the_boundaries_are_accepted_recorded_and_unique(client, org):
    from app.db import AdminSession
    from app.models import AuditEvent
    for key, policy in (("C_HUMAN_OVERSIGHT", {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": 1}),
                        ("C_AI_MONITORING_LOGS", {"aggregation_mode": "K_OF_N_TESTS", "k_threshold": 2}),
                        ("C_ACCESS_REVIEW", {"aggregation_mode": "PRIMARY_PLUS_CORROBORATION",
                                             "tests": [{"name": "p", "primary": True}, {"name": "c"}]}),
                        ("C_MFA_STANDARD", {"automation_requirement": "REQUIRED"})):
        r = client.post("/api/controls", headers=H(OWNER), json=_control(key, **policy))
        assert r.status_code == 200, (key, r.text)
        with AdminSession() as db:
            [a] = db.execute(select(AuditEvent).where(AuditEvent.action == "control_implementation.create",
                                                      AuditEvent.object_id == r.json()["control_implementation_id"])).scalars().all()
            assert a.tenant_id == TEN and a.actor_id == OWNER
    dupe = client.post("/api/controls", headers=H(OWNER), json=_control("C_HUMAN_OVERSIGHT"))
    assert dupe.status_code == 409


def _test_run(client, td_id, result="FAIL", start="2026-01-01", end="2026-02-01"):
    return client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td_id, "occurred_at": f"{start}T01:00:00Z", "window_from": f"{start}T00:00:00Z",
        "window_to": f"{end}T00:00:00Z", "result": result, "source_system": "probe"})


def test_a_stored_invalid_policy_is_a_configuration_error_never_operating(client, org):
    """Data that predates the API check (simulated by writing a policy version directly) is refused by the evaluator."""
    from app.db import AdminSession
    from app.models import ControlEvaluation, ControlTestPolicy, TestDefinition
    from app.platform import next_record_seq
    ci = client.post("/api/controls", headers=H(OWNER), json=_control("C_AI_TRANSPARENCY_NOTICE")).json()
    ci_id = ci["control_implementation_id"]
    with AdminSession() as db:
        pol = ControlTestPolicy(tenant_id=TEN, control_implementation_id=ci_id, aggregation_mode="K_OF_N_TESTS",
                                k_threshold=-5, automation_requirement="PREFERRED", version=2, record_seq=next_record_seq(db))
        db.add(pol)
        db.flush()
        tds = [TestDefinition(tenant_id=TEN, policy_version_id=pol.policy_version_id, name=n, source_class="CONNECTOR")
               for n in ("a", "b")]
        db.add_all(tds)
        db.commit()
        td_ids = [t.test_definition_id for t in tds]
    for td in td_ids:  # both required tests fail: the old code reported OPERATING for k = -5
        assert _test_run(client, td).status_code == 200
    r = client.post(f"/api/controls/{ci_id}/evaluate", headers=H(OWNER),
                    json={"window_from": "2026-01-01T00:00:00Z", "window_to": "2026-02-01T00:00:00Z"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "ASSURANCE_CONFIGURATION_INVALID"
    assert r.json()["detail"]["code"] == "CONTROL_TEST_POLICY_INVALID"
    with AdminSession() as db:
        assert not db.execute(select(ControlEvaluation).where(ControlEvaluation.control_implementation_id == ci_id)).all()
    listed = next(c for c in client.get("/api/controls", headers=H(OWNER)).json() if c["control_implementation_id"] == ci_id)
    assert listed["current_state"]["state"] is None and listed["current_state"]["configuration_error"]["code"] == \
        "CONTROL_TEST_POLICY_INVALID"
    detail = client.get(f"/api/controls/{ci_id}", headers=H(OWNER)).json()
    assert detail["evaluation_preview"]["state"] is None


def test_test_run_windows_must_be_non_empty_and_runs_are_recorded(client, org):
    from app.db import AdminSession
    from app.models import AuditEvent, TestDefinition
    ci = next(c for c in client.get("/api/controls", headers=H(OWNER)).json() if c["definition"]["key"] == "C_MFA_STANDARD")
    with AdminSession() as db:
        from app.models import ControlTestPolicy
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == ci["control_implementation_id"])).scalars().first()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalars().first()
    for start, end in (("2026-02-01", "2026-02-01"), ("2026-02-01", "2026-01-01")):
        r = _test_run(client, td.test_definition_id, "PASS", start, end)
        assert r.status_code == 422 and r.json()["detail"]["error"] == "TEST_RUN_WINDOW_EMPTY"
    ok = _test_run(client, td.test_definition_id, "PASS")
    assert ok.status_code == 200
    with AdminSession() as db:
        [a] = db.execute(select(AuditEvent).where(AuditEvent.action == "test_run.record",
                                                  AuditEvent.object_id == ok.json()["test_run_id"])).scalars().all()
        assert a.new_state == "PASS" and a.tenant_id == TEN


def _t(status, required=True, primary=False):
    return {"status": status, "required": required, "primary": primary, "degrades_on_fail": False, "reason_codes": []}


@pytest.mark.parametrize("mode, k, tests, expected", [
    ("K_OF_N_TESTS", -5, [_t("FAIL"), _t("FAIL")], "ERROR"),
    ("K_OF_N_TESTS", -5, [_t("UNKNOWN"), _t("UNKNOWN")], "ERROR"),
    ("K_OF_N_TESTS", 0, [_t("OK"), _t("OK")], "ERROR"),
    ("K_OF_N_TESTS", 3, [_t("OK"), _t("OK")], "ERROR"),        # was a false FAILING
    ("K_OF_N_TESTS", None, [_t("OK"), _t("OK")], "ERROR"),
    ("NOT_A_MODE", None, [_t("FAIL")], "ERROR"),               # was evaluated as ALL_REQUIRED
    ("ALL_REQUIRED", None, [_t("OK", required=False)], "ERROR"),  # was vacuously OPERATING
    ("PRIMARY_PLUS_CORROBORATION", None, [_t("OK"), _t("OK")], "ERROR"),  # was the first test, implicitly
    ("K_OF_N_TESTS", 1, [_t("OK"), _t("FAIL")], "OPERATING"),
    ("K_OF_N_TESTS", 2, [_t("OK"), _t("OK")], "OPERATING"),
    ("K_OF_N_TESTS", 2, [_t("OK"), _t("FAIL")], "FAILING"),
    ("K_OF_N_TESTS", 2, [_t("OK"), _t("UNKNOWN")], "NOT_EVALUATED"),
    ("PRIMARY_PLUS_CORROBORATION", None, [_t("FAIL", primary=True), _t("OK")], "FAILING"),
    ("ALL_REQUIRED", None, [_t("OK"), _t("OK")], "OPERATING"),
])
def test_control_aggregation_semantics_and_configuration_errors(mode, k, tests, expected):
    from app.engines.assurance import AssuranceConfigurationError, _aggregate
    policy = SimpleNamespace(aggregation_mode=mode, k_threshold=k)
    if expected == "ERROR":
        with pytest.raises(AssuranceConfigurationError) as e:
            _aggregate(policy, tests)
        assert e.value.status_code == 409 and e.value.detail["error"] == "ASSURANCE_CONFIGURATION_INVALID"
    else:
        assert _aggregate(policy, tests)[0] == expected


# ------------------------------------------------------------------------------------------------ expressions
def _leaf(states):
    return lambda key: (states[key], {"control_state": states[key]})


@pytest.mark.parametrize("node", [
    ALL(),                                                                      # empty ALL was vacuously SATISFIED
    {"node_type": "ANY", "node_id": "any", "children": []},
    {"node_type": "K_OF_N", "node_id": "k", "children": [C("A")]},              # k missing (was a KeyError)
    {"node_type": "K_OF_N", "node_id": "k", "k": 0, "children": [C("A")]},
    {"node_type": "K_OF_N", "node_id": "k", "k": 2, "children": [C("A")]},
    ALL({"node_type": "OPTIONAL_EVIDENCE", "node_id": "opt"}),                  # nothing binding
    {"node_type": "XOR", "node_id": "x", "children": [C("A")]},                 # unknown node type (was HTTP 500)
])
def test_invalid_expression_nodes_are_configuration_errors(node):
    from app.engines.assurance import SAT, AssuranceConfigurationError, eval_expression, expression_problems
    assert expression_problems(node)
    with pytest.raises(AssuranceConfigurationError) as e:
        eval_expression(node, _leaf({"A": SAT}), lambda cid: (True, {}), lambda: (SAT, {}))
    assert e.value.detail["code"] == "SATISFACTION_EXPRESSION_INVALID"


def test_compensation_semantics_follow_sta_section_09():
    from app.engines.assurance import REV, SAT, UNK, UNSAT, eval_expression, expression_problems
    node = {"node_type": "COMPENSATION", "node_id": "comp", "compensation_id": "c1", "primary": C("A"),
            "compensating": ALL(C("B"))}
    assert not expression_problems(node)
    empty = {**node, "compensating": ALL()}
    assert expression_problems(empty)  # an empty compensating branch is invalid, never vacuously satisfied
    run = lambda states, valid, reason=None: eval_expression(
        node, _leaf(states), lambda cid: (valid, {"compensation_id": cid, "valid": valid, "reason": reason}),
        lambda: (SAT, {}))["result"]
    assert run({"A": SAT, "B": UNSAT}, True) == SAT                             # primary satisfied
    assert run({"A": UNSAT, "B": SAT}, True) == SAT                             # valid compensation satisfies
    assert run({"A": UNSAT, "B": SAT}, False, "COMPENSATION_EXPIRED") == UNSAT  # invalid: the primary result stands
    assert run({"A": UNK, "B": UNSAT}, True) == UNK
    assert run({"A": UNSAT, "B": REV}, True) == REV


# ------------------------------------------------------------------------------------------------ F-03: compensation
PRIMARY, OTHER = "C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT"


@pytest.fixture(scope="module")
def comp_obligation(client, org):
    return _obligation([(1, ALL(C(PRIMARY), C(OTHER)), T0, None)])


def _comp(client, lineage, keys, *, primary=PRIMARY, valid_from="2026-02-01T00:00:00Z", valid_to=None, actor=OWNER):
    body = {"obligation_lineage_id": lineage, "primary_control_key": primary, "compensating_control_keys": keys,
            "equivalence_rationale": "Compensating control addresses the same incident-handling objective.",
            "valid_from": valid_from}
    if valid_to:
        body["valid_to"] = valid_to
    return client.post("/api/compensations", headers=H(actor), json=body)


@pytest.mark.parametrize("keys, extra, status, code", [
    ([], {}, 422, None),                                                          # "One or more controls"
    (["NO_SUCH_CONTROL"], {}, 422, "UNKNOWN_CONTROL_DEFINITION"),
    (["C_DEVICE_ATTESTATION", "C_DEVICE_ATTESTATION"], {}, 422, "DUPLICATE_COMPENSATING_CONTROL"),
    ([PRIMARY], {}, 422, "PRIMARY_CONTROL_COMPENSATES_ITSELF"),
    (["C_DEVICE_ATTESTATION"], {"primary": "NO_SUCH_PRIMARY"}, 422, "UNKNOWN_CONTROL_DEFINITION"),
    (["C_DEVICE_ATTESTATION"], {"primary": "C_ACCESS_REVIEW"}, 409, "PRIMARY_CONTROL_NOT_IN_EXPRESSION"),
    (["C_DEVICE_ATTESTATION"], {"valid_to": "2026-02-01T00:00:00Z"}, 422, "COMPENSATION_PERIOD_EMPTY"),
])
def test_invalid_compensations_are_refused(client, comp_obligation, keys, extra, status, code):
    from app.db import AdminSession
    from app.models import Compensation
    r = _comp(client, comp_obligation, keys, **extra)
    assert r.status_code == status, r.text
    if code:
        assert code in [p["code"] for p in r.json()["detail"]["problems"]]
    with AdminSession() as db:  # nothing is written for a refused proposal
        assert db.execute(select(Compensation).where(Compensation.obligation_lineage_id == comp_obligation)).all() == []


def _comp_node(ast):
    if ast.get("node_type") == "COMPENSATION":
        return ast
    for c in ast.get("children", []) or []:
        found = _comp_node(c)
        if found:
            return found
    return None


def test_compensation_lifecycle_from_proposal_to_approval_evaluation_and_revocation(client, org):
    from app.db import AdminSession
    from app.models import SatisfactionExpressionVersion
    lineage = _obligation([(1, ALL(C(PRIMARY), C(OTHER)), T0, None)])
    r = _comp(client, lineage, ["C_DEVICE_ATTESTATION", "C_PRIVILEGED_NETWORK_RESTRICTION"])
    assert r.status_code == 200, r.text
    cid = r.json()["compensation_id"]
    approved = client.post(f"/api/compensations/{cid}/decide", headers=H(RISK), json={"decision": "APPROVE", "reason": "ok"})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    with AdminSession() as db:
        versions = db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage).order_by(SatisfactionExpressionVersion.version)).scalars().all()
    assert [v.version for v in versions] == [1, 2]
    node = _comp_node(versions[1].ast)
    assert node["compensation_id"] == cid and node["primary"]["control_key"] == PRIMARY
    assert [k["control_key"] for k in node["compensating"]["children"]] == ["C_DEVICE_ATTESTATION", "C_PRIVILEGED_NETWORK_RESTRICTION"]
    events = client.get(f"/api/compensations/{cid}/events", headers=H(CCO)).json()
    cutoff = events[-1]["record_seq"]  # what Assure knew right after the approval
    # A second approval of the same primary branch cannot stack: the leaf is already inside the compensation node.
    again = _comp(client, lineage, ["C_MFA_STANDARD"])
    assert again.status_code == 409 and again.json()["detail"]["error"] == "PRIMARY_CONTROL_NOT_IN_EXPRESSION"
    assert client.post(f"/api/compensations/{cid}/decide", headers=H(RISK),
                       json={"decision": "REVOKE", "reason": "superseded"}).json()["status"] == "REVOKED"

    def comp_trace(**extra):
        p = _preview(client, lineage, "2026-03-01", "2026-04-01", **extra)
        assert p.status_code == 200, p.text
        return _comp_node(p.json()["segments"][-1]["expression_trace"])["compensation"]
    # F-08: the earlier record perspective still sees the approval; current knowledge sees the revocation.
    then = comp_trace(record_cutoff_seq=cutoff)
    assert (then["status"], then["valid"], then["status_source"]) == ("APPROVED", True, "EVENT_HISTORY")
    now = comp_trace()
    assert (now["status"], now["valid"], now["status_source"]) == ("REVOKED", False, "EVENT_HISTORY")


def test_a_stored_empty_compensation_can_neither_be_approved_nor_satisfy(client, org):
    """Records written before the F-03 checks: an empty proposal cannot be approved, and an approved empty branch already
    inside an expression is a configuration error, never a SATISFIED result."""
    from app.db import AdminSession
    from app.models import Compensation, SatisfactionExpressionVersion
    from app.platform import next_record_seq
    lineage = _obligation([(1, ALL(C(PRIMARY), C(OTHER)), T0, None)])
    with AdminSession() as db:
        c = Compensation(tenant_id=TEN, obligation_lineage_id=lineage, primary_control_key=PRIMARY,
                         compensating_expression=ALL(), equivalence_rationale="legacy empty compensation proposal",
                         valid_from=T0, record_seq=next_record_seq(db))
        db.add(c)
        db.commit()
        cid = c.compensation_id
    r = client.post(f"/api/compensations/{cid}/decide", headers=H(RISK), json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "COMPENSATING_EXPRESSION_INVALID"
    with AdminSession() as db:
        assert db.get(Compensation, cid).status == "PENDING_APPROVAL"
        assert len(db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage)).all()) == 1
    vacuous = {"node_type": "COMPENSATION", "node_id": "comp", "compensation_id": cid, "primary": C(PRIMARY),
               "compensating": ALL()}
    stored = _obligation([(1, ALL(vacuous, C(OTHER)), T0, None)])
    p = _preview(client, stored)
    assert p.status_code == 409 and p.json()["detail"]["code"] == "SATISFACTION_EXPRESSION_INVALID"


# ------------------------------------------------------------------------------------------------ F-09: expressions
def _segments(resp):
    assert resp.status_code == 200, resp.text
    return [(s["segment_range"][0][:10], s["segment_range"][1][:10], s["expression_version_id"]) for s in resp.json()["segments"]]


def test_only_the_expression_version_in_force_is_used(client, org):
    from app.db import AdminSession
    from app.models import SatisfactionExpressionVersion
    lineage = _obligation([(1, ALL(C(PRIMARY)), T0, None),                                    # current, then superseded
                           (2, ALL(C(PRIMARY), C(OTHER)), _dt("2026-04-01"), None),           # supersedes from April
                           (3, ALL(C(OTHER)), _dt("2027-01-01"), None)])                      # future-effective
    with AdminSession() as db:
        ids = {v.version: (v.expression_version_id, v.record_seq) for v in db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage)).scalars()}
    used = {e for _, _, e in _segments(_preview(client, lineage, "2026-01-01", "2026-07-01"))}
    assert used == {ids[1][0], ids[2][0]}  # v1 before April, v2 after; the future-effective v3 is never used
    for start, end, e in _segments(_preview(client, lineage, "2026-01-01", "2026-07-01")):
        assert e == (ids[1][0] if end <= "2026-04-01" else ids[2][0]), (start, end)
    # Record perspective: before v2 was recorded, only v1 was known.
    known_then = _segments(_preview(client, lineage, "2026-01-01", "2026-07-01", record_cutoff_seq=ids[1][1]))
    assert {e for _, _, e in known_then} == {ids[1][0]}


def test_an_applicable_segment_without_an_expression_in_force_is_a_configuration_error(client, org):
    lineage = _obligation([(1, ALL(C(PRIMARY)), _dt("2026-04-01"), None)])  # the obligation applies from January
    r = _preview(client, lineage, "2026-01-01", "2026-07-01")
    assert r.status_code == 409, r.text  # was evaluated under v1 although v1 was not in force in Q1
    assert r.json()["detail"]["code"] == "NO_EXPRESSION_IN_FORCE"
    assert _segments(_preview(client, lineage, "2026-04-01", "2026-07-01"))  # the in-force period still evaluates


# ------------------------------------------------------------------------------------------------ F-05 / F-06 adoption
@pytest.fixture(scope="module")
def released(client, org):
    """A definition released through every publication gate, and an APPLIES determination for the probe subject."""
    from app.db import AdminSession
    from app.models import Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title=f"Remediation probe {INSTR}", jurisdiction="EU",
                          authority="Probe Authority", coverage_mode="ENCODED"))
        db.commit()
    s = _source(client, "Credit institutions shall keep a register and shall review it annually.", instrument=INSTR,
                locator=f"OJ L {TAG}/rem").json()
    rv = _requirement(client, s["source_id"], f"REM-{TAG}-V", instrument=INSTR).json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    rv2 = _requirement(client, s["source_id"], f"REM-{TAG}-W", instrument=INSTR, locator="Art. 5").json()
    assert client.post(f"/api/catalog/requirements/{rv2['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return {"source": s, "rv": rv, "rv2": rv2}


def _definition(client, released, key):
    rv = released["rv"]
    r = client.post("/api/catalog/obligation-definitions", headers=H(AUTHOR), json={
        "key": key, "title": "Maintain the register (remediation probe)", "instrument_identifier": INSTR,
        "subject_type": "LEGAL_ENTITY",
        "links": [{"requirement_version_id": rv["requirement_version_id"], "provision_id": p["provision_id"]}
                  for p in rv["provisions"][:2]]})
    assert r.status_code == 201, r.text
    return r.json()["obligation_definition_id"]


def _publish_direct(draft_id, label):
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.routers.rules import PublishIn, publish
    from app.security import Ctx
    with SessionLocal() as db:
        set_principal(db, None, RELEASE)
        return publish(draft_id, PublishIn(label=label, changelog=label), Ctx(db=db, actor=db.get(Actor, RELEASE)))


@pytest.fixture(scope="module")
def applies(client, released):
    oid = _definition(client, released, f"REM_{TAG}_ADOPT")
    did = _reviewed_draft(client, oid, f"rem{TAG.lower()}", released["rv"], released["source"])
    assert client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"rem-{TAG}", "changelog": "probe"}).status_code == 200
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{SUBJ}/facts", headers=H(CCO),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    ev = client.post("/api/applicability/evaluations", headers=H(CCO),
                     json={"subject_id": SUBJ, "obligation_definition_id": oid}).json()
    assert ev["outcome"] == "APPLIES" and ev["current_determination"]["state"] == "PROPOSED"
    return {"odef_id": oid, "determination": ev["current_determination"]["lineage_id"]}


def _obligation_revisions(odef_id):
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        return db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == TEN,
                                                           ObligationInstance.subject_id == SUBJ,
                                                           ObligationInstance.obligation_definition_id == odef_id)
                          .order_by(ObligationInstance.record_from_seq)).scalars().all()


def test_concurrent_or_repeated_adoption_creates_exactly_one_obligation(client, applies):
    from app.db import IS_POSTGRES
    from app.engines import applicability as appl
    lin = applies["determination"]

    def attest():
        from app.db import SessionLocal, set_principal
        from app.models import Actor
        from app.security import Ctx
        with SessionLocal() as db:
            set_principal(db, TEN, CCO)
            ctx = Ctx(db=db, actor=db.get(Actor, CCO))
            appl.attest(db, ctx=ctx, determination_lineage_id=lin, decision="ADOPT", authority_basis="Board delegation")
            db.commit()

    results = []

    def worker(barrier=None):
        try:
            if barrier:
                barrier.wait()
            attest()
            results.append("ok")
        except HTTPException as e:
            results.append(e.status_code)
        except Exception as e:  # noqa: BLE001 - reported by the assertion
            results.append(repr(e))
    if IS_POSTGRES:  # two transactions at once (READ COMMITTED, separate connections)
        barrier = threading.Barrier(2)
        threads = [threading.Thread(target=worker, args=(barrier,)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    else:  # SQLite serialises writers: the repeated adoption is refused the same way
        worker()
        worker()
    assert sorted(results, key=str) == [409, "ok"], results
    revs = _obligation_revisions(applies["odef_id"])
    assert len({r.lineage_id for r in revs}) == 1 and len(revs) == 1
    assert [r.lifecycle for r in revs if r.record_to_seq is None] == ["ADOPTED"]
    # UNCERTAIN never creates an obligation: covered for authored definitions by
    # test_obligation_definition_authoring::test_authored_definition_flows_through_applicability_and_adoption_unchanged.


def test_readoption_after_closure_never_backdates_over_the_closed_interval(client, applies):
    """The lifecycle step itself (engines/applicability.py _close_obligation / _activate_obligation). Through the API an
    adopted determination currently cannot reach closure (docs/backend-remediation-report.md, R-02), so the transition
    is exercised at the engine boundary with explicit valid times."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    from app.platform import ensure_utc, next_record_seq
    [first] = _obligation_revisions(applies["odef_id"])
    start = ensure_utc(first.valid_from)
    closed_at = start + timedelta(days=10)
    with AdminSession() as db:
        subject, odef = db.get(Subject, SUBJ), db.get(ObligationDefinition, applies["odef_id"])
        appl._close_obligation(db, TEN, SUBJ, applies["odef_id"], next_record_seq(db), closed_at)
        db.commit()
        # Re-adopted under a determination whose valid time starts BEFORE the closure: clamped to the closure.
        det = SimpleNamespace(tenant_id=TEN, revision_id="probe-det-1", valid_from=start + timedelta(days=5))
        att = SimpleNamespace(attestation_id="probe-att-1")
        appl._activate_obligation(db, det, att, subject, odef, next_record_seq(db), CCO)
        db.commit()
    revs = _obligation_revisions(applies["odef_id"])
    assert [r.lifecycle for r in revs] == ["ADOPTED", "CLOSED", "ADOPTED"]
    assert len({r.lineage_id for r in revs}) == 1
    original, closed, readopted = revs
    assert ensure_utc(original.valid_from) == start and original.record_to_seq is not None  # history unchanged
    assert (ensure_utc(closed.valid_from), ensure_utc(closed.valid_to)) == (start, closed_at)
    assert ensure_utc(readopted.valid_from) == closed_at  # was `start`: the closed interval was re-claimed
    assert readopted.record_to_seq is None
    # Closed again, then re-adopted with a later determination: the new period starts at that determination.
    later = closed_at + timedelta(days=20)
    with AdminSession() as db:
        subject, odef = db.get(Subject, SUBJ), db.get(ObligationDefinition, applies["odef_id"])
        appl._close_obligation(db, TEN, SUBJ, applies["odef_id"], next_record_seq(db), closed_at + timedelta(days=15))
        appl._activate_obligation(db, SimpleNamespace(tenant_id=TEN, revision_id="probe-det-2", valid_from=later),
                                  SimpleNamespace(attestation_id="probe-att-2"), subject, odef, next_record_seq(db), CCO)
        db.commit()
    assert ensure_utc(_obligation_revisions(applies["odef_id"])[-1].valid_from) == later


# ------------------------------------------------------------------------------------------------ F-04: publish vs edit
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _edit_in_thread(oid, rv_id, provision_id, out, *, hold: threading.Event | None = None,
                    locked: threading.Event | None = None):
    import app.engines.catalog as cat
    from app.db import SessionLocal, set_principal
    from app.models import Actor
    from app.security import Ctx

    def run():
        try:
            with SessionLocal() as db:
                set_principal(db, None, AUTHOR)
                cat.add_link(Ctx(db=db, actor=db.get(Actor, AUTHOR)), oid, rv_id, provision_id, "race")
                if locked:
                    locked.set()
                if hold:
                    hold.wait(30)
                db.commit()
                out["edit"] = "ok"
        except HTTPException as e:
            out["edit"] = (e.status_code, (e.detail or {}).get("error") if isinstance(e.detail, dict) else e.detail)
        except Exception as e:  # noqa: BLE001
            out["edit"] = repr(e)
    t = threading.Thread(target=run)
    t.start()
    return t


def _publish_in_thread(did, label, out):
    def run():
        try:
            out["publish"] = _publish_direct(did, label)
        except HTTPException as e:
            out["publish"] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001
            out["publish"] = repr(e)
    t = threading.Thread(target=run)
    t.start()
    return t


def _bound(publish_result):
    return sorted(p for r in publish_result["content_release"]["manifest"]["requirement_versions"] for p in r["provision_ids"])


def _links_of(client, oid):
    return sorted(client.get(f"/api/catalog/obligation-definitions/{oid}", headers=H(AUTHOR)).json()["provision_ids"])


def test_publication_waits_for_an_in_flight_link_edit_and_signs_the_committed_links(client, released, pg):
    rv2 = released["rv2"]
    oid = _definition(client, released, f"REM_{TAG}_RACE_A")
    did = _reviewed_draft(client, oid, f"rema{TAG.lower()}", released["rv"], released["source"])
    extra = rv2["provisions"][0]["provision_id"]
    out, locked, hold = {}, threading.Event(), threading.Event()
    edit = _edit_in_thread(oid, rv2["requirement_version_id"], extra, out, hold=hold, locked=locked)
    assert locked.wait(30)
    pub = _publish_in_thread(did, f"rem-a-{TAG}", out)
    time.sleep(1.5)
    assert pub.is_alive(), out  # the publication waits for the definition lock
    hold.set()
    edit.join(60)
    pub.join(60)
    assert out["edit"] == "ok", out
    assert isinstance(out["publish"], dict), out
    # Without the lock the publication read the links before the edit committed and signed a stale set.
    assert extra in _bound(out["publish"])
    assert _bound(out["publish"]) == _links_of(client, oid)


def test_a_link_edit_waiting_behind_a_publication_is_refused_after_the_release(client, released, pg, monkeypatch):
    import app.engines.catalog as cat
    rv2 = released["rv2"]
    oid = _definition(client, released, f"REM_{TAG}_RACE_B")
    did = _reviewed_draft(client, oid, f"remb{TAG.lower()}", released["rv"], released["source"])
    entered, go = threading.Event(), threading.Event()
    original = cat.release_binding

    def paused(db, odef):  # runs after publish took the definition lock (argument evaluated first)
        entered.set()
        go.wait(30)
        return original(db, odef)
    monkeypatch.setattr(cat, "release_binding", paused)
    out = {}
    pub = _publish_in_thread(did, f"rem-b-{TAG}", out)
    assert entered.wait(30)
    edit = _edit_in_thread(oid, rv2["requirement_version_id"], rv2["provisions"][0]["provision_id"], out)
    time.sleep(1.5)
    assert edit.is_alive(), out  # the edit waits for the publication's definition lock
    go.set()
    pub.join(60)
    edit.join(60)
    monkeypatch.undo()
    assert isinstance(out["publish"], dict), out
    assert out["edit"] == (409, "OBLIGATION_DEFINITION_RELEASED"), out
    assert _bound(out["publish"]) == _links_of(client, oid)  # the released definition is exactly what was signed
