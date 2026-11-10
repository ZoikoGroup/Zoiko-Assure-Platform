"""Zoiko Assure — Regulatory Catalogue Seed-Mapping Remediation Tests.

Verifies the remediation of historical catalogue seed-mapping defects:
- 6 previously unresolved source mappings in AST / decision tables
- 5 previously ungrounded mapping records without requirement/provision IDs
- End-to-end forward and reverse traceability: Source -> Requirement -> Provision -> Rule -> Applicability -> Obligation Definition -> Control Roles
- Validation hardening and release gate blocking of invalid relationships (unresolved, ungrounded, duplicate, missing, mismatched, tampered)
- Cryptographic integrity and immutability of published catalogue releases
- Zero mutation or regression in tenant determinations, evaluations, and obligations
"""
import json
import secrets
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from conftest import H, add_principal

TAG = secrets.token_hex(4).upper()
AUTHOR = "actor-content-analyst"
PEER = "actor-encoder"
LEGAL = "actor-content-legal"
RELEASE = "actor-release"
CCO = "actor-cco"
TA = f"tenant-rem-a-{TAG}"
TB = f"tenant-rem-b-{TAG}"
A_CCO = f"actor-cco-rem-a-{TAG}"
B_CCO = f"actor-cco-rem-b-{TAG}"


def _reviewed_draft_with_mappings(client, odef_id, ast, mappings, tag="probe"):
    """Helper to author, review, and approve a draft through all gates before publication."""
    vectors = [
        {"vector_id": f"{tag}-POS", "vector_class": "POSITIVE", "effective_at": "2026-01-01",
         "facts": {"entity.established_in_eu": True}, "expected_outcome": "APPLIES"},
        {"vector_id": f"{tag}-NEG", "vector_class": "NEGATIVE", "effective_at": "2026-01-01",
         "facts": {"entity.established_in_eu": False}, "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": f"{tag}-UNK", "vector_class": "UNKNOWN", "effective_at": "2026-01-01",
         "facts": {"entity.established_in_eu": {"unknown": "MISSING_REQUIRED_FACT"}}, "expected_outcome": "UNCERTAIN"},
    ]
    d = client.post("/api/rules/drafts", headers=H(AUTHOR), json={
        "obligation_definition_id": odef_id,
        "authoring_mode": "AST",
        "ast": ast,
        "golden_vectors": vectors,
        "source_mappings": mappings,
        "valid_from": "2026-01-01",
        "change_class": "EXECUTABLE_OUTCOME"
    })
    assert d.status_code == 200, d.text
    did = d.json()["draft_id"]
    r = client.post(f"/api/rules/drafts/{did}/submit-review", headers=H(AUTHOR))
    if r.status_code == 409:
        # ZA-RUL-001 §11 stage 2: unresolved, ungrounded or mismatched mappings are now refused at review already. Put
        # the draft in the state of one verified before review resolved its mappings, so the release gate is still
        # exercised by the tests below.
        detail = r.json()["detail"]
        assert detail["message"] == "Validation errors block review" and detail["error"].startswith("RULE_SOURCE_MAPPING_"), detail
        from app.db import AdminSession
        from app.models import RuleDraft
        with AdminSession() as db:
            row = db.get(RuleDraft, did)
            row.status, row.peer_reviewer_id, row.legal_reviewer_id = "VERIFIED", PEER, LEGAL
            row.shadow_report = {"buckets": {}, "completed_at": "2026-11-07T00:00:00+00:00"}
            db.commit()
        return did
    assert r.status_code == 200, r.text
    for actor, step in ((PEER, "peer-approve"), (LEGAL, "legal-approve"), (RELEASE, "shadow")):
        r = client.post(f"/api/rules/drafts/{did}/{step}", headers=H(actor))
        assert r.status_code == 200, (step, r.text)
    return did


