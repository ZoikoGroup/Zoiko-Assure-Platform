"""RUL-REQ-029: "Rule complexity limits MUST be enforced at validation and runtime; budget failures are infrastructure
errors, not UNCERTAIN."

The limits are the ZA-RUL-001 §11 "Compiler resource limits -- v1 safety defaults" (rules.LIMITS). Validation already
enforced them; these tests cover the runtime half:
  * the evaluator measures the AST it is about to execute, before any node runs, exactly as validation measures it;
  * a rule over a limit is refused with the APP §12 error EVALUATOR_UNAVAILABLE (cause RUL_RESOURCE_LIMIT, every limit
    exceeded listed), never an outcome or UNCERTAIN; no fact is read and nothing is written;
  * every path that executes a binding RuleVersion refuses it: single evaluation, evaluate-all, fact-change
    re-evaluation, publication impact, history comparison and replay (which reports NOT_REPRODUCED, never an
    approximation);
  * concurrent evaluations keep their locking and collapse behaviour; rules within the limits behave as before.

A published rule is over a runtime limit when the limit is lowered after its publication ("They may be changed by ADR
after production measurements", §11). The tests lower rules.LIMITS the same way, after publishing through the API.

Not covered, because ZA-RUL-001 §11 gives no value or unit for it: the "Evaluation CPU/time budget" ("Runtime
configured") and the §16 evaluator memory budget.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RCB-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TEN, CCO = f"tenant-rcb-{TAG.lower()}", f"rcb-cco-{TAG.lower()}"
S: dict = {}
_n = iter(range(10**6))
SPEC_LIMITS = {"max_depth": 32, "max_nodes": 500, "max_inline_set": 100, "max_table_rows": 250,
               "max_conditions_per_row": 40, "max_source_mappings_per_node": 20}


# ================================================================================================ pure evaluator
FT = {"f.b": "BOOLEAN", "f.e": "ENUM"}
FACTS = {"f.b": {"known": True, "value": True, "revision_id": "r1"},
         "f.e": {"known": True, "value": "V0", "revision_id": "r2"}}
FACT_DEFS = {"f.b": {"data_type": "BOOLEAN", "status": "RELEASED"}, "f.e": {"data_type": "ENUM", "status": "RELEASED"}}


def _p(nid, fact="f.b", value=True, typ="BOOLEAN", op="EQ"):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}_f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{nid}_c", "type": typ, "value": value}]}


def _exists(nid):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": "EXISTS", "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}_f", "fact": "f.b"}]}


def _depth(d):
    """NOT^(d-2) over one predicate: the predicate's arguments sit at depth d. An even NOT count keeps it TRUE."""
    node = _p("leaf")
    for i in range(d - 2):
        node = {"node_type": "NOT", "node_id": f"not{i}", "child": node}
    return node


