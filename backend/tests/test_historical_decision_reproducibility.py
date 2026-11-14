"""Focused regression tests for historical applicability and obligation decision reproducibility.

Requirements exercised:
  * PRD-001 §3.1, §12: Traceable and verifiable regulatory decisions across catalogue releases.
  * ZA-GLS-001: Historical decisions retain original provenance; corrections create new verifiable states.
  * ZA-RUL-001 §02, §08, §11, §12: Explicit rule and requirement version bindings; SetVersion pins; AST hash.
  * ZA-TIM-001 §12, WF-TIM-03, TIM-REQ-012: Bitemporal visibility (record sequence cutoff); deterministic replay.
  * ZA-WRK-001 WRK-REQ-043: Corrections preserve original release, evaluations, determinations, and adoptions.
  * ZA-DAT-001 DAT-REQ-012: Immutability of historical decision records; fail-closed missing provenance.
  * ZA-STA-001 §07, §16, STA-REQ-021: Expression versioning and control mapping resolution at cutoff.
  * ZA-API-001 §17, APP-REQ-003, APP-REQ-006: Tenant isolation and fail-closed integrity verification.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import H, add_principal
from test_rule_integrity import _tampered

TAG = secrets.token_hex(3).upper()
INSTR = f"HDR-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
ORGS = {k: (f"tenant-hdr{k.lower()}-{TAG.lower()}", f"subj-hdr{k.lower()}-{TAG.lower()}", f"hdr{k.lower()}-cco-{TAG.lower()}")
        for k in ("A", "B")}
S: dict = {}


def _ast(tag: str, entity_type: str) -> dict:
    def pred(nid, fact, typ, value, sm):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": "EQ", "source_mappings": [sm],
                "args": [{"node_type": "FACT_REF", "node_id": f"{nid}f", "fact": fact},
                         {"node_type": "CONST", "node_id": f"{nid}c", "type": typ, "value": value}]}
    return {"node_type": "ALL", "node_id": f"{tag}_root", "source_mappings": [f"sm_{tag}_root"], "children": [
        pred(f"{tag}_1", "entity.established_in_eu", "BOOLEAN", True, f"sm_{tag}_1"),
        pred(f"{tag}_2", "entity.financial_entity_type", "ENUM", entity_type, f"sm_{tag}_2")]}


def _vectors(entity_type: str) -> list[dict]:
    return [
        {"vector_id": "HDR-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
         "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": entity_type},
         "expected_outcome": "APPLIES"},
        {"vector_id": "HDR-NEG", "vector_class": "NEGATIVE", "description": "outside the EU", "effective_at": "2025-06-01",
         "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": entity_type},
         "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "HDR-UNK", "vector_class": "UNKNOWN", "description": "type unknown", "effective_at": "2025-06-01",
         "facts": {"entity.established_in_eu": True,
                   "entity.financial_entity_type": {"unknown": "MISSING_REQUIRED_FACT"}},
         "expected_outcome": "UNCERTAIN"},
    ]


def _draft(client, odef_id: str, tag: str, change_class: str, entity_type: str = "CREDIT_INSTITUTION") -> str:
    rv, src = S["rv"], S["source"]
    p0, p1 = rv["provisions"][0], rv["provisions"][1]
    m = lambda sid, p: {"source_mapping_id": sid, "requirement_version_id": rv["requirement_version_id"],
                        "provision_id": p["provision_id"], "authoritative_reference": p["locator"],
                        "source_fingerprint": src["fingerprint"], "mapping_type": "DIRECT_TEXT", "reviewer": LEGAL}
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id, "authoring_mode": "AST", "ast": _ast(tag, entity_type),
        "golden_vectors": _vectors(entity_type), "valid_from": "2025-01-01", "change_class": change_class,
        "source_mappings": [m(f"sm_{tag}_root", p0), m(f"sm_{tag}_1", p0), m(f"sm_{tag}_2", p1)]})
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    for actor, step in ((AUTHOR, "submit-review"), (PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


def _publish(client, did: str, label: str, actor: str = RELEASE, **extra):
    return client.post(f"/api/rules/drafts/{did}/publish", headers=H(actor),
                       json={"label": label, "changelog": label, **extra})


@pytest.fixture(scope="module")
def world(client):
    """Setup instrument, source, requirements, obligation definition, tenant principals, and release r1."""
    from app.db import CELL_ID, AdminSession
    from app.models import ControlDefinition, Instrument, LegalEntity, ObligationDefinition, Subject, Tenant

    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Historical Decision Instrument", jurisdiction="EU",
                          authority="Regulatory Authority", coverage_mode="ENCODED", regime_owner="Zoiko probe"))
        # Add a control definition for satisfaction template
        cd_key = f"CTRL-{TAG}-REGISTER"
        if db.execute(select(ControlDefinition).where(ControlDefinition.key == cd_key)).scalar() is None:
            db.add(ControlDefinition(key=cd_key, title="Maintain Contract Register",
                                     objective="Maintain an updated third party contract register.",
                                     category="RISK_MANAGEMENT"))
        for tid, sid, cco in ORGS.values():
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{tid} Entity Ltd", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=f"entity-{tid}", subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Entity Ltd"))
            add_principal(db, actor_id=cco, tenant_id=tid, actor_type="HUMAN", display_name=cco,
                          roles=["CCO", "ASSURANCE_REVIEWER"])
        db.commit()

    S["cd_key"] = cd_key

    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Regulatory Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Historical Integrity Regulation", "locator": f"OJ L {TAG}/hdr", "instrument_identifier": INSTR,
        "raw_text": "Credit institutions shall maintain a digital contract register and perform periodic risk assessments."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()

    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"HDR-{TAG}-REQ",
        "title": "Digital contract register requirement", "locator": "Art. 12", "valid_from": "2025-01-01",
        "provisions": [
            {"locator": "Art. 12(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain register",
             "regulated_object": "contract register", "timing": "continuous",
             "normalized_meaning": "Maintain register.", "evidence_expectation": "Register"},
            {"locator": "Art. 12(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "assess risk",
             "regulated_object": "third party risk", "timing": "annually",
             "normalized_meaning": "Assess risk annually.", "evidence_expectation": "Assessment record"}]})
    assert rv.status_code in (200, 201), rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200

    template = {"node_type": "CONTROL", "node_id": f"ctrl_{TAG}", "control_key": cd_key}
    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"HDR_{TAG}_MAIN", title=f"Historical Reproducibility Obligation {TAG}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 satisfaction_template=template, material=True)
        db.add(o)
        db.commit()
        S["odef_id"] = o.obligation_definition_id

    # Release 1: RuleVersion 1 (targets CREDIT_INSTITUTION)
    r1 = _publish(client, _draft(client, S["odef_id"], "r1", "EXECUTABLE_OUTCOME", "CREDIT_INSTITUTION"), f"hdr-{TAG}-r1")
    assert r1.status_code == 200, r1.text
    S["r1"], S["rv1"] = r1.json()["content_release"], r1.json()["rule_version"]

    # Org A ingests facts: Credit Institution, Established in EU
    tid_a, sid_a, cco_a = ORGS["A"]
    at = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
        assert client.post(f"/api/subjects/{sid_a}/facts", headers=H(cco_a),
                           json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200

    # Org A evaluates under r1
    ev1 = client.post("/api/applicability/evaluations", headers=H(cco_a),
                      json={"subject_id": sid_a, "obligation_definition_id": S["odef_id"]}).json()
    assert ev1["outcome"] == "APPLIES"
    assert ev1["rule_version_id"] == S["rv1"]["rule_version_id"]
    S["ev1"] = ev1
    S["cutoff_1"] = ev1["record_cutoff_seq"]

    # Org A CCO attests determination (decision: ADOPT)
    lineage = ev1["current_determination"]["lineage_id"]
    att_res = client.post(f"/api/applicability/determinations/{lineage}/attest", headers=H(cco_a),
                          json={"decision": "ADOPT", "authority_basis": "Board resolution 2025-01"}).json()
    S["det1"] = att_res["determination"]
    S["att1"] = att_res["attestation"]
    S["obl1"] = att_res["obligation"]
    S["obl_lineage"] = S["obl1"]["lineage_id"]

    # Release 2: Correction release (targets PAYMENT_INSTITUTION, correcting credit institutions)
    did_c = _draft(client, S["odef_id"], "c1", "CORRECTION", "PAYMENT_INSTITUTION")
    r2 = _publish(client, did_c, f"hdr-{TAG}-r2",
                   correction_reason="Erroneously applied to credit institutions; amended to payment institutions only.")
    assert r2.status_code == 200, r2.text
    S["r2"], S["rv2"] = r2.json()["content_release"], r2.json()["rule_version"]
    return True


# ------------------------------------------------------------------------------------------------ Tests

def test_reproducing_old_applicability_evaluation_after_new_release(client, world):
    """An old evaluation remains deterministic and re-verifies against the exact historical rule version."""
    ev1_id = S["ev1"]["evaluation_id"]
    cco_a = ORGS["A"][2]

    # 1. Fetch historical evaluation details and verify regulatory provenance
    rep = client.get(f"/api/applicability/evaluations/{ev1_id}", headers=H(cco_a)).json()
    assert rep["outcome"] == "APPLIES"
    assert rep["rule_version"]["version"] == 1
    assert rep["rule_version"]["content_release_id"] == S["r1"]["content_release_id"]
    assert rep["rule_version"]["ast_hash"] == S["rv1"]["ast_hash"]

    # Regulatory provenance links to exact release and source snapshot
    prov = rep["regulatory_provenance"]
    assert prov["binding_basis"] == "RELEASE_BOUND"
    assert prov["rule_version_id"] == S["rv1"]["rule_version_id"]
    assert prov["requirements"][0]["requirement"]["requirement_version_id"] == S["rv"]["requirement_version_id"]
    assert prov["requirements"][0]["source"]["source_id"] == S["source"]["source_id"]
    assert prov["requirements"][0]["fingerprint_matches_binding"] is True

    # 2. Automated replay of frozen bundle re-executes deterministically
    assert rep["replay"]["matches"] is True
    assert rep["replay"]["reproduced_outcome"] == "APPLIES"
    assert rep["replay"]["input_bundle_hash"] == S["ev1"]["input_bundle_hash"]

    # 3. New evaluation request with cutoff_seq = cutoff_1 resolves to rv1, not rv2. With the same exact effective_at
    # too, every APP-001 §06 key component is equal, so it collapses to the historical evaluation.
    replay_ev = client.post("/api/applicability/evaluations", headers=H(cco_a),
                            json={"subject_id": ORGS["A"][1], "obligation_definition_id": S["odef_id"],
                                  "effective_at": S["ev1"]["effective_at"],
                                  "record_cutoff_seq": S["cutoff_1"]}).json()
    assert replay_ev["evaluation_id"] == ev1_id  # Collapses idempotently to historical evaluation
    assert replay_ev["outcome"] == "APPLIES"
    assert replay_ev["rule_version_id"] == S["rv1"]["rule_version_id"]


def test_detecting_substituted_rule_or_requirement_versions(client, world):
    """Replay fails closed when rule version is missing or tampered; release verification catches substitutions."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ApplicabilityEvaluation

    cco_a = ORGS["A"][2]
    ev1_id = S["ev1"]["evaluation_id"]

    # 1. Verification detects tampered RuleVersion AST
    rv1_id = S["rv1"]["rule_version_id"]
    with _tampered("catalog_rule_version", "rule_version_id", rv1_id, ast_hash="0" * 64):
        with AdminSession() as db:
            ev = db.get(ApplicabilityEvaluation, ev1_id)
            # Calling verify_rule_integrity fails closed
            with pytest.raises(appl.RuleIntegrityError) as exc_info:
                appl.evaluate(db, tenant_id=ev.tenant_id, subject=db.get(appl.Subject, ev.subject_id),
                              odef=db.get(appl.ObligationDefinition, ev.obligation_definition_id),
                              effective_at=ev.effective_at, record_cutoff_seq=ev.record_cutoff_seq,
                              trigger="RECONSTRUCTION", actor_id=cco_a, rule_version=db.get(appl.RuleVersion, rv1_id),
                              persist=False)
            assert "AST_HASH_MISMATCH" in str(exc_info.value)

    # 2. When rule version is missing, _replay reports RULE_VERSION_MISSING and does not fallback to rv2
    with AdminSession() as db:
        ev = db.get(ApplicabilityEvaluation, ev1_id)
        # Detached evaluation with invalid rule_version_id
        from app.models import Actor
        from app.routers.applicability import _replay
        from app.security import Ctx
        ctx = Ctx(db=db, actor=db.get(Actor, cco_a))
        fake_ev = ApplicabilityEvaluation(
            tenant_id=ev.tenant_id, entity_id=ev.entity_id, subject_id=ev.subject_id,
            obligation_definition_id=ev.obligation_definition_id, rule_version_id="00000000-missing-rv",
            effective_at=ev.effective_at, record_cutoff_seq=ev.record_cutoff_seq,
            evaluator_build=ev.evaluator_build, input_bundle=ev.input_bundle,
            input_bundle_hash=ev.input_bundle_hash, evaluation_key="fake-key", outcome="APPLIES",
            trace=ev.trace, trigger="RECONSTRUCTION", requested_by=cco_a, record_seq=1)
        res = _replay(ctx, fake_ev)
        assert res["matches"] is False
        assert res["error"] == "RULE_VERSION_MISSING"


