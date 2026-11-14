"""Inline SET<T> literal static validation.

Controlling text: ZA-RUL-001 §04 type system ("SET<T> {DE, FR, IT}: Elements same type T"; ENUM<T> "Values must come
from versioned domain T"; literal rules "Decimal: Canonical decimal string; exponent notation prohibited", "Date/time:
ISO 8601 canonical form", "Set: Small controlled inline sets only under compiler limit", "Null: Not permitted as a
literal"), type compatibility ("IN / NOT_IN: T against SET<T>"), §11 stages 1-4 (schema, reference resolution, type
checking and semantic validation before any binding runtime), §15 / RUL-REQ-020 (publication fails closed on a
validation or type error), §18 RUL_TYPE_MISMATCH / RUL_SCHEMA_INVALID.

Every member of an inline set is checked against the declared element type, exactly as a scalar literal of that type is
(rules._literal_ok), a null member is a null literal, and against an ENUM fact every member must belong to the fact's
domain (as EQ already checks). The specification defines no rule for an empty set or repeated members: both keep their
existing behaviour (a set: an empty set has no member, a repeated member counts once) and canonicalization is unchanged.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

# ------------------------------------------------------------------------------------------------ the rules engine
ELEMENT_TYPES = {  # element type -> (fact definition, valid members, a member value, a non-member value)
    "BOOLEAN": ({"data_type": "BOOLEAN"}, [True], True, False),
    "STRING": ({"data_type": "STRING"}, ["alpha", "beta"], "beta", "gamma"),
    "INTEGER": ({"data_type": "INTEGER"}, [250, 500], 250, 249),
    "DECIMAL": ({"data_type": "DECIMAL"}, ["1.50", "2", "-0.0000000001"], "1.5", "1.51"),
    "DATE": ({"data_type": "DATE"}, ["2025-01-01", "2026-06-30"], "2026-06-30", "2026-07-01"),
    "TIMESTAMP": ({"data_type": "TIMESTAMP"}, ["2025-01-01T00:00:00Z"], "2025-01-01T00:00:00+00:00", "2025-01-01T00:00:01Z"),
    "COUNTRY": ({"data_type": "COUNTRY"}, ["DE", "FR", "IT"], "FR", "GB"),
    "JURISDICTION": ({"data_type": "JURISDICTION"}, ["EU", "GB"], "EU", "US"),
    "ENUM": ({"data_type": "ENUM", "enum_domain": ["A", "B", "C"]}, ["A", "B"], "B", "C"),
    "ENUM<Role@1>": ({"data_type": "ENUM", "enum_domain": ["A", "B", "C"]}, ["A"], "A", "C"),
}


def _defs(et):
    fd, *_ = ELEMENT_TYPES[et]
    return {"x": {**fd, "status": "RELEASED"}, "n": {"data_type": "INTEGER", "status": "RELEASED"}}


def _ast(set_type, members, op="IN", fact="x"):
    const = {"node_type": "CONST", "node_id": "c", "type": set_type, "value": members}
    ref = {"node_type": "FACT_REF", "node_id": "f", "fact": fact}
    args = [const, {"node_type": "CONST", "node_id": "k", "type": "INTEGER", "value": 1}] if op == "SET_SIZE_GTE" \
        else [ref, const]
    return {"node_type": "PREDICATE", "node_id": "p", "operator": op, "source_mappings": ["s"], "args": args}


def _errors(ast, defs) -> list:
    from app.engines import rules
    rep = rules.validate(ast, defs, {})
    return [i["code"] for i in rep["issues"] if i["severity"] == "ERROR"]


def _run(ast, defs, value):
    from app.engines import rules
    facts = {"x": {"known": True, "value": value, "revision_id": "r"}}
    return rules.evaluate(ast, facts, {k: v["data_type"] for k, v in defs.items()}, {}, {"effective_at": "2025-06-01"})["outcome"]


@pytest.mark.parametrize("et", sorted(ELEMENT_TYPES))
def test_valid_members_of_every_element_type_validate_and_evaluate_as_before(et):
    from app.engines import rules
    _, members, inside, outside = ELEMENT_TYPES[et]
    defs, ast = _defs(et), _ast(f"SET<{et}>", members)
    assert _errors(ast, defs) == []
    assert _run(ast, defs, inside) == "APPLIES" and _run(ast, defs, outside) == "DOES_NOT_APPLY"
    assert rules.canonicalize(ast)["args"][1]["value"] == members  # canonical form (and so the AST hash) unchanged


INVALID = {  # case -> (element type, members, the §18 code)
    "string_and_integer_mixed": ("STRING", ["alpha", 7], "RUL_TYPE_MISMATCH"),
    "integer_set_with_a_boolean": ("INTEGER", [250, True], "RUL_TYPE_MISMATCH"),
    "boolean_set_with_an_integer": ("BOOLEAN", [True, 1], "RUL_TYPE_MISMATCH"),
    "integer_set_with_a_numeric_string": ("INTEGER", [250, "500"], "RUL_TYPE_MISMATCH"),
    "integer_set_with_a_float": ("INTEGER", [250, 500.0], "RUL_TYPE_MISMATCH"),
    "decimal_nan": ("DECIMAL", ["1.50", "NaN"], "RUL_TYPE_MISMATCH"),
    "decimal_snan": ("DECIMAL", ["sNaN"], "RUL_TYPE_MISMATCH"),
    "decimal_infinity": ("DECIMAL", ["Infinity"], "RUL_TYPE_MISMATCH"),
    "decimal_negative_infinity": ("DECIMAL", ["-Infinity"], "RUL_TYPE_MISMATCH"),
    "decimal_as_a_json_number": ("DECIMAL", [1.5], "RUL_TYPE_MISMATCH"),
    "decimal_in_exponent_notation": ("DECIMAL", ["1E+3"], "RUL_TYPE_MISMATCH"),
    "date_not_a_calendar_date": ("DATE", ["2025-13-01"], "RUL_TYPE_MISMATCH"),
    "timestamp_not_iso": ("TIMESTAMP", ["yesterday"], "RUL_TYPE_MISMATCH"),
    "nested_list_member": ("STRING", [["alpha"]], "RUL_TYPE_MISMATCH"),
    "object_member": ("STRING", [{"value": "alpha"}], "RUL_TYPE_MISMATCH"),
    "null_member": ("STRING", [None, "alpha"], "RUL_SCHEMA_INVALID"),
    "enum_member_outside_the_fact_domain": ("ENUM", ["A", "Z"], "RUL_TYPE_MISMATCH"),
    "country_member_not_a_string": ("COUNTRY", ["DE", 49], "RUL_TYPE_MISMATCH"),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_an_invalid_member_is_refused_with_its_rul_code(case):
    et, members, code = INVALID[case]
    assert code in _errors(_ast(f"SET<{et}>", members), _defs(et))


@pytest.mark.parametrize("set_type", ["SET<FOO>", "SET<SET<STRING>>", "SET<STRING", "SET<>"])
def test_an_unknown_or_malformed_element_type_is_refused(set_type):
    """Also where no operand type check would catch it (a constant SET_SIZE_GTE operand)."""
    assert "RUL_TYPE_MISMATCH" in _errors(_ast(set_type, ["alpha"], op="SET_SIZE_GTE"), _defs("STRING"))


@pytest.mark.parametrize("op", ["NOT_IN", "INTERSECTS", "CONTAINS_ALL"])
def test_enum_domain_membership_is_checked_for_every_set_operator(op):
    defs = {"x": {"data_type": "SET<ENUM>" if op != "NOT_IN" else "ENUM", "enum_domain": ["A", "B"], "status": "RELEASED"}}
    assert _errors(_ast("SET<ENUM>", ["A", "B"], op=op), defs) == []
    assert "RUL_TYPE_MISMATCH" in _errors(_ast("SET<ENUM>", ["A", "Q"], op=op), defs)


def test_an_empty_set_and_repeated_members_keep_set_semantics():
    """No rule in the specification refuses either: an empty set has no member (IN is FALSE, CONTAINS_ALL of nothing is
    TRUE), a repeated member counts once, and the canonical form is the authored one (no rewriting, same hash)."""
    from app.engines import rules
    defs = _defs("COUNTRY")
    assert _errors(_ast("SET<COUNTRY>", []), defs) == [] and _run(_ast("SET<COUNTRY>", []), defs, "DE") == "DOES_NOT_APPLY"
    sdefs = {"x": {"data_type": "SET<COUNTRY>", "status": "RELEASED"}}
    facts = {"x": {"known": True, "value": ["DE"], "revision_id": "r"}}
    out = rules.evaluate(_ast("SET<COUNTRY>", [], op="CONTAINS_ALL"), facts, {"x": "SET<COUNTRY>"}, {}, {"effective_at": "2025-06-01"})
    assert _errors(_ast("SET<COUNTRY>", [], op="CONTAINS_ALL"), sdefs) == [] and out["outcome"] == "APPLIES"
    dup = _ast("SET<COUNTRY>", ["DE", "DE", "FR"])
    assert _errors(dup, defs) == [] and _run(dup, defs, "DE") == "APPLIES"
    assert rules.canonicalize(dup)["args"][1]["value"] == ["DE", "DE", "FR"]
    assert rules.ast_hash(dup, {}) != rules.ast_hash(_ast("SET<COUNTRY>", ["DE", "FR"]), {})


def test_the_inline_set_size_limit_still_applies():
    defs = _defs("INTEGER")
    assert _errors(_ast("SET<INTEGER>", list(range(100))), defs) == []
    assert "RUL_RESOURCE_LIMIT" in _errors(_ast("SET<INTEGER>", list(range(101))), defs)


# ------------------------------------------------------------------------------------------------ through the API
TAG = secrets.token_hex(3).upper()
INSTR = f"ISL-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-isl-{TAG.lower()}", f"isl-cco-{TAG.lower()}"
EU, TYPE = "entity.established_in_eu", "entity.financial_entity_type"
S: dict = {}
UNK = {"unknown": "MISSING_REQUIRED_FACT"}
API_INVALID = {
    "enum_member_outside_the_domain": ["CREDIT_INSTITUTION", "BANK"],
    "nested_member": [["CREDIT_INSTITUTION"]],
    "null_member": [None, "CREDIT_INSTITUTION"],
    "mixed_member_types": ["CREDIT_INSTITUTION", 7],
}


def _body(members) -> dict:
    t, p = f"i{secrets.token_hex(3)}", S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    pred = lambda nid, fact, op, typ, value: {  # noqa: E731
        "node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": [f"sm_{nid}"],
        "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                 {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        pred(f"{t}_1", EU, "EQ", "BOOLEAN", True), pred(f"{t}_2", TYPE, "IN", "SET<ENUM>", members)]}
    vec = lambda vid, cls, typ, out: {"vector_id": vid, "vector_class": cls, "description": vid,  # noqa: E731
                                      "effective_at": "2025-06-01", "facts": {EU: True, TYPE: typ}, "expected_outcome": out}
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast, "valid_from": "2024-01-01",
            "golden_vectors": [vec("P", "POSITIVE", "PAYMENT_INSTITUTION", "APPLIES"),
                               vec("N", "NEGATIVE", "INVESTMENT_FIRM", "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", UNK, "UNCERTAIN")],
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps}


VALID = ["CREDIT_INSTITUTION", "PAYMENT_INSTITUTION"]


def _create(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _verified(client, members=VALID) -> str:
    did = _create(client, _body(members))
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"isl-{secrets.token_hex(4)}", "changelog": "x"})


def _ledger() -> tuple:
    from app.db import AdminSession
    from app.models import AuditEvent, ContentRelease, OutboxEvent, RuleVersion
    from app.platform import current_watermark
    with AdminSession() as db:
        n = lambda m: db.execute(select(func.count()).select_from(m)).scalar()  # noqa: E731
        return current_watermark(db)["record_seq"], n(AuditEvent), n(OutboxEvent), n(RuleVersion), n(ContentRelease)


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Set Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="ISL CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Set Act",
        "locator": f"OJ L {TAG}/isl", "instrument_identifier": INSTR, "raw_text": "Listed institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"ISL-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"ISL_{TAG}", title="Set obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


@pytest.mark.parametrize("case", sorted(API_INVALID))
def test_review_refuses_an_invalid_inline_set_and_writes_no_record(client, world, case):
    did = _create(client, _body(API_INVALID[case]))
    before = _ledger()
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    codes = [i["code"] for i in r.json()["detail"]["validation"]["issues"]]
    assert {"RUL_TYPE_MISMATCH", "RUL_SCHEMA_INVALID"} & set(codes), codes
    assert _ledger() == before  # no record sequence, audit, outbox, rule version or release
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR))
    assert r.status_code == 422 and r.json()["detail"]["validation"]["ok"] is False, r.text
    assert db_fingerprint() == before


@pytest.mark.parametrize("case", sorted(API_INVALID))
def test_shadow_and_publication_refuse_a_verified_draft_holding_an_invalid_inline_set(client, world, case):
    """A draft verified before members were checked can hold an invalid set: shadow and publication re-validate and
    refuse it, writing nothing (no RuleVersion, release, audit, outbox or record sequence)."""
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _verified(client)
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        ast = dict(d.ast)
        ast["children"] = [ast["children"][0], {**ast["children"][1], "args": [
            ast["children"][1]["args"][0], {**ast["children"][1]["args"][1], "value": API_INVALID[case]}]}]
        d.ast = ast
        db.commit()
    before = db_fingerprint()
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 409
    r = _publish(client, did)
    assert r.status_code == 409 and "Validation failure blocks publication" in r.text, r.text
    assert db_fingerprint() == before


def test_a_valid_inline_set_reviews_publishes_and_evaluates(client, world):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    did = _create(client, _body(VALID))
    rep = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR)).json()
    assert rep["all_passed"] and [x["actual"] for x in rep["results"]] == ["APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    assert r.json()["rule_version"]["canonical_ast"]["children"][1]["args"][1]["value"] == VALID
    for name, typ, outcome in (("in", "PAYMENT_INSTITUTION", "APPLIES"), ("out", "E_MONEY_INSTITUTION", "DOES_NOT_APPLY")):
        sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
        with AdminSession() as db:
            db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=name, country="DE", home_cell_id=CELL_ID, subject_id=sid))
            db.flush()
            db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=name))
            db.commit()
        for key, value in ((EU, True), (TYPE, typ)):
            assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
                "fact_key": key, "value": value,
                "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}).status_code == 200
        r = client.post("/api/applicability/evaluations", headers=H(CCO),
                        json={"subject_id": sid, "obligation_definition_id": S["odef"]})
        assert r.status_code == 200 and r.json()["outcome"] == outcome, r.text
