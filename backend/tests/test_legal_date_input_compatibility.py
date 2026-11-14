"""Legal-date and timestamp input contracts: the current behaviour, pinned, so it cannot change by accident.

What this module pins is what the code does today. A pinned behaviour is NOT an approved policy: where the controlled
specifications point elsewhere (ZA-RUL-001 §03 "Date/time ISO 8601 canonical form; no ambiguous local date/time
strings"; ZA-API-001 §03 "Canonical timestamps RFC 3339/ISO 8601 UTC instants with `Z`; legal dates remain
`YYYY-MM-DD`"; ZA-TIM-001 "Source precision is retained where material"), changing it is a TIM/RUL owner decision, and
these tests are expected to be updated together with that decision, never silently. Approved policy referenced here:
"Option U" (rules.legal_date) for effective_at only.

  * Legal-date fields (rule draft and requirement valid_from / valid_to, catalogue ?valid_at=) are parsed by Pydantic
    `date`: a date, a date-time whose local time is midnight in any offset (its WRITTEN date: not converted to UTC, as
    Option U does not apply to them), lowercase `z`, and a UTC-midnight Unix epoch; the basic and week forms, short or
    second-precision offsets and non-midnight times are refused. DATE literals and DATE facts accept the opposite set
    (date forms only, basic and week included).
  * Instants: live evaluation (Pydantic `datetime` -> ensure_utc -> legal_date), golden vectors and the sandbox
    (_effective_at_ok -> legal_date) and TIMESTAMP literals and facts (timestamp_value) accept different spellings; where
    two accept the same spelling they read the same instant and legal date.
  * More than six fractional digits are truncated, never rounded, on every path; equivalent spellings are one runtime
    value and one evaluation; literals keep their written bytes (and hash).
  * Stored instants, frozen bundles, keys and their canonical serialization are pinned; replay and release
    verification hold.

Fixed here (a demonstrable defect, no policy): an instant whose UTC conversion leaves the calendar (year 1 east of UTC,
year 9999 west of it) was accepted and then failed with an unhandled OverflowError: a 500 from live evaluation, history
compare, the fact profile and fact writes, and from the sandbox, whose contract is a refusal "never ... a 500"; and
rules.legal_date, whose contract is that what it cannot read is a RUL_TYPE_MISMATCH. It is now refused (422 /
RUL_SCHEMA_INVALID / RUL_TYPE_MISMATCH). No value that worked before is affected. Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"LDI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-ldi-{TAG.lower()}", f"ldi-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
DAY, AT = f"ldi.{TAG.lower()}.licensed_on", f"ldi.{TAG.lower()}.reported_at"
UTC, EST = timezone.utc, timezone(timedelta(hours=-5))
S: dict = {}


# ------------------------------------------------------------------------------------------------ the parsers (pure)
def _draft_date(v):
    from app.routers.rules import DraftIn
    return DraftIn(obligation_definition_id="o", valid_from=v).valid_from


def _requirement_date(v):
    from app.routers.catalog import RequirementIn
    return RequirementIn(source_id="s", instrument_identifier="i", requirement_key="rk", title="tt", locator="l",
                         valid_from=v, provisions=[{"locator": "l", "duty_bearer": "db", "modality": "MUST",
                                                    "action": "aa", "regulated_object": "ro",
                                                    "normalized_meaning": "nm"}]).valid_from


def _extract_date(v):
    from app.routers.catalog import ExtractIn
    return ExtractIn(instrument_identifier="i", requirement_key="rk", requirement_title="tt", locator="l",
                     valid_from=v).valid_from


def _query_date(v):  # a query parameter (catalogue ?valid_at=) always arrives as text
    return TypeAdapter(date).validate_python(str(v))


DATE_FIELDS = (_draft_date, _requirement_date, _extract_date, _query_date)

# Every spelling a legal-date field accepts today, all read as the WRITTEN date 2027-01-01 (never the UTC date of an
# instant: "2027-01-01T00:00:00+05:00" is 2026-12-31T19:00:00Z).
DATE_FIELD_ACCEPTED = ["2027-01-01", "2027-01-01T00:00:00", "2027-01-01T00:00:00Z", "2027-01-01T00:00:00z",
                       "2027-01-01T00:00:00+05:00", "2027-01-01T00:00:00-05:00", "2027-01-01T00:00:00.000000+05:30",
                       "2027-01-01T00:00Z", "2027-01-01 00:00:00+05:00", 1798761600, "1798761600"]
# ... and what each refuses: a non-midnight local time (even one that is UTC midnight), short and second-precision
# offsets, a non-midnight epoch, the basic and week forms, unpadded or impossible dates, empty text.
DATE_FIELD_REFUSED = ["2027-01-01T05:00:00+05:00", "2027-01-01T10:00:00+05:00", "2027-01-01T00:00:00+05",
                      "2027-01-01T00:00:00+05:00:00", 1798761601, "20270101", "2026-W53-5", "2027-1-1", "2027-02-30", ""]


@pytest.mark.parametrize("v", DATE_FIELD_ACCEPTED)
def test_legal_date_fields_read_these_spellings_as_their_written_date(v):
    assert [f(v) for f in DATE_FIELDS] == [date(2027, 1, 1)] * len(DATE_FIELDS)


@pytest.mark.parametrize("v", DATE_FIELD_REFUSED)
def test_legal_date_fields_refuse_these_spellings(v):
    for f in DATE_FIELDS:
        with pytest.raises(ValidationError):
            f(v)


def test_date_literals_and_date_facts_accept_date_forms_only():
    """DATE literals (rules._literal_ok) and DATE facts (facts.typed_columns, rules._fact_value_ok) use
    date.fromisoformat: the basic and week forms the legal-date fields refuse are accepted, and every date-time the
    fields accept is refused. Two contracts for one calendar date, kept as they are."""
    from app.engines import rules
    fd = {"data_type": "DATE", "enum_domain": None}
    for v in ("2027-01-01", "20270101", "2026-W53-5"):
        assert rules._literal_ok("DATE", v) and rules._fact_value_ok(fd, v), v
        assert rules._coerce("DATE", v) == date(2027, 1, 1)
    for v in DATE_FIELD_ACCEPTED[1:] + ["2027-1-1", "2027-02-30", ""]:
        assert not rules._literal_ok("DATE", v) and not rules._fact_value_ok(fd, v), v


# input, live reading (instant in UTC, or None = refused), golden-vector / sandbox reading (legal date, or None = refused),
# TIMESTAMP literal / fact reading (instant as written, or None = refused). Where two read a value, they agree.
INSTANTS = [
    ("2027-01-01T23:30:00-05:00", "2027-01-02T04:30:00+00:00", date(2027, 1, 2), "2027-01-01T23:30:00-05:00"),
    ("2027-01-01T23:30:00z", "2027-01-01T23:30:00+00:00", None, None),                      # lowercase z: live only
    ("2027-01-01 23:30:00-05:00", "2027-01-02T04:30:00+00:00", date(2027, 1, 2), "2027-01-01T23:30:00-05:00"),  # space
    ("2027-01-01T23:30-05:00", "2027-01-02T04:30:00+00:00", date(2027, 1, 2), "2027-01-01T23:30:00-05:00"),  # no seconds
    ("2027-01-01T23-05:00", None, date(2027, 1, 2), "2027-01-01T23:00:00-05:00"),           # hour only: not live
    ("2027-01-01T23:30:00-05", None, date(2027, 1, 2), "2027-01-01T23:30:00-05:00"),        # short offset: not live
    ("2027-01-01T23:30:00-0500", "2027-01-02T04:30:00+00:00", date(2027, 1, 2), "2027-01-01T23:30:00-05:00"),
    ("2027-01-01T23:30:00-05:00:30", None, date(2027, 1, 2), "2027-01-01T23:30:00-05:00:30"),  # offset seconds: not live
    ("2027-01-01T23:30:00", "2027-01-01T23:30:00+00:00", date(2027, 1, 1), None),           # naive: live reads it as UTC
    ("2027-01-01", "2027-01-01T00:00:00+00:00", date(2027, 1, 1), None),                    # a date: not a TIMESTAMP
    (1798846200, "2027-01-01T23:30:00+00:00", None, None),                                  # Unix epoch: live only
    ("1798846200", "2027-01-01T23:30:00+00:00", None, None),
    (1798846200.5, "2027-01-01T23:30:00.500000+00:00", None, None),
    ("2027-01-01T23:59:59,5Z", "2027-01-01T23:59:59.500000+00:00", date(2027, 1, 1), "2027-01-01T23:59:59.500000+00:00"),
    ("20270101T233000Z", None, date(2027, 1, 1), "2027-01-01T23:30:00+00:00"),              # basic form: not live
    ("2027-01-01T24:00:00Z", None, None, None), ("2027-01-01T23:59:60Z", None, None, None),
    ("2027-13-01T00:00:00Z", None, None, None), ("garbage", None, None, None), ("", None, None, None)]


def _live(v):
    from app.platform import ensure_utc
    from app.routers.applicability import EvalIn
    try:
        return ensure_utc(EvalIn(subject_id="s", obligation_definition_id="o", effective_at=v).effective_at)
    except ValidationError:
        return None


def _vector(v):
    from app.engines import rules
    return rules.legal_date(v) if rules._effective_at_ok(v) else None


def _timestamp(v):
    from app.engines import rules
    t = rules.timestamp_value(v) if isinstance(v, str) else None
    return t.isoformat() if t else None


@pytest.mark.parametrize("v, live, vector, ts", INSTANTS, ids=[repr(r[0]) for r in INSTANTS])
def test_each_instant_parser_accepts_and_reads_each_spelling_as_before(v, live, vector, ts):
    from app.engines import rules
    got = _live(v)
    assert (got.isoformat() if got else None, _vector(v), _timestamp(v)) == (live, vector, ts)
    if got is not None and vector is not None:  # read by both: one legal date (Option U)
        assert rules.legal_date(got) == vector


def test_more_than_six_fractional_digits_are_truncated_never_rounded_on_every_path():
    """23:59:59.9999999 stays on its day: rounding would move it to the next legal date."""
    from app.engines import rules
    for digits in ("999999", "9999999", "999999999"):
        v = f"2027-01-01T23:59:59.{digits}Z"
        assert _live(v) == datetime(2027, 1, 1, 23, 59, 59, 999999, tzinfo=UTC), v
        assert _vector(v) == date(2027, 1, 1) == rules.legal_date(_live(v)), v
        assert rules.timestamp_value(v) == datetime(2027, 1, 1, 23, 59, 59, 999999, tzinfo=UTC), v


def test_truncated_timestamps_are_one_runtime_value_but_literals_keep_their_written_bytes():
    """A TIMESTAMP fact with seven fractional digits EQ a literal with six is TRUE (both truncate to the microsecond),
    while the literal is hashed as written: the two spellings are two rule hashes (canonicalize does not touch
    TIMESTAMP literals; hashes computed on the implementation before this change)."""
    from app.engines import rules

    def ast(lit):
        return {"node_type": "PREDICATE", "node_id": "p", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": "f", "fact": "ts"},
            {"node_type": "CONST", "node_id": "c", "type": "TIMESTAMP", "value": lit}]}
    six, seven = "2027-01-01T23:59:59.999999Z", "2027-01-01T23:59:59.9999999Z"
    out = rules.evaluate(ast(six), {"ts": {"known": True, "value": seven, "revision_id": "r"}}, {"ts": "TIMESTAMP"}, {},
                         {"effective_at": "2027-01-01"})
    assert out["outcome"] == "APPLIES"
    assert rules.canonicalize(ast(seven)) == ast(seven)
    assert (rules.ast_hash(ast(seven), {}), rules.ast_hash(ast(six), {})) == (
        "72bb90a830ec7703e9e1d8459ac4942e467227586ad46c558f6314abf038f92a",
        "98dfc2a345864f4fef632b26b1ec21fb603b078336a8d9589842920523acb530")


def test_stored_instants_bundles_and_keys_serialize_as_before():
    """The canonical form an instant takes in a frozen bundle (UTC, `Z`, six fractional digits or none) and the bundle
    hash and APP §06 key it gives, pinned on the implementation before this change: a change here would rewrite the
    identity of every stored evaluation."""
    from app.engines import applicability as appl
    from app.platform import canonical_json, digest

    def bundle(ea):
        return {"tenant_id": "t", "subject_ref": {"type": "LEGAL_ENTITY", "id": "s"}, "obligation_definition_id": "o",
                "rule_version_id": "r", "rule_ast_hash": "h", "effective_at": ea, "record_cutoff_seq": 41,
                "fact_inputs": [], "unknown_facts": [], "evaluator_build": "za-applicability-evaluator:1.0.0"}
    pins = [
        (datetime(2027, 1, 2, 23, 59, 59, 999999, tzinfo=UTC), '"2027-01-02T23:59:59.999999Z"',
         "128071e7250e69ada7970e5aae34ccac3c0ca11d0a25075b3e49b77e4dd597bd",
         "319a429c73c2ca8bc6a7cfbda475f805d485560367972a25b33426991d43dc10"),
        (datetime(2027, 1, 2, 18, 59, 59, 999999, tzinfo=EST), '"2027-01-02T23:59:59.999999Z"',
         "128071e7250e69ada7970e5aae34ccac3c0ca11d0a25075b3e49b77e4dd597bd",
         "319a429c73c2ca8bc6a7cfbda475f805d485560367972a25b33426991d43dc10"),
        (datetime(2027, 1, 2, 23, 59, 59, 500000, tzinfo=UTC), '"2027-01-02T23:59:59.500000Z"',
         "f526b717cb4f7d9449fa9278c620e29a6431a40dc39bd9a37305ad90e9f48a21",
         "f7857973f68d51ccec48d721b7535c65e0fce23bf75834c7baa67db68ee2b465"),
        (datetime(2027, 1, 2, 23, 59, 59, tzinfo=UTC), '"2027-01-02T23:59:59Z"',
         "d79c0f3b3f7d5422eebf8dfc461d22f49024be63ac429e63f0feeacad7146d2d",
         "c755569ffe6b7e70f0e80113eedf25ff494cccbb16b20a2a67e74a92e43ceafd"),
        (datetime(2027, 1, 2, 23, 59, 59), '"2027-01-02T23:59:59Z"',  # naive: read as UTC
         "d79c0f3b3f7d5422eebf8dfc461d22f49024be63ac429e63f0feeacad7146d2d",
         "c755569ffe6b7e70f0e80113eedf25ff494cccbb16b20a2a67e74a92e43ceafd")]
    for ea, text, bundle_hash, key in pins:
        assert (canonical_json(ea).decode(), digest(bundle(ea)), appl.evaluation_key(bundle(ea))) == \
            (text, bundle_hash, key), ea


# ------------------------------------------------------------------------------------------------ out-of-calendar (defect)
NO_UTC_DATE = ["9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"]


@pytest.mark.parametrize("v", NO_UTC_DATE)
def test_an_instant_with_no_utc_calendar_date_is_refused_not_a_crash(v):
    """Its UTC conversion leaves years 1-9999: legal_date raised OverflowError (its contract: RUL_TYPE_MISMATCH), the
    vector check accepted it so evaluation crashed, and live parsing accepted it."""
    from app.engines import rules
    with pytest.raises(rules.RuleError) as e:
        rules.legal_date(v)
    assert e.value.code == "RUL_TYPE_MISMATCH"
    vec = {"vector_id": "v", "vector_class": "POSITIVE", "effective_at": v, "facts": {}, "expected_outcome": "APPLIES"}
    assert [p["code"] for p in rules.vector_input_problems(vec, {})] == ["RUL_SCHEMA_INVALID"]
    ast = {"node_type": "PREDICATE", "node_id": "p", "operator": "DATE_ON_OR_AFTER", "args": [
        {"node_type": "CONTEXT_REF", "node_id": "x", "key": "effective_at"},
        {"node_type": "CONST", "node_id": "c", "type": "DATE", "value": "2027-01-01"}]}
    vr = rules.run_golden_vectors(ast, [vec], {}, {})
    assert (vr["passed"], vr["results"][0]["error"]) == (0, "RUL_SCHEMA_INVALID")
    assert _live(v) is None


def test_instants_at_the_edges_of_the_calendar_that_have_a_utc_date_are_unchanged():
    """Guard: only an instant with no UTC calendar date is refused."""
    for v, day in (("9999-12-31T23:59:59+05:00", date(9999, 12, 31)), ("9999-12-31T18:59:59-05:00", date(9999, 12, 31)),
                   ("9999-12-31T23:59:59Z", date(9999, 12, 31)), ("0001-01-01T00:00:00Z", date(1, 1, 1)),
                   ("0001-01-01T05:00:00+05:00", date(1, 1, 1)), ("0001-01-01T00:00:00-05:00", date(1, 1, 1))):
        assert _vector(v) == day and _live(v).date() == day, v


# ------------------------------------------------------------------------------------------------ the API
def _context_rule(threshold: str, t: str) -> dict:
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_on", "operator": "DATE_ON_OR_AFTER", "args": [
            {"node_type": "CONTEXT_REF", "node_id": f"{t}_ctx", "key": "effective_at"},
            {"node_type": "CONST", "node_id": f"{t}_d", "type": "DATE", "value": threshold}]}]}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, Tenant
    S["client"] = client
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Legal date input Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=DAY, label="Licensed on", data_type="DATE", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(FactDefinition(key=AT, label="Reporting instant", data_type="TIMESTAMP", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="LDI CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Legal date Act",
        "locator": f"OJ L {TAG}/ldi", "instrument_identifier": INSTR, "raw_text": "Institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    S["rv"] = _requirement(client, f"LDI-{TAG}-REQ", "2024-01-01", None).json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    S["subject"] = _subject("main")
    S["ctx_odef"] = _odef()  # effective date on or after 2027-01-02, in force from 2024-01-01
    S["ctx_draft"], _ = _published(client, S["ctx_odef"], "2027-01-02", "2024-01-01", None, "2027-03-01")
    S["window_odef"] = _odef()  # in force on [2027-01-02, 2027-01-03) only
    _, S["window_pub"] = _published(client, S["window_odef"], "2000-01-01", "2027-01-02", "2027-01-03", "2027-01-02")
    return True


def _requirement(client, key, valid_from, valid_to):
    return client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": key,
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": valid_from, "valid_to": valid_to,
        "provisions": [{"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
                        "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
                        "evidence_expectation": "Report"}]})


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
        o = ObligationDefinition(key=f"LDI_{TAG}_{secrets.token_hex(2)}", title="Legal date obligation",
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
                    json={"label": f"ldi-{secrets.token_hex(4)}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return did, r.json()


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _evaluate(client, odef, ea, cutoff=None, sid=None):
    body = {"subject_id": sid or S["subject"], "obligation_definition_id": odef, "effective_at": ea}
    if cutoff is not None:
        body["record_cutoff_seq"] = cutoff
    return client.post("/api/applicability/evaluations", headers=H(CCO), json=body)


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_requirement_dates_keep_their_written_date_and_the_catalogue_filters_on_it(client, world):
    """A midnight in another offset is stored as the date written, and ?valid_at= is read the same way, over the
    half-open [valid_from, valid_to): the UTC date would put each probe on the other side of a boundary."""
    key = f"LDI-{TAG}-WRITTEN"
    r = _requirement(client, key, "2027-01-01T00:00:00+05:00", "2027-06-01T00:00:00-05:00")
    assert r.status_code == 201, r.text
    assert (r.json()["valid_from"], r.json()["valid_to"]) == ("2027-01-01", "2027-06-01")

    def listed(valid_at):
        x = client.get("/api/catalog/requirements", headers=H(AUTHOR), params={"requirement_key": key, "valid_at": valid_at})
        assert x.status_code == 200, x.text
        return len(x.json()["items"])
    assert [listed(v) for v in ("2027-01-01T00:00:00+05:00", "2026-12-31", "2027-05-31T00:00:00-05:00",
                                "2027-06-01T00:00:00+05:00", "2027-06-01")] == [1, 0, 1, 0, 0]
    assert client.get("/api/catalog/requirements", headers=H(AUTHOR),
                      params={"valid_at": "2027-01-01T05:00:00+05:00"}).status_code == 422


def test_a_rule_draft_keeps_its_written_dates_into_the_signed_release_and_the_binding(client, world):
    """Draft dates given as midnights in other offsets are the written dates in the draft, the RuleVersion, the signed
    manifest and applicability's [valid_from, valid_to) selection; the release verifies."""
    odef = _odef()
    body = _body(odef, "2000-01-01", "2027-01-01T00:00:00+05:00", "2027-02-01T00:00:00-05:00", "2027-01-15")
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200 and (r.json()["valid_from"], r.json()["valid_to"]) == ("2027-01-01", "2027-02-01"), r.text
    _, pub = _published(client, odef, "2000-01-01", "2027-01-01T00:00:00+05:00", "2027-02-01T00:00:00-05:00", "2027-01-15")
    rv, cr = pub["rule_version"], pub["content_release"]
    assert (rv["valid_from"], rv["valid_to"]) == ("2027-01-01", "2027-02-01")
    entry = cr["manifest"]["rule_versions"][0]
    assert (entry["valid_from"], entry["valid_to"]) == ("2027-01-01", "2027-02-01")
    bound = {ea: _evaluate(client, odef, ea).status_code for ea in (
        "2026-12-31T23:59:59.999999Z", "2027-01-01T00:00:00Z", "2027-01-31T23:59:59.999999Z", "2027-02-01T00:00:00Z")}
    assert list(bound.values()) == [409, 200, 200, 409], bound
    v = client.get(f"/api/catalog/releases/{cr['content_release_id']}/verify", headers=H(AUTHOR)).json()
    assert (v["integrity"], v["signature"]["status"]) == ("INTACT", "VALID") and \
        not [c for c in v["checks"] if c["status"] == "FAIL"], v


