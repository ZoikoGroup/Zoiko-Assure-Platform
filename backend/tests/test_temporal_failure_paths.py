"""Temporal input failure paths: every invalid date, instant or record-time coordinate is a controlled refusal that
writes nothing, never an unhandled error (a 500) and never a partial write or a misleading event.

Scope: the rules, facts, applicability and catalogue paths (routers/applicability.py, rules.py, catalog.py and the engines
behind them). Accepted inputs, their interpretation (Option U for effective_at, written dates for legal-date fields),
parser syntax, precision and every hash are unchanged; tests/test_legal_date_input_compatibility.py pins them. Passing
these tests does not make the accepted syntax compliant with ZA-RUL-001 §03 / ZA-API-001 §03: that is an owner decision.

Fixed here (each was an unhandled error on a reachable path):
  * a TIMESTAMP fact value is stored as written in value_text, varchar(500); a valid instant longer than that (hundreds
    of fractional digits, all beyond the microsecond the evaluator reads) failed at persistence on PostgreSQL with a 500,
    after the record sequence was taken, while SQLite stored it. Refused before anything is written now (422), as
    typed_columns already refuses an INTEGER or DECIMAL its column cannot hold (API-REQ-033);
  * an evaluation's record_cutoff_seq and the fact profile's known_seq had no lower bound: below the BIGINT range they
    reached the query and SQLite could not bind them (OverflowError, a 500; PostgreSQL answered 409 / 200). Refused (422)
    outside the range of the record_seq column they are compared with; every value inside it is answered as before.
Earlier (tests/test_legal_date_input_compatibility.py): an instant whose UTC conversion leaves years 1-9999. Runs on
SQLite and PostgreSQL 17. No row this module writes holds an instant near year 1 or 9999 (see the platform note there).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"TFP-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-tfp-{TAG.lower()}", f"tfp-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
DAY, AT = f"tfp.{TAG.lower()}.licensed_on", f"tfp.{TAG.lower()}.reported_at"
LATE, EARLY = "9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"  # no UTC calendar date
BELOW_BIGINT = -(2 ** 63) - 1
LONG_TS = "2027-01-01T00:00:00." + "1" * 480 + "Z"  # 501 characters: a valid instant, one over value_text
FIT_TS = "2027-01-01T00:00:00." + "1" * 479 + "Z"   # 500 characters
S: dict = {}


def _validation_error(r, field: str) -> bool:
    """FastAPI's request-validation 422: {"detail": [{"loc": [..., field], "type": ..., "msg": ...}, ...]}."""
    return r.status_code == 422 and isinstance(r.json()["detail"], list) and \
        any(field in [str(x) for x in e["loc"]] for e in r.json()["detail"])


def _coded(r, status: int, code: str) -> bool:
    """A refusal with a stable machine code: {"detail": {"error": code, ...}}."""
    d = r.json().get("detail")
    return r.status_code == status and isinstance(d, dict) and d.get("error") == code


