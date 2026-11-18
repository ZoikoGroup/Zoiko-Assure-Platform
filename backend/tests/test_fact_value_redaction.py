"""A sensitive FactAssertion value is returned by no API response, in any field.

Controlling text:
  * ZA-APP-001 APP-REQ-018 (sensitive fact values redacted according to FactDefinition sensitivity) and §13
    ("Sensitive fact disclosure through trace: ... machine trace can retain assertion IDs without value");
  * ZA-API-001 §25 ("Redaction: Trace/evidence/fact values redact by field classification and viewer role while
    preserving stable reference/reason structure"; "sensitive field release is policy-controlled, not 'return full
    object then hide in UI'");
  * ZA-TIM-001 §22 ("Payload/value redaction/erasure policies apply to historical views").

The redaction policy is the one the application already applied to the generic `value` field and to the evaluation trace
(RESTRICTED_PERSONAL and LEGAL_PRIVILEGED; marker "••• (redacted)"). The defect: the typed value columns of the same
revision (value_text, value_decimal, ...) were returned unredacted beside it. The specifications define neither another
marker nor a viewer role that may see such a value, so no new rule is pinned here: no value-bearing field carries the
value, and everything else, including every non-sensitive response, is exactly as before.
"""
from __future__ import annotations

import ast
import json
import pathlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
TA = f"tenant-fvr-{TAG.lower()}"
CCO = f"fvr-cco-{TAG.lower()}"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
INSTR = f"FVR-{TAG}-REG"
EU = "entity.established_in_eu"  # seeded, CUSTOMER_INTERNAL
REDACTED = "••• (redacted)"
SENSITIVE = ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED")
SENSITIVITIES = ("CUSTOMER_INTERNAL", *SENSITIVE)
# Every column that holds the value itself (stated here, not read from the code under test).
VALUE_COLUMNS = {"value_boolean", "value_integer", "value_decimal", "value_text", "value_date", "value_set"}
NON_VALUE_COLUMNS = {"revision_id", "lineage_id", "tenant_id", "entity_id", "subject_id", "fact_definition_id",
                     "value_type", "provenance_class", "source_ref", "semantic_origin", "valid_from", "valid_to",
                     "recorded_at", "record_from_seq", "record_to_seq", "record_batch_id", "change_kind",
                     "change_reason", "correction_of_revision_id", "actor_id"}
# Every data type a fact write accepts (facts.typed_columns) and the column that stores it.
TYPES = {
    "boolean": ("BOOLEAN", None, "value_boolean"),
    "integer": ("INTEGER", None, "value_integer"),
    "decimal": ("DECIMAL", None, "value_decimal"),
    "date": ("DATE", None, "value_date"),
    "timestamp": ("TIMESTAMP", None, "value_text"),
    "string": ("STRING", None, "value_text"),
    "enum": ("ENUM", [f"ALPHA{i}_{TAG}" for i in range(3)], "value_text"),
    "country": ("COUNTRY", None, "value_text"),
    "jurisdiction": ("JURISDICTION", None, "value_text"),
    "set": ("SET<COUNTRY>", None, "value_set"),
}


def _value(slug: str, sensitivity: str):
    """A value distinct per type and sensitivity, so a redacted one can be searched for in a whole response."""
    i = SENSITIVITIES.index(sensitivity)
    return {"boolean": True, "integer": 987650001 + 1000 * i, "decimal": f"98765.43{i}1", "date": f"198{7 + i}-06-05",
            "timestamp": f"2026-01-0{2 + i}T03:04:05.12345{i}+00:00", "string": f"SECRET-{TAG}-STRING-{i}",
            "enum": f"ALPHA{i}_{TAG}", "country": f"CTRY{i}{TAG}", "jurisdiction": f"JUR{i}-{TAG}",
            "set": [f"SETA{i}{TAG}", f"SETB{i}{TAG}"]}[slug]


S: dict = {}
AGO = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()  # noqa: E731


def _key(slug: str, sensitivity: str) -> str:
    return f"fvr{TAG.lower()}.{slug}.{sensitivity.lower()}"