def test_detecting_changed_source_fingerprints(client, world):
    """When source snapshot text changes after release, trace_rule_version reports fingerprint mismatch."""
    src_id = S["source"]["source_id"]
    rv1_id = S["rv1"]["rule_version_id"]

    from app.db import AdminSession
    from app.engines import catalog
    from app.models import RuleVersion

    with _tampered("catalog_source_record", "source_id", src_id, fingerprint="f" * 64):
        with AdminSession() as db:
            rv = db.get(RuleVersion, rv1_id)
            trace = catalog.trace_rule_version(db, rv)
            assert trace["binding_basis"] == "RELEASE_BOUND"
            assert trace["requirements"][0]["fingerprint_matches_binding"] is False
            assert trace["requirements"][0]["bound_source_fingerprint"] == S["source"]["fingerprint"]


def test_reproducing_historical_obligation_adoption(client, world):
    """Historical obligation dossier reproduces exact state and determination as known at decision sequence."""
    cco_a = ORGS["A"][2]
    lineage = S["obl_lineage"]
    o1_seq = S["obl1"]["record_from_seq"]

    # 1. Current dossier query (at current watermark) reflects obligation state
    cur_dossier = client.get(f"/api/obligations/{lineage}", headers=H(cco_a)).json()
    assert cur_dossier["obligation"]["lifecycle"] == "ADOPTED"
    assert cur_dossier["obligation"]["determination_revision_id"] == S["det1"]["revision_id"]
    assert cur_dossier["determination"]["revision_id"] == S["det1"]["revision_id"]
    assert cur_dossier["determination"]["state"] == "ADOPTED"
    assert cur_dossier["attestation"]["authority_basis"] == "Board resolution 2025-01"

    # 2. Historical dossier query at o1_seq returns exact adoption context
    hist_dossier = client.get(f"/api/obligations/{lineage}", params={"as_of_record_seq": o1_seq},
                              headers=H(cco_a)).json()
    assert hist_dossier["obligation"]["revision_id"] == S["obl1"]["revision_id"]
    assert hist_dossier["obligation"]["lifecycle"] == "ADOPTED"
    assert hist_dossier["as_of_record_seq"] == o1_seq
    assert len(hist_dossier["revisions"]) == 1
    assert hist_dossier["regulatory_provenance"]["rule_version_id"] == S["rv1"]["rule_version_id"]

    # 3. Out of bounds sequence rejected
    bad_req = client.get(f"/api/obligations/{lineage}", params={"as_of_record_seq": 10**15}, headers=H(cco_a))
    assert bad_req.status_code == 422
    assert bad_req.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"