def _refuses_all(cases, send) -> None:
    """Every case is refused as expected, and together they write nothing (every row of every table unchanged)."""
    before = db_fingerprint()
    failures = []
    for case, check in cases:
        r = send(case)
        if not check(r):
            failures.append((case, r.status_code, r.text[:300]))
    assert not failures, failures
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ pure
def test_edge_instants_in_timestamp_values_evaluate_and_validate_without_error():
    """The evaluator never converts a TIMESTAMP to UTC; Python compares and hashes aware date-times at the calendar edges
    without overflow. So every operator, set membership and the decision-table duplicate check answer normally."""
    from app.engines import rules

    def pred(op, lit, lit_type="TIMESTAMP"):
        return {"node_type": "PREDICATE", "node_id": "p", "operator": op, "source_mappings": ["s"], "args": [
            {"node_type": "FACT_REF", "node_id": "f", "fact": "ts"},
            {"node_type": "CONST", "node_id": "c", "type": lit_type, "value": lit}]}
    fd = {"ts": {"data_type": "TIMESTAMP", "enum_domain": None, "status": "RELEASED"}}
    outcomes = {op: rules.evaluate(pred(op, EARLY), {"ts": {"known": True, "value": LATE, "revision_id": "r"}},
                                   {"ts": "TIMESTAMP"}, {}, {"effective_at": "2027-01-01"})["outcome"]
                for op in ("EQ", "NEQ", "LT", "GTE")}
    assert outcomes == {"EQ": "DOES_NOT_APPLY", "NEQ": "APPLIES", "LT": "DOES_NOT_APPLY", "GTE": "APPLIES"}
    member = rules.evaluate(pred("IN", [EARLY, LATE], "SET<TIMESTAMP>"),
                            {"ts": {"known": True, "value": LATE, "revision_id": "r"}}, {"ts": "TIMESTAMP"}, {},
                            {"effective_at": "2027-01-01"})
    assert member["outcome"] == "APPLIES" and rules.validate(pred("IN", [EARLY, LATE], "SET<TIMESTAMP>"), fd, {})["ok"]
    table = {"table_mode": "SINGLE_MATCH_VALIDATED",
             "columns": [{"column_id": "c", "fact": "ts", "operator": "EQ", "value_type": "TIMESTAMP"}],
             "rows": [{"row_id": "r1", "cells": {"c": LATE}, "source_mappings": ["s"]},
                      {"row_id": "r2", "cells": {"c": "9999-12-31T22:59:59-06:00"}, "source_mappings": ["s"]}]}
    _, issues = rules.compile_decision_table(table)  # the same edge instant twice: a duplicate row, not an error
    assert "RUL_DECISION_DUPLICATE_ROW" in {i["code"] for i in issues}


# ------------------------------------------------------------------------------------------------ the API
def _context_rule(threshold: str, t: str) -> dict:
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_on", "operator": "DATE_ON_OR_AFTER", "args": [
            {"node_type": "CONTEXT_REF", "node_id": f"{t}_ctx", "key": "effective_at"},
            {"node_type": "CONST", "node_id": f"{t}_d", "type": "DATE", "value": threshold}]}]}


