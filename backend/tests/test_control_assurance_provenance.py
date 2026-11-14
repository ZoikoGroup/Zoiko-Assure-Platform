"""Historical control evaluation and assurance provenance integrity: Obligation -> SatisfactionExpression -> control
mapping -> ControlEvaluation -> TestRun -> Evidence -> Issue -> remediation -> verification -> closure.

Requirements exercised:
  * ZA-STA-001 STA-REQ-003 (control state from exact admissible TestRun/Evidence revisions), STA-REQ-019 (new knowledge
    creates new evaluations; an earlier cutoff never changes), STA-REQ-020/021 (reproducible trace; snapshots pin exact
    expression/control/test/evidence revisions), STA-REQ-023 (closure needs a new test/evidence cycle and result),
    §05 (evidence conditions), §17 (create/link issue unless an open one covers it; closure rule), §19 (immutable
    ControlEvaluation, deduplication by identical input digest);
  * ZA-TIM-001 TIM-REQ-006/015/017 (historical reads at an explicit record cutoff, no latest joins), §09 ("Any
    unresolved temporal ambiguity ... REQUIRES_REVIEW"), §11 ("Correction join: Historical AS_KNOWN_THEN excludes
    revisions first recorded after K");
  * ZA-TEN-001 TEN-REQ-008 / §14 (no cross-entity proof without a governed shared-evidence reference), DOM-INV-11
    (another organisation's record reads as not found, on every backend, not only under row-level security).

Every test builds its own organisation records; seeded data is only read.
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TA, TB = f"tenant-cap-{TAG}", f"tenant-capb-{TAG}"
ENT, SUBJ, ENT2, SUBJ2 = f"entity-cap-{TAG}", f"subj-cap-{TAG}", f"entity-cap2-{TAG}", f"subj-cap2-{TAG}"
ENT_B, SUBJ_B = f"entity-capb-{TAG}", f"subj-capb-{TAG}"
OWNER, CCO, APPROVER = f"cap-owner-{TAG}", f"cap-cco-{TAG}", f"cap-approver-{TAG}"
B_OWNER, B_CCO = f"capb-owner-{TAG}", f"capb-cco-{TAG}"
INC, HUM, MON, DEV, NOTICE = ("C_ICT_INCIDENT_PROCESS", "C_HUMAN_OVERSIGHT", "C_AI_MONITORING_LOGS",
                              "C_DEVICE_ATTESTATION", "C_AI_TRANSPARENCY_NOTICE")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEP, OCT, NOV = (datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc),
                 datetime(2026, 11, 1, tzinfo=timezone.utc))


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
        for aid, tid, roles in ((OWNER, TA, ["CONTROL_OWNER"]), (CCO, TA, ["CCO"]), (APPROVER, TA, ["RISK_APPROVER"]),
                                (B_OWNER, TB, ["CONTROL_OWNER"]), (B_CCO, TB, ["CCO"])):
            add_principal(db, actor_id=aid, tenant_id=tid, actor_type="HUMAN", display_name=aid, roles=roles)
        db.commit()
    return True


# ------------------------------------------------------------------------------------------------ helpers
def _subject(tenant=TA, entity=ENT) -> str:
    """A fresh subject for one test, so its control implementations never collide with another test's."""
    from app.db import AdminSession
    from app.models import Subject, new_id
    sid = new_id()
    with AdminSession() as db:
        db.add(Subject(subject_id=sid, tenant_id=tenant, entity_id=entity, subject_type="AI_ASSET",
                       display_name=f"probe {sid[:8]}"))
        db.commit()
    return sid


def _control(client, key, subject, actor=OWNER, ipe=False, valid_from="2026-01-01T00:00:00Z"):
    test = {"name": f"{key} probe", "source_class": "CONNECTOR" if ipe else "MANUAL", "ipe_required": ipe}
    r = client.post("/api/controls", headers=H(actor), json={
        "control_key": key, "subject_id": subject, "method": "probe", "valid_from": valid_from,
        "automation_requirement": "NONE", "tests": [test]})
    assert r.status_code == 200, r.text
    ci = r.json()["control_implementation_id"]
    from app.db import AdminSession
    from app.models import ControlTestPolicy, TestDefinition
    with AdminSession() as db:
        pol = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.control_implementation_id == ci)).scalar_one()
        td = db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)).scalar_one()
    return ci, td.test_definition_id


