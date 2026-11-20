"""Control evaluation integrity: invalid inputs and configurations fail closed, and stored records say what happened.

Requirements exercised (ZA-STA-001 unless stated):
  §04 / STA-REQ-006   missing proof never yields OPERATING; an empty or inverted evaluation window holds no proof, so it
                      is invalid input (§18 "Missing required temporal context returns validation error rather than
                      guessing"), never a vacuous OPERATING;
  §07 / §08           K_OF_N counts distinct peers ("rationale proving children are valid peers"): a control listed twice
                      under one K_OF_N is not two peers (the authoring rule DUPLICATE_CONTROL_LEAF, engines/satisfaction.py,
                      applied at evaluation to versions stored without it); §24 K_OF_N boundary vectors;
  §18 / STA-REQ-022   an invalid SatisfactionExpression (a CONTROL leaf naming no catalogue control definition, the authoring
                      rule UNKNOWN_CONTROL_DEFINITION) is a configuration error, never a NOT_PROVEN or PROVEN substitute;
                      evaluator faults are errors, never states; one configuration-error representation everywhere;
  ZA-EVT-001 §17/§20  control.evaluated means "Immutable ControlEvaluation completed": it is written only with a stored
                      evaluation, names it, and carries the IMMUTABLE_RESULT / STA §18 minimum payload;
  §17                 no evaluation, issue or event is written from a refused or failed evaluation (one transaction).

Every test builds its own organisation, subjects, controls and obligations; seeded data is only read.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-cei-{TAG}", f"tenant-ceib-{TAG}"
ENT, ENT_B = f"entity-cei-{TAG}", f"entity-ceib-{TAG}"
OWNER, CCO, B_CCO = f"cei-owner-{TAG}", f"cei-cco-{TAG}", f"cei-b-cco-{TAG}"
KEY_A, KEY_B, KEY_C = "C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT", "C_AI_MONITORING_LOGS"
UNKNOWN_KEY = f"C_NOT_IN_CATALOGUE_{TAG.upper()}"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11))
EMPTY_WINDOW = "The control window must be a non-empty half-open interval"


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
        for aid, tenant, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ records
def _subject():
    """A fresh subject in the probe entity, so each test owns its control implementations (INV-08)."""
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _control(client, subject, key=KEY_A, **policy):
    """(control implementation, [test definition ids]) with one required manual test unless ``tests`` is given."""
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    tests = policy.pop("tests", [{"name": "probe", "source_class": "MANUAL"}])
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": tests, **policy})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        tds = [t.test_definition_id for t in db.execute(select(TestDefinition).where(
            TestDefinition.policy_version_id == pol.policy_version_id)).scalars()]
    return ci, tds


def _evidence(client):
    r = client.post("/api/evidence/upload", headers=H(OWNER),
                    files={"file": ("proof.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": ENT, "title": "Probe record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _run(client, td, result, start=OCT, end=NOV, evidence=()):
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": list(evidence)})
    assert r.status_code == 200, r.text
    return r.json()["test_run_id"]


def _evaluate(client, ci, start=OCT, end=NOV):
    return client.post(f"/api/controls/{ci}/evaluate", headers=H(OWNER),
                       json={"window_from": start.isoformat(), "window_to": end.isoformat()})


def _stored(ci):
    """What a control has written: (evaluations, control.evaluated outbox events naming it or its evaluations, issues)."""
    from app.db import AdminSession
    from app.models import ControlEvaluation, Issue, OutboxEvent
    with AdminSession() as db:
        evals = db.execute(select(ControlEvaluation).where(ControlEvaluation.control_implementation_id == ci)
                           .order_by(ControlEvaluation.created_at)).scalars().all()
        ids = [ci, *(e.evaluation_id for e in evals)]
        events = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "control.evaluated",
                                                      OutboxEvent.aggregate_id.in_(ids))).scalars().all()
        issues = db.execute(select(Issue).where(Issue.tenant_id == TA, Issue.dedupe_key.like(f"%:{ci}:%"))).scalars().all()
        for row in (*evals, *events, *issues):
            db.expunge(row)
    return evals, events, issues


def _obligation(subject, ast):
    """A probe obligation on ``subject`` whose one expression version is ``ast``, written directly: a version stored
    without the authoring checks (data predating them), as the evaluator must still refuse."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def C(key, nid):
    return {"node_type": "CONTROL", "node_id": nid, "control_key": key}


