"""Every issue begins with one CREATED event, and its creation reason describes its own source.

Controlling text and existing contracts:
  * governance.record_issue_created is the creation record every issue producer uses: one CREATED IssueEvent (from no
    state to the issue's initial state, append-only), the ``issue.create`` audit record at the same record sequence and
    the ``assurance.issue_created`` outbox event, in the producer's transaction (DAT-REQ-014). Its reason is the one the
    producer gives, else the issue title.
  * governance.issue_states_at reconstructs an issue's historical state from those events only (TIM-REQ-006/015): an
    issue without a CREATED event cannot be placed at any record cutoff (ISSUE_HISTORY_MISSING).
  * assurance.OBLIGATION_ISSUE_RULES names each snapshot-raised issue ("Obligation FAILING", "Proof gap", "Assurance
    requires review"); the creation reason is that name with the snapshot state, not "Proof gap" for every state.

Producers covered: the adopted-determination review flag (applicability._flag_review, DETERMINATION_REVIEW), evidence
and ledger integrity failures (evidence._integrity_incident, INTEGRITY_INCIDENT), persisted snapshots
(assurance._issue_hooks: OBLIGATION_FAILING / PROOF_GAP / ASSURANCE_REVIEW); control-level issues
(governance.raise_control_issue) are checked unchanged. Every test builds its own organisation and catalogue content.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(3).upper()
INSTR = f"ICE-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
TA = f"tenant-ice-{TAG.lower()}"
ENT = f"entity-ice-{TAG.lower()}"
CCO, OWNER, AUDIT = (f"ice-{r}-{TAG.lower()}" for r in ("cco", "owner", "audit"))
OCT, NOV = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)
S: dict = {}
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


# ------------------------------------------------------------------------------------------------ world
def _ast(entity_type: str) -> dict:
    def pred(nid, fact, typ, value):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [f"sm_{nid}"],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": "ice_root", "source_mappings": ["sm_ice_root"], "children": [
        pred("ice_1", "entity.established_in_eu", "BOOLEAN", True),
        pred("ice_2", "entity.financial_entity_type", "ENUM", entity_type)]}


def _vectors(t: str) -> list[dict]:
    facts = lambda eu, v: {"entity.established_in_eu": eu, "entity.financial_entity_type": v}  # noqa: E731
    return [{"vector_id": "P", "vector_class": "POSITIVE", "description": "in", "effective_at": "2025-06-01",
             "facts": facts(True, t), "expected_outcome": "APPLIES"},
            {"vector_id": "N", "vector_class": "NEGATIVE", "description": "out", "effective_at": "2025-06-01",
             "facts": facts(False, t), "expected_outcome": "DOES_NOT_APPLY"},
            {"vector_id": "U", "vector_class": "UNKNOWN", "description": "unknown", "effective_at": "2025-06-01",
             "facts": facts(True, {"unknown": "MISSING_REQUIRED_FACT"}), "expected_outcome": "UNCERTAIN"}]


@pytest.fixture(scope="module")
def world(client):
    """A probe organisation with a legal entity, a CCO, a control owner and an internal auditor, and a published rule of
    its own (APPLIES to EU credit institutions)."""
    from app.db import CELL_ID, AdminSession
    from app.models import Instrument, LegalEntity, ObligationDefinition, Subject, Tenant
    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Issue creation instrument", jurisdiction="EU", authority="Authority",
                          coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=ENT, tenant_id=TA, legal_name=f"{ENT} AG", country="DE", home_cell_id=CELL_ID,
                           subject_id=f"s-{ENT}"))
        db.add(Subject(subject_id=f"s-{ENT}", tenant_id=TA, entity_id=ENT, subject_type="LEGAL_ENTITY", display_name=ENT))
        for aid, roles in ((CCO, ["CCO"]), (OWNER, ["CONTROL_OWNER"]), (AUDIT, ["INTERNAL_AUDIT"])):
            add_principal(db, actor_id=aid, tenant_id=TA, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION", "title": "Issue Act",
        "locator": f"OJ L {TAG}/ice", "instrument_identifier": INSTR, "raw_text": "Credit institutions shall keep a register."})
    assert src.status_code == 200, src.text
    source = src.json()
    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": source["source_id"], "instrument_identifier": INSTR, "requirement_key": f"ICE-{TAG}-REQ",
        "title": "Register requirement", "locator": "Art. 1", "valid_from": "2025-01-01", "provisions": [
            {"locator": "Art. 1(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "keep register",
             "regulated_object": "register", "timing": "continuous", "normalized_meaning": "Keep a register.",
             "evidence_expectation": "Register"}]})
    assert rv.status_code in (200, 201), rv.text
    rv = rv.json()
    assert client.post(f"/api/catalog/requirements/{rv['requirement_version_id']}/verify", headers=H(LEGAL)).status_code == 200
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"ICE_{TAG}", title="Issue creation obligation", instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in rv["provisions"]], subject_type="LEGAL_ENTITY",
                                 coverage_mode="ENCODED", schedule_kind="CONTINUOUS", material=True)
        db.add(o)
        db.commit()
        S["odef"] = o.obligation_definition_id
    p0 = rv["provisions"][0]
    m = lambda sid: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],  # noqa: E731
                     "provision_id": p0["provision_id"], "authoritative_reference": p0["locator"],
                     "source_fingerprint": source["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": S["odef"], "authoring_mode": "AST", "ast": _ast("CREDIT_INSTITUTION"),
        "golden_vectors": _vectors("CREDIT_INSTITUTION"), "valid_from": "2025-01-01", "change_class": "EXECUTABLE_OUTCOME",
        "source_mappings": [m("sm_ice_root"), m("sm_ice_1"), m("sm_ice_2")]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        assert client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor)).status_code == 200, step
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"ice-{TAG}", "changelog": "ice"})
    assert r.status_code == 200, r.text
    return True


# ------------------------------------------------------------------------------------------------ records
def _creation_record(issue_id: str) -> dict:
    """Every event, audit record and outbox event written for one issue."""
    from app.db import AdminSession
    from app.models import AuditEvent, IssueEvent, OutboxEvent
    with AdminSession() as db:
        events = db.execute(select(IssueEvent).where(IssueEvent.issue_id == issue_id)
                            .order_by(IssueEvent.record_seq, IssueEvent.event_id)).scalars().all()
        audits = db.execute(select(AuditEvent).where(AuditEvent.object_type == "Issue", AuditEvent.object_id == issue_id,
                                                     AuditEvent.action == "issue.create")).scalars().all()
        outbox = db.execute(select(OutboxEvent).where(OutboxEvent.aggregate_type == "Issue",
                                                      OutboxEvent.aggregate_id == issue_id,
                                                      OutboxEvent.event_type == "assurance.issue_created")).scalars().all()
        for row in (*events, *audits, *outbox):
            db.expunge(row)
    return {"events": events, "audits": audits, "outbox": outbox}


def _issues(**where):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        q = select(Issue).where(*(getattr(Issue, k) == v for k, v in where.items() if k != "dedupe_like"))
        if "dedupe_like" in where:
            q = q.where(Issue.dedupe_key.like(where["dedupe_like"]))
        rows = db.execute(q.order_by(Issue.created_at, Issue.issue_id)).scalars().all()
        for r in rows:
            db.expunge(r)
    return rows


def _assert_created_once(issue, reason: str, *, after_seq: int | None = None):
    """The existing creation contract: one CREATED event (no prior state -> the issue's initial state) by the system for
    an automatically raised issue, in the issue's organisation, with the issue.create audit record at the same record
    sequence and the assurance.issue_created outbox event; the CREATED event is the issue's first event."""
    rec = _creation_record(issue.issue_id)
    created = [e for e in rec["events"] if e.event_type == "CREATED"]
    assert len(created) == 1, [e.event_type for e in rec["events"]]
    ev = created[0]
    assert rec["events"][0].event_id == ev.event_id
    assert (ev.issue_id, ev.tenant_id, ev.from_state, ev.to_state, ev.actor_id) == (
        issue.issue_id, issue.tenant_id, None, "OPEN", "system")
    assert ev.reason == reason, ev.reason
    assert ev.owner_actor_id == issue.owner_actor_id
    if after_seq is not None:
        assert ev.record_seq > after_seq
    [audit] = rec["audits"]
    assert (audit.tenant_id, audit.record_seq, audit.new_state, audit.reason) == (issue.tenant_id, ev.record_seq, "OPEN",
                                                                                  reason)
    [out] = rec["outbox"]
    assert out.tenant_id == issue.tenant_id and out.payload == {"title": issue.title, "severity": issue.severity}
    return ev


def _history(issue):
    """governance.issue_states_at at the current record watermark and just before the issue's CREATED event."""
    from app.db import AdminSession
    from app.engines import governance
    from app.platform import current_watermark
    with AdminSession() as db:
        now = current_watermark(db)["record_seq"]
        [ev] = [e for e in _creation_record(issue.issue_id)["events"] if e.event_type == "CREATED"]
        return (governance.issue_states_at(db, issue.tenant_id, [issue], now)[issue.issue_id],
                governance.issue_states_at(db, issue.tenant_id, [issue], ev.record_seq - 1)[issue.issue_id])


# ------------------------------------------------------------------------------------------------ DETERMINATION_REVIEW
def _subject(client, name):
    from app.db import CELL_ID, AdminSession
    from app.models import LegalEntity, Subject
    sid = f"s-{name}-{TAG.lower()}"
    with AdminSession() as db:
        db.add(LegalEntity(entity_id=f"e-{sid}", tenant_id=TA, legal_name=f"{name} Ltd", country="DE",
                           home_cell_id=CELL_ID, subject_id=sid))
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=f"e-{sid}", subject_type="LEGAL_ENTITY",
                       display_name=f"{name} Ltd"))
        db.commit()
    return sid


