"""DECIMAL precision-loss guard and canonicalized-AST golden-vector verification.

Owner decisions (RUL owner and Head of Assurance, recorded as approved):
  1. DECIMAL precision-loss guard: a DECIMAL literal whose exact value canonicalization would change is refused, at
     validation and every later gate (review, approval, shadow, publication), with RUL_DECIMAL_CANONICALIZATION_LOSS.
     A value canonicalization keeps ("50000000.00" -> "5E+7") is not refused (exponent-form output: out of scope).
  2. Publication golden vectors run on the exact canonicalized AST that is stored, hashed and signed.
  4. Historical releases, hashes, manifests and their verification are unchanged; the canonicalizer is unchanged.
Controlling text: ZA-RUL-001 §03 ("Exact decimal arithmetic"), §11 stages 7-10, §12 ("Parse(canonicalize(rule)) must
preserve exact semantics and canonical hash"), §15, RUL-REQ-016/017.

Runs on SQLite and PostgreSQL 17; the concurrency test is PostgreSQL-only.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint
from test_rule_publication_impact import _ctx, _race, _session

TAG = secrets.token_hex(3).upper()
INSTR = f"DCG-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, CCO = f"tenant-dcg-{TAG.lower()}", f"dcg-cco-{TAG.lower()}"
EU = "entity.established_in_eu"
TURN = f"dcg.{TAG.lower()}.turnover"
LOSSY = "1234567890123456789012345678.1234567890"  # 38 significant digits: storable (numeric(38,10)); rounded to 28
S: dict = {}

FD = {"t": {"data_type": "DECIMAL", "enum_domain": None, "status": "RELEASED"},
      "s": {"data_type": "SET<DECIMAL>", "enum_domain": None, "status": "RELEASED"},
      "f": {"data_type": "ENUM", "enum_domain": None, "status": "RELEASED"}}


def _const(value, nid="c", typ="DECIMAL"):
    return {"node_type": "CONST", "node_id": nid, "type": typ, "value": value}


def _pred(op, *args, nid="p"):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "source_mappings": ["s"], "args": list(args)}


def _fact(nid="ft", fact="t"):
    return {"node_type": "FACT_REF", "node_id": nid, "fact": fact}


def _issues(ast) -> list[dict]:
    from app.engines import rules
    return [i for i in rules.validate(ast, FD, {})["issues"] if i["severity"] == "ERROR"]


# ------------------------------------------------------------------------------------------------ the guard (pure)
@pytest.mark.parametrize("value", [LOSSY, "12345678901234567890123456789", "0.12345678901234567890123456789",
                                   "-" + LOSSY], ids=["38-digits", "29-digit-integer", "29-fraction-digits", "negative"])
def test_a_literal_canonicalization_would_change_is_refused(value):
    """These validated and were released rounded (Decimal.normalize() keeps 28 significant digits)."""
    [issue] = [i for i in _issues(_pred("GTE", _fact(), _const(value))) if i["code"] == "RUL_DECIMAL_CANONICALIZATION_LOSS"]
    assert issue["node_id"] == "c" and issue["authored_value"] == value
    from decimal import Decimal
    assert Decimal(issue["canonical_value"]) != Decimal(value)


@pytest.mark.parametrize("value", ["50000000.00", "50000000", "10", "0", "-0", "0.00", "-12.50", "1.0", "0.0000001",
                                   "0.0000000001", "1234567890123456789012345678", "1234567890123456789.123456789",
                                   "0.1234567890"])
def test_values_canonicalization_keeps_are_accepted(value):
    """Guard (passes before and after): every value whose exact value survives canonicalization is accepted, including
    those released in exponent form ("50000000.00" -> "5E+7", "0.0000001" -> "1E-7"; outside this decision)."""
    assert _issues(_pred("GTE", _fact(), _const(value))) == []


def _table_cell_ast(value):
    from app.engines import rules
    ast, issues = rules.compile_decision_table({
        "table_mode": "ANY_MATCH", "decision_table_id": "dt", "source_mappings": ["s"],
        "columns": [{"column_id": "c_t", "fact": "t", "value_type": "DECIMAL", "operator": "GTE"}],
        "rows": [{"row_id": "r1", "cells": {"c_t": value}, "source_mappings": ["s"]}]})
    assert not issues
    return ast


LOCATIONS = {  # (AST builder, node expected to be refused or None)
    "between_lower": (lambda v: _pred("BETWEEN_INCL", _fact(), _const(v, "lo"), _const("9" * 28, "hi")), "lo"),
    "between_upper": (lambda v: _pred("BETWEEN_INCL", _fact(), _const("0", "lo"), _const(v, "hi")), "hi"),
    "constant_compared_with_constant": (lambda v: _pred("EQ", _const(v, "left"), _const("1", "right")), "left"),
    "compiled_decision_table_cell": (_table_cell_ast, "row_r1_c_t_val"),
    "inline_set_member": (lambda v: _pred("IN", _fact(), _const([v, "1"], "set", "SET<DECIMAL>")), None),
}


@pytest.mark.parametrize("where", list(LOCATIONS))
def test_every_executed_decimal_constant_is_checked(where):
    """Every place a DECIMAL constant is evaluated. Inline SET<DECIMAL> members are not normalized by canonicalize, so they
    cannot lose precision there and are not refused (their canonical form keeps the member exactly)."""
    from app.engines import rules
    build, node = LOCATIONS[where]
    ast = build(LOSSY)
    flagged = [i["node_id"] for i in _issues(ast) if i["code"] == "RUL_DECIMAL_CANONICALIZATION_LOSS"]
    if node is None:
        assert flagged == [] and LOSSY in rules.canonicalize(ast)["args"][1]["value"]
    else:
        assert flagged == [node]


@pytest.mark.parametrize("value", ["NaN", "1E+3", "abc", 5], ids=["nan", "exponent", "text", "number"])
def test_an_invalid_decimal_literal_stays_a_type_error(value):
    """Guard: an invalid literal is RUL_TYPE_MISMATCH as before, never reported as a canonicalization loss or raised."""
    codes = {i["code"] for i in _issues(_pred("GTE", _fact(), _const(value)))}
    assert "RUL_TYPE_MISMATCH" in codes and "RUL_DECIMAL_CANONICALIZATION_LOSS" not in codes


def test_the_canonicalizer_and_its_hashes_are_unchanged():
    """Guard: canonical forms and the AST hash below were computed on the implementation before this change."""
    from app.engines import rules
    values = ["50000000.00", "1.00", "-12.50", "0.0000001", LOSSY]
    assert [rules.canonicalize(_const(v))["value"] for v in values] == ["5E+7", "1", "-12.5", "1E-7",
                                                                        "1234567890123456789012345678"]
    ast = {"node_type": "ALL", "node_id": "r", "source_mappings": ["s"], "children": [
        {"node_type": "PREDICATE", "node_id": f"p{i}", "operator": "GTE", "args": [
            {"node_type": "FACT_REF", "node_id": f"p{i}f", "fact": "t"},
            {"node_type": "CONST", "node_id": f"p{i}c", "type": "DECIMAL", "value": v}]} for i, v in enumerate(values)]}
    assert rules.ast_hash(ast, {}) == "a645e19082db328e05076f457eb9dcc3cebe4f9e7fc2792078656b7db402947b"


# ------------------------------------------------------------------------------------------------ the API lifecycle
@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Decimal guard Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TURN, label="Annual turnover", data_type="DECIMAL", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="DCG CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Decimal guard Act",
        "locator": f"OJ L {TAG}/dcg", "instrument_identifier": INSTR, "raw_text": "Large institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"DCG-{TAG}-REQ",
        "title": "Reporting requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "report",
             "regulated_object": "report", "timing": "annual", "normalized_meaning": "Report annually.",
             "evidence_expectation": "Report"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200
    return True


def _odef() -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"DCG_{TAG}_{secrets.token_hex(2)}", title="Decimal guard obligation",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef: str, threshold: str, above: str, below: str) -> dict:
    """ALL(established in the EU, turnover >= threshold); vectors at `above` (APPLIES) and `below` (DOES_NOT_APPLY)."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_t", "operator": "GTE", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_tf", "fact": TURN},
            {"node_type": "CONST", "node_id": f"{t}_tc", "type": "DECIMAL", "value": threshold}]}]}

    def vec(vid, cls, eu, turn, out):
        return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2026-07-01",
                "facts": {EU: eu, TURN: turn}, "expected_outcome": out}
    return {"obligation_definition_id": odef, "authoring_mode": "AST", "ast": ast, "valid_from": "2024-01-01",
            "change_class": "EXECUTABLE_OUTCOME",
            "golden_vectors": [vec("P", "POSITIVE", True, above, "APPLIES"), vec("N", "NEGATIVE", True, below, "DOES_NOT_APPLY"),
                               vec("U", "UNKNOWN", {"unknown": "MISSING_REQUIRED_FACT"}, above, "UNCERTAIN")],
            "source_mappings": [{"source_mapping_id": f"sm_{t}", "requirement_version_id": S["rv"]["requirement_version_id"],
                                 "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                                 "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT",
                                 "reviewer": LEGAL}]}