def _timestamp_rule(t: str) -> dict:
    """ALL(established in the EU, reported at or after 0001-01-01T00:00:00+05:00): an edge TIMESTAMP literal."""
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_at", "operator": "GTE", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_atf", "fact": AT},
            {"node_type": "CONST", "node_id": f"{t}_atc", "type": "TIMESTAMP", "value": EARLY}]}]}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Temporal failure Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=DAY, label="Licensed on", data_type="DATE", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(FactDefinition(key=AT, label="Reporting instant", data_type="TIMESTAMP", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="TFP CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Temporal Act",
        "locator": f"OJ L {TAG}/tfp", "instrument_identifier": INSTR, "raw_text": "Institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json=_requirement_body(f"TFP-{TAG}-REQ", "2024-01-01"))
    assert r.status_code == 201, r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["subject"] = _subject("main")
    S["odef"] = _odef()
    S["draft"], S["pub"] = _published(client, S["odef"], "2024-01-01")
    S["ts_draft"] = _draft(client, _body(_odef(), "2024-01-01", ast=_timestamp_rule(secrets.token_hex(3)),
                                         facts={AT: "2027-06-01T00:00:00Z"}))
    return True


def _requirement_body(key, valid_from, valid_to=None):
    return {"source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
            "title": "Reporting requirement", "locator": "Art. 1", "valid_from": valid_from, "valid_to": valid_to,
            "provisions": [{"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST",
                            "action": "report", "regulated_object": "report", "timing": "annual",
                            "normalized_meaning": "Report annually.", "evidence_expectation": "Report"}]}


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
    assert _fact(sid, EU, True).status_code == 200
    return sid


def _fact(sid, key, value, valid_from="2024-01-01T00:00:00Z", **extra):
    return S["client"].post(f"/api/subjects/{sid}/facts", headers=H(CCO),
                            json={"fact_key": key, "value": value, "valid_from": valid_from, **extra})


def _odef() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"TFP_{TAG}_{secrets.token_hex(2)}", title="Temporal obligation",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef, valid_from, valid_to=None, ast=None, vector_at="2027-03-01", facts=None, vectors=None) -> dict:
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]
    ast = ast or _context_rule("2027-01-02", t)
    t = ast["node_id"].split("_")[0]

    def vec(vid, cls, eu, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": vector_at,
                "facts": {EU: eu, **(facts or {})}, "expected_outcome": out}
    return {"obligation_definition_id": odef, "authoring_mode": "AST", "ast": ast, "valid_from": valid_from,
            "valid_to": valid_to, "change_class": "EXECUTABLE_OUTCOME",
            "golden_vectors": vectors if vectors is not None else [
                vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")],
            "source_mappings": [{"source_mapping_id": f"sm_{t}", "requirement_version_id": S["rv"]["requirement_version_id"],
                                 "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                                 "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT",
                                 "reviewer": LEGAL}]}


def _draft(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _published(client, odef, valid_from):
    did = _draft(client, _body(odef, valid_from))
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"tfp-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return did, r.json()


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def test_live_evaluation_refuses_invalid_instants_and_cutoffs_and_writes_nothing(client, world):
    def send(body):
        return client.post("/api/applicability/evaluations", headers=H(CCO), json={
            "subject_id": S["subject"], "obligation_definition_id": S["odef"], **body})
    invalid_at = [LATE, EARLY, "2027-01-01T00:00:00+24:00", "2027-01-01T00:00:00+05", "2027-13-01T00:00:00Z",
                  "2027-01-01T23:59:60Z", "2027-01-01T00:00:00Z\x00", "garbage", "", True, [], {}, 1e20]
    cases = [({"effective_at": v}, lambda r: _validation_error(r, "effective_at")) for v in invalid_at]
    cases += [({"record_cutoff_seq": v}, lambda r: _validation_error(r, "record_cutoff_seq"))
              for v in (BELOW_BIGINT, 1.5, "abc")]  # BELOW_BIGINT: SQLite could not bind it (a 500)
    cases += [({"record_cutoff_seq": 2 ** 80}, lambda r: _coded(r, 422, "RECORD_SEQ_IN_FUTURE")),
              ({"record_cutoff_seq": -(2 ** 63)}, lambda r: _coded(r, 409, "RULE_NOT_EFFECTIVE")),  # in range: as before
              ({"record_cutoff_seq": -1}, lambda r: _coded(r, 409, "RULE_NOT_EFFECTIVE"))]
    _refuses_all(cases, send)


def test_history_compare_refuses_invalid_coordinates_and_writes_nothing(client, world):
    now = _seq()

    def send(params):
        return client.get("/api/history/compare", headers=H(CCO), params={
            "subject_id": S["subject"], "obligation_definition_id": S["odef"], "valid_at": "2027-06-01T00:00:00Z",
            "left_seq": now, "right_seq": now, **params})
    cases = [({"valid_at": v}, lambda r: _validation_error(r, "valid_at"))
             for v in (LATE, EARLY, "2027-01-01T00:00:00+24:00", "2027-13-01T00:00:00Z", "garbage", "")]
    cases += [({"left_seq": -1}, lambda r: _validation_error(r, "left_seq")),
              ({"right_seq": "x"}, lambda r: _validation_error(r, "right_seq")),
              ({"left_seq": now + 1}, lambda r: _coded(r, 422, "RECORD_SEQ_IN_FUTURE")),
              ({"right_seq": 2 ** 80}, lambda r: _coded(r, 422, "RECORD_SEQ_IN_FUTURE"))]
    _refuses_all(cases, send)
    missing = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["subject"], "obligation_definition_id": S["odef"], "left_seq": now, "right_seq": now})
    assert _validation_error(missing, "valid_at")
    # Accepted as before (nothing is written by a compare): the calendar's edge instants that have a UTC date, and a
    # seven-digit fraction.
    for v in ("9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z", "2027-06-01T23:59:59.9999999Z"):
        assert send({"valid_at": v}).status_code == 200, v


