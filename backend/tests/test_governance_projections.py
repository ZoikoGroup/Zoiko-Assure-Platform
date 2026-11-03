"""Stage 3c remediation: governance state projections and legal-hold evidence (migration 0010; pre-push audit).

Requirements: DAT-REQ-009/037, STA-REQ-027, EVD-REQ-018, SEC-REQ-029 (projections change only through their event
writer; nothing deletes governance state); ZA-EVT-001 §18/§19 evidence.hold_placed, privacy.legal_hold_released;
IAM §21 / SEC-REQ-031 (attributable audit of legal-hold decisions); DAT-REQ-014 (outbox in the same transaction);
DAT-REQ-024 (migration-created history is labelled, never presented as observed).

The PostgreSQL tests talk to the database directly as the runtime role (DATABASE_URL), below every application check.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from conftest import H, stamp

TENANT, OTHER = "tenant-zoiko-group", "tenant-authz-b"
LIFECYCLE = {  # migration 0010: the only columns the runtime role may UPDATE
    "assurance_exception_revision": {"state", "approved_by"},
    "assurance_compensation_revision": {"status", "approved_by"},
    "assurance_issue": {"state", "owner_actor_id"},
    "evidence_legal_hold_revision": {"status", "released_at"},
    "evidence_conflict": {"status", "disposition"},
    "assurance_obligation_occurrence": {"fulfilled_at", "evidence_ids"},
}


def _evidence(client):
    return client.get("/api/evidence", headers=H("actor-cco")).json()[0]["evidence_id"]


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _release_all(client, eid):
    # release_legal_hold releases one ACTIVE hold of the evidence; earlier tests may have left some on it.
    while client.post(f"/api/evidence/{eid}/release-hold", headers=H("actor-gc"), json={"reason": "test reset"}).status_code == 200:
        pass


def _place_and_release(client, eid):
    _release_all(client, eid)
    assert client.post(f"/api/evidence/{eid}/hold", headers=H("actor-gc"),
                       json={"reason": "litigation probe", "authority": "General Counsel"}).status_code == 200
    hold_id = client.get(f"/api/evidence/{eid}/holds/events", headers=H("actor-cco")).json()[-1]["hold_id"]
    assert client.post(f"/api/evidence/{eid}/release-hold", headers=H("actor-gc"),
                       json={"reason": "matter closed"}).status_code == 200
    return hold_id


# ------------------------------------------------------------------------------------------------ both databases
def test_legal_hold_place_and_release_record_event_audit_and_outbox_together(client):
    from app.models import AuditEvent, LegalHold, LegalHoldEvent, OutboxEvent
    eid = _evidence(client)
    hold_id = _place_and_release(client, eid)
    events = {e.event_type: e for e in _rows(LegalHoldEvent, hold_id=hold_id)}
    placed, released = events["HOLD_PLACED"], events["HOLD_RELEASED"]
    audits = {a.action: a for a in _rows(AuditEvent, object_id=hold_id)}
    assert (audits["legal_hold.placed"].actor_id, audits["legal_hold.placed"].new_state,
            audits["legal_hold.placed"].record_seq) == ("actor-gc", "ACTIVE", placed.record_seq)
    assert (audits["legal_hold.released"].prior_state, audits["legal_hold.released"].new_state,
            audits["legal_hold.released"].record_seq) == ("ACTIVE", "RELEASED", released.record_seq)
    assert audits["legal_hold.released"].tenant_id == TENANT and "matter closed" in audits["legal_hold.released"].reason
    out = {o.event_type: o for o in _rows(OutboxEvent, tenant_id=TENANT)
           if o.payload.get("hold_id") == hold_id}
    assert out["evidence.hold_placed"].record_seq == placed.record_seq
    rel = out["privacy.legal_hold_released"]
    assert (rel.record_seq, rel.aggregate_type, rel.aggregate_id) == (released.record_seq, "LegalHold", hold_id)
    assert rel.payload == {"hold_id": hold_id, "evidence_id": eid}  # identifiers only: no reason text, no evidence
    (h,) = _rows(LegalHold, hold_id=hold_id)
    assert h.status == "RELEASED" and h.released_at is not None
    assert client.post(f"/api/evidence/{eid}/release-hold", headers=H("actor-gc"), json={"reason": "again"}).status_code == 409


def test_a_failed_hold_placement_leaves_no_projection_event_audit_or_outbox(client, monkeypatch):
    """DAT-REQ-014: the projection, event, audit and outbox rows commit together or not at all."""
    from test_authorization import db_fingerprint
    from app.engines import evidence as evd
    eid = _evidence(client)
    before = db_fingerprint()

    def boom(*a, **k):
        raise RuntimeError("injected ledger failure after the hold was written")
    monkeypatch.setattr(evd, "append_ledger_event", boom)
    with pytest.raises(RuntimeError, match="injected"):
        client.post(f"/api/evidence/{eid}/hold", headers=H("actor-gc"), json={"reason": "probe", "authority": "GC"})
    assert db_fingerprint() == before


def test_history_endpoints_label_migration_created_events(client):
    """Migration 0008's BASELINE rows are labelled as migration-created; events the application writes are RECORDED."""
    from app.db import AdminSession
    from app.models import LegalHold, LegalHoldEvent
    eid = _evidence(client)
    hold_id = _place_and_release(client, eid)
    with AdminSession() as db:  # a row exactly as migration 0008 writes it (append-only: it stays)
        h = db.get(LegalHold, hold_id)
        db.add(LegalHoldEvent(hold_id=h.hold_id, tenant_id=h.tenant_id, evidence_id=h.evidence_id, event_type="BASELINE",
                              status=h.status, authority=h.authority, actor_id=h.placed_by, reason=h.reason, record_seq=0,
                              created_at=h.placed_at))
        db.commit()
    hist = [e for e in client.get(f"/api/evidence/{eid}/holds/events", headers=H("actor-cco")).json() if e["hold_id"] == hold_id]
    kinds = {e["event_type"]: e["provenance"] for e in hist}
    assert kinds["HOLD_PLACED"] == kinds["HOLD_RELEASED"] == {"kind": "RECORDED"}
    assert kinds["BASELINE"]["kind"] == "MIGRATION_BASELINE" and kinds["BASELINE"]["recorded_by"] == "system:migration:0008"


