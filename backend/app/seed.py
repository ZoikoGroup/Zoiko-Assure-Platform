"""Customer Zero seed (Zoiko Group) — driven through the real engines, not raw inserts of derived state.

All regulatory text and encodings here are SYNTHETIC ENGINEERING FIXTURES that demonstrate object
relationships; they are not a legal interpretation of any instrument (ZA-DOM-001 §16).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import CELL_ID
from .engines import applicability as appl
from .engines import assurance as asr
from .engines import evidence as evd
from .engines import ipe as ipe_engine
from .engines import keys, rules
from .engines.facts import assert_fact
from .models import (Actor, AuthorityGrant, AIAssetVersion, Cell, Compensation, ConnectorDefinition,
                     ConnectorInstallation, ContentRelease, ControlDefinition, ControlImplementation, ControlTestPolicy,
                     ExceptionRecord, ExtractionContractVersion, FactDefinition, FrameworkMapping, Instrument,
                     IPEPolicyVersion, LegalEntity, MappingContractVersion, ObligationDefinition, ObligationOccurrence,
                     Provision, RegulatorySource, RequirementVersion, RuleDraft, RuleVersion, SetVersion, Subject, Tenant,
                     TestDefinition, TestRun, new_id)
from .platform import canonical_json, next_record_seq, sha256_hex


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class _Ctx:  # minimal ctx for engine attestation calls during seeding
    def __init__(self, db, actor):
        self.db, self.actor = db, actor

    @property
    def tenant(self):
        return self.actor.tenant_id

    def require_human(self, what):
        assert self.actor.actor_type == "HUMAN"

    def require(self, perm):
        pass

    def actor_ref(self):
        return {"actor_id": self.actor.actor_id, "actor_type": self.actor.actor_type, "name": self.actor.display_name}


def seed_if_empty(db: Session) -> None:
    if db.execute(select(Tenant)).scalars().first():
        return
    seed(db)


def seed(db: Session) -> None:
    # ---------------------------------------------------------------- cells, tenant, entities
    db.add_all([Cell(cell_id=CELL_ID, region="eu-west (UK/EU)", status="ACTIVE", isolation_tier="POOLED"),
                Cell(cell_id="uk-south-cell-b", region="uk-south", status="STANDBY", isolation_tier="POOLED")])
    t = Tenant(tenant_id="tenant-zoiko-group", legal_name="Zoiko Group (Customer Zero)", contracting_region="UK")
    db.add(t)
    db.flush()
    ents = {}
    for key, name, country, reg in [("uk", "Zoiko Tech UK Ltd", "GB", "Companies House 12345678"),
                                    ("eu", "Zoiko Europe B.V.", "NL", "KvK 87654321")]:
        e = LegalEntity(entity_id=f"entity-{key}", tenant_id=t.tenant_id, legal_name=name, country=country,
                        registration_ref=reg, home_cell_id=CELL_ID)
        db.add(e)
        db.flush()
        s = Subject(subject_id=f"subj-entity-{key}", tenant_id=t.tenant_id, entity_id=e.entity_id,
                    subject_type="LEGAL_ENTITY", display_name=name)
        db.add(s)
        e.subject_id = s.subject_id
        ents[key] = (e, s)
    subj = {}
    for sid, ent, stype, name in [
        ("subj-ai-payables", "uk", "AI_ASSET", "Payables Reconciler Agent"),
        ("subj-ai-copilot", "eu", "AI_ASSET", "Customer Support Copilot"),
        ("subj-ai-credit", "eu", "AI_ASSET", "Credit Decisioning Model"),
        ("subj-bu-payments", "eu", "BUSINESS_UNIT", "Payments Operations"),
        ("subj-act-payments", "eu", "REGULATED_ACTIVITY", "Payment services (PSD2 licence)"),
        ("subj-env-prod-eu", "eu", "ENVIRONMENT", "Production — EU"),
    ]:
        s = Subject(subject_id=sid, tenant_id=t.tenant_id, entity_id=ents[ent][0].entity_id, subject_type=stype,
                    display_name=name, parent_subject_id=ents[ent][1].subject_id)
        db.add(s)
        subj[sid] = s
    db.flush()

    # ---------------------------------------------------------------- actors
    A = {}
    for aid, tenant, atype, name, roles, basis in [
        ("actor-cco", t.tenant_id, "HUMAN", "Priya Shah (Chief Compliance Officer)", ["CCO"], "Board delegation CCO-2026-01"),
        ("actor-gc", t.tenant_id, "HUMAN", "Daniel Okafor (General Counsel)", ["LEGAL_REVIEWER"], "Articles of Association; GC mandate"),
        ("actor-analyst", t.tenant_id, "HUMAN", "Maya Chen (Regulatory Analyst)", ["REGULATORY_ANALYST"], None),
        ("actor-owner", t.tenant_id, "HUMAN", "Tom Becker (Control Owner — IAM)", ["CONTROL_OWNER"], None),
        ("actor-audit", t.tenant_id, "HUMAN", "Aisha Rahman (Internal Audit)", ["INTERNAL_AUDIT"], None),
        ("actor-aigov", t.tenant_id, "HUMAN", "Lena Novak (AI Governance Lead)", ["AI_GOVERNANCE_LEAD"], "AI policy owner"),
        ("actor-admin", t.tenant_id, "HUMAN", "Sam Patel (Platform Administrator)", ["PLATFORM_ADMIN"], None),
        ("actor-risk", t.tenant_id, "HUMAN", "Rui Costa (Chief Risk Officer)", ["RISK_APPROVER"], "Risk appetite statement 2026"),
        ("actor-svc", t.tenant_id, "SERVICE", "Assure Connector Service", ["SERVICE"], None),
        ("actor-agent", t.tenant_id, "AGENT", "Payables Reconciler Agent (deliberately over-privileged to prove INV-01)", ["CCO"], None),
        ("actor-ext-auditor", t.tenant_id, "EXTERNAL_AUDITOR", "External Auditor (engagement EA-2026-Q3)", ["EXTERNAL_AUDITOR"], None),
        ("actor-content-analyst", None, "HUMAN", "Elena Ruiz (Zoiko Content Analyst)", ["CONTENT_ANALYST"], None),
        ("actor-encoder", None, "HUMAN", "Marco Rossi (Zoiko Encoding Engineer)", ["ENCODING_ENGINEER"], None),
        ("actor-content-legal", None, "HUMAN", "Harriet Cole (Zoiko Qualified Legal Reviewer)", ["CONTENT_LEGAL_REVIEWER"], None),
        ("actor-release", None, "HUMAN", "Kofi Mensah (Zoiko Content Release Manager)", ["CONTENT_RELEASE_MANAGER"], None),
    ]:
        a = Actor(actor_id=aid, tenant_id=tenant, actor_type=atype, display_name=name, roles=roles, authority_basis=basis,
                  email=f"{aid}@zoiko.example")
        db.add(a)
        A[aid] = a
    db.flush()

    # ---------------------------------------------------------------- catalogue
    insts = {}
    for ident, title, juris, auth, cov in [
        ("EU-2024-1689", "EU Artificial Intelligence Act (Regulation (EU) 2024/1689) — as amended", "EU", "European Union", "ENCODED"),
        ("EU-2022-2554", "Digital Operational Resilience Act (Regulation (EU) 2022/2554)", "EU", "European Union", "ENCODED"),
        ("UK-GDPR", "UK General Data Protection Regulation", "GB", "UK Parliament / ICO", "MONITORING_ONLY"),
        ("EU-2022-2555", "NIS2 Directive (EU) 2022/2555", "EU", "European Union", "MONITORING_ONLY"),
        ("FCA-PRIN-2A", "FCA Consumer Duty (PRIN 2A)", "GB", "Financial Conduct Authority", "MONITORING_ONLY"),
    ]:
        i = Instrument(identifier=ident, title=title, jurisdiction=juris, authority=auth, coverage_mode=cov,
                       regime_owner="Zoiko Regulatory Content — " + juris)
        db.add(i)
        insts[ident] = i
    db.flush()

    def source(inst, title, locator, text, eff):
        s = RegulatorySource(authority=inst.authority, jurisdiction=inst.jurisdiction, source_type="PRIMARY_LEGISLATION",
                             title=title, locator=locator, fingerprint=sha256_hex(text), rights_class="OFFICIAL_REUSE_PERMITTED",
                             rights_record={"class": "OFFICIAL_REUSE_PERMITTED", "store": True, "display": True, "derive": True,
                                            "model_use": True, "note": "Synthetic paraphrase fixture"},
                             raw_text=text, publication_date=eff - timedelta(days=400), effective_date=eff,
                             lifecycle="PUBLISHED", record_seq=next_record_seq(db))
        db.add(s)
        db.flush()
        return s

    def req(inst, key, title, locator, src, vf, provisions):
        rv = RequirementVersion(requirement_key=key, instrument_id=inst.instrument_id, source_id=src.source_id, title=title,
                                locator=locator, summary=title, valid_from=vf, lifecycle="PUBLISHED", origin="ZOIKO_ENCODED",
                                reviewed_by="actor-content-legal", record_seq=next_record_seq(db))
        db.add(rv)
        db.flush()
        ids = []
        for loc, bearer, action, obj, timing, meaning, ev in provisions:
            p = Provision(requirement_version_id=rv.requirement_version_id, locator=loc, duty_bearer=bearer, modality="MUST",
                          action=action, regulated_object=obj, timing=timing, normalized_meaning=meaning,
                          evidence_expectation=ev, lifecycle="PUBLISHED")
            db.add(p)
            db.flush()
            ids.append(p.provision_id)
        return rv, ids

    s_aia = source(insts["EU-2024-1689"], "AI Act — deployer obligations & transparency (synthetic excerpt)", "OJ L 2024/1689, Art. 26 & Art. 50",
                   "Deployers of high-risk AI systems shall take appropriate technical and organisational measures to use such systems in "
                   "accordance with instructions for use. Deployers shall assign human oversight to natural persons who have the necessary "
                   "competence. Deployers shall monitor the operation of the high-risk AI system and keep automatically generated logs. "
                   "Providers shall ensure that AI systems intended to interact directly with natural persons are designed so that the "
                   "persons concerned are informed that they are interacting with an AI system.", date(2024, 8, 1))
    s_dora = source(insts["EU-2022-2554"], "DORA — ICT risk management framework (synthetic excerpt)", "OJ L 333/1, Art. 5-6 & Art. 17-19",
                    "Financial entities shall have a sound, comprehensive and well-documented ICT risk management framework. The ICT risk "
                    "management framework shall be documented and reviewed at least once a year. Financial entities shall define, establish "
                    "and implement an ICT-related incident management process to detect, manage and notify ICT-related incidents.", date(2025, 1, 17))
    _, p_hr = req(insts["EU-2024-1689"], "AIA-ART26", "Deployer obligations for high-risk AI systems", "Art. 26(1)-(5)", s_aia, date(2027, 12, 2), [
        ("Art. 26(2)", "DEPLOYER", "assign human oversight", "high-risk AI system", "continuous",
         "Assign human oversight to competent natural persons.", "Oversight assignment record; training evidence"),
        ("Art. 26(5)", "DEPLOYER", "monitor operation and keep logs", "high-risk AI system", "continuous",
         "Monitor operation; retain automatically generated logs.", "Log retention configuration; monitoring reports")])
    _, p_tr = req(insts["EU-2024-1689"], "AIA-ART50", "Transparency for AI systems interacting with natural persons", "Art. 50(1)", s_aia, date(2026, 8, 2), [
        ("Art. 50(1)", "PROVIDER_DEPLOYER", "inform persons of AI interaction", "AI system interacting with natural persons", "at first interaction",
         "Persons must be informed they are interacting with an AI system.", "UI disclosure evidence")])
    _, p_dora = req(insts["EU-2022-2554"], "DORA-ART6", "ICT risk management framework", "Art. 6(1),(5)", s_dora, date(2025, 1, 17), [
        ("Art. 6(1)", "FINANCIAL_ENTITY", "maintain documented ICT risk management framework", "ICT risk management framework", "continuous",
         "Maintain a sound, comprehensive, documented ICT risk management framework.", "Framework document; access & MFA controls evidence"),
        ("Art. 6(5)", "FINANCIAL_ENTITY", "review framework", "ICT risk management framework", "at least annually",
         "Review the framework at least once a year.", "Annual review record")])
    _, p_inc = req(insts["EU-2022-2554"], "DORA-ART17", "ICT-related incident management process", "Art. 17(1)", s_dora, date(2025, 1, 17), [
        ("Art. 17(1)", "FINANCIAL_ENTITY", "implement incident management process", "ICT-related incidents", "continuous",
         "Define, establish and implement an ICT incident management process.", "Process document; incident drill evidence")])

    F = {}
    for key, label, dtype, dom, stype, sens, prov, manual, expiry in [
        ("entity.established_in_eu", "Entity established in the EU", "BOOLEAN", None, "LEGAL_ENTITY", "CUSTOMER_INTERNAL", None, "ALLOWED", None),
        ("entity.financial_entity_type", "Financial entity type (DORA Art. 2)", "ENUM",
         ["CREDIT_INSTITUTION", "PAYMENT_INSTITUTION", "E_MONEY_INSTITUTION", "INVESTMENT_FIRM", "ICT_THIRD_PARTY", "NONE"],
         "LEGAL_ENTITY", "CUSTOMER_INTERNAL", None, "ALLOWED_WITH_REVIEW", None),
        ("entity.microenterprise", "Microenterprise status", "BOOLEAN", None, "LEGAL_ENTITY", "CUSTOMER_INTERNAL", None, "ALLOWED", 365),
        ("entity.employee_count", "Employee headcount", "INTEGER", None, "LEGAL_ENTITY", "CUSTOMER_CONFIDENTIAL", None, "ALLOWED", 365),
        ("entity.operating_countries", "Operating countries", "SET<COUNTRY>", None, "LEGAL_ENTITY", "CUSTOMER_INTERNAL", None, "ALLOWED", None),
        ("ai.role", "AI Act role of the entity for this system", "ENUM",
         ["PROVIDER", "DEPLOYER", "PROVIDER_DEPLOYER", "IMPORTER", "DISTRIBUTOR"], "AI_ASSET", "CUSTOMER_INTERNAL", None, "ALLOWED", None),
        ("ai.high_risk_basis", "High-risk classification basis", "ENUM", ["ANNEX_I", "ANNEX_III", "NONE"], "AI_ASSET",
         "CUSTOMER_INTERNAL", None, "ALLOWED_WITH_REVIEW", None),
        ("ai.annex_iii_category", "Annex III area", "ENUM",
         ["BIOMETRICS", "CRITICAL_INFRASTRUCTURE", "EDUCATION", "EMPLOYMENT", "ESSENTIAL_SERVICES", "LAW_ENFORCEMENT", "MIGRATION", "JUSTICE"],
         "AI_ASSET", "CUSTOMER_INTERNAL", None, "ALLOWED_WITH_REVIEW", None),
        ("ai.interacts_with_natural_persons", "Interacts directly with natural persons", "BOOLEAN", None, "AI_ASSET", "CUSTOMER_INTERNAL", None, "ALLOWED", None),
        ("ai.placed_on_eu_market", "Placed on market / put into service in the EU", "BOOLEAN", None, "AI_ASSET", "CUSTOMER_INTERNAL", None, "ALLOWED", None),
    ]:
        f = FactDefinition(key=key, label=label, data_type=dtype, enum_domain=dom, subject_type=stype, sensitivity=sens,
                           manual_attestation_policy="ALLOWED" if manual == "ALLOWED_WITH_REVIEW" else manual, expiry_days=expiry)
        db.add(f)
        F[key] = f
    db.flush()

    for sid, key, etype, members, note in [
        ("set_dora_financial_entities@1", "dora_financial_entities", "ENUM",
         ["CREDIT_INSTITUTION", "PAYMENT_INSTITUTION", "E_MONEY_INSTITUTION", "INVESTMENT_FIRM"], "DORA Art. 2(1) (subset, synthetic)"),
        ("set_eu_member_states@1", "eu_member_states", "COUNTRY",
         ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"],
         "EU-27"),
        ("set_deployer_roles@1", "deployer_roles", "ENUM", ["DEPLOYER", "PROVIDER_DEPLOYER"], "AI Act deployer roles"),
    ]:
        db.add(SetVersion(set_version_id=sid, set_key=key, element_type=etype, members=sorted(members),
                          canonical_hash=rules.set_hash(etype, members), valid_from=date(2024, 1, 1), source_note=note))
    db.flush()

    def odef(key, title, inst, provs, stype, template, cov="ENCODED", schedule="CONTINUOUS", material=True):
        o = ObligationDefinition(key=key, title=title, instrument_id=insts[inst].instrument_id, provision_ids=provs,
                                 subject_type=stype, coverage_mode=cov, schedule_kind=schedule, satisfaction_template=template,
                                 material=material)
        db.add(o)
        db.flush()
        return o

    C = lambda nid, k: {"node_type": "CONTROL", "node_id": nid, "control_key": k}
    o_hr = odef("EUAIA_DEPLOYER_HR_OVERSIGHT", "High-risk AI deployer — human oversight & monitoring", "EU-2024-1689", p_hr, "AI_ASSET",
                {"node_type": "ALL", "node_id": "root", "children": [C("n_oversight", "C_HUMAN_OVERSIGHT"), C("n_logs", "C_AI_MONITORING_LOGS")]})
    o_tr = odef("EUAIA_TRANSPARENCY_INTERACTION", "AI interaction transparency notice", "EU-2024-1689", p_tr, "AI_ASSET",
                {"node_type": "ALL", "node_id": "root", "children": [C("n_notice", "C_AI_TRANSPARENCY_NOTICE")]})
    o_dora = odef("DORA_ICT_RISK_FRAMEWORK", "DORA ICT risk management framework (incl. annual review)", "EU-2022-2554", p_dora, "LEGAL_ENTITY",
                  {"node_type": "ALL", "node_id": "root", "children": [
                      C("n_framework", "C_ICT_RISK_FRAMEWORK"), C("n_access_review", "C_ACCESS_REVIEW"),
                      C("n_mfa", "C_MFA_PHISHING_RESISTANT"), {"node_type": "OCCURRENCES", "node_id": "n_annual_review"}]},
                  schedule="RECURRING")
    o_inc = odef("DORA_ICT_INCIDENT_PROCESS", "DORA ICT-related incident management process", "EU-2022-2554", p_inc, "LEGAL_ENTITY",
                 {"node_type": "ALL", "node_id": "root", "children": [C("n_inc", "C_ICT_INCIDENT_PROCESS")]})
    odef("UKGDPR_ROPA", "UK GDPR records of processing (monitoring only)", "UK-GDPR", [], "LEGAL_ENTITY", None, cov="MONITORING_ONLY")

    for k, title, obj, cat, maps in [
        ("C_MFA_PHISHING_RESISTANT", "Phishing-resistant MFA for all workforce identities", "All workforce accounts use phishing-resistant MFA.", "IAM",
         [("ISO/IEC 27001:2022", "A.8.5"), ("NIST CSF 2.0", "PR.AA-03")]),
        ("C_MFA_STANDARD", "Standard MFA", "All workforce accounts use MFA.", "IAM", [("ISO/IEC 27001:2022", "A.8.5")]),
        ("C_ACCESS_REVIEW", "Periodic privileged access review", "Privileged access is reviewed monthly.", "IAM",
         [("ISO/IEC 27001:2022", "A.5.18"), ("SOC 2", "CC6.2")]),
        ("C_DEVICE_ATTESTATION", "Managed-device attestation", "Only attested managed devices reach corporate apps.", "Endpoint", []),
        ("C_PRIVILEGED_NETWORK_RESTRICTION", "Privileged network restriction", "Privileged sessions only from restricted networks.", "Network", []),
        ("C_ICT_RISK_FRAMEWORK", "Documented ICT risk management framework", "A documented, approved ICT risk framework is in force.", "Governance",
         [("DORA", "Art. 6")]),
        ("C_ICT_INCIDENT_PROCESS", "ICT incident management process", "Incident process defined and exercised.", "Operations", [("DORA", "Art. 17")]),
        ("C_HUMAN_OVERSIGHT", "AI human oversight assignment", "Competent natural persons assigned to oversee the AI system.", "AI", [("ISO/IEC 42001", "A.9")]),
        ("C_AI_MONITORING_LOGS", "AI operation monitoring & log retention", "AI operation monitored; logs retained.", "AI", [("ISO/IEC 42001", "A.6.2.8")]),
        ("C_AI_TRANSPARENCY_NOTICE", "AI interaction disclosure", "Users are informed they are interacting with AI.", "AI", []),
    ]:
        cd = ControlDefinition(key=k, title=title, objective=obj, category=cat)
        db.add(cd)
        db.flush()
        for fw, ref in maps:
            db.add(FrameworkMapping(control_definition_id=cd.control_definition_id, framework=fw, reference=ref,
                                    rights_note="Reference only; no protected clause text stored"))
    db.flush()

    # ---------------------------------------------------------------- rule releases via the real pipeline
    def lit(nid, t, v):
        return {"node_type": "CONST", "node_id": nid, "type": t, "value": v}

    def fact(nid, k):
        return {"node_type": "FACT_REF", "node_id": nid, "fact": k}

    def pred(nid, op, args, sm):
        return {"node_type": "PREDICATE", "node_id": nid, "operator": op, "args": args, "source_mappings": sm}

    ast_hr = {"node_type": "ALL", "node_id": "n_root", "source_mappings": ["srcmap_aia_26"], "children": [
        pred("n1", "EQ", [fact("n1f", "entity.established_in_eu"), lit("n1c", "BOOLEAN", True)], ["srcmap_aia_2_scope"]),
        pred("n2", "IN", [fact("n2f", "ai.role"), {"node_type": "SET_REF", "node_id": "n2s", "set_version_id": "set_deployer_roles@1"}], ["srcmap_aia_3_4"]),
        pred("n3", "EQ", [fact("n3f", "ai.high_risk_basis"), lit("n3c", "ENUM", "ANNEX_III")], ["srcmap_aia_6_2"])]}
    vec_hr = [
        {"vector_id": "HR-POS-1", "vector_class": "POSITIVE", "description": "EU deployer of Annex III system", "effective_at": "2027-12-15",
         "facts": {"entity.established_in_eu": True, "ai.role": "DEPLOYER", "ai.high_risk_basis": "ANNEX_III"}, "expected_outcome": "APPLIES"},
        {"vector_id": "HR-NEG-1", "vector_class": "NEGATIVE", "description": "Non-high-risk system", "effective_at": "2027-12-15",
         "facts": {"entity.established_in_eu": True, "ai.role": "DEPLOYER", "ai.high_risk_basis": "NONE"}, "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "HR-NEG-2", "vector_class": "NEGATIVE", "description": "Provider-only role", "effective_at": "2027-12-15",
         "facts": {"entity.established_in_eu": True, "ai.role": "PROVIDER", "ai.high_risk_basis": "ANNEX_III"}, "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "HR-UNK-1", "vector_class": "UNKNOWN", "description": "High-risk basis unknown is material", "effective_at": "2027-12-15",
         "facts": {"entity.established_in_eu": True, "ai.role": "DEPLOYER", "ai.high_risk_basis": {"unknown": "MISSING_REQUIRED_FACT"}},
         "expected_outcome": "UNCERTAIN", "trace_assertions": {"n3": "UNKNOWN"}},
        {"vector_id": "HR-UNK-2", "vector_class": "UNKNOWN", "description": "Unknown is non-material when another branch is FALSE", "effective_at": "2027-12-15",
         "facts": {"entity.established_in_eu": False, "ai.role": {"unknown": "MISSING_REQUIRED_FACT"}, "ai.high_risk_basis": "ANNEX_III"},
         "expected_outcome": "DOES_NOT_APPLY"},
    ]
    ast_tr = {"node_type": "ALL", "node_id": "n_root", "source_mappings": ["srcmap_aia_50"], "children": [
        pred("t1", "EQ", [fact("t1f", "ai.interacts_with_natural_persons"), lit("t1c", "BOOLEAN", True)], ["srcmap_aia_50_1"]),
        pred("t2", "EQ", [fact("t2f", "ai.placed_on_eu_market"), lit("t2c", "BOOLEAN", True)], ["srcmap_aia_2_1"])]}
    vec_tr = [
        {"vector_id": "TR-POS-1", "vector_class": "POSITIVE", "effective_at": "2026-09-01",
         "facts": {"ai.interacts_with_natural_persons": True, "ai.placed_on_eu_market": True}, "expected_outcome": "APPLIES"},
        {"vector_id": "TR-NEG-1", "vector_class": "NEGATIVE", "effective_at": "2026-09-01",
         "facts": {"ai.interacts_with_natural_persons": False, "ai.placed_on_eu_market": True}, "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "TR-UNK-1", "vector_class": "UNKNOWN", "effective_at": "2026-09-01",
         "facts": {"ai.interacts_with_natural_persons": True, "ai.placed_on_eu_market": {"unknown": "MISSING_REQUIRED_FACT"}}, "expected_outcome": "UNCERTAIN"},
    ]
    dt_dora = {"decision_table_id": "dt_dora_scope", "table_mode": "ANY_MATCH", "source_mappings": ["srcmap_dora_2"],
               "columns": [{"column_id": "est", "fact": "entity.established_in_eu", "operator": "EQ", "value_type": "BOOLEAN"},
                           {"column_id": "type", "fact": "entity.financial_entity_type", "operator": "IN"},
                           {"column_id": "micro", "fact": "entity.microenterprise", "operator": "EQ", "value_type": "BOOLEAN"}],
               "rows": [{"row_id": "R1", "cells": {"est": True, "type": {"set_version_id": "set_dora_financial_entities@1"}, "micro": False},
                         "source_mappings": ["srcmap_dora_6_1"]},
                        {"row_id": "R2", "cells": {"est": True, "type": {"set_version_id": "set_dora_financial_entities@1"}, "micro": True},
                         "source_mappings": ["srcmap_dora_16"]}]}
    vec_dora = [
        {"vector_id": "D-POS-1", "vector_class": "POSITIVE", "effective_at": "2026-07-01",
         "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": "PAYMENT_INSTITUTION", "entity.microenterprise": False},
         "expected_outcome": "APPLIES", "trace_assertions": {"row_R1": "TRUE"}},
        {"vector_id": "D-NEG-1", "vector_class": "NEGATIVE", "effective_at": "2026-07-01",
         "facts": {"entity.established_in_eu": False, "entity.financial_entity_type": "PAYMENT_INSTITUTION", "entity.microenterprise": False},
         "expected_outcome": "DOES_NOT_APPLY"},
        {"vector_id": "D-UNK-1", "vector_class": "UNKNOWN", "effective_at": "2026-07-01",
         "facts": {"entity.established_in_eu": True, "entity.financial_entity_type": {"unknown": "FACT_CONFLICT"}, "entity.microenterprise": False},
         "expected_outcome": "UNCERTAIN"},
    ]
    ast_inc = {"node_type": "ALL", "node_id": "i_root", "source_mappings": ["srcmap_dora_17"], "children": [
        pred("i1", "EQ", [fact("i1f", "entity.established_in_eu"), lit("i1c", "BOOLEAN", True)], []),
        pred("i2", "IN", [fact("i2f", "entity.financial_entity_type"),
                          {"node_type": "SET_REF", "node_id": "i2s", "set_version_id": "set_dora_financial_entities@1"}], [])]}
    vec_inc = [dict(v, trace_assertions={}) for v in vec_dora]

    fdefs = appl.load_fact_defs(db)
    sets = appl.load_sets(db)

    def publish(o, mode, ast_or_table, vectors, vf, label, sm):
        d = RuleDraft(obligation_definition_id=o.obligation_definition_id, authoring_mode=mode,
                      ast=ast_or_table if mode == "AST" else None, decision_table=ast_or_table if mode == "DECISION_TABLE" else None,
                      source_mappings=sm, golden_vectors=vectors, valid_from=vf, status="VERIFIED", change_class="EXECUTABLE_OUTCOME",
                      author_id="actor-encoder", peer_reviewer_id="actor-content-analyst", legal_reviewer_id="actor-content-legal")
        db.add(d)
        db.flush()
        ast = rules.compile_decision_table(ast_or_table)[0] if mode == "DECISION_TABLE" else ast_or_table
        rep = rules.validate(ast, fdefs, sets)
        assert rep["ok"], (label, rep["issues"])
        vr = rules.run_golden_vectors(ast, vectors, fdefs, sets)
        assert vr["all_passed"], (label, vr)
        canon = rules.canonicalize(ast)
        sh = {s: sets[s]["hash"] for s in rep["set_refs"]}
        seq = next_record_seq(db)
        cr_id = new_id()  # both rows written once with final values: published catalogue is immutable (migration 0004)
        rv = RuleVersion(obligation_definition_id=o.obligation_definition_id, version=1, valid_from=vf, coverage_mode="ENCODED",
                         rule_schema_version=rules.SCHEMA_VERSION, operator_set_version=rules.OPERATOR_SET_VERSION,
                         canonical_ast=canon, ast_hash=rules.ast_hash(canon, sh), required_facts=rep["required_facts"],
                         set_refs=[{"set_version_id": k, "sha256": v} for k, v in sh.items()], source_mappings=sm,
                         golden_vectors=vectors, status="PUBLISHED", content_release_id=cr_id, draft_id=d.draft_id, record_seq=seq)
        db.add(rv)
        db.flush()
        manifest = json.loads(canonical_json({"label": label, "rule_versions": [{"rule_version_id": rv.rule_version_id,
                                                                                 "ast_sha256": rv.ast_hash, "valid_from": vf}],
                                              "set_refs": rv.set_refs, "golden_vector_result": {"total": vr["total"], "passed": vr["passed"]},
                                              "reviewers": {"author": "actor-encoder", "peer": "actor-content-analyst",
                                                            "legal": "actor-content-legal"}, "change_class": "EXECUTABLE_OUTCOME"}))
        mb = canonical_json(manifest)
        cr = ContentRelease(content_release_id=cr_id, label=label, manifest=manifest, manifest_hash=sha256_hex(mb),
                            signature=keys.sign(mb),
                            signer_key_id=keys.signer_key_id(), changelog=f"Initial encoded release: {o.title}",
                            shadow_summary={"subjects_evaluated": 0, "note": "No prior customer state"},
                            released_by="actor-release", cell_received_seq=seq)
        db.add(cr)
        db.flush()
        d.status = "PUBLISHED"
        return rv

    sm_hr = [{"source_mapping_id": "srcmap_aia_26", "provision_locator": "Art. 26", "mapping_type": "DIRECT_TEXT",
              "interpretation_rationale": "Deployer duties apply to high-risk systems in use under the deployer's authority.", "reviewer": "actor-content-legal"},
             {"source_mapping_id": "srcmap_aia_6_2", "provision_locator": "Art. 6(2) + Annex III", "mapping_type": "CROSS_REFERENCE",
              "interpretation_rationale": "High-risk classification via Annex III.", "reviewer": "actor-content-legal"}]
    publish(o_hr, "AST", ast_hr, vec_hr, date(2027, 12, 2), "CR-2026-AIA-001", sm_hr)
    publish(o_tr, "AST", ast_tr, vec_tr, date(2026, 8, 2), "CR-2026-AIA-002",
            [{"source_mapping_id": "srcmap_aia_50_1", "provision_locator": "Art. 50(1)", "mapping_type": "DIRECT_TEXT"}])
    publish(o_dora, "DECISION_TABLE", dt_dora, vec_dora, date(2025, 1, 17), "CR-2026-DORA-001",
            [{"source_mapping_id": "srcmap_dora_6_1", "provision_locator": "Art. 6(1)", "mapping_type": "DIRECT_TEXT"}])
    publish(o_inc, "AST", ast_inc, vec_inc, date(2025, 1, 17), "CR-2026-DORA-002",
            [{"source_mapping_id": "srcmap_dora_17", "provision_locator": "Art. 17(1)", "mapping_type": "DIRECT_TEXT"}])
    # a draft awaiting the content team (WF-RUL-01 walkthrough)
    db.add(RuleDraft(obligation_definition_id=o_tr.obligation_definition_id, authoring_mode="AST",
                     ast={**ast_tr, "children": ast_tr["children"] + [pred("t3", "NEQ", [fact("t3f", "ai.role"), lit("t3c", "ENUM", "DISTRIBUTOR")], ["srcmap_aia_50_scope"])]},
                     source_mappings=[{"source_mapping_id": "srcmap_aia_50_scope", "provision_locator": "Art. 50(1)-(2)", "mapping_type": "INTERPRETIVE",
                                       "interpretation_rationale": "Proposed: exclude pure distributors (draft for legal review)."}],
                     golden_vectors=vec_tr, valid_from=date(2026, 8, 2), status="DRAFT", change_class="EXECUTABLE_OUTCOME",
                     author_id="actor-content-analyst"))
    db.flush()

    # ---------------------------------------------------------------- facts (bitemporal), through the fact store
    def fct(sid, key, val, vf="2025-01-01", prov="P3_CUSTOMER_ATTESTED", actor="actor-analyst", src=None):
        s = db.get(Subject, sid)
        assert_fact(db, tenant_id=t.tenant_id, subject=s, fd=F[key], value=val, valid_from=dt(vf), valid_to=None,
                    provenance_class=prov, source_ref=src or "Customer attestation (onboarding questionnaire Q-2026-07)", actor_id=actor)

    fct("subj-entity-uk", "entity.established_in_eu", False)
    fct("subj-entity-uk", "entity.financial_entity_type", "PAYMENT_INSTITUTION")
    fct("subj-entity-uk", "entity.microenterprise", False)
    fct("subj-entity-uk", "entity.operating_countries", ["GB"])
    fct("subj-entity-eu", "entity.established_in_eu", True, prov="P1_SYSTEM_DIRECT", src="KvK registry API (certified connector)")
    fct("subj-entity-eu", "entity.financial_entity_type", "PAYMENT_INSTITUTION", prov="P1_SYSTEM_DIRECT", src="DNB licence register")
    fct("subj-entity-eu", "entity.microenterprise", False)
    fct("subj-entity-eu", "entity.employee_count", 240)
    fct("subj-entity-eu", "entity.operating_countries", ["NL", "DE", "FR"])
    for sid, role, basis, inter, mkt in [("subj-ai-payables", "DEPLOYER", "NONE", False, False),
                                        ("subj-ai-copilot", "DEPLOYER", None, True, True),
                                        ("subj-ai-credit", "DEPLOYER", "ANNEX_III", False, True)]:
        fct(sid, "ai.role", role, actor="actor-aigov")
        if basis:
            fct(sid, "ai.high_risk_basis", basis, actor="actor-aigov")
        fct(sid, "ai.interacts_with_natural_persons", inter, actor="actor-aigov")
        fct(sid, "ai.placed_on_eu_market", mkt, actor="actor-aigov")
    fct("subj-ai-credit", "ai.annex_iii_category", "ESSENTIAL_SERVICES", actor="actor-aigov")
    # an AI-proposed (P5) fact: recorded but never admissible for binding applicability
    fct("subj-ai-copilot", "ai.high_risk_basis", "NONE", prov="P5_INFERRED_AI", actor="actor-agent",
        src="Model classification proposal (non-binding)")

    for sid, kind, ver, purpose, prov_name, model, env, agbom in [
        ("subj-ai-payables", "AGENT", "1.4.0", "Reconcile supplier invoices and prepare VAT filing drafts", "Anthropic", "claude-sonnet-5-5", "prod-uk",
         [{"type": "MODEL", "ref": "claude-sonnet-5-5"}, {"type": "TOOL", "ref": "erp.invoices.read"}, {"type": "MCP_SERVER", "ref": "mcp://finance-ledger"}]),
        ("subj-ai-copilot", "COPILOT", "2.1.0", "Answer customer support questions", "Anthropic", "claude-haiku-4-5", "prod-eu",
         [{"type": "MODEL", "ref": "claude-haiku-4-5"}, {"type": "KNOWLEDGE_SOURCE", "ref": "kb://support-articles"}]),
        ("subj-ai-credit", "MODEL", "0.9.3", "Score creditworthiness for payment-account overdrafts", "In-house", "gbm-credit-v9", "prod-eu",
         [{"type": "MODEL", "ref": "gbm-credit-v9"}, {"type": "KNOWLEDGE_SOURCE", "ref": "dwh://bureau-features"}]),
    ]:
        db.add(AIAssetVersion(tenant_id=t.tenant_id, asset_subject_id=sid, asset_kind=kind, version_label=ver, purpose=purpose,
                              provider=prov_name, model=model, environment=env, owner_actor_id="actor-aigov", agbom=agbom))
    db.add(AuthorityGrant(tenant_id=t.tenant_id, agent_subject_id="subj-ai-payables", action_class="Prepare regulatory filing draft",
                          authority_level="A2", scope="UK legal entity; VAT filings only", reversibility="Reversible before submission",
                          expires_at=datetime.now(timezone.utc) + timedelta(days=90), granted_by="actor-cco"))
    db.flush()

    # ---------------------------------------------------------------- applicability evaluations + adoption
    def ev(sid, o, when, actor="actor-analyst"):
        return appl.evaluate(db, tenant_id=t.tenant_id, subject=db.get(Subject, sid), odef=o, effective_at=dt(when),
                             record_cutoff_seq=None, trigger="ONBOARDING", actor_id=actor)

    e_dora_eu = ev("subj-entity-eu", o_dora, "2026-07-01")
    ev("subj-entity-uk", o_dora, "2026-07-01")
    ev("subj-entity-eu", o_inc, "2026-07-01")
    e_tr = ev("subj-ai-copilot", o_tr, "2026-08-02")
    ev("subj-ai-payables", o_tr, "2026-08-02")
    ev("subj-ai-credit", o_hr, "2027-12-15")   # forecast: future-effective content
    ev("subj-ai-copilot", o_hr, "2027-12-15")  # UNCERTAIN: high-risk basis only has a P5 AI proposal
    gc = _Ctx(db, A["actor-gc"])
    dora_obl = appl.attest(db, ctx=gc, determination_lineage_id=appl.current_determination(db, t.tenant_id, "subj-entity-eu",
                           o_dora.obligation_definition_id).lineage_id, decision="ADOPT",
                           authority_basis="General Counsel mandate; DORA scoping memo 2026-06")["obligation"]
    tr_obl = appl.attest(db, ctx=_Ctx(db, A["actor-cco"]), determination_lineage_id=appl.current_determination(
        db, t.tenant_id, "subj-ai-copilot", o_tr.obligation_definition_id).lineage_id, decision="ADOPT",
        authority_basis="Board delegation CCO-2026-01")["obligation"]

    # ---------------------------------------------------------------- connectors + IPE
    full_matrix = {k: "PASS" for k in ("auth_scope", "tenant_isolation", "pagination_snapshot", "population_proof", "schema_mapping",
                                        "transforms", "retries_idempotency", "ipe_anomalies", "version_compatibility")}
    entra = ConnectorDefinition(key="entra-id", name="Microsoft Entra ID", provider="Microsoft", source_class="IDENTITY",
                                build_version="2.4.1", release_availability="PRIVATE_BETA", certification="CERTIFIED",
                                supported_objects=["users", "authenticationMethods"], certification_matrix=full_matrix,
                                known_limitations=["Sign-in activity lags up to 24h"])
    vanta = ConnectorDefinition(key="vanta", name="Vanta (control test results)", provider="Vanta", source_class="EXTERNAL_GRC",
                                build_version="1.2.0", release_availability="PRIVATE_BETA", certification="CERTIFIED",
                                supported_objects=["tests"], certification_matrix=full_matrix,
                                known_limitations=["Vendor PASS is an input; population not independently enumerable"])
    aws = ConnectorDefinition(key="aws-config", name="AWS Config", provider="AWS", source_class="CLOUD_POSTURE", build_version="0.8.0",
                              release_availability="PRIVATE_BETA", certification="UNASSESSED", supported_objects=["configRules"],
                              certification_matrix={"auth_scope": "PASS", "pagination_snapshot": "FAIL"})
    snow = ConnectorDefinition(key="servicenow-irm", name="ServiceNow IRM", provider="ServiceNow", source_class="ISSUE_SYSTEM",
                               build_version="0.1.0", release_availability="PLANNED", certification="UNASSESSED", runtime_health="DISABLED")
    db.add_all([entra, vanta, aws, snow])
    db.flush()
    src_fields = ["accountEnabled", "etag", "id", "lastSignIn", "mfaState", "userPrincipalName"]
    db.add(ExtractionContractVersion(connector_id=entra.connector_id, version=1, source_object="workforce_user",
                                     endpoint_id="graph:/v1.0/users?$select=id,userPrincipalName,accountEnabled", query_template="GET /users {scope}",
                                     record_identity="id", required_scopes=["User.Read.All", "UserAuthenticationMethod.Read.All"],
                                     ordering="id ASC", consistency_strategy="SNAPSHOT_TOKEN", pagination_method="@odata.nextLink; terminal when absent",
                                     population_proof_methods=["SOURCE_TOTAL_RECONCILIATION", "FULL_ENUMERATION_TERMINAL"],
                                     known_limitations=["$count requires ConsistencyLevel: eventual"]))
    db.add(MappingContractVersion(connector_id=entra.connector_id, version=1, source_schema_fingerprint=ipe_engine.schema_fingerprint(src_fields),
                                  source_fields=src_fields, relied_upon_fields=["user_id", "mfa_enabled"], field_mappings=[
                                      {"source": "id", "target": "user_id", "type": "STRING"},
                                      {"source": "userPrincipalName", "target": "upn", "type": "STRING"},
                                      {"source": "mfaState", "target": "mfa_enabled", "type": "BOOLEAN", "transform": "ENUM_MAP",
                                       "enum_map": {"Enforced": True, "Enabled": True, "Disabled": False}},
                                      {"source": "accountEnabled", "target": "account_enabled", "type": "BOOLEAN"},
                                      {"source": "lastSignIn", "target": "last_sign_in", "type": "STRING", "transform": "TIME_UTC", "nullable": True}]))
    pol_full = IPEPolicyVersion(name="Workforce MFA population — full IPE", purpose="Prove all workforce identities use phishing-resistant MFA",
                                required_completeness_checks=["SCOPE_AUTH", "CONSISTENCY", "ENUMERATION", "SOURCE_TOTAL_RECONCILIATION",
                                                              "DUPLICATES", "REJECTED_RECORDS", "ZERO_ROWS"],
                                required_accuracy_checks=["SCHEMA_DRIFT", "FIELD_MAPPING", "TRANSFORM_REPERFORMANCE", "SOURCE_CHECKSUM"])
    pol_acc = IPEPolicyVersion(name="Accuracy only (analytic use)", purpose="Non-material analytics", completeness_required=False,
                               required_completeness_checks=[], required_accuracy_checks=["FIELD_MAPPING", "TRANSFORM_REPERFORMANCE"])
    db.add_all([pol_full, pol_acc])
    inst = ConnectorInstallation(tenant_id=t.tenant_id, entity_id="entity-eu", connector_id=entra.connector_id,
                                 granted_scopes=["User.Read.All", "UserAuthenticationMethod.Read.All"],
                                 credential_ref="vault://cell-a/tenant-zoiko-group/entra-id/client-credential", schedule_days=7)
    vinst = ConnectorInstallation(tenant_id=t.tenant_id, entity_id="entity-eu", connector_id=vanta.connector_id,
                                  granted_scopes=["tests:read"], credential_ref="vault://cell-a/tenant-zoiko-group/vanta/api-token", schedule_days=1)
    db.add_all([inst, vinst])
    db.flush()

    # ---------------------------------------------------------------- control implementations (EU entity, Q3 2026)
    eu = "subj-entity-eu"

    def impl(key, sid, method, automation, tests, agg="ALL_REQUIRED"):
        cd = db.execute(select(ControlDefinition).where(ControlDefinition.key == key)).scalars().first()
        seq = next_record_seq(db)
        ci = ControlImplementation(tenant_id=t.tenant_id, entity_id=db.get(Subject, sid).entity_id, subject_id=sid,
                                   control_definition_id=cd.control_definition_id, owner_actor_id="actor-owner", method=method,
                                   valid_from=dt("2026-06-01"), record_seq=seq)
        db.add(ci)
        db.flush()
        p = ControlTestPolicy(tenant_id=t.tenant_id, control_implementation_id=ci.control_implementation_id,
                              aggregation_mode=agg, automation_requirement=automation, record_seq=seq)
        db.add(p)
        db.flush()
        tds = []
        for td in tests:
            x = TestDefinition(tenant_id=t.tenant_id, policy_version_id=p.policy_version_id, **td)
            db.add(x)
            db.flush()
            tds.append(x)
        return ci, tds

    _, (td_fw,) = impl("C_ICT_RISK_FRAMEWORK", eu, "Board-approved ICT risk framework v3 (document control)", "NONE",
                       [{"name": "Framework approved & in force (document review)", "source_class": "MANUAL", "cadence_days": 365}])
    _, (td_ar,) = impl("C_ACCESS_REVIEW", eu, "Monthly privileged access review via Vanta workflow", "PREFERRED",
                       [{"name": "Vanta: privileged access reviewed", "source_class": "EXTERNAL_GRC", "cadence_days": 31}])
    _, (td_mfa,) = impl("C_MFA_PHISHING_RESISTANT", eu, "Entra ID conditional access requiring FIDO2/WHfB", "REQUIRED",
                        [{"name": "All workforce users have MFA enforced (Entra ID population)", "source_class": "CONNECTOR",
                          "cadence_days": 7, "ipe_required": True, "primary": True,
                          "criterion": {"quantifier": "ALL", "field": "mfa_enabled", "op": "EQ", "value": True}}])
    _, (td_dev,) = impl("C_DEVICE_ATTESTATION", eu, "Intune compliant-device conditional access", "PREFERRED",
                        [{"name": "Device attestation enforced", "source_class": "NATIVE", "cadence_days": 30}])
    _, (td_net,) = impl("C_PRIVILEGED_NETWORK_RESTRICTION", eu, "PAW + named-location restriction for admins", "PREFERRED",
                        [{"name": "Privileged sessions restricted", "source_class": "NATIVE", "cadence_days": 30}])
    _, (td_inc,) = impl("C_ICT_INCIDENT_PROCESS", eu, "Incident runbook + quarterly drill", "NONE",
                        [{"name": "Incident drill executed", "source_class": "MANUAL", "cadence_days": 92}])
    _, (td_tn,) = impl("C_AI_TRANSPARENCY_NOTICE", "subj-ai-copilot", "Chat widget banner + first-message disclosure", "PREFERRED",
                       [{"name": "Disclosure present in production UI (synthetic monitor)", "source_class": "NATIVE", "cadence_days": 7}])

    def capture(title, origin, prov, cov_from, cov_to, valid_days, body, actor="actor-owner", method="FILE_UPLOAD", src=None):
        return evd.capture_evidence(db, tenant_id=t.tenant_id, entity_id="entity-eu", content=body.encode(), title=title,
                                    content_type="text/plain", origin_class=origin, acquisition_method=method, provenance_class=prov,
                                    coverage_from=dt(cov_from), coverage_to=dt(cov_to), valid_until=dt(cov_to) + timedelta(days=valid_days),
                                    actor_id=actor, source_system=src)

    def run(td, frm, to, result, evid, source, ext=None, detail=None):
        db.add(TestRun(tenant_id=t.tenant_id, test_definition_id=td.test_definition_id, occurred_at=dt(to) - timedelta(hours=1),
                       window_from=dt(frm), window_to=dt(to), result=result, source_system=source, external_status=ext,
                       evidence_ids=[e.evidence_id for e in evid], detail=detail, record_seq=next_record_seq(db)))

    fw_doc = capture("ICT Risk Management Framework v3 — board approval minute", "HUMAN_UPLOAD", "P4_MANUAL_UNATTESTED",
                     "2026-06-01", "2027-06-01", 30, "Board minute BM-2026-05: ICT risk framework v3 approved. (synthetic)")
    run(td_fw, "2026-06-01", "2027-01-01", "PASS", [fw_doc], "manual-review:actor-audit", detail="Reviewed by Internal Audit")
    for m_from, m_to in (("2026-07-01", "2026-08-01"), ("2026-08-01", "2026-09-01")):
        e = capture(f"Vanta access review export {m_from[:7]}", "EXTERNAL_IMPORT", "P2_SYSTEM_DERIVED", m_from, m_to, 45,
                    json.dumps({"test": "privileged-access-review", "status": "PASSING", "period": m_from}), actor="actor-svc",
                    method="API", src="vanta")
        run(td_ar, m_from, m_to, "PASS", [e], "vanta", ext="PASSING")
    # September: no access review result -> COVERAGE GAP (NOT_PROVEN segment)

    def users(n, mfa_bad=0):
        return [{"id": f"u{i:03d}", "userPrincipalName": f"user{i}@zoiko.example", "mfaState": "Disabled" if i < mfa_bad else "Enforced",
                 "accountEnabled": True, "lastSignIn": "2026-07-30T08:00:00Z", "etag": f"W/\"{i}\""} for i in range(n)]
    recs1 = users(40)
    ipe_engine.execute_source_run(db, tenant_id=t.tenant_id, installation=inst, actor_id="actor-svc", ipe_policy=pol_full,
                                  test_definition_id=td_mfa.test_definition_id, payload={
                                      "coverage_from": "2026-07-01T00:00:00Z", "coverage_to": "2026-08-15T00:00:00Z",
                                      "parameters": {"scope": {"tenant": "zoiko-eu.onmicrosoft.com", "accountEnabled": True}},
                                      "consistency_boundary": {"snapshot_token": "snap-2026-08-15T00:00Z"},
                                      "source_reported_total": 40, "terminal_reached": True,
                                      "pages": [{"page": 1, "cursor": "c1", "records": recs1[:20]}, {"page": 2, "cursor": "c2", "records": recs1[20:]}],
                                      "source_checksum": sha256_hex(canonical_json(recs1)), "source_checksum_algorithm": "sha256(canonical-json)"})
    recs2 = users(40)
    ipe_engine.execute_source_run(db, tenant_id=t.tenant_id, installation=inst, actor_id="actor-svc", ipe_policy=pol_full,
                                  test_definition_id=td_mfa.test_definition_id, payload={
                                      "coverage_from": "2026-08-15T00:00:00Z", "coverage_to": "2026-10-01T00:00:00Z",
                                      "parameters": {"scope": {"tenant": "zoiko-eu.onmicrosoft.com", "accountEnabled": True}},
                                      "consistency_boundary": {"snapshot_token": "snap-2026-10-01T00:00Z"},
                                      "source_reported_total": 42, "terminal_reached": True,
                                      "pages": [{"page": 1, "cursor": "d1", "records": recs2[:25]}, {"page": 2, "cursor": "d2", "records": recs2[25:]}]})
    for td, title in ((td_dev, "Intune compliance policy export"), (td_net, "Named-location / PAW policy export")):
        e = capture(title, "SYSTEM_DIRECT", "P1_SYSTEM_DIRECT", "2026-06-01", "2026-12-31", 30, title + " (synthetic)", actor="actor-svc",
                    method="API", src="native-test")
        run(td, "2026-06-01", "2026-12-31", "PASS", [e], "assure-native")
    inc_doc = capture("Incident drill report Q3", "HUMAN_UPLOAD", "P4_MANUAL_UNATTESTED", "2026-07-01", "2026-10-01", 90, "Drill DR-2026-Q3 executed (synthetic)")
    run(td_inc, "2026-07-01", "2026-10-01", "PASS", [inc_doc], "manual-review:actor-audit")
    tn = capture("Copilot disclosure synthetic monitor", "SYSTEM_DIRECT", "P1_SYSTEM_DIRECT", "2026-08-01", "2026-12-31", 30,
                 "Banner 'You are chatting with an AI assistant' present (synthetic)", actor="actor-svc", method="API", src="assure-native")
    run(td_tn, "2026-08-01", "2026-12-31", "PASS", [tn], "assure-native")

    # obligation adoption for the incident process
    appl.attest(db, ctx=gc, determination_lineage_id=appl.current_determination(db, t.tenant_id, "subj-entity-eu",
                o_inc.obligation_definition_id).lineage_id, decision="ADOPT", authority_basis="General Counsel mandate")

    # annual review occurrence (fulfilled before due date)
    review = capture("ICT framework annual review record 2026", "HUMAN_UPLOAD", "P4_MANUAL_UNATTESTED", "2026-09-20", "2026-09-21", 365,
                     "Annual review AR-2026 completed and approved (synthetic)")
    db.add(ObligationOccurrence(tenant_id=t.tenant_id, obligation_lineage_id=dora_obl.lineage_id, window_from=dt("2026-01-01"),
                                due_at=dt("2026-09-30"), fulfilled_at=dt("2026-09-20"), evidence_ids=[review.evidence_id],
                                record_seq=next_record_seq(db)))
    # governance objects: pending compensation (STA §7 example) and an approved exception for the September gap
    db.add(Compensation(tenant_id=t.tenant_id, obligation_lineage_id=dora_obl.lineage_id, primary_control_key="C_MFA_PHISHING_RESISTANT",
                        compensating_expression={"node_type": "ALL", "node_id": "comp_all", "children": [
                            {"node_type": "CONTROL", "node_id": "comp_dev", "control_key": "C_DEVICE_ATTESTATION"},
                            {"node_type": "CONTROL", "node_id": "comp_net", "control_key": "C_PRIVILEGED_NETWORK_RESTRICTION"}]},
                        equivalence_rationale="Device attestation plus privileged-network restriction mitigates credential phishing for the "
                                              "residual accounts not yet on FIDO2 during migration.",
                        valid_from=dt("2026-08-15"), valid_to=dt("2026-12-31"), record_seq=next_record_seq(db)))
    db.add(ExceptionRecord(tenant_id=t.tenant_id, target_type="OBLIGATION", target_id=dora_obl.lineage_id,
                           reason="September access review delayed by Vanta workflow migration; manual review scheduled.",
                           state="APPROVED", requested_by="actor-owner", approved_by="actor-risk", valid_from=dt("2026-09-01"),
                           valid_to=dt("2026-12-31"), record_seq=next_record_seq(db)))
    db.flush()

    # ---------------------------------------------------------------- frozen Q3 snapshots + ledger checkpoint
    for lin in (dora_obl.lineage_id, tr_obl.lineage_id):
        asr.compute_snapshot(db, tenant_id=t.tenant_id, obligation_lineage_id=lin, period_from=dt("2026-07-01"),
                             period_to=dt("2026-10-01"), record_cutoff_seq=None, perspective="RECONSTRUCTED_NOW", actor_id="actor-audit")
    evd.create_checkpoint(db)
    # The fixture's people hold their roles through the role registry (Stage 4b), recorded last so that every record
    # sequence number above is the same as before the registry existed.
    from .iam import provision_roles
    for a in A.values():
        provision_roles(db, a, list(a.roles or []), source="SEED", reason="Synthetic Customer Zero fixture (demo mode)")
    db.commit()