def _snapshot_rows(lineage):
    from app.db import AdminSession
    from app.models import AssuranceSnapshot, Issue
    with AdminSession() as db:
        snaps = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.obligation_lineage_id == lineage)).all()
        issues = db.execute(select(Issue).where(Issue.dedupe_key.like(f"%:{lineage}"))).all()
    return len(snaps), len(issues)


def _period(client, lineage, persist):
    return client.post(f"/api/obligations/{lineage}/{'snapshots' if persist else 'preview'}", headers=H(CCO),
                       json={"period_from": OCT.isoformat(), "period_to": NOV.isoformat()})


# ------------------------------------------------------------------------------------------------ evaluation window
@pytest.mark.parametrize("start, end", [(OCT, OCT), (NOV, OCT)], ids=["empty", "inverted"])
def test_an_empty_or_inverted_window_is_invalid_input_and_stores_nothing(client, org, start, end):
    """A required test with no run at all: over an empty or inverted window the old code found the window 'covered' by
    nothing and reported OPERATING, stored it as an immutable evaluation and published control.evaluated."""
    ci, _ = _control(client, _subject())
    r = _evaluate(client, ci, start, end)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == EMPTY_WINDOW
    assert _stored(ci) == ([], [], [])
    ok = _evaluate(client, ci)  # the same control over a real window: missing proof is NOT_EVALUATED, never OPERATING
    assert ok.status_code == 200 and ok.json()["state"] == "NOT_EVALUATED" and ok.json()["reason_codes"] == ["TEST_NOT_RUN"]


@pytest.mark.parametrize("days", [0, -3, 10 ** 9, 2 ** 62])
def test_the_control_preview_window_must_be_non_empty_and_in_range(client, org, days):
    """GET /controls/{id}?days=N previews [now - N days, now). N <= 0 gave an empty or inverted window and a vacuous
    OPERATING preview; a day count beyond the calendar raised an unhandled OverflowError (HTTP 500)."""
    ci, _ = _control(client, _subject())
    r = client.get(f"/api/controls/{ci}", headers=H(OWNER), params={"days": days})
    assert r.status_code == 422, r.text
    fine = client.get(f"/api/controls/{ci}", headers=H(OWNER), params={"days": 30})
    assert fine.status_code == 200 and fine.json()["evaluation_preview"]["state"] == "NOT_EVALUATED"
    assert _stored(ci) == ([], [], [])


def test_the_engine_refuses_an_empty_window_on_every_path(client, org):
    """Defence in depth below the routes: evaluate_control (persisted or not) and control_state_at."""
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlImplementation
    from app.platform import current_watermark
    ci_id, _ = _control(client, _subject())
    with AdminSession() as db:
        ci = db.get(ControlImplementation, ci_id)
        cutoff = current_watermark(db)["record_seq"]
        for call in (lambda: asr.evaluate_control(db, ci, OCT, OCT, cutoff, persist=True),
                     lambda: asr.evaluate_control(db, ci, NOV, OCT, cutoff, persist=False),
                     lambda: asr.control_state_at(db, ci, NOV, OCT, cutoff)):
            with pytest.raises(HTTPException) as e:
                call()
            assert e.value.status_code == 422 and e.value.detail == EMPTY_WINDOW
        db.rollback()
    assert _stored(ci_id) == ([], [], [])


# ------------------------------------------------------------------------------------------------ K_OF_N
def _leaf(states):
    return lambda key, impl=None: (states[key], {"control_state": states[key]})