def test_re_evaluation_after_correction_and_decision_closure(client, world):
    """Re-evaluation under corrected release flags review without mutating prior adoption; closure creates clean history."""
    cco_a = ORGS["A"][2]
    sid_a = ORGS["A"][1]
    lineage = S["obl_lineage"]
    o1_seq = S["obl1"]["record_from_seq"]

    # 1. Trigger current evaluation under r2 (rule version 2, payment institutions only)
    ev2 = client.post("/api/applicability/evaluations", headers=H(cco_a),
                      json={"subject_id": sid_a, "obligation_definition_id": S["odef_id"]}).json()
    assert ev2["outcome"] == "DOES_NOT_APPLY"
    assert ev2["rule_version_id"] == S["rv2"]["rule_version_id"]

    # 2. Determination for obligation is flagged for review, NOT silently overwritten
    det_rev = ev2["current_determination"]
    assert det_rev["state"] == "ADOPTED"
    assert det_rev["needs_review"] is True
    assert "DOES_NOT_APPLY differs from adopted APPLIES" in det_rev["review_reason"]

    # Obligation remains ADOPTED until human CCO attests
    dossier_mid = client.get(f"/api/obligations/{lineage}", headers=H(cco_a)).json()
    assert dossier_mid["obligation"]["lifecycle"] == "ADOPTED"

    # 3. CCO acknowledges non-applicability and attests decision
    close_att = client.post(f"/api/applicability/determinations/{det_rev['lineage_id']}/attest", headers=H(cco_a),
                            json={"decision": "ADOPT", "authority_basis": "Board review of correction r2"}).json()
    close_seq = close_att["determination"]["record_from_seq"]

    # 4. Obligation is closed; active revision lifecycle is CLOSED
    dossier_closed = client.get(f"/api/obligations/{lineage}", headers=H(cco_a)).json()
    assert dossier_closed["obligation"]["lifecycle"] == "CLOSED"
    assert len(dossier_closed["revisions"]) == 2
    assert [r["lifecycle"] for r in dossier_closed["revisions"]] == ["ADOPTED", "CLOSED"]

    # 5. Historical query at o1_seq STILL explains the historical obligation as ADOPTED under r1
    replay_dossier = client.get(f"/api/obligations/{lineage}", params={"as_of_record_seq": o1_seq},
                                headers=H(cco_a)).json()
    assert replay_dossier["obligation"]["lifecycle"] == "ADOPTED"
    assert replay_dossier["obligation"]["determination_revision_id"] == S["det1"]["revision_id"]
    assert len(replay_dossier["revisions"]) == 1