def test_a_draft_period_is_checked_on_its_written_dates(client, world):
    """valid_to 2027-01-01T00:00:00-05:00 is ten hours after valid_from 2027-01-01T00:00:00+05:00 as instants, but the
    same written date: an empty half-open period, refused."""
    body = _body(_odef(), "2000-01-01", "2027-01-01T00:00:00+05:00", "2027-01-01T00:00:00-05:00", "2027-01-15")
    assert client.post("/api/rules/drafts", headers=H(AUTHOR), json=body).status_code == 422


def test_date_and_timestamp_facts_store_what_their_parsers_accept(client, world):
    """A DATE fact refuses the date-times the legal-date fields accept; a TIMESTAMP fact keeps the value exactly as
    written (no UTC normalization, a short offset included) and refuses lowercase z, naive and epoch values."""
    sid = _subject(f"facts-{secrets.token_hex(2)}")
    assert _fact(sid, DAY, "2027-01-01T00:00:00Z").status_code == 422
    stored = _fact(sid, AT, "2027-01-01T23:30:00-05")
    assert stored.status_code == 200 and stored.json()["revisions"][-1]["value"] == "2027-01-01T23:30:00-05", stored.text
    for v in ("2027-01-01T23:30:00z", "2027-01-01T23:30:00", 1798846200):
        assert _fact(sid, AT, v).status_code == 422, v
    profile = client.get(f"/api/subjects/{sid}/facts", headers=H(CCO)).json()["facts"]
    assert next(f for f in profile if f["fact_key"] == AT)["resolution"]["value"] == "2027-01-01T23:30:00-05"