def test_k_of_n_counts_each_control_once():
    """K_OF_N k=2 over [A, A, B] with only A satisfied: the old evaluator counted A twice and returned SATISFIED."""
    from app.engines.assurance import SAT, UNSAT, AssuranceConfigurationError, eval_expression
    comp, occ = (lambda cid: (True, {})), (lambda: (SAT, {}))
    dup = {"node_type": "K_OF_N", "node_id": "k", "k": 2, "children": [C("A", "a1"), C("A", "a2"), C("B", "b")]}
    bound = {**dup, "children": [C("A", "a1"), {**C("A", "a2"), "control_implementation_id": "ci-x"}, C("B", "b")]}
    nested = {"node_type": "ALL", "node_id": "root", "children": [C("B", "b0"), dup]}
    for node in (dup, bound, nested):
        with pytest.raises(AssuranceConfigurationError) as e:
            eval_expression(node, _leaf({"A": SAT, "B": UNSAT}), comp, occ)
        assert e.value.status_code == 409 and e.value.detail["code"] == "SATISFACTION_EXPRESSION_INVALID"
        assert "A" in e.value.detail["message"] and e.value.detail["node_id"] == "k"
    distinct = {**dup, "children": [C("A", "a"), C("B", "b"), C("C", "c")]}
    assert eval_expression(distinct, _leaf({"A": SAT, "B": SAT, "C": UNSAT}), comp, occ)["result"] == SAT
    assert eval_expression(distinct, _leaf({"A": SAT, "B": UNSAT, "C": UNSAT}), comp, occ)["result"] == UNSAT
    # ALL / ANY keep their results with a repeated leaf (idempotent); authoring refuses those versions anyway.
    rep_all = {"node_type": "ALL", "node_id": "r", "children": [C("A", "a1"), C("A", "a2")]}
    assert eval_expression(rep_all, _leaf({"A": SAT}), comp, occ)["result"] == SAT


@pytest.mark.parametrize("k, vals, expected", [
    (2, ["SATISFIED", "SATISFIED", "UNSATISFIED"], "SATISFIED"),    # enough satisfied (exactly K)
    (3, ["SATISFIED", "SATISFIED", "SATISFIED"], "SATISFIED"),      # K = N
    (2, ["SATISFIED", "UNSATISFIED", "UNSATISFIED"], "UNSATISFIED"),  # impossible to reach K
    (2, ["SATISFIED", "REVIEW", "UNSATISFIED"], "REVIEW"),          # review-sensitive: could still reach K
    (2, ["SATISFIED", "UNKNOWN", "UNSATISFIED"], "UNKNOWN"),        # unknown-sensitive: could still reach K
    (2, ["SATISFIED", "REVIEW", "UNKNOWN"], "REVIEW"),              # review outranks unknown while K is reachable
    (3, ["SATISFIED", "SATISFIED", "UNKNOWN"], "UNKNOWN"),          # one short of K = N
    (1, ["UNSATISFIED", "UNSATISFIED", "UNKNOWN"], "UNKNOWN"),
])
def test_k_of_n_boundary_vectors(k, vals, expected):
    """ZA-STA-001 §08 K_OF_N table and §24 'Boundary tests for enough satisfied, impossible-to-reach K, review-sensitive
    and unknown-sensitive cases'."""
    from app.engines.assurance import combine_k
    assert combine_k(vals, k) == expected


@pytest.mark.parametrize("statuses, k, expected", [
    (["OK", "OK", "FAIL"], 2, "OPERATING"),          # enough peer tests pass
    (["OK", "FAIL", "FAIL"], 2, "FAILING"),          # K can no longer be reached
    (["OK", "CONFLICT", "FAIL"], 2, "REQUIRES_REVIEW"),
    (["OK", "UNKNOWN", "FAIL"], 2, "NOT_EVALUATED"),
    (["OK", "CONFLICT", "UNKNOWN"], 2, "REQUIRES_REVIEW"),
    (["OK", "OK", "OK"], 3, "OPERATING"),            # K = N
    (["OK", "OK", "UNKNOWN"], 3, "NOT_EVALUATED"),   # one test short of K = N never counts as passing
])
def test_k_of_n_tests_boundary_vectors(statuses, k, expected):
    """ZA-STA-001 §03 K_OF_N_TESTS ('at least K of N peer tests must pass') under the §04 precedence."""
    from types import SimpleNamespace
    from app.engines.assurance import _aggregate
    tests = [{"status": s, "required": True, "primary": False, "degrades_on_fail": False, "reason_codes": []}
             for s in statuses]
    assert _aggregate(SimpleNamespace(aggregation_mode="K_OF_N_TESTS", k_threshold=k), tests)[0] == expected