def _nodes(n):
    """ANY over predicates, exactly n node objects (EXISTS = 2 nodes, EQ = 3, root = 1)."""
    rest = n - 1
    kids = [] if rest % 2 == 0 else [_p("eq")]
    rest -= 0 if rest % 2 == 0 else 3
    kids += [_exists(f"x{i}") for i in range(rest // 2)]
    return {"node_type": "ANY", "node_id": "root", "source_mappings": ["sm_root"], "children": kids}


def _inline(k):
    return {"node_type": "ALL", "node_id": "root", "source_mappings": ["sm_root"], "children": [
        _p("a"), _p("in", fact="f.e", value=[f"V{i}" for i in range(k)], typ="SET<ENUM>", op="IN")]}


def _mappings(k):
    return {"node_type": "ALL", "node_id": "root", "source_mappings": [f"sm_r{i}" for i in range(k)],
            "children": [_p("a"), _p("b", fact="f.e", value="V0", typ="ENUM")]}


def _table(rows, conditions=1, tid="dt"):
    from app.engines import rules
    cols = [{"column_id": f"c{j}", "fact": "f.e", "operator": "EQ", "value_type": "ENUM"} for j in range(conditions)]
    ast, _ = rules.compile_decision_table({
        "decision_table_id": tid, "table_mode": "ANY_MATCH", "columns": cols, "source_mappings": [f"sm_{tid}"],
        "rows": [{"row_id": f"{tid}r{i}", "cells": {c["column_id"]: f"V{i}" for c in cols},
                  "source_mappings": [f"sm_{tid}r{i}"]} for i in range(rows)]})
    return ast


BUILD = {"max_depth": _depth, "max_nodes": _nodes, "max_inline_set": _inline,
         "max_source_mappings_per_node": _mappings, "max_table_rows": lambda k: _table(k),
         "max_conditions_per_row": lambda k: _table(1, k)}


def _only(monkeypatch, limit):
    """Every other limit out of the way, so one limit is measured on its own (a 250-row table alone has >500 nodes)."""
    from app.engines import rules
    for k in rules.LIMITS:
        if k != limit:
            monkeypatch.setitem(rules.LIMITS, k, 10**9)


def _run(ast):
    from app.engines import rules
    return rules.evaluate(ast, FACTS, FT, {}, {"effective_at": "2026-01-01"})


def test_runtime_limits_are_the_za_rul_001_v1_defaults():
    from app.engines import rules
    assert rules.LIMITS == SPEC_LIMITS


@pytest.mark.parametrize("limit", sorted(SPEC_LIMITS))
def test_exact_boundary_runs_and_one_over_is_refused(monkeypatch, limit):
    from app.engines import rules
    _only(monkeypatch, limit)
    at = BUILD[limit](SPEC_LIMITS[limit])
    assert rules.resource_limit_violations(at) == []
    assert _run(at)["outcome"] in ("APPLIES", "DOES_NOT_APPLY", "UNCERTAIN")
    over = BUILD[limit](SPEC_LIMITS[limit] + 1)
    with pytest.raises(rules.RuleResourceLimitError) as e:
        _run(over)
    assert e.value.code == "RUL_RESOURCE_LIMIT"
    assert [(v["limit"], v["allowed"], v["actual"]) for v in e.value.violations] == \
        [(limit, SPEC_LIMITS[limit], SPEC_LIMITS[limit] + 1)]


def test_every_simultaneous_violation_is_reported_in_a_fixed_order():
    from app.engines import rules
    leaf = _p("leaf", fact="f.e", value=[f"V{i}" for i in range(101)], typ="SET<ENUM>", op="IN")
    leaf["source_mappings"] = [f"sm{i}" for i in range(21)]
    node = leaf
    for i in range(32):  # root 1, NOTs 2..33, leaf 34, its arguments at depth 35
        node = {"node_type": "NOT", "node_id": f"not{i}", "child": node}
    ast = {"node_type": "ALL", "node_id": "root", "source_mappings": ["sm_root"],
           "children": [node, _table(251), _table(1, 41, tid="wide")]}
    v = rules.resource_limit_violations(ast)
    assert [x["limit"] for x in v] == ["max_depth", "max_nodes", "max_inline_set", "max_table_rows",
                                       "max_conditions_per_row", "max_source_mappings_per_node"]
    assert v[0]["actual"] == 35 and v[2]["node_id"] == "leaf_c" and v[3] == {**v[3], "node_id": "dt", "actual": 251}
    assert v[4] == {**v[4], "node_id": "row_wider0", "actual": 41} and v[5] == {**v[5], "node_id": "leaf", "actual": 21}
    assert rules.resource_limit_violations(ast) == v  # deterministic
    with pytest.raises(rules.RuleResourceLimitError) as e:
        _run(ast)
    assert e.value.violations == v


def test_execution_stops_before_any_node_runs(monkeypatch):
    from app.engines import rules
    calls = []
    real = rules._Eval.truth
    monkeypatch.setattr(rules._Eval, "truth", lambda self, n: calls.append(n["node_id"]) or real(self, n))
    with pytest.raises(rules.RuleResourceLimitError):
        _run(_depth(33))
    assert calls == []
    assert _run(_depth(32))["outcome"] == "APPLIES" and len(calls) == 31  # 30 NOTs and the predicate


def test_a_pathologically_deep_ast_is_refused_not_a_recursion_error():
    from app.engines import rules
    ast = _depth(5000)  # far beyond the interpreter's recursion limit if it were walked recursively
    with pytest.raises(rules.RuleResourceLimitError) as e:
        _run(ast)
    assert e.value.violations[0]["limit"] == "max_depth" and e.value.violations[0]["actual"] == 5000


@pytest.mark.parametrize("limit", ["max_depth", "max_nodes", "max_inline_set", "max_source_mappings_per_node"])
def test_runtime_measures_exactly_what_validation_measures(monkeypatch, limit):
    from app.engines import rules
    _only(monkeypatch, limit)
    for k in (SPEC_LIMITS[limit], SPEC_LIMITS[limit] + 1):
        ast = BUILD[limit](k)
        flagged = any(i["code"] == "RUL_RESOURCE_LIMIT" for i in rules.validate(ast, FACT_DEFS, {})["issues"])
        assert flagged == bool(rules.resource_limit_violations(ast)) == (k > SPEC_LIMITS[limit])


@pytest.mark.parametrize("limit", ["max_table_rows", "max_conditions_per_row"])
def test_runtime_table_limits_match_the_compiler(monkeypatch, limit):
    from app.engines import rules
    _only(monkeypatch, limit)
    cols = SPEC_LIMITS[limit] + 1 if limit == "max_conditions_per_row" else 1
    rows = SPEC_LIMITS[limit] + 1 if limit == "max_table_rows" else 1
    spec = {"decision_table_id": "dt", "table_mode": "ANY_MATCH", "source_mappings": ["sm_dt"],
            "columns": [{"column_id": f"c{j}", "fact": "f.e", "operator": "EQ"} for j in range(cols)],
            "rows": [{"row_id": f"r{i}", "cells": {f"c{j}": f"V{i}" for j in range(cols)}, "source_mappings": ["s"]}
                     for i in range(rows)]}
    ast, issues = rules.compile_decision_table(spec)
    assert any(i["code"] == "RUL_RESOURCE_LIMIT" for i in issues)
    assert [v["limit"] for v in rules.resource_limit_violations(ast)] == [limit]


def test_golden_vectors_report_the_limit_as_an_error_never_an_outcome():
    from app.engines import rules
    rep = rules.run_golden_vectors(_depth(33), [{"vector_id": "v", "facts": {"f.b": True}, "expected_outcome": "APPLIES",
                                                 "effective_at": "2027-12-02"}],  # explicit (RUL §13)
                                   FACT_DEFS, {})
    assert rep["all_passed"] is False and rep["results"][0]["error"] == "RUL_RESOURCE_LIMIT"
    assert "actual" not in rep["results"][0]


# ================================================================================================ API and database
ENTITY_TYPES = ["CREDIT_INSTITUTION", "PAYMENT_INSTITUTION", "E_MONEY_INSTITUTION", "INVESTMENT_FIRM", "ICT_THIRD_PARTY",
                "NONE"]  # the seeded entity.financial_entity_type domain


def _ast(tag: str, *, nest: int = 0, extra: int = 0, root_mappings: int = 1, inline: int = 0) -> dict:
    def pred(nid, fact, typ, value, op="EQ"):
        return {"node_type": "PREDICATE", "node_id": f"{tag}_{nid}", "operator": op, "source_mappings": [f"sm_{tag}_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{tag}_{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{tag}_{nid}c", "type": typ, "value": value}]}
    inner = pred("b", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")
    for i in range(nest):
        inner = {"node_type": "ALL", "node_id": f"{tag}_g{i}", "children": [pred(f"n{i}", "entity.established_in_eu",
                                                                                   "BOOLEAN", True), inner]}
    kids = [pred("a", "entity.established_in_eu", "BOOLEAN", True), inner]
    kids += [pred(f"x{i}", "entity.established_in_eu", "BOOLEAN", True) for i in range(extra)]
    if inline:  # members of the fact's ENUM domain (RUL §04; an inline set's members are validated against it)
        kids.append(pred("in", "entity.financial_entity_type", "SET<ENUM>", ENTITY_TYPES[:inline], op="IN"))
    return {"node_type": "ALL", "node_id": f"{tag}_root", "children": kids,
            "source_mappings": [f"sm_{tag}_root"] + [f"sm_{tag}_root{i}" for i in range(1, root_mappings)]}


def _cited(n, out):
    out.update(n.get("source_mappings") or [])
    for c in n.get("children") or []:
        _cited(c, out)
    return out


VECTORS = [
    {"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "APPLIES"},
    {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "CREDIT_INSTITUTION"}, "expected_outcome": "DOES_NOT_APPLY"},
    {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
     "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
     "expected_outcome": "UNCERTAIN"}]


def _definition() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RCB_{TAG}_{next(_n)}", title="Complexity budget obligation",
                                 instrument_id=inst.instrument_id, provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _publish(client, odef_id: str, valid_from: str = "2025-01-01", **shape) -> dict:
    tag = f"v{next(_n)}"
    ast = _ast(tag, **shape)
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": m, "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for m in sorted(_cited(ast, set()))]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": ast, "golden_vectors": VECTORS,
        "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"rcb-{did[:8]}", "changelog": "probe"})
    assert r.status_code == 200, r.text
    return r.json()


def _measure(rv_id: str) -> dict:
    """The published AST's own size, measured with every limit at zero (each measured quantity is then reported)."""
    from app.db import AdminSession
    from app.engines import rules
    from app.models import RuleVersion
    with AdminSession() as db:
        ast = db.get(RuleVersion, rv_id).canonical_ast
    saved = dict(rules.LIMITS)
    try:
        rules.LIMITS.update({k: 0 for k in rules.LIMITS})
        found = rules.resource_limit_violations(ast)
    finally:
        rules.LIMITS.update(saved)
    return {k: max((v["actual"] for v in found if v["limit"] == k), default=0) for k in saved}


def _subject(client, name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TEN, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TEN, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
    return sid


def _evaluate(client, sid, odef_id, **extra):
    return client.post("/api/applicability/evaluations", headers=H(CCO),
                       json={"subject_id": sid, "obligation_definition_id": odef_id, **extra})


def _evaluations(sid, odef_id):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return [e.evaluation_id for e in db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.subject_id == sid, ApplicabilityEvaluation.obligation_definition_id == odef_id)
            .order_by(ApplicabilityEvaluation.record_seq)).scalars()]


