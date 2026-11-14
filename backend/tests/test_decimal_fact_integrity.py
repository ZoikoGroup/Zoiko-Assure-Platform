"""DECIMAL fact integrity: non-finite values, column precision/scale, evaluation, golden vectors and replay.

Controlling text:
  * ZA-RUL-001 §05 type system ("DECIMAL turnover = 12.50: Exact decimal arithmetic; no binary floating-point legal
    thresholds"; literal rule "Decimal: Canonical decimal string"), §13 golden vectors ("Typed inputs"), §18
    RUL_TYPE_MISMATCH.
  * ZA-APP-001 §12 error responses ("EVALUATOR_UNAVAILABLE: Infrastructure error before deterministic result; do not
    store UNCERTAIN as a substitute"): an input the evaluator cannot compare is refused, never turned into an outcome.
  * ZA-DAT-001 typed fact value columns (value_decimal: migration 0001 numeric(38,10)); API-REQ-033 (input parsers
    enforce the stored field's limits); TIM-REQ-005 (a refused write takes no record sequence); DAT-REQ-014.
  * ZA-TIM-001 TIM-REQ-012 / APP-REQ-003 replay: REPRODUCED / NOT_REPRODUCED from the stored records, never by
    substituting current values; nothing is written.

NaN, sNaN and +/-Infinity are not decimal values. A fact write carrying one is refused (422) before the record sequence
is taken; so is a value the value_decimal column cannot hold exactly (more than 28 integer or 10 fractional digits:
PostgreSQL rounded the fraction silently and failed on the integer part after the sequence was taken). A non-finite
value stored before this check is never compared: evaluation refuses it with RUL_TYPE_MISMATCH, replay reports the
evaluation NOT_REPRODUCED.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"DFI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-dfia-{TAG.lower()}", f"tenant-dfib-{TAG.lower()}"
CCO, CCO_B = f"dfi-cco-{TAG.lower()}", f"dfib-cco-{TAG.lower()}"
EU, TURN = "entity.established_in_eu", f"entity.turnover_{TAG.lower()}"
UNK = {"unknown": "MISSING_REQUIRED_FACT"}
S: dict = {}
NON_FINITE = ["NaN", "nan", "sNaN", "-NaN", "Infinity", "-Infinity", "+Infinity", "inf", "-inf",
              float("nan"), float("inf"), float("-inf")]


def _pred(nid, fact, op, typ, value):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}


def _vec(vid, cls, turnover, expected):
    return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
            "facts": {EU: True, TURN: turnover}, "expected_outcome": expected}


RULES = {  # APPLIES iff established in the EU and turnover >= 50,000,000.00 / and turnover = 0.00
    "threshold": ("GTE", "50000000.00", [_vec("P", "POSITIVE", "60000000.00", "APPLIES"),
                                         _vec("N", "NEGATIVE", "10.50", "DOES_NOT_APPLY"),
                                         _vec("U", "UNKNOWN", UNK, "UNCERTAIN")]),
    "zero": ("EQ", "0.00", [_vec("P", "POSITIVE", "0.00", "APPLIES"), _vec("N", "NEGATIVE", "1.00", "DOES_NOT_APPLY"),
                            _vec("U", "UNKNOWN", UNK, "UNCERTAIN")]),
}


def _body(rule="threshold", *, literal=None, extra=()) -> dict:
    op, const, vectors = RULES[rule]
    t, p = f"d{secrets.token_hex(3)}", S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        _pred(f"{t}_1", EU, "EQ", "BOOLEAN", True), _pred(f"{t}_2", TURN, op, "DECIMAL", literal or const)]}
    return {"obligation_definition_id": S["odef"][rule], "authoring_mode": "AST", "ast": ast,
            "golden_vectors": [*vectors, *extra], "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME",
            "source_mappings": maps}


def _create(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _publish_rule(client, rule):
    did = _create(client, _body(rule))
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"),
                        (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"dfi-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Decimal Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TURN, label="Annual turnover", data_type="DECIMAL", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        for t in (TA, TB):
            db.add(Tenant(tenant_id=t, legal_name=f"{t} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="DFI CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="DFI CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Decimal Act",
        "locator": f"OJ L {TAG}/dfi", "instrument_identifier": INSTR, "raw_text": "Large institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"DFI-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["odef"] = {}
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        for rule in RULES:
            o = ObligationDefinition(key=f"DFI_{TAG}_{rule.upper()}", title=f"Decimal {rule}", instrument_id=inst.instrument_id,
                                     provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                     subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                     material=True)
            db.add(o)
            db.flush()
            S["odef"][rule] = o.obligation_definition_id
        db.commit()
    for rule in RULES:
        _publish_rule(client, rule)
    return True


def _subject(client, name: str, tenant: str = TA, cco: str = CCO, eu: bool | None = True) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    if eu is not None:
        r = _fact(client, sid, EU, eu, cco=cco)
        assert r.status_code == 200, r.text
    return sid


def _fact(client, sid, key, value, *, cco=CCO, **extra):
    body = {"fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            **extra}
    # json.dumps writes a float NaN / Infinity as the JSON tokens a client can send (Python's parser accepts them)
    return client.post(f"/api/subjects/{sid}/facts", headers={**H(cco), "content-type": "application/json"},
                       content=json.dumps(body))


def _evaluate(client, sid, rule="threshold", cco=CCO):
    return client.post("/api/applicability/evaluations", headers=H(cco), json={
        "subject_id": sid, "obligation_definition_id": S["odef"][rule]})


def _stored(sid) -> list:
    from app.db import AdminSession
    from app.models import FactAssertion, FactDefinition
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == TURN)).scalars().one()
        return [a.value_decimal for a in db.execute(select(FactAssertion).where(
            FactAssertion.subject_id == sid, FactAssertion.fact_definition_id == fd.fact_definition_id)
            .order_by(FactAssertion.record_from_seq, FactAssertion.revision_id)).scalars()]


def _legacy_writes(monkeypatch):
    """A non-finite value stored before fact writes refused it: the DECIMAL branch as it was (str(Decimal(str(v))))."""
    from app.engines import facts
    real = facts.typed_columns

    def legacy(fd, value):
        if fd.data_type != "DECIMAL":
            return real(fd, value)
        return {**real(fd, "0"), "value_decimal": str(Decimal(str(value)))}
    monkeypatch.setattr(facts, "typed_columns", legacy)


# ------------------------------------------------------------------------------------------------ fact writes
@pytest.mark.parametrize("value", NON_FINITE, ids=[repr(v) for v in NON_FINITE])
def test_a_non_finite_decimal_fact_is_refused_before_anything_is_written(client, world, value):
    """Refused (422) before the record sequence is taken: no fact revision or history, evaluation, determination,
    audit row, outbox event or record sequence, and the subject's existing evaluation is not re-run."""
    if "refuse" not in S:
        S["refuse"] = _subject(client, "refuse")
        assert _evaluate(client, S["refuse"]).json()["outcome"] == "UNCERTAIN"  # turnover unknown: a dependent exists
    before = db_fingerprint()
    r = _fact(client, S["refuse"], TURN, value)
    assert r.status_code == 422, r.text
    assert "not a valid DECIMAL" in r.text
    assert db_fingerprint() == before