def _fact(client, sid, key, value, *, change=False):
    body = {"fact_key": key, "value": value, "valid_from": (NOW() - timedelta(days=40 if not change else 1)).isoformat()}
    if change:
        body |= {"change_kind": "PROSPECTIVE_CHANGE", "reason": "Entity type changed"}
    r = client.post(f"/api/subjects/{sid}/facts", headers=H(CCO), json=body)
    assert r.status_code == 200, r.text


def _flagged_adoption(client, name):
    """An adopted APPLIES determination, then a prospective fact change whose new evaluation (DOES_NOT_APPLY) flags it
    for review (applicability._flag_review). Returns (lineage, flagged revision)."""
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination
    sid = _subject(client, name)
    _fact(client, sid, "entity.established_in_eu", True)
    _fact(client, sid, "entity.financial_entity_type", "CREDIT_INSTITUTION")
    ev = client.post("/api/applicability/evaluations", headers=H(CCO),
                     json={"subject_id": sid, "obligation_definition_id": S["odef"]})
    assert ev.status_code == 200 and ev.json()["outcome"] == "APPLIES", ev.text
    lineage = ev.json()["current_determination"]["lineage_id"]
    r = client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO),
                    json={"decision": "ADOPT", "authority_basis": "Board delegation ICE"})
    assert r.status_code == 200, r.text
    _fact(client, sid, "entity.financial_entity_type", "PAYMENT_INSTITUTION", change=True)
    with AdminSession() as db:
        [flagged] = db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.lineage_id == lineage, ApplicabilityDetermination.record_to_seq.is_(None))).scalars()
        db.expunge(flagged)
    assert flagged.needs_review and flagged.state == "ADOPTED"
    return lineage, flagged