def test_a_stored_k_of_n_with_a_repeated_control_is_refused_not_proven(client, org):
    """Through the period roll-up: A OPERATING, B FAILING, K_OF_N k=2 over [A, A, B] was PROVEN."""
    subject = _subject()
    a, (ta,) = _control(client, subject, KEY_A)
    b, (tb,) = _control(client, subject, KEY_B)
    _run(client, ta, "PASS", evidence=[_evidence(client)])
    _run(client, tb, "FAIL")
    ast = {"node_type": "K_OF_N", "node_id": "root", "k": 2,
           "children": [C(KEY_A, "a1"), C(KEY_A, "a2"), C(KEY_B, "b")]}
    lineage = _obligation(subject, ast)
    for persist in (False, True):
        r = _period(client, lineage, persist)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "ASSURANCE_CONFIGURATION_INVALID"
        assert r.json()["detail"]["code"] == "SATISFACTION_EXPRESSION_INVALID"
        # refused once for the stored version, before any leaf is evaluated, and named by it
        assert r.json()["detail"]["version"] == 1 and r.json()["detail"]["expression_version_id"]
        assert KEY_A in r.json()["detail"]["message"]
    assert _snapshot_rows(lineage) == (0, 0)
    for ci in (a, b):  # the refused snapshot left no control evaluation, issue or event behind (one transaction)
        assert _stored(ci) == ([], [], [])
    # The same controls under a valid expression roll up normally: 1 of 2 distinct peers is not 2.
    valid = _obligation(subject, {**ast, "children": [C(KEY_A, "a"), C(KEY_B, "b")]})
    assert _period(client, valid, False).json()["state"] == "FAILING"


# ------------------------------------------------------------------------------------------------ test criteria
POPULATION_NONE_COMPLIANT = [{"user": f"u{i}", "mfa_enabled": False} for i in range(9)]
POPULATION_ONE_COMPLIANT = POPULATION_NONE_COMPLIANT + [{"user": "u9", "mfa_enabled": True}]
MALFORMED_CRITERIA = [
    {"quantifier": "ALL", "field": "mfa_enabled", "op": "eq", "value": True},        # was evaluated as "!="
    {"quantifier": "ALL", "field": "mfa_enabled", "op": "GT", "value": True},        # was evaluated as "!="
    {"quantifier": "all", "field": "mfa_enabled", "op": "EQ", "value": True},        # was evaluated as ANY
    {"quantifier": "EVERY", "field": "mfa_enabled", "op": "EQ", "value": True},      # was evaluated as ANY
    {"quantifier": "ALL", "op": "EQ", "value": True},                                # no field: was a KeyError (500)
    {"quantifier": "ALL", "field": "", "op": "EQ", "value": True},
    {"quantifier": "ALL", "field": ["mfa_enabled"], "op": "EQ", "value": True},      # type mismatch
    {"quantifier": "ALL", "field": "mfa_enabled", "op": "EQ"},                       # no value: compared with null
    {"quantifier": "ALL", "field": "mfa_enabled", "op": "EQ", "value": True, "threshold": 0.5},  # unknown key ignored
]


@pytest.mark.parametrize("criterion", MALFORMED_CRITERIA)
def test_a_malformed_test_criterion_is_a_test_error_never_a_pass(criterion):
    """engines/ipe.evaluate_criterion turns a TestDefinition criterion into the TestRun result of a source run. A
    criterion outside the grammar it implements fell through to another operator or quantifier (a false PASS), compared
    against null, or crashed. It is now an ERROR result (STA §04: ERROR does not prove pass or fail; the control is
    NOT_EVALUATED), never PASS, for a population where nobody complies and where only one record does."""
    from app.engines.ipe import evaluate_criterion
    for population in (POPULATION_NONE_COMPLIANT, POPULATION_ONE_COMPLIANT, []):
        result, detail = evaluate_criterion(criterion, population)
        assert result == "ERROR" and detail.startswith("test criterion invalid"), (criterion, result, detail)


