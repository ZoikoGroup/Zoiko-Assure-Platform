"""Semantic rule diff (GET /rules/diff, WF-RUL-03) agrees with the executable artifact the hash identifies.

A RuleVersion stores the canonical AST; a draft stores the authored one. A DECIMAL literal canonicalization respells
("50000000.00" is released as "5E+7", "12.50" as "12.5") made the diff report an executable value change between a
release and a draft whose executable hash is identical: the "changed" node the reviewer saw did not exist in the
executable artifact. ZA-RUL-001 §12: "Pretty-printed authoring JSON is not the signed authority"; §14: semantic diff
reports executable node/value differences, and EDITORIAL_METADATA applies "only if executable artifact/hash unaffected";
RUL-INV-08: behavior is fixed by the canonical AST.

These tests assert consistency with the existing, unchanged canonicalizer and hash only. They define no numeric
equivalence of their own: a node is reported exactly when the form a release carries differs (so whatever the hash
distinguishes, the diff reports). Canonical form, hashes, signatures and stored artifacts are not changed, and the
round-trip refusal of exponent-form literals in authoring (ZA-RUL-001 §03) is untouched (owner decision pending).

Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import func, select

from conftest import H

TAG = secrets.token_hex(3).upper()
INSTR = f"RDX-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
EU = "entity.established_in_eu"
TURN = f"rdx.{TAG.lower()}.turnover"
S: dict = {}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import FactDefinition, Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Rule diff Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(FactDefinition(key=TURN, label="Annual turnover", data_type="DECIMAL", subject_type="LEGAL_ENTITY",
                              sensitivity="CUSTOMER_INTERNAL", manual_attestation_policy="ALLOWED"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Rule diff Act",
        "locator": f"OJ L {TAG}/rdx", "instrument_identifier": INSTR, "raw_text": "Large institutions shall report."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RDX-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"RDX_{TAG}_{secrets.token_hex(2)}", title="Rule diff obligation",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _body(odef: str, threshold, above: str, below: str, op: str = "GTE") -> dict:
    """ALL(established in the EU, turnover <op> threshold); vectors at `above` (APPLIES) and `below` (DOES_NOT_APPLY)."""
    t, p = secrets.token_hex(3), S["rv"]["provisions"][0]
    ast = {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}"], "children": [
        {"node_type": "PREDICATE", "node_id": f"{t}_eu", "operator": "EQ", "args": [
            {"node_type": "FACT_REF", "node_id": f"{t}_euf", "fact": EU},
            {"node_type": "CONST", "node_id": f"{t}_euc", "type": "BOOLEAN", "value": True}]},
        {"node_type": "PREDICATE", "node_id": f"{t}_t", "operator": op, "args": [
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


def _release(client, body) -> dict:
    """Publish `body` through every gate and return the stored RuleVersion as the API serves it."""
    did = _draft(client, body)
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                      json={"label": f"rdx-{secrets.token_hex(4)}", "changelog": "x"})
    assert pub.status_code == 200, pub.text
    rv_id = pub.json()["rule_version"]["rule_version_id"]
    return client.get(f"/api/rules/versions/{rv_id}", headers=H(AUTHOR)).json()


def _threshold_node(ast: dict) -> dict:
    return ast["children"][1]["args"][1]


_KEEP = object()


def _from_release(rv: dict, *, threshold=_KEEP, op: str | None = None) -> dict:
    """A draft body copying the release, with the threshold literal (any value, None included) and/or the operator
    re-authored."""
    ast = json.loads(json.dumps(rv["canonical_ast"]))
    if threshold is not _KEEP:
        _threshold_node(ast)["value"] = threshold
    if op is not None:
        ast["children"][1]["operator"] = op
    return {"obligation_definition_id": rv["obligation_definition_id"], "authoring_mode": "AST", "ast": ast,
            "golden_vectors": rv["golden_vectors"], "source_mappings": rv["source_mappings"], "valid_from": rv["valid_from"],
            "change_class": "EXECUTABLE_OUTCOME"}


def _diff(client, a: str, b: str) -> dict:
    r = client.get(f"/api/rules/diff?a={a}&b={b}", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    return r.json()


def _hash(client, did: str) -> str | None:
    r = client.post(f"/api/rules/drafts/{did}/validate", headers=H(AUTHOR))
    assert r.status_code == 200, r.text
    return r.json()["canonical_ast_hash"]


# ------------------------------------------------------------------------------------------------ the defect
@pytest.mark.parametrize("threshold,above,below", [
    ("50000000.00", "60000000.00", "40000000.00"),   # released as "5E+7"
    ("-50000000.00", "-40000000.00", "-60000000.00"),  # released as "-5E+7"
    ("0.0000001", "0.000001", "0.00000001"),          # released as "1E-7"
    ("12.50", "13", "12"),                            # released as "12.5"
], ids=["exponent", "negative-exponent", "small-exponent", "trailing-zero"])
def test_a_draft_whose_executable_is_the_release_reports_no_change(client, world, threshold, above, below):
    """The re-authored draft is the release's executable (same canonical hash), so the diff reports nothing, in both
    directions. Before: the threshold node was "changed" from the stored canonical spelling to the authored one."""
    rv = _release(client, _body(_odef(), threshold, above, below))
    assert _threshold_node(rv["canonical_ast"])["value"] != threshold  # the release stores a different spelling
    did = _draft(client, _from_release(rv, threshold=threshold))
    assert _hash(client, did) == rv["ast_hash"]  # precondition: the hash says the executables are identical
    for a, b in ((rv["rule_version_id"], did), (did, rv["rule_version_id"])):
        d = _diff(client, a, b)
        assert d["changed"] == [] and d["added"] == [] and d["removed"] == [], d
        assert d["facts_added"] == d["facts_removed"] == []


# ------------------------------------------------------------------------------------------------ guards
def test_a_value_change_is_reported_with_both_values_as_stored(client, world):
    rv = _release(client, _body(_odef(), "50000000.00", "60000000.00", "40000000.00"))
    did = _draft(client, _from_release(rv, threshold="50000001.00"))  # canonicalizes to "50000001"
    assert _hash(client, did) != rv["ast_hash"]
    d = _diff(client, rv["rule_version_id"], did)
    node = _threshold_node(rv["canonical_ast"])["node_id"]
    assert [c["node_id"] for c in d["changed"]] == [node]
    c = d["changed"][0]
    assert c["before"]["value"] == "5E+7" and c["after"]["value"] == "50000001.00"  # output as stored, never rewritten


def test_an_operator_change_is_reported(client, world):
    rv = _release(client, _body(_odef(), "50000000.00", "60000000.00", "40000000.00"))
    did = _draft(client, _from_release(rv, threshold="50000000.00", op="GT"))
    assert _hash(client, did) != rv["ast_hash"]
    d = _diff(client, rv["rule_version_id"], did)
    assert [(c["before"]["operator"], c["after"]["operator"]) for c in d["changed"]] == [("GTE", "GT")]


def test_a_difference_the_executable_hash_sees_is_reported(client, world):
    """Whatever the hash distinguishes, the diff reports: "0" and "-0" are equal numbers but different canonical
    values (and hashes), so the diff does not hide the node behind an equality of its own."""
    rv = _release(client, _body(_odef(), "0", "1", "-1"))
    did = _draft(client, _from_release(rv, threshold="-0"))
    assert _hash(client, did) != rv["ast_hash"]
    d = _diff(client, rv["rule_version_id"], did)
    assert [(c["before"]["value"], c["after"]["value"]) for c in d["changed"]] == [("0", "-0")]


@pytest.mark.parametrize("literal", ["abc", None, {"x": 1}], ids=["text", "null", "object"])
def test_a_literal_the_canonicalizer_cannot_read_is_compared_as_written(client, world, literal):
    """An invalid draft (never releasable: validation refuses it) still diffs: its node is compared as written."""
    rv = _release(client, _body(_odef(), "50000000.00", "60000000.00", "40000000.00"))
    did = _draft(client, _from_release(rv, threshold=literal))
    d = _diff(client, rv["rule_version_id"], did)
    assert [(c["before"].get("value"), c["after"].get("value")) for c in d["changed"]] == [("5E+7", literal)]


def test_releases_and_drafts_compare_in_executable_form(client, world):
    """Version to version (both stored canonical) is unchanged; two drafts spelling one executable differently match."""
    odef = _odef()
    v1 = _release(client, _body(odef, "50000000.00", "60000000.00", "40000000.00"))
    v2 = _release(client, _from_release(v1, threshold="60000000"))
    d = _diff(client, v1["rule_version_id"], v2["rule_version_id"])
    assert [(c["before"]["value"], c["after"]["value"]) for c in d["changed"]] == [("5E+7", "6E+7")]
    same = [_draft(client, _from_release(v2, threshold=t)) for t in ("60000000.00", "60000000")]
    assert _hash(client, same[0]) == _hash(client, same[1]) == v2["ast_hash"]
    assert _diff(client, *same)["changed"] == []


def test_the_diff_writes_nothing_and_leaves_the_release_as_signed(client, world):
    from app.db import AdminSession
    from app.engines import rules
    from app.models import AuditEvent, ContentRelease, OutboxEvent, RecordSequence, RuleVersion
    rv = _release(client, _body(_odef(), "50000000.00", "60000000.00", "40000000.00"))
    did = _draft(client, _from_release(rv, threshold="50000000.00"))

    def state():
        with AdminSession() as db:
            row = db.get(RuleVersion, rv["rule_version_id"])
            cr = db.get(ContentRelease, row.content_release_id)
            return (db.execute(select(func.count()).select_from(AuditEvent)).scalar_one(),
                    db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one(),
                    sorted(r.current_seq for r in db.execute(select(RecordSequence)).scalars()),
                    json.dumps(row.canonical_ast, sort_keys=True), row.ast_hash, cr.manifest_hash, cr.signature)
    before = state()
    for a, b in ((rv["rule_version_id"], did), (did, rv["rule_version_id"])):
        _diff(client, a, b)
    assert state() == before
    assert rules.ast_hash(rv["canonical_ast"], {}) == rv["ast_hash"]  # the stored canonical AST still hashes as signed
    assert _threshold_node(rv["canonical_ast"])["value"] == "5E+7"  # the canonical form is unchanged
