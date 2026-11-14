"""Applicability replay guards not covered elsewhere (GET /applicability/evaluations/{id} -> replay).

Controlling text: ZA-APP-001 APP-REQ-003 / §06 (the evaluation binds its exact fact revisions, rule version and evaluator
build), ZA-TIM-001 TIM-REQ-012 (exact temporal context and immutable input revisions), ZA-STA-001 STA-REQ-032 (replay
across evaluator builds is a governed regression, never a silent reproduction).

These document behaviour that is already correct (they pass on the code as it stands); no defect is claimed:
  * a different evaluator build is reported BUILD_CHANGED with its recorded and running builds, never REPRODUCED;
  * a historical fact revision whose value was altered in place (the table has no column guard yet: a migration
    decision) makes the replay NOT_REPRODUCED, never REPRODUCED under the altered value;
  * replay is read-only and another organisation cannot reach it.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"ERG-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-erga-{TAG.lower()}", f"tenant-ergb-{TAG.lower()}"
CCO, CCO_B = f"erg-cco-{TAG.lower()}", f"ergb-cco-{TAG.lower()}"
S: dict = {}


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
        db.add(Instrument(identifier=INSTR, title="Replay Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="ERG CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="ERG CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Replay Act",
        "locator": f"OJ L {TAG}/erg", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"ERG-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    rv = r.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"ERG_{TAG}", title="Replay obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    p, tag = rv["provisions"][0], f"g{secrets.token_hex(3)}"
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
    assert client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": f"erg-{TAG}", "changelog": "x"}).status_code == 200
    return True


def _evaluated(client, name: str) -> dict:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=TA, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
            "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
        assert r.status_code == 200, r.text
    ev = client.post("/api/applicability/evaluations", headers=H(CCO), json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert ev.status_code == 200 and ev.json()["outcome"] == "APPLIES", ev.text
    return ev.json()


def _replay(client, evaluation_id, actor=CCO):
    r = client.get(f"/api/applicability/evaluations/{evaluation_id}", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()["replay"]


def test_a_different_evaluator_build_is_reported_build_changed_never_reproduced(client, world, monkeypatch):
    from app import codes
    ev = _evaluated(client, "build")
    assert _replay(client, ev["evaluation_id"])["status"] == "REPRODUCED"
    monkeypatch.setattr(codes, "EVALUATOR_BUILD", "za-applicability-evaluator:9.9.9-probe")
    replay = _replay(client, ev["evaluation_id"])
    assert replay["status"] == "BUILD_CHANGED" and replay["matches"] is False
    [build] = [c for c in replay["checks"] if c["check"] == "EVALUATOR_BUILD"]
    assert (build["passed"], build["recorded"], build["running"]) == (False, ev["evaluator_build"], "za-applicability-evaluator:9.9.9-probe")


def test_a_historical_fact_value_altered_in_place_is_never_reproduced(client, world):
    from app.db import AdminSession
    from app.models import FactAssertion
    ev = _evaluated(client, "tamper")
    used = {f["fact_definition"]: f["fact_assertion_revision_id"] for f in ev["input_bundle"]["fact_inputs"]}
    with AdminSession() as db:
        row = db.get(FactAssertion, used["entity.financial_entity_type"])
        row.value_text = "PAYMENT_INSTITUTION"  # below the API: the column has no database guard yet
        db.commit()
    replay = _replay(client, ev["evaluation_id"])
    failed = set(replay["failed_checks"])
    assert replay["status"] == "NOT_REPRODUCED" and {"REDERIVED_BUNDLE", "OUTCOME"} <= failed, replay
    assert replay["reproduced_outcome"] == "DOES_NOT_APPLY" and replay["matches"] is False


def test_replay_is_read_only_and_never_reachable_from_another_organisation(client, world):
    ev = _evaluated(client, "readonly")
    before = db_fingerprint()
    first, second = _replay(client, ev["evaluation_id"]), _replay(client, ev["evaluation_id"])
    assert first == second and first["status"] == "REPRODUCED"
    assert db_fingerprint() == before  # no evaluation, audit row, outbox event or record sequence written
    r = client.get(f"/api/applicability/evaluations/{ev['evaluation_id']}", headers=H(CCO_B))
    assert r.status_code in (403, 404) and ev["evaluation_id"] not in r.text