def _refusal(detail: dict, rv_id: str, limit: str, allowed: int, actual: int):
    assert detail["error"] == "EVALUATOR_UNAVAILABLE" and detail["reason"] == "RUL_RESOURCE_LIMIT", detail
    assert detail["rule_version_id"] == rv_id and detail["retryable"] is False
    assert [(v["limit"], v["allowed"], v["actual"]) for v in detail["violations"]] == [(limit, allowed, actual)]
    assert "outcome" not in detail


def _signals(rv_id: str) -> int:
    from app.db import admin_engine
    like = "payload::text LIKE :p" if admin_engine.dialect.name == "postgresql" else "payload LIKE :p"
    with admin_engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM platform_outbox_event WHERE event_type = "
                              f"'security.content_signature_failed' AND {like}"), {"p": f"%{rv_id}%"}).scalar()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Complexity Budget Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TEN, legal_name=f"{TEN} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TEN, actor_type="HUMAN", display_name="RCB CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Complexity Budget Act",
        "locator": f"OJ L {TAG}/rcb", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RCB-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 3", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 3(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return True


SHAPES = {"max_nodes": {"extra": 1}, "max_depth": {"nest": 2}, "max_source_mappings_per_node": {"root_mappings": 3},
          "max_inline_set": {"inline": 5}}


