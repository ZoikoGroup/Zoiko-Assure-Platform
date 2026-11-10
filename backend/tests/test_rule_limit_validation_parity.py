"""RUL-REQ-029 validation/runtime parity and the workbench sandbox refusal (pre-push audit finding F4).

ZA-RUL-001 §11 "Compiler resource limits": every limit is a "Validation error" above its default, and RUL-REQ-029
"Rule complexity limits MUST be enforced at validation and runtime; budget failures are infrastructure errors, not
UNCERTAIN". The audit found that AST-mode validation did not measure decision-table rows or conditions per row (only
the decision-table compiler did), so an authored AST row with 41 conditions validated and then failed at runtime, and
/sandbox let that runtime error escape unhandled. These tests prove:

  * all six limits: the exact default passes validation and runtime; one over fails both, with the limit named;
  * table rows and conditions per row are measured on authored ASTs, top-level and nested, like the runtime does;
  * validation refuses everything the runtime guard would refuse (parity, on every limit);
  * malformed ASTs and decision tables are refused as schema errors, never raised;
  * /sandbox answers a draft over a limit with a structured refusal (422 at validation; the RUL-REQ-029
    EVALUATOR_UNAVAILABLE / RUL_RESOURCE_LIMIT error should the runtime guard refuse), never an outcome, and writes
    nothing.

Not covered: the §11 "Evaluation CPU/time budget" ("Runtime configured") and the evaluator memory budget have no
approved value; RUL-REQ-029 stays partial until they do.
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import pytest

from conftest import H

SPEC = {"max_depth": 32, "max_nodes": 500, "max_inline_set": 100, "max_table_rows": 250,
        "max_conditions_per_row": 40, "max_source_mappings_per_node": 20}
FACT = "entity.established_in_eu"  # a released BOOLEAN FactDefinition of the seed
FD = {FACT: {"data_type": "BOOLEAN", "status": "RELEASED"}, "f.e": {"data_type": "ENUM", "status": "RELEASED"}}
AUTHOR = "actor-content-analyst"


def _exists(nid):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": "EXISTS",
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}_f", "fact": FACT}]}


def _row(rid, n):
    return {"node_type": "ALL", "node_id": f"row_{rid}", "row_id": rid, "source_mappings": [f"sm_{rid}"],
            "children": [_exists(f"{rid}_c{i}") for i in range(n)]}


def _table(rows, conds=1, tid="dt"):
    """An authored (AST-mode) decision table: an ANY root marked decision_table over row_id nodes."""
    return {"node_type": "ANY", "node_id": tid, "decision_table": True, "source_mappings": [f"sm_{tid}"],
            "children": [_row(f"{tid}r{i}", conds) for i in range(rows)]}


def _nested(node):
    """``node`` two levels down inside an ordinary expression."""
    return {"node_type": "ALL", "node_id": "root", "source_mappings": ["sm_root"], "children": [
        _exists("top"), {"node_type": "NOT", "node_id": "not1", "child": node}]}


def _depth(d):
    node = _exists("leaf")
    node["source_mappings"] = ["sm_leaf"]
    for i in range(d - 2):
        node = {"node_type": "NOT", "node_id": f"not{i}", "child": node}
    return node


def _nodes(n):
    """ANY over predicates, exactly n node objects (root 1, EXISTS 2, EQ 3)."""
    rest = n - 1
    odd = rest % 2
    kids = [_exists(f"x{i}") for i in range((rest - 3 * odd) // 2)]
    if odd:
        kids.append({"node_type": "PREDICATE", "node_id": "odd", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": "odd_f", "fact": FACT},
            {"node_type": "CONST", "node_id": "odd_c", "type": "BOOLEAN", "value": True}]})
    return {"node_type": "ANY", "node_id": "root", "source_mappings": ["sm_root"], "children": kids}


def _inline(k):
    return {"node_type": "ALL", "node_id": "root", "source_mappings": ["sm_root"], "children": [
        _exists("a"), {"node_type": "PREDICATE", "node_id": "in", "operator": "IN", "args": [
            {"node_type": "FACT_REF", "node_id": "in_f", "fact": "f.e"},
            {"node_type": "CONST", "node_id": "in_c", "type": "SET<ENUM>", "value": [f"V{i}" for i in range(k)]}]}]}


def _mappings(k):
    return {"node_type": "ALL", "node_id": "root", "source_mappings": [f"sm_{i}" for i in range(k)],
            "children": [_exists("a"), _exists("b")]}


BUILD = {"max_depth": _depth, "max_nodes": _nodes, "max_inline_set": _inline, "max_source_mappings_per_node": _mappings,
         "max_table_rows": lambda k: _table(k), "max_conditions_per_row": lambda k: _table(1, k)}


def _only(monkeypatch, limit):
    """Every other limit out of the way, so one limit is measured on its own (250 one-condition rows are 751 nodes)."""
    from app.engines import rules
    for k in rules.LIMITS:
        if k != limit:
            monkeypatch.setitem(rules.LIMITS, k, 10**9)


def _flagged(rep) -> list[str]:
    return sorted({i["limit"] for i in rep["issues"] if i["code"] == "RUL_RESOURCE_LIMIT"})


def test_the_limits_are_the_za_rul_001_defaults():
    from app.engines import rules
    assert rules.LIMITS == SPEC


@pytest.mark.parametrize("limit", sorted(SPEC))
def test_each_limit_passes_at_its_default_and_fails_one_over_at_validation_and_runtime(monkeypatch, limit):
    from app.engines import rules
    _only(monkeypatch, limit)
    at, over = BUILD[limit](SPEC[limit]), BUILD[limit](SPEC[limit] + 1)
    rep_at = rules.validate(at, FD, {})
    assert rep_at["ok"], rep_at["issues"]
    assert rules.resource_limit_violations(at) == []
    rep_over = rules.validate(over, FD, {})
    assert not rep_over["ok"] and _flagged(rep_over) == [limit]
    assert [v["limit"] for v in rules.resource_limit_violations(over)] == [limit]


@pytest.mark.parametrize("where", ["top", "nested"])
def test_an_authored_row_with_41_conditions_fails_validation(where):
    from app.engines import rules
    ast = _table(1, 41) if where == "top" else _nested(_row("deep", 41))
    rep = rules.validate(ast, FD, {})
    assert not rep["ok"] and _flagged(rep) == ["max_conditions_per_row"]
    issue = next(i for i in rep["issues"] if i.get("limit") == "max_conditions_per_row")
    assert issue["node_id"] == ("row_dtr0" if where == "top" else "row_deep") and issue["severity"] == "ERROR"
    with pytest.raises(rules.RuleResourceLimitError):  # and the runtime refuses the same rule
        rules.evaluate(ast, {FACT: {"known": True, "value": True, "revision_id": "r"}}, {FACT: "BOOLEAN"}, {},
                       {"effective_at": "2026-01-01"})


@pytest.mark.parametrize("where", ["top", "nested"])
def test_an_authored_table_over_the_row_limit_fails_validation(where):
    from app.engines import rules
    table = _table(251)
    ast = table if where == "top" else _nested(table)
    rep = rules.validate(ast, FD, {})
    assert not rep["ok"] and "max_table_rows" in _flagged(rep)  # at the defaults it is also over max_nodes
    assert {v["limit"] for v in rules.resource_limit_violations(ast)} <= set(_flagged(rep))


def test_validation_refuses_everything_the_runtime_guard_refuses():
    """Parity on one AST that breaks every limit, at the defaults: whatever the runtime would refuse, validation
    reports (by limit name), so a rule this validator accepts never fails its budget at evaluation."""
    from app.engines import rules
    deep = _depth(40)
    wide = _inline(101)
    wide["node_id"] = "wide"
    for n in (wide["children"][0], wide["children"][1]):
        n["node_id"] = "w_" + n["node_id"]
    for n in wide["children"][1]["args"]:
        n["node_id"] = "w_" + n["node_id"]
    ast = {"node_type": "ALL", "node_id": "everything", "source_mappings": [f"sm_{i}" for i in range(21)],
           "children": [deep, wide, _table(251, tid="big"), _table(1, 41, tid="row")]}
    runtime = {v["limit"] for v in rules.resource_limit_violations(ast)}
    assert runtime == set(SPEC)
    assert set(_flagged(rules.validate(ast, FD, {}))) == runtime


@pytest.mark.parametrize("ast", [
    {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"], "children": 5},
    {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"], "children": {"a": 1}},
    {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"],
     "children": [{"node_type": "PREDICATE", "node_id": "p", "operator": "EXISTS", "args": {"a": 1}}, _exists("q")]},
    {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"],
     "children": [{"node_type": "PREDICATE", "node_id": "p", "operator": "EXISTS", "args": [5]}, _exists("q")]},
    {"node_type": "ALL", "node_id": "r", "source_mappings": 7, "children": [_exists("a"), _exists("b")]},
    {"node_type": "ANY", "node_id": "r", "decision_table": True, "source_mappings": ["s"],
     "children": [{"node_type": "ALL", "node_id": "row", "row_id": "x", "source_mappings": ["s"], "children": "abc"}]},
], ids=["children-int", "children-dict", "args-dict", "args-scalar", "mappings-int", "row-children-str"])
def test_malformed_asts_are_schema_errors_never_exceptions(ast):
    from app.engines import rules
    rep = rules.validate(ast, FD, {})
    assert not rep["ok"] and "RUL_SCHEMA_INVALID" in {i["code"] for i in rep["issues"]}
    rules.resource_limit_violations(ast)  # the runtime guard measures it without raising


@pytest.mark.parametrize("table", [
    "not-a-table", {"columns": 3, "rows": []}, {"columns": [{"column_id": "c"}], "rows": []},
    {"columns": [{"column_id": "c", "fact": FACT}], "rows": [5]},
    {"columns": [{"column_id": "c", "fact": FACT}], "rows": [{"row_id": "r", "cells": [1]}]},
    {"table_mode": "SINGLE_MATCH_VALIDATED", "columns": [{"column_id": "c", "fact": FACT}],
     "rows": [{"row_id": "a", "source_mappings": ["s"]}, {"row_id": "b", "source_mappings": ["s"]}]},
], ids=["string", "columns-int", "column-without-fact", "row-scalar", "cells-list", "rows-without-cells"])
def test_malformed_decision_tables_are_refused_never_raised(table):
    from app.engines import rules
    ast, issues = rules.compile_decision_table(table)
    assert any(i["severity"] == "ERROR" for i in issues)
    if ast is None:
        assert {i["code"] for i in issues} == {"RUL_SCHEMA_INVALID"}


# ------------------------------------------------------------------------------------------------ API: /sandbox
@pytest.fixture(scope="module")
def odef(client):
    from app.db import AdminSession
    from app.models import ObligationDefinition
    with AdminSession() as db:
        return db.query(ObligationDefinition).filter_by(key="DORA_ICT_RISK_FRAMEWORK").one().obligation_definition_id


def _draft(client, odef, ast) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": ast, "valid_from": "2026-01-01",
        "change_class": "EXECUTABLE_OUTCOME", "source_mappings": []})
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _state(draft_id):
    from app.db import AdminSession
    from app.models import RuleDraft
    from app.platform import current_watermark
    with AdminSession() as db:
        d = db.get(RuleDraft, draft_id)
        return current_watermark(db)["record_seq"], (d.revision, d.status, d.validation_report, d.vector_report,
                                                     d.shadow_report)


def _sandbox(client, draft_id):
    return client.post(f"/api/rules/drafts/{draft_id}/sandbox", headers=H(AUTHOR), json={"facts": {FACT: True}})


@pytest.mark.parametrize("where", ["top", "nested"])
def test_sandbox_refuses_a_draft_over_a_limit_at_validation_and_writes_nothing(client, odef, where):
    did = _draft(client, odef, _table(1, 41) if where == "top" else _nested(_row("deep", 41)))
    before = _state(did)
    r = _sandbox(client, did)
    assert r.status_code == 422, r.text
    issues = r.json()["detail"]["validation"]["issues"]
    assert [i["limit"] for i in issues if i["code"] == "RUL_RESOURCE_LIMIT"] == ["max_conditions_per_row"]
    assert "outcome" not in r.json() and _state(did) == before


def test_sandbox_answers_a_runtime_budget_refusal_with_the_structured_error(client, odef, monkeypatch):
    """Should validation and the runtime guard ever disagree (here: validation is made to miss the limit), /sandbox
    still answers with the RUL-REQ-029 refusal, never an outcome and never an unhandled exception."""
    from app.engines import rules
    did = _draft(client, odef, _table(1, 41))
    real = rules.validate
    monkeypatch.setattr(rules, "validate", lambda *a, **k: {**(rep := real(*a, **k)), "ok": True, "error_count": 0,
                                                            "issues": [i for i in rep["issues"]
                                                                       if i["code"] != "RUL_RESOURCE_LIMIT"]})
    before = _state(did)
    r = _sandbox(client, did)
    assert r.status_code == 500, r.text
    body = r.json()["detail"]
    assert (body["error"], body["reason"], body["draft_id"], body["retryable"]) == \
        ("EVALUATOR_UNAVAILABLE", "RUL_RESOURCE_LIMIT", did, False)
    assert [v["limit"] for v in body["violations"]] == ["max_conditions_per_row"]
    assert "outcome" not in body and "truth" not in r.text and _state(did) == before


def test_sandbox_answers_the_evaluators_own_guard_with_the_structured_error(client, odef, monkeypatch):
    """The evaluator's own guard (rules.evaluate raising RuleResourceLimitError) is answered the same way."""
    from app.engines import rules
    did = _draft(client, odef, _table(1, 2))

    def over_budget(*a, **k):
        raise rules.RuleResourceLimitError([{"limit": "max_nodes", "allowed": 500, "actual": 501, "node_id": None,
                                             "message": "probe"}])
    monkeypatch.setattr(rules, "evaluate", over_budget)
    monkeypatch.setattr(rules, "run_golden_vectors", lambda *a, **k: {"results": [{"vector_id": "sandbox"}]})
    r = _sandbox(client, did)
    assert r.status_code == 500 and r.json()["detail"]["reason"] == "RUL_RESOURCE_LIMIT", r.text


def test_sandbox_within_the_limits_still_evaluates(client, odef):
    did = _draft(client, odef, _table(1, 40))
    r = _sandbox(client, did)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] in ("APPLIES", "DOES_NOT_APPLY", "UNCERTAIN")


def test_sandbox_of_an_unknown_draft_is_not_found(client):
    assert _sandbox(client, "no-such-draft").status_code == 404
