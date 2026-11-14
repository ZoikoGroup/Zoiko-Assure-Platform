"""APP-001 §06 evaluation deduplication key.

ZA-APP-001 §06 "Idempotency and concurrency": "evaluation_key = hash(subject, obligation_definition, rule_version,
effective_at, record_cutoff, exact fact revision set, evaluator_build). Concurrent identical jobs collapse to one
committed evaluation ... A new FactAssertion revision, RuleVersion or evaluator build changes the key and creates a new
immutable evaluation." APP-REQ-016 (idempotent under retries and concurrency); TIM-REQ-012/017; DOM-INV-11.

Every key component is a field of the frozen EvaluationInputBundle, whose format and hash are unchanged. Evaluations
stored before the full key keep their recorded (date-level, no-cutoff) key; they are reported as LEGACY_EFFECTIVE_DATE and
never matched by a new request.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H, add_principal
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"EKC-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA, TB = f"tenant-ekca-{TAG.lower()}", f"tenant-ekcb-{TAG.lower()}"
CCO, CCO_B = f"ekc-cco-{TAG.lower()}", f"ekcb-cco-{TAG.lower()}"
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


def _publish_rule(client, valid_from="2024-01-01"):
    p, tag = S["rv"]["provisions"][0], f"k{secrets.token_hex(3)}"
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": valid_from, "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"ekc-{tag}", "changelog": "x"})
    assert r.status_code == 200, r.text
    return r.json()["rule_version"]


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Key Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        add_principal(db, actor_id=CCO, tenant_id=TA, actor_type="HUMAN", display_name="EKC CCO", roles=["CCO"])
        add_principal(db, actor_id=CCO_B, tenant_id=TB, actor_type="HUMAN", display_name="EKC CCO B", roles=["CCO"])
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Key Act",
        "locator": f"OJ L {TAG}/ekc", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"EKC-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"EKC_{TAG}", title="Key obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    S["rule1"] = _publish_rule(client)
    return True


def _subject(client, name: str, tenant=TA, actor=CCO) -> str:
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid, eid = f"s-{name}-{TAG.lower()}", f"e-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=eid, tenant_id=tenant, legal_name=f"{name} Ltd", country="DE", home_cell_id=CELL_ID, subject_id=sid))
        db.flush()
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=eid, subject_type="LEGAL_ENTITY", display_name=f"{name} Ltd"))
        db.commit()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        r = client.post(f"/api/subjects/{sid}/facts", headers=H(actor), json={
            "fact_key": key, "value": value, "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
        assert r.status_code == 200, r.text
    return sid


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _advance():
    from app.db import AdminSession
    from app.platform import next_record_seq
    with AdminSession() as db:
        next_record_seq(db)
        db.commit()


def _eval(client, sid, actor=CCO, **body):
    r = client.post("/api/applicability/evaluations", headers=H(actor),
                    json={"subject_id": sid, "obligation_definition_id": S["odef"], **body})
    assert r.status_code == 200, r.text
    return r.json()


def _evaluations(sid) -> list:
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        return db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == sid)
                          .order_by(ApplicabilityEvaluation.record_seq)).scalars().all()


AT = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


# ------------------------------------------------------------------------------------------------ key components
def test_an_identical_complete_key_reuses_one_evaluation(client, world):
    sid = _subject(client, "same")
    k = _seq()
    first = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    again = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    assert again["evaluation_id"] == first["evaluation_id"] and len(_evaluations(sid)) == 1
    assert first["evaluation_key_profile"] == "APP_001_06"


def test_a_different_record_cutoff_is_a_distinct_evaluation(client, world):
    sid = _subject(client, "cutoff")
    k1 = _seq()
    _advance()
    k2 = _seq()
    e1 = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k1)
    e2 = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k2)
    assert e1["evaluation_id"] != e2["evaluation_id"]
    assert (e1["record_cutoff_seq"], e2["record_cutoff_seq"]) == (k1, k2)  # each records the cutoff it was asked for


def test_a_different_instant_on_the_same_date_is_a_distinct_evaluation(client, world):
    sid = _subject(client, "instant")
    k = _seq()
    e1 = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    e2 = _eval(client, sid, effective_at=(AT + timedelta(seconds=1)).isoformat(), record_cutoff_seq=k)
    assert e1["evaluation_id"] != e2["evaluation_id"]
    assert (e1["effective_at"][:10], e2["effective_at"][:10]) == (AT.date().isoformat(),) * 2


def test_a_different_fact_revision_build_rule_subject_or_definition_is_a_distinct_evaluation(client, world, monkeypatch):
    from app import codes
    sid, other = _subject(client, "facts"), _subject(client, "other")
    k = _seq()
    base = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    # Another subject with the same fact values: never the same evaluation.
    assert _eval(client, other, effective_at=AT.isoformat(), record_cutoff_seq=k)["evaluation_id"] != base["evaluation_id"]
    # Another evaluator build.
    monkeypatch.setattr(codes, "EVALUATOR_BUILD", "za-applicability-evaluator:9.9.9-key")
    built = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    assert built["evaluation_id"] != base["evaluation_id"] and built["evaluator_build"] == "za-applicability-evaluator:9.9.9-key"
    monkeypatch.undo()
    # A new fact revision (recorded later, read at a later cutoff).
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json={
        "fact_key": "entity.established_in_eu", "value": True, "change_kind": "CORRECTION", "reason": "re-attested",
        "valid_from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()})
    assert r.status_code == 200, r.text
    k2 = _seq()
    revised = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k2)
    assert revised["evaluation_id"] != base["evaluation_id"]
    assert revised["input_bundle"]["fact_inputs"] != base["input_bundle"]["fact_inputs"]
    # A new rule version.
    rule2 = _publish_rule(client)
    newer = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=_seq())
    assert newer["rule_version_id"] == rule2["rule_version_id"] != base["rule_version_id"]


# ------------------------------------------------------------------------------------------------ determinations, replay
def test_the_new_evaluation_drives_the_determination_and_the_old_one_still_replays(client, world):
    sid = _subject(client, "derive")
    k1 = _seq()
    e1 = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k1)
    assert e1["current_determination"]["evaluation_id"] == e1["evaluation_id"]
    _advance()
    e2 = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=_seq())
    assert e2["evaluation_id"] != e1["evaluation_id"]
    assert e2["current_determination"]["evaluation_id"] == e2["evaluation_id"]  # refreshed by the newer evaluation
    old = client.get(f"/api/applicability/evaluations/{e1['evaluation_id']}", headers=H(CCO)).json()
    assert old["replay"]["status"] == "REPRODUCED" and old["record_cutoff_seq"] == k1
    assert old["effective_at"] == e1["effective_at"] and old["evaluation_key_profile"] == "APP_001_06"


def test_a_stored_legacy_key_is_reported_as_legacy_and_never_reused(client, world):
    """An evaluation stored before the full key keeps its date-level key as recorded; a new request with the same inputs
    does not match it and creates a new evaluation under the full key."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ApplicabilityEvaluation, new_id
    from app.platform import digest, next_record_seq
    sid = _subject(client, "legacy")
    k = _seq()
    probe = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    with AdminSession() as db:
        src = db.get(ApplicabilityEvaluation, probe["evaluation_id"])
        b = src.input_bundle
        legacy_key = digest({f: b[f] for f in appl.LEGACY_EVALUATION_KEY_FIELDS} | {"effective_date": b["effective_at"][:10]})
        legacy = ApplicabilityEvaluation(**{c.key: getattr(src, c.key) for c in ApplicabilityEvaluation.__mapper__.column_attrs
                                            if c.key not in ("evaluation_id", "evaluation_key", "record_seq")},
                                         evaluation_id=new_id(), evaluation_key=legacy_key, record_seq=next_record_seq(db))
        db.add(legacy)
        db.commit()
        legacy_id = legacy.evaluation_id
    view = client.get(f"/api/applicability/evaluations/{legacy_id}", headers=H(CCO)).json()
    assert view["evaluation_key_profile"] == "LEGACY_EFFECTIVE_DATE"
    assert view["evaluation_key"] == legacy_key and view["replay"]["status"] == "REPRODUCED"
    again = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=k)
    assert again["evaluation_id"] == probe["evaluation_id"] != legacy_id