def test_the_criterion_grammar_the_evaluator_implements_is_unchanged():
    from app.engines.ipe import evaluate_criterion
    all_eq = {"quantifier": "ALL", "field": "mfa_enabled", "op": "EQ", "value": True}
    assert evaluate_criterion(all_eq, POPULATION_ONE_COMPLIANT)[0] == "FAIL"
    assert evaluate_criterion(all_eq, [{"mfa_enabled": True}] * 3) == ("PASS", "3/3 records satisfy mfa_enabled EQ True")
    assert evaluate_criterion({**all_eq, "quantifier": "ANY"}, POPULATION_ONE_COMPLIANT)[0] == "PASS"
    assert evaluate_criterion({**all_eq, "quantifier": "ANY"}, POPULATION_NONE_COMPLIANT)[0] == "FAIL"
    assert evaluate_criterion({"field": "mfa_enabled", "value": True}, [{"mfa_enabled": True}])[0] == "PASS"  # defaults
    assert evaluate_criterion(all_eq, []) == ("INCONCLUSIVE", "empty population")


@pytest.mark.parametrize("criterion", MALFORMED_CRITERIA + ["mfa_enabled == true", ["ALL"]])
def test_a_malformed_test_criterion_is_refused_when_the_control_is_created(client, org, criterion):
    from app.db import AdminSession
    from app.models import ControlImplementation
    subject = _subject()
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": KEY_A, "subject_id": subject, "method": "probe", "valid_from": T0.isoformat(),
        "automation_requirement": "NONE", "tests": [{"name": "t", "source_class": "CONNECTOR", "criterion": criterion}]})
    assert r.status_code == 422, r.text
    with AdminSession() as db:
        assert not db.execute(select(ControlImplementation).where(ControlImplementation.subject_id == subject)).all()


# ------------------------------------------------------------------------------------------------ member references
@pytest.mark.parametrize("shape", ["ALL", "ANY", "K_OF_N"])
def test_a_leaf_naming_no_catalogue_control_is_a_configuration_error(client, org, shape):
    """Was NOT_PROVEN (ALL; CONTROL_NOT_IMPLEMENTED substituted for the configuration error) or even PROVEN (ANY,
    K_OF_N k=1, where the other child is OPERATING)."""
    subject = _subject()
    a, (ta,) = _control(client, subject, KEY_A)
    _run(client, ta, "PASS", evidence=[_evidence(client)])
    node = {"node_type": shape, "node_id": "root", "children": [C(UNKNOWN_KEY, "u"), C(KEY_A, "a")]}
    if shape == "K_OF_N":
        node["k"] = 1
    lineage = _obligation(subject, node)
    for persist in (False, True):
        r = _period(client, lineage, persist)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "ASSURANCE_CONFIGURATION_INVALID" and detail["code"] == "CONTROL_MAPPING_INVALID"
        assert UNKNOWN_KEY in detail["message"] and detail["version"] == 1
    assert _snapshot_rows(lineage) == (0, 0)
    assert _stored(a) == ([], [], [])


# ------------------------------------------------------------------------------------------------ stored records
def test_control_evaluated_is_written_only_with_a_stored_evaluation(client, org):
    """ZA-EVT-001 §17 control.evaluated = 'Immutable ControlEvaluation completed'. A window outside the control's valid
    time stores no evaluation; the old route still published control.evaluated, naming the control implementation as
    a ControlEvaluation aggregate."""
    ci, _ = _control(client, _subject())
    before = _evaluate(client, ci, datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 2, 1, tzinfo=timezone.utc))
    assert before.status_code == 200, before.text
    assert before.json()["state"] == "NOT_EVALUATED" and before.json()["reason_codes"] == ["CONTROL_NOT_IMPLEMENTED"]
    assert before.json()["evaluation_id"] is None
    assert _stored(ci) == ([], [], [])