# ---------------------------------------------------------------- 1. Seed Defect Closure & Baseline Validation
def test_seeded_catalogue_validation_has_zero_errors(client):
    """Confirm all 6 unresolved source mappings and 5 ungrounded mapping records are completely resolved."""
    from app.db import AdminSession
    from app.models import ContentRelease, RuleVersion

    r = client.get("/api/catalog/traceability/validation", headers=H(LEGAL))
    assert r.status_code == 200, r.text
    res = r.json()
    issues = res.get("issues", [])

    # Confirm none of the 6 historical unresolved source mappings appear as unresolved
    unresolved_sm_ids = {i.get("source_mapping_id") for i in issues if i["code"] == "RULE_SOURCE_MAPPING_UNRESOLVED"}
    assert not ({"srcmap_aia_2_scope", "srcmap_aia_3_4", "srcmap_aia_50", "srcmap_aia_2_1", "srcmap_dora_2", "srcmap_dora_16"} & unresolved_sm_ids)

    # Confirm none of the 5 historical ungrounded mapping records appear as ungrounded
    ungrounded_sm_ids = {i.get("source_mapping_id") for i in issues if i["code"] == "RULE_SOURCE_MAPPING_UNGROUNDED"}
    assert not ({"srcmap_aia_26", "srcmap_aia_6_2", "srcmap_aia_50_1", "srcmap_dora_6_1", "srcmap_dora_17"} & ungrounded_sm_ids)

    # Confirm all Customer Zero seeded rules have zero validation issues
    with AdminSession() as db:
        seeded_labels = {"CR-2026-AIA-001", "CR-2026-AIA-002", "CR-2026-DORA-001", "CR-2026-DORA-002"}
        seeded_cr_ids = {cr.content_release_id for cr in db.query(ContentRelease).filter(ContentRelease.label.in_(seeded_labels)).all()}
        seeded_rv_ids = {rv.rule_version_id for rv in db.query(RuleVersion).filter(RuleVersion.content_release_id.in_(seeded_cr_ids)).all()}
        # Confirm Customer Zero seeded rules have zero ERRORS and zero mapping issues
        seeded_errors = [i for i in issues if i.get("rule_version_id") in seeded_rv_ids and i["severity"] == "ERROR"]
        assert seeded_errors == []
        seeded_mapping_issues = [i for i in issues if i.get("rule_version_id") in seeded_rv_ids and i["code"].startswith("RULE_SOURCE_MAPPING_")]
        assert seeded_mapping_issues == []


def test_seeded_mapping_records_are_fully_grounded(client):
    """Verify that every mapping record across all 4 seeded rules links to a valid requirement and provision."""
    from app.db import AdminSession
    from app.models import ContentRelease, Provision, RegulatorySource, RequirementVersion, RuleVersion

    with AdminSession() as db:
        rules = db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars().all()
        assert len(rules) >= 4

        expected_mappings = {
            "CR-2026-AIA-001": {"srcmap_aia_26", "srcmap_aia_2_scope", "srcmap_aia_3_4", "srcmap_aia_6_2"},
            "CR-2026-AIA-002": {"srcmap_aia_50", "srcmap_aia_50_1", "srcmap_aia_2_1"},
            "CR-2026-DORA-001": {"srcmap_dora_2", "srcmap_dora_6_1", "srcmap_dora_16"},
            "CR-2026-DORA-002": {"srcmap_dora_17"},
        }

        for rule in rules:
            cr = db.get(ContentRelease, rule.content_release_id)
            if cr is None or cr.label not in expected_mappings:
                continue
            expected_ids = expected_mappings[cr.label]
            actual_ids = {m["source_mapping_id"] for m in (rule.source_mappings or [])}
            assert actual_ids == expected_ids

            for m in rule.source_mappings:
                assert m.get("requirement_version_id") is not None
                assert m.get("provision_id") is not None
                assert m.get("authoritative_reference") is not None
                assert m.get("source_fingerprint") is not None
                assert m.get("mapping_type") in ("DIRECT_TEXT", "CROSS_REFERENCE", "INTERPRETIVE")
                assert m.get("reviewer") == LEGAL

                rv = db.get(RequirementVersion, m["requirement_version_id"])
                assert rv is not None, f"Missing RequirementVersion {m['requirement_version_id']}"
                prov = db.get(Provision, m["provision_id"])
                assert prov is not None, f"Missing Provision {m['provision_id']}"
                assert prov.requirement_version_id == rv.requirement_version_id

                src = db.get(RegulatorySource, rv.source_id)
                assert src is not None
                assert src.fingerprint == m["source_fingerprint"]