# ------------------------------------------------------------------------------------------------ isolation, refusals
def test_another_organisation_cannot_reach_or_reuse_an_evaluation(client, world):
    sid = _subject(client, "iso")
    e = _eval(client, sid, effective_at=AT.isoformat(), record_cutoff_seq=_seq())
    assert client.get(f"/api/applicability/evaluations/{e['evaluation_id']}", headers=H(CCO_B)).status_code in (403, 404)
    r = client.post("/api/applicability/evaluations", headers=H(CCO_B), json={
        "subject_id": sid, "obligation_definition_id": S["odef"], "effective_at": AT.isoformat()})
    assert r.status_code in (403, 404) and e["evaluation_id"] not in r.text
    b_sid = _subject(client, "isob", tenant=TB, actor=CCO_B)  # identical facts, another organisation
    b = _eval(client, b_sid, actor=CCO_B, effective_at=AT.isoformat(), record_cutoff_seq=_seq())
    assert b["evaluation_id"] != e["evaluation_id"] and b["tenant_id"] == TB


def test_rejected_requests_write_nothing(client, world):
    sid = _subject(client, "reject")
    before = db_fingerprint()
    for body in ({"subject_id": sid, "obligation_definition_id": S["odef"], "record_cutoff_seq": _seq() + 1000},
                 {"subject_id": sid, "obligation_definition_id": S["odef"], "trigger": "NOT_A_TRIGGER"},
                 {"subject_id": "no-such-subject", "obligation_definition_id": S["odef"]}):
        r = client.post("/api/applicability/evaluations", headers=H(CCO), json=body)
        assert r.status_code in (404, 422), r.text
    assert db_fingerprint() == before and _evaluations(sid) == []


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(world):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")  # concurrent transactions and locks; exact reason required by the CI gate
    return True