def _draft(client, body) -> str:
    r = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def _shadowed(client, body) -> str:
    did = _draft(client, body)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"dcg-{secrets.token_hex(4)}", "changelog": "x"})


def _set_draft(did, **fields):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        for k, v in fields.items():
            setattr(d, k, v)
        db.commit()


def _records() -> tuple:
    from app.db import AdminSession
    from app.models import AuditEvent, OutboxEvent, RecordSequence
    with AdminSession() as db:
        return (db.execute(select(func.count()).select_from(AuditEvent)).scalar_one(),
                db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one(),
                [r.current_seq for r in db.execute(select(RecordSequence)).scalars()])


def _capture_vector_asts(monkeypatch) -> list:
    from app.engines import rules
    seen, real = [], rules.run_golden_vectors

    def capturing(ast, *a, **k):
        seen.append(ast)
        return real(ast, *a, **k)
    monkeypatch.setattr(rules, "run_golden_vectors", capturing)
    return seen


def test_validation_and_review_refuse_a_lossy_literal_with_the_approved_code(client, world):
    """It validated and entered review; now validation reports it and review refuses it with
    RUL_DECIMAL_CANONICALIZATION_LOSS before any vector runs, with no audit, outbox or record-sequence write."""
    did = _draft(client, _body(_odef(), LOSSY, "1234567890123456789012345679", "1"))
    rep = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR)).json()
    [issue] = [i for i in rep["issues"] if i["code"] == "RUL_DECIMAL_CANONICALIZATION_LOSS"]
    assert not rep["ok"] and issue["canonical_value"] == "1234567890123456789012345678"
    before = _records()
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECIMAL_CANONICALIZATION_LOSS", r.text
    assert _records() == before