def test_the_application_never_writes_baseline_events():
    """BASELINE is migration 0008's marker; if application code could write it, the label above would be unreliable."""
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    offenders = [p.name for p in app.rglob("*.py") if p.name != "models.py" and p.name != "governance.py"
                 and '"BASELINE"' in p.read_text(encoding="utf-8")]
    gov = (app / "engines" / "governance.py").read_text(encoding="utf-8")
    assert offenders == [] and gov.count('"BASELINE"') == 1  # the one comparison in event_view


# ------------------------------------------------------------------------------------------------ PostgreSQL below the API
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, admin_engine, engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine, engine


def _refused(conn, sql, params, match):
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError, match=match):
        conn.execute(text(sql), params)
    sp.rollback()


def test_runtime_role_may_update_only_lifecycle_columns_and_never_delete(pg):
    owner, _ = pg
    with owner.connect() as c:
        for t, cols in LIFECYCLE.items():
            upd, dele = c.execute(text("SELECT has_table_privilege('assure_app', :t, 'UPDATE'), "
                                       "has_table_privilege('assure_app', :t, 'DELETE')"), {"t": t}).one()
            granted = set(c.execute(text(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = CAST(:t AS regclass) AND a.attnum > 0 "
                "AND NOT a.attisdropped AND has_column_privilege('assure_app', a.attrelid, a.attnum, 'UPDATE')"),
                {"t": t}).scalars())
            triggers = set(c.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) "
                                          "AND NOT tgisinternal"), {"t": t}).scalars())
            assert (upd, dele, granted) == (False, False, cols), t
            assert {"immutable_columns", "lifecycle_final"} <= triggers, t
            forced = c.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                                    "WHERE oid = CAST(:t AS regclass)"), {"t": t}).scalar()
            assert forced, t  # FORCE RLS untouched