def _concurrent(sid, cutoffs) -> list:
    from app.db import SessionLocal, set_principal
    from app.engines import applicability as appl
    from app.models import ObligationDefinition, Subject
    barrier, out = threading.Barrier(len(cutoffs)), []

    def run(k):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, CCO)
                barrier.wait(20)
                ev = appl.evaluate(db, tenant_id=TA, subject=db.get(Subject, sid), odef=db.get(ObligationDefinition, S["odef"]),
                                   effective_at=AT, record_cutoff_seq=k, trigger="EXPLICIT_REQUEST", actor_id=CCO)
                db.commit()
                out.append(ev.evaluation_id)
        except (HTTPException, Exception) as e:  # noqa: BLE001
            out.append(f"error: {e!r}")
    threads = [threading.Thread(target=run, args=(k,)) for k in cutoffs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_concurrent_identical_keys_commit_one_evaluation(client, world, pg):
    sid = _subject(client, "concurrent-same")
    k = _seq()
    out = _concurrent(sid, [k, k, k])
    assert len(set(out)) == 1 and not out[0].startswith("error"), out
    assert len(_evaluations(sid)) == 1


def test_concurrent_requests_at_different_cutoffs_commit_distinct_evaluations(client, world, pg):
    sid = _subject(client, "concurrent-diff")
    k1 = _seq()
    _advance()
    k2 = _seq()
    out = _concurrent(sid, [k1, k2])
    assert len(set(out)) == 2 and not any(o.startswith("error") for o in out), out
    assert sorted(e.record_cutoff_seq for e in _evaluations(sid)) == [k1, k2]