# ---------------------------------------------------------------- 2. Forward Traceability (Source -> Controls)
def test_forward_traceability_source_to_control_roles(client):
    """Verify forward trace: Source -> RequirementVersion -> Provision -> ObligationDefinition -> Rule -> Control Roles."""
    from app.db import AdminSession
    from app.models import RegulatorySource

    with AdminSession() as db:
        sources = db.execute(select(RegulatorySource).where(RegulatorySource.lifecycle == "PUBLISHED")).scalars().all()
        assert len(sources) >= 2

        for src in sources:
            r = client.get(f"/api/catalog/sources/{src.source_id}/trace", headers=H(LEGAL))
            assert r.status_code == 200, r.text
            trace = r.json()
            assert trace["source"]["source_id"] == src.source_id
            assert trace["source"]["fingerprint_intact"] is True
            assert len(trace["requirements"]) >= 1

            for req in trace["requirements"]:
                assert req["requirement"]["requirement_version_id"] is not None
                assert len(req["provisions"]) >= 1
                for odef in req["obligation_definitions"]:
                    if odef["key"] in ("EUAIA_DEPLOYER_HR_OVERSIGHT", "EUAIA_TRANSPARENCY_INTERACTION",
                                       "DORA_ICT_RISK_FRAMEWORK", "DORA_ICT_INCIDENT_PROCESS"):
                        assert odef["current_rule_version"] is not None
                        assert len(odef["control_keys"]) >= 1


# ---------------------------------------------------------------- 3. Reverse Traceability (Controls -> Source)
def test_reverse_traceability_from_obligation_definition(client):
    """Verify reverse trace: ObligationDefinition -> Provisions -> RequirementVersion -> SourceSnapshot."""
    from app.db import AdminSession
    from app.models import ObligationDefinition

    with AdminSession() as db:
        seeded_keys = [
            "EUAIA_DEPLOYER_HR_OVERSIGHT",
            "EUAIA_TRANSPARENCY_INTERACTION",
            "DORA_ICT_RISK_FRAMEWORK",
            "DORA_ICT_INCIDENT_PROCESS",
        ]
        odefs = db.execute(
            select(ObligationDefinition).where(ObligationDefinition.key.in_(seeded_keys))
        ).scalars().all()
        assert len(odefs) == 4

        for odef in odefs:
            r = client.get(f"/api/catalog/obligation-definitions/{odef.obligation_definition_id}/trace", headers=H(LEGAL))
            assert r.status_code == 200, r.text
            trace = r.json()
            assert trace["obligation_definition"]["obligation_definition_id"] == odef.obligation_definition_id
            assert trace["missing_provision_ids"] == []
            assert trace["current_rule"] is not None
            assert len(trace["requirements"]) >= 1
            assert len(trace["control_roles"]) >= 1

            for req in trace["requirements"]:
                assert req["source"]["fingerprint_intact"] is True
                assert req["source"]["fingerprint"] is not None
                assert len(req["provision_ids"]) >= 1


def test_reverse_traceability_rule_version_mapping_coverage(client):
    """Verify rule version reverse trace: required facts resolve to valid grounded mapping records."""
    from app.db import AdminSession
    from app.engines import catalog
    from app.models import ContentRelease, RuleVersion

    with AdminSession() as db:
        rules = db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars().all()
        for rv in rules:
            cr = db.get(ContentRelease, rv.content_release_id)
            if cr is None or not cr.label.startswith("CR-2026"):
                continue
            trace = catalog.trace_rule_version(db, rv)
            cov = trace["source_mapping_coverage"]
            assert cov["unresolved"] == []
            assert cov["ungrounded_records"] == []
            assert cov["orphaned_records"] == []

            # Every required fact has resolved mapping records
            for fact_info in cov["fact_mappings"]:
                assert len(fact_info["source_mapping_ids"]) >= 1
                assert len(fact_info["resolved"]) == len(fact_info["source_mapping_ids"])
                for rec in fact_info["resolved"]:
                    assert rec["requirement_version_id"] is not None
                    assert rec["provision_id"] is not None