def _tokens(value) -> list[str]:
    """Text of the value that must not appear anywhere in a redacted response (a boolean is checked by field)."""
    if isinstance(value, bool):
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import FactDefinition, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        for slug, (dtype, dom, _) in TYPES.items():
            for sens in SENSITIVITIES:
                db.add(FactDefinition(key=_key(slug, sens), label=f"{slug} {sens}", data_type=dtype, enum_domain=dom,
                                      subject_type="LEGAL_ENTITY", sensitivity=sens, manual_attestation_policy="ALLOWED"))
        for sens in SENSITIVITIES:
            db.add(FactDefinition(key=_key("carve", sens), label=f"carve {sens}", data_type="STRING",
                                  subject_type="LEGAL_ENTITY", sensitivity=sens, manual_attestation_policy="ALLOWED"))
        db.add(FactDefinition(key=_key("trace", "RESTRICTED_PERSONAL"), label="trace threshold", data_type="INTEGER",
                              subject_type="LEGAL_ENTITY", sensitivity="RESTRICTED_PERSONAL",
                              manual_attestation_policy="ALLOWED"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="FVR CCO", roles=["CCO"])
        for name in ("main", "trace"):
            sid, eid = f"s-fvr-{name}-{TAG.lower()}", f"e-fvr-{name}-{TAG.lower()}"
            db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"FVR {name} Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.flush()
            db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"FVR {name} Ltd"))
            S[name] = sid
        db.commit()
    return True


def _write(client, sid, key, value, *, days=30, **extra):
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO),
                    json={"fact_key": key, "value": value, "valid_from": AGO(days), **extra})
    assert r.status_code == 200, r.text
    return r


def _history(client, sid, key) -> tuple[list[dict], str]:
    r = client.get(f"/api/subjects/{sid}/facts/history", headers=H(CCO))
    assert r.status_code == 200, r.text
    return [row for row in r.json() if row["fact_key"] == key], r.text


def _stored(revision_ids) -> dict:
    """The stored revisions, as the response showed them before this change: every column plus `value`."""
    from app.db import AdminSession
    from app.engines.facts import assertion_value
    from app.models import FactAssertion
    from app.platform import to_dict
    with AdminSession() as db:
        rows = db.execute(select(FactAssertion).where(FactAssertion.revision_id.in_(list(revision_ids)))).scalars().all()
        return {a.revision_id: json.loads(json.dumps({**to_dict(a), "value": assertion_value(a),
                                                      "_stored": {c: to_dict(a)[c] for c in VALUE_COLUMNS}}))
                for a in rows}


def _expected(stored: dict, *, redacted: bool, key: str, label: str) -> dict:
    out = {k: v for k, v in stored.items() if k != "_stored"} | {"fact_key": key, "fact_label": label}
    if redacted:
        out |= {c: None for c in VALUE_COLUMNS} | {"value": REDACTED}
    return out


def _assert_no_value(rows: list[dict], text: str, value):
    for row in rows:
        assert row["value"] == REDACTED, row
        assert {c: row[c] for c in VALUE_COLUMNS} == {c: None for c in VALUE_COLUMNS}, row
    for token in _tokens(value):
        assert token not in text, f"{token!r} recoverable from the response"


# ------------------------------------------------------------------------------------------------ the policy's reach
def test_every_fact_assertion_column_is_classified_and_every_value_column_is_withheld():
    """A column added to the revision table must be classified here first; any value_* column is withheld by the shared
    view without another change, and every column a fact write can fill with the value is one of them."""
    from app.engines import facts
    from app.models import FactAssertion
    columns = {c.key for c in FactAssertion.__table__.columns}
    assert columns == VALUE_COLUMNS | NON_VALUE_COLUMNS, columns ^ (VALUE_COLUMNS | NON_VALUE_COLUMNS)
    assert set(facts.VALUE_COLUMNS) == VALUE_COLUMNS
    for slug, (dtype, dom, column) in TYPES.items():
        written = facts.typed_columns(type("FD", (), {"data_type": dtype, "enum_domain": dom, "key": "k"})(),
                                      _value(slug, "CUSTOMER_INTERNAL"))
        assert {c for c, v in written.items() if v is not None} == {column}, (slug, written)
        assert set(written) <= set(facts.VALUE_COLUMNS), written
    assert facts.REDACTED_SENSITIVITIES == frozenset(SENSITIVE) and facts.REDACTED == REDACTED


def test_fact_assertion_rows_are_read_only_where_responses_use_the_shared_view():
    """Tripwire: FactAssertion rows reach a response only through facts.assertion_view. A module that starts reading
    them fails here until it is checked to serialise them through that view and is added to this list."""
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    importers = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "FactAssertion" for a in node.names):
                importers.add(path.relative_to(app_dir).as_posix())
    assert importers == {"engines/facts.py", "routers/applicability.py"}, importers
    router = (app_dir / "routers" / "applicability.py").read_text(encoding="utf-8")
    fact_row = router[router.index("def _fact_row"):router.index("\n\n\n", router.index("def _fact_row"))]
    assert "assertion_view(" in fact_row and "to_dict(" not in fact_row, fact_row


