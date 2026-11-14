"""Rule publication atomicity, retry safety and outbox/audit consistency (routers/rules.py publish).

Controlling text:
  * ZA-DAT-001 DAT-REQ-014 / ZA-TIM-001 TIM-REQ-020: the authoritative revision, its audit record and its outbox events
    commit in one transaction; DAT-REQ-012 published content is immutable;
  * ZA-RUL-001 RUL-REQ-020 ("Rule publication MUST fail closed"), §15 (an incomplete impact is never "no impact"), §17
    events; SEC-REQ-048 (integrity failure: fail closed, signal raised);
  * ZA-EVT-001 content_release.published, rule.superseded ("Prior RuleVersion no longer current for new binding
    evaluations"), security.content_signature_failed;
  * ZA-API-001 API-REQ-033 (field length limits enforced by the parser), API-REQ-014;
  * ZA-TIM-001 TIM-REQ-005 (monotonic record sequence; a rolled-back transaction consumes no committed number).

A publication either commits everything (rule version, signed release, published requirement content, draft state,
audit records, outbox events, impact re-evaluations) or nothing. A retry of a published draft is refused and changes
nothing; a retry after a failure publishes exactly once. No Idempotency-Key is involved: the publication route has none,
and adding one is the open API-REQ-004 decision for platform-scope mutations (reported, not implemented).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H
from test_authorization import db_fingerprint

TAG = secrets.token_hex(3).upper()
INSTR = f"RPA-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
T = datetime.now(timezone.utc).date()
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


def _ready_draft(client, odef: str, valid_from: date) -> str:
    """A reviewed, shadowed draft of ``odef``, ready for release."""
    tag = f"p{secrets.token_hex(3)}"
    p = S["rv"]["provisions"][0]
    maps = [{"source_mapping_id": f"sm_{tag}_{n}", "requirement_version_id": S["rv"]["requirement_version_id"],
             "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
             "source_fingerprint": S["source"]["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
            for n in ("root", "1", "2")]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef, "authoring_mode": "AST", "ast": _ast(tag), "golden_vectors": _vectors(),
        "valid_from": valid_from.isoformat(), "change_class": "EXECUTABLE_OUTCOME", "source_mappings": maps})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, r.text
    return did


def _publish(client, did: str, label: str | None = None):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE),
                       json={"label": label or f"rpa-{did[:8]}", "changelog": "probe"})


def _definition(suffix: str) -> str:
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"RPA_{TAG}_{suffix}", title=f"Publication atomicity {suffix}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        return o.obligation_definition_id


def _draft_status(did: str) -> str:
    from app.db import AdminSession
    from app.models import RuleDraft
    with AdminSession() as db:
        return db.get(RuleDraft, did).status


def _rule_versions(odef: str) -> list:
    from app.db import AdminSession
    from app.models import RuleVersion
    with AdminSession() as db:
        return db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == odef)
                          .order_by(RuleVersion.version)).scalars().all()


def _outbox(event_type: str, **payload) -> list:
    from app.db import AdminSession
    from app.models import OutboxEvent
    with AdminSession() as db:
        rows = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)
                          .order_by(OutboxEvent.record_seq)).scalars().all()
        return [e for e in rows if all(e.payload.get(k) == v for k, v in payload.items())]


def _audits(action: str, object_id: str) -> list:
    from app.db import AdminSession
    from app.models import AuditEvent
    with AdminSession() as db:
        return db.execute(select(AuditEvent).where(AuditEvent.action == action, AuditEvent.object_id == object_id)
                          .order_by(AuditEvent.record_seq)).scalars().all()


@pytest.fixture(scope="module")
def world(client):
    from app.db import AdminSession
    from app.models import Instrument
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Atomicity Instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Atomicity Act",
        "locator": f"OJ L {TAG}/rpa", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()
    r = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"RPA-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2024-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert r.status_code in (200, 201), r.text
    S["rv"] = r.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    return True


# ------------------------------------------------------------------------------------------------ request validation
def test_a_label_longer_than_the_release_column_is_refused_before_anything_is_written(client, world):
    """API-REQ-033: the parser enforces field length. catalog_content_release.label is varchar(64); an over-long label
    used to pass the parser, be signed into the manifest and then fail inside the transaction (a raw database error on
    PostgreSQL, a stored over-long label on SQLite)."""
    odef = _definition("LABEL")
    did = _ready_draft(client, odef, T - timedelta(days=30))
    before = db_fingerprint()
    r = _publish(client, did, "L" * 65)
    assert r.status_code == 422, r.text
    assert db_fingerprint() == before and _draft_status(did) == "VERIFIED"
    ok = _publish(client, did, "L" * 64)
    assert ok.status_code == 200, ok.text
    assert ok.json()["content_release"]["label"] == "L" * 64


# ------------------------------------------------------------------------------------------------ failure paths
def test_a_signing_failure_rolls_everything_back_and_a_retry_publishes_once(client, world, monkeypatch):
    from app.engines import keys
    odef = _definition("SIGN")
    v1 = _publish(client, _ready_draft(client, odef, T - timedelta(days=400))).json()["rule_version"]
    did = _ready_draft(client, odef, T - timedelta(days=1))
    real = keys.sign

    def failing(data):
        raise RuntimeError("signing service unavailable")
    monkeypatch.setattr(keys, "sign", failing)
    before = db_fingerprint()
    with pytest.raises(RuntimeError):
        _publish(client, did)
    assert db_fingerprint() == before  # nothing written, the record sequence included
    assert _draft_status(did) == "VERIFIED" and [v.version for v in _rule_versions(odef)] == [1]
    assert _outbox("rule.superseded", obligation_definition_id=odef) == []
    monkeypatch.setattr(keys, "sign", real)
    r = _publish(client, did)
    assert r.status_code == 200, r.text
    v2 = r.json()["rule_version"]
    assert v2["version"] == 2 and [v.version for v in _rule_versions(odef)] == [1, 2]
    [sup] = _outbox("rule.superseded", obligation_definition_id=odef)
    assert (sup.aggregate_id, sup.payload["superseded_by_rule_version_id"]) == (v1["rule_version_id"], v2["rule_version_id"])
    assert len(_outbox("content_release.published", rule_version_id=v2["rule_version_id"])) == 1


def test_a_failure_during_impact_leaves_no_release_and_no_supersession_event(client, world, monkeypatch):
    from app.routers import rules as rules_router
    odef = _definition("IMPACT")
    _publish(client, _ready_draft(client, odef, T - timedelta(days=400)))
    did = _ready_draft(client, odef, T - timedelta(days=1))

    def broken(*a, **k):  # after the rule version, the release and rule.superseded are written in the transaction
        raise RuntimeError("impact infrastructure failure")
    monkeypatch.setattr(rules_router, "_reevaluate_targets", broken)
    before = db_fingerprint()
    with pytest.raises(RuntimeError):
        _publish(client, did)
    assert db_fingerprint() == before
    assert _outbox("rule.superseded", obligation_definition_id=odef) == [] and _draft_status(did) == "VERIFIED"
    monkeypatch.undo()
    assert _publish(client, did).status_code == 200
    assert len(_outbox("rule.superseded", obligation_definition_id=odef)) == 1


def test_an_integrity_failure_fails_closed_and_commits_only_the_security_signal(client, world, monkeypatch):
    from app.engines import applicability as appl
    from app.platform import current_watermark
    from app.db import AdminSession
    odef = _definition("INTEGRITY")
    did = _ready_draft(client, odef, T - timedelta(days=1))

    def tampered(db, rv):
        raise appl.RuleIntegrityError(rv, "PROBE_ALTERED", rv.rule_version_id)
    monkeypatch.setattr(appl, "verify_rule_integrity", tampered)
    with AdminSession() as db:
        seq_before = current_watermark(db)["record_seq"]
    r = _publish(client, did)
    assert r.status_code == 500 and r.json()["detail"]["error"] == "RUL_SIGNATURE_INVALID", r.text
    assert _rule_versions(odef) == [] and _draft_status(did) == "VERIFIED"
    signals = [e for e in _outbox("security.content_signature_failed") if e.payload.get("verification_result") == "PROBE_ALTERED"]
    [signal] = signals
    assert _outbox("content_release.published", rule_version_id=signal.payload["rule_version_id"]) == []
    assert _outbox("rule.superseded", obligation_definition_id=odef) == []
    [audit] = _audits("content.verify", signal.payload["rule_version_id"])
    with AdminSession() as db:
        seq_after = current_watermark(db)["record_seq"]
    # The publication's numbers were rolled back; the signal is the only committed write and took exactly one.
    assert audit.record_seq == signal.record_seq == seq_after == seq_before + 1


def test_a_retry_after_success_is_refused_and_changes_nothing(client, world):
    odef = _definition("RETRY")
    did = _ready_draft(client, odef, T - timedelta(days=1))
    assert _publish(client, did).status_code == 200
    before = db_fingerprint()
    again = _publish(client, did)
    assert again.status_code == 409
    assert db_fingerprint() == before and len(_rule_versions(odef)) == 1


# ------------------------------------------------------------------------------------------------ consistency
def test_one_publication_writes_its_rows_audit_and_events_at_one_record_sequence(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease
    odef = _definition("SEQ")
    v1 = _publish(client, _ready_draft(client, odef, T - timedelta(days=400))).json()["rule_version"]
    out = _publish(client, _ready_draft(client, odef, T - timedelta(days=1))).json()
    rv, cr_id = out["rule_version"], out["content_release"]["content_release_id"]
    with AdminSession() as db:
        cr = db.get(ContentRelease, cr_id)
        received = cr.cell_received_seq
    [published] = _outbox("content_release.published", rule_version_id=rv["rule_version_id"])
    [sup] = _outbox("rule.superseded", obligation_definition_id=odef)
    [pub_audit] = _audits("content.publish", cr_id)
    [impact_audit] = _audits("content.impact", cr_id)
    assert rv["record_seq"] == received == published.record_seq == sup.record_seq == pub_audit.record_seq \
        == impact_audit.record_seq
    assert sup.aggregate_id == v1["rule_version_id"] and sup.payload["content_release_id"] == cr_id
    assert len(_outbox("content_release.published")) == len({e.aggregate_id for e in _outbox("content_release.published")})


def test_release_verification_never_changes_publication_history(client, world):
    from app.db import AdminSession
    from app.models import ContentRelease, RuleVersion
    from app.platform import to_dict
    odef = _definition("VERIFY")
    out = _publish(client, _ready_draft(client, odef, T - timedelta(days=1))).json()
    cr_id, rv_id = out["content_release"]["content_release_id"], out["rule_version"]["rule_version_id"]

    def rows():
        with AdminSession() as db:
            return to_dict(db.get(ContentRelease, cr_id)), to_dict(db.get(RuleVersion, rv_id))
    published = rows()
    first = client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(AUTHOR))
    assert first.status_code == 200, first.text
    before = db_fingerprint()
    second = client.get(f"/api/catalog/releases/{cr_id}/verify", headers=H(AUTHOR))
    assert second.status_code == 200 and second.json() == first.json()
    assert second.headers["Za-Verification-Recorded"] == "false" and db_fingerprint() == before
    assert rows() == published