def test_historical_control_mapping_and_expression_version_resolution(client, world):
    """SatisfactionExpressionVersion history is preserved; as_of_record_seq strictly bounds expression versions."""
    cco_a = ORGS["A"][2]
    lineage = S["obl_lineage"]

    # 1. Fetch initial expression versions (version 1 from definition template)
    v1_list = client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(cco_a)).json()
    assert len(v1_list) == 1
    assert v1_list[0]["version"] == 1
    seq_v1 = v1_list[0]["record_seq"]

    # 2. Release expression version 2 (updating control mappings)
    cd_key = S["cd_key"]
    ast_v2 = {"node_type": "ALL", "node_id": "v2_all", "children": [
        {"node_type": "CONTROL", "node_id": "v2_c1", "control_key": cd_key, "rationale": "Digital register control leaf"}
    ]}
    v2_res = client.post(f"/api/obligations/{lineage}/expression-versions", headers=H(cco_a),
                         json={"ast": ast_v2, "valid_from": "2025-01-01T00:00:00Z",
                               "rationale": "Updated control mapping to composite ALL node"}).json()
    assert v2_res["version"] == 2
    seq_v2 = v2_res["record_seq"]

    # 3. Querying without sequence cutoff returns both versions
    all_vs = client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(cco_a)).json()
    assert len(all_vs) == 2
    assert [v["version"] for v in all_vs] == [1, 2]

    # 4. Querying at seq_v1 returns ONLY version 1; version 2 is strictly excluded
    hist_vs = client.get(f"/api/obligations/{lineage}/expression-versions",
                         params={"as_of_record_seq": seq_v1}, headers=H(cco_a)).json()
    assert len(hist_vs) == 1
    assert hist_vs[0]["version"] == 1

    # 5. Dossier at seq_v1 also includes only expression version 1
    dossier_v1 = client.get(f"/api/obligations/{lineage}",
                            params={"as_of_record_seq": seq_v1}, headers=H(cco_a)).json()
    assert len(dossier_v1["expression_versions"]) == 1
    assert dossier_v1["expression_versions"][0]["version"] == 1