def test_live_evaluation_reads_lowercase_z_epoch_naive_and_seven_digit_spellings_as_one_instant(client, world):
    """At one record cutoff these spellings of 2027-01-02T04:30:00Z are one frozen bundle and one evaluation, stored at
    the UTC instant, which replays."""
    assert datetime.fromtimestamp(1798864200, UTC) == datetime(2027, 1, 2, 4, 30, tzinfo=UTC)
    cutoff = _seq()
    spellings = ["2027-01-02T04:30:00Z", "2027-01-02T04:30:00z", 1798864200, "1798864200", "2027-01-02T04:30:00",
                 "2027-01-02 04:30:00+00:00", "2027-01-01T23:30-05:00", "2027-01-02T04:30:00.0000001Z"]
    rows = [_evaluate(client, S["ctx_odef"], ea, cutoff) for ea in spellings]
    assert all(r.status_code == 200 for r in rows), [r.text for r in rows]
    assert len({(r.json()["evaluation_id"], r.json()["input_bundle_hash"]) for r in rows}) == 1
    v = client.get(f"/api/applicability/evaluations/{rows[0].json()['evaluation_id']}", headers=H(CCO)).json()
    assert (v["outcome"], v["replay"]["status"], v["input_bundle"]["effective_at"]) == \
        ("APPLIES", "REPRODUCED", "2027-01-02T04:30:00Z")