@pytest.mark.parametrize("value, outcome", [
    ("50000000.00", "APPLIES"),                                   # the threshold itself (GTE)
    ("49999999.9999999999", "DOES_NOT_APPLY"),                    # ten fractional digits: the column's scale
    ("9999999999999999999999999999.9999999999", "APPLIES"),       # 28 + 10 digits: the largest the column holds
    ("-9999999999999999999999999999.9999999999", "DOES_NOT_APPLY"),
    ("0.0000000001", "DOES_NOT_APPLY"),
    ("1.50000000000000", "DOES_NOT_APPLY"),                       # trailing zeros beyond the scale: no rounding
    ("5E+7", "APPLIES"),
    (12.5, "DOES_NOT_APPLY"),
    (60000000, "APPLIES"),
    ("0", "DOES_NOT_APPLY"),
], ids=lambda v: repr(v))
def test_a_finite_decimal_keeps_its_exact_value_and_compares_exactly(client, world, value, outcome):
    sid = _subject(client, f"ok-{secrets.token_hex(3)}")
    r = _fact(client, sid, TURN, value)
    assert r.status_code == 200, r.text
    [stored] = _stored(sid)
    assert Decimal(str(stored)) == Decimal(str(value))  # the value written, never rounded
    r = _evaluate(client, sid)
    assert r.status_code == 200 and r.json()["outcome"] == outcome, r.text