def test_missing_or_incomplete_provenance_fails_closed(client, world):
    """When a rule version is missing from storage, provenance reports INCOMPLETE and missing=True."""
    from app.db import AdminSession
    from app.models import ApplicabilityDetermination, ApplicabilityEvaluation, Actor
    from app.routers.assurance import _provenance
    from app.security import Ctx

    cco_a = ORGS["A"][2]
    with AdminSession() as db:
        ctx = Ctx(db=db, actor=db.get(Actor, cco_a))
        # Evaluation pointing to missing rule version
        det = ApplicabilityDetermination(
            lineage_id="det-test-lin", tenant_id=ORGS["A"][0], subject_id=ORGS["A"][1],
            obligation_definition_id=S["odef_id"], evaluation_id="ev-missing-rule", outcome="APPLIES",
            state="ADOPTED", valid_from=datetime.now(timezone.utc), record_from_seq=1, actor_id=cco_a)
        ev = ApplicabilityEvaluation(
            evaluation_id="ev-missing-rule", tenant_id=ORGS["A"][0], entity_id=None, subject_id=ORGS["A"][1],
            obligation_definition_id=S["odef_id"], rule_version_id="00000000-missing-rv",
            effective_at=datetime.now(timezone.utc), record_cutoff_seq=1, evaluator_build="build",
            input_bundle={}, input_bundle_hash="hash", evaluation_key="key", outcome="APPLIES",
            trace={}, trigger="EXPLICIT", requested_by=cco_a, record_seq=1)
        db.add(det)
        db.add(ev)
        db.flush()

        prov = _provenance(ctx, det)
        assert prov is not None
        assert prov["binding_basis"] == "INCOMPLETE"
        assert prov["missing"] is True
        assert prov["rule_version_id"] == "00000000-missing-rv"
        db.rollback()