def test_a_legal_hold_cannot_be_deleted_rewritten_or_reopened(client, pg):
    owner, runtime = pg
    eid = _evidence(client)
    hold_id = _place_and_release(client, eid)
    k = {"h": hold_id}
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        _refused(c, "DELETE FROM evidence_legal_hold_revision WHERE hold_id = :h", k, "permission denied")
        _refused(c, "UPDATE evidence_legal_hold_revision SET authority = 'nobody' WHERE hold_id = :h", k, "permission denied")
        _refused(c, "UPDATE evidence_legal_hold_revision SET placed_by = 'someone-else' WHERE hold_id = :h", k,
                 "permission denied")
        _refused(c, "UPDATE evidence_legal_hold_revision SET status = 'ACTIVE', released_at = NULL WHERE hold_id = :h", k,
                 "is final once status is RELEASED")
        stamp(c, OTHER)  # another organisation cannot even see it to change it
        assert c.execute(text("UPDATE evidence_legal_hold_revision SET status = 'RELEASED' WHERE hold_id = :h"), k).rowcount == 0
        tx.rollback()
    with owner.connect() as c:  # the owner role: refused below the privilege layer as well
        tx = c.begin()
        _refused(c, "DELETE FROM evidence_legal_hold_revision WHERE hold_id = :h", k, "append-only")
        _refused(c, "UPDATE evidence_legal_hold_revision SET reason = 'rewritten' WHERE hold_id = :h", k, "append-only except")
        tx.rollback()


def test_an_active_hold_is_released_only_through_the_lifecycle_columns(client, pg):
    _, runtime = pg
    eid = _evidence(client)
    _release_all(client, eid)
    assert client.post(f"/api/evidence/{eid}/hold", headers=H("actor-gc"),
                       json={"reason": "column probe", "authority": "General Counsel"}).status_code == 200
    hold_id = client.get(f"/api/evidence/{eid}/holds/events", headers=H("actor-cco")).json()[-1]["hold_id"]
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        assert c.execute(text("UPDATE evidence_legal_hold_revision SET status = 'RELEASED', released_at = now() "
                              "WHERE hold_id = :h"), {"h": hold_id}).rowcount == 1
        tx.rollback()  # what the event writer does, refused nothing; rolled back here so the API releases it below
    assert client.post(f"/api/evidence/{eid}/release-hold", headers=H("actor-gc"), json={"reason": "done"}).status_code == 200


def test_terminal_governance_states_are_frozen(client, pg):
    """A resolved conflict and a fulfilled occurrence cannot be reopened or refilled, even by the runtime role."""
    _, runtime = pg
    eid = _evidence(client)
    c1 = client.post("/api/evidence-conflicts", headers=H("actor-owner"),
                     json={"member_evidence_ids": [eid], "description": "freeze probe " + secrets.token_hex(3)}).json()
    assert client.post(f"/api/evidence-conflicts/{c1['conflict_id']}/resolve", headers=H("actor-cco"),
                       json={"reason": "source A is authoritative"}).status_code == 200
    with runtime.connect() as c:
        tx = c.begin()
        stamp(c, TENANT)
        _refused(c, "UPDATE evidence_conflict SET status = 'OPEN', disposition = NULL WHERE conflict_id = :i",
                 {"i": c1["conflict_id"]}, "is final once status is RESOLVED")
        occ = c.execute(text("SELECT occurrence_id FROM assurance_obligation_occurrence WHERE fulfilled_at IS NOT NULL "
                             "LIMIT 1")).scalar()
        if occ:
            _refused(c, "UPDATE assurance_obligation_occurrence SET fulfilled_at = NULL WHERE occurrence_id = :i",
                     {"i": occ}, "is final once fulfilled_at is")
        tx.rollback()