def test_approval_shadow_and_publication_refuse_a_lossy_draft_and_write_nothing(client, world):
    """A draft that got further before the guard existed (set below the API here) is refused at peer and legal approval,
    at shadow and at publication, each with the approved code, and none of them writes anything."""
    did = _draft(client, _body(_odef(), LOSSY, "1234567890123456789012345679", "1"))
    _set_draft(did, status="IN_REVIEW", peer_reviewer_id=PEER)
    before = db_fingerprint()
    for actor, step in ((PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECIMAL_CANONICALIZATION_LOSS", (step, r.text)
    assert db_fingerprint() == before
    _set_draft(did, status="VERIFIED", legal_reviewer_id=LEGAL)
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "RUL_DECIMAL_CANONICALIZATION_LOSS", r.text
    assert db_fingerprint() == before


def test_publication_vectors_run_on_the_exact_canonical_ast_that_is_released(client, world, monkeypatch):
    """Publication ran the vectors on the uncanonicalized AST ("50000000.00") and released the canonical one ("5E+7").
    The AST the vectors run on is now the stored AST, the input of its hash and the one the signed manifest binds."""
    from app.engines import rules
    from app.platform import canonical_json, sha256_hex
    did = _shadowed(client, _body(_odef(), "50000000.00", "60000000", "40000000"))
    seen = _capture_vector_asts(monkeypatch)
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    rv, cr = r.json()["rule_version"], r.json()["content_release"]
    [tested] = seen
    assert tested == rv["canonical_ast"] and tested["children"][1]["args"][1]["value"] == "5E+7"
    [entry] = cr["manifest"]["rule_versions"]
    assert rules.ast_hash(tested, {}) == rv["ast_hash"] == entry["ast_sha256"]
    assert sha256_hex(canonical_json(cr["manifest"])) == cr["manifest_hash"]
    assert cr["manifest"]["golden_vector_result"]["passed"] == cr["manifest"]["golden_vector_result"]["total"] == 3


def test_a_canonicalization_change_the_guard_misses_fails_the_vectors_and_publishes_nothing(client, world, monkeypatch):
    """Defence in depth: with the guard out of the way (as for any change it does not detect), the canonical threshold
    answers vector N differently from the authored one, so publication fails closed on the vectors with no release,
    signature, audit, outbox or record-sequence write. It used to publish: the vectors had passed on the authored AST."""
    from app.engines import rules
    monkeypatch.setattr(rules, "decimal_canonicalization_loss", lambda *a: None, raising=False)
    did = _shadowed(client, _body(_odef(), LOSSY, "1234567890123456789012345679", "1234567890123456789012345678.1"))
    before = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and r.json()["detail"] == "Mandatory golden-vector failure blocks publication", r.text
    assert db_fingerprint() == before


def test_a_release_published_before_the_guard_still_verifies_and_evaluates(client, world, monkeypatch):
    """Guard (passes before and after; Decision 4): a release whose literal was already rounded (published here with the
    guard out of the way and vectors that do not reach the threshold) is left exactly as stored: release verification is
    INTACT and its rule still evaluates, because historical verification only re-hashes the stored canonical AST."""
    from app.db import CELL_ID, AdminSession
    from app.engines import rules
    from app.models import LegalEntity, Subject
    odef = _odef()
    monkeypatch.setattr(rules, "decimal_canonicalization_loss", lambda *a: None, raising=False)
    did = _shadowed(client, _body(odef, LOSSY, "1234567890123456789012345679", "1"))
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    rv, cr = r.json()["rule_version"], r.json()["content_release"]
    assert rv["canonical_ast"]["children"][1]["args"][1]["value"] == "1234567890123456789012345678"  # stored as it was
    v = client.get(f"/api/catalog/releases/{cr['content_release_id']}/verify", headers=H(AUTHOR))
    assert v.status_code == 200 and v.json()["integrity"] == "INTACT", v.text
    sid = f"s-dcg-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TA, legal_name="Legacy Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY", display_name="Legacy"))
        db.commit()
    for key, value in ((EU, True), (TURN, "2")):
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value,
            "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}).status_code == 200
    ev = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": odef})
    assert ev.status_code == 200 and ev.json()["outcome"] == "DOES_NOT_APPLY", ev.text


# ------------------------------------------------------------------------------------------------ concurrency (PostgreSQL 17)
def test_an_edit_cannot_replace_the_tested_ast_before_release(client, world, monkeypatch):
    """Publication holds the draft row from before validation to commit. An edit arriving while publication is held right
    after its vectors ran waits, then is refused (the draft is published), and the released AST is the one the vectors
    tested."""
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    from app.db import AdminSession
    from app.engines import rules
    from app.models import RuleVersion
    from app.routers.rules import DraftIn, PublishIn, publish, update_draft
    body = _body(_odef(), "50000000.00", "60000000", "40000000")
    did = _shadowed(client, body)
    seen = _capture_vector_asts(monkeypatch)

    def publish_direct():
        with _session(None, RELEASE) as db:
            return publish(did, PublishIn(label=f"dcg-{did[:8]}", changelog="race"), _ctx(db, RELEASE))["rule_version"]["version"]

    def edit_direct():
        with _session(None, AUTHOR) as db:
            return update_draft(did, DraftIn(**body), _ctx(db, AUTHOR))["revision"]
    out, waited = _race(monkeypatch, rules, "run_golden_vectors", publish_direct, edit_direct)
    assert out["A"][0] == "ok" and out["B"][0] == 409 and waited, out
    with AdminSession() as db:
        stored = db.execute(select(RuleVersion).where(RuleVersion.draft_id == did)).scalars().one().canonical_ast
    assert seen and seen[-1] == stored