def test_the_fact_profile_refuses_invalid_coordinates_and_answers_the_others_as_before(client, world):
    path = f"/api/subjects/{S['subject']}/facts"
    now = _seq()
    cases = [({"valid_at": v}, lambda r: _validation_error(r, "valid_at"))
             for v in (LATE, EARLY, "2027-01-01T00:00:00+24:00", "garbage")]
    cases += [({"known_seq": BELOW_BIGINT}, lambda r: _validation_error(r, "known_seq")),  # SQLite: a 500 before
              ({"known_seq": "x"}, lambda r: _validation_error(r, "known_seq")),
              ({"known_seq": now + 1}, lambda r: _coded(r, 422, "RECORD_SEQ_IN_FUTURE"))]
    _refuses_all(cases, lambda params: client.get(path, headers=H(CCO), params=params))
    # Unchanged answers inside the record_seq range and at the edges that have a UTC date (reads write nothing).
    for params in ({"known_seq": -1}, {"known_seq": -(2 ** 63)}, {"valid_at": "9999-12-31T23:59:59Z"},
                   {"valid_at": "0001-01-01T00:00:00Z"}, {"valid_at": "2027-06-01T23:59:59.9999999Z"}):
        r = client.get(path, headers=H(CCO), params=params)
        assert r.status_code == 200, (params, r.text)
    assert client.get(path, headers=H(CCO), params={"known_seq": -1}).json()["perspective"]["known_seq"] == -1


def test_fact_creation_and_correction_refuse_invalid_temporal_input_and_write_nothing(client, world):
    sid = _subject(f"facts-{secrets.token_hex(2)}")
    send_cases = [
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from=LATE), "valid_from"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from=EARLY), "valid_from"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_to=LATE), "valid_to"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from="2027-01-01T00:00:00+24:00"), "valid_from"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from=True), "valid_from"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from=""), "valid_from"),
        (dict(key=AT, value="2027-01-01T00:00:00Z", change_kind="CORRECTION", reason="r", valid_from=EARLY), "valid_from"),
    ]
    cases = [(c, lambda r, f=f: _validation_error(r, f)) for c, f in send_cases]

    def refused_value(r):  # typed_columns: "Value ... is not a valid <TYPE> for fact <key>"
        return r.status_code == 422 and "is not a valid" in str(r.json()["detail"])
    for key, value in ((AT, "2027-01-01T00:00:00"), (AT, "2027-01-01T00:00:00z"), (AT, "2027-01-01"), (AT, 1798761600),
                       (AT, "2027-01-01T00:00:00+24:00"), (AT, LONG_TS), (DAY, "2027-01-01T00:00:00Z"),
                       (DAY, 20270101), (DAY, "2027-02-30"), (DAY, "")):
        cases.append((dict(key=key, value=value), refused_value))
    cases.append((dict(key=AT, value=LONG_TS, change_kind="CORRECTION", reason="r"), refused_value))

    def empty_range(r):
        return r.status_code == 422 and "valid range" in str(r.json()["detail"])
    cases += [(dict(key=AT, value="2027-01-01T00:00:00Z", valid_from="2027-01-01T00:00:00Z",
                    valid_to="2027-01-01T00:00:00Z"), empty_range),
              (dict(key=AT, value="2027-01-01T00:00:00Z", valid_from="2027-01-02T00:00:00Z",
                    valid_to="2027-01-01T00:00:00Z"), empty_range),
              (dict(key=AT, value="2027-01-01T00:00:00Z", change_kind="CORRECTION"),  # a correction needs a reason
               lambda r: r.status_code == 422 and "change_reason" in str(r.json()["detail"]))]

    def send(c):
        c = dict(c)
        return _fact(sid, c.pop("key"), c.pop("value"), **({"valid_from": c.pop("valid_from")} if "valid_from" in c else {}),
                     **c)
    _refuses_all(cases, send)


