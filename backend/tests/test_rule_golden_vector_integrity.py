"""Golden-vector input integrity and published-draft immutability.

Controlling text:
  * ZA-RUL-001 §13 GoldenVector fields ("facts: Typed inputs or deliberate UNKNOWN reason"; "expected_outcome: APPLIES /
    DOES_NOT_APPLY / UNCERTAIN"; "trace_assertions: ... node IDs/row IDs and expected TRUE/FALSE/UNKNOWN states";
    "effective_at: Explicit time perspective") and vector pass policy ("ZERO TOLERANCE FOR KNOWN MISMATCH"); §15/RUL-REQ-017
    /020 (a mandatory golden-vector failure blocks publication, fail closed); §18 taxonomy (RUL_SCHEMA_INVALID,
    RUL_TYPE_MISMATCH, RUL_FACT_REF_INVALID, RUL_GOLDEN_FAILURE); §17 POST /rule-drafts/{id}/test "Run sandbox/golden
    vectors"; the evaluator's "no implicit coercion" (rules.py).
  * ZA-RUL-001 §10 ("PUBLISHED: Immutable RuleVersion is part of signed ContentRelease"), §16 ("Modify published rule: No").

A malformed vector is never evaluated and never passes: it is a failed vector carrying its §18 code, so each path answers
in its existing format (submit-review 409 RUL_GOLDEN_FAILURE, /test a failing report, publish 409, sandbox 422).
"""
from __future__ import annotations

import secrets
import threading

import pytest
from sqlalchemy import func, select

from conftest import H
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RGV-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
S: dict = {}
EU, TYPE, COUNT = "entity.established_in_eu", "entity.financial_entity_type", "entity.employee_count"
UNK = {"unknown": "MISSING_REQUIRED_FACT"}


def _pred(nid, fact, op, typ, value):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}


def _vec(vid, cls, facts, expected, **extra):
    return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01", "facts": facts,
            "expected_outcome": expected, **extra}


SHAPES = {  # APPLIES iff established in the EU and a credit institution / and at least 250 employees
    "base": (lambda t: {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        _pred(f"{t}_1", EU, "EQ", "BOOLEAN", True), _pred(f"{t}_2", TYPE, "EQ", "ENUM", "CREDIT_INSTITUTION")]},
        [_vec("P", "POSITIVE", {EU: True, TYPE: "CREDIT_INSTITUTION"}, "APPLIES"),
         _vec("N", "NEGATIVE", {EU: False, TYPE: "CREDIT_INSTITUTION"}, "DOES_NOT_APPLY"),
         _vec("U", "UNKNOWN", {EU: UNK, TYPE: "CREDIT_INSTITUTION"}, "UNCERTAIN")]),
    "headcount": (lambda t: {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        _pred(f"{t}_1", EU, "EQ", "BOOLEAN", True), _pred(f"{t}_2", COUNT, "GTE", "INTEGER", 250)]},
        [_vec("P", "POSITIVE", {EU: True, COUNT: 300}, "APPLIES"),
         _vec("N", "NEGATIVE", {EU: True, COUNT: 10}, "DOES_NOT_APPLY"),
         _vec("U", "UNKNOWN", {EU: True, COUNT: UNK}, "UNCERTAIN")]),
}

# case -> (rule shape, the malformed vector appended to the valid ones, the §18 code it fails with)
MALFORMED = {
    "vector_not_an_object": ("base", "x", "RUL_SCHEMA_INVALID"),
    "null_vector": ("base", None, "RUL_SCHEMA_INVALID"),
    "facts_not_an_object": ("base", _vec("B", "NEGATIVE", [EU, False], "DOES_NOT_APPLY"), "RUL_SCHEMA_INVALID"),
    "expected_outcome_missing": ("base", {k: v for k, v in _vec("B", "NEGATIVE", {EU: False}, "x").items()
                                          if k != "expected_outcome"}, "RUL_SCHEMA_INVALID"),
    "expected_outcome_not_an_outcome": ("base", _vec("B", "NEGATIVE", {EU: False}, "NOT_APPLICABLE"), "RUL_SCHEMA_INVALID"),
    "vector_class_not_a_string": ("base", _vec("B", ["NEGATIVE"], {EU: False}, "DOES_NOT_APPLY"), "RUL_SCHEMA_INVALID"),
    "trace_assertions_not_an_object": ("base", _vec("B", "NEGATIVE", {EU: False}, "DOES_NOT_APPLY",
                                                    trace_assertions=["root"]), "RUL_SCHEMA_INVALID"),
    "effective_at_not_a_date": ("base", dict(_vec("B", "NEGATIVE", {EU: False}, "DOES_NOT_APPLY"), effective_at="soon"),
                                "RUL_SCHEMA_INVALID"),
    "unknown_reason_not_a_string": ("base", _vec("B", "UNKNOWN", {EU: {"unknown": 5}}, "UNCERTAIN"), "RUL_SCHEMA_INVALID"),
    "unknown_fact_key": ("base", _vec("B", "POSITIVE", {EU: True, TYPE: "CREDIT_INSTITUTION", "entity.no_such_fact": 1},
                                      "APPLIES"), "RUL_FACT_REF_INVALID"),
    "boolean_given_as_text": ("base", _vec("B", "NEGATIVE", {EU: "false", TYPE: "CREDIT_INSTITUTION"}, "DOES_NOT_APPLY"),
                              "RUL_TYPE_MISMATCH"),
    "enum_value_outside_domain": ("base", _vec("B", "NEGATIVE", {EU: True, TYPE: "BANK"}, "DOES_NOT_APPLY"),
                                  "RUL_TYPE_MISMATCH"),
    "integer_given_as_text": ("headcount", _vec("B", "POSITIVE", {EU: True, COUNT: "300"}, "APPLIES"), "RUL_TYPE_MISMATCH"),
    "integer_given_as_boolean": ("headcount", _vec("B", "NEGATIVE", {EU: True, COUNT: True}, "DOES_NOT_APPLY"),
                                 "RUL_TYPE_MISMATCH"),
}


