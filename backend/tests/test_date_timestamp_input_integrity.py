"""DATE and TIMESTAMP input integrity, with canonical forms left exactly as they are.

Fixed here (no policy decision needed; nothing a valid value means changes):
  * a golden-vector or sandbox effective_at was checked on its first ten characters only, so "2027-01-01garbage" was
    accepted and evaluated as 2027-01-01 (ZA-RUL-001 §13 "effective_at: Explicit time perspective"; §18 RUL_SCHEMA_INVALID);
  * a DATE fact write turned a JSON number (20270101) into a date, an implicit coercion golden-vector inputs refuse
    (RUL-INV-02 / RUL-REQ-003 "no implicit type coercion"; facts.typed_columns "no implicit coercion").
Not changed (owner decisions): the canonical DATE / TIMESTAMP form and the ISO forms accepted, TIMESTAMP precision beyond
microseconds, and how an instant with an offset maps to a legal date (DOC-CON-003). The guards below pin that canonical
bytes, hashes and published releases are unchanged. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"DTI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-dti-{TAG.lower()}", f"dti-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
DAY, AT = f"dti.{TAG.lower()}.licensed_on", f"dti.{TAG.lower()}.reported_at"
S: dict = {}


def _problems(ea):
    from app.engines import rules
    return rules.vector_input_problems({"expected_outcome": "APPLIES", "effective_at": ea, "facts": {}}, {})


# ------------------------------------------------------------------------------------------------ effective_at (pure)
@pytest.mark.parametrize("ea", ["2027-01-01garbage", "2027-01-01 nonsense!"])
def test_an_effective_at_with_trailing_text_is_refused(ea):
    """It was accepted and evaluated as 2027-01-01, the trailing text silently ignored."""
    assert [(p["code"], p["field"]) for p in _problems(ea)] == [("RUL_SCHEMA_INVALID", "effective_at")]


@pytest.mark.parametrize("ea, accepted, read_as", [
    ("2027-01-01", True, date(2027, 1, 1)), ("20270101", True, date(2027, 1, 1)), ("2026-W53-5", True, date(2027, 1, 1)),
    ("2027-01-01T23:30:00-05:00", True, date(2027, 1, 2)), ("2027-01-01 10:00", True, date(2027, 1, 1)),
    ("2027-13-01", False, None), ("2027-02-30", False, None)])
def test_every_other_effective_at_is_accepted_or_refused_and_read_as_before(ea, accepted, read_as):
    """Guard: acceptance is unchanged for every value that is a whole ISO date or date-time. A date, or a date-time
    without an offset, is read as its written date; an instant with an offset is read as its UTC calendar date, the owner
    decision (Option U) on DOC-CON-003 that this guard previously left open (test_effective_date_utc)."""
    from app.engines import rules
    assert (not _problems(ea)) is accepted
    if accepted:
        assert rules._coerce("DATE", ea) == read_as


def test_date_and_timestamp_literals_canonicalize_byte_for_byte_and_hash_as_before():
    """Guard (RUL-REQ-016 byte stability): canonicalize leaves DATE and TIMESTAMP literals exactly as written and is
    idempotent; the hash below was computed on the implementation before this change. No canonical form is chosen here."""
    from app.engines import rules
    lits = [("DATE", "20270101"), ("DATE", "2026-W53-5"), ("TIMESTAMP", "2027-01-01T01:00:00+01:00"),
            ("TIMESTAMP", "2027-01-01 00:00:00Z")]
    ast = {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"], "children": [
        {"node_type": "PREDICATE", "node_id": f"p{i}", "operator": "GTE", "args": [
            {"node_type": "FACT_REF", "node_id": f"p{i}f", "fact": "d" if t == "DATE" else "ts"},
            {"node_type": "CONST", "node_id": f"p{i}c", "type": t, "value": v}]} for i, (t, v) in enumerate(lits)]}
    canon = rules.canonicalize(ast)
    assert canon == ast and rules.canonicalize(canon) == canon
    assert rules.ast_hash(ast, {}) == "d2b45a79c002eab642a0f99a1a57ff70c545df778b68c45abceff1b629cf20f2"


def test_timestamps_compare_as_instants():
    """Guard (§03 "Offset-aware canonical instant"): one instant written with two offsets is equal at runtime; instants a
    second apart are not."""
    from app.engines import rules
    ast = {"node_type": "PREDICATE", "node_id": "p", "operator": "EQ", "args": [
        {"node_type": "FACT_REF", "node_id": "f", "fact": "ts"},
        {"node_type": "CONST", "node_id": "c", "type": "TIMESTAMP", "value": "2027-01-01T01:00:00+01:00"}]}

    def outcome(v):
        return rules.evaluate(ast, {"ts": {"known": True, "value": v, "revision_id": "r"}}, {"ts": "TIMESTAMP"}, {},
                              {"effective_at": "2027-01-01"})["outcome"]
    assert (outcome("2027-01-01T00:00:00Z"), outcome("2027-01-01T00:00:01Z")) == ("APPLIES", "DOES_NOT_APPLY")


# ------------------------------------------------------------------------------------------------ the API
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Date Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=DAY, label="Licensed on", data_type="DATE", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(FactDefinition(key=AT, label="Reporting instant", data_type="TIMESTAMP", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="DTI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Date Act",
        "locator": f"OJ L {TAG}/dti", "instrument_identifier": INSTR, "raw_text": "Licensed institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"DTI-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"DTI_{TAG}", title="Date obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    return True


def _body() -> dict:
    """ALL(established in the EU, licensed on or after 20270101, reported at or after 2027-01-01T01:00:00+01:00): literals
    written in the basic DATE form and with a non-UTC offset, so the published canonical AST shows they are kept as is."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def pred(n, op, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": f"{t}_{n}", "operator": op, "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_{n}f", "fact": fact},
            {"node_type": "CONST", "node_id": f"{t}_{n}c", "type": typ, "value": value}]}
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        pred("eu", "EQ", EU, "BOOLEAN", True), pred("day", "DATE_ON_OR_AFTER", DAY, "DATE", "20270101"),
        pred("at", "GTE", AT, "TIMESTAMP", "2027-01-01T01:00:00+01:00")]}

    def vec(vid, cls, eu, day, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2027-07-01",
                "facts": {EU: eu, DAY: day, AT: "2027-06-01T00:00:00Z"}, "expected_outcome": out}
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast, "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME",
            "golden_vectors": [vec("P", "POSITIVE", True, "2027-06-01", "APPLIES"),
                               vec("N", "NEGATIVE", True, "2026-06-01", "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "2027-06-01", "UNCERTAIN")],
            "source_mappings": [{"source_mapping_id": f"sm_{t}", "requirement_version_id": S["rv"]["requirement_version_id"],
                                 "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                                 "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT",
                                 "reviewer": LEGAL}]}


def _draft(client) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=_body())
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


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