# ------------------------------------------------------------------------------------------------ history and writes
@pytest.mark.parametrize("sensitivity", SENSITIVITIES)
@pytest.mark.parametrize("slug", list(TYPES))
def test_history_and_write_response_withhold_every_typed_value_of_a_sensitive_fact(client, world, slug, sensitivity):
    """POST /subjects/{id}/facts and GET /subjects/{id}/facts/history: for a sensitive fact `value` and every typed
    value column carry no value (the stored value is unchanged); a non-sensitive revision is returned exactly as before.
    The fields are the same either way."""
    dtype, _, column = TYPES[slug]
    value = _value(slug, sensitivity)
    key, redacted = _key(slug, sensitivity), sensitivity in SENSITIVE
    label = f"{slug} {sensitivity}"
    posted = _write(client, S["main"], key, value)
    revisions = posted.json()["revisions"]
    assert len(revisions) == 1
    history, text = _history(client, S["main"], key)
    assert [h["revision_id"] for h in history] == [revisions[0]["revision_id"]]
    stored = _stored([revisions[0]["revision_id"]])[revisions[0]["revision_id"]]
    assert stored["_stored"][column] is not None  # the value is stored as given; only the response withholds it
    assert history[0] == _expected(stored, redacted=redacted, key=key, label=label)
    assert set(revisions[0]) == set(history[0])  # the write response has the same fields as the history
    if redacted:
        _assert_no_value(revisions, posted.text, value)
        _assert_no_value(history, text, value)
    else:  # as before: the value in `value` and in its typed column
        got = revisions[0]["value"]
        if dtype == "DECIMAL":
            assert Decimal(got) == Decimal(value) and Decimal(revisions[0][column]) == Decimal(value)
        else:
            assert got == (sorted(value) if isinstance(value, list) else value)
            assert revisions[0][column] is not None
        assert history[0]["value"] == stored["value"] and history[0][column] == stored["_stored"][column]


@pytest.mark.parametrize("sensitivity", SENSITIVITIES)
def test_carved_and_withdrawn_slices_of_a_sensitive_fact_stay_redacted(client, world, sensitivity):
    """A later write splits the earlier value's interval: the write response returns the SPLIT clone carrying the EARLIER
    value, and a withdrawal returns the clone of the later one. Neither value is recoverable from either response or
    from the history (closed, split and current slices); a non-sensitive fact still shows both values."""
    key, redacted = _key("carve", sensitivity), sensitivity in SENSITIVE
    first, second = f"FIRST-{TAG}-{sensitivity[:4]}", f"SECOND-{TAG}-{sensitivity[:4]}"
    _write(client, S["main"], key, first, days=30)
    changed = _write(client, S["main"], key, second, days=10, change_kind="PROSPECTIVE_CHANGE", reason="new value")
    kinds = sorted(r["change_kind"] for r in changed.json()["revisions"])
    assert kinds == ["PROSPECTIVE_CHANGE", "SPLIT"]
    withdrawn = _write(client, S["main"], key, None, days=5, change_kind="WITHDRAWAL", reason="withdrawn")
    assert [r["change_kind"] for r in withdrawn.json()["revisions"]] == ["SPLIT"]
    history, text = _history(client, S["main"], key)
    assert len(history) == 4  # first and its split; second and its split after the withdrawal
    stored = _stored(h["revision_id"] for h in history)
    for h in history:
        assert h == _expected(stored[h["revision_id"]], redacted=redacted, key=key, label=f"carve {sensitivity}")
    if redacted:
        for response in (changed, withdrawn):
            _assert_no_value(response.json()["revisions"], response.text, first)
            _assert_no_value(response.json()["revisions"], response.text, second)
        _assert_no_value(history, text, first)
        _assert_no_value(history, text, second)
    else:
        assert {h["value"] for h in history} == {first, second}


# ------------------------------------------------------------------------------------------------ other fact read paths
def test_fact_profile_shows_no_sensitive_resolved_value(client, world):
    """GET /subjects/{id}/facts (unchanged behaviour, now from the shared policy): a resolved sensitive fact shows the
    marker and its revision reference, never the value; a non-sensitive one shows the value."""
    if "profile" not in S:
        for slug in TYPES:
            for sens in SENSITIVITIES:
                key = _key(slug, sens)
                if not _history(client, S["main"], key)[0]:
                    _write(client, S["main"], key, _value(slug, sens))
        S["profile"] = True
    r = client.get(f"/api/subjects/{S['main']}/facts", headers=H(CCO))
    assert r.status_code == 200, r.text
    rows = {f["fact_key"]: f for f in r.json()["facts"]}
    for slug, (dtype, _, _) in TYPES.items():
        for sens in SENSITIVITIES:
            value = _value(slug, sens)
            res = rows[_key(slug, sens)]["resolution"]
            assert res["known"] is True and res["revision_id"], res
            if sens in SENSITIVE:
                assert res["value"] == REDACTED and set(res) - {"value"} == {
                    "known", "revision_id", "provenance_class", "holder_subject_id", "valid_from", "valid_to",
                    "sensitivity", "rejected"}, res
            elif dtype == "DECIMAL":
                assert Decimal(res["value"]) == Decimal(value)
            else:
                assert res["value"] == (sorted(value) if isinstance(value, list) else value)
    for slug, (_, domain, _) in TYPES.items():
        if domain:  # the profile lists the FactDefinition's enum domain (catalogue content): checked by field above
            continue
        for sens in SENSITIVE:
            for token in _tokens(_value(slug, sens)):
                assert token not in r.text, f"{token!r} recoverable from the fact profile"