def _run(client, td, result, start=SEP, end=OCT, evidence=(), actor=OWNER):
    r = client.post("/api/test-runs", headers=H(actor), json={
        "test_definition_id": td, "occurred_at": start.isoformat(), "window_from": start.isoformat(),
        "window_to": end.isoformat(), "result": result, "source_system": "probe", "evidence_ids": list(evidence)})
    assert r.status_code == 200, r.text
    return r.json()


def _legacy_run(td, evidence=(), source_run_id=None, start=SEP, end=OCT, tenant=TA):
    """A TestRun written below the API, as an import that predates the ingestion scope checks would have."""
    from app.db import AdminSession
    from app.models import TestRun
    from app.platform import next_record_seq
    with AdminSession() as db:
        run = TestRun(tenant_id=tenant, test_definition_id=td, occurred_at=start, window_from=start, window_to=end,
                      result="PASS", source_system="legacy-import", evidence_ids=list(evidence),
                      source_run_id=source_run_id, record_seq=next_record_seq(db))
        db.add(run)
        db.commit()
        return run.test_run_id


def _evidence(client, entity=ENT, actor=OWNER):
    r = client.post("/api/evidence/upload", headers=H(actor),
                    files={"file": ("record.txt", secrets.token_bytes(16), "text/plain")},
                    data={"entity_id": entity, "title": "Test record", "coverage_from": "2026-09-01T00:00:00Z",
                          "coverage_to": "2026-11-01T00:00:00Z", "validity_days": "400"})
    assert r.status_code == 200, r.text
    return r.json()["evidence_id"]


def _seq() -> int:
    from app.db import AdminSession
    from app.platform import current_watermark
    with AdminSession() as db:
        return current_watermark(db)["record_seq"]


def _advance():
    """Move the record watermark on (an unrelated committed write), so the previous watermark is a historical cutoff."""
    from app.db import AdminSession
    from app.platform import next_record_seq
    with AdminSession() as db:
        next_record_seq(db)
        db.commit()


def _eval(ci, cutoff, a=SEP, b=OCT, persist=False):
    """The engine's control evaluation at an explicit record cutoff (what a historical snapshot leaf runs)."""
    from app.db import AdminSession
    from app.engines import assurance as asr
    from app.models import ControlImplementation
    with AdminSession() as db:
        out = asr.evaluate_control(db, db.get(ControlImplementation, ci), a, b, cutoff, persist=persist)
        db.commit() if persist else db.rollback()
    return out


def _stored(evaluation_id):
    from app.db import AdminSession
    from app.models import ControlEvaluation
    with AdminSession() as db:
        return db.get(ControlEvaluation, evaluation_id)


def _obligation(subject, key, tenant=TA, entity=ENT):
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance, SatisfactionExpressionVersion, new_id
    from app.platform import digest, next_record_seq
    lineage = new_id()
    ast = {"node_type": "ALL", "node_id": "root", "rationale": "probe", "children": [
        {"node_type": "CONTROL", "node_id": "leaf", "control_key": key, "rationale": "probe"}]}
    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
        db.add(ObligationInstance(lineage_id=lineage, tenant_id=tenant, entity_id=entity, subject_id=subject,
                                  obligation_definition_id=odef.obligation_definition_id,
                                  determination_revision_id="probe", attestation_id="probe", lifecycle="ADOPTED",
                                  valid_from=T0, record_from_seq=next_record_seq(db)))
        db.add(SatisfactionExpressionVersion(tenant_id=tenant, obligation_lineage_id=lineage, version=1, ast=ast,
                                             ast_hash=digest(ast), rationale="probe", valid_from=T0,
                                             record_seq=next_record_seq(db)))
        db.commit()
    return lineage


def _period(start=SEP, end=OCT, cutoff=None) -> dict:
    body = {"period_from": start.isoformat(), "period_to": end.isoformat()}
    if cutoff is not None:
        body["record_cutoff_seq"] = cutoff
    return body


def _issue_of(client, lineage, actor=CCO):
    [issue] = [i for i in client.get("/api/issues", headers=H(actor)).json() if lineage in i["dedupe_key"]]
    return issue