@pytest.mark.parametrize("value", ["0.12345678901", "-0.00000000001", "1E-11", "10000000000000000000000000000", "1E+28",
                                   "-99999999999999999999999999999.5"])
def test_a_decimal_the_column_cannot_hold_exactly_is_refused_not_rounded(client, world, value):
    """value_decimal is numeric(38,10): more than 10 fractional digits were rounded silently by PostgreSQL (and kept
    exactly by SQLite), more than 28 integer digits failed at persistence after the record sequence was taken."""
    if "scale" not in S:
        S["scale"] = _subject(client, "scale")
    before = db_fingerprint()
    r = _fact(client, S["scale"], TURN, value)
    assert r.status_code == 422, r.text
    assert db_fingerprint() == before


def test_fact_versions_and_tenant_isolation_are_unchanged(client, world):
    sid_b = _subject(client, "tenant-b", tenant=TB, cco=CCO_B)
    assert _fact(client, sid_b, TURN, "70000000.00", cco=CCO_B).status_code == 200
    assert _fact(client, sid_b, TURN, "70000000.00", cco=CCO).status_code == 404  # another organisation's subject
    r = _fact(client, sid_b, TURN, "80000000.25", cco=CCO_B, change_kind="CORRECTION", reason="restated accounts")
    assert r.status_code == 200, r.text
    history = _stored(sid_b)
    assert [Decimal(str(v)) for v in history][:1] == [Decimal("70000000.00")] and Decimal("80000000.25") in \
        [Decimal(str(v)) for v in history]
    before = db_fingerprint()
    r = _fact(client, sid_b, TURN, "NaN", cco=CCO_B, change_kind="CORRECTION", reason="bad feed")
    assert r.status_code == 422 and db_fingerprint() == before and _stored(sid_b) == history
    assert _evaluate(client, sid_b, cco=CCO_B).json()["outcome"] == "APPLIES"