def test_control_evaluated_names_the_stored_evaluation_with_its_minimum_payload(client, org):
    """IMMUTABLE_RESULT profile (ZA-EVT-001 §20) and the STA §18 minimum: tenant/entity, subject/control revision,
    window, record cutoff, evaluation ID, state, reason codes, versions and the input digest."""
    subject = _subject()
    ci, (td,) = _control(client, subject)
    _run(client, td, "FAIL")
    r = _evaluate(client, ci)
    assert r.status_code == 200 and r.json()["state"] == "FAILING"
    evals, events, issues = _stored(ci)
    assert len(evals) == 1 and len(events) == 1 and len(issues) == 1
    ce, ev = evals[0], events[0]
    assert ev.aggregate_type == "ControlEvaluation" and ev.aggregate_id == ce.evaluation_id == r.json()["evaluation_id"]
    assert ev.tenant_id == TA and ev.entity_id == ENT
    assert ev.payload == {
        "evaluation_id": ce.evaluation_id, "control_implementation_id": ci, "subject_id": subject,
        "policy_version_id": ce.policy_version_id, "window": [OCT.isoformat().replace("+00:00", "Z"),
                                                              NOV.isoformat().replace("+00:00", "Z")],
        "record_cutoff_seq": ce.record_cutoff_seq, "state": "FAILING", "reason_codes": ["TEST_FAIL"],
        "evaluator_version": ce.evaluator_version, "dependency_digest": ce.dependency_digest}


def test_an_identical_re_evaluation_is_deterministic_and_records_nothing_twice(client, org):
    """The same window at the same record cutoff, evaluated again: the same result, digest and stored evaluation, and no
    second evaluation, event or issue (STA §19 deduplication; STA-REQ-019/020). A later cutoff is a new evaluation,
    which tests/test_control_issues.py covers."""
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlImplementation
    from app.platform import current_watermark
    ci_id, (td,) = _control(client, _subject())
    _run(client, td, "FAIL")
    outs = []
    with AdminSession() as db:
        ci = db.get(ControlImplementation, ci_id)
        cutoff = current_watermark(db)["record_seq"]
        for _ in range(2):
            outs.append(asr.evaluate_control(db, ci, OCT, NOV, cutoff, persist=True, emit_event=True))
            db.commit()
    assert outs[0] == outs[1] and outs[0]["state"] == "FAILING" and outs[0]["evaluation_id"]
    evals, events, issues = _stored(ci_id)
    assert (len(evals), len(events), len(issues)) == (1, 1, 1)
    assert events[0].aggregate_id == evals[0].evaluation_id == outs[0]["evaluation_id"]


def test_a_refused_or_failed_evaluation_writes_nothing(client, org, monkeypatch):
    """Fault injection. An evaluator fault is an error (STA-REQ-022), never a state; a fault in the issue hook after the
    evaluation row was flushed rolls the evaluation back with it, so no FAILING evaluation exists without its issue."""
    from app.engines import assurance as asr
    from app.engines import governance
    ci, (td,) = _control(client, _subject())
    _run(client, td, "FAIL")

    def boom(*a, **k):
        raise RuntimeError("injected evaluator fault")
    monkeypatch.setattr(asr, "_aggregate", boom)
    with pytest.raises(RuntimeError, match="injected evaluator fault"):
        _evaluate(client, ci)
    monkeypatch.undo()
    assert _stored(ci) == ([], [], [])

    def issue_fault(db, **k):
        raise RuntimeError("injected issue-hook fault")
    monkeypatch.setattr(governance, "raise_control_issue", issue_fault)
    with pytest.raises(RuntimeError, match="injected issue-hook fault"):
        _evaluate(client, ci)
    monkeypatch.undo()
    assert _stored(ci) == ([], [], [])
    ok = _evaluate(client, ci)  # the same request once the fault is gone: one evaluation, its issue and its event
    assert ok.status_code == 200 and ok.json()["state"] == "FAILING"
    evals, events, issues = _stored(ci)
    assert (len(evals), len(events), len(issues)) == (1, 1, 1)