def _publish_trace_rule(client) -> str:
    """APPLIES iff established in the EU and the RESTRICTED_PERSONAL integer is at least 1000."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    sensitive = _key("trace", "RESTRICTED_PERSONAL")
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Redaction Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Redaction Act",
        "locator": f"OJ L {TAG}/fvr", "instrument_identifier": INSTR, "raw_text": "Large entities shall keep a register."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": INSTR, "requirement_key": f"FVR-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"FVR_{TAG}_TRACE", title="Redaction trace", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"fvr{secrets.token_hex(3)}", rv["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": rv["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": src.json()["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]

    def pred(nid, fact, op, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}

    def vec(vid, cls, amount, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: True, sensitive: amount}, "expected_outcome": expected}
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps,
            "ast": {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
                pred(f"{t}_1", EU, "EQ", "BOOLEAN", True), pred(f"{t}_2", sensitive, "GTE", "INTEGER", 1000)]},
            "golden_vectors": [vec("P", "POSITIVE", 5000, "APPLIES"), vec("N", "NEGATIVE", 10, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"fvr-{secrets.token_hex(4)}", "changelog": "redaction probe"})
    assert r.status_code == 200, r.text
    return odef


def _fact_nodes(node, out):
    if isinstance(node, dict):
        if node.get("node_type") == "FACT_REF":
            out[node["fact"]] = node
        for v in node.values():
            _fact_nodes(v, out)
    elif isinstance(node, list):
        for v in node:
            _fact_nodes(v, out)
    return out


def test_evaluation_trace_and_history_comparison_show_no_sensitive_fact_value(client, world):
    """The evaluation trace (POST /applicability/evaluations, GET /applicability/evaluations/{id}) and the bitemporal
    comparison (GET /history/compare) keep the fact's revision reference and show the marker, never the value; the
    non-sensitive fact beside it is shown as before."""
    odef = _publish_trace_rule(client)
    sensitive, amount = _key("trace", "RESTRICTED_PERSONAL"), 987650777
    _write(client, S["trace"], EU, True)
    _write(client, S["trace"], sensitive, amount)
    r = client.post("/api/applicability/evaluations", headers=H(CCO),
                    json={"subject_id": S["trace"], "obligation_definition_id": odef})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["outcome"] == "APPLIES"
    got = client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(CCO))
    assert got.status_code == 200, got.text
    cmp = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": S["trace"], "obligation_definition_id": odef, "valid_at": datetime.now(timezone.utc).isoformat(),
        "left_seq": ev["record_cutoff_seq"], "right_seq": ev["record_cutoff_seq"]})
    assert cmp.status_code == 200, cmp.text
    traces = [r.json()["trace"], got.json()["trace"], cmp.json()["left"]["trace"], cmp.json()["right"]["trace"]]
    for trace in traces:
        nodes = _fact_nodes(trace, {})
        assert nodes[sensitive]["display_value"] == REDACTED and nodes[sensitive]["fact_revision_id"], nodes
        assert nodes[EU]["display_value"] is True, nodes
    for response in (r, got, cmp):
        assert str(amount) not in response.text, "the sensitive value is recoverable from the evaluation response"


# ------------------------------------------------------------------------------------------- evaluation input bundle
# The frozen EvaluationInputBundle (ZA-APP-001 §03) records, per consumed fact, typed_value_hash = digest({"v": value}):
# an unkeyed digest, so for a low-entropy value hashing each candidate finds it (the risk ZA-EVD-001 §08 names: "an
# unsalted/plain SHA-256 of plaintext could enable content guessing"). For a redacted sensitivity a response withholds
# it (None) and keeps the entry's reference: fact definition, assertion revision and provenance (APP-REQ-018, APP §13
# "machine trace can retain assertion IDs without value", API-001 §25). Stored records, their hashes and replay are as
# before, and so is every entry of a non-redacted fact.
BUNDLE_INSTR = f"FVR-{TAG}-BUN"
LOW_ENTROPY = ("boolean", "enum")
CANDIDATES = {"boolean": [True, False], "enum": TYPES["enum"][1]}
# A value per fact (both boolean values and every enum member appear), and every candidate an observer could try.
BUNDLE_VALUES = {**{_key("boolean", s): v for s, v in zip(SENSITIVITIES, (True, False, True))},
                 **{_key("enum", s): TYPES["enum"][1][i] for i, s in enumerate(SENSITIVITIES)}}
READER_ROLES = ("CCO", "LEGAL_REVIEWER", "INTERNAL_AUDIT", "EXTERNAL_AUDITOR", "SERVICE")
B: dict = {}


def _slug(key: str) -> str:
    return key.split(".")[1]


def _sensitivity(key: str) -> str:
    return next(s for s in SENSITIVITIES if key.endswith("." + s.lower()))


def _candidate_hashes(key: str) -> list[str]:
    from app.platform import digest
    return [digest({"v": c}) for c in CANDIDATES[_slug(key)]]


def _publish_bundle_rule(client) -> str:
    """ALL(EU established, ANY(EXISTS(f) for every low-entropy fact)): the rule consumes all six facts, so each is in
    every evaluation's frozen bundle whatever the outcome."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=BUNDLE_INSTR, title="Bundle Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Bundle Act",
        "locator": f"OJ L {TAG}/fvr-bundle", "instrument_identifier": BUNDLE_INSTR,
        "raw_text": "Entities shall record the attributes they hold."})
    assert src.status_code == 200, src.text
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": src.json()["source_id"], "instrument_identifier": BUNDLE_INSTR, "requirement_key": f"FVR-{TAG}-BUN",
        "title": "Attribute requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "record",
             "regulated_object": "attributes", "timing": "continuous", "normalized_meaning": "Record attributes.",
             "evidence_expectation": "Record"}]})
    assert rv.status_code in (200, 201), rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == BUNDLE_INSTR)).scalars().one()
        o = ObligationDefinition(key=f"FVR_{TAG}_BUNDLE", title="Bundle redaction", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        odef = o.obligation_definition_id
    t, p = f"fvb{secrets.token_hex(3)}", rv["provisions"][0]
    # ANY short-circuits on its first TRUE child: the RESTRICTED_PERSONAL boolean (corrected in the historical test)
    # comes first so its FACT_REF, and so its revision, is in every trace.
    first = _key("boolean", "RESTRICTED_PERSONAL")
    exists = [{"node_type": "PREDICATE", "node_id": f"{t}_x{i}", "operator": "EXISTS",
               "source_mappings": [f"sm_{t}_x{i}"], "args": [{"node_type": "FACT_REF", "node_id": f"{t}_x{i}f", "fact": k}]}
              for i, k in enumerate(sorted(BUNDLE_VALUES, key=lambda k: k != first))]
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "source_mappings": [f"sm_{t}_eu"],
         "args": [{"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
                  {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "ANY", "node_id": f"{t}_any", "source_mappings": [f"sm_{t}_any"], "children": exists}]}
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": rv["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": src.json()["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "eu", "any", *(f"x{i}" for i in range(len(BUNDLE_VALUES))))]

    def vec(vid, cls, eu, expected):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
                "facts": {EU: eu, **BUNDLE_VALUES}, "expected_outcome": expected}
    body = {"obligation_definition_id": odef, "authoring_mode": "AST", "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps, "ast": ast,
            "golden_vectors": [vec("P", "POSITIVE", True, "APPLIES"), vec("N", "NEGATIVE", False, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, "UNCERTAIN")]}
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    did = r.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                    json={"label": f"fvb-{secrets.token_hex(4)}", "changelog": "bundle redaction probe"})
    assert r.status_code == 200, r.text
    return odef


