"""Evidence conflict lifecycle integrity: opening, resolution, history and their effect on assurance.

Controlling text:
  * ZA-EVD-001 §13: "Conflict detected: Create EvidenceConflict object referencing evidence IDs ... not intrinsic
    CONFLICTING mutation"; "Conflict resolved: Append resolution/disposition event ... New evaluation/snapshot; old
    conflict history preserved"; data model "EvidenceConflict: conflict ID, member evidence IDs, scope,
    status/disposition refs. Governed workflow; members stay immutable"; EVD-REQ-019 (relationship, not an artifact
    state); EVD-REQ-038 (lifecycle event publication idempotent under retries).
  * ZA-API-001 §08 Idempotency-Key, as engines/idempotency.py implements it for other governed actions; API-REQ-014
    (fields a route does not act on are refused, never silently dropped).
  * ZA-STA-001 §04 (CONFLICTING evidence: REQUIRES_REVIEW), STA-REQ-019 and ZA-TIM-001 §11 (a later resolution never
    rewrites an earlier evaluation or snapshot; a historical read uses the conflict history recorded by its cutoff).
  * The governed-list conventions tests/test_list_order_determinism.py pins: lists in a stable order, governance event
    histories by record sequence then event ID.

Every test builds its own organisation, evidence and controls; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-eci-{TAG}", f"tenant-ecib-{TAG}"
ENT, ENT_B = f"entity-eci-{TAG}", f"entity-ecib-{TAG}"
OWNER, CCO, B_OWNER = f"eci-owner-{TAG}", f"eci-cco-{TAG}", f"ecib-owner-{TAG}"
OCT, NOV = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject, Tenant
    with AdminSession() as db:
        for tenant, ent in ((TA, ENT), (TB, ENT_B)):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=f"s-{ent}"))
            db.add(Subject(subject_id=f"s-{ent}", tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                           display_name=ent))
        for aid, tenant, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (B_OWNER, TB, ["CONTROL_OWNER"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ records
def _evidence(client, actor=OWNER, entity=ENT):
    r = client.post("/api/evidence/upload", headers=H(actor),
                    files={"file": ("proof.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Probe record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _open(client, members, description="Two sources disagree on the reviewed population", headers=None):
    return client.post("/api/evidence-conflicts", headers={**H(OWNER), **(headers or {})},
                       json={"member_evidence_ids": members, "description": description})


def _resolve(client, cid, body):
    return client.post(f"/api/evidence-conflicts/{cid}/resolve", headers=H(CCO), json=body)


def _rows(tenant=TA):
    """Every conflict-lifecycle record of an organisation: conflicts, their events, audit records and outbox events."""
    from app.db import AdminSession
    from app.models import AuditEvent, EvidenceConflict, EvidenceConflictEvent, OutboxEvent
    with AdminSession() as db:
        conflicts = db.execute(select(EvidenceConflict).where(EvidenceConflict.tenant_id == tenant)).scalars().all()
        ids = [c.conflict_id for c in conflicts] or ["-"]
        events = db.execute(select(EvidenceConflictEvent).where(EvidenceConflictEvent.conflict_id.in_(ids))
                            .order_by(EvidenceConflictEvent.record_seq)).scalars().all()
        audits = db.execute(select(AuditEvent).where(AuditEvent.object_type == "EvidenceConflict",
                                                     AuditEvent.object_id.in_(ids))).scalars().all()
        outbox = db.execute(select(OutboxEvent).where(OutboxEvent.aggregate_type == "EvidenceConflict",
                                                      OutboxEvent.aggregate_id.in_(ids))).scalars().all()
        for row in (*conflicts, *events, *audits, *outbox):
            db.expunge(row)
    return {"conflicts": conflicts, "events": events, "audits": audits, "outbox": outbox}


def _of(rows, cid):
    return {k: [r for r in v if getattr(r, "conflict_id", None) == cid or getattr(r, "object_id", None) == cid
                or getattr(r, "aggregate_id", None) == cid] for k, v in rows.items()}


def _counts(rows):
    return {k: len(v) for k, v in rows.items()}


# ------------------------------------------------------------------------------------------------ opening
@pytest.mark.parametrize("members, description", [
    ([], "no member at all"),                      # a conflict that references no evidence affects nothing
    (["DUP", "DUP"], "the same artifact twice"),   # an artifact cannot be a conflicting source against itself
    (["ONE"], "   "),                              # a required description of only whitespace
], ids=["empty", "duplicate", "blank-description"])
def test_opening_refuses_malformed_member_sets_and_blank_descriptions(client, org, members, description):
    """Was: each of these was stored as an OPEN conflict, with its OPENED event, audit record and outbox event."""
    eid = _evidence(client)
    members = [eid if m in ("DUP", "ONE") else m for m in members]
    before = _counts(_rows())
    r = _open(client, members, description)
    assert r.status_code == 422, r.text
    assert _counts(_rows()) == before


def test_opening_refuses_another_organisations_evidence(client, org):
    mine, theirs = _evidence(client), _evidence(client, actor=B_OWNER, entity=ENT_B)
    before = _counts(_rows())
    assert _open(client, [mine, theirs]).status_code == 404
    assert _counts(_rows()) == before


def test_a_retried_open_creates_one_conflict(client, org):
    """EVD-REQ-038 / API-001 §08: a retry with the same Idempotency-Key returns the conflict the first request created
    and writes nothing; the same key with another request is refused (409 idempotency_conflict). Was: the header was
    ignored and every retry created another OPEN conflict, with its own event, audit record and outbox event."""
    a, b = _evidence(client), _evidence(client)
    key = {"Idempotency-Key": f"eci-open-{secrets.token_hex(4)}"}
    first = _open(client, [a, b], headers=key)
    assert first.status_code == 200, first.text
    before = _counts(_rows())
    again = _open(client, [a, b], headers=key)
    assert again.status_code == 200 and again.json()["conflict_id"] == first.json()["conflict_id"]
    assert _counts(_rows()) == before
    other = _open(client, [a], headers=key)
    assert other.status_code == 409 and other.json()["detail"]["error"] == "idempotency_conflict"
    assert _counts(_rows()) == before
    mine = _of(_rows(), first.json()["conflict_id"])
    assert (len(mine["events"]), len(mine["audits"]), len(mine["outbox"])) == (1, 1, 1)


def test_opening_records_who_opened_it(client, org):
    """The issue.create-style audit record of the opening names the person and their principal type, as the resolution's
    record does. Was: the actor type was dropped (the actor ID was passed as a bare string)."""
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    mine = _of(_rows(), cid)
    [opened] = [x for x in mine["audits"] if x.action == "evidence_conflict.opened"]
    assert (opened.actor_id, opened.actor_type, opened.new_state, opened.tenant_id) == (OWNER, "HUMAN", "OPEN", TA)
    [ev] = mine["events"]
    assert (ev.event_type, ev.status, ev.actor_id, ev.member_evidence_ids) == ("OPENED", "OPEN", OWNER, [a, b])


# ------------------------------------------------------------------------------------------------ resolution
@pytest.mark.parametrize("extra", [
    {"successor_evidence_id": "no-such-evidence"},      # an evidence reference resolution neither checks nor records
    {"successor_evidence_id": "SELF"},                   # even a real member: no authoritative-source selection exists
    {"authority": "source A is authoritative"},          # an authority the resolution never records
    {"reason": "   "},                                   # a governed disposition with no reason
], ids=["unknown-successor", "member-successor", "authority", "blank-reason"])
def test_resolution_refuses_what_it_does_not_record_and_leaves_the_conflict_open(client, org, extra):
    """Was: 200 RESOLVED, with the evidence reference or authority silently dropped (resolution shared the supersession
    request model), or with an empty disposition. A refused resolution changes nothing."""
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    if extra.get("successor_evidence_id") == "SELF":
        extra = {"successor_evidence_id": a}
    before = _of(_rows(), cid)
    r = _resolve(client, cid, {"reason": "Reviewed", **extra})
    assert r.status_code == 422, r.text
    after = _of(_rows(), cid)
    assert _counts(after) == _counts(before)
    assert after["conflicts"][0].status == "OPEN" and after["conflicts"][0].disposition is None


def test_resolution_appends_its_record_and_preserves_the_opening(client, org):
    """EVD §13: the disposition is appended; the opening event, the members and the API view agree with the stored state."""
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    [opened] = _of(_rows(), cid)["events"]
    r = _resolve(client, cid, {"reason": "Source A re-extracted; B was a partial export"})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    mine = _of(_rows(), cid)
    first, resolved = mine["events"]
    assert (first.event_id, first.reason, first.record_seq, first.status) == (
        opened.event_id, opened.reason, opened.record_seq, "OPEN")  # the opening is never rewritten
    assert (resolved.event_type, resolved.status, resolved.actor_id, resolved.member_evidence_ids) == (
        "RESOLVED", "RESOLVED", CCO, [a, b])
    assert resolved.record_seq > first.record_seq
    [audit] = [x for x in mine["audits"] if x.action == "evidence_conflict.resolved"]
    assert (audit.actor_id, audit.actor_type, audit.prior_state, audit.new_state, audit.record_seq) == (
        CCO, "HUMAN", "OPEN", "RESOLVED", resolved.record_seq)
    assert [o.event_type for o in sorted(mine["outbox"], key=lambda o: o.record_seq)] == [
        "evidence.conflict_opened", "evidence.conflict_resolved"]
    assert mine["conflicts"][0].member_evidence_ids == [a, b]  # members stay immutable
    listed = next(c for c in client.get("/api/evidence-conflicts", headers=H(CCO)).json() if c["conflict_id"] == cid)
    assert (listed["status"], listed["disposition"], listed["member_evidence_ids"]) == (
        "RESOLVED", "Source A re-extracted; B was a partial export", [a, b])
    hist = client.get(f"/api/evidence-conflicts/{cid}/events", headers=H(CCO)).json()
    assert [(e["event_type"], e["status"]) for e in hist] == [("OPENED", "OPEN"), ("RESOLVED", "RESOLVED")]
    again = _resolve(client, cid, {"reason": "again"})  # a stale second resolution is refused and writes nothing
    assert again.status_code == 409 and _counts(_of(_rows(), cid)) == _counts(mine)
    assert _resolve(client, "no-such-conflict", {"reason": "x"}).status_code == 404


def test_a_failed_resolution_leaves_the_conflict_as_it_was(client, org, monkeypatch):
    """Fault injection: a failure after the status change and the RESOLVED event were written (the outbox write) rolls
    the whole resolution back: still OPEN, no disposition, no RESOLVED event or audit record; a retry then resolves once."""
    from app.engines import governance
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    before = _of(_rows(), cid)
    real_emit = governance.emit

    def failing_emit(db, **kw):
        if kw.get("event_type") == "evidence.conflict_resolved":
            raise RuntimeError("injected outbox fault")
        return real_emit(db, **kw)
    monkeypatch.setattr(governance, "emit", failing_emit)
    with pytest.raises(RuntimeError, match="injected outbox fault"):
        _resolve(client, cid, {"reason": "reviewed"})
    monkeypatch.undo()
    after = _of(_rows(), cid)
    assert _counts(after) == _counts(before)
    assert (after["conflicts"][0].status, after["conflicts"][0].disposition) == ("OPEN", None)
    assert _resolve(client, cid, {"reason": "reviewed"}).status_code == 200
    assert [e.event_type for e in _of(_rows(), cid)["events"]] == ["OPENED", "RESOLVED"]


def test_another_organisation_cannot_resolve_or_read_a_conflict(client, org):
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    before = _of(_rows(), cid)
    assert client.post(f"/api/evidence-conflicts/{cid}/resolve", headers=H(B_OWNER), json={"reason": "x"}).status_code in (403, 404)
    assert client.get(f"/api/evidence-conflicts/{cid}/events", headers=H(B_OWNER)).status_code in (403, 404)
    assert all(c["conflict_id"] != cid for c in client.get("/api/evidence-conflicts", headers=H(B_OWNER)).json())
    assert _counts(_of(_rows(), cid)) == _counts(before)


# ------------------------------------------------------------------------------------------------ order
def _order_bys(client, path, actor=CCO) -> list[str]:
    import re
    from app import db as dbmod
    seen: list[str] = []

    def capture(conn, cursor, statement, *args):
        seen.append(statement)
    event.listen(dbmod.engine, "before_cursor_execute", capture)
    try:
        assert client.get(path, headers=H(actor)).status_code == 200
    finally:
        event.remove(dbmod.engine, "before_cursor_execute", capture)
    out = []
    for s in seen:
        if "evidence_conflict" not in s:
            continue
        m = re.search(r"ORDER BY (.+?)(?:\s+LIMIT\b|\s+OFFSET\b|\s+FOR\b|$)", " ".join(s.split()))
        out.append(m.group(1) if m else "")
    return out


def test_conflict_lists_and_histories_have_a_stable_order(client, org):
    """GET /evidence-conflicts read the table in physical order, which a resolution (an UPDATE) changes on PostgreSQL;
    the event history broke record-sequence ties by nothing. Now: creation order then conflict ID, and record sequence
    then event ID, the order the assurance engine reads conflicts in and every governance history uses."""
    ids = [_open(client, [_evidence(client), _evidence(client)]).json()["conflict_id"] for _ in range(3)]
    order = [c["conflict_id"] for c in client.get("/api/evidence-conflicts", headers=H(CCO)).json() if c["conflict_id"] in ids]
    assert order == ids
    assert _resolve(client, ids[0], {"reason": "reviewed"}).status_code == 200
    after = [c["conflict_id"] for c in client.get("/api/evidence-conflicts", headers=H(CCO)).json() if c["conflict_id"] in ids]
    assert after == ids  # resolving the first does not move it
    def last_column(clause: str) -> str:
        assert clause, "the query has no ORDER BY"
        return clause.replace('"', "").split(",")[-1].strip().split()[0]
    [lst] = _order_bys(client, "/api/evidence-conflicts")
    assert last_column(lst).endswith("evidence_conflict.conflict_id"), lst
    hist = [x for x in _order_bys(client, f"/api/evidence-conflicts/{ids[0]}/events") if "record_seq" in x]
    assert hist and last_column(hist[-1]).endswith("evidence_conflict_event.event_id"), hist


# ------------------------------------------------------------------------------------------------ history and assurance
def _control_with_evidence(client):
    """A control with one required manual test, a PASS run over October with fresh evidence, and an obligation on it
    (CONTROL leaf): (control implementation, obligation lineage, evidence)."""
    from app.db import AdminSession
    from app.models import (ControlTestPolicy, ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion,
                            Subject, TestDefinition, new_id)
    from app.platform import digest, next_record_seq
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    ci = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": "C_ICT_INCIDENT_PROCESS", "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]}).json()
    ci_id = ci["control_implementation_id"]
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci_id)).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        lineage = new_id()
        ast = {"node_type": "CONTROL", "node_id": "leaf", "control_key": "C_ICT_INCIDENT_PROCESS"}
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=sid,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle="ADOPTED", valid_from=t0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=t0,
                                             record_seq=next_record_seq(db)))
        db.commit()
        td_id = td.test_definition_id
    eid = _evidence(client)
    assert client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td_id, "occurred_at": OCT.isoformat(), "window_from": OCT.isoformat(),
        "window_to": NOV.isoformat(), "result": "PASS", "source_system": "probe", "evidence_ids": [eid]}).status_code == 200
    return ci_id, lineage, eid


def test_resolution_never_rewrites_history_or_verification(client, org):
    """While the conflict is open the control is REQUIRES_REVIEW (STA §04) and a snapshot frozen then says so; after the
    resolution a new evaluation is OPERATING and a new snapshot PROVEN, while the earlier evaluation, read again at its
    own record cutoff, and the frozen snapshot, read and reproduced, are unchanged. Integrity verification of the member
    evidence is the same before and after: a conflict is a relationship, never an artifact state (EVD-REQ-019)."""
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlEvaluation, ControlImplementation
    ci_id, lineage, eid = _control_with_evidence(client)
    window = {"window_from": OCT.isoformat(), "window_to": NOV.isoformat()}
    period = {"period_from": OCT.isoformat(), "period_to": NOV.isoformat()}
    other = _evidence(client)
    cid = _open(client, [eid, other]).json()["conflict_id"]
    during = client.post(f"/api/controls/{ci_id}/evaluate", headers=H(OWNER), json=window).json()
    assert during["state"] == "REQUIRES_REVIEW" and "SOURCE_CONFLICT" in during["reason_codes"]
    frozen = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=period).json()
    assert frozen["state"] == "REQUIRES_REVIEW"
    verified_before = client.post(f"/api/evidence/{eid}/verify", headers=H(CCO)).json()["result"]
    with AdminSession() as db:
        cutoff = db.get(ControlEvaluation, during["evaluation_id"]).record_cutoff_seq
    assert _resolve(client, cid, {"reason": "Both sources agree after re-extraction"}).status_code == 200
    after = client.post(f"/api/controls/{ci_id}/evaluate", headers=H(OWNER), json=window).json()
    assert after["state"] == "OPERATING" and after["evaluation_id"] != during["evaluation_id"]
    with AdminSession() as db:
        then = asr.control_state_at(db, db.get(ControlImplementation, ci_id), OCT, NOV, cutoff)
        db.rollback()
    assert (then["state"], then["evaluation_id"]) == ("REQUIRES_REVIEW", during["evaluation_id"])
    stored = client.get(f"/api/snapshots/{frozen['snapshot_id']}", headers=H(CCO)).json()
    assert (stored["state"], stored["snapshot_hash"]) == ("REQUIRES_REVIEW", frozen["snapshot_hash"])
    rep = client.get(f"/api/snapshots/{frozen['snapshot_id']}/reproduce", headers=H(CCO)).json()
    assert (rep["status"], rep["reproduced_state"]) == ("REPRODUCED", "REQUIRES_REVIEW")
    assert client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=period).json()["state"] == "PROVEN"
    assert client.post(f"/api/evidence/{eid}/verify", headers=H(CCO)).json()["result"] == verified_before == "VERIFIED"


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and row locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_concurrent_resolutions_resolve_once(client, org, pg, monkeypatch):
    """Deterministic interleaving: the first resolution is held inside its transaction, after its writes and before its
    commit, while the second starts. The second must wait for the first and then find the conflict RESOLVED (409),
    never act on a stale OPEN read. Was: the second read OPEN, waited only on the record sequence, then tried to resolve
    again; migration 0010's finality trigger stopped the write, but as an unhandled database error (HTTP 500), not the
    409 the engine answers for a resolved conflict (without that trigger it would have been a second resolution)."""
    from app.db import SessionLocal, set_principal
    from app.engines import governance
    from app.models import Actor, EvidenceConflict
    from app.security import Ctx
    a, b = _evidence(client), _evidence(client)
    cid = _open(client, [a, b]).json()["conflict_id"]
    held, release = threading.Event(), threading.Event()
    real_audit = governance.audit

    def holding_audit(db, **kw):
        if threading.current_thread().name == "first" and kw.get("action") == "evidence_conflict.resolved":
            held.set()
            release.wait(30)
        return real_audit(db, **kw)
    monkeypatch.setattr(governance, "audit", holding_audit)
    results: dict[str, object] = {}

    def resolve(name, reason):
        try:
            with SessionLocal() as db:
                set_principal(db, TA, CCO)
                ctx = Ctx(db=db, actor=db.get(Actor, CCO))
                # As the route does (ctx.scoped): the conflict is read into the session before the engine runs. The
                # reference is kept (the identity map holds rows weakly), so a locked select that did not re-read the
                # row would check this stale state instead of the committed one.
                preloaded = ctx.scoped(db.get(EvidenceConflict, cid), "conflict")
                governance.resolve_evidence_conflict(db, tenant_id=TA, actor_id=CCO, actor=ctx.actor,
                                                     conflict_id=cid, reason=reason)
                del preloaded
                db.commit()
                results[name] = "ok"
        except HTTPException as e:
            results[name] = e.status_code
        except Exception as e:  # noqa: BLE001
            results[name] = repr(e)

    first = threading.Thread(target=resolve, args=("first", "first disposition"), name="first")
    second = threading.Thread(target=resolve, args=("second", "second disposition"), name="second")
    first.start()
    assert held.wait(30)
    second.start()
    time.sleep(2)
    assert second.is_alive()  # the second waits for the first's transaction
    release.set()
    first.join(60)
    second.join(60)
    assert results == {"first": "ok", "second": 409}, results
    mine = _of(_rows(), cid)
    assert [e.event_type for e in mine["events"]] == ["OPENED", "RESOLVED"]
    assert len([x for x in mine["audits"] if x.action == "evidence_conflict.resolved"]) == 1
    assert mine["conflicts"][0].disposition == "first disposition"
