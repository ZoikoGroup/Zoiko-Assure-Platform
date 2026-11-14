"""Decision tables written directly as AST: table identity, metadata integrity and semantic duplicate rows.

Controlling text (ZA-RUL-001): §18 "RUL_DECISION_DUPLICATE_ROW: Semantically duplicate row detected" and
RUL_SCHEMA_INVALID; §11 stage 1 (schema), stage 6 "Decision-table analysis: ... duplicate rows"; §07 (rows: "Stable
row_id"); §04 (the AST is authoritative; the decision-table editor is a projection of it); §03 typed literals; RUL-INV-11 /
RUL-REQ-025 (row identity in the AST and trace); §15 / RUL-REQ-020 (publication fails closed on validation errors).

The specification defines rows only in the decision-table authoring view. How a table is recognised in an AST, which
metadata is refused, and when two AST rows are the same are team-lead implementation decisions (rules._ast_table_issues):
  TL-1  a table is an ANY node with decision_table true; its rows are its children, ALL nodes with a string row_id (the
        compiler's own output shape), wherever the table sits;
  TL-2  any other use of that metadata is RUL_SCHEMA_INVALID;
  TL-3  two rows are duplicates when their conditions are the same in source order: same operators, facts and set
        references, literals equal as decision-table cells are (rules._cell_key). No reordering, no operator
        equivalence, no flattening of nested logic.
The §11 stage-5 lint "duplicate/redundant conditions" for ordinary AST logic is not implemented (no severity or code is
defined). Pure validation, identical on SQLite and PostgreSQL 17 (the module runs on both).
"""
from __future__ import annotations

import copy
import secrets

import pytest
from sqlalchemy import func, select

from conftest import H
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RAD-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
EU = "entity.established_in_eu"
TURN = f"rad.{TAG.lower()}.turnover"
S: dict = {}