def _body(shape="base", extra=(), vectors=None) -> dict:
    build, valid = SHAPES[shape]
    t, p = f"g{secrets.token_hex(3)}", S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": build(t),
            "golden_vectors": vectors if vectors is not None else [*valid, *extra], "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps}


def _create(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _review(client, did: str) -> None:
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)


def _verified(client, body) -> str:
    did = _create(client, body)
    _review(client, did)
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 200
    return did


def _publish(client, did):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"rgv-{secrets.token_hex(4)}", "changelog": "x"})


def _draft_row(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        db.expunge(d)
        return d


def _ledger() -> tuple:
    """Record sequence, audit, outbox, rule versions and releases: what a refused operation must leave unchanged."""
    from app.db import AdminSession
    from app.models import AuditEvent, ContentRelease, OutboxEvent, RuleVersion
    from app.platform import current_watermark
    with AdminSession() as db:
        n = lambda m: db.execute(select(func.count()).select_from(m)).scalar()  # noqa: E731
        return current_watermark(db)["record_seq"], n(AuditEvent), n(OutboxEvent), n(RuleVersion), n(ContentRelease)


def _failed(report: dict, code: str) -> list:
    bad = [r for r in report["results"] if not r["passed"]]
    assert len(bad) == 1 and bad[0]["error"] == code, report
    return bad


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Vector Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Vector Act",
        "locator": f"OJ L {TAG}/rgv", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RGV-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RGV_{TAG}", title="Vector obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    base = _verified(client, _body())
    r = _publish(client, base)
    assert r.status_code == 200, r.text
    S["published"] = base
    return True


# ------------------------------------------------------------------------------------------------ malformed vectors
@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_vector_fails_review_with_its_code_and_writes_no_record(client, world, case):
    shape, bad, code = MALFORMED[case]
    did = _create(client, _body(shape, extra=[bad]))
    before = _ledger()
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "RUL_GOLDEN_FAILURE" and not detail["vectors"]["all_passed"]
    _failed(detail["vectors"], code)
    assert _ledger() == before  # no record sequence, audit, outbox, rule version or release
    d = _draft_row(did)
    assert d.status == "DRAFT" and d.peer_reviewer_id is None and d.legal_reviewer_id is None


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_vector_fails_the_test_run_and_is_never_counted_as_passing(client, world, case):
    shape, bad, code = MALFORMED[case]
    did = _create(client, _body(shape, extra=[bad]))
    r = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["all_passed"] is False and rep["passed"] == rep["total"] - 1 == 3
    _failed(rep, code)


@pytest.mark.parametrize("case", ["facts_not_an_object", "boolean_given_as_text", "integer_given_as_text"])
def test_publication_refuses_a_verified_draft_whose_stored_vectors_are_malformed(client, world, case):
    """A draft verified before vector inputs were checked can hold malformed vectors: publication re-runs them, refuses,
    and writes nothing (no RuleVersion, release, audit, outbox or record sequence)."""
    from app.db import AdminSession
    from app.models import RuleDraft
    shape, bad, _ = MALFORMED[case]
    did = _verified(client, _body(shape))
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        d.golden_vectors = [*d.golden_vectors, bad]
        db.commit()
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and "golden-vector failure" in r.text, r.text
    assert db_fingerprint() == before and _draft_row(did).status == "VERIFIED"


@pytest.mark.parametrize("facts, code", [({EU: True, COUNT: "300"}, "RUL_TYPE_MISMATCH"),
                                         ({EU: True, COUNT: 300, "entity.no_such_fact": 1}, "RUL_FACT_REF_INVALID"),
                                         ({EU: True, COUNT: {"unknown": ["x"]}}, "RUL_SCHEMA_INVALID")])
def test_the_sandbox_refuses_malformed_fixture_facts(client, world, facts, code):
    did = _create(client, _body("headcount"))
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR), json={"facts": facts, "effective_at": "2025-06-01"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == code and code in [i["code"] for i in detail["validation"]["issues"]]
    assert db_fingerprint() == before


def test_valid_vectors_including_unknown_outcomes_behave_as_before(client, world):
    for shape in SHAPES:
        did = _create(client, _body(shape))
        rep = client.post(f"/api/rules/drafts/{did}/test", headers=H(AUTHOR)).json()
        assert rep["all_passed"] and rep["passed"] == 3 and not rep["missing_mandatory_classes"], rep
        assert [r["actual"] for r in rep["results"]] == ["APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"]
        r = client.post(f"/api/rules/drafts/{did}/sandbox", headers=H(AUTHOR),
                        json={"facts": SHAPES[shape][1][2]["facts"], "effective_at": "2025-06-01"})
        assert r.status_code == 200 and r.json()["outcome"] == "UNCERTAIN", r.text
    did = _create(client, _body())
    _review(client, did)
    assert _draft_row(did).status == "VERIFIED"


# ------------------------------------------------------------------------------------------------ published drafts
def _retired(monkeypatch, *, thread: str | None = None):
    """The catalogue as a later validator sees it: a FactDefinition the published rule references is no longer RELEASED,
    so the draft's content no longer validates (RUL_FACT_REF_INVALID)."""
    from app.engines import applicability as appl
    real = appl.load_fact_defs

    def load(db):
        defs = real(db)
        if thread is None or threading.current_thread().name == thread:
            defs = {**defs, TYPE: {**defs[TYPE], "status": "RETIRED"}}
        return defs
    monkeypatch.setattr(appl, "load_fact_defs", load)


def test_a_failing_validation_never_unfreezes_a_published_draft(client, world, monkeypatch):
    did = S["published"]
    row = _draft_row(did)
    _retired(monkeypatch)
    r = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert r.status_code == 200 and r.json()["ok"] is False, r.text
    assert _draft_row(did).status == "PUBLISHED"
    monkeypatch.undo()
    r = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    d = _draft_row(did)
    assert (d.status, d.revision, d.ast, d.golden_vectors, d.shadow_report, d.peer_reviewer_id, d.legal_reviewer_id) == \
        ("PUBLISHED", row.revision, row.ast, row.golden_vectors, row.shadow_report, row.peer_reviewer_id, row.legal_reviewer_id)
    body = {k: getattr(row, k) for k in ("obligation_definition_id", "authoring_mode", "ast", "decision_table",
                                         "source_mappings", "golden_vectors", "change_class")}
    body.update(valid_from=row.valid_from.isoformat(), valid_to=None)
    before = db_fingerprint()
    assert client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=body).status_code == 409
    assert client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR)).status_code == 409
    assert db_fingerprint() == before


