"""Historical dossier reconstruction of exceptions, compensations and occurrences (GET /obligations/{lineage}
?as_of_record_seq=K).

Requirements exercised:
  * ZA-TIM-001 TIM-REQ-006/015/017 (a historical answer uses the requested record cutoff; no latest joins; the
    resolved watermark is echoed), §11 ("Historical AS_KNOWN_THEN excludes revisions first recorded after K");
  * ZA-STA-001 §10 ("Exception approved retroactively ... historical 'what was known then' still shows it was not
    approved before its record time"), §09 (a compensation "never silently inherits its prior approval"), §13
    (occurrence fulfilment events; missed history never overwritten), STA-REQ-001 (separate state dimensions);
  * ZA-GLS-001 exception / compensation lifecycle values; DOM-INV-11 (another organisation's record is not found).

The state fields of these records are projections updated in place (migration 0010); what was true at a cutoff is in
their append-only event tables (0008). A historical read takes the events recorded by the cutoff, reports a history
that does not prove itself (state null + problem) and never substitutes the projection. The current perspective is
unchanged. Every test builds its own organisation records; seeded data is only read.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-dgh-{TAG}", f"tenant-dghb-{TAG}"
ENT, SUBJ, ENT_B, SUBJ_B = f"entity-dgh-{TAG}", f"subj-dgh-{TAG}", f"entity-dghb-{TAG}", f"subj-dghb-{TAG}"
OWNER, CCO, APPROVER, B_CCO = f"dgh-owner-{TAG}", f"dgh-cco-{TAG}", f"dgh-approver-{TAG}", f"dghb-cco-{TAG}"
MON, DEV = "C_AI_MONITORING_LOGS", "C_DEVICE_ATTESTATION"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, DEC = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tid in (TA, TB):
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
        db.flush()
        for tid, eid, sid in ((TA, ENT, SUBJ), (TB, ENT_B, SUBJ_B)):
            db.add(LegalEntity(entity_id=eid, tenant_id=tid, legal_name=f"{eid} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=eid, subject_type="LEGAL_ENTITY",
                           display_name=f"{eid} AG"))
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (APPROVER, TA, ["RISK_APPROVER"]),
                                (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


def _obligation(tenant=TA, entity=ENT, subject=SUBJ, key=MON):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": key}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=tenant, entity_id=entity, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle="ADOPTED", valid_from=T0,
                                  record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _dossier(client, lineage, as_of=None, actor=CCO):
    params = {} if as_of is None else {"as_of_record_seq": as_of}
    r = client.get(f"/api/obligations/{lineage}", headers=H(actor), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _evidence(client):
    return _ok(client.post("/api/evidence/upload", headers=H(OWNER),
                           files={"file": ("record.txt", secrets.token_bytes(16), "text/plain")},
                           data={"entity_id": ENT, "title": "Filing receipt", "coverage_from": "2026-09-01T00:00:00Z",
                                 "coverage_to": "2026-10-01T00:00:00Z", "validity_days": "400"}))["evidence_id"]


# ------------------------------------------------------------------------------------------------ exceptions
def test_exception_state_and_approver_are_those_recorded_by_the_cutoff(client, org):
    lineage = _obligation()
    before = _seq()
    x = _ok(client.post("/api/exceptions", headers=H(OWNER), json={
        "target_type": "OBLIGATION", "target_id": lineage, "reason": "Migration window",
        "valid_from": SEP.isoformat(), "valid_to": DEC.isoformat()}))
    requested = _seq()
    _ok(client.post(f"/api/exceptions/{x['exception_id']}/decide", headers=H(CCO), json={"decision": "APPROVE"}))
    approved = _seq()
    _ok(client.post(f"/api/exceptions/{x['exception_id']}/decide", headers=H(CCO),
                    json={"decision": "REVOKE", "reason": "Migration finished"}))

    def at(seq):
        return {e["exception_id"]: e for e in _dossier(client, lineage, seq)["exceptions"]}
    assert x["exception_id"] not in at(before)  # recorded later
    pending = at(requested)[x["exception_id"]]
    assert (pending["state"], pending["approved_by"], pending["requested_by"]) == ("PENDING_APPROVAL", None, OWNER)
    assert pending["historical_state"]["source"] == "EVENT_HISTORY" and pending["historical_state"]["problem"] is None
    assert len(pending["historical_state"]["event_ids"]) == 1
    then = at(approved)[x["exception_id"]]
    assert (then["state"], then["approved_by"]) == ("APPROVED", CCO)
    [now] = _dossier(client, lineage)["exceptions"]
    assert (now["state"], now["approved_by"]) == ("REVOKED", CCO) and "historical_state" not in now


# ------------------------------------------------------------------------------------------------ compensations
def test_compensation_status_and_approver_are_those_recorded_by_the_cutoff(client, org):
    lineage = _obligation()
    c = _ok(client.post("/api/compensations", headers=H(OWNER), json={
        "obligation_lineage_id": lineage, "primary_control_key": MON, "compensating_control_keys": [DEV],
        "equivalence_rationale": "Device attestation evidences the same monitoring outcome", "valid_from": T0.isoformat()}))
    proposed = _seq()
    _ok(client.post(f"/api/compensations/{c['compensation_id']}/decide", headers=H(APPROVER), json={"decision": "APPROVE"}))
    approved = _seq()
    _ok(client.post(f"/api/compensations/{c['compensation_id']}/decide", headers=H(APPROVER),
                    json={"decision": "REVOKE", "reason": "Primary control restored"}))

    def at(seq):
        return {e["compensation_id"]: e for e in _dossier(client, lineage, seq)["compensations"]}
    pending = at(proposed)[c["compensation_id"]]
    assert (pending["status"], pending["approved_by"]) == ("PENDING_APPROVAL", None)
    assert pending["historical_state"] == {**pending["historical_state"], "source": "EVENT_HISTORY", "problem": None}
    assert (at(approved)[c["compensation_id"]]["status"], at(approved)[c["compensation_id"]]["approved_by"]) == \
        ("APPROVED", APPROVER)
    [now] = _dossier(client, lineage)["compensations"]
    assert now["status"] == "REVOKED"


# ------------------------------------------------------------------------------------------------ occurrences
def test_occurrence_fulfilment_evidence_and_dispute_are_those_recorded_by_the_cutoff(client, org):
    from app.db import AdminSession
    from app.models import ObligationOccurrenceEvent
    from app.platform import next_record_seq
    lineage = _obligation()
    o = _ok(client.post(f"/api/obligations/{lineage}/occurrences", headers=H(OWNER), json={
        "window_from": SEP.isoformat(), "due_at": "2026-09-30T00:00:00Z"}))
    scheduled = _seq()
    e = _evidence(client)
    _ok(client.post(f"/api/occurrences/{o['occurrence_id']}/fulfil", headers=H(OWNER),
                    json={"fulfilled_at": "2026-09-20T00:00:00Z", "evidence_ids": [e]}))
    fulfilled = _seq()
    with AdminSession() as db:  # a recorded dispute of the fulfilment (no route writes one yet)
        db.add(ObligationOccurrenceEvent(occurrence_id=o["occurrence_id"], tenant_id=TA, obligation_lineage_id=lineage,
                                         event_type="DISPUTED", actor_id=CCO, window_from=SEP,
                                         due_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
                                         fulfilled_at=None, evidence_ids=[], is_late=False, record_seq=next_record_seq(db)))
        db.commit()
    disputed = _seq()

    def at(seq):
        [x] = [x for x in _dossier(client, lineage, seq)["occurrences"] if x["occurrence_id"] == o["occurrence_id"]]
        return x
    then = at(scheduled)
    assert (then["fulfilled_at"], then["evidence_ids"], then["disputed"]) == (None, [], False), then
    done = at(fulfilled)
    assert done["fulfilled_at"].startswith("2026-09-20") and done["evidence_ids"] == [e] and done["disputed"] is False
    assert at(disputed)["disputed"] is True
    assert at(disputed)["historical_state"]["source"] == "EVENT_HISTORY"


# ------------------------------------------------------------------------------------------------ gaps
def test_missing_inconsistent_and_baseline_only_history_is_reported_not_replaced(client, org):
    from app.db import AdminSession
    from app.engines.governance import MIGRATION_BASELINE_EVENT
    from app.models import (Compensation, CompensationEvent, ExceptionEvent, ExceptionRecord, ObligationOccurrence)
    from app.platform import next_record_seq
    lineage = _obligation()
    with AdminSession() as db:
        def exception(state):
            x = ExceptionRecord(tenant_id=TA, target_type="OBLIGATION", target_id=lineage, reason=state, state=state,
                                requested_by=OWNER, approved_by=CCO if state != "PENDING_APPROVAL" else None,
                                valid_from=SEP, valid_to=DEC, record_seq=next_record_seq(db))
            db.add(x)
            db.flush()
            return x
        no_history = exception("APPROVED")
        broken = exception("APPROVED")  # its whole history says PENDING_APPROVAL
        db.add(ExceptionEvent(exception_id=broken.exception_id, tenant_id=TA, event_type="REQUESTED", from_state=None,
                              to_state="PENDING_APPROVAL", actor_id=OWNER, reason="x", target_type="OBLIGATION",
                              target_id=lineage, valid_from=SEP, valid_to=DEC, record_seq=broken.record_seq))
        baseline = exception("APPROVED")  # 0008 baseline: the state at migration time, stamped at the row's seq
        db.add(ExceptionEvent(exception_id=baseline.exception_id, tenant_id=TA, event_type=MIGRATION_BASELINE_EVENT,
                              from_state=None, to_state="APPROVED", actor_id=OWNER, reason="baseline",
                              target_type="OBLIGATION", target_id=lineage, valid_from=SEP, valid_to=DEC,
                              record_seq=baseline.record_seq))
        comp = Compensation(tenant_id=TA, obligation_lineage_id=lineage, primary_control_key=MON,
                            compensating_expression={"node_type": "CONTROL", "node_id": "c", "control_key": DEV},
                            equivalence_rationale="seeded without history", status="APPROVED", valid_from=T0,
                            record_seq=next_record_seq(db))
        late = Compensation(tenant_id=TA, obligation_lineage_id=lineage, primary_control_key=MON,
                            compensating_expression={"node_type": "CONTROL", "node_id": "c", "control_key": DEV},
                            equivalence_rationale="history recorded after the row", status="PENDING_APPROVAL",
                            valid_from=T0, record_seq=next_record_seq(db))
        db.add_all([comp, late])
        db.flush()
        db.add(CompensationEvent(compensation_id=late.compensation_id, tenant_id=TA, event_type="SUBMITTED",
                                 from_status=None, to_status="PENDING_APPROVAL", actor_id=OWNER, reason="x",
                                 obligation_lineage_id=lineage, primary_control_key=MON, record_seq=next_record_seq(db) + 5))
        occ = ObligationOccurrence(tenant_id=TA, obligation_lineage_id=lineage, window_from=SEP,
                                   due_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
                                   fulfilled_at=datetime(2026, 9, 20, tzinfo=timezone.utc), evidence_ids=["e"],
                                   record_seq=next_record_seq(db))
        db.add(occ)
        db.commit()
        ids = (no_history.exception_id, broken.exception_id, baseline.exception_id, comp.compensation_id,
               late.compensation_id, occ.occurrence_id)
    no_hist_id, broken_id, baseline_id, comp_id, late_id, occ_id = ids
    cutoff = _seq()
    d = _dossier(client, lineage, cutoff)
    xs = {x["exception_id"]: x for x in d["exceptions"]}
    for xid, problem in ((no_hist_id, "EXCEPTION_HISTORY_MISSING"), (broken_id, "EXCEPTION_HISTORY_INCONSISTENT"),
                         (baseline_id, "EXCEPTION_HISTORY_INCOMPLETE")):
        assert (xs[xid]["state"], xs[xid]["approved_by"], xs[xid]["historical_state"]["problem"]) == (None, None, problem), xid
    cs = {c["compensation_id"]: c for c in d["compensations"]}
    assert (cs[comp_id]["status"], cs[comp_id]["historical_state"]["problem"]) == (None, "COMPENSATION_HISTORY_MISSING")
    assert (cs[late_id]["status"], cs[late_id]["historical_state"]["problem"]) == (None, "COMPENSATION_HISTORY_INCONSISTENT")
    [o] = d["occurrences"]
    assert (o["fulfilled_at"], o["evidence_ids"], o["disputed"]) == (None, None, None)
    assert o["historical_state"]["problem"] == "OCCURRENCE_HISTORY_MISSING"
    assert not any("their state fields are current" in x for x in d["historical_limitations"])
    # The current perspective is unchanged: the records as they are now.
    now = _dossier(client, lineage)
    assert {x["exception_id"]: x["state"] for x in now["exceptions"]}[no_hist_id] == "APPROVED"
    assert {c["compensation_id"]: c["status"] for c in now["compensations"]}[comp_id] == "APPROVED"


# ------------------------------------------------------------------------------------------------ isolation, read-only
def test_historical_dossiers_are_refused_to_another_organisation_and_write_nothing(client, org):
    from app.db import AdminSession
    from app.models import AuditEvent, CompensationEvent, ExceptionEvent, ObligationOccurrenceEvent, OutboxEvent
    lineage = _obligation()
    x = _ok(client.post("/api/exceptions", headers=H(OWNER), json={
        "target_type": "OBLIGATION", "target_id": lineage, "reason": "probe",
        "valid_from": SEP.isoformat(), "valid_to": DEC.isoformat()}))
    cutoff = _seq()
    _ok(client.post(f"/api/exceptions/{x['exception_id']}/decide", headers=H(CCO), json={"decision": "APPROVE"}))
    b_lineage = _obligation(TB, ENT_B, SUBJ_B)
    for actor, target in ((B_CCO, lineage), (CCO, b_lineage)):
        r = client.get(f"/api/obligations/{target}", headers=H(actor), params={"as_of_record_seq": cutoff})
        assert r.status_code in (403, 404), (actor, r.text)

    def counts():
        with AdminSession() as db:
            return (_seq(), *(db.execute(select(func.count()).select_from(m)).scalar()
                              for m in (AuditEvent, OutboxEvent, ExceptionEvent, CompensationEvent,
                                        ObligationOccurrenceEvent)))
    before = counts()
    for seq in (cutoff, _seq()):
        d = _dossier(client, lineage, seq)
        assert d["record_watermark"] == {**d["record_watermark"], "record_seq": seq, "perspective": "AS_OF_RECORD_SEQ"}
    assert counts() == before