def test_the_sandbox_accepts_and_refuses_effective_at_as_golden_vectors_do(client, world):
    """Golden-vector syntax, not live syntax: lowercase z and an epoch are refused (an epoch is not text), a short or
    second-precision offset is accepted and read as its UTC date."""
    def sandbox(ea):
        return client.post(f"/api/rules/drafts/{S['ctx_draft']}/sandbox", headers=H(AUTHOR),
                           json={"facts": {EU: True}, "effective_at": ea})
    refused = sandbox("2027-01-02T04:30:00z")
    assert refused.status_code == 422 and refused.json()["detail"]["error"] == "RUL_SCHEMA_INVALID", refused.text
    assert sandbox(1798864200).status_code == 422
    for ea in ("2027-01-01T23:30:00-05", "2027-01-01T23:30:00-05:00:30"):
        r = sandbox(ea)
        assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", (ea, r.text)


def test_a_seven_digit_instant_is_its_truncated_instant_at_a_utc_midnight(client, world):
    """[2027-01-02, 2027-01-03): 2027-01-01T23:59:59.9999999Z is not in force (rounding would make it 2027-01-02); at
    2027-01-02T23:59:59 six, seven and nine digits are one evaluation stored at .999999, which replays, and the history
    compare reads the same instant."""
    assert _evaluate(client, S["window_odef"], "2027-01-01T23:59:59.9999999Z").json()["detail"]["error"] == "RULE_NOT_EFFECTIVE"
    cutoff = _seq()
    rows = [_evaluate(client, S["window_odef"], f"2027-01-02T23:59:59.{d}Z", cutoff)
            for d in ("999999", "9999999", "999999999")]
    assert all(r.status_code == 200 for r in rows) and len({r.json()["evaluation_id"] for r in rows}) == 1
    v = client.get(f"/api/applicability/evaluations/{rows[0].json()['evaluation_id']}", headers=H(CCO)).json()
    assert v["input_bundle"]["effective_at"] == "2027-01-02T23:59:59.999999Z" and v["replay"]["status"] == "REPRODUCED"
    assert _instant(v["effective_at"]) == datetime(2027, 1, 2, 23, 59, 59, 999999, tzinfo=UTC)
    cmp = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["subject"], "obligation_definition_id": S["window_odef"],
        "valid_at": "2027-01-02T23:59:59.9999999Z", "left_seq": cutoff, "right_seq": cutoff}).json()
    assert cmp["left"]["rule_version_id"] == S["window_pub"]["rule_version"]["rule_version_id"]
    assert cmp["left"]["input_bundle_hash"] == v["input_bundle_hash"]


