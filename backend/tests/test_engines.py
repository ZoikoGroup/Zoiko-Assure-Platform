"""Pure-engine conformance tests: rule language, roll-up semantics, IPE derivation, ledger/Merkle."""
import itertools

import pytest

from app.engines import assurance as asr
from app.engines import evidence as evd
from app.engines import ipe, rules

FD = {"a": {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"},
      "b": {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"},
      "role": {"data_type": "ENUM", "enum_domain": ["DEPLOYER", "PROVIDER"], "status": "RELEASED"},
      "n": {"data_type": "INTEGER", "enum_domain": None, "status": "RELEASED"}}
FT = {k: v["data_type"] for k, v in FD.items()}


def P(nid, fact):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": ["s"],
            "args": [{"node_type": "FACT_REF", "node_id": nid + "f", "fact": fact},
                     {"node_type": "CONST", "node_id": nid + "c", "type": "BOOLEAN", "value": True}]}


def facts(a, b):
    def f(v):
        return {"known": False, "reason": "MISSING_REQUIRED_FACT"} if v == "UNKNOWN" else {"known": True, "value": v == "TRUE"}
    return {"a": f(a), "b": f(b)}


EXPECT_ALL = {("TRUE", "TRUE"): "TRUE", ("TRUE", "FALSE"): "FALSE", ("TRUE", "UNKNOWN"): "UNKNOWN",
              ("FALSE", "TRUE"): "FALSE", ("FALSE", "FALSE"): "FALSE", ("FALSE", "UNKNOWN"): "FALSE",
              ("UNKNOWN", "TRUE"): "UNKNOWN", ("UNKNOWN", "FALSE"): "FALSE", ("UNKNOWN", "UNKNOWN"): "UNKNOWN"}
EXPECT_ANY = {("TRUE", "TRUE"): "TRUE", ("TRUE", "FALSE"): "TRUE", ("TRUE", "UNKNOWN"): "TRUE",
              ("FALSE", "TRUE"): "TRUE", ("FALSE", "FALSE"): "FALSE", ("FALSE", "UNKNOWN"): "UNKNOWN",
              ("UNKNOWN", "TRUE"): "TRUE", ("UNKNOWN", "FALSE"): "UNKNOWN", ("UNKNOWN", "UNKNOWN"): "UNKNOWN"}


@pytest.mark.parametrize("a,b", list(itertools.product(["TRUE", "FALSE", "UNKNOWN"], repeat=2)))
def test_three_valued_truth_tables(a, b):
    for kind, exp in (("ALL", EXPECT_ALL), ("ANY", EXPECT_ANY)):
        ast = {"node_type": kind, "node_id": "r", "children": [P("p1", "a"), P("p2", "b")]}
        assert rules.evaluate(ast, facts(a, b), FT, {}, {"effective_at": "2027-01-01"})["truth"] == exp[(a, b)]


def test_unknown_never_coerces_to_false_and_materiality():
    ast = {"node_type": "ALL", "node_id": "r", "children": [P("p1", "a"), P("p2", "b")]}
    out = rules.evaluate(ast, facts("TRUE", "UNKNOWN"), FT, {}, {"effective_at": "2027-01-01"})
    assert out["outcome"] == "UNCERTAIN" and out["missing_facts"] == ["b"]
    out = rules.evaluate(ast, facts("FALSE", "UNKNOWN"), FT, {}, {"effective_at": "2027-01-01"})
    assert out["outcome"] == "DOES_NOT_APPLY" and out["missing_facts"] == []


def test_static_type_checking_rejects_coercion():
    bad = {"node_type": "PREDICATE", "node_id": "x", "operator": "EQ", "source_mappings": ["s"],
           "args": [{"node_type": "FACT_REF", "node_id": "f", "fact": "n"}, {"node_type": "CONST", "node_id": "c", "type": "STRING", "value": "5"}]}
    rep = rules.validate(bad, FD, {})
    assert not rep["ok"] and any(i["code"] == "RUL_TYPE_MISMATCH" for i in rep["issues"])


def test_enum_domain_and_prohibited_constructs():
    bad = {"node_type": "PREDICATE", "node_id": "x", "operator": "EQ", "source_mappings": ["s"], "regex": ".*",
           "args": [{"node_type": "FACT_REF", "node_id": "f", "fact": "role"}, {"node_type": "CONST", "node_id": "c", "type": "ENUM", "value": "PIRATE"}]}
    codes_ = {i["code"] for i in rules.validate(bad, FD, {})["issues"]}
    assert {"RUL_PROHIBITED_CONSTRUCT", "RUL_TYPE_MISMATCH"} <= codes_


def test_source_mapping_required():
    p = P("p1", "a")
    p["source_mappings"] = []
    rep = rules.validate({"node_type": "ALL", "node_id": "r", "children": [p, P("p2", "b")]}, FD, {})
    assert any(i["code"] == "RUL_SOURCE_MAPPING_REQUIRED" for i in rep["issues"])


def test_decision_table_first_match_and_blank_prohibited():
    _, issues = rules.compile_decision_table({"table_mode": "FIRST_MATCH", "columns": [{"column_id": "c", "fact": "a", "value_type": "BOOLEAN"}],
                                              "rows": [{"row_id": "R1", "cells": {"c": None}, "source_mappings": ["s"]}]})
    codes_ = {i["code"] for i in issues}
    assert "RUL_PROHIBITED_CONSTRUCT" in codes_ and "RUL_DECISION_CELL_BLANK" in codes_


def test_single_match_invariant_breach_is_error_not_uncertain():
    table = {"table_mode": "SINGLE_MATCH_VALIDATED", "columns": [{"column_id": "c1", "fact": "a", "value_type": "BOOLEAN"},
                                                                {"column_id": "c2", "fact": "b", "value_type": "BOOLEAN"}],
             "rows": [{"row_id": "R1", "cells": {"c1": True, "c2": "ANY"}, "source_mappings": ["s"]},
                      {"row_id": "R2", "cells": {"c1": "ANY", "c2": True}, "source_mappings": ["s"]}]}
    ast, issues = rules.compile_decision_table(table)
    assert any(i["code"] == "RUL_DECISION_OVERLAP" for i in issues)
    with pytest.raises(rules.RuleError) as e:
        rules.evaluate(ast, facts("TRUE", "TRUE"), FT, {}, {"effective_at": "2027-01-01"})
    assert e.value.code == "RUNTIME_RULE_INVARIANT_BREACH"


def test_canonical_hash_stable_and_semantic():
    a = {"node_type": "ALL", "node_id": "r", "children": [P("p1", "a"), P("p2", "b")]}
    b = {"children": [P("p1", "a"), P("p2", "b")], "node_id": "r", "node_type": "ALL"}
    assert rules.ast_hash(a, {}) == rules.ast_hash(b, {})
    c = {"node_type": "ANY", "node_id": "r", "children": [P("p1", "a"), P("p2", "b")]}
    assert rules.ast_hash(a, {}) != rules.ast_hash(c, {})


S, U, K, R = asr.SAT, asr.UNSAT, asr.UNK, asr.REV


def test_expression_four_valued_semantics():
    assert asr.combine_all([S, U, R]) == U and asr.combine_all([S, R, K]) == R and asr.combine_all([S, K]) == K
    assert asr.combine_any([U, S]) == S and asr.combine_any([U, R, K]) == R and asr.combine_any([U, U]) == U
    assert asr.combine_k([S, S, U], 2) == S and asr.combine_k([S, U, U], 2) == U
    assert asr.combine_k([S, R, U], 2) == R and asr.combine_k([S, K, U], 2) == K


def test_compensation_only_when_valid():
    node = {"node_type": "COMPENSATION", "node_id": "c", "compensation_id": "x",
            "primary": {"node_type": "CONTROL", "node_id": "p", "control_key": "P"},
            "compensating": {"node_type": "CONTROL", "node_id": "q", "control_key": "Q"}}
    leaf = lambda k: ((U if k == "P" else S), {})
    assert asr.eval_expression(node, leaf, lambda c: (True, {}), lambda: (S, {}))["result"] == S
    assert asr.eval_expression(node, leaf, lambda c: (False, {}), lambda: (S, {}))["result"] == U  # expired compensation -> primary failure stands


def test_degraded_never_satisfies():
    assert asr.LEAF_MAP["DEGRADED"] == K and asr.SEGMENT_STATE[K] == "NOT_PROVEN"


class _Pol:
    def __init__(self, req=True, comp=True):
        self.completeness_required, self.accuracy_required = comp, req


@pytest.mark.parametrize("c,a,exp", [("VERIFIED", "VERIFIED", "IPE_VERIFIED"), ("FAILED", "VERIFIED", "IPE_FAILED"),
                                     ("VERIFIED", "FAILED", "IPE_FAILED"), ("INCONCLUSIVE", "VERIFIED", "IPE_INCONCLUSIVE"),
                                     ("VERIFIED", "INCONCLUSIVE", "IPE_INCONCLUSIVE"), ("NOT_REQUIRED", "VERIFIED", "IPE_VERIFIED"),
                                     ("NOT_REQUIRED", "NOT_REQUIRED", "IPE_NOT_REQUIRED")])
def test_ipe_overall_table(c, a, exp):
    assert ipe.derive_overall(_Pol(), c, a) == exp


def test_ledger_tamper_detection_and_merkle_proof():
    body = {"event_type": "MANIFEST_CREATED", "evidence_id": "e1"}
    h1 = evd.ledger_hash("t", 1, evd.GENESIS_HASH, body)
    assert evd.ledger_hash("t", 1, evd.GENESIS_HASH, {**body, "evidence_id": "e2"}) != h1
    leaves = [evd.leaf_hash(f"t{i}", i + 1, h1) for i in range(5)]
    root, proofs = evd.merkle_root_and_proofs(leaves)
    for i, leaf in enumerate(leaves):
        h = leaf
        for s in proofs[i]:
            h = evd._node(h, s["sibling"]) if s["position"] == "RIGHT" else evd._node(s["sibling"], h)
        assert h == root