def test_a_flagged_adoption_raises_its_review_issue_with_one_created_event(client, world):
    """Was: the DETERMINATION_REVIEW issue was inserted with no CREATED event, audit record or outbox event, so its
    history could not be placed at any record cutoff (ISSUE_HISTORY_MISSING)."""
    lineage, flagged = _flagged_adoption(client, "review")
    [issue] = _issues(tenant_id=TA, issue_type="DETERMINATION_REVIEW", dedupe_like=f"detreview:{lineage}:%")
    # The reason is the review reason the flag recorded on the determination: the source of this issue, not a generic one.
    assert "differs from adopted APPLIES" in flagged.review_reason and issue.source_ref_id in flagged.review_reason
    ev = _assert_created_once(issue, flagged.review_reason, after_seq=flagged.record_from_seq - 1)
    assert ev.record_seq >= flagged.record_from_seq  # recorded with the flag, never before it
    now, before = _history(issue)
    assert (now["known"], now["state"], now["problem"]) == (True, "OPEN", None)
    assert before["known"] is False
    # The issue history endpoint shows the creation as recorded.
    hist = client.get(f"/api/issues/{issue.issue_id}/events", headers=H(CCO)).json()
    assert [(e["event_type"], e["to_state"], e["reason"]) for e in hist] == [("CREATED", "OPEN", flagged.review_reason)]