def test_the_api_refuses_an_instant_with_no_utc_calendar_date_instead_of_failing(client, world):
    """Each was accepted and then failed in the UTC conversion (an unhandled OverflowError: a 500)."""
    late, early = NO_UTC_DATE
    assert _evaluate(client, S["ctx_odef"], late).status_code == 422
    assert client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["subject"], "obligation_definition_id": S["ctx_odef"], "valid_at": early, "left_seq": 1,
        "right_seq": 1}).status_code == 422
    assert client.get(f"/api/subjects/{S['subject']}/facts", headers=H(CCO), params={"valid_at": late}).status_code == 422
    assert _fact(S["subject"], EU, True, valid_from=early).status_code == 422
    assert _fact(S["subject"], EU, True, valid_to=late).status_code == 422
    sb = client.post(f"/api/rules/drafts/{S['ctx_draft']}/sandbox", headers=H(AUTHOR),
                     json={"facts": {EU: True}, "effective_at": late})
    assert sb.status_code == 422 and sb.json()["detail"]["error"] == "RUL_SCHEMA_INVALID", sb.text
    # Guard: an edge instant that has a UTC date is still evaluated. Through the history compare, which writes nothing:
    # a STORED instant in the last hours of year 9999 UTC cannot be read back by a PostgreSQL session whose TimeZone is
    # east of UTC (the driver cannot load year 10000), an open platform question this module must not create rows for.
    cutoff = _seq()
    edge = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["subject"], "obligation_definition_id": S["ctx_odef"], "valid_at": "9999-12-31T23:59:59+05:00",
        "left_seq": cutoff, "right_seq": cutoff})
    assert edge.status_code == 200 and edge.json()["left"]["outcome"] == "APPLIES", edge.text