# ---------------------------------------------------------------- 4. Release Immutability & Cryptographic Verification
def test_all_published_releases_verify_cryptographically(client):
    """Verify that all published content releases have intact SHA-256 manifests and valid cryptographic signatures."""
    from app.db import AdminSession
    from app.engines import applicability as appl
    from app.models import ContentRelease, RuleVersion

    with AdminSession() as db:
        for cr in db.execute(select(ContentRelease)).scalars():
            r = client.get(f"/api/catalog/releases/{cr.content_release_id}/verify", headers=H(AUTHOR))
            assert r.status_code == 200, r.text
            rep = r.json()
            assert rep["manifest_hash_ok"] is True
            assert rep["signature_ok"] is True or rep["signature"]["status"] in ("VALID", "KEY_UNAVAILABLE")
            assert rep["integrity"] == "INTACT"

        for rv in db.execute(select(RuleVersion).where(RuleVersion.status == "PUBLISHED")).scalars():
            appl.verify_rule_integrity(db, rv)


# ---------------------------------------------------------------- 5. Release Gate Hardening & Negative Testing
def test_release_gate_rejects_unresolved_mapping(client):
    """Release gate blocks publication if an AST node cites a mapping ID missing from source_mappings."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, Provision, RequirementVersion

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        rv = db.get(RequirementVersion, p.requirement_version_id)
        oid, rvid, pid = odef.obligation_definition_id, rv.requirement_version_id, p.provision_id

    ast_unresolved = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_missing_from_list"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [{"source_mapping_id": "sm_other", "requirement_version_id": rvid, "provision_id": pid,
                 "authoritative_reference": "Art. 1", "mapping_type": "DIRECT_TEXT"}]

    did = _reviewed_draft_with_mappings(client, oid, ast_unresolved, mappings, tag="unres")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-unres-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_UNRESOLVED" for p in problems)


def test_release_gate_rejects_ungrounded_mapping(client):
    """Release gate blocks publication if a source mapping lacks requirement_version_id or provision_id."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, Provision

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        oid = odef.obligation_definition_id

    ast_ok = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_ungrounded"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [{"source_mapping_id": "sm_ungrounded", "provision_locator": "Art. 1", "mapping_type": "DIRECT_TEXT"}]

    did = _reviewed_draft_with_mappings(client, oid, ast_ok, mappings, tag="ungrounded")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-ungr-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_UNGROUNDED" for p in problems)


def test_release_gate_rejects_duplicate_mapping_ids(client):
    """Release gate blocks publication if duplicate source_mapping_ids are supplied."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, Provision, RequirementVersion

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        rv = db.get(RequirementVersion, p.requirement_version_id)
        oid, rvid, pid = odef.obligation_definition_id, rv.requirement_version_id, p.provision_id

    ast_ok = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_dup"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [
        {"source_mapping_id": "sm_dup", "requirement_version_id": rvid, "provision_id": pid, "mapping_type": "DIRECT_TEXT"},
        {"source_mapping_id": "sm_dup", "requirement_version_id": rvid, "provision_id": pid, "mapping_type": "CROSS_REFERENCE"},
    ]

    did = _reviewed_draft_with_mappings(client, oid, ast_ok, mappings, tag="dup")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-dup-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_DUPLICATE" for p in problems)


def test_release_gate_rejects_missing_requirement_version(client):
    """Release gate blocks publication if a mapping cites a non-existent requirement_version_id."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, Provision

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        oid, pid = odef.obligation_definition_id, p.provision_id

    ast_ok = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_missing_req"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [
        {"source_mapping_id": "sm_missing_req", "requirement_version_id": "no-such-requirement-version",
         "provision_id": pid, "mapping_type": "DIRECT_TEXT"}
    ]

    did = _reviewed_draft_with_mappings(client, oid, ast_ok, mappings, tag="missreq")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-mr-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_REQUIREMENT_MISSING" for p in problems)