def _control_issues(ci, tenant=TA):
    from app.db import AdminSession
    from app.models import Issue
    with AdminSession() as db:
        return db.execute(select(Issue).where(Issue.tenant_id == tenant, Issue.dedupe_key.like(f"%:{ci}:%"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()


def _work(client, iid):
    for path, body in ((f"/api/issues/{iid}/assign", {"owner_actor_id": OWNER, "reason": "Control owner"}),
                       (f"/api/issues/{iid}/transition", {"state": "TRIAGED"}),
                       (f"/api/issues/{iid}/transition", {"state": "IN_REMEDIATION", "reason": "Fixing"}),
                       (f"/api/issues/{iid}/transition", {"state": "PENDING_VERIFICATION", "reason": "Fix deployed"})):
        r = client.post(path, headers=H(OWNER), json=body)
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ complete chain
def test_a_complete_assurance_chain_is_bound_end_to_end(client, org):
    s = _subject()
    lineage = _obligation(s, INC)
    ci, td = _control(client, INC, s)
    e = _evidence(client)
    run = _run(client, td, "PASS", evidence=[e])
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=_period())
    assert snap.status_code == 200, snap.text
    snap = snap.json()
    assert snap["state"] == "PROVEN"
    [segment] = snap["segments"]
    [leaf] = segment["leaf_evaluations"]
    # Obligation -> expression version -> exact control implementation -> stored evaluation -> run -> evidence.
    assert leaf["control_implementation_id"] == ci and leaf["control_state"] == "OPERATING"
    stored = _stored(leaf["control_evaluation_id"])
    assert (stored.tenant_id, stored.control_implementation_id, stored.state) == (TA, ci, "OPERATING")
    assert stored.record_cutoff_seq == snap["record_cutoff_seq"]
    assert stored.inputs["deps"]["runs"] == [run["test_run_id"]] and stored.inputs["deps"]["evidence"] == [e]
    manifest = snap["dependency_manifest"]
    assert segment["expression_version_id"] in manifest["expression_versions"]
    assert run["test_run_id"] in manifest["test_runs"] and e in manifest["evidence"] and ci in manifest["control_implementations"]
    dossier = client.get(f"/api/obligations/{lineage}", headers=H(CCO)).json()
    [pinned] = [x for x in dossier["snapshots"] if x["snapshot_id"] == snap["snapshot_id"]]
    assert pinned["expression_pins"]["status"] == "INTACT"
    rep = client.get(f"/api/snapshots/{snap['snapshot_id']}/reproduce", headers=H(CCO)).json()
    assert rep["status"] == "REPRODUCED"


# ------------------------------------------------------------------------------------------------ historical evidence
def test_a_later_evidence_invalidation_is_not_known_at_an_earlier_cutoff(client, org):
    ci, td = _control(client, INC, _subject())
    e = _evidence(client)
    _run(client, td, "PASS", evidence=[e])
    k = _seq()
    first = _eval(ci, k, persist=True)
    assert first["state"] == "OPERATING"
    r = client.post(f"/api/evidence/{e}/invalidate", headers=H(OWNER), json={"reason": "Wrong population"})
    assert r.status_code == 200, r.text
    then = _eval(ci, k)
    assert (then["state"], then["reason_codes"]) == ("OPERATING", []), then
    assert then["tests"][0]["evidence"][0]["condition"] == "CURRENT"
    # The stored evaluation for that cutoff is reused only because it records this same result.
    again = _eval(ci, k, persist=True)
    assert again["evaluation_id"] == first["evaluation_id"] and _stored(again["evaluation_id"]).state == again["state"]
    now = _eval(ci, _seq())
    assert now["state"] == "NOT_EVALUATED" and "INVALID_EVIDENCE" in now["reason_codes"]


def test_evidence_conflict_state_is_read_as_recorded_at_the_cutoff(client, org):
    ci, td = _control(client, HUM, _subject())
    e, other = _evidence(client), _evidence(client)
    _run(client, td, "PASS", evidence=[e])
    before_conflict = _seq()
    c = client.post("/api/evidence-conflicts", headers=H(OWNER),
                    json={"member_evidence_ids": [e, other], "description": "Two records disagree"})
    assert c.status_code == 200, c.text
    assert _eval(ci, before_conflict)["state"] == "OPERATING"  # opened later: not known then
    while_open = _seq()
    r = client.post(f"/api/evidence-conflicts/{c.json()['conflict_id']}/resolve", headers=H(CCO),
                    json={"reason": "Second record was a draft"})
    assert r.status_code == 200, r.text
    then = _eval(ci, while_open)  # resolved later: still open as known then
    assert then["state"] == "REQUIRES_REVIEW" and "SOURCE_CONFLICT" in then["reason_codes"], then
    assert _eval(ci, _seq())["state"] == "OPERATING"


def test_evidence_history_that_cannot_be_placed_is_reported_never_invented(client, org):
    from app.db import AdminSession
    from app.engines import evidence as evd
    from app.models import EvidenceConflict
    ci, td = _control(client, MON, _subject())
    e = _evidence(client)
    _run(client, td, "PASS", evidence=[e])
    k = _seq()
    with AdminSession() as db:  # a lifecycle ledger event without the outbox event its route writes (not placeable)
        evd.append_ledger_event(db, tenant_id=TA, evidence_id=e, event_type="INVALIDATED", body={}, actor_id=OWNER,
                                reason="imported without record time")
        db.commit()
    _advance()
    hist = _eval(ci, k)
    assert hist["state"] == "REQUIRES_REVIEW" and "EVIDENCE_HISTORY_UNRESOLVED" in hist["reason_codes"], hist
    [row] = hist["tests"][0]["evidence"]
    assert row["condition"] is None and row["history_problem"] == "EVIDENCE_HISTORY_UNRESOLVED"
    now = _eval(ci, _seq())  # every recorded event is known at the current cutoff
    assert now["state"] == "NOT_EVALUATED" and "INVALID_EVIDENCE" in now["reason_codes"]
    # A conflict row with no recorded history: its state at an earlier cutoff is unknown, not assumed.
    ci2, td2 = _control(client, DEV, _subject())
    e2, e3 = _evidence(client), _evidence(client)
    _run(client, td2, "PASS", evidence=[e2])
    k2 = _seq()
    with AdminSession() as db:
        db.add(EvidenceConflict(tenant_id=TA, member_evidence_ids=[e2, e3], description="legacy", status="RESOLVED"))
        db.commit()
    _advance()
    hist2 = _eval(ci2, k2)
    assert hist2["state"] == "REQUIRES_REVIEW" and hist2["reason_codes"] == ["EVIDENCE_HISTORY_UNRESOLVED"], hist2
    assert _eval(ci2, _seq())["state"] == "OPERATING"


def test_a_stored_evaluation_lends_its_id_only_to_the_result_it_records(client, org):
    from app import codes
    from app.db import AdminSession
    from app.models import ControlEvaluation
    ci, td = _control(client, NOTICE, _subject())
    _run(client, td, "PASS", evidence=[_evidence(client)])
    k = _seq()
    preview = _eval(ci, k)
    assert preview["state"] == "OPERATING"
    with AdminSession() as db:  # an earlier stored evaluation with the same input digest but another result
        stale = ControlEvaluation(tenant_id=TA, control_implementation_id=ci, policy_version_id=preview["policy_version_id"],
                                  window_from=SEP, window_to=OCT, record_cutoff_seq=k, state="NOT_EVALUATED",
                                  reason_codes=["INVALID_EVIDENCE"], inputs={}, precedence_rule="3:NOT_EVALUATED",
                                  evaluator_version=codes.CONTROL_EVALUATOR_VERSION,
                                  dependency_digest=preview["dependency_digest"],
                                  created_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        db.add(stale)
        db.commit()
        stale_id = stale.evaluation_id
    out = _eval(ci, k, persist=True)
    assert out["state"] == "OPERATING" and out["evaluation_id"] != stale_id
    assert _stored(out["evaluation_id"]).state == "OPERATING" and _stored(stale_id).state == "NOT_EVALUATED"
    assert _eval(ci, k, persist=True)["evaluation_id"] == out["evaluation_id"]


# ------------------------------------------------------------------------------------------------ scope of proof
def test_evidence_of_another_entity_does_not_prove_a_control(client, org):
    ci, td = _control(client, INC, _subject())
    _legacy_run(td, evidence=[_evidence(client, entity=ENT2)])
    out = _eval(ci, _seq())
    assert out["state"] == "NOT_EVALUATED" and out["reason_codes"] == ["MISSING_EVIDENCE"], out
    assert out["tests"][0]["evidence"] == []


def _ipe_source_run(tenant, entity, result="IPE_VERIFIED"):
    """A source run of ``entity`` with a bound IPE evaluation of ``result`` (copies a seeded run's row)."""
    from app.db import AdminSession
    from app.models import IPEEvaluation, SourceRun, new_id
    from app.platform import next_record_seq
    with AdminSession() as db:
        seed = db.execute(select(SourceRun).order_by(SourceRun.record_seq, SourceRun.source_run_id)).scalars().first()
        row = {c.key: getattr(seed, c.key) for c in SourceRun.__mapper__.column_attrs}
        row.update(source_run_id=new_id(), tenant_id=tenant, entity_id=entity, evidence_ids=[],
                   record_seq=next_record_seq(db))
        db.add(SourceRun(**row))
        db.add(IPEEvaluation(tenant_id=tenant, source_run_id=row["source_run_id"],
                             ipe_policy_version_id=row["ipe_policy_version_id"],
                             mapping_contract_version_id=row["mapping_contract_version_id"], completeness_result="PASS",
                             accuracy_result="PASS", overall_result=result, checks=[], reason_codes=[],
                             engine_version="probe"))
        db.commit()
        return row["source_run_id"]


def test_a_source_run_of_another_organisation_or_entity_is_not_population_proof(client, org):
    ci, td = _control(client, HUM, _subject(), ipe=True)
    _legacy_run(td, evidence=[_evidence(client)], source_run_id=_ipe_source_run(TA, ENT))
    assert _eval(ci, _seq())["state"] == "OPERATING"  # the setup proves a verified run of this entity supports it
    for tenant, entity in ((TB, ENT_B), (TA, ENT2)):
        ci2, td2 = _control(client, HUM, _subject(), ipe=True)
        _legacy_run(td2, evidence=[_evidence(client)], source_run_id=_ipe_source_run(tenant, entity))
        out = _eval(ci2, _seq())
        assert out["state"] == "NOT_EVALUATED" and "IPE_INCONCLUSIVE" in out["reason_codes"], (tenant, out)
        assert out["tests"][0]["evidence"][0]["ipe_result"] is None


def test_test_runs_are_refused_for_another_organisations_test_or_evidence(client, org):
    b_ci, b_td = _control(client, MON, _subject(TB, ENT_B), actor=B_OWNER)
    ci, td = _control(client, MON, _subject())
    for body, status, error in (
            ({"test_definition_id": b_td, "evidence_ids": []}, 404, "TEST_DEFINITION_NOT_FOUND"),
            ({"test_definition_id": td, "evidence_ids": [_evidence(client, ENT_B, B_OWNER)]}, 404, "EVIDENCE_NOT_FOUND"),
            ({"test_definition_id": td, "evidence_ids": [_evidence(client, ENT2)]}, 422, "EVIDENCE_SCOPE_MISMATCH")):
        r = client.post("/api/test-runs", headers=H(OWNER), json={
            "occurred_at": SEP.isoformat(), "window_from": SEP.isoformat(), "window_to": OCT.isoformat(),
            "result": "PASS", "source_system": "probe", **body})
        assert (r.status_code, r.json()["detail"]["error"]) == (status, error), r.text


# ------------------------------------------------------------------------------------------------ expression versions
def test_an_earlier_cutoff_keeps_its_expression_version_and_segments(client, org):
    s = _subject()
    lineage = _obligation(s, NOTICE)
    ci, td = _control(client, NOTICE, s)
    _run(client, td, "PASS", evidence=[_evidence(client)])
    k = _seq()
    before = client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=_period(cutoff=k)).json()
    # Recorded after k: a new expression version, and another implementation of the same control starting mid-period.
    v2 = {"node_type": "ALL", "node_id": "root2", "rationale": "probe", "children": [
        {"node_type": "CONTROL", "node_id": "leaf2", "control_key": NOTICE, "control_implementation_id": ci,
         "rationale": "exact binding"}]}
    r = client.post(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO),
                    json={"ast": v2, "valid_from": T0.isoformat(), "rationale": "Bind the exact implementation"})
    assert r.status_code == 201, r.text
    _control(client, NOTICE, _subject(), valid_from="2026-09-15T00:00:00Z")
    after = client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=_period(cutoff=k)).json()
    assert after["segments"][0]["expression_version_id"] == before["segments"][0]["expression_version_id"]
    assert after["segment_count"] == before["segment_count"] and after["segments"] == before["segments"]
    assert after["dependency_digest"] == before["dependency_digest"]
    current = client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=_period()).json()
    assert current["segments"][0]["expression_version_id"] == r.json()["expression_version_id"]