def test_fact_writes_accept_the_boundary_values_they_accepted_before(client, world):
    """Guard: value_text holds exactly 500 characters; edge instants and DATEs are stored as written (text and calendar
    dates: no session-timezone conversion on read); a seven-digit validity instant is truncated, not refused."""
    sid = _subject(f"edge-{secrets.token_hex(2)}")
    for key, value in ((AT, FIT_TS), (AT, LATE), (AT, EARLY), (DAY, "9999-12-31"), (DAY, "0001-01-01")):
        r = _fact(sid, key, value)
        assert r.status_code == 200 and r.json()["revisions"][-1]["value"] == value, (key, value[:40], r.text[:300])
    r = _fact(sid, DAY, "2027-01-01", valid_from="2026-01-01T23:59:59.9999999Z")
    assert r.status_code == 200, r.text
    stored = datetime.fromisoformat(r.json()["revisions"][-1]["valid_from"].replace("Z", "+00:00"))
    assert stored == datetime(2026, 1, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_the_sandbox_refuses_invalid_temporal_inputs_and_evaluates_edge_instants(client, world):
    def send(body):
        return client.post(f"/api/rules/drafts/{S['ts_draft']}/sandbox", headers=H(AUTHOR),
                           json={"facts": {EU: True, AT: "2027-06-01T00:00:00Z"}, "effective_at": "2027-06-01", **body})
    cases = [({"effective_at": v}, lambda r: _coded(r, 422, "RUL_SCHEMA_INVALID"))
             for v in (LATE, EARLY, "2027-01-01T00:00:00z", "2027-13-01", "garbage", "")]
    cases += [({"effective_at": 1798761600}, lambda r: _validation_error(r, "effective_at"))]
    cases += [({"facts": {EU: True, AT: v}}, lambda r: _coded(r, 422, "RUL_TYPE_MISMATCH"))
              for v in ("2027-01-01T00:00:00", "2027-01-01", 1798761600, "2027-01-01T00:00:00+24:00")]
    cases += [({"facts": {EU: True, DAY: "2027-01-01T00:00:00Z"}}, lambda r: _coded(r, 422, "RUL_TYPE_MISMATCH"))]
    _refuses_all(cases, send)
    for at in (LATE, EARLY, LONG_TS):  # valid instants at the calendar's edges and past value_text: an outcome
        r = send({"facts": {EU: True, AT: at}})
        assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", (at[:40], r.text[:300])


def test_golden_vectors_with_invalid_temporal_inputs_fail_as_results_and_block_review(client, world):
    """Malformed vectors are failed results with their §18 code (never a 500); submit-review refuses the draft with
    RUL_GOLDEN_FAILURE, keeps it out of review and emits no rule.review_requested."""
    from app.db import AdminSession
    from app.models import OutboxEvent

    def vec(vid, cls, ea, facts, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": ea, "facts": facts,
                "expected_outcome": out}
    vectors = [vec("late", "POSITIVE", LATE, {EU: True, AT: "2027-06-01T00:00:00Z"}, "APPLIES"),
               vec("early", "NEGATIVE", EARLY, {EU: False, AT: "2027-06-01T00:00:00Z"}, "DOES_NOT_APPLY"),
               vec("bad", "UNKNOWN", "2027-13-01", {EU: {"unknown": "MISSING_REQUIRED_FACT"}}, "UNCERTAIN"),
               vec("naive", "POSITIVE", "2027-06-01", {EU: True, AT: "2027-06-01T00:00:00"}, "APPLIES"),
               vec("edge", "POSITIVE", "2027-06-01", {EU: True, AT: LATE}, "APPLIES")]
    did = _draft(client, _body(_odef(), "2024-01-01", ast=_timestamp_rule(secrets.token_hex(3)), vectors=vectors))
    r = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    got = {x["vector_id"]: (x["passed"], x.get("error")) for x in r.json()["results"]}
    assert got == {"late": (False, "RUL_SCHEMA_INVALID"), "early": (False, "RUL_SCHEMA_INVALID"),
                   "bad": (False, "RUL_SCHEMA_INVALID"), "naive": (False, "RUL_TYPE_MISMATCH"), "edge": (True, None)}
    sub = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert _coded(sub, 409, "RUL_GOLDEN_FAILURE"), sub.text
    assert client.get(f"/api/rules/drafts/{did}", headers=H(AUTHOR)).json()["status"] != "IN_REVIEW"
    with AdminSession() as db:
        assert not db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "rule.review_requested",
                                                        OutboxEvent.aggregate_id == did)).scalars().all()


