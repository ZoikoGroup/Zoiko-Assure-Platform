"""Manual issue raising from an assurance result: ZA-PRD-001 §13 WF-03 Obligation Dossier "request remediation" and
WF-05 Assurance Period View "create issue" (POST /api/obligations/{lineage}/issues; engines/governance.py
raise_obligation_issue).

Requirements exercised: ZA-STA-001 §17 (issue triggers FAILING / NOT_PROVEN / REQUIRES_REVIEW; "create/link issue
unless an existing open issue already covers"; closure verified by a new AssuranceSnapshot), STA-REQ-023 (no assurance
mutation), ZA-WRK-001 §17 (deduplicate), ZA-GLS-001 §07 (OPEN; a closed issue is never reopened) and §08 (severity and
priority are separate dimensions), ZA-EVT-001 issue.created, ZA-API-001 §08 / API-REQ-005 (Idempotency-Key replay and
conflict), API-REQ-014 (unknown fields refused), QA-REQ-019 (another organisation's identifiers answer as unknown),
IAM-REQ-007/010 (deny by default; entity scope does not reach state-changing routes), DAT-REQ-014 (outbox in the same
transaction). Every test builds its own records.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal
from test_authorization import db_fingerprint, denials

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-mic-{TAG}", f"tenant-micb-{TAG}"
ENT, SUBJ = f"entity-mic-{TAG}", f"subj-mic-{TAG}"
ENT_B, SUBJ_B = f"entity-micb-{TAG}", f"subj-micb-{TAG}"
CCO, OWNER, AUDIT, ANALYST, RISK, LEGAL = (f"mic-{r}-{TAG}" for r in ("cco", "owner", "audit", "analyst", "risk", "legal"))
SCOPED = f"mic-scoped-{TAG}"  # CONTROL_OWNER for ENT only (LEGAL_ENTITY scope)
B_CCO = f"mic-b-cco-{TAG}"
KEY = "C_ICT_INCIDENT_PROCESS"
OPERATION = "issue.raise_from_snapshot"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV, DEC = (datetime(2026, m, 1, tzinfo=timezone.utc) for m in (9, 10, 11, 12))
RULES = {"FAILING": ("OBLIGATION_FAILING", "HIGH", "P1"), "NOT_PROVEN": ("PROOF_GAP", "MEDIUM", "P2"),
         "REQUIRES_REVIEW": ("ASSURANCE_REVIEW", "HIGH", "P1")}


@pytest.fixture(scope="module")
def org(client):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, RoleAssignment, Subject, Tenant
    from app.platform import next_record_seq
    with AdminSession() as db:
        for tenant, ent, subj in ((TA, ENT, SUBJ), (TB, ENT_B, SUBJ_B)):
            db.add(Tenant(tenant_id=tenant, legal_name=f"{tenant} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=ent, tenant_id=tenant, legal_name=f"{ent} AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=subj))
            db.add(Subject(subject_id=subj, tenant_id=tenant, entity_id=ent, subject_type="LEGAL_ENTITY",
                           display_name=ent))
        for aid, tenant, roles in ((CCO, TA, ["CCO"]), (OWNER, TA, ["CONTROL_OWNER"]), (AUDIT, TA, ["INTERNAL_AUDIT"]),
                                   (ANALYST, TA, ["REGULATORY_ANALYST"]), (RISK, TA, ["RISK_APPROVER"]),
                                   (LEGAL, TA, ["LEGAL_REVIEWER"]), (SCOPED, TA, []), (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=roles)
        db.add(RoleAssignment(tenant_id=TA, principal_id=SCOPED, role_code="CONTROL_OWNER", scope_type="LEGAL_ENTITY",
                              scope_id=ENT, valid_from=datetime.now(timezone.utc) - timedelta(hours=1), status="ACTIVE",
                              source="TEST", reason="entity-scoped probe", record_seq=next_record_seq(db)))
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ records
def _subject(tenant=TA, ent=ENT):
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=ent, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    return sid


def _obligation(subject, tenant=TA, ent=ENT):
    """An adopted obligation with a one-control satisfaction expression: (lineage, current revision)."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage, revision = new_id(), new_id()
    ast = {"node_type": "ALL", "node_id": "root",
           "children": [{"node_type": "CONTROL", "node_id": "leaf", "control_key": KEY}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")
                          ).scalar_one()
        db.add(ObligationInstance(revision_id=revision, lineage_id=lineage, tenant_id=tenant, entity_id=ent,
                                  subject_id=subject, obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage, revision


def _new_revision(lineage, prior_id):
    """Record the obligation's next revision: the prior one closes at the sequence the new one starts."""
    from app.db import AdminSession
    from app.models import ObligationInstance, new_id
    from app.platform import next_record_seq
    rid = new_id()
    with AdminSession() as db:
        prior = db.get(ObligationInstance, prior_id)
        seq = next_record_seq(db)
        prior.record_to_seq = seq
        db.add(ObligationInstance(revision_id=rid, lineage_id=lineage, tenant_id=prior.tenant_id,
                                  entity_id=prior.entity_id, subject_id=prior.subject_id,
                                  obligation_definition_id=prior.obligation_definition_id,
                                  determination_revision_id="probe-2", attestation_id="probe-2", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=seq))
        db.commit()
    return rid


def _result(lineage, revision, state, start=SEP, end=OCT, tenant=TA, ent=ENT):
    """A persisted period result with no issue covering it, as one recorded before snapshots raised issues themselves:
    written with its assurance.snapshot_created event, as asr.compute_snapshot records a snapshot."""
    from app.db import AdminSession
    from app.models import AssuranceSnapshot
    from app.platform import emit, next_record_seq
    with AdminSession() as db:
        seq = next_record_seq(db)
        snap = AssuranceSnapshot(tenant_id=tenant, entity_id=ent, obligation_lineage_id=lineage,
                                 obligation_revision_id=revision, period_from=start, period_to=end,
                                 record_cutoff_seq=seq, perspective="RECONSTRUCTED_NOW", expression_version_id="probe",
                                 rollup_engine_version="probe", state=state, segments=[], dependency_manifest={},
                                 dependency_digest="0" * 64, snapshot_hash=secrets.token_hex(32), created_by=CCO)
        db.add(snap)
        db.flush()
        emit(db, event_type="assurance.snapshot_created", aggregate_type="AssuranceSnapshot",
             aggregate_id=snap.snapshot_id, tenant_id=tenant, entity_id=ent, record_seq=seq,
             payload={"obligation_lineage_id": lineage, "state": state})
        db.commit()
        return snap.snapshot_id


def _control(client, subject):
    r = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": KEY, "subject_id": subject, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert r.status_code == 200, r.text
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == r.json()["control_implementation_id"])).scalar_one()
        return db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                          ).scalar_one().test_definition_id