def test_the_five_outcomes_stay_distinct_and_none_is_operating(client, org, monkeypatch):
    """A valid failing evaluation, missing evidence, a configuration error, invalid input and an evaluation execution
    error each keep their own representation; none is OPERATING and none is converted into another."""
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlTestPolicy, TestDefinition
    from app.platform import next_record_seq
    failing, (tf,) = _control(client, _subject())
    _run(client, tf, "FAIL")
    missing, (tm,) = _control(client, _subject())
    _run(client, tm, "PASS")  # a PASS without evidence proves nothing
    broken, _ = _control(client, _subject())
    with AdminSession() as db:  # a stored policy predating the API check (k outside 1..N)
        pol = ControlTestPolicy(tenant_id=TA, control_implementation_id=broken, aggregation_mode="K_OF_N_TESTS",
                                k_threshold=5, automation_requirement="NONE", version=2, record_seq=next_record_seq(db))
        db.add(pol)
        db.flush()
        db.add(TestDefinition(tenant_id=TA, policy_version_id=pol.policy_version_id, name="t", source_class="MANUAL"))
        db.commit()
    f = _evaluate(client, failing)
    assert (f.status_code, f.json()["state"], f.json()["reason_codes"]) == (200, "FAILING", ["TEST_FAIL"])
    m = _evaluate(client, missing)
    assert (m.status_code, m.json()["state"], m.json()["reason_codes"]) == (200, "NOT_EVALUATED", ["MISSING_EVIDENCE"])
    c = _evaluate(client, broken)
    assert c.status_code == 409 and c.json()["detail"]["code"] == "CONTROL_TEST_POLICY_INVALID"
    i = _evaluate(client, failing, NOV, OCT)
    assert i.status_code == 422 and i.json()["detail"] == EMPTY_WINDOW
    monkeypatch.setattr(asr, "_aggregate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("engine fault")))
    with pytest.raises(RuntimeError):
        _evaluate(client, missing)
    monkeypatch.undo()
    assert len(_stored(missing)[0]) == 1 and len(_stored(broken)[0]) == 0


def test_a_configuration_error_has_one_representation_on_every_surface(client, org):
    """The 409 body of POST /evaluate and GET /state is exactly the object the list and the detail embed in place of a
    state (state null: no state is fabricated, STA §15/§18). It names records by ID only and stays in its organisation."""
    import json
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    from app.platform import next_record_seq
    ci, _ = _control(client, _subject())
    with AdminSession() as db:
        pol = ControlTestPolicy(tenant_id=TA, control_implementation_id=ci, aggregation_mode="NOT_A_MODE",
                                automation_requirement="NONE", version=2, record_seq=next_record_seq(db))
        db.add(pol)
        db.flush()
        db.add(TestDefinition(tenant_id=TA, policy_version_id=pol.policy_version_id, name="t", source_class="MANUAL"))
        db.commit()
        policy_id = pol.policy_version_id
    evaluated = _evaluate(client, ci)
    state = client.get(f"/api/controls/{ci}/state", headers=H(OWNER),
                       params={"window_from": OCT.isoformat(), "window_to": NOV.isoformat()})
    listed = next(c for c in client.get("/api/controls", headers=H(OWNER)).json() if c["control_implementation_id"] == ci)
    detail = client.get(f"/api/controls/{ci}", headers=H(OWNER)).json()
    assert evaluated.status_code == state.status_code == 409
    body = evaluated.json()["detail"]
    assert body == state.json()["detail"] == listed["current_state"]["configuration_error"] == \
        detail["evaluation_preview"]["configuration_error"]
    assert body == {"error": "ASSURANCE_CONFIGURATION_INVALID", "code": "CONTROL_TEST_POLICY_INVALID",
                    "message": body["message"], "control_implementation_id": ci, "policy_version_id": policy_id}
    assert listed["current_state"]["state"] is None and detail["evaluation_preview"]["state"] is None
    json.dumps(body)  # serialisable as stored and sent
    assert client.get(f"/api/controls/{ci}", headers=H(B_CCO)).status_code in (403, 404)
    assert _stored(ci) == ([], [], [])