FD = {"eu": {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"},
      "t": {"data_type": "DECIMAL", "enum_domain": None, "status": "RELEASED"},
      "at": {"data_type": "TIMESTAMP", "enum_domain": None, "status": "RELEASED"},
      "d": {"data_type": "DATE", "enum_domain": None, "status": "RELEASED"},
      "c": {"data_type": "SET<COUNTRY>", "enum_domain": None, "status": "RELEASED"},
      "n": {"data_type": "INTEGER", "enum_domain": None, "status": "RELEASED"}}
_ids = iter(range(10**9))


def _p(fact, op, typ, val):
    i = next(_ids)
    return {"node_type": "PREDICATE", "node_id": f"p{i}", "operator": op,
            "args": [{"node_type": "FACT_REF", "node_id": f"p{i}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"p{i}c", "type": typ, "value": val}]}


def _row(rid, *conds, **extra):
    return {"node_type": "ALL", "node_id": f"row_{rid}", "row_id": rid, "source_mappings": [f"sm_{rid}"],
            "children": list(conds), **extra}


def _table(*rows, **extra):
    return {"node_type": "ANY", "node_id": "dt", "decision_table": True, "source_mappings": ["sm_dt"],
            "children": list(rows), **extra}


def _eu():
    return _p("eu", "EQ", "BOOLEAN", True)


def _codes(ast) -> set:
    from app.engines import rules
    return {i["code"] for i in rules.validate(ast, FD, {})["issues"] if i["severity"] == "ERROR"}


# ------------------------------------------------------------------------------------------------ duplicate rows
DUPLICATES = {
    "identical": lambda: _table(_row("r1", _eu(), _p("t", "GTE", "DECIMAL", "1.5")),
                                _row("r2", _eu(), _p("t", "GTE", "DECIMAL", "1.5"))),
    "decimal_trailing_zero": lambda: _table(_row("r1", _eu(), _p("t", "GTE", "DECIMAL", "1.0")),
                                            _row("r2", _eu(), _p("t", "GTE", "DECIMAL", "1.00"))),
    "timestamp_same_instant_other_offset": lambda: _table(
        _row("r1", _p("at", "GTE", "TIMESTAMP", "2027-01-01T00:00:00Z")),
        _row("r2", _p("at", "GTE", "TIMESTAMP", "2027-01-01T01:00:00+01:00"))),
    "set_same_members_other_order": lambda: _table(_row("r1", _p("c", "INTERSECTS", "SET<COUNTRY>", ["DE", "FR"])),
                                                   _row("r2", _p("c", "INTERSECTS", "SET<COUNTRY>", ["FR", "DE"]))),
    "same_nested_logic": lambda: _table(*[_row(r, _eu(), {"node_type": "NOT", "node_id": f"not_{r}",
                                                          "child": _p("t", "LT", "DECIMAL", "0")}) for r in ("r1", "r2")]),
    "single_match_table": lambda: _table(_row("r1", _p("t", "GTE", "DECIMAL", "2")),
                                         _row("r2", _p("t", "GTE", "DECIMAL", "2.0")), single_match=True),
    "table_nested_in_ordinary_logic": lambda: {"node_type": "ALL", "node_id": "root", "source_mappings": ["sm_root"],
                                               "children": [_eu(), _table(_row("r1", _p("t", "GTE", "DECIMAL", "3")),
                                                                          _row("r2", _p("t", "GTE", "DECIMAL", "3.0")))]},
}


@pytest.mark.parametrize("case", list(DUPLICATES))
def test_duplicate_rows_of_an_ast_table_are_refused(case):
    """Every one of these validated: AST tables had no duplicate-row analysis at all, identical rows included."""
    assert "RUL_DECISION_DUPLICATE_ROW" in _codes(DUPLICATES[case]())


DISTINCT = {
    "different_decimal": (_row("r1", _p("t", "GTE", "DECIMAL", "1.0")), _row("r2", _p("t", "GTE", "DECIMAL", "1.01"))),
    "different_instant": (_row("r1", _p("at", "GTE", "TIMESTAMP", "2027-01-01T00:00:00Z")),
                          _row("r2", _p("at", "GTE", "TIMESTAMP", "2027-01-01T00:00:00+01:00"))),
    "different_set": (_row("r1", _p("c", "INTERSECTS", "SET<COUNTRY>", ["DE", "FR"])),
                      _row("r2", _p("c", "INTERSECTS", "SET<COUNTRY>", ["DE"]))),
    "operator_identity_kept": (_row("r1", _p("d", "DATE_ON_OR_AFTER", "DATE", "2027-01-01")),
                               _row("r2", _p("d", "GTE", "DATE", "2027-01-01"))),
    "condition_order_kept": (_row("r1", _eu(), _p("t", "GTE", "DECIMAL", "1")),
                             _row("r2", _p("t", "GTE", "DECIMAL", "1"), _eu())),
    "nested_logic_not_flattened": (_row("r1", _eu(), {"node_type": "ALL", "node_id": "inner", "children": [
                                       _p("t", "GTE", "DECIMAL", "1"), _p("t", "LT", "DECIMAL", "9")]}),
                                   _row("r2", _eu(), _p("t", "GTE", "DECIMAL", "1"), _p("t", "LT", "DECIMAL", "9"))),
}


@pytest.mark.parametrize("case", list(DISTINCT))
def test_rows_that_differ_or_need_an_undecided_equivalence_stay_distinct(case):
    """Guard (passes before and after): different values stay distinct, and no equivalence the specification does not
    define is assumed (operator labels, condition order, nested versus flat conjunction)."""
    assert _codes(_table(*copy.deepcopy(DISTINCT[case]))) == set()


@pytest.mark.parametrize("valid, invalid", [("1", "1E0"), ("2027-01-01T00:00:00Z", "2027-01-01T00:00:00")],
                         ids=["decimal-exponent", "timestamp-no-offset"])
def test_an_invalid_literal_is_a_type_error_never_a_duplicate_of_the_value_it_resembles(valid, invalid):
    """Guard: no coercion. The invalid spelling is refused as a literal and is not merged with the valid row."""
    typ, fact = ("DECIMAL", "t") if valid == "1" else ("TIMESTAMP", "at")
    codes = _codes(_table(_row("r1", _p(fact, "GTE", typ, valid)), _row("r2", _p(fact, "GTE", typ, invalid))))
    assert "RUL_TYPE_MISMATCH" in codes and "RUL_DECISION_DUPLICATE_ROW" not in codes, codes


def test_ordinary_logic_is_never_read_as_a_table():
    """Guard: an ANY over two identical ALL branches, without table metadata, is ordinary logic. It is not a decision
    table, so it is not refused as duplicate rows (redundant ordinary conditions are the undefined §11 stage-5 lint)."""
    branch = lambda: {"node_type": "ALL", "node_id": f"b{next(_ids)}", "children": [_eu(), _p("t", "GTE", "DECIMAL", "1")]}  # noqa: E731
    assert _codes({"node_type": "ANY", "node_id": "root", "source_mappings": ["sm_root"], "children": [branch(), branch()]}) == set()


# ------------------------------------------------------------------------------------------------ table metadata
FORGED = {
    "flag_on_all_node": lambda: {"node_type": "ALL", "node_id": "root", "decision_table": True, "source_mappings": ["s"],
                                 "children": [_eu(), _p("t", "GTE", "DECIMAL", "1")]},
    "flag_not_a_boolean": lambda: _table(_row("r1", _eu()), _row("r2", _p("t", "GTE", "DECIMAL", "1")), decision_table="yes"),
    "table_child_not_a_row": lambda: _table(_row("r1", _eu()), _p("t", "GTE", "DECIMAL", "1")),
    "row_outside_a_table": lambda: {"node_type": "ANY", "node_id": "root", "source_mappings": ["s"], "children": [
        _row("r1", _eu()), _row("r2", _p("t", "GTE", "DECIMAL", "1"))]},
    "row_id_not_a_string": lambda: _table(_row("r1", _eu()), {**_row("x", _p("t", "GTE", "DECIMAL", "1")), "row_id": 7}),
    "row_id_repeated": lambda: _table(_row("r1", _eu()), {**_row("r1", _p("t", "GTE", "DECIMAL", "1")), "node_id": "row_b"}),
}


@pytest.mark.parametrize("case", list(FORGED))
def test_table_metadata_that_does_not_describe_a_table_is_refused(case):
    """The compiler's table metadata was accepted anywhere and in any form: a flag on an ALL node or with a non-boolean
    value, a non-row child, rows outside a table, non-string or repeated row IDs (ambiguous row identity in traces)."""
    assert "RUL_SCHEMA_INVALID" in _codes(FORGED[case]())


def test_validation_changes_neither_the_ast_nor_its_canonical_hash():
    """Guard: the hash below was computed on the implementation before this change; validation only reads the AST."""
    from app.engines import rules
    ast = _table(_row("r1", {**_eu(), "node_id": "r1a"}, {**_p("t", "GTE", "DECIMAL", "1.0"), "node_id": "r1b"}),
                 _row("r2", {**_eu(), "node_id": "r2a"}, {**_p("at", "GTE", "TIMESTAMP", "2027-01-01T01:00:00+01:00"),
                                                          "node_id": "r2b"}))
    for row in ast["children"]:  # the same literal IDs as the fixture the reference hash was computed on
        for pred in row["children"]:
            pred["args"][0]["node_id"], pred["args"][1]["node_id"] = f"{pred['node_id']}f", f"{pred['node_id']}c"
    before = copy.deepcopy(ast)
    assert rules.validate(ast, FD, {})["ok"] and ast == before
    assert rules.ast_hash(rules.canonicalize(ast), {}) == "d3a1798ab9eb639a7a87d494683f1ffc981c9a9a6ec7979229be078087326278"


# ------------------------------------------------------------------------------------------------ the API lifecycle
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Raw table Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TURN, label="Annual turnover", data_type="DECIMAL", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Raw table Act",
        "locator": f"OJ L {TAG}/rad", "instrument_identifier": INSTR, "raw_text": "Large institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RAD-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RAD_{TAG}", title="Raw table obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def _draft(client) -> str:
    """AST mode: a decision table with the row "established in the EU and turnover >= 50 000 000" written twice."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def row(rid, threshold):
        return {"node_type": "ALL", "node_id": f"row_{t}_{rid}", "row_id": rid, "source_mappings": [f"sm_{t}_{rid}"],
                "children": [
                    {"node_type": "PREDICATE", "node_id": f"{t}_{rid}_eu", "operator": "EQ", "args": [
                        {"node_type": "FACT_REF", "node_id": f"{t}_{rid}_euf", "fact": EU},
                        {"node_type": "CONST", "node_id": f"{t}_{rid}_euc", "type": "BOOLEAN", "value": True}]},
                    {"node_type": "PREDICATE", "node_id": f"{t}_{rid}_t", "operator": "GTE", "args": [
                        {"node_type": "FACT_REF", "node_id": f"{t}_{rid}_tf", "fact": TURN},
                        {"node_type": "CONST", "node_id": f"{t}_{rid}_tc", "type": "DECIMAL", "value": threshold}]}]}
    ast = {"node_type": "ANY", "node_id": f"{t}_dt", "decision_table": True, "source_mappings": [f"sm_{t}_dt"],
           "children": [row("r1", "50000000.0"), row("r2", "50000000.00")]}
    maps = [{"source_mapping_id": ref, "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for ref in (f"sm_{t}_dt", f"sm_{t}_r1", f"sm_{t}_r2")]
    vec = lambda vid, cls, eu, out: {"vector_id": vid, "vector_class": cls, "description": vid,  # noqa: E731
                                     "effective_at": "2026-07-01", "facts": {EU: eu, TURN: "60000000"},
                                     "expected_outcome": out}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast,
        "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                           vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
        "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    return d.json()["draft_id"]


def _records() -> tuple:
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent, RecordSequence
    with AdminSession() as db:
        return (db.execute(select(func.count()).select_from(AuditEvent)).scalar_one(),
                db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one(),
                [r.current_seq for r in db.execute(select(RecordSequence)).scalars()])


def test_draft_validation_and_review_refuse_duplicate_ast_rows(client, world):
    """The AST-mode draft passed validation and review with every vector passing. Draft validation now reports the
    duplicate; review refuses it before any vector runs, with no audit, outbox or record-sequence write."""
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _draft(client)
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    assert not rep["ok"] and "RUL_DECISION_DUPLICATE_ROW" in {i["code"] for i in rep["issues"]}, rep
    before = _records()
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECISION_DUPLICATE_ROW", r.text
    assert _records() == before
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        assert (d.status, d.vector_report) == ("VALIDATION_FAILED", None)


def test_shadow_and_publication_refuse_an_ast_table_verified_before_the_check(client, world):
    """A draft that reached VERIFIED before AST tables were analysed (set below the API here) is refused by the shadow run
    and by publication (RUL §15 fail closed); nothing is written."""
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _draft(client)
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        d.status, d.peer_reviewer_id, d.legal_reviewer_id = "VERIFIED", PEER, LEGAL
        db.commit()
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECISION_DUPLICATE_ROW", r.text
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"rad-{TAG}", "changelog": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "Validation failure blocks publication", r.text
    assert db_fingerprint() == before


def test_a_released_compiled_table_re_authored_as_ast_is_not_misread(client, world):
    """Guard: the seeded DORA decision table's released canonical AST (the compiler's shape) is what an AST-mode "new
    draft from the current version" starts from; it validates with no table-metadata or duplicate-row error."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.engines import rules
    from app.models import ContentRelease, RuleVersion
    with AdminSession() as db:
        cr = db.execute(select(ContentRelease).where(ContentRelease.label == "CR-2026-DORA-001")).scalars().one()
        rv = db.get(RuleVersion, cr.manifest["rule_versions"][0]["rule_version_id"])
        assert rv.canonical_ast.get("decision_table") is True
        rep = rules.validate(rv.canonical_ast, appl.load_fact_defs(db), appl.load_sets(db))
    assert rep["ok"], rep["issues"]