@pytest.mark.parametrize("limit", sorted(SHAPES))
def test_single_evaluation_at_and_over_the_limit(client, world, monkeypatch, limit):
    """At exactly the rule's size the limit admits it; one below, the rule is refused before input freeze: no fact read,
    no node run, no row, no outbox event, no integrity signal, no fallback to another version, no outcome."""
    from app.engines import applicability as appl
    from app.engines import rules
    odef = _definition()
    rv_id = _publish(client, odef, **SHAPES[limit])["rule_version"]["rule_version_id"]
    size = _measure(rv_id)[limit]
    sid = _subject(client, f"single{next(_n)}")
    monkeypatch.setitem(rules.LIMITS, limit, size)
    ok = _evaluate(client, sid, odef)
    assert ok.status_code == 200 and ok.json()["outcome"] == "APPLIES", ok.text
    sid2 = _subject(client, f"single{next(_n)}")
    monkeypatch.setitem(rules.LIMITS, limit, size - 1)
    reads, runs = [], []
    real_resolve, real_truth = appl.resolve_fact, rules._Eval.truth
    monkeypatch.setattr(appl, "resolve_fact", lambda *a, **k: reads.append(1) or real_resolve(*a, **k))
    monkeypatch.setattr(rules._Eval, "truth", lambda self, n: runs.append(1) or real_truth(self, n))
    before = db_fingerprint()
    r = _evaluate(client, sid2, odef)
    assert r.status_code == 500, r.text
    _refusal(r.json()["detail"], rv_id, limit, size - 1, size)
    assert (reads, runs) == ([], [])
    assert db_fingerprint() == before and _evaluations(sid2, odef) == [] and _signals(rv_id) == 0