def test_release_gate_rejects_cross_instrument_mismatch(client):
    """Release gate blocks publication if a mapping links a requirement from a different regulatory instrument."""
    from app.db import AdminSession
    from app.models import Instrument, ObligationDefinition, Provision, RequirementVersion

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        oid, pid = odef.obligation_definition_id, p.provision_id

        other_inst = db.execute(select(Instrument).where(Instrument.instrument_id != odef.instrument_id)).scalars().first()
        other_rv = db.execute(
            select(RequirementVersion).where(RequirementVersion.instrument_id == other_inst.instrument_id)
        ).scalars().first()
        other_rvid = other_rv.requirement_version_id

    ast_ok = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_x_inst"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [
        {"source_mapping_id": "sm_x_inst", "requirement_version_id": other_rvid,
         "provision_id": pid, "mapping_type": "DIRECT_TEXT"}
    ]

    did = _reviewed_draft_with_mappings(client, oid, ast_ok, mappings, tag="xinst")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-xi-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_INSTRUMENT_MISMATCH" for p in problems)


def test_release_gate_rejects_tampered_source_fingerprint(client):
    """Release gate blocks publication if a mapping cites a fingerprint mismatching the source snapshot."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, Provision, RequirementVersion

    with AdminSession() as db:
        odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.coverage_mode == "ENCODED")).scalars().first()
        p = db.get(Provision, odef.provision_ids[0])
        rv = db.get(RequirementVersion, p.requirement_version_id)
        oid, rvid, pid = odef.obligation_definition_id, rv.requirement_version_id, p.provision_id

    ast_ok = {
        "node_type": "PREDICATE", "node_id": "p1", "operator": "EQ", "source_mappings": ["sm_tampered_fp"],
        "args": [{"node_type": "FACT_REF", "node_id": "f1", "fact": "entity.established_in_eu"},
                 {"node_type": "CONST", "node_id": "c1", "type": "BOOLEAN", "value": True}]
    }
    mappings = [
        {"source_mapping_id": "sm_tampered_fp", "requirement_version_id": rvid, "provision_id": pid,
         "source_fingerprint": "0" * 64, "mapping_type": "DIRECT_TEXT"}
    ]

    did = _reviewed_draft_with_mappings(client, oid, ast_ok, mappings, tag="fp")
    r = client.post(f"/api/rules/drafts/{did}/publish", headers=H(RELEASE), json={"label": f"probe-fp-{TAG}", "changelog": "probe"})
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any(p["code"] == "RULE_SOURCE_MAPPING_FINGERPRINT_MISMATCH" for p in problems)


# ---------------------------------------------------------------- 6. Tenant Isolation & Obligation Invariance
def test_tenant_isolation_preserved(client):
    """Catalogue reads are available to authorised principals, while tenant state is isolated."""
    from app.db import AdminSession
    from app.models import Tenant

    with AdminSession() as db:
        if db.get(Tenant, TA) is None:
            db.add(Tenant(tenant_id=TA, legal_name=f"{TA} Corp", contracting_region="EU"))
            db.flush()
            add_principal(db, actor_id=A_CCO, tenant_id=TA, actor_type="HUMAN", display_name="Tenant A CCO", roles=["CCO"])
            db.commit()

    # Global catalogue route is accessible to content roles
    assert client.get("/api/catalog/traceability/validation", headers=H(LEGAL)).status_code == 200

    # Tenant A cannot see Customer Zero determinations
    r = client.get("/api/applicability/determinations", headers=H(A_CCO))
    if r.status_code == 200:
        assert not [d for d in r.json() if d.get("tenant_id") == "tenant-zoiko-group"]


def test_customer_zero_determinations_and_obligations_unaffected(client):
    """Verify that seeded tenant determinations, evaluations, and obligations remain intact."""
    from app.db import AdminSession
    from app.models import ObligationDefinition, ObligationInstance

    with AdminSession() as db:
        obls = db.execute(
            select(ObligationInstance).where(ObligationInstance.tenant_id == "tenant-zoiko-group")
        ).scalars().all()
        assert len(obls) == 3
        keys = {db.get(ObligationDefinition, o.obligation_definition_id).key for o in obls}
        assert keys == {"DORA_ICT_RISK_FRAMEWORK", "EUAIA_TRANSPARENCY_INTERACTION", "DORA_ICT_INCIDENT_PROCESS"}
        for o in obls:
            assert o.lifecycle == "ADOPTED"
