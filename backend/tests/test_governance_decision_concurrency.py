"""Exception decisions and occurrence fulfilment decide on the row read under a lock (pre-push audit finding F3).

governance.decide_exception and governance.fulfil_occurrence used to check the state of a row they had read without a
lock (and that the route had already cached), then write. Two concurrent requests could both pass the check: an
APPROVE and a REJECT of one exception both committed (0010 freezes only REJECTED/REVOKED), and a second fulfilment
reached the 0010 lifecycle_final trigger and surfaced as a raw database error. They now lock the row
(SELECT ... FOR UPDATE), re-read it (populate_existing) and validate the transition on that state, like
decide_compensation and the issue transitions. These tests prove:

  * (SQLite and PostgreSQL) a decision taken on a stale cached row re-reads it and is refused with the established 409;
  * (PostgreSQL only: genuinely concurrent transactions and row locks) the second of two concurrent decisions waits
    for the first and is refused with 409; exactly one decision or fulfilment is recorded, with one event, one audit
    record and one outbox event, and the record sequence has no trace of the refused request.
SQLite has no row locks, so nothing here claims that SQLite proves the PostgreSQL locking.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from conftest import H

TENANT = "tenant-zoiko-group"
REQUESTER, APPROVER_1, APPROVER_2, OWNER = "actor-owner", "actor-cco", "actor-risk", "actor-owner"


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _lineage() -> str:
    from app.db import AdminSession
    from app.models import ObligationInstance
    with AdminSession() as db:
        return db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None) \
            .order_by(ObligationInstance.lineage_id).first().lineage_id


def _exception(client) -> str:
    # Valid outside the seeded Q3 2026 assurance period, so no other module's snapshot sees it.
    r = client.post("/api/exceptions", headers=H(REQUESTER), json={
        "target_type": "OBLIGATION", "target_id": _lineage(), "reason": "F3 concurrency probe",
        "valid_from": "2027-03-01T00:00:00Z", "valid_to": "2027-04-01T00:00:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "PENDING_APPROVAL"
    return r.json()["exception_id"]


def _occurrence(client) -> str:
    r = client.post(f"/api/obligations/{_lineage()}/occurrences", headers=H(OWNER),
                    json={"window_from": "2027-03-01T00:00:00Z", "due_at": "2027-03-31T00:00:00Z"})
    assert r.status_code == 200, r.text
    return r.json()["occurrence_id"]


def _evidence() -> str:
    from app.db import AdminSession
    from app.models import EvidenceArtifact
    with AdminSession() as db:
        return db.execute(select(EvidenceArtifact.evidence_id).where(EvidenceArtifact.tenant_id == TENANT)
                          .order_by(EvidenceArtifact.record_seq, EvidenceArtifact.evidence_id)).scalars().first()


def _decide(db, actor, exception_id, decision):
    from app.engines import governance
    from app.models import Actor
    return governance.decide_exception(db, TENANT, actor, db.get(Actor, actor), exception_id, decision, "F3 probe")


def _fulfil(db, actor, occurrence_id, evidence):
    from datetime import datetime, timezone

    from app.engines import governance
    from app.models import Actor
    return governance.fulfil_occurrence(db, TENANT, actor, db.get(Actor, actor), occurrence_id,
                                        datetime(2027, 3, 20, tzinfo=timezone.utc), [evidence])


def _records(object_id, audit_action, outbox_types) -> dict:
    from app.db import AdminSession
    from app.models import AuditEvent, ExceptionEvent, ObligationOccurrenceEvent, OutboxEvent
    from app.platform import current_watermark
    with AdminSession() as db:
        events = [(e.event_type, e.record_seq) for e in db.execute(select(ExceptionEvent).where(
            ExceptionEvent.exception_id == object_id).order_by(ExceptionEvent.record_seq)).scalars()] + \
            [(e.event_type, e.record_seq) for e in db.execute(select(ObligationOccurrenceEvent).where(
                ObligationOccurrenceEvent.occurrence_id == object_id).order_by(ObligationOccurrenceEvent.record_seq)).scalars()]
        audits = [a.record_seq for a in db.execute(select(AuditEvent).where(
            AuditEvent.object_id == object_id, AuditEvent.action == audit_action)).scalars()]
        outbox = [(o.event_type, o.record_seq) for o in db.execute(select(OutboxEvent).where(
            OutboxEvent.aggregate_id == object_id, OutboxEvent.event_type.in_(outbox_types))).scalars()]
        return {"events": events, "audit": audits, "outbox": outbox, "watermark": current_watermark(db)["record_seq"]}


# ------------------------------------------------------------------------------------------------ both backends
def test_a_decision_on_a_stale_cached_row_re_reads_it_and_is_refused(client):
    """The route loads the exception before calling the engine (ctx.scoped(db.get(...))). If another decision commits
    in between, the engine must decide on the committed state, not on that cached copy."""
    from app.db import SessionLocal, set_principal
    from app.models import ExceptionRecord
    xid = _exception(client)
    with SessionLocal() as stale:
        set_principal(stale, TENANT, APPROVER_2)
        # Cached and held, as the route holds it (the identity map is weak: an unreferenced copy would be re-queried).
        cached = stale.get(ExceptionRecord, xid)
        assert cached.state == "PENDING_APPROVAL"
        stale.commit()  # end the read transaction; the identity map keeps the stale copy (expire_on_commit=False)
        r = client.post(f"/api/exceptions/{xid}/decide", headers=H(APPROVER_1), json={"decision": "APPROVE"})
        assert r.status_code == 200, r.text
        with pytest.raises(HTTPException) as e:
            _decide(stale, APPROVER_2, xid, "REJECT")
        assert e.value.status_code == 409 and e.value.detail == "Invalid transition APPROVED -> REJECT"
        assert cached.state == "APPROVED"  # the held copy was refreshed by the locked re-read
        stale.rollback()
    rec = _records(xid, "exception.decide", ["exception.approved", "exception.rejected"])
    assert [t for t, _ in rec["events"]] == ["REQUESTED", "APPROVED"] and len(rec["audit"]) == 1


def test_a_fulfilment_on_a_stale_cached_row_re_reads_it_and_is_refused(client):
    from app.db import SessionLocal, set_principal
    from app.models import ObligationOccurrence
    oid, ev = _occurrence(client), _evidence()
    with SessionLocal() as stale:
        set_principal(stale, TENANT, OWNER)
        cached = stale.get(ObligationOccurrence, oid)  # held, as the route holds it
        assert cached.fulfilled_at is None
        stale.commit()
        r = client.post(f"/api/occurrences/{oid}/fulfil", headers=H(OWNER),
                        json={"fulfilled_at": "2027-03-20T00:00:00Z", "evidence_ids": [ev]})
        assert r.status_code == 200, r.text
        with pytest.raises(HTTPException) as e:
            _fulfil(stale, OWNER, oid, ev)
        assert e.value.status_code == 409 and e.value.detail == "Occurrence already fulfilled; history is immutable"
        stale.rollback()
    rec = _records(oid, "occurrence.fulfil", ["obligation.occurrence_fulfilled"])
    assert [t for t, _ in rec["events"]] == ["SCHEDULED", "FULFILLED"] and len(rec["audit"]) == 1


# ------------------------------------------------------------------------------------------------ PostgreSQL concurrency
def _in_thread(fn, out, key, *, actor, locked=None, hold=None):
    """Run ``fn(db)`` in its own transaction as ``actor``. With ``locked``/``hold``: signal once fn has run (its row
    lock is held), then keep the transaction open until ``hold`` is set, then commit."""
    from app.db import SessionLocal, set_principal

    def run():
        try:
            with SessionLocal() as db:
                set_principal(db, TENANT, actor)
                fn(db)
                if locked:
                    locked.set()
                if hold:
                    hold.wait(30)
                db.commit()
                out[key] = "ok"
        except HTTPException as e:
            out[key] = (e.status_code, e.detail)
        except Exception as e:  # noqa: BLE001 - a raw database error is exactly what must not happen
            out[key] = repr(e)
    t = threading.Thread(target=run)
    t.start()
    return t


def _race(first, second, *, actors):
    """``first`` takes its lock and holds its transaction open; ``second`` starts and must wait (it is still running
    after 1.5 s); then ``first`` commits and ``second`` finishes. Returns the outcomes and whether second waited."""
    out, locked, hold = {}, threading.Event(), threading.Event()
    t1 = _in_thread(first, out, "first", actor=actors[0], locked=locked, hold=hold)
    assert locked.wait(30), out
    t2 = _in_thread(second, out, "second", actor=actors[1])
    time.sleep(1.5)
    waited = t2.is_alive()
    hold.set()
    t1.join(60)
    t2.join(60)
    return out, waited


@pytest.mark.parametrize("decisions", [("APPROVE", "REJECT"), ("REJECT", "APPROVE"), ("APPROVE", "APPROVE")])
def test_concurrent_exception_decisions_record_exactly_one(client, pg, decisions):
    xid = _exception(client)
    out, waited = _race(lambda db: _decide(db, APPROVER_1, xid, decisions[0]),
                        lambda db: _decide(db, APPROVER_2, xid, decisions[1]), actors=(APPROVER_1, APPROVER_2))
    won = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}[decisions[0]]
    assert out["first"] == "ok" and waited, out
    assert out["second"] == (409, f"Invalid transition {won} -> {decisions[1]}"), out  # structured 409, no DB error
    rec = _records(xid, "exception.decide", ["exception.approved", "exception.rejected"])
    assert [t for t, _ in rec["events"]] == ["REQUESTED", won]
    decided_seq = rec["events"][1][1]
    assert rec["audit"] == [decided_seq] and rec["outbox"] == [(f"exception.{won.lower()}", decided_seq)]
    from app.db import AdminSession
    from app.models import ExceptionRecord
    with AdminSession() as db:
        assert db.get(ExceptionRecord, xid).state == won
    assert rec["watermark"] == decided_seq  # the refused request left no record sequence behind


def test_concurrent_fulfilments_record_exactly_one(client, pg):
    oid, ev = _occurrence(client), _evidence()
    out, waited = _race(lambda db: _fulfil(db, OWNER, oid, ev), lambda db: _fulfil(db, OWNER, oid, ev),
                        actors=(OWNER, OWNER))
    assert out["first"] == "ok" and waited, out
    assert out["second"] == (409, "Occurrence already fulfilled; history is immutable"), out
    rec = _records(oid, "occurrence.fulfil", ["obligation.occurrence_fulfilled"])
    assert [t for t, _ in rec["events"]] == ["SCHEDULED", "FULFILLED"]
    seq = rec["events"][1][1]
    assert rec["audit"] == [seq] and rec["outbox"] == [("obligation.occurrence_fulfilled", seq)]
    assert rec["watermark"] == seq


def test_concurrent_decisions_through_the_api_never_surface_a_database_error(client, pg):
    """Many simultaneous API decisions on one exception: one 200, every other a 409, no 500."""
    xid = _exception(client)
    barrier, codes = threading.Barrier(4), []

    def call(actor, decision):
        barrier.wait()
        codes.append(client.post(f"/api/exceptions/{xid}/decide", headers=H(actor),
                                 json={"decision": decision}).status_code)
    threads = [threading.Thread(target=call, args=a) for a in
               ((APPROVER_1, "APPROVE"), (APPROVER_2, "REJECT"), (APPROVER_1, "REJECT"), (APPROVER_2, "APPROVE"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(codes) == [200, 409, 409, 409], codes
    rec = _records(xid, "exception.decide", ["exception.approved", "exception.rejected"])
    assert len(rec["events"]) == 2 and len(rec["audit"]) == 1 and len(rec["outbox"]) == 1
