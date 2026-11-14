"""Decision-table duplicate rows are detected by meaning, not by spelling.

Controlling text: ZA-RUL-001 §18 "RUL_DECISION_DUPLICATE_ROW: Semantically duplicate row detected" (an ERROR: "Blocks
review/release"); §11 stage 6 "Decision-table analysis: ... duplicate rows"; §21 "Decision tables: ... duplicate rows
detected"; §03 types and literals (DECIMAL "Exact decimal arithmetic", literal "Canonical decimal string; exponent
notation prohibited"; TIMESTAMP "Offset-aware canonical instant"; SET<T> a set of T; INTEGER "No implicit decimal/string
coercion"; STRING "case/normalization policy explicit in FactDefinition" and Text "canonical Unicode normalization defined
by RuleSchemaVersion", neither of which is defined); §15 / RUL-REQ-020 (publication fails closed on validation errors).

Two cells are one condition when both are valid literals of the column type and the evaluator compares them equal
(rules._literal_ok, rules._coerce); everything else is compared as written, as before. The compiled AST and its hash are
unchanged. Pure validation: identical on SQLite and PostgreSQL 17 (the module runs on both).
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import func, select

from conftest import H
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"DTD-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
EU = "entity.established_in_eu"
TURN = f"dtd.{TAG.lower()}.turnover"  # a DECIMAL FactDefinition (ZA-APP-001 §04 data_type)
S: dict = {}


def _table(value_type: str, a, b, *, operator: str = "EQ", mode: str = "ANY_MATCH") -> dict:
    return {"table_mode": mode, "decision_table_id": "dt",
            "columns": [{"column_id": "c", "fact": "f", "value_type": value_type, "operator": operator}],
            "rows": [{"row_id": "r1", "cells": {"c": a}, "source_mappings": ["s"]},
                     {"row_id": "r2", "cells": {"c": b}, "source_mappings": ["s"]}]}


def _codes(table: dict) -> set:
    from app.engines import rules
    return {i["code"] for i in rules.compile_decision_table(table)[1]}


# ------------------------------------------------------------------------------------------------ the compiler
SAME = {  # two spellings of one value of the column type; both valid literals
    "decimal_trailing_zero": ("DECIMAL", "GTE", "50000000.0", "50000000.00"),
    "timestamp_same_instant_other_offset": ("TIMESTAMP", "GTE", "2027-01-01T00:00:00Z", "2027-01-01T01:00:00+01:00"),
    "set_same_members_other_order": ("SET<COUNTRY>", "INTERSECTS", ["DE", "FR"], ["FR", "DE"]),
}


@pytest.mark.parametrize("case", list(SAME))
def test_two_spellings_of_one_value_are_a_duplicate_row(case):
    """They were compared as text, so an ANY_MATCH table carrying both rows passed decision-table analysis."""
    value_type, op, a, b = SAME[case]
    assert "RUL_DECISION_DUPLICATE_ROW" in _codes(_table(value_type, a, b, operator=op))


@pytest.mark.parametrize("value_type, op, a, b", [
    ("DECIMAL", "GTE", "50000000.0", "50000000.01"),
    ("TIMESTAMP", "GTE", "2027-01-01T00:00:00Z", "2027-01-01T00:00:00+01:00"),  # one hour apart
    ("SET<COUNTRY>", "INTERSECTS", ["DE", "FR"], ["DE"]),
    ("STRING", "EQ", "Credit", "credit"),  # no case policy is defined: compared as written, as the evaluator does
    ("INTEGER", "GTE", 1, 2),
], ids=["decimal", "timestamp", "set", "string-case", "integer"])
def test_different_values_stay_distinct_rows(value_type, op, a, b):
    """Guard (passes before and after): genuinely different values, and text the specification gives no equality
    policy for, are never merged."""
    assert "RUL_DECISION_DUPLICATE_ROW" not in _codes(_table(value_type, a, b, operator=op))


@pytest.mark.parametrize("value_type, valid, invalid", [
    ("INTEGER", 1, "01"), ("INTEGER", 1, 1.0), ("INTEGER", 1, True),
    ("DECIMAL", "1", "1E0"), ("TIMESTAMP", "2027-01-01T00:00:00Z", "2027-01-01T00:00:00"),
], ids=["integer-text", "integer-float", "integer-bool", "decimal-exponent", "timestamp-no-offset"])
def test_an_invalid_literal_is_a_type_error_never_a_duplicate_of_an_equal_valid_value(value_type, valid, invalid):
    """Guard (passes before and after): no coercion. An invalid spelling is refused as a literal (RUL_TYPE_MISMATCH)
    and is not merged with the valid value it would coerce to."""
    from app.engines import rules
    ast, issues = rules.compile_decision_table(_table(value_type, valid, invalid, operator="GTE"))
    fd = {"f": {"data_type": value_type, "enum_domain": None, "status": "RELEASED"}}
    codes = {i["code"] for i in issues + rules.validate(ast, fd, {})["issues"]}
    assert "RUL_TYPE_MISMATCH" in codes and "RUL_DECISION_DUPLICATE_ROW" not in codes, codes


def test_compilation_and_its_hash_are_unchanged():
    """Guard: the compiled AST of a table with differently spelled cells is byte-for-byte what the compiler produced
    before this change (the canonical hash below was computed on the previous implementation); only analysis changed."""
    from app.engines import rules
    table = {"table_mode": "ANY_MATCH", "decision_table_id": "dt_guard", "source_mappings": ["sm_dt"],
             "columns": [{"column_id": "c_eu", "fact": "entity.established_in_eu", "value_type": "BOOLEAN"},
                         {"column_id": "c_t", "fact": "turnover", "value_type": "DECIMAL", "operator": "GTE"},
                         {"column_id": "c_at", "fact": "reported_at", "value_type": "TIMESTAMP", "operator": "GTE"}],
             "rows": [{"row_id": "r1", "cells": {"c_eu": True, "c_t": "1.0", "c_at": "ANY"}, "source_mappings": ["sm_r1"]},
                      {"row_id": "r2", "cells": {"c_eu": True, "c_t": "1.00", "c_at": "2027-01-01T01:00:00+01:00"},
                       "source_mappings": ["sm_r2"]},
                      {"row_id": "r3", "cells": {"c_eu": "ANY", "c_t": "2.5", "c_at": "2027-01-01T00:00:00Z"},
                       "source_mappings": ["sm_r3"]}]}
    ast, issues = rules.compile_decision_table(table)
    assert not issues
    assert rules.ast_hash(rules.canonicalize(ast), {}) == "04d1fcd9d25cd12b4a9087647d77f7a280de8a23470985a3dbd97243db57ebcd"


# ------------------------------------------------------------------------------------------------ the API gates
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Duplicate Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TURN, label="Annual turnover", data_type="DECIMAL", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Duplicate Act",
        "locator": f"OJ L {TAG}/dtd", "instrument_identifier": INSTR, "raw_text": "Large institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"DTD-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"DTD_{TAG}", title="Duplicate obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def _draft(client) -> str:
    """ANY_MATCH: established in the EU with turnover at least 50 000 000, written twice ("50000000.0" / ".00")."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]
    table = {"table_mode": "ANY_MATCH", "decision_table_id": f"dt_{t}", "source_mappings": [f"sm_{t}_dt"],
             "columns": [{"column_id": "c_eu", "fact": EU, "value_type": "BOOLEAN"},
                         {"column_id": "c_t", "fact": TURN, "value_type": "DECIMAL", "operator": "GTE"}],
             "rows": [{"row_id": rid, "cells": {"c_eu": True, "c_t": v}, "source_mappings": [f"sm_{t}_{rid}"]}
                      for rid, v in (("r1", "50000000.0"), ("r2", "50000000.00"))]}
    maps = [{"source_mapping_id": ref, "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for ref in (f"sm_{t}_dt", f"sm_{t}_r1", f"sm_{t}_r2")]
    vec = lambda vid, cls, eu, out: {"vector_id": vid, "vector_class": cls, "description": vid,  # noqa: E731
                                     "effective_at": "2026-07-01", "facts": {EU: eu, TURN: "60000000"},
                                     "expected_outcome": out}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "DECISION_TABLE", "decision_table": table,
        "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                           vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
        "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    return d.json()["draft_id"]


def _records() -> tuple:
    """The audit trail, the outbox and the record sequence: what a refusal must not write."""
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent, RecordSequence
    with AdminSession() as db:
        return (db.execute(select(func.count()).select_from(AuditEvent)).scalar_one(),
                db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one(),
                [r.current_seq for r in db.execute(select(RecordSequence)).scalars()])


def test_review_refuses_a_semantically_duplicate_row_before_any_vector_runs(client, world):
    """The draft passed review (every vector passes: the rows are one condition written twice). It is refused at
    static validation with RUL_DECISION_DUPLICATE_ROW, before golden execution, with no audit, outbox or sequence write."""
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _draft(client)
    before = _records()
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECISION_DUPLICATE_ROW", r.text
    assert _records() == before
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        assert (d.status, d.vector_report) == ("VALIDATION_FAILED", None)


def test_shadow_and_publication_refuse_a_draft_verified_before_the_check(client, world):
    """A draft that reached VERIFIED while duplicate rows were compared as text (below the API here) is refused by the
    shadow run and by publication (RUL §15 fail closed), and neither writes anything."""
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
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"dtd-{TAG}", "changelog": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "Validation failure blocks publication", r.text
    assert db_fingerprint() == before