# ------------------------------------------------------------------------------------------------ rule content
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "0.12345678901"])
def test_a_non_finite_or_unstorable_golden_vector_value_is_an_ordinary_vector_failure(client, world, value):
    did = _create(client, _body(extra=[_vec("B", "BOUNDARY", value, "APPLIES")]))
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_GOLDEN_FAILURE", r.text
    [bad] = [x for x in r.json()["detail"]["vectors"]["results"] if not x["passed"]]
    assert bad["vector_id"] == "B" and bad["error"] == "RUL_TYPE_MISMATCH", bad
    rep = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR)).json()
    assert rep["all_passed"] is False and rep["passed"] == 3
    r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR),
                    json={"facts": {EU: True, TURN: value}, "effective_at": "2025-06-01"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RUL_TYPE_MISMATCH", r.text


@pytest.mark.parametrize("literal", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_a_non_finite_decimal_literal_is_refused_at_validation(client, world, literal):
    """RUL §05 literal rule: a Decimal literal is a canonical decimal string; NaN / Infinity are not decimals."""
    did = _create(client, _body(literal=literal))
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    assert "RUL_TYPE_MISMATCH" in [i["code"] for i in r.json()["detail"]["validation"]["issues"]], r.text


# ------------------------------------------------------------------------------------------------ stored before the check
def test_a_stored_non_finite_value_is_never_compared(client, world, monkeypatch):
    """Evaluation refuses it with RUL_TYPE_MISMATCH and writes nothing (APP §12: never UNCERTAIN as a substitute); a
    later fact write still commits and reports the dependent it could not re-evaluate; the history comparison reports
    the error per side; the shadow run counts it as an execution error."""
    _legacy_writes(monkeypatch)
    sid = _subject(client, "legacy-nan", eu=False)
    assert _fact(client, sid, TURN, "NaN").status_code == 200
    monkeypatch.undo()
    assert _evaluate(client, sid).json()["outcome"] == "DOES_NOT_APPLY"  # ALL decided by EU = false; turnover unread
    r = _fact(client, sid, EU, True, change_kind="CORRECTION", reason="relocated")
    assert r.status_code == 200, r.text
    [failed] = [x for x in r.json()["impact_reevaluations"] if "error" in x]
    assert failed["error"]["error"] == "RUL_TYPE_MISMATCH", failed
    before = db_fingerprint()
    r = _evaluate(client, sid)
    assert r.status_code == 500 and r.json()["detail"]["error"] == "RUL_TYPE_MISMATCH", r.text
    assert db_fingerprint() == before
    from app.platform import current_watermark
    from app.db import AdminSession
    with AdminSession() as db:
        seq = current_watermark(db)["record_seq"]
    r = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": sid, "obligation_definition_id": S["odef"]["threshold"],
        "valid_at": datetime.now(timezone.utc).isoformat(), "left_seq": seq, "right_seq": seq})
    assert r.status_code == 200 and r.json()["left"]["error"]["error"] == "RUL_TYPE_MISMATCH", r.text
    did = _create(client, _body())
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 200 and r.json()["execution_errors"] >= 1, r.text


def test_replay_reads_a_stored_non_finite_value_from_the_record_never_a_current_one(client, world, monkeypatch):
    """An evaluation that never compared the stored value replays REPRODUCED from that very revision; one that did
    compare it (an EQ, under the evaluator as it was) is NOT_REPRODUCED with RUL_TYPE_MISMATCH. Both stay readable
    and nothing is written."""
    from app.engines import rules
    _legacy_writes(monkeypatch)
    unread = _subject(client, "replay-unread", eu=False)
    compared = _subject(client, "replay-compared", eu=True)
    for sid in (unread, compared):
        assert _fact(client, sid, TURN, "NaN").status_code == 200
    monkeypatch.undo()
    ev_unread = _evaluate(client, unread).json()
    monkeypatch.setattr(rules, "_decimal", lambda v: Decimal(str(v)))  # the evaluator as it was: NaN = 0.00 is FALSE
    ev_compared = _evaluate(client, compared, rule="zero").json()
    monkeypatch.undo()
    assert ev_compared["outcome"] == "DOES_NOT_APPLY"
    before = db_fingerprint()
    r = client.get(f"/api/applicability/evaluations/{ev_unread['evaluation_id']}", headers=H(CCO))
    assert r.status_code == 200, r.text
    rep = r.json()["replay"]
    assert rep["status"] == "REPRODUCED" and rep["matches"], rep
    r = client.get(f"/api/applicability/evaluations/{ev_compared['evaluation_id']}", headers=H(CCO))
    assert r.status_code == 200, r.text
    rep = r.json()["replay"]
    assert rep["status"] == "NOT_REPRODUCED" and rep["error"] == "RUL_TYPE_MISMATCH" and not rep["matches"], rep
    assert "OUTCOME" in rep["failed_checks"]
    assert db_fingerprint() == before