def _pass(client, td, start, end):
    ev = client.post("/api/evidence/upload", headers=H(OWNER),
                     files={"file": ("r.txt", secrets.token_bytes(16), "text/plain")},
                     data={"entity_id": ENT, "title": "Retest", "coverage_from": start.isoformat(),
                           "coverage_to": end.isoformat(), "validity_days": "400"})
    assert ev.status_code == 200, ev.text
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": "PASS", "source_system": "probe",
        "evidence_ids": [ev.json()["evidence_id"]]})
    assert r.status_code == 200, r.text


def _snapshot(client, lineage, start, end):
    r = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                    json={"period_from": start.isoformat(), "period_to": end.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ reads
def _url(lineage):
    return f"/api/obligations/{lineage}/issues"


def _raise(client, lineage, snapshot_id, revision_id, actor=CCO, reason="Remediate this condition", key=None, **extra):
    headers = {**H(actor), **({"Idempotency-Key": key} if key is not None else {})}
    return client.post(_url(lineage), headers=headers, json={
        "assurance_snapshot_id": snapshot_id, "obligation_revision_id": revision_id, "reason": reason, **extra})


def _rows(model, **where):
    from app.db import AdminSession
    with AdminSession() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.execute(q).scalars().all()


def _issues(lineage, tenant=TA):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        return db.execute(select(Issue).where(Issue.tenant_id == tenant, Issue.dedupe_key.like(f"%:{lineage}"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()


def _assurance_records(lineage):
    """Everything that states the obligation's assurance: snapshots and obligation revisions, as stored."""
    from app.models import AssuranceSnapshot, ObligationInstance
    snaps = sorted((s.snapshot_id, s.state, s.snapshot_hash, s.lifecycle)
                   for s in _rows(AssuranceSnapshot, obligation_lineage_id=lineage))
    revs = sorted((r.revision_id, r.lifecycle, r.record_from_seq, r.record_to_seq)
                  for r in _rows(ObligationInstance, lineage_id=lineage))
    return snaps, revs


# ------------------------------------------------------------------------------------------------ WF-03
def test_wf03_request_remediation_links_the_open_issue_or_raises_a_new_one(client, org):
    from app.models import AuditEvent, Issue, IssueEvent, OutboxEvent
    lineage, revision = _obligation(_subject())
    snap = _snapshot(client, lineage, SEP, OCT)  # no test result: NOT_PROVEN, and the snapshot raises its proof gap
    sid = snap["snapshot_id"]
    assert snap["state"] == "NOT_PROVEN"
    [auto] = _issues(lineage)
    assurance_before = _assurance_records(lineage)

    linked = _raise(client, lineage, sid, revision, actor=OWNER, reason="Please remediate before the audit")
    assert linked.status_code == 200, linked.text
    assert linked.json()["issue_id"] == auto.issue_id and linked.json()["state"] == "OPEN"
    [link] = _rows(AuditEvent, action="issue.snapshot_linked", object_id=auto.issue_id)
    assert (link.actor_id, link.actor_type, link.new_state, link.tenant_id) == (OWNER, "HUMAN", "OPEN", TA)
    assert sid in link.reason and "Please remediate before the audit" in link.reason
    assert len(_rows(IssueEvent, issue_id=auto.issue_id)) == 1  # linking is not an issue event
    assert _rows(OutboxEvent, event_type="issue.created", aggregate_id=auto.issue_id) == []

    # The open issue is cancelled; the condition persists, so a new request raises a new issue (never a reopening).
    r = client.post(f"/api/issues/{auto.issue_id}/transition", headers=H(OWNER),
                    json={"state": "CANCELLED", "reason": "Opened before the obligation owner was known"})
    assert r.status_code == 200, r.text
    created = _raise(client, lineage, sid, revision, actor=CCO, reason="Proof gap persists; remediate")
    assert created.status_code == 201, created.text
    body = created.json()
    iid = body["issue_id"]
    assert iid != auto.issue_id
    assert set(body) == {c.key for c in Issue.__table__.columns}  # the established issue representation
    assert {k: body[k] for k in ("tenant_id", "issue_type", "severity", "priority", "state", "source_ref_type",
                                 "source_ref_id", "owner_actor_id", "due_at", "dedupe_key")} == {
        "tenant_id": TA, "issue_type": "PROOF_GAP", "severity": "MEDIUM", "priority": "P2", "state": "OPEN",
        "source_ref_type": "AssuranceSnapshot", "source_ref_id": sid, "owner_actor_id": None, "due_at": None,
        "dedupe_key": f"PROOF_GAP:{lineage}"}
    assert body["title"] == auto.title  # the title the snapshot gives its own issue
    listed = {i["issue_id"]: i for i in client.get("/api/issues", headers=H(CCO)).json()}
    assert listed[iid] == body

    [ev] = _rows(IssueEvent, issue_id=iid)
    assert (ev.event_type, ev.from_state, ev.to_state, ev.actor_id, ev.reason, ev.owner_actor_id) == (
        "CREATED", None, "OPEN", CCO, "Proof gap persists; remediate", None)
    [aud] = _rows(AuditEvent, action="issue.create", object_id=iid)
    assert (aud.actor_id, aud.actor_type, aud.new_state, aud.record_seq, aud.tenant_id) == (
        CCO, "HUMAN", "OPEN", ev.record_seq, TA)
    [out] = _rows(OutboxEvent, event_type="issue.created", aggregate_id=iid)
    assert (out.record_seq, out.tenant_id, out.entity_id, out.aggregate_type) == (ev.record_seq, TA, ENT, "Issue")
    assert out.payload == {"issue_id": iid, "issue_type": "PROOF_GAP", "obligation_lineage_id": lineage,
                           "obligation_revision_id": revision, "assurance_snapshot_id": sid}
    assert out.actor_ref["actor_id"] == CCO and out.actor_ref["actor_type"] == "HUMAN"
    assert len(_rows(OutboxEvent, event_type="assurance.issue_created", aggregate_id=iid)) == 1
    # Detecting the gap is the snapshot's event, recorded once when it raised its own issue; a request repeats nothing.
    assert len(_rows(OutboxEvent, event_type="assurance.proof_gap_detected", aggregate_id=sid)) == 1
    [snap_event] = _rows(OutboxEvent, event_type="assurance.snapshot_created", aggregate_id=sid)
    assert ev.record_seq > snap_event.record_seq

    history = client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()
    assert [(h["event_type"], h["actor_id"], h["to_state"]) for h in history] == [("CREATED", CCO, "OPEN")]
    assert _assurance_records(lineage) == assurance_before  # STA-REQ-023: no assurance or obligation record changed


# ------------------------------------------------------------------------------------------------ WF-05
@pytest.mark.parametrize("state", sorted(RULES))
def test_wf05_create_issue_from_the_selected_period(client, org, state):
    from app.models import IssueEvent
    lineage, revision = _obligation(_subject())
    q3 = _result(lineage, revision, state, SEP, OCT)
    r = _raise(client, lineage, q3, revision, actor=OWNER, reason="Create issue for Q3")
    assert r.status_code == 201, r.text
    itype, severity, priority = RULES[state]
    body = r.json()
    assert (body["issue_type"], body["severity"], body["priority"], body["state"], body["source_ref_id"]) == (
        itype, severity, priority, "OPEN", q3)
    [ev] = _rows(IssueEvent, issue_id=body["issue_id"])
    assert (ev.event_type, ev.actor_id, ev.reason) == ("CREATED", OWNER, "Create issue for Q3")
    # Another period with the same result is the same open condition (type + obligation): linked, not duplicated.
    q4 = _result(lineage, revision, state, OCT, NOV)
    again = _raise(client, lineage, q4, revision, actor=CCO, reason="Create issue for Q4")
    assert again.status_code == 200 and again.json()["issue_id"] == body["issue_id"]
    assert len(_issues(lineage)) == 1


def test_a_result_without_an_issue_trigger_is_refused(client, org):
    lineage, revision = _obligation(_subject())
    proven = _result(lineage, revision, "PROVEN")
    before = db_fingerprint()
    r = _raise(client, lineage, proven, revision)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "ISSUE_NOT_ELIGIBLE", r.text
    assert db_fingerprint() == before


# ------------------------------------------------------------------------------------------------ validation
def test_missing_invalid_and_client_classified_fields_are_refused(client, org):
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    good = {"assurance_snapshot_id": snap, "obligation_revision_id": revision, "reason": "Remediate"}
    bodies = [{k: v for k, v in good.items() if k != field} for field in good]
    bodies += [{**good, "reason": ""}, {**good, "reason": "x" * 2001}, {**good, "assurance_snapshot_id": ""},
               {**good, "assurance_snapshot_id": "x" * 37}, {**good, "obligation_revision_id": None},
               {**good, "reason": 7}]
    # Classification, ownership and workflow state are never the caller's to set (API-REQ-014: refused, not ignored).
    bodies += [{**good, field: value} for field, value in (
        ("issue_type", "PROOF_GAP"), ("severity", "CRITICAL"), ("priority", "P0"), ("state", "TRIAGED"),
        ("owner_actor_id", OWNER), ("due_at", "2026-12-01T00:00:00Z"), ("title", "Mine"), ("dedupe_key", "x"))]
    before = db_fingerprint()
    for body in bodies:
        r = client.post(_url(lineage), headers=H(CCO), json=body)
        assert r.status_code == 422 and isinstance(r.json()["detail"], list), (body, r.text)
    blank = _raise(client, lineage, snap, revision, reason="   ")
    assert (blank.status_code, blank.json()) == (422, {"detail": {"error": "REASON_REQUIRED",
                                                                  "message": "A reason is required."}})
    assert client.post(_url(lineage), headers=H(CCO), content=b"not json").status_code == 422
    assert db_fingerprint() == before


def test_unknown_references_are_refused_and_nothing_is_written(client, org):
    lineage, revision = _obligation(_subject())
    other_lineage, other_revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    other_snap = _result(other_lineage, other_revision, "FAILING")
    before = db_fingerprint()
    cases = [
        ("no-such-obligation", snap, revision, 404, "obligation not found"),
        (lineage, snap, "no-such-revision", 404, "OBLIGATION_REVISION_NOT_FOUND"),
        (lineage, "no-such-snapshot", revision, 404, "SNAPSHOT_NOT_FOUND"),
        (lineage, other_snap, revision, 422, "SNAPSHOT_SCOPE_MISMATCH"),
        (lineage, snap, other_revision, 422, "OBLIGATION_REVISION_SCOPE_MISMATCH"),
    ]
    for path_lineage, sid, rid, status, code in cases:
        r = _raise(client, path_lineage, sid, rid)
        detail = r.json()["detail"]
        assert r.status_code == status and (detail if isinstance(detail, str) else detail["error"]) == code, r.text
    assert db_fingerprint() == before
    assert _issues(lineage) == [] and _issues(other_lineage) == []


def test_an_issue_is_never_attached_to_another_revision_of_the_obligation(client, org):
    from app.models import OutboxEvent
    lineage, first = _obligation(_subject())
    second = _new_revision(lineage, first)
    old = _result(lineage, first, "FAILING", SEP, OCT)  # computed while the first revision was current
    new = _result(lineage, second, "FAILING", OCT, NOV)
    before = db_fingerprint()
    for sid, rid, computed_for in ((old, second, first), (new, first, second)):
        r = _raise(client, lineage, sid, rid)
        assert r.status_code == 409 and r.json()["detail"]["error"] == "OBLIGATION_REVISION_MISMATCH", r.text
        assert computed_for in r.json()["detail"]["message"]
    assert db_fingerprint() == before
    r = _raise(client, lineage, old, first)  # the revision the caller reviewed, exactly
    assert r.status_code == 201, r.text
    assert r.json()["source_ref_id"] == old
    [out] = _rows(OutboxEvent, event_type="issue.created", aggregate_id=r.json()["issue_id"])
    assert (out.payload["obligation_revision_id"], out.payload["assurance_snapshot_id"]) == (first, old)


# ------------------------------------------------------------------------------------------------ isolation and authority
def test_another_organisations_records_answer_as_unknown(client, org):
    a_lineage, a_rev = _obligation(_subject())
    a_snap = _result(a_lineage, a_rev, "FAILING")
    b_lineage, b_rev = _obligation(_subject(TB, ENT_B), tenant=TB, ent=ENT_B)
    b_snap = _result(b_lineage, b_rev, "FAILING", tenant=TB, ent=ENT_B)
    before = db_fingerprint()
    unknown = _raise(client, "no-such-obligation", a_snap, a_rev, actor=B_CCO)
    foreign = _raise(client, a_lineage, a_snap, a_rev, actor=B_CCO)
    assert (foreign.status_code, foreign.json()) == (unknown.status_code, unknown.json()) == (
        404, {"detail": "obligation not found"})
    for body_snap, body_rev, foreign_snap, foreign_rev in (("no-such-snapshot", a_rev, b_snap, a_rev),
                                                           (a_snap, "no-such-revision", a_snap, b_rev)):
        unknown = _raise(client, a_lineage, body_snap, body_rev)
        foreign = _raise(client, a_lineage, foreign_snap, foreign_rev)
        assert unknown.status_code == 404 and (foreign.status_code, foreign.json()) == (unknown.status_code,
                                                                                         unknown.json())
    assert db_fingerprint() == before
    own = _raise(client, b_lineage, b_snap, b_rev, actor=B_CCO)
    assert own.status_code == 201 and own.json()["tenant_id"] == TB
    bid = own.json()["issue_id"]
    assert bid not in {i["issue_id"] for i in client.get("/api/issues", headers=H(CCO)).json()}
    assert client.get(f"/api/issues/{bid}/events", headers=H(CCO)).status_code == 404
    assert _issues(a_lineage) == []


def test_principals_without_manage_issues_are_refused_before_anything_is_written(client, org):
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    before = db_fingerprint()
    # SCOPED holds CONTROL_OWNER for this very entity: entity scope does not reach state-changing routes (fail closed).
    for actor in (AUDIT, ANALYST, RISK, LEGAL, SCOPED):
        n = denials(actor)
        r = _raise(client, lineage, snap, revision, actor=actor)
        assert r.status_code == 403, (actor, r.text)
        assert client.post(_url(lineage), headers=H(actor), json={}).status_code == 403  # before body validation
        assert denials(actor) == n + 2
    assert db_fingerprint() == before
    assert _raise(client, lineage, snap, revision, actor=OWNER).status_code == 201  # both manage_issues roles
    assert _raise(client, lineage, snap, revision, actor=CCO).status_code == 200


# ------------------------------------------------------------------------------------------------ retries and atomicity
def test_a_retry_with_the_same_idempotency_key_returns_the_original_outcome(client, org):
    from app.models import AuditEvent, IdempotencyRecord
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    key = f"mic-create-{TAG}"
    first = _raise(client, lineage, snap, revision, key=key)
    assert first.status_code == 201, first.text
    iid = first.json()["issue_id"]
    before = db_fingerprint()
    again = _raise(client, lineage, snap, revision, key=key)
    assert (again.status_code, again.json()) == (201, first.json())
    other = _raise(client, lineage, snap, revision, key=key, reason="A different request")
    assert other.status_code == 409 and other.json()["detail"]["error"] == "idempotency_conflict"
    bad = _raise(client, lineage, snap, revision, key="k" * 201)
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "IDEMPOTENCY_KEY_INVALID"
    assert db_fingerprint() == before
    # A linked outcome replays as linked, and the link is recorded once.
    link_key = f"mic-link-{TAG}"
    linked = _raise(client, lineage, snap, revision, actor=OWNER, key=link_key, reason="Second request")
    assert linked.status_code == 200 and linked.json()["issue_id"] == iid
    before = db_fingerprint()
    assert _raise(client, lineage, snap, revision, actor=OWNER, key=link_key, reason="Second request").status_code == 200
    assert db_fingerprint() == before
    assert len(_rows(AuditEvent, action="issue.snapshot_linked", object_id=iid)) == 1
    stored = {r.idempotency_key: r.result_ref for r in _rows(IdempotencyRecord, tenant_id=TA, operation_code=OPERATION)}
    assert stored[key] == f"CREATED:{iid}" and stored[link_key] == f"LINKED:{iid}"


def test_repeated_requests_without_a_key_link_to_the_open_issue(client, org):
    from app.models import AuditEvent, IssueEvent, OutboxEvent
    lineage, revision = _obligation(_subject())
    gap = _result(lineage, revision, "NOT_PROVEN")
    a = _raise(client, lineage, gap, revision, actor=CCO, reason="first")
    b = _raise(client, lineage, gap, revision, actor=OWNER, reason="second")
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["issue_id"] == b.json()["issue_id"]
    [issue] = _issues(lineage)
    assert len(_rows(IssueEvent, issue_id=issue.issue_id)) == 1
    assert len(_rows(AuditEvent, action="issue.create", object_id=issue.issue_id)) == 1
    assert len(_rows(OutboxEvent, event_type="issue.created", aggregate_id=issue.issue_id)) == 1
    [link] = _rows(AuditEvent, action="issue.snapshot_linked", object_id=issue.issue_id)
    assert link.actor_id == OWNER and link.reason.endswith("second")
    # A different result of the same obligation is a different condition; another obligation is never merged.
    failing = _result(lineage, revision, "FAILING", OCT, NOV)
    c = _raise(client, lineage, failing, revision)
    other_lineage, other_rev = _obligation(_subject())
    d = _raise(client, other_lineage, _result(other_lineage, other_rev, "NOT_PROVEN"), other_rev)
    assert (c.status_code, d.status_code) == (201, 201)
    assert len({a.json()["issue_id"], c.json()["issue_id"], d.json()["issue_id"]}) == 3
    assert c.json()["issue_type"] == "OBLIGATION_FAILING"


@pytest.mark.parametrize("failing", ["issue_event", "outbox_event"])
def test_a_failed_event_write_leaves_nothing_and_the_retry_raises_once(client, org, monkeypatch, failing):
    from app.engines import governance
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    key = f"mic-atomic-{failing}-{TAG}"
    if failing == "outbox_event":
        real_emit = governance.emit

        def broken(db, **kw):
            if kw.get("event_type") == "issue.created":
                raise RuntimeError("outbox write failed")
            return real_emit(db, **kw)
        monkeypatch.setattr(governance, "emit", broken)
    else:
        real_created = governance.record_issue_created

        def broken(db, **kw):
            real_created(db, **kw)
            db.flush()  # the issue and its CREATED event reach the database, then the request fails
            raise RuntimeError("issue event write failed")
        monkeypatch.setattr(governance, "record_issue_created", broken)
    before = db_fingerprint()
    with pytest.raises(RuntimeError):
        _raise(client, lineage, snap, revision, key=key)
    monkeypatch.undo()
    assert db_fingerprint() == before  # no issue, event, audit, outbox or idempotency record, no sequence used
    first = _raise(client, lineage, snap, revision, key=key)
    again = _raise(client, lineage, snap, revision, key=key)
    assert (first.status_code, again.status_code) == (201, 201) and first.json() == again.json()
    assert len(_issues(lineage)) == 1


# ------------------------------------------------------------------------------------------------ existing workflow
def test_the_raised_issue_follows_the_existing_workflow_to_verified_closure(client, org):
    from app.models import OutboxEvent
    s = _subject()
    lineage, revision = _obligation(s)
    td = _control(client, s)
    old = _result(lineage, revision, "NOT_PROVEN", SEP, OCT)
    r = _raise(client, lineage, old, revision, reason="Remediate the Q3 proof gap")
    assert r.status_code == 201, r.text
    iid = r.json()["issue_id"]
    move = lambda state: client.post(f"/api/issues/{iid}/transition", headers=H(OWNER),  # noqa: E731
                                     json={"state": state, "reason": "step"})
    assert move("TRIAGED").json()["detail"]["error"] == "ISSUE_OWNER_REQUIRED"  # GLS TRIAGED needs ownership
    assert client.post(f"/api/issues/{iid}/assign", headers=H(OWNER),
                       json={"owner_actor_id": OWNER, "reason": "Owner"}).status_code == 200
    for state in ("TRIAGED", "IN_REMEDIATION", "PENDING_VERIFICATION"):
        assert move(state).status_code == 200
    stale = client.post(f"/api/issues/{iid}/close", headers=H(OWNER),
                        json={"verification_snapshot_id": old, "reason": "No retest"})
    assert stale.status_code == 409 and stale.json()["detail"]["error"] == "VERIFICATION_PREDATES_REMEDIATION"
    assert [i.state for i in _issues(lineage)] == ["PENDING_VERIFICATION"]  # the raising snapshot verifies nothing
    _pass(client, td, OCT, NOV)
    proven = _snapshot(client, lineage, OCT, NOV)
    assert proven["state"] == "PROVEN"
    closed = client.post(f"/api/issues/{iid}/close", headers=H(OWNER),
                         json={"verification_snapshot_id": proven["snapshot_id"], "reason": "Retest passed"})
    assert closed.status_code == 200 and closed.json()["state"] == "CLOSED", closed.text
    [out] = _rows(OutboxEvent, event_type="issue.closed", aggregate_id=iid)
    assert out.payload["verification_snapshot_id"] == proven["snapshot_id"]
    assert _raise(client, lineage, proven["snapshot_id"], revision).json()["detail"]["error"] == "ISSUE_NOT_ELIGIBLE"
    # Only an open issue covers a condition (STA §17): a closed issue is never reopened, a new request is a new issue.
    again = _raise(client, lineage, old, revision, reason="Raised again")
    assert again.status_code == 201 and again.json()["issue_id"] != iid
    assert [i.state for i in _issues(lineage)] == ["CLOSED", "OPEN"]


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def _thread(fn, errors):
    def run():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    t = threading.Thread(target=run)
    t.start()
    return t


def _pause_first_creation(monkeypatch):
    """Hold the first issue creation (after its request found no open issue, before anything is recorded) until
    released. Patched where every raising path calls it: raise_obligation_issue and the snapshot's _issue_hooks."""
    from app.engines import governance
    entered, go, real, calls = threading.Event(), threading.Event(), governance.record_issue_created, []

    def paused(db, **kw):
        if not calls:
            calls.append(1)
            entered.set()
            go.wait(20)
        return real(db, **kw)
    monkeypatch.setattr(governance, "record_issue_created", paused)
    return entered, go


def _raise_in_session(lineage, snap, revision, out, key=None, reason="Concurrent request"):
    from app.db import SessionLocal, set_principal
    from app.engines import governance
    with SessionLocal() as db:
        set_principal(db, TA, CCO)
        issue, created = governance.raise_obligation_issue(
            db, tenant_id=TA, actor=CCO, obligation_lineage_id=lineage, obligation_revision_id=revision,
            snapshot_id=snap, reason=reason, idempotency_key=key)
        db.commit()
        out.append((issue.issue_id, created))


def _second_waits_for_first(monkeypatch, first, second):
    """Run ``first`` until it is inside its issue creation, then ``second``: ``second`` must still be waiting two
    seconds later (it is serialised behind ``first``), and both must finish once ``first`` is released."""
    entered, go = _pause_first_creation(monkeypatch)
    errors = []
    t1 = _thread(first, errors)
    assert entered.wait(20), errors
    t2 = _thread(second, errors)
    t2.join(2)
    waited = t2.is_alive()
    go.set()
    t1.join(60)
    t2.join(60)
    assert not errors, errors
    assert waited, "the second request did not wait for the first one's issue creation to commit"


def test_a_concurrent_request_waits_and_links_the_issue_the_first_one_raised(client, org, pg, monkeypatch):
    from app.models import AuditEvent, IssueEvent
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    first, second = [], []
    _second_waits_for_first(monkeypatch, lambda: _raise_in_session(lineage, snap, revision, first),
                            lambda: _raise_in_session(lineage, snap, revision, second))
    [issue] = _issues(lineage)
    assert (first, second) == ([(issue.issue_id, True)], [(issue.issue_id, False)])
    assert len(_rows(IssueEvent, issue_id=issue.issue_id)) == 1
    assert len(_rows(AuditEvent, action="issue.snapshot_linked", object_id=issue.issue_id)) == 1


def test_a_concurrent_retry_with_the_same_key_replays_the_first_request(client, org, pg, monkeypatch):
    from app.models import AuditEvent, IdempotencyRecord, IssueEvent
    lineage, revision = _obligation(_subject())
    snap = _result(lineage, revision, "FAILING")
    key, first, retry = f"mic-race-{TAG}", [], []
    _second_waits_for_first(monkeypatch, lambda: _raise_in_session(lineage, snap, revision, first, key=key),
                            lambda: _raise_in_session(lineage, snap, revision, retry, key=key))
    [issue] = _issues(lineage)
    assert first == retry == [(issue.issue_id, True)]  # the retry reports the original outcome
    assert len(_rows(IssueEvent, issue_id=issue.issue_id)) == 1
    assert _rows(AuditEvent, action="issue.snapshot_linked", object_id=issue.issue_id) == []
    assert len(_rows(IdempotencyRecord, tenant_id=TA, operation_code=OPERATION, idempotency_key=key)) == 1


def test_a_new_snapshot_of_the_same_condition_waits_and_raises_no_second_issue(client, org, pg, monkeypatch):
    from app.db import SessionLocal, set_principal
    from app.engines import assurance as asr
    lineage, revision = _obligation(_subject())
    old = _result(lineage, revision, "NOT_PROVEN", SEP, OCT)  # no issue covers it yet
    raised, states = [], []

    def snapshot():
        with SessionLocal() as db:
            set_principal(db, TA, CCO)
            r = asr.compute_snapshot(db, tenant_id=TA, obligation_lineage_id=lineage, period_from=OCT, period_to=NOV,
                                     record_cutoff_seq=None, perspective="RECONSTRUCTED_NOW", actor_id=CCO)
            db.commit()
            states.append(r["state"])
    _second_waits_for_first(monkeypatch, lambda: _raise_in_session(lineage, old, revision, raised), snapshot)
    [issue] = _issues(lineage)
    assert states == ["NOT_PROVEN"] and raised == [(issue.issue_id, True)]
    assert (issue.state, issue.issue_type, issue.source_ref_id) == ("OPEN", "PROOF_GAP", old)