def test_mappings_to_another_scope_or_control_are_refused(client, org):
    s = _subject()
    lineage = _obligation(s, DEV)
    b_ci, _ = _control(client, DEV, _subject(TB, ENT_B), actor=B_OWNER)
    other_key_ci, _ = _control(client, INC, s)
    other_entity_ci, _ = _control(client, DEV, SUBJ2)
    for impl in (b_ci, other_key_ci, other_entity_ci):
        ast = {"node_type": "ALL", "node_id": "root", "rationale": "probe", "children": [
            {"node_type": "CONTROL", "node_id": "leaf", "control_key": DEV, "control_implementation_id": impl,
             "rationale": "probe"}]}
        r = client.post(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO),
                        json={"ast": ast, "valid_from": T0.isoformat(), "rationale": "probe"})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "CONTROL_MAPPING_INVALID", r.text


def test_a_tampered_expression_cannot_be_released_again_through_a_compensation(client, org):
    from app.db import AdminSession
    from app.models import Compensation, SatisfactionExpressionVersion
    s = _subject()
    lineage = _obligation(s, MON)
    c = client.post("/api/compensations", headers=H(OWNER), json={
        "obligation_lineage_id": lineage, "primary_control_key": MON, "compensating_control_keys": [DEV],
        "equivalence_rationale": "Device attestation evidences the same monitoring outcome", "valid_from": T0.isoformat()})
    assert c.status_code == 200, c.text
    with AdminSession() as db:  # the released version altered in place after release
        v1 = db.execute(select(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage)).scalar_one()
        v1.ast = {**v1.ast, "children": [*v1.ast["children"], {"node_type": "CONTROL", "node_id": "injected",
                                                                "control_key": HUM, "rationale": "x"}]}
        flag_modified(v1, "ast")
        db.commit()
    r = client.post(f"/api/compensations/{c.json()['compensation_id']}/decide", headers=H(APPROVER),
                    json={"decision": "APPROVE"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "SATISFACTION_EXPRESSION_HASH_MISMATCH", r.text
    with AdminSession() as db:
        assert db.execute(select(func.count()).select_from(SatisfactionExpressionVersion).where(
            SatisfactionExpressionVersion.obligation_lineage_id == lineage)).scalar() == 1
        assert db.get(Compensation, c.json()["compensation_id"]).status == "PENDING_APPROVAL"
    p = client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=_period())
    assert p.status_code == 409 and p.json()["detail"]["code"] == "SATISFACTION_EXPRESSION_HASH_MISMATCH"
    versions = client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO)).json()
    assert versions[0]["integrity"]["ast_hash_matches"] is False