def test_re_evaluating_a_flagged_adoption_never_adds_a_second_created_event(client, world):
    """Repeated processing: re-running the evaluation on unchanged facts writes no further creation record for the
    issue already raised, and every review issue that exists has exactly one CREATED event."""
    lineage, flagged = _flagged_adoption(client, "repeat")
    [issue] = _issues(tenant_id=TA, issue_type="DETERMINATION_REVIEW", dedupe_like=f"detreview:{lineage}:%")
    for _ in range(2):
        assert client.post("/api/applicability/evaluations", headers=H(CCO),
                           json={"subject_id": flagged.subject_id, "obligation_definition_id": S["odef"]}).status_code == 200
    assert len([e for e in _creation_record(issue.issue_id)["events"] if e.event_type == "CREATED"]) == 1
    for each in _issues(tenant_id=TA, issue_type="DETERMINATION_REVIEW", dedupe_like=f"detreview:{lineage}:%"):
        rec = _creation_record(each.issue_id)
        assert [e.event_type for e in rec["events"]].count("CREATED") == 1 and len(rec["audits"]) == 1


# ------------------------------------------------------------------------------------------------ INTEGRITY_INCIDENT
def _evidence(client):
    r = client.post("/api/evidence/upload", headers=H(OWNER),
                    files={"file": ("proof.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": ENT, "title": "Probe record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "120"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def test_a_failed_evidence_verification_raises_its_incident_with_one_created_event(client, world):
    """Was: the INTEGRITY_INCIDENT issue had no CREATED event. Its creation reason is its own incident description (the
    helper's existing default, the issue title), never another issue type's reason."""
    from app.db import AdminSession
    from app.engines.evidence import OBJECT_DIR
    from app.models import EvidenceArtifact
    eid = _evidence(client)
    with AdminSession() as db:
        key = db.get(EvidenceArtifact, eid).object_key
    (OBJECT_DIR / f"{key}.bin").unlink()  # the stored payload is gone: verification must fail
    r = client.post(f"/api/evidence/{eid}/verify", headers=H(AUDIT))
    assert r.status_code == 200 and r.json()["result"] == "FAILED", r.text
    [issue] = _issues(tenant_id=TA, issue_type="INTEGRITY_INCIDENT", source_ref_id=eid)
    title = f"Evidence {eid} integrity verification FAILED"
    assert issue.title == title
    _assert_created_once(issue, title)
    now, before = _history(issue)
    assert (now["known"], now["state"], now["problem"]) == (True, "OPEN", None) and before["known"] is False
    # Repeated failed verification: each incident the evidence domain records has exactly one creation record.
    assert client.post(f"/api/evidence/{eid}/verify", headers=H(AUDIT)).json()["result"] == "FAILED"
    incidents = _issues(tenant_id=TA, issue_type="INTEGRITY_INCIDENT", source_ref_id=eid)
    assert incidents and all(len(_creation_record(i.issue_id)["events"]) == 1 for i in incidents)


def test_a_ledger_integrity_incident_is_created_with_its_own_reason(client, world):
    """The ledger-verification caller of the same producer (evidence.verify_ledger), driven directly: tampering an
    append-only ledger row is not possible on the test database by design."""
    from app.db import AdminSession
    from app.engines import evidence
    from app.platform import next_record_seq
    title = f"Tenant ledger verification failed at seq {secrets.randbelow(10 ** 6)}"
    with AdminSession() as db:
        start = next_record_seq(db)
        evidence._integrity_incident(db, TA, title, TA)
        db.commit()
    [issue] = _issues(tenant_id=TA, issue_type="INTEGRITY_INCIDENT", title=title)
    ev = _assert_created_once(issue, title, after_seq=start)
    from app.models import OutboxEvent
    with AdminSession() as db:
        failed = db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "evidence.integrity_failed",
                                                      OutboxEvent.aggregate_id == TA,
                                                      OutboxEvent.record_seq > start)).scalars().all()
    assert len(failed) == 1 and failed[0].record_seq < ev.record_seq  # the integrity failure is recorded first


def test_an_issue_never_exists_without_its_creation_record(client, world, monkeypatch):
    """Fault injection (DAT-REQ-014): when the creation record fails after the issue row was flushed, the producer's whole
    transaction is rolled back, through both producers that now write it, and the same request succeeds once the fault
    is gone, with one issue and one creation record."""
    from app.db import AdminSession
    from app.engines import governance
    from app.engines.evidence import OBJECT_DIR
    from app.models import ApplicabilityDetermination, EvidenceArtifact

    def fault(*a, **k):
        raise RuntimeError("injected creation-record fault")

    eid = _evidence(client)
    with AdminSession() as db:
        (OBJECT_DIR / f"{db.get(EvidenceArtifact, eid).object_key}.bin").unlink()
    monkeypatch.setattr(governance, "record_issue_created", fault)
    with pytest.raises(RuntimeError, match="injected creation-record fault"):
        client.post(f"/api/evidence/{eid}/verify", headers=H(AUDIT))
    monkeypatch.undo()
    assert _issues(tenant_id=TA, issue_type="INTEGRITY_INCIDENT", source_ref_id=eid) == []
    assert client.post(f"/api/evidence/{eid}/verify", headers=H(AUDIT)).json()["result"] == "FAILED"
    [incident] = _issues(tenant_id=TA, issue_type="INTEGRITY_INCIDENT", source_ref_id=eid)
    _assert_created_once(incident, incident.title)

    sid = _subject(client, "fault")
    _fact(client, sid, "entity.established_in_eu", True)
    _fact(client, sid, "entity.financial_entity_type", "CREDIT_INSTITUTION")
    lineage = client.post("/api/applicability/evaluations", headers=H(CCO), json={
        "subject_id": sid, "obligation_definition_id": S["odef"]}).json()["current_determination"]["lineage_id"]
    assert client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(CCO),
                       json={"decision": "ADOPT", "authority_basis": "Board delegation ICE"}).status_code == 200
    monkeypatch.setattr(governance, "record_issue_created", fault)
    with pytest.raises(RuntimeError, match="injected creation-record fault"):
        _fact(client, sid, "entity.financial_entity_type", "PAYMENT_INSTITUTION", change=True)
    monkeypatch.undo()
    assert _issues(tenant_id=TA, issue_type="DETERMINATION_REVIEW", dedupe_like=f"detreview:{lineage}:%") == []
    with AdminSession() as db:  # the flag itself was rolled back with the issue
        [cur] = db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.lineage_id == lineage, ApplicabilityDetermination.record_to_seq.is_(None))).scalars()
        assert not cur.needs_review
    _fact(client, sid, "entity.financial_entity_type", "PAYMENT_INSTITUTION", change=True)
    [review] = _issues(tenant_id=TA, issue_type="DETERMINATION_REVIEW", dedupe_like=f"detreview:{lineage}:%")
    assert len(_creation_record(review.issue_id)["events"]) == 1


