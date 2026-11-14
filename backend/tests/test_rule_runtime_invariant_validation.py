"""Rule runtime invariants: static validation and execution agree, and shadow/impact accounting stays exact.

Controlling text:
  * ZA-RUL-001 §03 type system (TIMESTAMP "Offset-aware canonical instant"; literal rule "Date/time: ISO 8601 canonical
    form; no ambiguous local date/time strings"), §04 node contracts (ALL/ANY/NOT/PREDICATE -> TRUTH3; FactRef, Constant,
    SetRef and ContextRef are values), §06 ALL/ANY/NOT truth tables, §07 SINGLE_MATCH_VALIDATED semantics ("More than one
    TRUE: RUNTIME_RULE_INVARIANT_BREACH ... Publication should have prevented it"; "TRUE + UNKNOWN rows: TRUE only if
    exclusivity proof guarantees UNKNOWN rows cannot also legally match; otherwise validation forbids this table design
    or runtime returns invariant breach"), §11 stages 1-6 (schema parse "unknown node/operator/field rejected", type
    checking, decision-table analysis), §15 publication gate ("no unexplained runtime invariant breach"; "Any SINGLE_MATCH
    runtime overlap in shadow testing blocks publication"), §18 taxonomy; RUL-INV-04 (any valid typed input yields TRUE,
    FALSE or UNKNOWN, never an uncaught semantic state), RUL-INV-10, RUL-REQ-013/020.
  * ZA-APP-001 §10 shadow step 4 ("execution errors" are their own bucket), §12 (errors are not applicability outcomes),
    §14 ("One malformed rule/content release cannot crash evaluator pool"), APP-REQ-004/019.

Each test fails on the implementation before this change (see the defect named in its docstring).
Runs on SQLite and PostgreSQL 17; no defect here depends on locking, so there is no concurrency test.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RRI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-rri-{TAG.lower()}"
CCO = f"rri-cco-{TAG.lower()}"
EU, TYPE, MICRO = "entity.established_in_eu", "entity.financial_entity_type", "entity.microenterprise"
TS = f"rri.{TAG.lower()}.reported_at"  # a TIMESTAMP FactDefinition (ZA-APP-001 §04 data_type)
S: dict = {}
UNK = {"unknown": "MISSING_REQUIRED_FACT"}
SINCE, REPORTED, NAIVE = "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z", "2026-06-01T00:00:00"


# ------------------------------------------------------------------------------------------------ rule builders
def _pred(t, n, fact, op, typ, value):
    return {"node_type": "PREDICATE", "node_id": f"{t}_{n}", "operator": op, "source_mappings": [f"sm_{t}_{n}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{t}_{n}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{t}_{n}c", "type": typ, "value": value}]}


def _node(nt, t, *children, **extra):
    return {"node_type": nt, "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": list(children),
            **extra}


def _vec(vid, cls, facts, expected):
    return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2026-07-01", "facts": facts,
            "expected_outcome": expected}


# Golden vectors on EU / entity type, and on EU / reported_at.
TYPE_VECTORS = [_vec("P", "POSITIVE", {EU: True, TYPE: "CREDIT_INSTITUTION"}, "APPLIES"),
                _vec("N", "NEGATIVE", {EU: False, TYPE: "CREDIT_INSTITUTION"}, "DOES_NOT_APPLY"),
                _vec("U", "UNKNOWN", {EU: UNK, TYPE: "CREDIT_INSTITUTION"}, "UNCERTAIN")]
TS_VECTORS = [_vec("P", "POSITIVE", {EU: True, TS: REPORTED}, "APPLIES"),
              _vec("N", "NEGATIVE", {EU: False, TS: REPORTED}, "DOES_NOT_APPLY"),
              _vec("U", "UNKNOWN", {EU: UNK, TS: REPORTED}, "UNCERTAIN")]


def _refs(x, out: list) -> list:
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "source_mappings" and isinstance(v, list):
                out += [r for r in v if isinstance(r, str) and r not in out]
            else:
                _refs(v, out)
    elif isinstance(x, list):
        for v in x:
            _refs(v, out)
    return out


def _body(*, ast=None, table=None, vectors=TYPE_VECTORS, change_class="EXECUTABLE_OUTCOME") -> dict:
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": ref, "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for ref in _refs(ast if table is None else table, [])]
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST" if table is None else "DECISION_TABLE",
            "ast": ast, "decision_table": table, "golden_vectors": vectors, "valid_from": "2024-01-01",
            "change_class": change_class, "source_mappings": maps}


def _ts_rule(t, op="GTE", value=SINCE, *, ts_first=False):  # APPLIES iff established in the EU and reported_at op value
    eu, ts = _pred(t, "eu", EU, "EQ", "BOOLEAN", True), _pred(t, "ts", TS, op, "TIMESTAMP", value)
    return _node("ALL", t, *((ts, eu) if ts_first else (eu, ts)))


def _tag() -> str:
    return f"r{secrets.token_hex(3)}"


# ------------------------------------------------------------------------------------------------ API helpers
def _draft(client, body: dict) -> str:
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert d.status_code == 200, d.text
    return d.json()["draft_id"]


def _reviewed(client, body: dict) -> str:
    did = _draft(client, body)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _shadow(client, did: str) -> dict:
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 200, r.text
    return r.json()


def _publish(client, did: str):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"rri-{secrets.token_hex(4)}", "changelog": "x"})


def _draft_row(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        db.expunge(d)
        return d


def _fact(client, sid, key, value, **extra):
    return client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        **extra})


def _subject(client, name: str, facts: dict) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    for key, value in facts.items():
        r = _fact(client, sid, key, value)
        assert r.status_code == 200, r.text
    return sid


def _legacy_subject(client, monkeypatch, name: str, eu: bool | None, reported_at: str = NAIVE) -> str:
    """A subject whose reported_at was stored before fact writes checked TIMESTAMP values: the branch as it was (any
    string, kept as text)."""
    from app.engines import facts
    real = facts.typed_columns

    def legacy(fd, value):
        if fd.data_type != "TIMESTAMP":
            return real(fd, value)
        return {**real(fd, SINCE), "value_text": value}
    monkeypatch.setattr(facts, "typed_columns", legacy)
    sid = _subject(client, name, {**({EU: eu} if eu is not None else {}), TS: reported_at})
    monkeypatch.undo()
    return sid


def _evaluate(client, sid):
    return client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["odef"]})


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Invariant Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TS, label="Reporting instant", data_type="TIMESTAMP", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RRI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Invariant Act",
        "locator": f"OJ L {TAG}/rri", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RRI-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "continuous", "normalized_meaning": "Report.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RRI_{TAG}", title="Invariant obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    base = _reviewed(client, _body(ast=_ts_rule(_tag()), vectors=TS_VECTORS))  # in force: EU and reported since 2026
    _shadow(client, base)
    r = _publish(client, base)
    assert r.status_code == 200, r.text
    S["base_draft"] = base
    return True


# ------------------------------------------------------------------------------------------------ static validation
def _mutated(mutate):
    def build():
        t = _tag()
        ast = _node("ALL", t, _pred(t, "eu", EU, "EQ", "BOOLEAN", True),
                    _pred(t, "type", TYPE, "EQ", "ENUM", "CREDIT_INSTITUTION"))
        mutate(ast, t)
        return _body(ast=ast)
    return build


def _value_root():
    t = _tag()
    return _body(ast={"node_type": "FACT_REF", "node_id": f"{t}_root", "fact": EU, "source_mappings": [f"sm_{t}_root"]})


def _set(key, value):
    def mutate(ast, t):
        node = ast
        for k in key[:-1]:
            node = node[k]
        node[key[-1]] = value
    return mutate


def _naive_timestamp_literal():
    return _body(ast=_ts_rule(_tag(), value="2026-01-01T00:00:00"), vectors=TS_VECTORS)


def _table(cols, rows, mode="ANY_MATCH", vectors=TYPE_VECTORS):
    t = _tag()
    return _body(table={"decision_table_id": f"{t}_dt", "table_mode": mode, "columns": cols, "source_mappings": [f"sm_{t}_dt"],
                        "rows": [{"row_id": rid, "cells": cells, "source_mappings": [f"sm_{t}_{rid}"]} for rid, cells in rows]},
                 vectors=vectors)


EU_COL = {"column_id": "c_eu", "fact": EU, "value_type": "BOOLEAN"}
STRUCTURES = {
    # defect 1: a value as the rule root validated, then every evaluation raised RUL_UNKNOWN_NODE
    "root_is_a_value": (_value_root, "RUL_TYPE_MISMATCH"),
    # defect 2: single_match outside an ANY node, or not a boolean, validated
    "single_match_on_all": (_mutated(_set(["single_match"], True)), "RUL_SCHEMA_INVALID"),
    "single_match_not_a_boolean": (_mutated(lambda a, t: a.update(node_type="ANY", single_match="false")),
                                   "RUL_SCHEMA_INVALID"),
    # defect 7: a field of the wrong JSON type made validation itself raise
    "node_id_not_a_string": (_mutated(_set(["children", 0, "node_id"], ["n1"])), "RUL_SCHEMA_INVALID"),
    "node_type_not_a_string": (_mutated(_set(["children", 0, "node_type"], ["PREDICATE"])), "RUL_UNKNOWN_NODE"),
    "operator_not_a_string": (_mutated(_set(["children", 0, "operator"], ["EQ"])), "RUL_UNKNOWN_OPERATOR"),
    "literal_type_not_a_string": (_mutated(_set(["children", 0, "args", 1, "type"], 5)), "RUL_SCHEMA_INVALID"),
    "fact_not_a_string": (_mutated(_set(["children", 0, "args", 0, "fact"], [EU])), "RUL_FACT_REF_INVALID"),
    "set_ref_not_a_string": (_mutated(_set(["children", 1, "args", 1],
                                           {"node_type": "SET_REF", "node_id": "sref", "set_version_id": ["s"]})),
                             "RUL_SET_REF_INVALID"),
    # defect 6: a TIMESTAMP literal without a UTC offset validated, then compared with instants (TypeError)
    "timestamp_literal_without_offset": (_naive_timestamp_literal, "RUL_TYPE_MISMATCH"),
    # defect 5: an authored condition the compiler silently left out of the AST
    "table_cell_for_undeclared_column": (lambda: _table([EU_COL], [("r1", {"c_eu": True, "c_tpye": "CREDIT_INSTITUTION"})]),
                                         "RUL_SCHEMA_INVALID"),
    "table_duplicate_column_id": (lambda: _table([{"column_id": "c", "fact": EU, "value_type": "BOOLEAN"},
                                                  {"column_id": "c", "fact": TYPE, "value_type": "ENUM"}],
                                                 [("r1", {"c": "CREDIT_INSTITUTION"})]), "RUL_SCHEMA_INVALID"),
    # defect 4: SINGLE_MATCH rows "proved" exclusive by different spellings of one instant
    "single_match_rows_equal_by_value": (lambda: _table(
        [{"column_id": "c_ts", "fact": TS, "value_type": "TIMESTAMP"}],
        [("r1", {"c_ts": REPORTED}), ("r2", {"c_ts": "2026-06-01T01:00:00+01:00"})],
        mode="SINGLE_MATCH_VALIDATED", vectors=TS_VECTORS), "RUL_DECISION_OVERLAP"),
}


@pytest.mark.parametrize("case", list(STRUCTURES))
def test_an_unsupported_structure_is_refused_at_static_validation_before_any_vector_runs(client, world, case):
    """RUL §11: validation is "a safety boundary": stages 1-6 refuse the draft with its §18 code, so it never enters
    review, never reaches golden execution (stage 9), and validation itself never fails with an exception."""
    build, code = STRUCTURES[case]
    did = _draft(client, build())
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    issues = r.json()["detail"]["validation"]["issues"]
    assert code in [i["code"] for i in issues if i["severity"] == "ERROR"], issues
    d = _draft_row(did)
    assert d.status == "VALIDATION_FAILED" and d.vector_report is None


# ------------------------------------------------------------------------------------------------ evaluator semantics
FD = {EU: {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"},
      TYPE: {"data_type": "ENUM", "enum_domain": ["CREDIT_INSTITUTION", "PAYMENT_INSTITUTION"], "status": "RELEASED"},
      MICRO: {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"},
      TS: {"data_type": "TIMESTAMP", "enum_domain": None, "status": "RELEASED"},
      "turnover": {"data_type": "DECIMAL", "enum_domain": None, "status": "RELEASED"}}
FT = {k: v["data_type"] for k, v in FD.items()}
CTX = {"effective_at": "2026-07-01"}


def _known(v):
    return {"known": True, "value": v, "revision_id": "rev"}


def test_single_match_on_an_all_node_never_evaluates_to_an_outcome():
    """Defect 2 (runtime half): ALL with single_match applied single-match semantics, so ALL(TRUE, FALSE) returned
    APPLIES against the §06 ALL truth table. A released rule carrying it (validated before validation refused it) is
    refused as a content error, never evaluated to an outcome."""
    from app.engines import rules
    t = "a"
    ast = _node("ALL", t, _pred(t, "eu", EU, "EQ", "BOOLEAN", True), _pred(t, "type", TYPE, "EQ", "ENUM",
                                                                           "CREDIT_INSTITUTION"), single_match=True)
    with pytest.raises(rules.RuleError) as e:
        rules.evaluate(ast, {EU: _known(True), TYPE: _known("PAYMENT_INSTITUTION")}, FT, {}, CTX)
    assert e.value.code == "RUL_SCHEMA_INVALID" and e.value.node_id == "a_root"


def test_single_match_with_a_matching_row_and_an_unknown_row_is_an_invariant_breach():
    """Defect 3: RUL §07 "TRUE + UNKNOWN rows: TRUE only if exclusivity proof guarantees UNKNOWN rows cannot also legally
    match; otherwise ... runtime returns invariant breach". It returned TRUE (APPLIES). Under the compiler's exclusivity
    proof the state cannot arise: a proven table with an unknown fact in the other row still evaluates."""
    from app.engines import rules
    t = "s"
    ast = _node("ANY", t, _pred(t, "eu", EU, "EQ", "BOOLEAN", True),
                _pred(t, "type", TYPE, "EQ", "ENUM", "CREDIT_INSTITUTION"), single_match=True)
    with pytest.raises(rules.RuleError) as e:
        rules.evaluate(ast, {EU: {"known": False, "reason": "MISSING_REQUIRED_FACT"}, TYPE: _known("CREDIT_INSTITUTION")},
                       FT, {}, CTX)
    assert e.value.code == "RUNTIME_RULE_INVARIANT_BREACH" and e.value.node_id == "s_root"
    proven, issues = rules.compile_decision_table({
        "table_mode": "SINGLE_MATCH_VALIDATED", "decision_table_id": "dt",
        "columns": [{"column_id": "c_type", "fact": TYPE, "value_type": "ENUM"},
                    {"column_id": "c_eu", "fact": EU, "value_type": "BOOLEAN"},
                    {"column_id": "c_micro", "fact": MICRO, "value_type": "BOOLEAN"}],
        "rows": [{"row_id": "r1", "cells": {"c_type": "CREDIT_INSTITUTION", "c_eu": True, "c_micro": "ANY"},
                  "source_mappings": ["s"]},
                 {"row_id": "r2", "cells": {"c_type": "PAYMENT_INSTITUTION", "c_eu": "ANY", "c_micro": True},
                  "source_mappings": ["s"]}]})
    assert not [i for i in issues if i["severity"] == "ERROR"], issues
    out = rules.evaluate(proven, {TYPE: _known("CREDIT_INSTITUTION"), EU: _known(True),
                                  MICRO: {"known": False, "reason": "MISSING_REQUIRED_FACT"}}, FT, {}, CTX)
    assert out["outcome"] == "APPLIES"


@pytest.mark.parametrize("value_type, fact, same, other", [
    ("DECIMAL", "turnover", ("1.0", "1.00"), "1.5"),
    ("TIMESTAMP", TS, (REPORTED, "2026-06-01T01:00:00+01:00"), "2026-06-01T00:00:01Z"),
])
def test_single_match_exclusivity_is_proved_on_values_not_cell_text(value_type, fact, same, other):
    """Defect 4: the static proof compared raw cell text, so two spellings of one value "proved" two rows exclusive and
    the published table breached at runtime. Distinct values still prove exclusivity."""
    from app.engines import rules

    def overlaps(a, b):
        _, issues = rules.compile_decision_table({
            "table_mode": "SINGLE_MATCH_VALIDATED", "columns": [{"column_id": "c", "fact": fact, "value_type": value_type}],
            "rows": [{"row_id": "r1", "cells": {"c": a}, "source_mappings": ["s"]},
                     {"row_id": "r2", "cells": {"c": b}, "source_mappings": ["s"]}]})
        return "RUL_DECISION_OVERLAP" in {i["code"] for i in issues}
    assert overlaps(*same) and not overlaps(same[0], other)


@pytest.mark.parametrize("op, value", [("EQ", NAIVE), ("GTE", "yesterday")], ids=["local-time", "malformed"])
def test_the_evaluator_refuses_a_timestamp_it_cannot_compare(op, value):
    """Defect 6 (runtime half): a TIMESTAMP fact without an offset compared EQ to an instant was silently FALSE (a false
    DOES_NOT_APPLY), and malformed text raised ValueError out of the evaluator. Both are RUL_TYPE_MISMATCH now, with the
    node that read the value, as a non-finite DECIMAL already is."""
    from app.engines import rules
    ast = _pred("t", "ts", TS, op, "TIMESTAMP", REPORTED)
    with pytest.raises(rules.RuleError) as e:
        rules.evaluate(ast, {TS: _known(value)}, FT, {}, CTX)
    assert e.value.code == "RUL_TYPE_MISMATCH" and e.value.node_id == "t_tsf"


# ------------------------------------------------------------------------------------------------ TIMESTAMP inputs
@pytest.mark.parametrize("value", [NAIVE, "2026-06-01"], ids=["local-time", "date-only"])
def test_the_sandbox_refuses_a_timestamp_fixture_without_an_offset(client, world, value):
    """Defect 6: the fixture passed the typed-input check (RUL §13 "Typed inputs") and the evaluator then raised
    TypeError comparing it with the rule's instant (an unhandled 500). Now refused before evaluation, nothing written."""
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{S['base_draft']}/sandbox", headers=H(AUTHOR),
                    json={"facts": {EU: True, TS: value}, "effective_at": "2026-07-01"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RUL_TYPE_MISMATCH", r.text
    assert db_fingerprint() == before


@pytest.mark.parametrize("value", ["yesterday", NAIVE, "2026-06-01"], ids=["malformed", "local-time", "date-only"])
def test_a_timestamp_fact_must_be_an_offset_aware_instant(client, world, value):
    """Defect 6 (write half): TIMESTAMP facts fell through to the free-text branch and any string was stored. Refused
    with 422 before the record sequence is taken; an instant is accepted."""
    sid = _subject(client, f"write-{secrets.token_hex(2)}", {EU: True})
    before = db_fingerprint()
    r = _fact(client, sid, TS, value)
    assert r.status_code == 422, r.text
    assert db_fingerprint() == before
    assert _fact(client, sid, TS, REPORTED).status_code == 200


def test_a_stored_timestamp_without_an_offset_is_refused_never_compared(client, world, monkeypatch):
    """Defect 6: evaluation of a value stored before the write check raised TypeError (an unhandled 500). It is an
    APP §12 error now: RUL_TYPE_MISMATCH, no evaluation, no determination, no event, no record sequence."""
    sid = _legacy_subject(client, monkeypatch, "stored", eu=True)
    before = db_fingerprint()
    r = _evaluate(client, sid)
    assert r.status_code == 500 and r.json()["detail"]["error"] == "RUL_TYPE_MISMATCH", r.text
    assert db_fingerprint() == before


def test_a_fact_write_commits_when_a_dependent_cannot_be_re_evaluated(client, world, monkeypatch):
    """APP §14 failure isolation: a fact write re-evaluates dependents (APP §10). One whose stored TIMESTAMP cannot be
    compared made the evaluator raise TypeError out of the whole fact write; the write now commits and reports the
    dependent with its error, as it does for every other evaluation error."""
    sid = _legacy_subject(client, monkeypatch, "dependent", eu=False)
    first = _evaluate(client, sid)
    assert first.status_code == 200 and first.json()["outcome"] == "DOES_NOT_APPLY", first.text  # ALL decided by EU
    r = _fact(client, sid, EU, True, change_kind="CORRECTION", reason="relocated")
    assert r.status_code == 200, r.text
    [failed] = [x for x in r.json()["impact_reevaluations"] if "error" in x]
    assert failed["error"]["error"] == "RUL_TYPE_MISMATCH", failed


# ------------------------------------------------------------------------------------------------ shadow and publication
def test_shadow_counts_an_uncomparable_timestamp_as_an_execution_error_and_publication_is_refused(client, world,
                                                                                                    monkeypatch):
    """APP §10 step 4 / RUL §15: the candidate compared a local time with an instant and got FALSE, so the shadow
    counted a DOES_NOT_APPLY transition (a successful evaluation) and publication went ahead. It is an execution error
    now: counted, reconciled, and the release is refused with nothing written."""
    did = _reviewed(client, _body(ast=_ts_rule(_tag(), "EQ", REPORTED), vectors=TS_VECTORS))
    before = _shadow(client, did)
    _legacy_subject(client, monkeypatch, "shadow", eu=True)
    after = _shadow(client, did)
    assert after["execution_errors"] - before["execution_errors"] == 1
    assert after["transition_buckets"] == before["transition_buckets"]
    for rep in (before, after):
        assert sum(rep["transition_buckets"].values()) + rep["execution_errors"] == rep["subjects_evaluated"]
    fp = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SHADOW_IMPACT_INCOMPLETE", r.text
    assert db_fingerprint() == fp


def test_shadow_counts_a_single_match_with_an_unknown_row_as_an_execution_error(client, world):
    """Defect 3 in shadow: a subject meeting one branch of a SINGLE_MATCH node with the other branch UNKNOWN was counted
    as an APPLIES transition and the release went ahead. It is a runtime invariant breach (RUL §07), an execution error
    that blocks publication (RUL §15)."""
    t = _tag()
    single = _node("ANY", t, _pred(t, "eu", EU, "EQ", "BOOLEAN", True),
                   _pred(t, "type", TYPE, "EQ", "ENUM", "CREDIT_INSTITUTION"), single_match=True)
    vectors = [_vec("P", "POSITIVE", {EU: True, TYPE: "PAYMENT_INSTITUTION"}, "APPLIES"),
               _vec("N", "NEGATIVE", {EU: False, TYPE: "PAYMENT_INSTITUTION"}, "DOES_NOT_APPLY"),
               _vec("U", "UNKNOWN", {EU: UNK, TYPE: "PAYMENT_INSTITUTION"}, "UNCERTAIN")]
    did = _reviewed(client, _body(ast=single, vectors=vectors))
    before = _shadow(client, did)
    _subject(client, "single", {TYPE: "CREDIT_INSTITUTION"})  # established_in_eu unknown
    after = _shadow(client, did)
    assert after["execution_errors"] - before["execution_errors"] == 1
    assert after["transition_buckets"] == before["transition_buckets"]
    fp = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SHADOW_IMPACT_INCOMPLETE", r.text
    assert db_fingerprint() == fp


def test_publication_impact_counts_an_uncomparable_timestamp_as_failed(client, world, monkeypatch):
    """Runs last: it changes the rule in force. A release without a mandatory shadow (EXECUTABLE_NON_OUTCOME, RUL §14)
    re-evaluates every evaluated subject (APP §10). The evaluator's TypeError aborted the whole publication; the target
    is now counted as failed with its code, the impact is INCOMPLETE (never "no impact", RUL §15) and the counts add up."""
    sid = _legacy_subject(client, monkeypatch, "impact", eu=False)
    assert _evaluate(client, sid).json()["outcome"] == "DOES_NOT_APPLY"
    did = _reviewed(client, _body(ast=_ts_rule(_tag(), ts_first=True), vectors=TS_VECTORS,
                                  change_class="EXECUTABLE_NON_OUTCOME"))  # the same conditions, reordered
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    i = r.json()["impact"]
    assert i["status"] == "INCOMPLETE" and i["failed"].get("RUL_TYPE_MISMATCH", 0) >= 1, i
    assert i["targeted"] == i["evaluated"] + sum(i["failed"].values())