def test_dependent_paths_report_the_refusal_and_write_no_evaluation(client, world, monkeypatch):
    """Fact-change re-evaluation and evaluate-all name the refusal per definition (the fact is still recorded); the
    refused definition gets no evaluation, determination or FactRequest."""
    from app.engines import rules
    odef = _definition()
    rv_id = _publish(client, odef, extra=2)["rule_version"]["rule_version_id"]
    sid = _subject(client, "dep")
    first = _evaluate(client, sid, odef)
    assert first.status_code == 200
    monkeypatch.setitem(rules.LIMITS, "max_nodes", _measure(rv_id)["max_nodes"] - 1)
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": "entity.established_in_eu", "value": False, "change_kind": "PROSPECTIVE_CHANGE", "reason": "moved",
        "valid_from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    mine = [x["error"] for x in r.json()["impact_reevaluations"]
            if isinstance(x.get("error"), dict) and x["error"].get("rule_version_id") == rv_id]
    assert len(mine) == 1 and mine[0]["error"] == "EVALUATOR_UNAVAILABLE" and mine[0]["reason"] == "RUL_RESOURCE_LIMIT"
    allr = client.post("/api/applicability/evaluate-all", headers=H(CCO), params={"subject_id": sid})
    assert allr.status_code == 200
    errs = [x["error"] for x in allr.json() if isinstance(x.get("error"), dict) and x["error"].get("rule_version_id") == rv_id]
    assert len(errs) == 1 and errs[0]["error"] == "EVALUATOR_UNAVAILABLE"
    assert _evaluations(sid, odef) == [first.json()["evaluation_id"]]  # nothing new for the refused definition
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    with AdminSession() as db:
        dets = db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.subject_id == sid,
                          ApplicabilityDetermination.obligation_definition_id == odef)).scalars().all()
    assert len(dets) == 1  # only the one derived from the first evaluation


