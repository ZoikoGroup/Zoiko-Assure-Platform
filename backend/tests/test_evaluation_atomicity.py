"""Applicability evaluation failure atomicity and retry safety (guards; no defect is claimed).

Controlling text: ZA-DAT-001 DAT-REQ-014 / ZA-TIM-001 TIM-REQ-020 (the evaluation, its determination revision and its
outbox events commit together or not at all), ZA-APP-001 APP-REQ-016 (idempotent under retries) and §06 evaluation_key,
TIM-REQ-005 (a rolled-back transaction leaves no committed record sequence).

An evaluation is written, its outbox event emitted and its determination derived in one transaction that the caller
commits once. These tests fail a step after the evaluation row is written and check that nothing survives, and that a
retry with the same complete key then evaluates cleanly (no orphan row, no misleading deduplication).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"EAT-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-eat-{TAG.lower()}"
CCO = f"eat-cco-{TAG.lower()}"
S: dict = {}
AT = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


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
        db.add(Instrument(identifier=INSTR, title="Atomicity Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="EAT CCO", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Atomicity Act",
        "locator": f"OJ L {TAG}/eat", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"EAT-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    rv = r.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"EAT_{TAG}", title="Atomicity obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    p, tag = rv["provisions"][0], f"a{secrets.token_hex(3)}"
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
                       json={"label": f"eat-{TAG}", "changelog": "x"}).status_code == 200
    return True


def _subject(client, name: str) -> str:
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
    return sid


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _rows(sid):
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination, ApplicabilityEvaluation
    with AdminSession() as db:
        n = lambda m: db.execute(select(func.count()).select_from(m).where(m.subject_id == sid)).scalar()  # noqa: E731
        return n(ApplicabilityEvaluation), n(ApplicabilityDetermination)


@pytest.mark.parametrize("step", ["_derive", "emit"])
def test_a_failure_after_the_evaluation_is_written_leaves_nothing_and_a_retry_evaluates_cleanly(client, world, monkeypatch, step):
    """The evaluation row is flushed, then its outbox event (emit) or its determination (_derive) fails: nothing of it is
    committed, and the same complete key then evaluates as if the failed attempt never happened."""
    from app.engines import applicability as appl
    sid = _subject(client, f"fail-{step.strip('_')}")
    k = _seq()
    body = {"subject_id": sid, "obligation_definition_id": S["odef"], "effective_at": AT.isoformat(), "record_cutoff_seq": k}
    real = getattr(appl, step)

    def failing(*a, **kw):
        if step == "emit" and kw.get("event_type") != "applicability.evaluated":
            return real(*a, **kw)
        raise RuntimeError(f"simulated failure in {step}")
    monkeypatch.setattr(appl, step, failing)
    before = db_fingerprint()
    with pytest.raises(RuntimeError):
        client.post("/api/applicability/evaluations", headers=H(CCO), json=body)
    assert db_fingerprint() == before and _rows(sid) == (0, 0)  # no evaluation, determination, event or sequence
    monkeypatch.undo()
    r = client.post("/api/applicability/evaluations", headers=H(CCO), json=body)
    assert r.status_code == 200 and r.json()["outcome"] == "APPLIES", r.text
    assert r.json()["current_determination"]["evaluation_id"] == r.json()["evaluation_id"]
    assert _rows(sid) == (1, 1)


def test_a_failure_part_way_through_evaluate_all_commits_none_of_the_batch(client, world, monkeypatch):
    """evaluate-all evaluates every definition of the subject's type in one transaction: a failure after some of them
    were written rolls back the whole batch (no partial batch)."""
    from app.engines import applicability as appl
    sid = _subject(client, "batch")
    real, calls = appl._derive, []

    def fail_second(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("simulated failure on the second evaluation of the batch")
        return real(*a, **kw)
    monkeypatch.setattr(appl, "_derive", fail_second)
    before = db_fingerprint()
    with pytest.raises(RuntimeError):
        client.post(f"/api/applicability/evaluate-all?subject_id={sid}", headers=H(CCO))
    assert len(calls) == 2 and db_fingerprint() == before and _rows(sid) == (0, 0)