def test_cross_tenant_isolation_on_evaluations_and_obligations(client, world):
    """Tenant A cannot access Tenant B's evaluations, obligation dossiers, or expression versions."""
    cco_b = ORGS["B"][2]
    ev1_id = S["ev1"]["evaluation_id"]
    lineage = S["obl_lineage"]

    # Org B cannot inspect Org A's evaluation
    assert client.get(f"/api/applicability/evaluations/{ev1_id}", headers=H(cco_b)).status_code == 404

    # Org B cannot access Org A's obligation dossier
    assert client.get(f"/api/obligations/{lineage}", headers=H(cco_b)).status_code == 404

    # Org B cannot access Org A's expression versions
    assert client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(cco_b)).status_code == 404

    # Unauthenticated rejected
    assert client.get(f"/api/obligations/{lineage}").status_code == 401


def test_no_mutation_of_previous_decisions(client, world):
    """Reads and historical replays are strictly read-only and execute zero database writes."""
    from app.db import AdminSession
    from app.models import (ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot,
                          AuditEvent, ObligationInstance, SatisfactionExpressionVersion)

    cco_a = ORGS["A"][2]
    ev1_id = S["ev1"]["evaluation_id"]
    lineage = S["obl_lineage"]

    def snapshot_counts():
        with AdminSession() as db:
            return {
                "evaluations": db.execute(select(func.count(ApplicabilityEvaluation.evaluation_id))).scalar(),
                "determinations": db.execute(select(func.count(ApplicabilityDetermination.revision_id))).scalar(),
                "obligations": db.execute(select(func.count(ObligationInstance.revision_id))).scalar(),
                "expressions": db.execute(select(func.count(SatisfactionExpressionVersion.expression_version_id))).scalar(),
                "snapshots": db.execute(select(func.count(AssuranceSnapshot.snapshot_id))).scalar(),
                "audits": db.execute(select(func.count(AuditEvent.id))).scalar(),
            }

    before = snapshot_counts()

    # Perform multiple inspection calls
    for _ in range(5):
        client.get(f"/api/applicability/evaluations/{ev1_id}", headers=H(cco_a))
        client.get(f"/api/obligations/{lineage}", headers=H(cco_a))
        client.get(f"/api/obligations/{lineage}", params={"as_of_record_seq": S["cutoff_1"]}, headers=H(cco_a))
        client.get(f"/api/obligations/{lineage}/expression-versions", headers=H(cco_a))

    after = snapshot_counts()
    assert before == after