# ------------------------------------------------------------------------------------------------ issues in history
def test_a_historical_dossier_shows_issue_state_as_recorded_at_the_cutoff(client, org):
    s = _subject()
    lineage = _obligation(s, DEV)
    ci, td = _control(client, DEV, s)
    _run(client, td, "FAIL")
    before_issue = _seq()
    failing = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=_period()).json()
    assert failing["state"] == "FAILING"
    iid = _issue_of(client, lineage)["issue_id"]
    opened = _seq()
    _work(client, iid)
    with_owner_pending = _seq()
    _run(client, td, "PASS", start=OCT, end=NOV, evidence=[_evidence(client)])
    proven = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=_period(OCT, NOV)).json()
    assert proven["state"] == "PROVEN"
    r = client.post(f"/api/issues/{iid}/close", headers=H(OWNER),
                    json={"verification_snapshot_id": proven["snapshot_id"], "reason": "Retest passed"})
    assert r.status_code == 200, r.text

    def issues_at(seq):
        d = client.get(f"/api/obligations/{lineage}", headers=H(CCO), params={"as_of_record_seq": seq})
        assert d.status_code == 200, d.text
        return {i["issue_id"]: i for i in d.json()["issues"]}, d.json()["historical_limitations"]
    at_open, limits = issues_at(opened)
    assert (at_open[iid]["state"], at_open[iid]["owner_actor_id"]) == ("OPEN", None), at_open
    assert at_open[iid]["historical_state"]["source"] == "EVENT_HISTORY"
    assert not any("shown as they are now" in x for x in limits)
    at_pending, _ = issues_at(with_owner_pending)
    assert (at_pending[iid]["state"], at_pending[iid]["owner_actor_id"]) == ("PENDING_VERIFICATION", OWNER)
    assert iid not in issues_at(before_issue)[0]  # not yet recorded
    current = client.get(f"/api/obligations/{lineage}", headers=H(CCO)).json()
    assert [i["state"] for i in current["issues"] if i["issue_id"] == iid] == ["CLOSED"]
    events = client.get(f"/api/issues/{iid}/events", headers=H(CCO), params={"as_of_record_seq": opened}).json()
    assert [e["event_type"] for e in events] == ["CREATED"]
    assert [e["event_type"] for e in client.get(f"/api/issues/{iid}/events", headers=H(CCO)).json()][-1] == "CLOSED"
    future = client.get(f"/api/issues/{iid}/events", headers=H(CCO), params={"as_of_record_seq": _seq() + 1000})
    assert future.status_code == 422 and future.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"