# ------------------------------------------------------------------------------------------------ snapshot-raised issues
def _snapshot_issue(client, result, evidence=False):
    """A control with one required manual test and an obligation on its own subject, one test run, one persisted
    snapshot over October: (snapshot, the obligation-level issue it raised)."""
    from app.db import AdminSession
    from app.models import ControlTestPolicy, ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion
    from app.models import Subject, TestDefinition, new_id
    from app.platform import digest, next_record_seq
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    ci = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": "C_ICT_INCIDENT_PROCESS", "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]})
    assert ci.status_code == 200, ci.text
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == ci.json()["control_implementation_id"])).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        lineage = new_id()
        ast = {"node_type": "CONTROL", "node_id": "leaf", "control_key": "C_ICT_INCIDENT_PROCESS"}
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=TA, entity_id=ENT, subject_id=sid,
                                  obligation_definition_id=odef.obligation_definition_id, determination_revision_id="probe",
                                  attestation_id="probe", lifecycle="ADOPTED", valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                  record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=TA, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe",
                                             valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc), record_seq=next_record_seq(db)))
        db.commit()
        td_id = td.test_definition_id
    r = client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td_id, "occurred_at": OCT.isoformat(), "window_from": OCT.isoformat(),
        "window_to": NOV.isoformat(), "result": result, "source_system": "probe",
        "evidence_ids": [_evidence(client)] if evidence else []})
    assert r.status_code == 200, r.text
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO),
                       json={"period_from": OCT.isoformat(), "period_to": NOV.isoformat()})
    assert snap.status_code == 200, snap.text
    issues = _issues(tenant_id=TA, dedupe_like=f"%:{lineage}")
    return snap.json(), issues