def test_rule_drafts_refuse_invalid_legal_dates_and_write_nothing(client, world):
    odef = _odef()
    did = _draft(client, _body(odef, "2024-01-01"))
    bad = [("2027-02-30", None), ("20270101", None), (True, None), ("", None), (1798761601, None),
           ("2027-01-01T10:00:00+05:00", None), ("2027-01-01", "2027-01-01"), ("2027-01-02", "2027-01-01")]
    cases = []
    for vf, vt in bad:
        cases.append((("POST", vf, vt), lambda r: r.status_code == 422))
        cases.append((("PUT", vf, vt), lambda r: r.status_code == 422))

    def send(c):
        method, vf, vt = c
        body = _body(odef, vf, vt)
        if method == "POST":
            return client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
        return client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=body)
    _refuses_all(cases, send)
    for vf in ("0001-01-01", "9999-12-31"):  # calendar dates at the edges: accepted as before
        r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=_body(odef, vf))
        assert r.status_code == 200 and r.json()["valid_from"] == vf, r.text


def test_the_catalogue_refuses_invalid_dates_and_record_coordinates_and_writes_nothing(client, world):
    cr = S["pub"]["content_release"]["content_release_id"]
    source = {"authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Act",
              "locator": f"OJ L {TAG}/x", "instrument_identifier": INSTR, "raw_text": "Institutions shall report."}
    req = _requirement_body(f"TFP-{TAG}-BAD", "2024-01-01")
    cases = [(("POST", "/api/catalog/sources", {**source, field: v}), lambda r, f=field: _validation_error(r, f))
             for field in ("publication_date", "effective_date") for v in ("2027-02-30", True, "20270101")]
    cases += [(("POST", "/api/catalog/requirements", {**req, "valid_from": v}), lambda r: _validation_error(r, "valid_from"))
              for v in ("2027-02-30", "", "2027-01-01T10:00:00+05:00")]
    cases += [(("POST", "/api/catalog/requirements", {**req, "valid_from": "2027-01-01", "valid_to": vt}),
               lambda r: r.status_code == 422 and "half-open" in str(r.json()["detail"]))
              for vt in ("2027-01-01", "2026-12-31")]
    cases += [(("POST", f"/api/catalog/sources/{S['source']['source_id']}/extract",
                {"instrument_identifier": INSTR, "requirement_key": f"TFP-{TAG}-X", "requirement_title": "Extracted",
                 "locator": "Art. 9", "valid_from": "2027-02-30"}), lambda r: _validation_error(r, "valid_from"))]
    cases += [(("GET", "/api/catalog/requirements", {"valid_at": v}), lambda r: _validation_error(r, "valid_at"))
              for v in ("garbage", "2027-02-30", "2027-01-01T10:00:00+05:00")]
    cases += [(("GET", f"/api/catalog/releases/{cr}/impact", {"as_of_record_seq": -1}),
               lambda r: _validation_error(r, "as_of_record_seq")),
              (("GET", f"/api/catalog/releases/{cr}/impact", {"as_of_record_seq": 2 ** 80}),
               lambda r: _coded(r, 422, "RECORD_SEQ_IN_FUTURE"))]

    def send(c):
        method, path, payload = c
        actor = CCO if "/impact" in path else AUTHOR
        if method == "GET":
            return client.get(path, headers=H(actor), params=payload)
        return client.post(path, headers=H(actor), json=payload)
    _refuses_all(cases, send)
