"""Focused regression test suite for regulatory catalogue lifecycle and correction integrity.

Requirements exercised:
  * PRD-001 §3.1 and FR-WRK-003: Impacted tenants receive a signed correction notice and scoped impact report.
  * PRD-001 §12: Notifications delivered through the content release mechanism.
  * ZA-GLS-001: "Correction Notice" identifies affected releases and triggers scoped impact analysis.
  * ZA-RUL-001 §02: Release packages its RequirementVersion(s) and RuleVersion(s).
  * ZA-RUL-001 §08, §11: Source mapping validation, resolution, and grounding.
  * ZA-RUL-001 §12, WF-RUL-03: Signed manifest binds exact hashes; correction requires explicit reason.
  * ZA-TIM-001 WF-TIM-03: Impact counts and materiality as known then; historical reproducibility.
  * ZA-WRK-001 WRK-REQ-043: Corrections preserve the original release and customer decision history.
  * ZA-DAT-001 DAT-REQ-012: Published catalogue rows immutable; historical reproducibility.
  * ZA-API-001 §17: Correction flags, tenant-authorized impact summary without cross-tenant detail.
  * APP-REQ-006, RUL-REQ-027: Release verification detects missing/substituted links and altered content.
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
INSTR = f"CLI-{TAG}-REG"
AUTHOR, PEER, LEGAL, RELEASE = "actor-content-analyst", "actor-encoder", "actor-content-legal", "actor-release"
ORGS = {k: (f"tenant-cli{k.lower()}-{TAG.lower()}", f"subj-cli{k.lower()}-{TAG.lower()}", f"cli{k.lower()}-cco-{TAG.lower()}")
        for k in ("A", "B", "C")}
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
        {"vector_id": "CLI-POS", "vector_class": "POSITIVE", "description": "in scope", "effective_at": "2025-06-01",
         "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": entity_type},
         "expected_outcome": "APPLIES"},
        {"vector_id": "CLI-NEG", "vector_class": "NEGATIVE", "description": "outside the EU", "effective_at": "2025-06-01",
         "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": entity_type},
         "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "CLI-UNK", "vector_class": "UNKNOWN", "description": "type unknown", "effective_at": "2025-06-01",
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


def _verify(client, release_id: str, actor: str = AUTHOR) -> dict:
    r = client.get(f"/api/catalog/releases/{release_id}/verify", headers=H(actor))
    assert r.status_code == 200, r.text
    return r.json()


def _failed(report: dict) -> set[str]:
    return {c["check"] for c in report.get("checks", []) if c["status"] == "FAIL"}


@pytest.fixture(scope="module")
def setup_world(client):
    """Publish original release r1, adopt across Org A and Org B, then publish correction r2."""
    from app.db import CELL_ID, AdminSession
    from app.models import (AssuranceSnapshot, EvidencePackage, Instrument, LegalEntity,
                          ObligationDefinition, Subject, Tenant)

    with AdminSession() as db:
        db.add(Instrument(identifier=INSTR, title="Lifecycle Integrity Instrument", jurisdiction="EU",
                          authority="Test Authority", coverage_mode="ENCODED", regime_owner="Zoiko test"))
        for tid, sid, cco in ORGS.values():
            db.add(Tenant(tenant_id=tid, legal_name=f"{tid} Group", contracting_region="EU"))
            db.flush()
            db.add(LegalEntity(entity_id=f"entity-{tid}", tenant_id=tid, legal_name=f"{tid} Bank AG", country="DE",
                               home_cell_id=CELL_ID, subject_id=sid))
            db.add(Subject(subject_id=sid, tenant_id=tid, entity_id=f"entity-{tid}", subject_type="LEGAL_ENTITY",
                           display_name=f"{tid} Bank AG"))
            add_principal(db, actor_id=cco, tenant_id=tid, actor_type="HUMAN", display_name=cco, roles=["CCO"])
        db.commit()

    src = client.post("/api/catalog/sources", headers=H(AUTHOR), json={
        "authority": "Test Authority", "jurisdiction": "EU", "source_type": "PRIMARY_LEGISLATION",
        "title": "Lifecycle Test Regulation", "locator": f"OJ L {TAG}/life", "instrument_identifier": INSTR,
        "raw_text": "Financial entities shall maintain a register of third-party contracts and review it annually."})
    assert src.status_code == 200, src.text
    S["source"] = src.json()

    rv = client.post("/api/catalog/requirements", headers=H(AUTHOR), json={
        "source_id": S["source"]["source_id"], "instrument_identifier": INSTR, "requirement_key": f"CLI-{TAG}-REQ",
        "title": "Third-party register obligation", "locator": "Art. 10", "valid_from": "2025-01-01",
        "provisions": [
            {"locator": "Art. 10(1)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "maintain register",
             "regulated_object": "third-party contracts", "timing": "continuous",
             "normalized_meaning": "Maintain register.", "evidence_expectation": "Register"},
            {"locator": "Art. 10(2)", "duty_bearer": "FINANCIAL_ENTITY", "modality": "MUST", "action": "review register",
             "regulated_object": "third-party register", "timing": "annually",
             "normalized_meaning": "Review register annually.", "evidence_expectation": "Annual review record"}]})
    assert rv.status_code in (200, 201), rv.text
    S["rv"] = rv.json()
    assert client.post(f"/api/catalog/requirements/{S['rv']['requirement_version_id']}/verify",
                       headers=H(LEGAL)).status_code == 200

    with AdminSession() as db:
        inst = db.execute(select(Instrument).where(Instrument.identifier == INSTR)).scalars().one()
        o = ObligationDefinition(key=f"CLI_{TAG}_MAIN", title=f"Lifecycle probe obligation {TAG}",
                                 instrument_id=inst.instrument_id,
                                 provision_ids=[p["provision_id"] for p in S["rv"]["provisions"]],
                                 subject_type="LEGAL_ENTITY", coverage_mode="ENCODED", schedule_kind="CONTINUOUS",
                                 material=True)
        db.add(o)
        db.commit()
        S["odef_id"] = o.obligation_definition_id

    # 1. Publish original release r1
    r1 = _publish(client, _draft(client, S["odef_id"], "r1", "EXECUTABLE_OUTCOME"), f"cli-{TAG}-r1")
    assert r1.status_code == 200, r1.text
    S["r1"], S["rv1"] = r1.json()["content_release"], r1.json()["rule_version"]

    # 2. Org A and Org B adopt r1
    at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    for k in ("A", "B"):
        tid, sid, cco = ORGS[k]
        for key, value in (("entity.established_in_eu", True), ("entity.financial_entity_type", "CREDIT_INSTITUTION")):
            assert client.post(f"/api/subjects/{sid}/facts", headers=H(cco),
                               json={"fact_key": key, "value": value, "valid_from": at}).status_code == 200
        ev = client.post("/api/applicability/evaluations", headers=H(cco),
                         json={"subject_id": sid, "obligation_definition_id": S["odef_id"]}).json()
        assert ev["outcome"] == "APPLIES" and ev["rule_version_id"] == S["rv1"]["rule_version_id"], ev
        assert client.post(f"/api/applicability/determinations/{ev['current_determination']['lineage_id']}/attest",
                           headers=H(cco), json={"decision": "ADOPT", "authority_basis": "Board resolution"}).status_code == 200
        [obl] = [o for o in client.get("/api/obligations", headers=H(cco)).json()
                 if o["obligation_definition_id"] == S["odef_id"]]
        S[f"eval_{k}"], S[f"obl_{k}"] = ev["evaluation_id"], obl

    # 3. Org B creates snapshot and package
    tid, sid, _ = ORGS["B"]
    with AdminSession() as db:
        snap = AssuranceSnapshot(tenant_id=tid, entity_id=f"entity-{tid}",
                                 obligation_lineage_id=S["obl_B"]["lineage_id"],
                                 obligation_revision_id=S["obl_B"]["revision_id"],
                                 period_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 period_to=datetime(2026, 6, 1, tzinfo=timezone.utc),
                                 record_cutoff_seq=1, perspective="AS_KNOWN_THEN",
                                 expression_version_id="probe", rollup_engine_version="probe", state="PROVEN",
                                 segments=[], dependency_manifest={}, dependency_digest="0" * 64,
                                 snapshot_hash="0" * 64, created_by="probe")
        db.add(snap)
        db.flush()
        pkg = EvidencePackage(tenant_id=tid, entity_id=f"entity-{tid}", period_from=snap.period_from,
                              period_to=snap.period_to, record_cutoff_seq=1,
                              scope={"evidence_ids": [], "snapshot_ids": [snap.snapshot_id]},
                              manifest_hash="2" * 64, signer_key_id="probe", file_ref="probe",
                              verification_report={}, created_by="probe")
        db.add(pkg)
        db.commit()
        S["snapshot_B"], S["package_B"] = snap.snapshot_id, pkg.package_id

    # 4. Publish correction release r2
    reason = "Regulation applies exclusively to payment institutions; credit institutions erroneously encoded in r1."
    did_c = _draft(client, S["odef_id"], "c1", "CORRECTION", "PAYMENT_INSTITUTION")
    r2 = _publish(client, did_c, f"cli-{TAG}-r2", correction_reason=reason)
    assert r2.status_code == 200, r2.text
    S["r2"], S["rv2"] = r2.json()["content_release"], r2.json()["rule_version"]
    S["correction_reason"] = reason
    return True


# ------------------------------------------------------------------------------------------------ Tests

def test_historical_release_reproducibility_after_correction(client, setup_world):
    """A correction creates a new verifiable release without mutating or invalidating the historical release."""
    from app.db import AdminSession
    from app.models import ContentRelease, RuleVersion

    r1_id = S["r1"]["content_release_id"]
    r2_id = S["r2"]["content_release_id"]

    # 1. Historical release record r1 is unchanged in DB
    with AdminSession() as db:
        r1_db = db.get(ContentRelease, r1_id)
        assert r1_db is not None
        assert r1_db.label == f"cli-{TAG}-r1"
        assert r1_db.manifest_hash == S["r1"]["manifest_hash"]
        assert r1_db.signature == S["r1"]["signature"]
        assert r1_db.correction_of_release_id is None

        rv1_db = db.get(RuleVersion, S["rv1"]["rule_version_id"])
        assert rv1_db is not None
        assert rv1_db.content_release_id == r1_id
        assert rv1_db.ast_hash == S["rv1"]["ast_hash"]

    # 2. Historical release r1 independently verifies as INTACT
    rep1 = _verify(client, r1_id)
    assert rep1["manifest_hash_ok"] is True
    assert rep1["integrity"] == "INTACT"
    assert rep1["outcome"] == "UNVERIFIABLE"  # unverified trust anchor
    assert not _failed(rep1)

    # 3. Correction release r2 references r1 and independently verifies
    rep2 = _verify(client, r2_id)
    assert rep2["manifest_hash_ok"] is True
    assert rep2["integrity"] == "INTACT"
    assert rep2["outcome"] == "UNVERIFIABLE"
    assert not _failed(rep2)

    checks2 = {c["check"]: c["status"] for c in rep2["checks"]}
    assert checks2["CORRECTION_METADATA"] == "PASS"
    assert checks2["RULE_SOURCE_MAPPINGS"] == "PASS"
    assert checks2["OBLIGATION_DEFINITION_PRESENT"] == "PASS"


def test_signed_manifest_integrity_and_tamper_detection(client, setup_world):
    """Release verification detects altered correction metadata, missing target release, and corrupted reasons."""
    r2_id = S["r2"]["content_release_id"]

    # 1. Altered correction reason in signed manifest is caught by signature/manifest hash
    corrupted_manifest = lambda row: {
        **row["manifest"],
        "correction": {**row["manifest"]["correction"], "reason": "Tampered unauthorized reason"}
    }
    with _tampered("catalog_content_release", "content_release_id", r2_id, manifest=corrupted_manifest):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert rep["integrity"] == "ALTERED"
        assert {"MANIFEST_HASH", "SIGNATURE"} <= _failed(rep)

    # 2. Detached correction release (DB column nullified while manifest declares correction)
    with _tampered("catalog_content_release", "content_release_id", r2_id, correction_of_release_id=None):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert rep["integrity"] == "ALTERED"
        assert "CORRECTION_METADATA" in _failed(rep)

    # 3. Repointed correction release targeting a non-existent release
    with _tampered("catalog_content_release", "content_release_id", r2_id, correction_of_release_id="00000000-none"):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert rep["integrity"] == "ALTERED"
        assert "CORRECTION_METADATA" in _failed(rep)


def test_missing_and_substituted_traceability_links_detected(client, setup_world):
    """Verification detects substituted requirement versions, unbound provisions, and missing obligation definitions."""
    r2_id = S["r2"]["content_release_id"]
    rv2_id = S["rv2"]["rule_version_id"]

    # 1. Source mapping cites requirement version not bound in release
    fake_sm = lambda row: [
        {"source_mapping_id": "sm_unbound", "requirement_version_id": "00000000-unbound-rv",
         "provision_id": S["rv"]["provisions"][0]["provision_id"], "mapping_type": "DIRECT_TEXT"}
    ]
    with _tampered("catalog_rule_version", "rule_version_id", rv2_id, source_mappings=fake_sm):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert rep["integrity"] == "ALTERED"
        assert "RULE_SOURCE_MAPPINGS" in _failed(rep)

    # 2. Source mapping cites provision not bound in release requirement
    fake_prov_sm = lambda row: [
        {"source_mapping_id": "sm_bad_prov", "requirement_version_id": S["rv"]["requirement_version_id"],
         "provision_id": "00000000-unbound-prov", "mapping_type": "DIRECT_TEXT"}
    ]
    with _tampered("catalog_rule_version", "rule_version_id", rv2_id, source_mappings=fake_prov_sm):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert "RULE_SOURCE_MAPPINGS" in _failed(rep)

    # 3. Source mapping fingerprint mismatch
    fake_fp_sm = lambda row: [
        {"source_mapping_id": "sm_bad_fp", "requirement_version_id": S["rv"]["requirement_version_id"],
         "provision_id": S["rv"]["provisions"][0]["provision_id"], "source_fingerprint": "f" * 64,
         "mapping_type": "DIRECT_TEXT"}
    ]
    with _tampered("catalog_rule_version", "rule_version_id", rv2_id, source_mappings=fake_fp_sm):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert "RULE_SOURCE_MAPPINGS" in _failed(rep)

    # 4. Obligation definition link pointing to missing definition (database FK protects storage; engine verifies)
    from app.db import AdminSession
    from app.engines import release_verification
    from app.models import ContentRelease
    with AdminSession() as db:
        real_get = db.get
        def mock_get(model, ident):
            if getattr(model, "__name__", "") == "ObligationDefinition":
                return None
            return real_get(model, ident)
        db.get = mock_get
        mock_checks = []
        cr = real_get(ContentRelease, r2_id)
        release_verification._rule_versions(db, cr, cr.manifest, mock_checks)
        failed_checks = {c["check"] for c in mock_checks if c["status"] == "FAIL"}
        assert "OBLIGATION_DEFINITION_PRESENT" in failed_checks


def test_source_fingerprint_and_content_tampering_detected(client, setup_world):
    """Release verification detects changed source fingerprints and modified source text."""
    r2_id = S["r2"]["content_release_id"]
    src_id = S["source"]["source_id"]

    # 1. Modifying source record's fingerprint violates release binding
    with _tampered("catalog_source_record", "source_id", src_id, fingerprint="0" * 64):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert rep["integrity"] == "ALTERED"
        assert {"SOURCE_FINGERPRINT", "SOURCE_CONTENT"} <= _failed(rep)

    # 2. Modifying source raw_text so it no longer hashes to its registered fingerprint
    with _tampered("catalog_source_record", "source_id", src_id, raw_text="Substituted unofficial legislative text."):
        rep = _verify(client, r2_id)
        assert rep["outcome"] == "INVALID"
        assert "SOURCE_CONTENT" in _failed(rep)


def test_correction_impact_reproducibility_across_record_sequences(client, setup_world):
    """Historical impact reports are reproducible as known at requested record sequence."""
    r2_id = S["r2"]["content_release_id"]
    r2_seq = S["r2"]["cell_received_seq"]
    cco_a = ORGS["A"][2]

    # 1. At the exact release record sequence, re-evaluation has not yet occurred
    rep_at_release = client.get(f"/api/catalog/releases/{r2_id}/impact",
                                params={"as_of_record_seq": r2_seq}, headers=H(cco_a)).json()
    t_hist = rep_at_release["tenant_impact"]
    assert t_hist["impact"] == "CONFIRMED"
    assert t_hist["dependencies_complete"] is False
    assert {p["code"] for p in t_hist["problems"]} == {"RE_EVALUATION_NOT_RECORDED"}
    assert t_hist["subjects"][0]["outcome_changed"] is None

    # 2. Org A performs re-evaluation under r2 (outcome changes from APPLIES to DOES_NOT_APPLY)
    _, sid_a, _ = ORGS["A"]
    ev2 = client.post("/api/applicability/evaluations", headers=H(cco_a),
                      json={"subject_id": sid_a, "obligation_definition_id": S["odef_id"]}).json()
    assert ev2["outcome"] == "DOES_NOT_APPLY"
    assert ev2["rule_version_id"] == S["rv2"]["rule_version_id"]

    # 3. Querying at the historical sequence r2_seq STILL yields the exact historical report
    rep_replay = client.get(f"/api/catalog/releases/{r2_id}/impact",
                            params={"as_of_record_seq": r2_seq}, headers=H(cco_a)).json()
    assert rep_replay["tenant_impact"] == t_hist

    # 4. Current impact (as_of_record_seq omitted) now shows completed re-evaluation and outcome change
    rep_now = client.get(f"/api/catalog/releases/{r2_id}/impact", headers=H(cco_a)).json()
    t_now = rep_now["tenant_impact"]
    assert t_now["impact"] == "CONFIRMED"
    assert t_now["dependencies_complete"] is True
    assert t_now["subjects"][0]["outcome_changed"] is True
    assert t_now["subjects"][0]["corrected_outcome"] == "DOES_NOT_APPLY"
    assert t_now["counts"]["material_changes"] == 1

    # 5. Invalid sequence bounds rejected
    bad_past = client.get(f"/api/catalog/releases/{r2_id}/impact",
                          params={"as_of_record_seq": r2_seq - 1}, headers=H(cco_a))
    assert bad_past.status_code == 422
    assert bad_past.json()["detail"]["error"] == "RECORD_SEQ_BEFORE_RELEASE"

    bad_future = client.get(f"/api/catalog/releases/{r2_id}/impact",
                            params={"as_of_record_seq": 10**14}, headers=H(cco_a))
    assert bad_future.status_code == 422
    assert bad_future.json()["detail"]["error"] == "RECORD_SEQ_IN_FUTURE"


def test_incomplete_historical_data_handling(client, setup_world):
    """Missing dependency or binding data is reported as INCOMPLETE, never as NONE_RECORDED."""
    from app.db import AdminSession
    from app.models import ContentRelease

    with AdminSession() as db:
        # A release that lacks requirement binding in manifest
        early_release = next(r for r in db.execute(select(ContentRelease)).scalars()
                             if "requirement_versions" not in (r.manifest or {}))

    cco_c = ORGS["C"][2]
    rep = client.get(f"/api/catalog/releases/{early_release.content_release_id}/impact", headers=H(cco_c)).json()
    assert {p["code"] for p in rep["scope_problems"]} >= {"REQUIREMENT_BINDING_NOT_RECORDED"}
    assert rep["tenant_impact"]["impact"] == "INCOMPLETE"
    assert rep["tenant_impact"]["dependencies_complete"] is False


def test_repeated_impact_reads_are_idempotent_and_mutate_nothing(client, setup_world):
    """Impact analysis is strictly read-only: repeated reads do not alter tenant assurance state."""
    from app.db import AdminSession
    from app.models import (ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot,
                          AuditEvent, EvidencePackage, ObligationInstance)

    r2_id = S["r2"]["content_release_id"]
    cco_a = ORGS["A"][2]

    def state_fingerprint():
        with AdminSession() as db:
            return {
                "evaluations": db.execute(select(func.count(ApplicabilityEvaluation.evaluation_id))).scalar(),
                "determinations": db.execute(select(func.count(ApplicabilityDetermination.revision_id))).scalar(),
                "obligations": db.execute(select(func.count(ObligationInstance.revision_id))).scalar(),
                "snapshots": db.execute(select(func.count(AssuranceSnapshot.snapshot_id))).scalar(),
                "packages": db.execute(select(func.count(EvidencePackage.package_id))).scalar(),
                "audits": db.execute(select(func.count(AuditEvent.id))).scalar(),
            }

    before = state_fingerprint()
    results = [client.get(f"/api/catalog/releases/{r2_id}/impact", headers=H(cco_a)).json() for _ in range(5)]
    after = state_fingerprint()

    # Zero database writes or side-effects across all assurance tables
    assert before == after
    # Deterministic output across repeats
    assert len({json.dumps(r, sort_keys=True) for r in results}) == 1

    # Existing adopted obligations retain their historical provenance
    obls = client.get("/api/obligations", headers=H(cco_a)).json()
    target_obls = [o for o in obls if o["obligation_definition_id"] == S["odef_id"]]
    assert len(target_obls) == 1
    assert target_obls[0]["lifecycle"] == "ADOPTED"
    assert target_obls[0]["lineage_id"] == S["obl_A"]["lineage_id"]


def test_tenant_isolation_and_authorization(client, setup_world):
    """Org A cannot view Org B's impact; platform staff cannot access tenant data; unauthenticated denied."""
    r2_id = S["r2"]["content_release_id"]
    cco_a, cco_b = ORGS["A"][2], ORGS["B"][2]

    resp_a = client.get(f"/api/catalog/releases/{r2_id}/impact", headers=H(cco_a)).json()
    resp_b = client.get(f"/api/catalog/releases/{r2_id}/impact", headers=H(cco_b)).json()

    raw_a = json.dumps(resp_a)
    raw_b = json.dumps(resp_b)

    # Org A sees none of Org B's identifiers
    assert ORGS["B"][0] not in raw_a  # Org B tenant_id
    assert ORGS["B"][1] not in raw_a  # Org B subject_id
    assert S["eval_B"] not in raw_a
    assert S["obl_B"]["lineage_id"] not in raw_a
    assert S["snapshot_B"] not in raw_a
    assert S["package_B"] not in raw_a

    # Org B sees none of Org A's identifiers
    assert ORGS["A"][0] not in raw_b  # Org A tenant_id
    assert ORGS["A"][1] not in raw_b  # Org A subject_id
    assert S["eval_A"] not in raw_b
    assert S["obl_A"]["lineage_id"] not in raw_b

    # Org B holds potential assurance impact (snapshots and packages); Org A does not
    assert resp_b["tenant_impact"]["potential"]["snapshots"][0]["snapshot_id"] == S["snapshot_B"]
    assert resp_b["tenant_impact"]["potential"]["packages"][0]["package_id"] == S["package_B"]
    assert resp_a["tenant_impact"]["potential"]["snapshots"] == []
    assert resp_a["tenant_impact"]["potential"]["packages"] == []

    # Platform staff only sees global release scope and shadow summary, zero tenant data (INV-14)
    staff = client.get(f"/api/catalog/releases/{r2_id}/impact", headers=H(RELEASE))
    assert staff.status_code == 200
    assert staff.json()["tenant_impact"] is None
    assert staff.json()["is_correction"] is True
    assert staff.json()["scope"]["rule_version_ids"] == [S["rv2"]["rule_version_id"]]

    # Unauthenticated rejected
    assert client.get(f"/api/catalog/releases/{r2_id}/impact").status_code == 401
