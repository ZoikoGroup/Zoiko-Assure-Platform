"""Effective-date interpretation: one UTC calendar date per instant on every path (owner decision "Option U").

Owner decision (TIM owner and RUL owner, Option U), the explicit conversion rule ZA-TIM-001 §02 requires for comparing an
instant with legal dates (RuleVersion legal effect is a LEGAL_DATE_RANGE): an offset-aware effective_at is converted to
UTC and its UTC calendar date is the legal date (rules.legal_date). "2027-01-01T23:30:00-05:00" is 2027-01-02.
Golden vectors and the sandbox read the first ten characters as written (2027-01-01) while live evaluation, replay, rule
selection, shadow, publication impact and the catalogue used the UTC date; they now all derive it from rules.legal_date.
Validity stays [valid_from, valid_to) (TIM-REQ-002). Unchanged: accepted DATE/TIMESTAMP syntax, canonical forms, stored
evaluations, bundles and evaluation keys (live evaluation already used the UTC date). Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"EDU-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-edu-{TAG.lower()}", f"edu-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
SAME_INSTANT = ["2027-01-01T23:30:00-05:00", "2027-01-02T04:30:00Z", "2027-01-02T04:30:00+00:00",
                "2027-01-02T05:30:00+01:00", "2027-01-02T13:30:00+09:00", "2027-01-01T18:30:00-10:00"]
CROSSING = ["2027-01-01T23:30:00-05:00", "2027-01-01T18:30:00-10:00"]  # written date 2027-01-01, UTC date 2027-01-02
S: dict = {}


def _context_rule(threshold: str, t: str = "x") -> dict:
    """ALL(established in the EU, effective date on or after `threshold`)."""
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_on", "operator": "DATE_ON_OR_AFTER", "args": [
            {"node_type": "CONTEXT_REF", "node_id": f"{t}_ctx", "key": "effective_at"},
            {"node_type": "CONST", "node_id": f"{t}_d", "type": "DATE", "value": threshold}]}]}


FD = {EU: {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"}}


# ------------------------------------------------------------------------------------------------ the conversion (pure)
def test_one_instant_written_six_ways_is_one_legal_date():
    """The evaluator read the date as written: the two spellings west of UTC gave 2027-01-01, the others 2027-01-02."""
    from app.engines import rules
    assert {rules._coerce("DATE", ea) for ea in SAME_INSTANT} == {date(2027, 1, 2)}


@pytest.mark.parametrize("ea", CROSSING)
def test_golden_vectors_read_the_utc_date_of_the_instant(ea):
    """A vector at an instant west of UTC near midnight evaluated the previous day (DOES_NOT_APPLY here)."""
    from app.engines import rules
    vr = rules.run_golden_vectors(_context_rule("2027-01-02"), [{"vector_id": "v", "vector_class": "POSITIVE", "effective_at": ea,
                                                                 "facts": {EU: True}, "expected_outcome": "APPLIES"}], FD, {})
    assert vr["results"][0]["actual"] == "APPLIES", vr


@pytest.mark.parametrize("value, expected", [
    ("2027-01-01", date(2027, 1, 1)), ("20270101", date(2027, 1, 1)), ("2026-W53-5", date(2027, 1, 1)),
    ("2027-01-01T23:30:00", date(2027, 1, 1)), (date(2027, 1, 1), date(2027, 1, 1))],
    ids=["extended", "basic", "week", "naive-text", "date"])
def test_dates_and_values_without_an_offset_keep_their_reading(value, expected):
    """Guard (passes before and after): a date is itself and a date-time without an offset keeps its written date."""
    from app.engines import rules
    assert rules._coerce("DATE", value) == expected


@pytest.mark.parametrize("ea, accepted", [("2027-01-01T23:30:00", True), ("20270101T000000Z", True),
                                          ("2027-01-01garbage", False), ("2027-13-01", False)])
def test_effective_at_validation_is_unchanged(ea, accepted):
    """Guard: which effective_at values a golden vector or sandbox fixture may carry is unchanged (naive accepted,
    malformed refused); only how an accepted instant is read changed."""
    from app.engines import rules
    assert (not rules.vector_input_problems({"expected_outcome": "APPLIES", "effective_at": ea, "facts": {}}, {})) is accepted


def test_timestamp_and_date_literal_validation_is_unchanged():
    """Guard: a TIMESTAMP literal without an offset is still refused, and invalid date text is still a type error."""
    from app.engines import rules
    ast = {"node_type": "PREDICATE", "node_id": "p", "operator": "EQ", "source_mappings": ["s"], "args": [
        {"node_type": "CONST", "node_id": "a", "type": "TIMESTAMP", "value": "2027-01-01T00:00:00"},
        {"node_type": "CONST", "node_id": "b", "type": "TIMESTAMP", "value": "2027-01-01T00:00:00Z"}]}
    assert "RUL_TYPE_MISMATCH" in {i["code"] for i in rules.validate(ast, FD, {})["issues"]}
    with pytest.raises(rules.RuleError) as e:
        rules._coerce("DATE", "soon")
    assert e.value.code == "RUL_TYPE_MISMATCH"


# ------------------------------------------------------------------------------------------------ the API
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Effective date Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="EDU CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Effective date Act",
        "locator": f"OJ L {TAG}/edu", "instrument_identifier": INSTR, "raw_text": "Institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"EDU-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["subject"] = _subject("main")
    assert client.post(f"/api/subjects/{S['subject']}/facts", headers=H(CCO), json={
        "fact_key": EU, "value": True, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}).status_code == 200
    S["ctx_odef"] = _odef()
    S["ctx_draft"], _ = _published(client, S["ctx_odef"], "2027-01-02", "2024-01-01", None, "2027-03-01")
    S["window_odef"] = _odef()  # in force on [2027-01-02, 2027-01-03) only
    _published(client, S["window_odef"], "2000-01-01", "2027-01-02", "2027-01-03", "2027-01-02")
    return True


def _subject(name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _odef() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"EDU_{TAG}_{secrets.token_hex(2)}", title="Effective date obligation",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef, threshold, valid_from, valid_to, vector_at) -> dict:
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]

    def vec(vid, cls, eu, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": vector_at, "facts": {EU: eu},
                "expected_outcome": out}
    return {"obligation_definition_id": odef, "authoring_mode": "AST", "ast": _context_rule(threshold, t),
            "valid_from": valid_from, "valid_to": valid_to, "change_class": "EXECUTABLE_OUTCOME",
            "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
            "source_mappings": [{"source_mapping_id": f"sm_{t}", "requirement_version_id": S["rv"]["requirement_version_id"],
                                 "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                                 "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT",
                                 "reviewer": LEGAL}]}


def _shadowed(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _published(client, odef, threshold, valid_from, valid_to, vector_at):
    did = _shadowed(client, _body(odef, threshold, valid_from, valid_to, vector_at))
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"edu-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return did, r.json()


def _evaluate(client, odef, ea, sid=None):
    return client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid or S["subject"], "obligation_definition_id": odef, "effective_at": ea})


def _shift_instants(monkeypatch, to: date):
    """Every instant's legal date becomes `to` (as if evaluated then); dates and text keep the real conversion. A path
    that derives its date elsewhere than rules.legal_date does not see the change."""
    from app.engines import rules
    real = getattr(rules, "legal_date", None)
    monkeypatch.setattr(rules, "legal_date", lambda v: to if isinstance(v, datetime) else real(v), raising=False)


@pytest.mark.parametrize("ea", SAME_INSTANT)
def test_sandbox_and_live_evaluation_agree_on_the_same_instant(client, world, ea):
    """The sandbox read the written date (DOES_NOT_APPLY for the two west-of-UTC spellings); live evaluation read the UTC
    date. Both now give the outcome at 2027-01-02 for every spelling."""
    sb = client.post(f"/api/rules/drafts/{S['ctx_draft']}/sandbox", headers=H(AUTHOR),
                     json={"facts": {EU: True}, "effective_at": ea})
    live = _evaluate(client, S["ctx_odef"], ea)
    assert sb.status_code == 200 and live.status_code == 200, (sb.text, live.text)
    assert (sb.json()["outcome"], live.json()["outcome"]) == ("APPLIES", "APPLIES")


def test_replay_reproduces_the_live_evaluation_of_the_instant(client, world):
    """Guard: the stored instant replays to the same legal date and decision; the record keeps the instant (UTC)."""
    ev = _evaluate(client, S["ctx_odef"], "2027-01-01T23:30:00-05:00").json()
    view = client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(CCO)).json()
    assert view["replay"]["status"] == "REPRODUCED" and view["outcome"] == "APPLIES"
    assert datetime.fromisoformat(view["effective_at"].replace("Z", "+00:00")) == datetime(2027, 1, 2, 4, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("ea, in_force", [
    ("2027-01-01T23:59:59Z", False), ("2027-01-02T00:00:00Z", True), ("2027-01-02T23:59:59Z", True),
    ("2027-01-03T00:00:00Z", False), ("2027-01-01T23:30:00-05:00", True), ("2027-01-02T00:30:00+01:00", False),
    ("2027-01-02T20:00:00-05:00", False), ("2027-01-03T00:30:00+01:00", True)])
def test_rule_selection_uses_the_utc_date_with_inclusive_from_and_exclusive_to(client, world, ea, in_force):
    """Guard (live selection already used the UTC date): the version valid on [2027-01-02, 2027-01-03) is in force for
    exactly the instants whose UTC date is 2027-01-02, whatever offset they are written in."""
    r = _evaluate(client, S["window_odef"], ea)
    if in_force:
        assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", r.text
    else:
        assert r.status_code == 409 and r.json()["detail"]["error"] == "RULE_NOT_EFFECTIVE", r.text


def test_shadow_derives_its_effective_date_from_the_shared_conversion(client, world, monkeypatch):
    """Shadow read now.date() itself. Moving every instant's legal date to 2099-01-05 through rules.legal_date now moves
    the shadow run's effective date too: subjects in the EU become APPLIES for a rule effective from 2099-01-01."""
    did = _shadowed(client, _body(_odef(), "2099-01-01", "2024-01-01", None, "2099-06-01"))

    def applies(rep):
        return sum(n for k, n in rep["transition_buckets"].items() if k.endswith("->APPLIES"))
    before = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).json()
    _shift_instants(monkeypatch, date(2099, 1, 5))
    after = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).json()
    assert applies(before) == 0 and applies(after) >= 1, (before, after)