def test_validation_of_an_unpublished_draft_keeps_its_workflow(client, world, monkeypatch):
    did = _create(client, _body())
    _retired(monkeypatch)
    r = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert r.status_code == 200 and r.json()["ok"] is False
    assert _draft_row(did).status == "VALIDATION_FAILED"
    monkeypatch.undo()
    assert client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()["ok"] is True
    assert _draft_row(did).status == "DRAFT"
    _review(client, did)
    assert _draft_row(did).status == "VERIFIED"
    assert client.post("/api/rules/drafts/no-such-draft/validate", headers=H(AUTHOR)).status_code == 404


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
@pytest.fixture
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and row locks; exact reason required by the CI gate
    return True


def test_a_validation_racing_the_publication_never_unfreezes_the_published_draft(client, world, pg, monkeypatch):
    """A validation that fails (thread B) while the draft is being published (thread A) waits for the publication and
    then leaves the PUBLISHED draft as it is; unlocked, it overwrote the status after the release committed."""
    from app.engines import applicability as appl
    from app.routers.rules import validate
    from test_rule_publication_impact import _ctx, _publish_direct, _race, _session
    did = _verified(client, _body())
    _retired(monkeypatch, thread="B")

    def validate_direct():
        with _session(None, AUTHOR) as db:
            return validate(did, _ctx(db, AUTHOR))["ok"]
    out, waited = _race(monkeypatch, appl, "lock_decisions", lambda: _publish_direct(did), validate_direct)
    assert out["A"][0] == "ok" and out["B"] == ("ok", False), out
    assert _draft_row(did).status == "PUBLISHED"
    assert waited
