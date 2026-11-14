"""Applicability fact provenance and evaluation-input integrity.

Controlling text:
  * ZA-APP-001 §04 deterministic fact resolution (steps 1-7; "7 -- Freeze ... chosen assertion revision ID/value hash or
    unknown reason"), APP-REQ-005 (FactDefinition source/provenance/conflict/expiry policy), §06 EvaluationInputBundle,
    §10 "FactAssertion revised: Evaluations/rules dependent on that FactDefinition ... Automatic re-evaluation; no silent
    rewrite of previous result", APP-REQ-016 (identical inputs collapse to one evaluation);
  * ZA-TIM-001 TIM-REQ-006/015 (historical answers at an explicit record cutoff), TIM-REQ-019 (no answer for a cutoff
    newer than the data watermark); DOM-INV-11 (another organisation's records are never read or shown);
  * ZA-API-001 API-REQ-014/033 (strict, bounded inputs; refused before any write).

The fact's ``source_ref`` is stored as given: DAT §07 types it as a reference (uuid) without saying to what, so no
organisation/entity check of it is invented here (owner decision).
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"FII-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-fiia-{TAG.lower()}", f"tenant-fiib-{TAG.lower()}"
CCO, CCO_B = f"fii-cco-{TAG.lower()}", f"fiib-cco-{TAG.lower()}"
S: dict = {}
PAST = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()  # noqa: E731


def _ast(tag: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", "CREDIT_INSTITUTION")]}


def _vectors() -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, "CREDIT_INSTITUTION"), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, "CREDIT_INSTITUTION"), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Fact Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="FII CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="FII CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Fact Act",
        "locator": f"OJ L {TAG}/fii", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"FII-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    rv = r.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"FII_{TAG}", title="Fact obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    p = rv["provisions"][0]
    tag = f"f{secrets.token_hex(3)}"
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": rv["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": "2024-01-01", "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200
    pub = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"fii-{TAG}", "changelog": "x"})
    assert pub.status_code == 200, pub.text
    return True


def _entity(name: str, tenant=TA) -> tuple[str, str]:
    """(entity_id, legal-entity subject_id) for a new legal entity of ``tenant``."""
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    return eid, sid


def _fact(client, sid, key, value, actor=CCO, **extra):
    return client.post(f"/api/subjects/{sid}/facts", headers=H(actor),
                       json={"fact_key": key, "value": value, "valid_from": PAST(40), **extra})


def _evaluate(client, sid, actor=CCO, **extra):
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": S["odef"], **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


# ------------------------------------------------------------------------------------------------ definition integrity
def test_unknown_definitions_wrong_types_and_enum_values_are_refused_before_any_write(client, world):
    _, sid = _entity("refuse")
    before = db_fingerprint()
    cases = [(("no.such.fact", True), 404), (("entity.established_in_eu", "yes"), 422),
             (("entity.employee_count", 12.5), 422), (("entity.employee_count", True), 422),
             (("entity.financial_entity_type", "NOT_A_DOMAIN_VALUE"), 422),
             (("entity.operating_countries", "DE"), 422)]
    for (key, value), status in cases:
        r = _fact(client, sid, key, value)
        assert r.status_code == status, (key, value, r.text)
    assert db_fingerprint() == before  # no write and no record sequence consumed


def test_missing_required_facts_are_explicit_unknowns_and_identical_inputs_collapse(client, world):
    _, sid = _entity("missing")
    assert _fact(client, sid, "entity.established_in_eu", True).status_code == 200
    first = _evaluate(client, sid)
    assert first["outcome"] == "UNCERTAIN" and first["missing_facts"] == ["entity.financial_entity_type"]
    assert first["input_bundle"]["unknown_facts"] == [{"fact_definition": "entity.financial_entity_type",
                                                      "reason_code": "MISSING_REQUIRED_FACT"}]
    # The same validated inputs, the same exact instant and record cutoff: one authoritative evaluation (APP-REQ-016,
    # APP-001 §06 evaluation_key).
    again = _evaluate(client, sid, effective_at=first["effective_at"], record_cutoff_seq=first["record_cutoff_seq"])
    assert again["evaluation_id"] == first["evaluation_id"] and again["input_bundle_hash"] == first["input_bundle_hash"]


# ------------------------------------------------------------------------------------------------ history
def test_an_earlier_cutoff_uses_the_fact_revisions_known_then(client, world):
    _, sid = _entity("history")
    assert _fact(client, sid, "entity.established_in_eu", True).status_code == 200
    assert _fact(client, sid, "entity.financial_entity_type", "CREDIT_INSTITUTION").status_code == 200
    then = _seq()
    before = _evaluate(client, sid)
    assert before["outcome"] == "APPLIES"
    changed = _fact(client, sid, "entity.financial_entity_type", "PAYMENT_INSTITUTION",
                    change_kind="PROSPECTIVE_CHANGE", reason="licence changed", valid_from=PAST(1))
    assert changed.status_code == 200
    now = _evaluate(client, sid)
    assert now["outcome"] == "DOES_NOT_APPLY"
    historical = _evaluate(client, sid, record_cutoff_seq=then)
    assert historical["outcome"] == "APPLIES" and historical["record_cutoff_seq"] == then
    assert {f["fact_assertion_revision_id"] for f in historical["input_bundle"]["fact_inputs"]} == \
        {f["fact_assertion_revision_id"] for f in before["input_bundle"]["fact_inputs"]}
    profile = client.get(f"/api/subjects/{sid}/facts", headers=H(CCO), params={"known_seq": then})
    assert profile.status_code == 200 and profile.json()["perspective"]["known_seq"] == then
    [ftype] = [f for f in profile.json()["facts"] if f["fact_key"] == "entity.financial_entity_type"]
    assert ftype["resolution"]["value"] == "CREDIT_INSTITUTION"


def test_a_fact_profile_is_never_answered_for_a_record_cutoff_beyond_the_watermark(client, world):
    _, sid = _entity("future")
    before = db_fingerprint()
    r = client.get(f"/api/subjects/{sid}/facts", headers=H(CCO), params={"known_seq": _seq() + 1000})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE", r.text
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ isolation
def test_an_entity_fact_is_resolved_from_this_organisations_legal_entity_only(client, world):
    """A subordinate subject's entity-level fact is held by its legal entity's subject in the same organisation. A row of
    another organisation naming the same entity (written below the API, or legacy) is never taken as the holder: on
    PostgreSQL row-level security hides it, and the query itself is scoped too (SQLite has no RLS)."""
    from app.db import CELL_ID, AdminSession
    from app.engines.facts import resolve_fact
    from app.models import FactDefinition, LegalEntity, Subject
    eid, le_sid = f"e-shared-{TAG.lower()}", f"s-shared-{TAG.lower()}"
    asset = f"s-asset-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name="Shared Ltd", country="DE", home_cell_id=CELL_ID,
                           subject_id=le_sid))
        db.flush()
        # Recorded first, so an unscoped, unordered lookup meets it first.
        db.add(Subject(subject_id=f"s-0foreign-{TAG.lower()}", tenant_id=TB, entity_id=eid, subject_type="LEGAL_ENTITY",
                       display_name="Foreign row"))
        db.flush()
        db.add(Subject(subject_id=le_sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name="Shared Ltd"))
        db.add(Subject(subject_id=asset, tenant_id=TA, entity_id=eid, subject_type="AI_ASSET", display_name="Model"))
        db.commit()
    assert _fact(client, le_sid, "entity.established_in_eu", True).status_code == 200
    with AdminSession() as db:
        fd = db.execute(select(FactDefinition).where(FactDefinition.key == "entity.established_in_eu")).scalar_one()
        r = resolve_fact(db, tenant_id=TA, subject=db.get(Subject, asset), fd=fd, effective_at=datetime.now(timezone.utc),
                         known_seq=_seq(), create_conflicts=False)
    assert r["known"] is True and r["value"] is True and r["holder_subject_id"] == le_sid, r


def test_another_organisation_cannot_read_or_write_these_facts(client, world):
    _, sid = _entity("iso")
    assert _fact(client, sid, "entity.established_in_eu", True).status_code == 200
    before = db_fingerprint()
    assert _fact(client, sid, "entity.established_in_eu", False, actor=CCO_B).status_code in (403, 404)
    for path in (f"/api/subjects/{sid}/facts", f"/api/subjects/{sid}/facts/history"):
        assert client.get(path, headers=H(CCO_B)).status_code in (403, 404), path
    r = client.post("/api/applicability/evaluations", headers=H(CCO_B), json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert r.status_code in (403, 404)
    # Refusals record only their own authorization.denied audit rows (excluded by db_fingerprint).
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and locks; exact reason required by the CI gate
    return True


def test_a_fact_written_during_a_first_evaluation_is_never_left_unevaluated(client, world, pg, monkeypatch):
    """APP §10: a revised fact re-evaluates its dependents. A subject's first evaluation that is running while the fact
    is written either completes first (and is then re-evaluated by the fact write) or starts after it (and reads it);
    it never commits a result from before the fact with nothing left to re-evaluate it."""
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import Actor, ApplicabilityEvaluation, ObligationDefinition, Subject
    from app.routers.applicability import FactIn, assert_fact_ep
    from app.security import Ctx
    _, sid = _entity("race")
    assert _fact(client, sid, "entity.established_in_eu", True).status_code == 200
    assert _fact(client, sid, "entity.financial_entity_type", "CREDIT_INSTITUTION").status_code == 200
    held, release, out = threading.Event(), threading.Event(), {}
    real = appl.resolve_fact

    def holding(*a, **k):
        r = real(*a, **k)
        if threading.current_thread().name == "evaluator" and not held.is_set():
            held.set()
            assert release.wait(30)
        return r
    monkeypatch.setattr(appl, "resolve_fact", holding)

    def evaluate():
        with SessionLocal() as db:
            set_principal(db, TA, CCO)
            ev = appl.evaluate(db, tenant_id=TA, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, S["odef"]),
                               effective_at=datetime.now(timezone.utc), record_cutoff_seq=None,
                               trigger="EXPLICIT_REQUEST", actor_id=CCO)
            db.commit()
            return ev.evaluation_id

    def change_fact():
        with SessionLocal() as db:
            set_principal(db, TA, CCO)
            assert_fact_ep(sid, FactIn(fact_key="entity.financial_entity_type", value="PAYMENT_INSTITUTION",
                                       valid_from=datetime.now(timezone.utc) - timedelta(days=1),
                                       change_kind="PROSPECTIVE_CHANGE", reason="race"),
                           Ctx(db=db, actor=db.get(Actor, CCO)))

    def run(name, fn):
        try:
            out[name] = ("ok", fn())
        except HTTPException as e:
            out[name] = (e.status_code, e.detail)
    a = threading.Thread(target=run, args=("evaluate", evaluate), name="evaluator")
    a.start()
    assert held.wait(60), out
    b = threading.Thread(target=run, args=("fact", change_fact), name="writer")
    b.start()
    b.join(2.0)
    release.set()
    a.join(90)
    b.join(90)
    assert out["evaluate"][0] == "ok" and out["fact"][0] == "ok", out
    from app.db import AdminSession
    with AdminSession() as db:
        evs = db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == sid)
                         .order_by(ApplicabilityEvaluation.record_seq)).scalars().all()
    # The latest evaluation knows the changed fact: PAYMENT_INSTITUTION does not meet the rule.
    assert evs and evs[-1].outcome == "DOES_NOT_APPLY", [(e.outcome, e.trigger, e.record_cutoff_seq) for e in evs]
