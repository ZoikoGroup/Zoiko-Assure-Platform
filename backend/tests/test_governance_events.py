"""Stage 3c Append-Only Governance Event Tables Verification Suite (Task 4.2; ZA-DAT-001, ZA-STA-001).

Tests the durability, immutability, tenant isolation, and lifecycle fidelity of the six append-only governance
event tables:
1. assurance_exception_event
2. assurance_compensation_event
3. assurance_issue_event
4. evidence_legal_hold_event
5. evidence_conflict_event
6. assurance_obligation_occurrence_event
"""
from __future__ import annotations

import datetime
import secrets
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from conftest import H, stamp, add_principal

TENANT = "tenant-zoiko-group"
TENANT_B = "tenant-authz-b"


@pytest.fixture(scope="module")
def setup_tenants_and_actors(client):
    """Ensure both tenant-zoiko-group and tenant-authz-b exist with appropriate actors."""
    from app.db import CELL_ID, AdminSession
    from app.models import Actor, LegalEntity, Subject, Tenant
    with AdminSession() as db:
        if db.get(Tenant, TENANT_B) is None:
            db.add(Tenant(tenant_id=TENANT_B, legal_name="Authz Probe Group B", contracting_region="UK"))
            db.flush()
        if db.get(LegalEntity, "entity-gov-b") is None:
            db.add(LegalEntity(entity_id="entity-gov-b", tenant_id=TENANT_B, legal_name="Gov Probe Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id="subj-gov-b"))
        if db.get(Subject, "subj-gov-b") is None:
            db.add(Subject(subject_id="subj-gov-b", tenant_id=TENANT_B, entity_id="entity-gov-b",
                           subject_type="LEGAL_ENTITY", display_name="Gov Probe Ltd"))
        for aid, tenant, atype, roles in (
            ("gov-b-cco", TENANT_B, "HUMAN", ["CCO"]),
            ("gov-b-owner", TENANT_B, "HUMAN", ["CONTROL_OWNER"]),
        ):
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type=atype, display_name=aid, roles=roles)
        db.commit()
    return True


# ==============================================================================
# 1. Database-Level Immutability (Trigger Enforcement on PostgreSQL 17)
# ==============================================================================
EVENT_TABLES = [
    "assurance_exception_event",
    "assurance_compensation_event",
    "assurance_issue_event",
    "evidence_legal_hold_event",
    "evidence_conflict_event",
    "assurance_obligation_occurrence_event",
]


def test_governance_event_tables_enforce_database_trigger_immutability(client):
    """Direct SQL UPDATE or DELETE on any governance event table is rejected by assure_forbid_mutation()."""
    from app.db import IS_POSTGRES, AdminSession
    from app.models import (
        CompensationEvent,
        EvidenceConflictEvent,
        ExceptionEvent,
        IssueEvent,
        LegalHoldEvent,
        ObligationOccurrenceEvent,
    )
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")

    with AdminSession() as db:
        now = datetime.datetime.now(datetime.timezone.utc)
        probes = [
            (
                "assurance_exception_event",
                ExceptionEvent(
                    exception_id="exc-probe",
                    tenant_id=TENANT,
                    event_type="REQUESTED",
                    from_state=None,
                    to_state="PENDING_APPROVAL",
                    actor_id="actor-probe",
                    reason="probe",
                    target_type="OBLIGATION",
                    target_id="obl-probe",
                    valid_from=now,
                    valid_to=now + datetime.timedelta(days=30),
                    record_seq=999901,
                ),
            ),
            (
                "assurance_compensation_event",
                CompensationEvent(
                    compensation_id="comp-probe",
                    tenant_id=TENANT,
                    event_type="PROPOSED",
                    from_status=None,
                    to_status="PENDING_APPROVAL",
                    actor_id="actor-probe",
                    reason="probe",
                    obligation_lineage_id="obl-probe",
                    primary_control_key="CTRL-01",
                    record_seq=999902,
                ),
            ),
            (
                "assurance_issue_event",
                IssueEvent(
                    issue_id="iss-probe",
                    tenant_id=TENANT,
                    event_type="CREATED",
                    from_state=None,
                    to_state="OPEN",
                    actor_id="actor-probe",
                    reason="probe",
                    record_seq=999903,
                ),
            ),
            (
                "evidence_legal_hold_event",
                LegalHoldEvent(
                    hold_id="hld-probe",
                    tenant_id=TENANT,
                    evidence_id="evd-probe",
                    event_type="HOLD_PLACED",
                    status="ACTIVE",
                    authority="General Counsel",
                    actor_id="actor-probe",
                    reason="probe",
                    record_seq=999904,
                ),
            ),
            (
                "evidence_conflict_event",
                EvidenceConflictEvent(
                    conflict_id="cnf-probe",
                    tenant_id=TENANT,
                    event_type="OPENED",
                    status="OPEN",
                    actor_id="actor-probe",
                    reason="probe",
                    member_evidence_ids=["evd-1", "evd-2"],
                    record_seq=999905,
                ),
            ),
            (
                "assurance_obligation_occurrence_event",
                ObligationOccurrenceEvent(
                    occurrence_id="occ-probe",
                    tenant_id=TENANT,
                    obligation_lineage_id="obl-probe",
                    event_type="SCHEDULED",
                    actor_id="actor-probe",
                    window_from=now,
                    due_at=now + datetime.timedelta(days=7),
                    evidence_ids=[],
                    record_seq=999906,
                ),
            ),
        ]

        for tbl, obj in probes:
            db.add(obj)
            db.flush()
            eid = obj.event_id

            # Attempt UPDATE -> Must fail with assure_forbid_mutation trigger
            sp = db.begin_nested()
            with pytest.raises(DBAPIError) as exc_info:
                db.execute(text(f"UPDATE {tbl} SET actor_id = 'attacker' WHERE event_id = :eid"), {"eid": eid})
                db.flush()
            sp.rollback()
            assert "is append-only" in str(exc_info.value), f"Expected append-only violation on {tbl}"

            # Attempt DELETE -> Must fail with assure_forbid_mutation trigger
            sp = db.begin_nested()
            with pytest.raises(DBAPIError) as exc_info:
                db.execute(text(f"DELETE FROM {tbl} WHERE event_id = :eid"), {"eid": eid})
                db.flush()
            sp.rollback()
            assert "is append-only" in str(exc_info.value), f"Expected append-only violation on {tbl}"

        db.rollback()


# ==============================================================================
# 2. Strict Monotonic Record Sequence (record_seq)
# ==============================================================================
def test_governance_events_monotonic_record_seq(client):
    """next_record_seq(db) generates strictly increasing sequence numbers."""
    from app.db import AdminSession
    from app.platform import next_record_seq

    with AdminSession() as db:
        seq1 = next_record_seq(db)
        seq2 = next_record_seq(db)
        seq3 = next_record_seq(db)
        assert seq1 < seq2 < seq3


# ==============================================================================
# 3. Exception Lifecycle & Event Persistence
# ==============================================================================
def test_exception_lifecycle_events_and_projection(client):
    """Exception request, approval, event audit history, and invalid transition handling."""
    from app.db import AdminSession
    from app.models import ObligationInstance

    with AdminSession() as db:
        obl = db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None).first()
        assert obl is not None
        lineage_id = obl.lineage_id

    # 1. Propose exception as Control Owner
    body = {
        "target_type": "OBLIGATION",
        "target_id": lineage_id,
        "reason": "Grace period for system migration to v2",
        "valid_from": "2026-11-01T00:00:00Z",
        "valid_to": "2026-12-01T00:00:00Z",
    }
    r = client.post("/api/exceptions", json=body, headers=H("actor-owner"))
    assert r.status_code == 200, r.text
    exc = r.json()
    exc_id = exc["exception_id"]
    assert exc["state"] == "PENDING_APPROVAL"

    # Verify event logged
    r_ev = client.get(f"/api/exceptions/{exc_id}/events", headers=H("actor-owner"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "REQUESTED"
    assert events[0]["to_state"] == "PENDING_APPROVAL"
    assert events[0]["actor_id"] == "actor-owner"

    # 2. Unauthorized actor cannot decide
    r_unauth = client.post(
        f"/api/exceptions/{exc_id}/decide",
        json={"decision": "APPROVE", "reason": "Attempted approval"},
        headers=H("actor-analyst"),
    )
    assert r_unauth.status_code == 403

    # 3. CCO approves exception
    r_decide = client.post(
        f"/api/exceptions/{exc_id}/decide",
        json={"decision": "APPROVE", "reason": "Approved by CCO under policy"},
        headers=H("actor-cco"),
    )
    assert r_decide.status_code == 200
    exc_approved = r_decide.json()
    assert exc_approved["state"] == "APPROVED"

    # Verify second event logged
    r_ev2 = client.get(f"/api/exceptions/{exc_id}/events", headers=H("actor-owner"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) == 2
    assert events2[1]["event_type"] == "APPROVED"
    assert events2[1]["to_state"] == "APPROVED"
    assert events2[1]["actor_id"] == "actor-cco"
    assert events2[1]["record_seq"] > events2[0]["record_seq"]

    # 4. Deciding an already decided exception fails (invalid lifecycle)
    r_bad = client.post(
        f"/api/exceptions/{exc_id}/decide",
        json={"decision": "REJECT", "reason": "Cannot re-decide"},
        headers=H("actor-cco"),
    )
    assert r_bad.status_code in (400, 409)


# ==============================================================================
# 4. Compensation Lifecycle & Event Persistence
# ==============================================================================
def test_compensation_lifecycle_events_and_projection(client):
    """Compensation proposal, decision, and event projection."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance

    with AdminSession() as db:
        # ZA-STA-001 §09 (audit finding F-03): the primary must be a CONTROL leaf of the obligation's expression and every
        # compensating control a catalogue control definition, so the probe uses the seeded DORA obligation and controls.
        dora = db.query(ObligationDefinition).filter_by(key="DORA_ICT_RISK_FRAMEWORK").one()
        obl = db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None,
                                                     obligation_definition_id=dora.obligation_definition_id).first()
        lineage_id = obl.lineage_id

    # 1. Propose compensation
    comp_payload = {
        "obligation_lineage_id": lineage_id,
        "primary_control_key": "C_MFA_PHISHING_RESISTANT",
        "compensating_control_keys": ["C_DEVICE_ATTESTATION", "C_PRIVILEGED_NETWORK_RESTRICTION"],
        "equivalence_rationale": "Secondary hardware tokens provide equivalent multifactor assurance.",
        "valid_from": "2026-11-01T00:00:00Z",
    }
    r = client.post("/api/compensations", json=comp_payload, headers=H("actor-owner"))
    assert r.status_code == 200, r.text
    comp = r.json()
    comp_id = comp["compensation_id"]
    assert comp["status"] == "PENDING_APPROVAL"

    # Verify event
    r_ev = client.get(f"/api/compensations/{comp_id}/events", headers=H("actor-owner"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "SUBMITTED"
    assert events[0]["to_status"] == "PENDING_APPROVAL"

    # 2. Reject compensation as Risk Approver (who holds approve_compensation)
    r_decide = client.post(
        f"/api/compensations/{comp_id}/decide",
        json={"decision": "REJECT", "reason": "Hardware tokens insufficient for this obligation scope"},
        headers=H("actor-risk"),
    )
    assert r_decide.status_code == 200
    comp_decided = r_decide.json()
    assert comp_decided["status"] == "REJECTED"

    # Verify second event
    r_ev2 = client.get(f"/api/compensations/{comp_id}/events", headers=H("actor-owner"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) == 2
    assert events2[1]["event_type"] == "REJECTED"
    assert events2[1]["to_status"] == "REJECTED"
    assert events2[1]["record_seq"] > events2[0]["record_seq"]

    # 3. Invalid lifecycle: re-deciding fails
    r_bad = client.post(
        f"/api/compensations/{comp_id}/decide",
        json={"decision": "APPROVE", "reason": "Already rejected"},
        headers=H("actor-risk"),
    )
    assert r_bad.status_code in (400, 409)


# ==============================================================================
# 5. Issue Lifecycle & Event Transitions
# ==============================================================================
def test_issue_lifecycle_events_and_projection(client):
    """Issue creation, controlled state transitions, and audit event logging."""
    from app.db import AdminSession
    from app.engines import governance
    from app.models import Issue

    # Create an issue directly via engine
    with AdminSession() as db:
        iss = Issue(
            tenant_id=TENANT,
            title="Firewall Misconfiguration Probe",
            issue_type="PROOF_GAP",
            severity="HIGH",
            priority="P1",
            source_ref_type="AssuranceSnapshot",
            source_ref_id="snap-test-probe",
            dedupe_key=f"probe-dedupe-{secrets.token_hex(4)}",
        )
        db.add(iss)
        db.flush()
        governance.record_issue_created(db, issue=iss, actor_id="actor-owner", reason="Detected by audit probe")
        db.commit()
        iss_id = iss.issue_id

    # Verify initial creation event
    r_ev = client.get(f"/api/issues/{iss_id}/events", headers=H("actor-owner"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "CREATED"
    assert events[0]["to_state"] == "OPEN"

    # Transition OPEN -> TRIAGED
    r_tr1 = client.post(f"/api/issues/{iss_id}/transition", json={"state": "TRIAGED", "reason": "Triaged by team"}, headers=H("actor-owner"))
    assert r_tr1.status_code == 200
    assert r_tr1.json()["state"] == "TRIAGED"

    # Transition TRIAGED -> IN_REMEDIATION
    r_tr2 = client.post(f"/api/issues/{iss_id}/transition", json={"state": "IN_REMEDIATION", "reason": "Assigned to DevSecOps"}, headers=H("actor-owner"))
    assert r_tr2.status_code == 200
    assert r_tr2.json()["state"] == "IN_REMEDIATION"

    # Verify events
    r_ev2 = client.get(f"/api/issues/{iss_id}/events", headers=H("actor-owner"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) == 3
    assert [e["to_state"] for e in events2] == ["OPEN", "TRIAGED", "IN_REMEDIATION"]

    # Invalid transition (IN_REMEDIATION directly to CLOSED is forbidden by ISSUE_FLOW)
    r_bad = client.post(f"/api/issues/{iss_id}/transition", json={"state": "CLOSED", "reason": "Premature closure"}, headers=H("actor-owner"))
    assert r_bad.status_code in (400, 409, 422)


# ==============================================================================
# 6. Legal Hold Lifecycle & Event Persistence
# ==============================================================================
def test_legal_hold_lifecycle_events_and_projection(client):
    """Evidence legal hold placement, release, and event logging."""
    from app.db import AdminSession
    from app.models import EvidenceArtifact

    # Find existing evidence artifact
    with AdminSession() as db:
        evd_art = db.query(EvidenceArtifact).filter_by(tenant_id=TENANT).first()
        assert evd_art is not None
        evd_id = evd_art.evidence_id

    # 1. Place hold
    r_hold = client.post(
        f"/api/evidence/{evd_id}/hold",
        json={"reason": "Litigation hold order #2026-99", "authority": "General Counsel"},
        headers=H("actor-gc"),
    )
    assert r_hold.status_code == 200, r_hold.text

    # Verify event logged
    r_ev = client.get(f"/api/evidence/{evd_id}/holds/events", headers=H("actor-gc"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) >= 1
    placed_ev = [e for e in events if e["event_type"] == "HOLD_PLACED"][-1]
    assert placed_ev["status"] == "ACTIVE"
    assert placed_ev["actor_id"] == "actor-gc"

    # 2. Release hold
    r_rel = client.post(
        f"/api/evidence/{evd_id}/release-hold",
        json={"reason": "Litigation resolved and hold released", "authority": "General Counsel"},
        headers=H("actor-gc"),
    )
    assert r_rel.status_code == 200, r_rel.text

    # Verify release event logged
    r_ev2 = client.get(f"/api/evidence/{evd_id}/holds/events", headers=H("actor-gc"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) >= 2
    rel_ev = [e for e in events2 if e["event_type"] == "HOLD_RELEASED"][-1]
    assert rel_ev["status"] == "RELEASED"
    assert rel_ev["record_seq"] > placed_ev["record_seq"]

    # 3. Release again -> 409 (no active hold)
    r_bad = client.post(
        f"/api/evidence/{evd_id}/release-hold",
        json={"reason": "Re-releasing"},
        headers=H("actor-gc"),
    )
    assert r_bad.status_code in (400, 409)


# ==============================================================================
# 7. Evidence Conflict Lifecycle & Event Persistence
# ==============================================================================
def test_evidence_conflict_lifecycle_events_and_projection(client):
    """Evidence conflict creation, resolution, and event logging."""
    from app.db import AdminSession
    from app.models import EvidenceArtifact

    with AdminSession() as db:
        arts = db.query(EvidenceArtifact).filter_by(tenant_id=TENANT).limit(2).all()
        ids = [a.evidence_id for a in arts]

    # 1. Open conflict
    r_open = client.post(
        "/api/evidence-conflicts",
        json={"member_evidence_ids": ids, "description": "Conflicting access log entries for user-probe"},
        headers=H("actor-owner"),
    )
    assert r_open.status_code == 200, r_open.text
    conf = r_open.json()
    conf_id = conf["conflict_id"]
    assert conf["status"] == "OPEN"

    # Verify event logged
    r_ev = client.get(f"/api/evidence-conflicts/{conf_id}/events", headers=H("actor-owner"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "OPENED"
    assert events[0]["status"] == "OPEN"

    # 2. Resolve conflict
    r_res = client.post(
        f"/api/evidence-conflicts/{conf_id}/resolve",
        json={"reason": "Resolved by manual forensic review; second artifact confirmed canonical"},
        headers=H("actor-cco"),
    )
    assert r_res.status_code == 200, r_res.text
    assert r_res.json()["status"] == "RESOLVED"

    # Verify resolve event logged
    r_ev2 = client.get(f"/api/evidence-conflicts/{conf_id}/events", headers=H("actor-owner"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) == 2
    assert events2[1]["event_type"] == "RESOLVED"
    assert events2[1]["status"] == "RESOLVED"
    assert events2[1]["record_seq"] > events2[0]["record_seq"]

    # 3. Resolve again -> 409 (already resolved)
    r_bad = client.post(
        f"/api/evidence-conflicts/{conf_id}/resolve",
        json={"reason": "Resolving already resolved"},
        headers=H("actor-cco"),
    )
    assert r_bad.status_code in (400, 409)


# ==============================================================================
# 8. Obligation Occurrence Lifecycle & Event Persistence
# ==============================================================================
def test_obligation_occurrence_lifecycle_events_and_projection(client):
    """Obligation occurrence scheduling, fulfilment, and event tracking."""
    from app.db import AdminSession
    from app.models import EvidenceArtifact, ObligationInstance

    with AdminSession() as db:
        obl = db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None).first()
        assert obl is not None
        lineage_id = obl.lineage_id
        evd = db.query(EvidenceArtifact).filter_by(tenant_id=TENANT).first()
        evd_id = evd.evidence_id

    # 1. Schedule occurrence
    occ_payload = {
        "window_from": "2026-11-01T00:00:00Z",
        "due_at": "2026-11-05T23:59:59Z",
    }
    r_sch = client.post(f"/api/obligations/{lineage_id}/occurrences", json=occ_payload, headers=H("actor-owner"))
    assert r_sch.status_code == 200, r_sch.text
    occ = r_sch.json()
    occ_id = occ["occurrence_id"]
    assert occ.get("fulfilled_at") is None

    # Verify event logged
    r_ev = client.get(f"/api/occurrences/{occ_id}/events", headers=H("actor-owner"))
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "SCHEDULED"

    # 2. Fulfil occurrence
    fulfil_payload = {
        "fulfilled_at": "2026-11-03T12:00:00Z",
        "evidence_ids": [evd_id],
    }
    r_ful = client.post(f"/api/occurrences/{occ_id}/fulfil", json=fulfil_payload, headers=H("actor-owner"))
    assert r_ful.status_code == 200, r_ful.text
    assert r_ful.json().get("fulfilled_at") is not None

    # Verify fulfilment event logged
    r_ev2 = client.get(f"/api/occurrences/{occ_id}/events", headers=H("actor-owner"))
    assert r_ev2.status_code == 200
    events2 = r_ev2.json()
    assert len(events2) == 2
    assert events2[1]["event_type"] == "FULFILLED"
    assert events2[1]["record_seq"] > events2[0]["record_seq"]

    # 3. Fulfil again -> 400
    r_bad = client.post(f"/api/occurrences/{occ_id}/fulfil", json=fulfil_payload, headers=H("actor-owner"))
    assert r_bad.status_code in (400, 409)


# ==============================================================================
# 9. Multi-Tenant Isolation & Cross-Tenant Rejection
# ==============================================================================
def test_cross_tenant_isolation_on_governance_events(client, setup_tenants_and_actors):
    """Tenant B cannot read or modify Tenant A's governance events via API or database RLS."""
    from app.db import AdminSession, IS_POSTGRES, SessionLocal
    from app.engines import governance
    from app.models import ExceptionRecord, ObligationInstance

    with AdminSession() as db:
        exc = db.query(ExceptionRecord).filter_by(tenant_id=TENANT).first()
        if exc is None:
            obl = db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None).first()
            now = datetime.datetime.now(datetime.timezone.utc)
            exc, _ = governance.request_exception(
                db,
                tenant_id=TENANT,
                actor_id="actor-owner",
                target_type="OBLIGATION",
                target_id=obl.lineage_id,
                reason="Cross tenant isolation test probe",
                valid_from=now,
                valid_to=now + datetime.timedelta(days=30),
            )
            db.commit()
        exc_id = exc.exception_id

    # 1. API isolation: Actor from Tenant B requesting Tenant A's exception events gets 404
    r_cross = client.get(f"/api/exceptions/{exc_id}/events", headers=H("gov-b-cco"))
    assert r_cross.status_code == 404

    # 2. Database RLS isolation: Queries under Tenant B context return 0 rows for Tenant A
    if IS_POSTGRES:
        with SessionLocal() as db:
            stamp(db.connection(), tenant=TENANT_B, actor="gov-b-cco")
            for tbl in EVENT_TABLES:
                cnt = db.execute(text(f"SELECT count(*) FROM {tbl} WHERE tenant_id = :ta"), {"ta": TENANT}).scalar()
                assert cnt == 0, f"Tenant B RLS leaked rows from {tbl}"


# ==============================================================================
# 10. Outbox and Audit Atomicity
# ==============================================================================
def test_governance_event_outbox_and_audit_atomicity(client):
    """Governance transitions emit platform_audit_event and outbox entries atomically."""
    from app.db import AdminSession
    from app.models import AuditEvent, ObligationInstance, OutboxEvent

    with AdminSession() as db:
        audit_count_before = db.query(AuditEvent).count()
        outbox_count_before = db.query(OutboxEvent).count()
        obl = db.query(ObligationInstance).filter_by(tenant_id=TENANT, record_to_seq=None).first()
        lineage_id = obl.lineage_id

    r = client.post(
        "/api/exceptions",
        json={
            "target_type": "OBLIGATION",
            "target_id": lineage_id,
            "reason": "Atomicity test exception proposal",
            "valid_from": "2026-11-01T00:00:00Z",
            "valid_to": "2026-12-01T00:00:00Z",
        },
        headers=H("actor-owner"),
    )
    assert r.status_code == 200

    with AdminSession() as db:
        audit_count_after = db.query(AuditEvent).count()
        outbox_count_after = db.query(OutboxEvent).count()
        assert audit_count_after > audit_count_before, "Audit event was not emitted"
        assert outbox_count_after > outbox_count_before, "Outbox event was not emitted"