def test_publication_impact_counts_the_refusal_and_is_incomplete(client, world, monkeypatch):
    """A new future-effective version is published while the version in force is over a lowered limit: the impact run
    re-evaluates under the version in force, is refused, and reports INCOMPLETE with the failure code, never no impact."""
    from app.engines import rules
    odef = _definition()
    v1 = _publish(client, odef, extra=2)["rule_version"]["rule_version_id"]
    sid = _subject(client, "impact")
    first = _evaluate(client, sid, odef)
    assert first.status_code == 200
    monkeypatch.setitem(rules.LIMITS, "max_nodes", _measure(v1)["max_nodes"] - 1)  # v2 (7 nodes) is within it
    out = _publish(client, odef, valid_from="2099-01-01")
    impact = out["impact"]
    assert impact["targeted"] >= 1 and impact["failed"].get("EVALUATOR_UNAVAILABLE", 0) >= 1, impact
    assert impact["status"] == "INCOMPLETE"
    assert _evaluations(sid, odef) == [first.json()["evaluation_id"]]


def test_history_compare_reports_the_refusal_on_each_side(client, world, monkeypatch):
    from app.engines import rules
    from app.platform import current_watermark
    from app.db import AdminSession
    odef = _definition()
    rv_id = _publish(client, odef, nest=1)["rule_version"]["rule_version_id"]
    sid = _subject(client, "cmp")
    with AdminSession() as db:
        seq = current_watermark(db)["record_seq"]
    monkeypatch.setitem(rules.LIMITS, "max_depth", _measure(rv_id)["max_depth"] - 1)
    r = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": sid, "obligation_definition_id": odef, "valid_at": datetime.now(timezone.utc).isoformat(),
        "left_seq": seq, "right_seq": seq})
    assert r.status_code == 200, r.text
    body = r.json()
    for side in ("left", "right"):
        assert body[side]["error"]["error"] == "EVALUATOR_UNAVAILABLE" and "outcome" not in body[side]
    assert body["differences"] == []  # nothing is compared when neither side could run


def test_historical_replay_does_not_approximate_and_history_stays_readable(client, world, monkeypatch):
    from app.engines import rules
    odef = _definition()
    rv_id = _publish(client, odef, inline=4)["rule_version"]["rule_version_id"]
    sid = _subject(client, "hist")
    e1 = _evaluate(client, sid, odef).json()
    view = client.get(f"/api/applicability/evaluations/{e1['evaluation_id']}", headers=H(CCO)).json()
    assert view["replay"]["status"] == "REPRODUCED"
    assert {"check": "RUNTIME_WITHIN_LIMITS", "passed": True} in view["replay"]["checks"]
    monkeypatch.setitem(rules.LIMITS, "max_inline_set", 3)
    before = db_fingerprint()
    view = client.get(f"/api/applicability/evaluations/{e1['evaluation_id']}", headers=H(CCO))
    assert view.status_code == 200
    rep = view.json()["replay"]
    assert rep["status"] == "NOT_REPRODUCED" and rep["matches"] is False and rep["reproduced_outcome"] is None
    assert rep["error"] == "EVALUATOR_UNAVAILABLE" and rep["reason"] == "RUL_RESOURCE_LIMIT"
    assert rep["failed_checks"] == ["RUNTIME_WITHIN_LIMITS"]
    assert [(v["limit"], v["actual"]) for v in rep["violations"]] == [("max_inline_set", 4)]
    assert view.json()["outcome"] == e1["outcome"] == "APPLIES"  # the recorded result is still what was recorded
    again = _evaluate(client, sid, odef, record_cutoff_seq=e1["record_cutoff_seq"], effective_at=e1["effective_at"])
    assert again.status_code == 500 and again.json()["detail"]["error"] == "EVALUATOR_UNAVAILABLE"
    assert db_fingerprint() == before
    monkeypatch.undo()
    view = client.get(f"/api/applicability/evaluations/{e1['evaluation_id']}", headers=H(CCO)).json()
    assert view["replay"]["status"] == "REPRODUCED"


