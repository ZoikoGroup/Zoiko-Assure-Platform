"""Request validation vs persistence: every value an owned mutation accepts is one the database stores the same way on
PostgreSQL and SQLite, and an invalid value is refused before anything is written or a record sequence is consumed.

Controlling text:
  * ZA-API-001 API-REQ-033 (parsers enforce field length and limits), API-REQ-014 (strict request schemas);
  * ZA-TIM-001 TIM-REQ-002 (canonical, non-empty half-open valid ranges), §01 perspectives (CURRENT, AS_KNOWN_THEN,
    RECONSTRUCTED_NOW, CUSTOM_BITEMPORAL), TIM-REQ-019 (no answer for a record cutoff newer than the data watermark);
  * ZA-APP-001 §06 EvaluationRequest.trigger "Enum; required ONBOARDING, FACT_CHANGED, RULE_CHANGED, CORRECTION,
    RECLASSIFICATION, EXPLICIT_REQUEST, SCHEDULED_REVIEW, RECONSTRUCTION";
  * ZA-RUL-001 §14 change classes and §15 / RUL-REQ-020 (publication fails closed when a required gate is missing).

Each test checks the exact boundary (accepted) and one value beyond it (422, nothing written, the record sequence
unchanged). Column sizes are those of app/models.py (migrations 0001-0013).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"APB-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-apb-{TAG.lower()}"
CCO, OWNER, APPROVER = f"apb-cco-{TAG.lower()}", f"apb-owner-{TAG.lower()}", f"apb-approver-{TAG.lower()}"
T = datetime.now(timezone.utc).date()
S: dict = {}


def _refused(r, before):
    assert r.status_code == 422, r.text
    assert db_fingerprint() == before  # nothing written; the record sequence is part of the fingerprint


def _subject(name: str) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


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


def _draft_body(**over) -> dict:
    tag = f"b{secrets.token_hex(3)}"
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    return {"obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
            "valid_from": (T - timedelta(days=30)).isoformat(), "change_class": "EXECUTABLE_OUTCOME",
            "source_mappings": maps, **over}


def _reviewed(client, body: dict, shadow=True) -> str:
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json=body)
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    steps = [(AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve")] + ([(RELEASE, "shadow")] if shadow else [])
    for actor, step in steps:
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, r.text
    return did


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Boundary Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        for aid, roles in ((CCO, ["CCO"]), (OWNER, ["CONTROL_OWNER"]), (APPROVER, ["RISK_APPROVER"])):
            add_principal(db, actor_id=aid, tenant_id=TA, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Boundary Act",
        "locator": f"OJ L {TAG}/apb", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"APB-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"APB_{TAG}", title="Boundary obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    # One published rule version, so evaluations of the definition resolve a rule.
    did = _reviewed(client, _draft_body())
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"apb-{TAG}", "changelog": "base"})
    assert r.status_code == 200, r.text
    return True


# ------------------------------------------------------------------------------------------------ rule drafts
CHANGE_CLASSES = ("EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME", "EXECUTABLE_OUTCOME", "TEMPORAL",
                  "DEPENDENCY", "CORRECTION")


def test_a_draft_change_class_is_one_of_the_rul_14_classes(client, world):
    before = db_fingerprint()
    for bad in (None, "BOGUS", "X" * 49):
        _refused(client.post("/api/rules/drafts", headers=H(AUTHOR), json=_draft_body(change_class=bad)), before)
    for cls in CHANGE_CLASSES:
        assert client.post("/api/rules/drafts", headers=H(AUTHOR), json=_draft_body(change_class=cls)).status_code == 200, cls
    did = client.post("/api/rules/drafts", headers=H(AUTHOR), json=_draft_body()).json()["draft_id"]
    before = db_fingerprint()
    _refused(client.put(f"/api/rules/drafts/{did}", headers=H(AUTHOR), json=_draft_body(change_class=None)), before)


def test_a_stored_unclassified_draft_cannot_publish_without_shadow_impact(client, world):
    """RUL §15 / RUL-REQ-020: publication fails closed. A draft whose stored change class is not a §14 class (written
    before the schema check, or below the API) needs the shadow impact the gate otherwise requires."""
    from app.db import AdminSession
    from app.models import RuleDraft
    did = _reviewed(client, _draft_body(), shadow=False)
    with AdminSession() as db:
        db.get(RuleDraft, did).change_class = None
        db.commit()
    before = db_fingerprint()
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"apb-{did[:8]}", "changelog": "x"})
    assert r.status_code == 409 and "Shadow impact required" in r.text, r.text
    assert db_fingerprint() == before


def test_a_draft_authoring_mode_is_ast_or_decision_table(client, world):
    before = db_fingerprint()
    for bad in ("TABLE", "X" * 33):
        _refused(client.post("/api/rules/drafts", headers=H(AUTHOR), json=_draft_body(authoring_mode=bad)), before)


def test_a_draft_valid_period_is_a_non_empty_half_open_range(client, world):
    before = db_fingerprint()
    start = T - timedelta(days=30)
    for end in (start, start - timedelta(days=1)):
        _refused(client.post("/api/rules/drafts", headers=H(AUTHOR),
                             json=_draft_body(valid_from=start.isoformat(), valid_to=end.isoformat())), before)
    ok = client.post("/api/rules/drafts", headers=H(AUTHOR),
                     json=_draft_body(valid_from=start.isoformat(), valid_to=(start + timedelta(days=1)).isoformat()))
    assert ok.status_code == 200, ok.text


# ------------------------------------------------------------------------------------------------ applicability
def test_an_evaluation_trigger_is_one_of_the_app_06_triggers(client, world):
    sid = _subject("trigger")
    before = db_fingerprint()
    for bad in ("BOGUS", "X" * 41):
        _refused(client.post("/api/applicability/evaluations", headers=H(CCO),
                             json={"subject_id": sid, "obligation_definition_id": S["odef"], "trigger": bad}), before)
    ok = client.post("/api/applicability/evaluations", headers=H(CCO),
                     json={"subject_id": sid, "obligation_definition_id": S["odef"], "trigger": "SCHEDULED_REVIEW"})
    assert ok.status_code == 200 and ok.json()["trigger"] == "SCHEDULED_REVIEW", ok.text


def test_fact_source_references_and_integer_values_fit_their_columns(client, world):
    sid = _subject("facts")
    vf = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

    def fact(key, value, **extra):
        return client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={"fact_key": key, "value": value,
                                                                              "valid_from": vf, **extra})
    before = db_fingerprint()
    _refused(fact("entity.established_in_eu", True, source_ref="R" * 301), before)
    for too_big in (2 ** 63, -(2 ** 63) - 1):
        r = fact("entity.employee_count", too_big)
        assert r.status_code == 422 and db_fingerprint() == before, r.text
    assert fact("entity.established_in_eu", True, source_ref="R" * 300).status_code == 200
    assert fact("entity.employee_count", 2 ** 63 - 1, change_kind="PROSPECTIVE_CHANGE", reason="boundary").status_code == 200


# ------------------------------------------------------------------------------------------------ controls and governance
def test_test_definitions_fit_their_columns(client, world):
    sid = _subject("controls")

    def control(key, **test):
        return client.post("/api/controls", headers=H(OWNER), json={
            "control_key": key, "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
            "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL", **test}]})
    before = db_fingerprint()
    for bad in ({"name": "N" * 201}, {"source_class": "S" * 49}, {"cadence_days": 2 ** 31}):
        _refused(control("C_ICT_INCIDENT_PROCESS", **bad), before)
    r = control("C_ICT_INCIDENT_PROCESS", name="N" * 200, source_class="S" * 48, cadence_days=2 ** 31 - 1)
    assert r.status_code == 200, r.text


def test_a_risk_acceptance_scope_fits_its_column(client, world):
    def accept(scope):
        return client.post("/api/risk-acceptances", headers=H(APPROVER), json={
            "scope_ref": scope, "residual_risk_statement": "Accepted residual risk.", "valid_from": "2026-01-01T00:00:00Z"})
    before = db_fingerprint()
    _refused(accept("S" * 201), before)
    assert accept("S" * 200).status_code == 200


def _obligation() -> str:
    from app.db import AdminSession
    from app.models import ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage, sid = new_id(), _subject(f"obl{secrets.token_hex(2)}")
    ast = {"node_type": "ALL", "node_id": "root", "children": [{"node_type": "CONTROL", "node_id": "l", "control_key": "C_AI_MONITORING_LOGS"}]}
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with AdminSession() as db:
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=f"e-{sid}", subject_id=sid,
                                  obligation_definition_id=S["odef"], determination_revision_id="probe", attestation_id="probe",
                                  lifecycle="ADOPTED", valid_from=t0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=t0, record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def test_a_snapshot_perspective_and_record_cutoff_are_valid_before_anything_is_persisted(client, world):
    from app.db import AdminSession
    from app.platform import current_watermark
    lineage = _obligation()
    period = {"period_from": "2026-09-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z"}
    before = db_fingerprint()
    for path in ("snapshots", "preview"):
        _refused(client.post(f"/api/obligations/{lineage}/{path}", headers=H(CCO), json={**period, "perspective": "BOGUS"}), before)
        with AdminSession() as db:
            future = current_watermark(db)["record_seq"] + 1000
        r = client.post(f"/api/obligations/{lineage}/{path}", headers=H(CCO), json={**period, "record_cutoff_seq": future})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE", r.text
        assert db_fingerprint() == before
    for perspective in ("CURRENT", "RECONSTRUCTED_NOW"):
        ok = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json={**period, "perspective": perspective})
        assert ok.status_code == 200 and ok.json()["perspective"] == perspective, ok.text
