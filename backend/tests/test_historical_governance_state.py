"""Historical exception and occurrence state in assurance snapshots (pre-push audit finding F2).

ZA-TIM-001 §9-§10 and TIM-REQ-006/015, STA-REQ-021: a snapshot is a reproducible derivative of what Assure knew at its
record cutoff. Exception decisions and occurrence fulfilment are current-state projections updated in place
(assurance_exception_revision.state, assurance_obligation_occurrence.fulfilled_at/evidence_ids; migration 0010); their
history is in the append-only event tables (0008). These tests prove that a snapshot at an earlier cutoff reads that
history and never today's projection, that it does not depend on wall-clock time (the evaluation instant is explicit
and a persisted snapshot reproduces with its own), that a gap in the history is reported and never filled with the
current row (REQUIRES_REVIEW for an occurrence, ZA-TIM-001 §9 "unresolved temporal ambiguity"), and that another
organisation's events are never read.

Every test runs as the owner role in one transaction that is always rolled back, so nothing it plants persists.
Runs on SQLite and on PostgreSQL 17.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

TENANT = "tenant-zoiko-group"  # synthetic Customer Zero, seeded by the suite
OTHER = "tenant-f2-history-probe"
PERIOD = (datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))
LATER = datetime(2026, 11, 10, tzinfo=timezone.utc)  # an evaluation instant after the whole period
EARLY = datetime(2026, 8, 1, tzinfo=timezone.utc)    # an evaluation instant before the probe occurrence is due


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.fixture
def db(client):  # the client fixture has migrated (PostgreSQL) and seeded the database
    from app.db import admin_engine
    conn = admin_engine.connect()
    tx = conn.begin()
    session = Session(bind=conn, autoflush=False)
    try:
        yield session
    finally:
        session.close()
        tx.rollback()
        conn.close()


def _seq(db) -> int:
    from app.platform import current_watermark
    return current_watermark(db)["record_seq"]


def _dora(db) -> str:
    from app.models import ObligationDefinition, ObligationInstance
    odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
    return db.execute(select(ObligationInstance.lineage_id).where(
        ObligationInstance.tenant_id == TENANT, ObligationInstance.obligation_definition_id == odef.obligation_definition_id)
        .order_by(ObligationInstance.lineage_id)).scalars().first()


def _evidence(db) -> str:
    from app.models import EvidenceArtifact
    return db.execute(select(EvidenceArtifact.evidence_id).where(EvidenceArtifact.tenant_id == TENANT)
                      .order_by(EvidenceArtifact.record_seq, EvidenceArtifact.evidence_id)).scalars().first()


def _snapshot(db, lineage, cutoff, at=LATER) -> dict:
    from app.engines import assurance as asr
    from app.platform import canonical_json
    r = asr.compute_snapshot(db, tenant_id=TENANT, obligation_lineage_id=lineage, period_from=PERIOD[0],
                             period_to=PERIOD[1], record_cutoff_seq=cutoff, perspective="AS_KNOWN_THEN",
                             actor_id="actor-audit", persist=False, evaluated_at=at)
    return json.loads(canonical_json(r))


def _occurrence_results(snap, occurrence_id) -> list[tuple]:
    """(result, reason) of one occurrence in every segment trace where it is due."""
    found = []

    def walk(n):
        if isinstance(n, dict):
            occs = n.get("occurrences")
            for o in occs if isinstance(occs, list) else []:
                if isinstance(o, dict) and o.get("occurrence_id") == occurrence_id:
                    found.append((o["result"], o["reason"]))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    for seg in snap["segments"]:
        walk(seg["expression_trace"])
    return found


def _hist(snap, kind, key, ident) -> dict:
    return next(x for x in snap["historical_state"][kind] if x[key] == ident)


def _applied(snap, exception_id) -> bool:
    return any(t["exception_id"] == exception_id for s in snap["segments"] for t in s["exception_treatments"])


def _request(db, lineage, start="2026-08-01", end="2026-09-15"):
    from app.engines import governance
    x, _ = governance.request_exception(db, tenant_id=TENANT, actor_id="actor-owner", target_type="OBLIGATION",
                                        target_id=lineage, reason="F2 probe", valid_from=dt(start), valid_to=dt(end))
    db.flush()
    return x


def _decide(db, x, decision, actor="actor-cco"):
    from app.engines import governance
    from app.models import Actor
    governance.decide_exception(db, TENANT, actor, db.get(Actor, actor), x.exception_id, decision, "F2 probe decision")
    db.flush()


def _schedule(db, lineage, due="2026-08-15"):
    from app.engines import governance
    o, _ = governance.schedule_occurrence(db, tenant_id=TENANT, actor_id="actor-owner", obligation_lineage_id=lineage,
                                          window_from=dt("2026-07-01"), due_at=dt(due))
    db.flush()
    return o


def _fulfil(db, o, evidence, when="2026-08-10"):
    from app.engines import governance
    from app.models import Actor
    governance.fulfil_occurrence(db, TENANT, "actor-owner", db.get(Actor, "actor-owner"), o.occurrence_id, dt(when),
                                 [evidence])
    db.flush()


def _occurrences_are_evaluated(snap) -> bool:
    return any("occurrences" in json.dumps(s["expression_trace"]) for s in snap["segments"])


# ------------------------------------------------------------------------------------------------ exceptions
def test_an_exception_approved_after_the_cutoff_is_pending_at_the_cutoff(db):
    lineage = _dora(db)
    x = _request(db, lineage)
    k1 = _seq(db)
    _decide(db, x, "APPROVE")
    k2 = _seq(db)
    assert x.state == "APPROVED"  # today's projection
    before, after = _snapshot(db, lineage, k1), _snapshot(db, lineage, k2)
    h1 = _hist(before, "exceptions", "exception_id", x.exception_id)
    assert (h1["state"], h1["source"], h1["problem"]) == ("PENDING_APPROVAL", "EVENT_HISTORY", None)
    assert not _applied(before, x.exception_id)  # the later approval is not treated as known at k1
    assert next(t for t in before["exception_treatments"] if t["exception_id"] == x.exception_id)["state"] == "PENDING_APPROVAL"
    assert _hist(after, "exceptions", "exception_id", x.exception_id)["state"] == "APPROVED"
    assert _applied(after, x.exception_id)


@pytest.mark.parametrize("later", ["REJECT", "REVOKE"])
def test_an_exception_rejected_or_revoked_after_the_cutoff_keeps_its_state_at_the_cutoff(db, later):
    lineage = _dora(db)
    x = _request(db, lineage)
    if later == "REVOKE":
        _decide(db, x, "APPROVE")
    k1 = _seq(db)
    _decide(db, x, later, actor="actor-risk")
    k2 = _seq(db)
    at_k1 = "APPROVED" if later == "REVOKE" else "PENDING_APPROVAL"
    before, after = _snapshot(db, lineage, k1), _snapshot(db, lineage, k2)
    assert _hist(before, "exceptions", "exception_id", x.exception_id)["state"] == at_k1
    assert _applied(before, x.exception_id) is (at_k1 == "APPROVED")
    assert _hist(after, "exceptions", "exception_id", x.exception_id)["state"] == ("REVOKED" if later == "REVOKE" else "REJECTED")
    assert not _applied(after, x.exception_id)


def test_an_exception_without_history_is_reported_and_never_read_from_the_row(db):
    from app.models import ExceptionRecord
    from app.platform import next_record_seq
    lineage = _dora(db)
    x = ExceptionRecord(tenant_id=TENANT, target_type="OBLIGATION", target_id=lineage, reason="no history",
                        state="APPROVED", requested_by="actor-owner", approved_by="actor-cco",
                        valid_from=dt("2026-08-01"), valid_to=dt("2026-09-01"), record_seq=next_record_seq(db))
    db.add(x)
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    h = _hist(snap, "exceptions", "exception_id", x.exception_id)
    assert (h["state"], h["problem"]) == (None, "EXCEPTION_HISTORY_MISSING")
    assert not _applied(snap, x.exception_id)  # the row says APPROVED; that is not history
    t = next(t for t in snap["exception_treatments"] if t["exception_id"] == x.exception_id)
    assert t["state"] is None and t["history_problem"] == "EXCEPTION_HISTORY_MISSING"
    assert "EXCEPTION_HISTORY_MISSING" in snap["history_problems"] and "EXCEPTION_HISTORY_MISSING" in snap["reason_codes"]


@pytest.mark.parametrize("defect", ["broken_chain", "disallowed_transition", "duplicate_seq", "projection_disagrees"])
def test_an_inconsistent_exception_history_is_reported(db, defect):
    from app.models import ExceptionEvent
    from app.platform import next_record_seq
    lineage = _dora(db)
    x = _request(db, lineage)
    ev = dict(exception_id=x.exception_id, tenant_id=TENANT, actor_id="actor-cco", reason="planted",
              target_type=x.target_type, target_id=x.target_id, valid_from=x.valid_from, valid_to=x.valid_to)
    if defect == "broken_chain":
        db.add(ExceptionEvent(event_type="REVOKED", from_state="APPROVED", to_state="REVOKED",
                              record_seq=next_record_seq(db), **ev))
    elif defect == "disallowed_transition":
        db.add(ExceptionEvent(event_type="APPROVED", from_state="PENDING_APPROVAL", to_state="APPROVED",
                              record_seq=next_record_seq(db), **ev))
        db.add(ExceptionEvent(event_type="PENDING_APPROVAL", from_state="APPROVED", to_state="PENDING_APPROVAL",
                              record_seq=next_record_seq(db), **ev))
    elif defect == "duplicate_seq":
        db.add(ExceptionEvent(event_type="APPROVED", from_state="PENDING_APPROVAL", to_state="APPROVED",
                              record_seq=x.record_seq, **ev))
    else:  # the full history says PENDING_APPROVAL, the projection says APPROVED
        x.state = "APPROVED"
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    h = _hist(snap, "exceptions", "exception_id", x.exception_id)
    assert (h["state"], h["problem"]) == (None, "EXCEPTION_HISTORY_INCONSISTENT")
    assert not _applied(snap, x.exception_id)


# ------------------------------------------------------------------------------------------------ occurrences
def test_an_occurrence_fulfilled_after_the_cutoff_is_unfulfilled_at_the_cutoff(db):
    lineage = _dora(db)
    evidence = _evidence(db)
    o = _schedule(db, lineage)
    k1 = _seq(db)
    _fulfil(db, o, evidence)
    k2 = _seq(db)
    assert o.fulfilled_at is not None and o.evidence_ids == [evidence]  # today's projection
    before, after = _snapshot(db, lineage, k1), _snapshot(db, lineage, k2)
    assert _occurrences_are_evaluated(before), "the DORA expression must evaluate occurrences for this proof"
    h1, h2 = (_hist(s, "occurrences", "occurrence_id", o.occurrence_id) for s in (before, after))
    assert (h1["fulfilled_at"], h1["evidence_ids"], h1["source"], h1["problem"]) == (None, [], "EVENT_HISTORY", None)
    assert h2["fulfilled_at"] is not None and h2["evidence_ids"] == [evidence]  # evidence references as of k2 only
    assert set(_occurrence_results(before, o.occurrence_id)) == {("UNSATISFIED", "OCCURRENCE_MISSED")}
    assert set(_occurrence_results(after, o.occurrence_id)) == {("SATISFIED", None)}


def test_the_evaluation_instant_decides_pending_or_missed_and_is_echoed(db):
    lineage = _dora(db)
    o = _schedule(db, lineage)
    k = _seq(db)
    early, late = _snapshot(db, lineage, k, at=EARLY), _snapshot(db, lineage, k, at=LATER)
    assert set(_occurrence_results(early, o.occurrence_id)) <= {("UNKNOWN", "OCCURRENCE_PENDING")}
    assert set(_occurrence_results(late, o.occurrence_id)) == {("UNSATISFIED", "OCCURRENCE_MISSED")}
    assert early["evaluation_instant"].startswith("2026-08-01") and early["evaluation_instant_source"] == "SNAPSHOT_CREATED_AT"


def test_an_occurrence_disputed_after_the_cutoff_is_not_disputed_at_the_cutoff(db):
    from app.models import ObligationOccurrenceEvent
    from app.platform import next_record_seq
    lineage = _dora(db)
    evidence = _evidence(db)
    o = _schedule(db, lineage)
    _fulfil(db, o, evidence)
    k1 = _seq(db)
    db.add(ObligationOccurrenceEvent(occurrence_id=o.occurrence_id, tenant_id=TENANT, obligation_lineage_id=lineage,
                                     event_type="DISPUTED", actor_id="actor-audit", window_from=o.window_from,
                                     due_at=o.due_at, fulfilled_at=o.fulfilled_at, evidence_ids=list(o.evidence_ids),
                                     is_late=False, record_seq=next_record_seq(db)))
    db.flush()
    k2 = _seq(db)
    before, after = _snapshot(db, lineage, k1), _snapshot(db, lineage, k2)
    assert _hist(before, "occurrences", "occurrence_id", o.occurrence_id)["disputed"] is False
    assert set(_occurrence_results(before, o.occurrence_id)) == {("SATISFIED", None)}
    assert _hist(after, "occurrences", "occurrence_id", o.occurrence_id)["disputed"] is True
    assert set(_occurrence_results(after, o.occurrence_id)) == {("REVIEW", "SOURCE_CONFLICT")}


def test_changed_evidence_references_are_never_read_from_the_projection(db):
    """The fulfilment's evidence references are those its FULFILLED event recorded. On PostgreSQL the fulfilled row
    cannot be changed at all (0010 lifecycle_final); where it could be (SQLite), the snapshot keeps the recorded
    references and reports the disagreement instead of using the changed row."""
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    from app.db import IS_POSTGRES
    from app.models import ObligationOccurrence
    lineage = _dora(db)
    evidence = _evidence(db)
    o = _schedule(db, lineage)
    _fulfil(db, o, evidence)
    stmt = update(ObligationOccurrence).where(ObligationOccurrence.occurrence_id == o.occurrence_id) \
        .values(evidence_ids=["ev-substituted"])
    if IS_POSTGRES:
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(stmt)
        return
    db.execute(stmt)
    db.expire_all()
    snap = _snapshot(db, lineage, _seq(db))
    h = _hist(snap, "occurrences", "occurrence_id", o.occurrence_id)
    assert h["problem"] == "OCCURRENCE_HISTORY_INCONSISTENT" and "ev-substituted" not in json.dumps(snap)
    assert set(_occurrence_results(snap, o.occurrence_id)) == {("REVIEW", "OCCURRENCE_HISTORY_INCONSISTENT")}


def test_occurrence_history_gaps_require_review_and_an_unfulfilled_row_without_events_is_exact(db):
    from app.models import ObligationOccurrence
    from app.platform import next_record_seq
    lineage = _dora(db)
    rows = {}
    for name, fulfilled in (("fulfilled", dt("2026-08-10")), ("open", None)):
        rows[name] = ObligationOccurrence(tenant_id=TENANT, obligation_lineage_id=lineage, window_from=dt("2026-07-01"),
                                          due_at=dt("2026-08-15"), fulfilled_at=fulfilled,
                                          evidence_ids=[_evidence(db)] if fulfilled else [], record_seq=next_record_seq(db))
        db.add(rows[name])
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    gap = _hist(snap, "occurrences", "occurrence_id", rows["fulfilled"].occurrence_id)
    assert (gap["problem"], gap["fulfilled_at"]) == ("OCCURRENCE_HISTORY_MISSING", None)
    assert set(_occurrence_results(snap, rows["fulfilled"].occurrence_id)) == {("REVIEW", "OCCURRENCE_HISTORY_MISSING")}
    assert "OCCURRENCE_HISTORY_MISSING" in snap["history_problems"]
    assert snap["state"] != "PROVEN"  # a gap is never proof (FAILING from the missed one outranks REQUIRES_REVIEW)
    exact = _hist(snap, "occurrences", "occurrence_id", rows["open"].occurrence_id)
    assert (exact["source"], exact["problem"]) == ("NO_EVENTS", None)
    assert set(_occurrence_results(snap, rows["open"].occurrence_id)) == {("UNSATISFIED", "OCCURRENCE_MISSED")}


def test_a_second_fulfilment_in_the_history_is_inconsistent(db):
    from app.models import ObligationOccurrenceEvent
    from app.platform import next_record_seq
    lineage = _dora(db)
    evidence = _evidence(db)
    o = _schedule(db, lineage)
    _fulfil(db, o, evidence)
    db.add(ObligationOccurrenceEvent(occurrence_id=o.occurrence_id, tenant_id=TENANT, obligation_lineage_id=lineage,
                                     event_type="FULFILLED", actor_id="actor-owner", window_from=o.window_from,
                                     due_at=o.due_at, fulfilled_at=dt("2026-08-12"), evidence_ids=[evidence],
                                     is_late=False, record_seq=next_record_seq(db)))
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    assert _hist(snap, "occurrences", "occurrence_id", o.occurrence_id)["problem"] == "OCCURRENCE_HISTORY_INCONSISTENT"


def test_a_migration_baseline_is_used_but_labelled_as_such(db):
    """Rows that existed before migration 0008 have one BASELINE event (state at migration time, stamped with the
    row's record_seq). Their state is taken from it and labelled BASELINE_0008, so nobody reads it as an observed event."""
    from app.models import ExceptionEvent, ExceptionRecord, ObligationOccurrence, ObligationOccurrenceEvent
    from app.platform import next_record_seq
    lineage = _dora(db)
    seq = next_record_seq(db)
    x = ExceptionRecord(tenant_id=TENANT, target_type="OBLIGATION", target_id=lineage, reason="pre-0008", state="APPROVED",
                        requested_by="actor-owner", approved_by="actor-cco", valid_from=dt("2026-08-01"),
                        valid_to=dt("2026-09-01"), record_seq=seq)
    o = ObligationOccurrence(tenant_id=TENANT, obligation_lineage_id=lineage, window_from=dt("2026-07-01"),
                             due_at=dt("2026-08-15"), fulfilled_at=dt("2026-08-10"), evidence_ids=[_evidence(db)],
                             record_seq=seq)
    db.add_all([x, o])
    db.flush()
    db.add(ExceptionEvent(exception_id=x.exception_id, tenant_id=TENANT, event_type="BASELINE", from_state=None,
                          to_state="APPROVED", actor_id="actor-owner", reason="Migration 0008 baseline snapshot",
                          target_type=x.target_type, target_id=x.target_id, valid_from=x.valid_from,
                          valid_to=x.valid_to, record_seq=seq))
    db.add(ObligationOccurrenceEvent(occurrence_id=o.occurrence_id, tenant_id=TENANT, obligation_lineage_id=lineage,
                                     event_type="BASELINE", actor_id="system:migration:0008", window_from=o.window_from,
                                     due_at=o.due_at, fulfilled_at=o.fulfilled_at, evidence_ids=list(o.evidence_ids),
                                     is_late=False, record_seq=seq))
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    hx, ho = _hist(snap, "exceptions", "exception_id", x.exception_id), _hist(snap, "occurrences", "occurrence_id", o.occurrence_id)
    assert (hx["state"], hx["source"], hx["problem"]) == ("APPROVED", "BASELINE_0008", None)
    assert (ho["source"], ho["problem"]) == ("BASELINE_0008", None) and ho["fulfilled_at"] is not None
    assert set(_occurrence_results(snap, o.occurrence_id)) == {("SATISFIED", None)}


# ------------------------------------------------------------------------------------------------ determinism, tenancy
def test_identical_historical_inputs_give_identical_results_after_later_changes(db):
    lineage = _dora(db)
    evidence = _evidence(db)
    x = _request(db, lineage)
    o = _schedule(db, lineage)
    k = _seq(db)
    first = _snapshot(db, lineage, k)
    assert _snapshot(db, lineage, k) == first
    _decide(db, x, "APPROVE")
    _fulfil(db, o, evidence)
    assert _snapshot(db, lineage, k) == first  # later decisions and fulfilment change nothing at cutoff k


def test_identical_requests_without_a_fixed_instant_stay_byte_identical(db):
    """The provenance this fix adds is itself deterministic: no request-time value is echoed into the output."""
    from app.engines import assurance as asr
    from app.platform import canonical_json
    lineage = _dora(db)
    k = _seq(db)
    runs = [canonical_json(asr.compute_snapshot(db, tenant_id=TENANT, obligation_lineage_id=lineage, period_from=PERIOD[0],
                                                period_to=PERIOD[1], record_cutoff_seq=k, perspective="AS_KNOWN_THEN",
                                                actor_id="actor-audit", persist=False)) for _ in range(2)]
    assert runs[0] == runs[1]
    assert json.loads(runs[0])["evaluation_instant_source"] == "REQUEST_TIME" and "evaluation_instant" not in json.loads(runs[0])


def test_a_persisted_snapshot_reproduces_with_its_own_evaluation_instant(db):
    from app.engines import assurance as asr
    from app.models import AssuranceSnapshot
    lineage = _dora(db)
    evidence = _evidence(db)
    o = _schedule(db, lineage, due="2026-09-29")
    made = asr.compute_snapshot(db, tenant_id=TENANT, obligation_lineage_id=lineage, period_from=PERIOD[0],
                                period_to=PERIOD[1], record_cutoff_seq=None, perspective="RECONSTRUCTED_NOW",
                                actor_id="actor-audit", persist=True, evaluated_at=EARLY.replace(month=9, day=1))
    from app.platform import ensure_utc
    snap = db.get(AssuranceSnapshot, made["snapshot_id"])
    assert ensure_utc(snap.created_at) == made["evaluation_instant"]  # the stored instant is the one the result used
    _fulfil(db, o, evidence, when="2026-09-20")
    again = asr.compute_snapshot(db, tenant_id=TENANT, obligation_lineage_id=lineage, period_from=snap.period_from,
                                 period_to=snap.period_to, record_cutoff_seq=snap.record_cutoff_seq,
                                 perspective=snap.perspective, actor_id="actor-audit", persist=False,
                                 evaluated_at=snap.created_at)
    assert (again["state"], again["dependency_digest"]) == (snap.state, snap.dependency_digest)
    # Segment by segment (a persisted run records control evaluation IDs that a rebuild does not; the digests exclude them).
    key = lambda r: [(s["segment_range"], s["assurance_state"], s["dependency_digest"]) for s in r["segments"]]  # noqa: E731
    assert key(again) == key(made)


def test_another_organisations_events_are_never_read(db):
    from app.models import ExceptionEvent, Tenant
    from app.platform import next_record_seq
    if db.get(Tenant, OTHER) is None:
        db.add(Tenant(tenant_id=OTHER, legal_name="F2 history probe", contracting_region="UK"))
        db.flush()
    lineage = _dora(db)
    x = _request(db, lineage)
    db.add(ExceptionEvent(exception_id=x.exception_id, tenant_id=OTHER, event_type="APPROVED",
                          from_state="PENDING_APPROVAL", to_state="APPROVED", actor_id="actor-cco", reason="foreign",
                          target_type=x.target_type, target_id=x.target_id, valid_from=x.valid_from,
                          valid_to=x.valid_to, record_seq=next_record_seq(db)))
    db.flush()
    snap = _snapshot(db, lineage, _seq(db))
    h = _hist(snap, "exceptions", "exception_id", x.exception_id)
    assert (h["state"], h["problem"]) == ("PENDING_APPROVAL", None) and not _applied(snap, x.exception_id)


def test_the_seeded_snapshot_reproduces_through_the_api(client):
    from conftest import H
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    with AdminSession() as s:
        snap = s.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == TENANT)
                         .order_by(AssuranceSnapshot.created_at, AssuranceSnapshot.snapshot_id)).scalars().first()
        sid, created = snap.snapshot_id, snap.created_at
    r = client.get(f"/api/snapshots/{sid}/reproduce", headers=H("actor-audit"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reproducible"] is True and body["history_problems"] == []
    assert datetime.fromisoformat(body["evaluation_instant"].replace("Z", "+00:00")) == \
        (created if created.tzinfo else created.replace(tzinfo=timezone.utc))