def test_issue_history_gaps_are_reported_as_limitations(client, org):
    from app.db import AdminSession
    from app.engines.governance import MIGRATION_BASELINE_EVENT
    from app.models import Issue, IssueEvent
    from app.platform import next_record_seq
    lineage = _obligation(_subject(), HUM)
    with AdminSession() as db:
        def issue(kind, state="OPEN"):
            i = Issue(tenant_id=TA, title=kind, issue_type=kind, state=state, source_ref_type="AssuranceSnapshot",
                      source_ref_id="legacy", dedupe_key=f"{kind}:{lineage}")
            db.add(i)
            db.flush()
            return i
        issue("PROOF_GAP")                                     # no history at all
        baseline = issue("ASSURANCE_REVIEW")                   # migration baseline only (record_seq 0)
        db.add(IssueEvent(issue_id=baseline.issue_id, tenant_id=TA, event_type=MIGRATION_BASELINE_EVENT,
                          from_state=None, to_state="OPEN", actor_id="system:migration:0008", record_seq=0))
        broken = issue("OBLIGATION_FAILING", state="CLOSED")   # projection disagrees with its full history
        db.add(IssueEvent(issue_id=broken.issue_id, tenant_id=TA, event_type="CREATED", from_state=None,
                          to_state="OPEN", actor_id="system", record_seq=next_record_seq(db)))
        db.commit()
        broken_id = broken.issue_id
    d = client.get(f"/api/obligations/{lineage}", headers=H(CCO), params={"as_of_record_seq": _seq()}).json()
    [listed] = d["issues"]
    assert listed["issue_id"] == broken_id and listed["state"] is None
    assert listed["historical_state"]["problem"] == "ISSUE_HISTORY_INCONSISTENT"
    assert any(x.startswith("2 issue(s) have no recorded history") for x in d["historical_limitations"])


