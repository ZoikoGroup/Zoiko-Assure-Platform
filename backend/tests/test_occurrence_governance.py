"""Obligation occurrence governance: scheduling and fulfilment rules, isolation, audit/outbox consistency and
historical reconstruction (engines/governance.py schedule_occurrence / fulfil_occurrence; routers/assurance.py).

Requirements exercised:
  * ZA-STA-001 §13 (REQUIRED_WINDOW; FULFILLMENT_EVENT "completion event with evidence"; WAIVER / EXTENSION "never an
    ad hoc due-date edit"), STA-REQ-027 (each occurrence preserved, never overwritten);
  * ZA-DOM-001 §6.2/§6.3 (occurrence identity; CLOSED "Duty ended and no future occurrence remains");
  * ZA-DAT-001 §07 assurance.obligation_occurrence ("fulfillment refs"); ZA-EVT-001 obligation.occurrence_completed
    ("Occurrence fulfillment recorded with evidence refs");
  * ZA-TIM-001 TIM-REQ-002 (non-empty half-open ranges), TIM-REQ-006/015 (historical reads at a record cutoff);
  * ZA-TEN-001 TEN-REQ-008 / §14 (no cross-entity proof without a governed shared-evidence reference), DOM-INV-11.

Waiver, extension and a governed dispute workflow are NOT implemented: no table, lifecycle, authority or event contract
for them exists in the controlled specifications (blocker). The test below only proves no route edits a due date.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-og-{TAG}", f"tenant-ogb-{TAG}"
ENT, SUBJ, ENT2, SUBJ2 = f"entity-og-{TAG}", f"subj-og-{TAG}", f"entity-og2-{TAG}", f"subj-og2-{TAG}"
ENT_B, SUBJ_B = f"entity-ogb-{TAG}", f"subj-ogb-{TAG}"
OWNER, CCO, B_OWNER = f"og-owner-{TAG}", f"og-cco-{TAG}", f"ogb-owner-{TAG}"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        for tid, eid, sid in ((TA, ENT, SUBJ), (TA, ENT2, SUBJ2), (TB, ENT_B, SUBJ_B)):
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{eid} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"{eid} AG"))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _obligation(tenant=TA, entity=ENT, subject=SUBJ, lifecycle="ADOPTED", valid_to=None):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, new_id
    from app.platform import next_record_seq
    lineage = new_id()
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=tenant, entity_id=entity, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle=lifecycle, valid_from=T0, valid_to=valid_to,
                                  record_from_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _evidence(client, entity=ENT, actor=OWNER):
    r = client.post("/api/evidence/upload", headers=H(actor), files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Filing receipt", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-10-01T00:00:00Z", "validity_days": "400"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _schedule(client, lineage, window_from="2026-09-01T00:00:00Z", due_at="2026-09-30T00:00:00Z", actor=OWNER):
    return client.post(f"/api/obligations/{lineage}/occurrences", headers=H(actor),
                       json={"window_from": window_from, "due_at": due_at})


def _fulfil(client, oid, evidence, actor=OWNER, at="2026-09-20T00:00:00Z"):
    return client.post(f"/api/occurrences/{oid}/fulfil", headers=H(actor),
                       json={"fulfilled_at": at, "evidence_ids": evidence})


def _seq():
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _writes(oid):
    """Everything an occurrence decision writes: its events, audit records, outbox events and the cell sequence."""
    from app.db import AdminSession
    from app.models import AuditEvent, ObligationOccurrenceEvent, OutboxEvent
    with AdminSession() as db:
        def n(model, *where):
            return db.execute(select(func.count()).select_from(model).where(*where)).scalar()
        return {"events": n(ObligationOccurrenceEvent, ObligationOccurrenceEvent.occurrence_id == oid),
                "audit": n(AuditEvent, AuditEvent.object_id == oid),
                "outbox": n(OutboxEvent, OutboxEvent.aggregate_id == oid), "seq": _seq()}


def _detail(r):
    d = r.json()["detail"]
    return d["error"] if isinstance(d, dict) else d


# ------------------------------------------------------------------------------------------------ scheduling
def test_an_occurrence_needs_a_non_empty_window_and_records_its_history(client, org):
    lineage = _obligation()
    for window_from, due_at in (("2026-09-30T00:00:00Z", "2026-09-30T00:00:00Z"),
                                ("2026-10-01T00:00:00Z", "2026-09-30T00:00:00Z")):
        before = _seq()
        r = _schedule(client, lineage, window_from, due_at)
        assert r.status_code == 422 and _detail(r) == "OCCURRENCE_WINDOW_EMPTY", r.text
        assert _seq() == before
    r = _schedule(client, lineage)
    assert r.status_code == 200, r.text
    oid = r.json()["occurrence_id"]
    assert _writes(oid) == {**_writes(oid), "events": 1, "audit": 1, "outbox": 1}


def test_no_occurrence_falls_due_after_the_obligation_was_closed(client, org):
    lineage = _obligation(lifecycle="CLOSED", valid_to=datetime(2026, 10, 1, tzinfo=timezone.utc))
    r = _schedule(client, lineage, "2026-10-01T00:00:00Z", "2026-10-31T00:00:00Z")
    assert r.status_code == 409 and _detail(r) == "OBLIGATION_CLOSED", r.text
    # A duty that fell due while the obligation still applied can still be recorded.
    assert _schedule(client, lineage, "2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z").status_code == 200


def test_no_route_edits_a_due_date(client, org):
    """STA §13: a waiver or extension is a governed valid-time object, "never an ad hoc due-date edit". The only
    occurrence mutations are scheduling a new occurrence and recording its fulfilment."""
    from app.main import ROUTERS
    from app.security import operations
    mutating = sorted((op.path, op.method) for op in operations(ROUTERS, "/api")
                      if "occurrence" in op.path and op.method in ("POST", "PUT", "PATCH", "DELETE"))
    assert mutating == [("/api/obligations/{lineage}/occurrences", "POST"),
                        ("/api/occurrences/{occurrence_id}/fulfil", "POST")]


# ------------------------------------------------------------------------------------------------ fulfilment
def test_fulfilment_needs_this_entitys_evidence_and_writes_nothing_when_refused(client, org):
    lineage = _obligation()
    oid = _schedule(client, lineage).json()["occurrence_id"]
    own = _evidence(client)
    before = _writes(oid)
    for evidence, status, error in (([], 422, "FULFILMENT_EVIDENCE_REQUIRED"),
                                    ([own, own], 422, "DUPLICATE_EVIDENCE"),
                                    ([_evidence(client, ENT2)], 422, "EVIDENCE_SCOPE_MISMATCH"),
                                    ([own, _evidence(client, ENT2)], 422, "EVIDENCE_SCOPE_MISMATCH")):
        r = _fulfil(client, oid, evidence)
        assert r.status_code == status and _detail(r) == error, (evidence, r.text)
    b_evidence = _evidence(client, ENT_B, B_OWNER)
    r = _fulfil(client, oid, [b_evidence])
    assert r.status_code == 404  # another organisation's record reads exactly like one that does not exist
    after = _writes(oid)
    assert {k: after[k] for k in ("events", "audit", "outbox")} == {k: before[k] for k in ("events", "audit", "outbox")}
    r = _fulfil(client, oid, [own])
    assert r.status_code == 200 and r.json()["evidence_ids"] == [own], r.text
    done = _writes(oid)
    assert (done["events"], done["audit"], done["outbox"]) == (before["events"] + 1, before["audit"] + 1, before["outbox"] + 1)
    again = _fulfil(client, oid, [own])
    assert again.status_code == 409 and _writes(oid) == done  # history is never overwritten (STA-REQ-027)


def test_another_organisation_cannot_schedule_fulfil_or_read_an_occurrence(client, org):
    lineage = _obligation()
    oid = _schedule(client, lineage).json()["occurrence_id"]
    assert _schedule(client, lineage, actor=B_OWNER).status_code in (403, 404)
    assert _fulfil(client, oid, [_evidence(client, ENT_B, B_OWNER)], actor=B_OWNER).status_code in (403, 404)
    assert client.get(f"/api/occurrences/{oid}/events", headers=H(B_OWNER)).status_code in (403, 404)
    assert _writes(oid)["events"] == 1


@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_concurrent_fulfilments_record_exactly_one(client, org, pg):
    import threading

    from fastapi import HTTPException

    from app.db import SessionLocal, set_principal
    from app.engines import governance
    from app.models import Actor
    lineage = _obligation()
    oid = _schedule(client, lineage).json()["occurrence_id"]
    evidence = [_evidence(client), _evidence(client), _evidence(client)]
    barrier, outcomes = threading.Barrier(3), []

    def fulfil(e):
        with SessionLocal() as db:
            set_principal(db, TA, OWNER)
            barrier.wait(20)
            try:
                governance.fulfil_occurrence(db, TA, OWNER, db.get(Actor, OWNER), oid,
                                             datetime(2026, 9, 20, tzinfo=timezone.utc), [e])
                db.commit()
                outcomes.append(200)
            except HTTPException as x:
                db.rollback()
                outcomes.append(x.status_code)
    threads = [threading.Thread(target=fulfil, args=(e,)) for e in evidence]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(outcomes) == [200, 409, 409], outcomes
    w = _writes(oid)
    assert (w["events"], w["audit"], w["outbox"]) == (2, 2, 2)  # SCHEDULED + exactly one FULFILLED, each audited once


# ------------------------------------------------------------------------------------------------ history
def test_a_historical_dossier_shows_the_occurrence_as_recorded_then_and_reads_write_nothing(client, org):
    lineage = _obligation()
    oid = _schedule(client, lineage).json()["occurrence_id"]
    scheduled = _seq()
    e = _evidence(client)
    assert _fulfil(client, oid, [e]).status_code == 200
    fulfilled = _seq()
    before = _writes(oid)

    def at(seq):
        r = client.get(f"/api/obligations/{lineage}", headers=H(CCO), params={"as_of_record_seq": seq})
        assert r.status_code == 200, r.text
        [o] = [x for x in r.json()["occurrences"] if x["occurrence_id"] == oid]
        return o
    assert (at(scheduled)["fulfilled_at"], at(scheduled)["evidence_ids"]) == (None, [])
    assert at(fulfilled)["evidence_ids"] == [e]
    assert _writes(oid) == before
    r = client.get(f"/api/obligations/{lineage}", headers=H(B_OWNER), params={"as_of_record_seq": scheduled})
    assert r.status_code in (403, 404)