def _fact(client, sid, key, value):
    return client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})


def test_the_sandbox_refuses_an_effective_at_with_trailing_text(client, world):
    """The fixture used to run at 2027-01-01 with the trailing text ignored; it is refused before evaluation (422)."""
    did = _draft(client)
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR),
                    json={"facts": {EU: True}, "effective_at": "2027-01-01garbage"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RUL_SCHEMA_INVALID", r.text
    assert db_fingerprint() == before


def test_a_date_fact_written_as_a_number_is_refused(client, world):
    """The number 20270101 was stored as the date 2027-01-01. It is refused with 422 before anything is written, as a
    golden vector giving the same value already was."""
    sid = _subject(client, f"num-{secrets.token_hex(2)}", {})
    before = db_fingerprint()
    r = _fact(client, sid, DAY, 20270101)
    assert r.status_code == 422, r.text
    assert db_fingerprint() == before


@pytest.mark.parametrize("spelling", ["2027-01-01", "20270101", "2026-W53-5"])
def test_an_accepted_date_spelling_is_stored_as_one_calendar_date(client, world, spelling):
    """Guard: whichever ISO spellings a fact write accepts (which ones should be accepted is an open decision), the value
    stored and read back is the calendar date itself, identically on SQLite and PostgreSQL."""
    from app.db import AdminSession
    from app.engines.facts import assertion_value
    from app.models import FactAssertion, FactDefinition
    sid = _subject(client, f"sp-{secrets.token_hex(2)}", {})
    r = _fact(client, sid, DAY, spelling)
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        with AdminSession() as db:
            fd = db.execute(select(FactDefinition).where(FactDefinition.key == DAY)).scalars().one()
            [a] = db.execute(select(FactAssertion).where(FactAssertion.subject_id == sid,
                                                         FactAssertion.fact_definition_id == fd.fact_definition_id)).scalars()
            assert (a.value_date, assertion_value(a)) == (date(2027, 1, 1), "2027-01-01")


def test_a_published_rule_keeps_its_date_and_timestamp_literals_and_still_verifies(client, world):
    """Guard (historical compatibility): the released canonical AST keeps "20270101" and the +01:00 offset exactly as
    authored, its golden vectors passed on that AST, release verification is INTACT and it evaluates."""
    did = _draft(client)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"dti-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    rv, cr = r.json()["rule_version"], r.json()["content_release"]
    assert [c["args"][1]["value"] for c in rv["canonical_ast"]["children"][1:]] == ["20270101", "2027-01-01T01:00:00+01:00"]
    v = client.get(f"/api/catalog/releases/{cr['content_release_id']}/verify", headers=H(AUTHOR))
    assert v.status_code == 200 and v.json()["integrity"] == "INTACT", v.text
    sid = _subject(client, "rel", {EU: True, DAY: "2027-06-01", AT: "2027-01-01T00:00:00Z"})  # the instant itself
    ev = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid,
                                                                            "obligation_definition_id": S["odef"]})
    assert ev.status_code == 200 and ev.json()["outcome"] == "APPLIES", ev.text