# ------------------------------------------------------------------------------------------------ closure
def test_closure_needs_a_new_operating_evaluation_after_remediation(client, org):
    ci, td = _control(client, MON, _subject())
    _run(client, td, "FAIL")
    failing = _eval(ci, _seq(), persist=True)
    [issue] = _control_issues(ci)
    assert issue.source_ref_id == failing["evaluation_id"]
    _work(client, issue.issue_id)

    def close(evaluation_id):
        return client.post(f"/api/issues/{issue.issue_id}/close", headers=H(OWNER), json={
            "verification_control_evaluation_id": evaluation_id, "reason": "Retest"})
    _run(client, td, "PASS", start=OCT, end=NOV, evidence=[_evidence(client)])
    before_remediation = _stored(failing["evaluation_id"]).record_cutoff_seq
    r = close(_eval(ci, before_remediation, OCT, NOV, persist=True)["evaluation_id"])
    assert r.status_code == 409 and "VERIFICATION_PREDATES_REMEDIATION" in [p["code"] for p in r.json()["detail"]["problems"]]
    ok = _eval(ci, _seq(), OCT, NOV, persist=True)
    assert ok["state"] == "OPERATING"
    r = close(ok["evaluation_id"])
    assert r.status_code == 200 and r.json()["state"] == "CLOSED", r.text


# ------------------------------------------------------------------------------------------------ isolation
def test_control_reads_never_list_another_organisations_rows(client, org):
    from app import codes
    from app.db import AdminSession
    from app.models import ControlEvaluation, TestRun
    from app.platform import next_record_seq
    ci, td = _control(client, NOTICE, _subject())
    with AdminSession() as db:  # rows of another organisation that name this control and test definition
        db.add(ControlEvaluation(tenant_id=TB, control_implementation_id=ci, policy_version_id="foreign",
                                 window_from=SEP, window_to=OCT, record_cutoff_seq=1, state="OPERATING", reason_codes=[],
                                 inputs={}, precedence_rule="5:OPERATING", evaluator_version=codes.CONTROL_EVALUATOR_VERSION,
                                 dependency_digest="f" * 64))
        db.add(TestRun(tenant_id=TB, test_definition_id=td, occurred_at=SEP, window_from=SEP, window_to=OCT,
                       result="PASS", source_system="foreign", evidence_ids=[], record_seq=next_record_seq(db)))
        db.commit()
    d = client.get(f"/api/controls/{ci}", headers=H(OWNER))
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["evaluations"] == [] and [t["runs"] for t in d["tests"]] == [[]]
    assert d["evaluation_preview"]["state"] == "NOT_EVALUATED"
    lineage = _obligation(_subject(), INC)
    for path in (f"/api/controls/{ci}", f"/api/obligations/{lineage}", f"/api/obligations/{lineage}?as_of_record_seq=1",
                 f"/api/obligations/{lineage}/expression-versions"):
        assert client.get(path, headers=H(B_CCO)).status_code in (403, 404), path
    ci2, td2 = _control(client, INC, _subject())
    _run(client, td2, "FAIL")
    _eval(ci2, _seq(), persist=True)
    [issue] = _control_issues(ci2)
    for actor in (B_CCO, B_OWNER):
        assert client.get(f"/api/issues/{issue.issue_id}/events", headers=H(actor)).status_code in (403, 404)
        assert client.get(f"/api/issues/{issue.issue_id}/events?as_of_record_seq=1", headers=H(actor)).status_code in (403, 404)


