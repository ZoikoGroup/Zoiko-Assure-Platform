"""Golden vectors and sandbox fixtures state their time explicitly; none is assumed.

Controlling text:
  * ZA-RUL-001 §13 GoldenVector "effective_at / record_cutoff: Explicit time perspective"; §20 WF-RUL-04 "Time
    controls: effective_at and record_cutoff explicit"; RUL-REQ-017 (every released RuleVersion passes its golden
    vectors on the target evaluator build); §18 RUL_SCHEMA_INVALID for a malformed field.
  * The legal date of an instant is its UTC calendar date (rules.legal_date, Option U: the contract
    tests/test_effective_date_utc*.py pin); malformed and calendar-edge values are pinned by
    tests/test_temporal_failure_paths.py.

Defect corrected: rules.run_golden_vectors ran a vector without effective_at at a hard-coded 2027-12-02, and the
/rules/drafts/{id}/sandbox request defaulted effective_at to the same date, so a vector or fixture with no stated time
silently passed or failed by a date nobody chose. Pinned here: a missing (or null) effective_at is refused as a
malformed vector or fixture is (RUL_SCHEMA_INVALID on field effective_at), the vector is never evaluated, the release
gate refuses it, and an explicit effective_at alone decides the outcome, deterministically.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from conftest import H

TAG = secrets.token_hex(3)
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"GVT-{TAG.upper()}-REG"
EU = f"gvt{TAG}.eu"
OLD_FALLBACK = "2027-12-02"
S: dict = {}


def _rule(t: str = "g", mapping: str | None = None) -> dict:
    """ALL(EU fact is TRUE, effective date on or after 2027-01-01): the outcome depends on the time perspective."""
    root = {"node_type": "ALL", "node_id": f"{t}_root", "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_on", "operator": "DATE_ON_OR_AFTER", "args": [
            {"node_type": "CONTEXT_REF", "node_id": f"{t}_ctx", "key": "effective_at"},
            {"node_type": "CONST", "node_id": f"{t}_d", "type": "DATE", "value": "2027-01-01"}]}]}
    if mapping:
        root["source_mappings"] = [mapping]
    return root


FACT_DEFS = {EU: {"data_type": "BOOLEAN", "enum_domain": None, "status": "RELEASED"}}


def _vec(vid, cls, eu, expected, **time):
    return {"vector_id": vid, "vector_class": cls, "description": vid, "facts": {EU: eu}, "expected_outcome": expected,
            **time}


def _run(vectors):
    from app.engines import rules
    return rules.run_golden_vectors(_rule(), vectors, FACT_DEFS, {})


# ------------------------------------------------------------------------------------------------------------ engine
def test_the_explicit_effective_at_alone_decides_the_outcome():
    rep = _run([_vec("before", "NEGATIVE", True, "DOES_NOT_APPLY", effective_at="2026-12-31"),
                _vec("on", "POSITIVE", True, "APPLIES", effective_at="2027-01-01"),  # inclusive boundary
                _vec("old-fallback-date", "POSITIVE", True, "APPLIES", effective_at=OLD_FALLBACK),
                _vec("unknown", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN", effective_at="2027-06-01")])
    assert rep["all_passed"] is True and rep["passed"] == 4, rep["results"]


def test_a_vector_without_effective_at_is_refused_and_never_evaluated(monkeypatch):
    """At the old fallback date this vector would have APPLIED and passed; now it is refused before evaluation."""
    from app.engines import rules
    calls = []
    original = rules.evaluate
    monkeypatch.setattr(rules, "evaluate", lambda *a, **k: calls.append(a[4]) or original(*a, **k))
    rep = _run([_vec("no-time", "POSITIVE", True, "APPLIES"),
                _vec("with-time", "NEGATIVE", True, "DOES_NOT_APPLY", effective_at="2026-06-01")])
    missing, explicit = rep["results"]
    assert (missing["passed"], missing["error"]) == (False, "RUL_SCHEMA_INVALID")
    assert [(i["code"], i["field"]) for i in missing["issues"]] == [("RUL_SCHEMA_INVALID", "effective_at")]
    assert "actual" not in missing  # no outcome was produced for it
    assert explicit["passed"] is True and explicit["actual"] == "DOES_NOT_APPLY"
    assert calls == [{"effective_at": "2026-06-01"}]  # only the explicit vector ran, at its own time
    assert rep["all_passed"] is False


@pytest.mark.parametrize("value", [None, "", "2027-13-01", "2027-02-30", "2027-01-01garbage", 20270101, True,
                                   "0001-01-01T00:00:00+01:00"])  # the existing format rule (rules._effective_at_ok)
def test_a_null_or_malformed_effective_at_is_refused(value):
    from app.engines import rules
    problems = rules.vector_input_problems(_vec("v", "POSITIVE", True, "APPLIES", effective_at=value), FACT_DEFS)
    assert [(p["code"], p["field"]) for p in problems] == [("RUL_SCHEMA_INVALID", "effective_at")], value


@pytest.mark.parametrize("instant, expected", [
    ("2026-12-31T23:30:00-05:00", "APPLIES"),          # 2027-01-01T04:30Z: on the threshold date
    ("2027-01-01T00:30:00+01:00", "DOES_NOT_APPLY"),   # 2026-12-31T23:30Z: the day before
    ("2027-01-01T00:00:00Z", "APPLIES"),
    ("2027-01-01", "APPLIES"),
])
def test_an_instant_is_read_as_its_utc_calendar_date(instant, expected):
    rep = _run([_vec("tz", "POSITIVE", True, expected, effective_at=instant)])
    assert rep["results"][0]["passed"] is True and rep["results"][0]["actual"] == expected, rep["results"]


def test_identical_vectors_give_identical_reports():
    vectors = [_vec("a", "POSITIVE", True, "APPLIES", effective_at="2027-06-01"),
               _vec("b", "NEGATIVE", False, "DOES_NOT_APPLY", effective_at="2027-06-01"),
               _vec("c", "POSITIVE", True, "APPLIES")]
    assert _run(vectors) == _run(vectors)


# ------------------------------------------------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def draft(client):
    """A rule of a fresh obligation definition, grounded in verified content, with four vectors that state their time
    and one ("no-time") that does not."""
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(FactDefinition(key=EU, label=EU, data_type="BOOLEAN", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Instrument(identifier=INSTR, title="Time Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Time Act",
        "locator": f"OJ L {TAG}/gvt", "instrument_identifier": INSTR, "raw_text": "From 1 January 2027 entities shall."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"GVT-{TAG}-REQ",
        "title": "Dated requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "comply",
             "regulated_object": "duty", "timing": "from 2027", "normalized_meaning": "Comply from 2027.",
             "evidence_expectation": "Record"}]})
    assert rv.status_code in (200, 201), rv.text
    assert client.post(f"/api/catalog/requirements/{rv.json()['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    prov = rv.json()["provisions"][0]
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"GVT_{TAG.upper()}", title="Dated duty", instrument_id=inst.instrument_id,
                                 provision_ids=[prov["provision_id"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    mapping = {"source_mapping_id": f"sm_gvt_{TAG}", "requirement_version_id": rv.json()["requirement_version_id"],
               "provision_id": prov["provision_id"], "authoritative_reference": prov["locator"],
               "source_fingerprint": src.json()["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    explicit = [_vec("P", "POSITIVE", True, "APPLIES", effective_at="2027-06-01"),
                _vec("N", "NEGATIVE", False, "DOES_NOT_APPLY", effective_at="2027-06-01"),
                _vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN", effective_at="2027-06-01"),
                _vec("T", "NEGATIVE", True, "DOES_NOT_APPLY", effective_at="2026-06-01")]
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": [mapping],
            "ast": _rule(f"gvt{TAG}", mapping["source_mapping_id"]),
            "golden_vectors": [*explicit, _vec("no-time", "POSITIVE", True, "APPLIES")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    S.update(draft_id=r.json()["draft_id"], body=body, explicit=explicit)
    return S


def test_the_draft_test_run_reports_the_vector_without_time_as_malformed(client, draft):
    rep = client.post(f"/api/rules/drafts/{draft['draft_id']}/test", headers=H(AUTHOR)).json()
    by_id = {r["vector_id"]: r for r in rep["results"]}
    assert (by_id["no-time"]["passed"], by_id["no-time"]["error"]) == (False, "RUL_SCHEMA_INVALID")
    assert all(by_id[v]["passed"] for v in ("P", "N", "U", "T")) and rep["all_passed"] is False


def test_the_release_gate_refuses_a_vector_without_time_and_accepts_it_stated(client, draft):
    from app.db import AdminSession
    from app.models import RuleDraft, RuleVersion
    did = draft["draft_id"]
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_GOLDEN_FAILURE", r.text
    failed = [x for x in r.json()["detail"]["vectors"]["results"] if not x["passed"]]
    assert [(x["vector_id"], x["error"]) for x in failed] == [("no-time", "RUL_SCHEMA_INVALID")]
    with AdminSession() as db:
        assert db.get(RuleDraft, did).status == "DRAFT"  # never entered review
    # The same vector with its time stated: it passes every gate and is released with it.
    stated = {**draft["body"], "golden_vectors": [*draft["explicit"],
                                                  _vec("no-time", "POSITIVE", True, "APPLIES", effective_at=OLD_FALLBACK)]}
    assert client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=stated).status_code == 200
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"gvt-{secrets.token_hex(4)}", "changelog": "explicit vector time"})
    assert r.status_code == 200, r.text
    with AdminSession() as db:
        rv = db.execute(select(RuleVersion).where(
            RuleVersion.obligation_definition_id == draft["body"]["obligation_definition_id"])).scalars().one()
        assert all(v.get("effective_at") for v in rv.golden_vectors)


def test_the_sandbox_refuses_a_fixture_without_time_and_uses_the_stated_one(client, draft):
    from test_authorization import db_fingerprint
    did = draft["draft_id"]
    before = db_fingerprint()
    for body in ({"facts": {EU: True}}, {"facts": {EU: True}, "effective_at": None}):
        r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR), json=body)
        assert r.status_code == 422 and r.json()["detail"]["error"] == "RUL_SCHEMA_INVALID", (body, r.text)
        assert [i["field"] for i in r.json()["detail"]["validation"]["issues"]] == ["effective_at"]
    assert db_fingerprint() == before  # refusals write nothing
    for at, outcome, legal in (("2026-06-01", "DOES_NOT_APPLY", "2026-06-01"), (OLD_FALLBACK, "APPLIES", OLD_FALLBACK),
                               ("2026-12-31T23:30:00-05:00", "APPLIES", "2027-01-01")):
        r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR), json={"facts": {EU: True}, "effective_at": at})
        assert r.status_code == 200, r.text
        body = r.json()
        ctx = body["trace"]["children"][1]["children"][0]
        assert (body["outcome"], ctx["node_type"], ctx["display_value"]) == (outcome, "CONTEXT_REF", legal), at
