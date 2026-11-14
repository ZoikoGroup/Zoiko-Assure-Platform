"""Rule runtime invariants and shadow coverage integrity.

Controlling text:
  * ZA-RUL-001 §10 draft lifecycle ("PUBLISHED: Immutable RuleVersion is part of signed ContentRelease"; IN_REVIEW
    "edits create new draft revision and invalidate approvals/tests"), §11 validation pipeline, §15 shadow completion and
    fail-closed rules, §16 separation of duties ("Modify published rule: No" for every role), §18 error taxonomy,
    RUL-REQ-013/020.
  * ZA-APP-001 §08 (material missing facts drive fact requests), §10 shadow steps 3-4 ("Compare current released outcome
    vs candidate outcome"; "new required fact counts, execution errors"), APP-REQ-014.
  * ZA-TIM-001 TIM-REQ-006/007 (an older-cutoff evaluation is history, not the current outcome).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RSC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-rsc-{TAG.lower()}"
CCO = f"rsc-cco-{TAG.lower()}"
S: dict = {}
AT = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
EU, TYPE, MICRO = "entity.established_in_eu", "entity.financial_entity_type", "entity.microenterprise"


def _pred(nid, fact, typ, value):
    return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
            "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                     {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}


def _base_ast(t: str) -> dict:  # APPLIES iff established in the EU and a credit institution
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        _pred(f"{t}_1", EU, "BOOLEAN", True), _pred(f"{t}_2", TYPE, "ENUM", "CREDIT_INSTITUTION")]}


def _inverted_ast(t: str) -> dict:  # APPLIES iff NOT established in the EU (a credit institution)
    return {"node_type": "ALL", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        _pred(f"{t}_1", EU, "BOOLEAN", False), _pred(f"{t}_2", TYPE, "ENUM", "CREDIT_INSTITUTION")]}


def _branching_ast(t: str) -> dict:  # ANY(ALL(type = PAYMENT_INSTITUTION, microenterprise), established in the EU)
    return {"node_type": "ANY", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "children": [
        {"node_type": "ALL", "node_id": f"{t}_a", "source_mappings": [f"sm_{t}_a"], "children": [
            _pred(f"{t}_1", TYPE, "ENUM", "PAYMENT_INSTITUTION"), _pred(f"{t}_2", MICRO, "BOOLEAN", True)]},
        _pred(f"{t}_3", EU, "BOOLEAN", True)]}


def _single_ast(t: str) -> dict:  # SINGLE_MATCH over two branches a subject may both meet
    return {"node_type": "ANY", "node_id": f"{t}_root", "source_mappings": [f"sm_{t}_root"], "single_match": True,
            "children": [_pred(f"{t}_1", EU, "BOOLEAN", True), _pred(f"{t}_2", TYPE, "ENUM", "CREDIT_INSTITUTION")]}


def _vec(vid, cls, eu, typ, expected):
    return {"vector_id": vid, "vector_class": cls, "description": vid, "effective_at": "2025-06-01",
            "facts": {EU: eu, TYPE: typ}, "expected_outcome": expected}


UNK = {"unknown": "MISSING_REQUIRED_FACT"}
SHAPES = {
    "base": (_base_ast, ("root", "1", "2"), [_vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
                                             _vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
                                             _vec("U", "UNKNOWN", UNK, "CREDIT_INSTITUTION", "UNCERTAIN")]),
    "inverted": (_inverted_ast, ("root", "1", "2"), [_vec("P", "POSITIVE", False, "CREDIT_INSTITUTION", "APPLIES"),
                                                     _vec("N", "NEGATIVE", True, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
                                                     _vec("U", "UNKNOWN", UNK, "CREDIT_INSTITUTION", "UNCERTAIN")]),
    "branching": (_branching_ast, ("root", "a", "1", "2", "3"),
                  [_vec("P", "POSITIVE", True, "CREDIT_INSTITUTION", "APPLIES"),
                   _vec("N", "NEGATIVE", False, "CREDIT_INSTITUTION", "DOES_NOT_APPLY"),
                   _vec("U", "UNKNOWN", UNK, "CREDIT_INSTITUTION", "UNCERTAIN")]),
    "single": (_single_ast, ("root", "1", "2"), [_vec("P", "POSITIVE", True, "PAYMENT_INSTITUTION", "APPLIES"),
                                                 _vec("N", "NEGATIVE", False, "PAYMENT_INSTITUTION", "DOES_NOT_APPLY"),
                                                 _vec("U", "UNKNOWN", UNK, "PAYMENT_INSTITUTION", "UNCERTAIN")]),
}


def _body(shape: str = "base", *, mutate=None) -> dict:
    build, nodes, vectors = SHAPES[shape]
    t, p = f"r{secrets.token_hex(3)}", S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{t}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in nodes]
    ast = build(t)
    if mutate:
        mutate(ast)
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": ast,
            "golden_vectors": vectors, "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME",
            "source_mappings": maps}


def _review(client, did: str) -> None:
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)


def _reviewed(client, body: dict) -> str:
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    _review(client, did)
    return did


def _shadow(client, did: str) -> dict:
    r = client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE))
    assert r.status_code == 200, r.text
    return r.json()


def _publish(client, did: str):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"rsc-{secrets.token_hex(4)}", "changelog": "x"})


def _draft_row(did):
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        d = db.get(RuleDraft, did)
        db.expunge(d)
        return d


def _delta(after: dict, before: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before) if after.get(k, 0) != before.get(k, 0)}


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Runtime Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="RSC CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Runtime Act",
        "locator": f"OJ L {TAG}/rsc", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RSC-{TAG}-REQ",
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
        o = ObligationDefinition(key=f"RSC_{TAG}", title="Runtime obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    base = _reviewed(client, _body())
    _shadow(client, base)
    r = _publish(client, base)
    assert r.status_code == 200, r.text
    S["base_draft"], S["base_release"] = base, r.json()["content_release"]["content_release_id"]
    return True


def _subject(client, name: str, facts: dict) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    for key, value in facts.items():
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
        assert r.status_code == 200, r.text
    return sid


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _evaluate(client, sid, **extra) -> dict:
    r = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["odef"], "effective_at": AT.isoformat(), **extra})
    assert r.status_code == 200, r.text
    return r.json()


IN_SCOPE = {EU: True, TYPE: "CREDIT_INSTITUTION"}


# ------------------------------------------------------------------------------------------------ draft lifecycle
def test_a_published_draft_cannot_be_submitted_for_review_again(client, world):
    """RUL §10: PUBLISHED is immutable; §16: no role may modify a published rule. Re-submitting a published draft would
    put it back through review and let it publish again: a second RuleVersion from the same draft, released on the
    shadow report taken before the first publication and on the peer approval already recorded on the draft."""
    did = S["base_draft"]
    before, row = db_fingerprint(), _draft_row(did)
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409, r.text
    assert db_fingerprint() == before
    after = _draft_row(did)
    assert (after.status, after.revision, after.shadow_report, after.peer_reviewer_id, after.legal_reviewer_id) == \
        ("PUBLISHED", row.revision, row.shadow_report, row.peer_reviewer_id, row.legal_reviewer_id)


def test_a_published_drafts_shadow_report_and_release_summary_survive_later_publications(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease
    row = _draft_row(S["base_draft"])
    with AdminSession() as db:
        summary = db.get(ContentRelease, S["base_release"]).shadow_summary
    assert summary == row.shadow_report
    later = _reviewed(client, _body())
    _shadow(client, later)
    assert _publish(client, later).status_code == 200
    _evaluate(client, _subject(client, "later", IN_SCOPE))
    with AdminSession() as db:
        assert db.get(ContentRelease, S["base_release"]).shadow_summary == summary
    assert _draft_row(S["base_draft"]).shadow_report == row.shadow_report


@pytest.mark.parametrize("bad", ["unknown_operator", "missing_args", "unknown_node", "null_literal", "prohibited_key"])
def test_an_invalid_ast_is_refused_at_review_with_its_rul_code(client, world, bad):
    """RUL §11 stages 1-4 and the §18 taxonomy: a malformed candidate never enters review (so it can never be shadowed
    or published)."""
    code = {"unknown_operator": "RUL_UNKNOWN_OPERATOR", "missing_args": "RUL_SCHEMA_INVALID",
            "unknown_node": "RUL_UNKNOWN_NODE", "null_literal": "RUL_SCHEMA_INVALID",
            "prohibited_key": "RUL_PROHIBITED_CONSTRUCT"}[bad]

    def corrupt(ast):
        p = ast["children"][0]
        if bad == "unknown_operator":
            p["operator"] = "MATCHES"
        elif bad == "missing_args":
            p["args"] = p["args"][:1]
        elif bad == "unknown_node":
            p["args"][1]["node_type"] = "EXPRESSION"
        elif bad == "null_literal":
            p["args"][1]["value"] = None
        else:
            p["script"] = "1"
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=_body(mutate=corrupt))
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    assert r.status_code == 409 and code in [i["code"] for i in r.json()["detail"]["validation"]["issues"]], r.text
    assert _draft_row(did).status == "VALIDATION_FAILED"
    assert client.post(f"/api/rules/drafts/{did}/shadow", headers=H(RELEASE)).status_code == 409


# ------------------------------------------------------------------------------------------------ shadow coverage
def test_shadow_new_required_fact_counts_are_the_facts_the_candidate_would_request(client, world):
    """APP §10 step 4 "new required fact counts": the material missing facts, the ones an evaluation of the candidate
    would raise fact requests for (APP §08, applicability._fact_requests). A fact the result does not depend on (here
    microenterprise, under a branch already FALSE) is not a required fact."""
    did = _reviewed(client, _body("branching"))
    before = _shadow(client, did)
    _subject(client, "material", {TYPE: "CREDIT_INSTITUTION"})  # established_in_eu and microenterprise unknown
    after = _shadow(client, did)
    assert _delta(after["transition_buckets"], before["transition_buckets"]) == {"NONE->UNCERTAIN": 1}
    assert _delta(after["missing_fact_counts"], before["missing_fact_counts"]) == {EU: 1}


def test_shadow_runs_the_draft_candidate_against_the_latest_knowledge_outcome(client, world):
    """APP §10 step 3: baseline = current released outcome (latest knowledge, TIM-REQ-007); candidate = this draft's
    content. A subject evaluated several times is one target with one result."""
    did = _reviewed(client, _body("inverted"))
    before = _shadow(client, did)
    sid = _subject(client, "candidate", {})
    old = _seq()
    for key, value in IN_SCOPE.items():
        assert client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value,
            "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}).status_code == 200
    assert _evaluate(client, sid)["outcome"] == "APPLIES"
    assert _evaluate(client, sid, record_cutoff_seq=old)["outcome"] == "UNCERTAIN"  # history, recorded last
    after = _shadow(client, did)
    assert after["subjects_evaluated"] - before["subjects_evaluated"] == 1
    assert _delta(after["transition_buckets"], before["transition_buckets"]) == {"APPLIES->DOES_NOT_APPLY": 1}


def test_shadow_execution_errors_are_counted_reconciled_and_block_publication(client, world):
    """RUL §15: a runtime invariant breach is an execution error, never an outcome; it is counted against its target,
    the counts reconcile, and a report carrying one cannot authorise publication."""
    did = _reviewed(client, _body("single"))
    before = _shadow(client, did)
    _subject(client, "breach", IN_SCOPE)
    after = _shadow(client, did)
    assert after["execution_errors"] - before["execution_errors"] == 1
    assert after["transition_buckets"] == before["transition_buckets"]
    for rep in (before, after):
        assert sum(rep["transition_buckets"].values()) + rep["execution_errors"] == rep["subjects_evaluated"]
    fp = db_fingerprint()
    r = _publish(client, did)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SHADOW_IMPACT_INCOMPLETE", r.text
    assert db_fingerprint() == fp