def _counts():
    from app.db import AdminSession
    from app.models import (AssuranceSnapshot, AuditEvent, ControlEvaluation, Issue, IssueEvent, OutboxEvent,
                            SatisfactionExpressionVersion)
    from app.platform import current_watermark
    with AdminSession() as db:
        return (current_watermark(db)["record_seq"],
                *(db.execute(select(func.count()).select_from(m)).scalar()
                  for m in (AssuranceSnapshot, AuditEvent, ControlEvaluation, Issue, IssueEvent, OutboxEvent,
                            SatisfactionExpressionVersion)))


def test_historical_reads_write_nothing(client, org):
    s = _subject()
    lineage = _obligation(s, HUM)
    ci, td = _control(client, HUM, s)
    _run(client, td, "FAIL")
    k = _seq()
    snap = client.post(f"/api/obligations/{lineage}/snapshots", headers=H(CCO), json=_period()).json()
    iid = _issue_of(client, lineage)["issue_id"]
    _advance()
    before = _counts()
    reads = [client.get(f"/api/obligations/{lineage}", headers=H(CCO), params={"as_of_record_seq": k}),
             client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(CCO), params={"as_of_record_seq": k}),
             client.post(f"/api/obligations/{lineage}/preview", headers=H(CCO), json=_period(cutoff=k)),
             client.get(f"/api/snapshots/{snap['snapshot_id']}/reproduce", headers=H(CCO)),
             client.get(f"/api/issues/{iid}/events", headers=H(CCO), params={"as_of_record_seq": k}),
             client.get(f"/api/controls/{ci}", headers=H(OWNER))]
    assert [r.status_code for r in reads] == [200] * len(reads), [r.text for r in reads if r.status_code != 200]
    assert _counts() == before


# ------------------------------------------------------------------------------------------------ concurrency
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES
    if not IS_POSTGRES:
        # Concurrent transactions and locks. The reason must be exactly "PostgreSQL-only" (CI backend-unit gate).
        pytest.skip("PostgreSQL-only")
    return True


def test_a_failure_evaluated_during_closure_is_never_left_without_an_active_issue(client, org, pg, monkeypatch):
    """A new FAILING evaluation of the same condition, committed while the issue is being closed, must end up covered
    by an active issue: either it waits for the closure and opens a new issue, or the closure follows it."""
    from app.db import SessionLocal, set_principal
    from app.engines import assurance as asr
    from app.engines import governance
    from app.models import ControlImplementation
    from app.platform import current_watermark
    ci, td = _control(client, DEV, _subject())
    _run(client, td, "FAIL")
    _eval(ci, _seq(), persist=True)
    [issue] = _control_issues(ci)
    _work(client, issue.issue_id)
    _run(client, td, "PASS", start=OCT, end=NOV, evidence=[_evidence(client)])
    verifying = _eval(ci, _seq(), OCT, NOV, persist=True)
    _run(client, td, "FAIL", start=NOV, end=datetime(2026, 12, 1, tzinfo=timezone.utc))
    entered, go, errors = threading.Event(), threading.Event(), []
    real = governance.control_closure_problems

    def paused(db, i, ref):
        entered.set()
        go.wait(20)
        return real(db, i, ref)
    monkeypatch.setattr(governance, "control_closure_problems", paused)

    def closer():
        try:
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                governance.close_issue(db, tenant_id=TA, actor=OWNER, issue_id=issue.issue_id, reason="Retest passed",
                                       verification_control_evaluation_id=verifying["evaluation_id"])
                db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    def evaluator():
        try:
            with SessionLocal() as db:
                set_principal(db, TA, OWNER)
                c = db.get(ControlImplementation, ci)
                out = asr.evaluate_control(db, c, NOV, datetime(2026, 12, 1, tzinfo=timezone.utc),
                                           current_watermark(db)["record_seq"], persist=True)
                assert out["state"] == "FAILING", out
                db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    t1 = threading.Thread(target=closer)
    t1.start()
    assert entered.wait(20)
    t2 = threading.Thread(target=evaluator)
    t2.start()
    t2.join(2)
    go.set()
    t1.join(60)
    t2.join(60)
    assert not errors, errors
    states = sorted(i.state for i in _control_issues(ci))
    assert states == ["CLOSED", "OPEN"], states