def test_rules_within_the_limits_behave_as_before(client, world):
    """Default limits: the seeded catalogue and a new rule evaluate, collapse idempotently and replay unchanged."""
    from app.db import AdminSession
    from app.engines import rules
    from app.models import RuleVersion
    with AdminSession() as db:
        published = db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars().all()
        assert published and all(rules.resource_limit_violations(r.canonical_ast) == [] for r in published)
    odef = _definition()
    _publish(client, odef, nest=2, extra=1, root_mappings=2, inline=3)
    sid = _subject(client, "normal")
    a = _evaluate(client, sid, odef)
    # Identical under ZA-APP-001 §06 (the same exact effective_at and record cutoff): collapses to one evaluation.
    b = _evaluate(client, sid, odef, effective_at=a.json()["effective_at"], record_cutoff_seq=a.json()["record_cutoff_seq"])
    assert a.status_code == b.status_code == 200 and a.json()["outcome"] == "APPLIES"
    assert a.json()["evaluation_id"] == b.json()["evaluation_id"]


def test_restoring_the_limit_restores_evaluation(client, world, monkeypatch):
    from app.engines import rules
    odef = _definition()
    rv_id = _publish(client, odef, extra=1)["rule_version"]["rule_version_id"]
    sid = _subject(client, "restore")
    monkeypatch.setitem(rules.LIMITS, "max_nodes", _measure(rv_id)["max_nodes"] - 1)
    assert _evaluate(client, sid, odef).status_code == 500
    monkeypatch.undo()
    r = _evaluate(client, sid, odef)
    assert r.status_code == 200 and r.json()["rule_version_id"] == rv_id and r.json()["outcome"] == "APPLIES"


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and advisory locks; exact reason required by the CI gate
    return True


def _engine_evaluate(sid, odef_id, start: threading.Barrier, at=None, cutoff=None):
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    with SessionLocal() as db:
        set_principal(db, TEN, CCO)
        start.wait(30)
        ev = appl.evaluate(db, tenant_id=TEN, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, odef_id),
                           effective_at=at or datetime.now(timezone.utc), record_cutoff_seq=cutoff,
                           trigger="EXPLICIT_REQUEST", actor_id=CCO)
        db.commit()
        return ev.evaluation_id


def test_concurrent_evaluations_refuse_the_over_limit_rule_and_collapse_the_other(client, world, pg, monkeypatch):
    """Six transactions start together on one subject: three on a definition whose rule is over the limit, three on one
    within it. The refused ones write nothing and hold no lock that blocks the others; the others still collapse to
    one evaluation (APP-REQ-016). No deadlock, no 500 other than the refusal."""
    from app.engines import rules
    big, small = _definition(), _definition()
    rv_big = _publish(client, big, extra=3)["rule_version"]["rule_version_id"]
    rv_small = _publish(client, small)["rule_version"]["rule_version_id"]
    sid = _subject(client, "race")
    monkeypatch.setitem(rules.LIMITS, "max_nodes", _measure(rv_small)["max_nodes"])
    assert _measure(rv_big)["max_nodes"] > rules.LIMITS["max_nodes"]
    start = threading.Barrier(6)
    out: dict = {}
    # One shared instant and record cutoff: the three "small" requests are then identical under ZA-APP-001 §06 and
    # collapse to one evaluation (APP-REQ-016).
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        cutoff = current_watermark(db)["record_seq"]
    at = datetime.now(timezone.utc)

    def run(name, odef):
        try:
            out[name] = ("ok", _engine_evaluate(sid, odef, start, at, cutoff))
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - reported by the assertions
            out[name] = ("error", repr(e))
    threads = [threading.Thread(target=run, args=(f"{k}{i}", odef)) for i in range(3) for k, odef in (("big", big), ("small", small))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(90)
    assert not any(t.is_alive() for t in threads), "an evaluation never finished (deadlock?)"
    for i in range(3):
        code, detail = out[f"big{i}"]
        assert code == 500 and detail["error"] == "EVALUATOR_UNAVAILABLE" and detail["rule_version_id"] == rv_big, out
    smalls = {out[f"small{i}"] for i in range(3)}
    assert len(smalls) == 1 and next(iter(smalls))[0] == "ok", out
    assert _evaluations(sid, big) == [] and _evaluations(sid, small) == [next(iter(smalls))[1]]