@pytest.fixture(scope="module")
def bundle(client, world):
    """A subject holding the six low-entropy facts and EU establishment, one evaluation of them, and a reader of every
    class that may read evaluations (plus one that may not)."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    with AdminSession() as db:
        sid, eid = f"s-fvr-bundle-{TAG.lower()}", f"e-fvr-bundle-{TAG.lower()}"
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name="FVR bundle Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY",
                       display_name="FVR bundle Ltd"))
        for role in (*READER_ROLES, "TENANT_SECURITY_ADMIN"):
            add_principal(db, actor_id=f"fvb-{role.lower()[:20]}-{TAG.lower()}", tenant_id=TA,
                          actor_type="SERVICE" if role == "SERVICE" else "HUMAN", display_name=f"FVB {role}",
                          roles=[role])
        db.commit()
    B["sid"], B["odef"] = sid, _publish_bundle_rule(client)
    _write(client, sid, EU, True)
    for key, value in BUNDLE_VALUES.items():
        _write(client, sid, key, value)
    r = client.post("/api/applicability/evaluations", headers=H(CCO),
                    json={"subject_id": sid, "obligation_definition_id": B["odef"]})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "APPLIES"
    B["posted"] = r
    return B


def _reader(role: str) -> str:
    return f"fvb-{role.lower()[:20]}-{TAG.lower()}"


def _stored_evaluation(evaluation_id: str):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        e = db.get(ApplicabilityEvaluation, evaluation_id)
        return json.loads(json.dumps({"input_bundle": e.input_bundle, "input_bundle_hash": e.input_bundle_hash,
                                      "evaluation_key": e.evaluation_key}))


def _assert_bundle_redacted(view: dict, stored: dict, text: str, values: dict | None = None,
                            bundles: list[dict] | None = None):
    """The response bundle is the stored one except that a redacted fact's value hash is None; no candidate value of a
    redacted low-entropy fact hashes to anything in the response. `values`: the fact values the evaluation consumed;
    `bundles`: every input bundle in the response (a list response carries several)."""
    from app.platform import digest
    values = BUNDLE_VALUES if values is None else values
    by_key = {f["fact_definition"]: f for f in view["fact_inputs"]}
    stored_by_key = {f["fact_definition"]: f for f in stored["fact_inputs"]}
    assert set(by_key) == set(stored_by_key) and set(BUNDLE_VALUES) <= set(by_key), by_key
    # The digest is of the value alone, so a non-redacted fact holding the same value (here EU and the CUSTOMER_INTERNAL
    # boolean, both True) legitimately shows the same hash on its own entry; anywhere else it would disclose.
    entries = [e for b in (bundles or [view]) for e in b["fact_inputs"]]
    shown = {e["typed_value_hash"] for e in entries} - {None}
    for key, entry in by_key.items():
        kept = stored_by_key[key]
        assert set(entry) == set(kept), entry  # the same fields, the reference structure kept
        assert entry["fact_assertion_revision_id"] == kept["fact_assertion_revision_id"]
        assert entry["provenance_class"] == kept["provenance_class"]
        if key in values and _sensitivity(key) in SENSITIVE:
            assert entry["typed_value_hash"] is None, entry
            assert kept["typed_value_hash"] == digest({"v": values[key]})  # still stored as recorded
            for h in set(_candidate_hashes(key)) - shown:
                assert h not in text, f"{key}: a candidate value's hash is recoverable from the response"
            assert text.count(kept["typed_value_hash"]) == sum(  # the stored hash only on non-redacted entries
                e["typed_value_hash"] == kept["typed_value_hash"] for e in entries), key
        else:  # a non-redacted fact (EU and the CUSTOMER_INTERNAL ones): as stored, the real value hash
            assert entry == kept, entry
            if key in values:
                assert entry["typed_value_hash"] == digest({"v": values[key]})
    assert {k: v for k, v in view.items() if k != "fact_inputs"} == {k: v for k, v in stored.items() if k != "fact_inputs"}


def test_input_bundle_view_withholds_only_redacted_value_hashes_and_never_modifies_the_stored_bundle():
    """facts.input_bundle_view: a redacted sensitivity, or a definition whose sensitivity is not known, gives None; any
    other entry and every other field is returned as given; the bundle passed in is not modified."""
    import copy
    from app.engines.facts import input_bundle_view
    stored = {"tenant_id": "t", "rule_version_id": "rv", "unknown_facts": [{"fact_definition": "u", "reason_code": "X"}],
              "fact_inputs": [{"fact_definition": f"k.{s}", "fact_assertion_revision_id": f"r-{s}",
                               "typed_value_hash": f"h-{s}", "provenance_class": "P3_CUSTOMER_ATTESTED"}
                              for s in (*SENSITIVITIES, "UNLISTED")]}
    before = copy.deepcopy(stored)
    view = input_bundle_view(stored, {f"k.{s}": s for s in SENSITIVITIES})
    assert stored == before  # the stored bundle is untouched
    assert view is not stored and all(a is not b for a, b in zip(view["fact_inputs"], stored["fact_inputs"]))
    hashes = {f["fact_definition"]: f["typed_value_hash"] for f in view["fact_inputs"]}
    assert hashes == {"k.CUSTOMER_INTERNAL": "h-CUSTOMER_INTERNAL", "k.RESTRICTED_PERSONAL": None,
                      "k.LEGAL_PRIVILEGED": None, "k.UNLISTED": None}
    assert [{k: v for k, v in f.items() if k != "typed_value_hash"} for f in view["fact_inputs"]] == \
        [{k: v for k, v in f.items() if k != "typed_value_hash"} for f in before["fact_inputs"]]
    assert {k: v for k, v in view.items() if k != "fact_inputs"} == {k: v for k, v in before.items() if k != "fact_inputs"}
    for odd in (None, {}, {"fact_inputs": None}, ["not", "a", "bundle"]):  # nothing to redact: returned as given
        assert input_bundle_view(odd, {}) == odd


def test_every_evaluation_response_withholds_the_value_hash_of_a_redacted_fact(client, bundle):
    """POST /applicability/evaluations, GET /applicability/evaluations and GET /applicability/evaluations/{id}: a
    RESTRICTED_PERSONAL or LEGAL_PRIVILEGED boolean/enum fact's value hash is None and no candidate value's hash appears;
    its definition, assertion revision and provenance remain; the CUSTOMER_INTERNAL facts are returned as stored."""
    posted = bundle["posted"]
    eid = posted.json()["evaluation_id"]
    stored = _stored_evaluation(eid)
    got = client.get(f"/api/applicability/evaluations/{eid}", headers=H(CCO))
    listed = client.get("/api/applicability/evaluations", headers=H(CCO), params={"subject_id": bundle["sid"]})
    assert got.status_code == 200 and listed.status_code == 200, (got.text, listed.text)
    row = next(x for x in listed.json() if x["evaluation_id"] == eid)
    for view, text, bundles in ((posted.json(), posted.text, None), (got.json(), got.text, None),
                                (row, listed.text, [x["input_bundle"] for x in listed.json()])):
        _assert_bundle_redacted(view["input_bundle"], stored["input_bundle"], text, bundles=bundles)
        # The whole-bundle hash and the deduplication key stay as recorded (ZA-API-001: the evaluation "Includes ...
        # input bundle hash"); see the reported residual.
        assert view["input_bundle_hash"] == stored["input_bundle_hash"]
        assert view["evaluation_key"] == stored["evaluation_key"]


def test_stored_record_hashes_and_replay_are_unchanged_by_the_redaction(client, bundle):
    """The stored bundle keeps every value hash; it still hashes to input_bundle_hash and to its evaluation_key; reading
    the evaluation (whose replay re-hashes the stored bundle after the view is built) reproduces it and writes nothing."""
    from app.engines.applicability import evaluation_key
    from app.platform import digest
    eid = bundle["posted"].json()["evaluation_id"]
    before = _stored_evaluation(eid)
    got = client.get(f"/api/applicability/evaluations/{eid}", headers=H(CCO))
    assert got.status_code == 200, got.text
    after = _stored_evaluation(eid)
    assert after == before  # nothing written, the stored bundle not altered by building the response
    assert all(f["typed_value_hash"] for f in after["input_bundle"]["fact_inputs"])
    assert digest(after["input_bundle"]) == after["input_bundle_hash"]
    assert evaluation_key(after["input_bundle"]) == after["evaluation_key"]
    replay = got.json()["replay"]
    checks = {c["check"]: c["passed"] for c in replay["checks"]}
    assert replay["status"] == "REPRODUCED" and replay["matches"] is True, replay
    assert checks["STORED_BUNDLE_HASH"] and checks["REDERIVED_BUNDLE"] and checks["OUTCOME"] and checks["TRACE"], checks


def test_historical_evaluation_and_comparison_withhold_the_value_hash(client, bundle):
    """A historical evaluation (an earlier record cutoff, before a correction of the RESTRICTED_PERSONAL boolean) and the
    current one both withhold the redacted hashes and keep each side's exact revision; GET /history/compare between the
    two cutoffs carries no value hash and reports the correction by revision reference."""
    from app.platform import digest
    key = _key("boolean", "RESTRICTED_PERSONAL")
    sid, odef = bundle["sid"], bundle["odef"]
    then = client.get("/api/watermark", headers=H(CCO)).json()["record_seq"]
    _write(client, sid, key, not BUNDLE_VALUES[key], change_kind="CORRECTION", reason="corrected value")
    now = client.get("/api/watermark", headers=H(CCO)).json()["record_seq"]
    sides = {}
    for name, seq, value in (("then", then, BUNDLE_VALUES[key]), ("now", now, not BUNDLE_VALUES[key])):
        r = client.post("/api/applicability/evaluations", headers=H(CCO),
                        json={"subject_id": sid, "obligation_definition_id": odef, "record_cutoff_seq": seq})
        assert r.status_code == 200, r.text
        stored = _stored_evaluation(r.json()["evaluation_id"])
        _assert_bundle_redacted(r.json()["input_bundle"], stored["input_bundle"], r.text, {**BUNDLE_VALUES, key: value})
        entry = next(f for f in r.json()["input_bundle"]["fact_inputs"] if f["fact_definition"] == key)
        kept = next(f for f in stored["input_bundle"]["fact_inputs"] if f["fact_definition"] == key)
        # Each side's stored hash is of the value known at its cutoff; the response withholds both.
        assert entry["typed_value_hash"] is None and kept["typed_value_hash"] == digest({"v": value}), (entry, kept)
        sides[name] = entry["fact_assertion_revision_id"]
    assert sides["then"] != sides["now"]  # each side names its own revision
    cmp = client.get("/api/history/compare", headers=H(CCO), params={
        "subject_id": sid, "obligation_definition_id": odef, "valid_at": datetime.now(timezone.utc).isoformat(),
        "left_seq": then, "right_seq": now})
    assert cmp.status_code == 200, cmp.text
    assert "typed_value_hash" not in cmp.text  # the comparison carries no per-fact value hash at all
    for k in BUNDLE_VALUES:
        for h in _candidate_hashes(k):
            assert h not in cmp.text, f"{k}: a candidate value's hash is recoverable from the comparison"
    changed = [d for d in cmp.json()["differences"] if d.get("fact") == key]
    assert changed == [{"type": "KNOWLEDGE_CORRECTED", "fact": key, "left": sides["then"], "right": sides["now"]}]


@pytest.mark.parametrize("role", READER_ROLES)
def test_no_reader_role_receives_a_redacted_value_hash(client, bundle, role):
    """The specifications name no viewer role that may see a redacted value (facts.REDACTED_SENSITIVITIES), so every
    principal that may read evaluations, workforce and external-auditor read alike, gets the same withheld hash."""
    eid = bundle["posted"].json()["evaluation_id"]
    stored = _stored_evaluation(eid)
    got = client.get(f"/api/applicability/evaluations/{eid}", headers=H(_reader(role)))
    assert got.status_code == 200, (role, got.text)
    _assert_bundle_redacted(got.json()["input_bundle"], stored["input_bundle"], got.text)
    listed = client.get("/api/applicability/evaluations", headers=H(_reader(role)), params={"subject_id": bundle["sid"]})
    assert listed.status_code == 200, (role, listed.text)
    _assert_bundle_redacted(next(x for x in listed.json() if x["evaluation_id"] == eid)["input_bundle"],
                            stored["input_bundle"], listed.text, bundles=[x["input_bundle"] for x in listed.json()])


def test_principals_without_evaluation_read_and_other_organisations_receive_no_bundle(client, bundle):
    """A tenant security admin (no business read) and Zoiko content staff are refused; another organisation's reader
    cannot find the evaluation and does not see it listed. No refusal carries a value hash."""
    eid = bundle["posted"].json()["evaluation_id"]
    refusals = [(client.get(f"/api/applicability/evaluations/{eid}", headers=H(_reader("TENANT_SECURITY_ADMIN"))), 403),
                (client.get("/api/applicability/evaluations", headers=H(_reader("TENANT_SECURITY_ADMIN"))), 403),
                (client.get(f"/api/applicability/evaluations/{eid}", headers=H(AUTHOR)), 403),
                (client.get(f"/api/applicability/evaluations/{eid}", headers=H("actor-cco")), 404)]
    for r, status in refusals:
        assert r.status_code == status, (status, r.status_code, r.text)
    other = client.get("/api/applicability/evaluations", headers=H("actor-cco"), params={"subject_id": bundle["sid"]})
    assert other.status_code == 200 and eid not in other.text, other.text
    for r in [x for x, _ in refusals] + [other]:
        for k in BUNDLE_VALUES:
            for h in _candidate_hashes(k):
                assert h not in r.text


def test_evaluations_of_non_redacted_facts_are_returned_exactly_as_stored(client, world):
    """The seeded Customer Zero evaluations consume no redacted fact: each listed bundle is the stored bundle, field for
    field (the redaction changes nothing for them)."""
    from app.db import AdminSession
    from app.engines.facts import value_redacted
    from app.models import ApplicabilityEvaluation, FactDefinition
    listed = client.get("/api/applicability/evaluations", headers=H("actor-cco"))
    assert listed.status_code == 200, listed.text
    with AdminSession() as db:
        sens = dict(db.execute(select(FactDefinition.key, FactDefinition.sensitivity)).all())
        stored = {e.evaluation_id: json.loads(json.dumps(e.input_bundle)) for e in db.execute(
            select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.evaluation_id.in_(
                [x["evaluation_id"] for x in listed.json()]))).scalars()}
    checked = 0
    for x in listed.json():
        consumed = [f["fact_definition"] for f in stored[x["evaluation_id"]].get("fact_inputs", [])]
        if consumed and not any(value_redacted(sens.get(k)) for k in consumed):
            assert x["input_bundle"] == stored[x["evaluation_id"]], x["evaluation_id"]
            assert all(f["typed_value_hash"] for f in x["input_bundle"]["fact_inputs"])
            checked += 1
    assert checked, "no seeded evaluation of non-redacted facts was found to compare"