@pytest.mark.parametrize("result, state, issue_type, reason", [
    ("FAIL", "FAILING", "OBLIGATION_FAILING", "Obligation FAILING: FAILING"),   # was "Proof gap: FAILING"
    ("PASS", "NOT_PROVEN", "PROOF_GAP", "Proof gap: NOT_PROVEN"),               # unchanged: a proof gap keeps its context
])
def test_a_snapshot_raised_issue_is_created_with_its_own_reason(client, world, result, state, issue_type, reason):
    snap, issues = _snapshot_issue(client, result)  # a PASS without evidence proves nothing: NOT_PROVEN
    assert snap["state"] == state
    [issue] = [i for i in issues if i.issue_type == issue_type]
    _assert_created_once(issue, reason)
    assert issue.source_ref_id == snap["snapshot_id"]
    if issue_type != "PROOF_GAP":
        assert "Proof gap" not in _creation_record(issue.issue_id)["events"][0].reason


@pytest.mark.parametrize("state", ["FAILING", "NOT_PROVEN", "REQUIRES_REVIEW"])
def test_every_snapshot_issue_rule_names_its_own_reason(client, world, state):
    """assurance._issue_hooks for each OBLIGATION_ISSUE_RULES entry (REQUIRES_REVIEW included, which needs conflicting
    evidence to reach through the API): the reason is the rule's own name with the state; only PROOF_GAP is a proof gap.
    A second identical snapshot links the open issue and writes no second creation record."""
    from app.db import AdminSession
    from app.engines import assurance
    from app.models import new_id
    title, itype, _sev, _pri = assurance.OBLIGATION_ISSUE_RULES[state]
    snap = SimpleNamespace(state=state, obligation_lineage_id=new_id(), snapshot_id=new_id(), reason_codes=["PROBE"])
    odef = SimpleNamespace(title="Probe obligation")
    for _ in range(2):
        with AdminSession() as db:
            assurance._issue_hooks(db, TA, snap, odef)
            db.commit()
    [issue] = _issues(tenant_id=TA, dedupe_key=f"{itype}:{snap.obligation_lineage_id}")
    _assert_created_once(issue, f"{title}: {state}")
    assert ("Proof gap" in issue.title) == (itype == "PROOF_GAP")
    assert ("Proof gap" in _creation_record(issue.issue_id)["events"][0].reason) == (itype == "PROOF_GAP")


def test_control_level_issue_creation_is_unchanged(client, world):
    """raise_control_issue already recorded its CREATED event with a reason naming its source evaluation."""
    from app.db import AdminSession
    from app.models import ControlTestPolicy, Subject, TestDefinition, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=TA, entity_id=ENT, subject_type="AI_ASSET", display_name=sid[:8]))
        db.commit()
    ci = client.post("/api/controls", headers=H(OWNER), json={
        "control_key": "C_HUMAN_OVERSIGHT", "subject_id": sid, "method": "probe", "valid_from": "2026-01-01T00:00:00Z",
        "automation_requirement": "NONE", "tests": [{"name": "probe", "source_class": "MANUAL"}]}).json()
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(
            ControlTestPolicy.control_implementation_id == ci["control_implementation_id"])).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
        td_id = td.test_definition_id
    assert client.post("/api/test-runs", headers=H(OWNER), json={
        "test_definition_id": td_id, "occurred_at": OCT.isoformat(), "window_from": OCT.isoformat(),
        "window_to": NOV.isoformat(), "result": "FAIL", "source_system": "probe"}).status_code == 200
    ev = client.post(f"/api/controls/{ci['control_implementation_id']}/evaluate", headers=H(OWNER),
                     json={"window_from": OCT.isoformat(), "window_to": NOV.isoformat()}).json()
    [issue] = _issues(tenant_id=TA, issue_type="CONTROL_FAILING", source_ref_id=ev["evaluation_id"])
    reason = (f"ControlEvaluation {ev['evaluation_id']} FAILING for control implementation "
              f"{ci['control_implementation_id']}; reason codes ['TEST_FAIL']")
    _assert_created_once(issue, reason)