def test_publication_impact_derives_its_effective_date_from_the_shared_conversion(client, world, monkeypatch):
    """Publication impact re-evaluates through the live evaluator, now on the shared conversion: with every instant's
    legal date moved to 2099-01-05 the re-evaluated subject turns APPLIES under the new version. The release verifies."""
    odef = _odef()
    _published(client, odef, "2099-01-01", "2024-01-01", None, "2099-06-01")
    sid = _subject(f"imp-{secrets.token_hex(2)}")
    assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": EU, "value": True, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}).status_code == 200
    first = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": odef})
    assert first.status_code == 200 and first.json()["outcome"] == "DOES_NOT_APPLY", first.text
    did = _shadowed(client, _body(odef, "2099-01-01", "2024-01-01", None, "2099-06-01"))
    _shift_instants(monkeypatch, date(2099, 1, 5))
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"edu-{secrets.token_hex(4)}",
                                                                                   "changelog": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["impact"]["transitions"].get("DOES_NOT_APPLY->APPLIES", 0) >= 1, r.json()["impact"]
    monkeypatch.undo()
    v = client.get(f"/api/catalog/releases/{r.json()['content_release']['content_release_id']}/verify", headers=H(AUTHOR))
    assert v.status_code == 200 and v.json()["integrity"] == "INTACT", v.text


def test_catalogue_selection_derives_today_from_the_shared_conversion(client, world, monkeypatch):
    """Catalogue effective-version selection (and the obligation-definition cache key built from the same date) used its
    own UTC date; it now uses rules.legal_date, so a version effective from 2099-01-02 is in force when every instant's
    legal date is 2099-01-05 and not before."""
    from app.db import AdminSession
    from app.engines import catalog
    odef = _odef()
    _, pub = _published(client, odef, "2000-01-01", "2099-01-02", None, "2099-06-01")
    with AdminSession() as db:
        assert catalog.effective_rule_version(db, odef) is None
        _shift_instants(monkeypatch, date(2099, 1, 5))
        assert catalog._today() == date(2099, 1, 5)
        assert catalog.effective_rule_version(db, odef).rule_version_id == pub["rule_version"]["rule_version_id"]
