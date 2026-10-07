"""Controlled Terminology & Status Dictionary (ZA-GLS-001 v1.0).

Machine codes are uppercase ASCII SNAKE_CASE and immutable. Six independent
state dimensions are kept separate; there is deliberately NO generic
COMPLIANT / NON_COMPLIANT code anywhere in this module (GLS-REQ-002).
"""
from __future__ import annotations

DICTIONARY_VERSION = "ZA-GLS-001:1.0"
ROLLUP_ENGINE_VERSION = "ZA-STA-001:rollup-1.0"
EVALUATOR_BUILD = "za-applicability-evaluator:1.0.0"
CONTROL_EVALUATOR_VERSION = "ZA-STA-001:control-eval-1.0"
IPE_ENGINE_VERSION = "ZA-IPE-001:engine-1.0"
RULE_SCHEMA_VERSION = "1.0"
OPERATOR_SET_VERSION = "1.0"

DIMENSIONS: dict[str, dict[str, str]] = {
    "content_lifecycle": {
        "DISCOVERED": "Potential source/change detected; not yet controlled content.",
        "INGESTED": "Source bytes/metadata captured with fingerprint and rights metadata.",
        "CLASSIFIED": "Instrument, jurisdiction, authority and source type classified.",
        "AI_EXTRACTED": "Governed extractor produced structured proposals; non-binding.",
        "REVIEW_REQUIRED": "Human review is required before release eligibility.",
        "VERIFIED": "Qualified reviewer validated encoded content and rule inputs.",
        "PUBLISHED": "Signed content release is active for declared coverage.",
        "SUPERSEDED": "A newer published version replaces this one for future valid time.",
        "WITHDRAWN": "Removed from release eligibility; reason required.",
    },
    "coverage_mode": {
        "ENCODED": "Provision-level content and applicability logic maintained.",
        "MONITORING_ONLY": "Change monitoring only; MUST NOT produce binding applicability.",
        "NOT_COVERED": "No supported coverage commitment.",
        "RETIRED": "Previously supported coverage no longer maintained.",
    },
    "applicability_outcome": {
        "APPLIES": "Released rule evaluates TRUE from admissible facts.",
        "DOES_NOT_APPLY": "Released rule evaluates FALSE with all decisive facts known.",
        "UNCERTAIN": "Required facts missing/conflicting/unsupported; never coerced to false.",
    },
    "determination_state": {
        "PROPOSED": "Deterministic evaluation exists; customer adoption not yet occurred.",
        "IN_REVIEW": "Named accountable reviewer is assessing.",
        "ADOPTED": "Authorized customer actor attested the determination.",
        "REJECTED": "Customer did not adopt; reason mandatory.",
        "SUPERSEDED": "A newer determination replaces this one.",
        "REVOKED": "Adopted determination withdrawn by authorized process.",
    },
    "obligation_lifecycle": {
        "PROPOSED": "Evaluation indicates applicability; adoption incomplete.",
        "ADOPTED": "Required customer attestation satisfied.",
        "SUSPENDED": "Temporarily inactive under approved condition.",
        "SUPERSEDED": "A newer obligation revision replaces it.",
        "CLOSED": "Duty ended; history retained.",
    },
    "assurance_state": {
        "PROVEN": "Satisfaction expression satisfied for the full asserted period with admissible evidence.",
        "NOT_PROVEN": "No decisive failure, but proof is incomplete.",
        "FAILING": "A required path is decisively unsatisfied with no valid alternative.",
        "REQUIRES_REVIEW": "A governed ambiguity prevents deterministic assurance.",
    },
    "control_operating_state": {
        "OPERATING": "Control criteria affirmatively satisfied for the window.",
        "DEGRADED": "Operating with a material limitation defined by policy.",
        "FAILING": "Control does not meet its defined operating criteria.",
        "NOT_EVALUATED": "No valid evaluation exists for the required window.",
        "REQUIRES_REVIEW": "Governed conflict or ambiguity must be resolved.",
    },
    "test_result": {
        "PASS": "Executed; expected criteria met.",
        "FAIL": "Executed; criteria not met.",
        "ERROR": "Did not complete reliably.",
        "INCONCLUSIVE": "Completed but cannot determine pass/fail.",
        "SKIPPED": "Intentionally not run under a recorded rule.",
    },
    "evidence_condition": {
        "CURRENT": "Usable, within validity window and not invalidated.",
        "MISSING": "Required evidence does not exist or is not retrievable.",
        "STALE": "Outside declared validity/freshness window.",
        "INVALID": "Integrity, scope, provenance or quality validation failed.",
        "CONFLICTING": "Authoritative sources materially disagree.",
        "UNVERIFIED": "Required integrity/provenance validation incomplete.",
        "ERASED": "Payload lawfully erased; manifest remains.",
        "UNAVAILABLE": "Source temporarily inaccessible.",
    },
    "artifact_lifecycle": {
        "ACTIVE": "Current evidence artifact.",
        "SUPERSEDED": "Replaced by a successor artifact; history retained.",
        "INVALIDATED": "Invalidated with recorded reason.",
        "ERASED": "Payload erased; manifest/ledger history remains.",
        "RESTRICTED": "Access-restricted (hold/privilege).",
    },
    "exception_workflow": {
        "DRAFT": "Being prepared; no effect.",
        "PENDING_APPROVAL": "Required approvers not complete.",
        "APPROVED": "Active for exact scope and interval; never changes assurance state.",
        "REJECTED": "Denied.",
        "EXPIRED": "Approved interval ended.",
        "REVOKED": "Withdrawn before expiry.",
        "SUPERSEDED": "Replaced by another exception.",
    },
    "issue_workflow": {
        "OPEN": "Awaits triage.", "TRIAGED": "Owner/severity established.",
        "IN_REMEDIATION": "Remediation active.", "PENDING_VERIFICATION": "Awaiting verification.",
        "CLOSED": "Closure criteria verified.", "CANCELLED": "Invalid/duplicate; reason required.",
    },
    "ipe_overall": {
        "IPE_VERIFIED": "Every required completeness/accuracy check verified.",
        "IPE_FAILED": "A required subresult FAILED.",
        "IPE_INCONCLUSIVE": "Unresolved material limitation; not a soft pass.",
        "IPE_NOT_REQUIRED": "Policy requires neither dimension.",
    },
    "connector_certification": {
        "UNASSESSED": "Not assessed.", "CERTIFIED": "Passed certification matrix for contract envelope.",
        "SUSPENDED": "Suspended pending recertification.", "REVOKED": "Revoked.",
    },
    "connector_release": {
        "PLANNED": "", "PRIVATE_BETA": "", "GENERAL_AVAILABILITY": "", "DEPRECATED": "", "RETIRED": "",
    },
    "connector_runtime": {"HEALTHY": "", "DEGRADED": "", "FAILED": "", "DISABLED": ""},
}

REASON_CODES: dict[str, str] = {
    "MISSING_REQUIRED_FACT": "A required fact is absent for applicability evaluation.",
    "FACT_CONFLICT": "Two admissible facts conflict for the same valid time.",
    "FACT_EXPIRED": "Fact exceeded its FactDefinition expiry policy.",
    "SOURCE_UNAVAILABLE": "Required upstream system temporarily unreachable.",
    "ACCESS_DENIED": "Connector/user lacked authority to retrieve data.",
    "NOT_COLLECTED": "Intentionally not collected under policy.",
    "OUTSIDE_RETENTION": "No longer available due to retention.",
    "ERASED_BY_POLICY": "Payload lawfully erased.",
    "UNSUPPORTED_SOURCE": "Connector does not expose the required field/population.",
    "LOW_PROVENANCE": "Source does not meet required provenance class.",
    "POPULATION_INCOMPLETE": "IPE checks show incomplete population.",
    "RULE_GAP": "No released rule can defensibly decide the case.",
    "CONTENT_CORRECTION_PENDING": "A content correction may alter state.",
    # Assurance / control reason codes (ZA-STA-001 §16)
    "MISSING_EVIDENCE": "Required evidence missing.",
    "STALE_EVIDENCE": "Evidence outside validity window.",
    "INVALID_EVIDENCE": "Evidence integrity/scope failed.",
    "TEST_FAIL": "Required decisive test failed.",
    "TEST_NOT_RUN": "Required test has no run covering window.",
    "TEST_ERROR": "Required test errored.",
    "TEST_INCONCLUSIVE": "Required test inconclusive.",
    "COVERAGE_GAP": "Accepted coverage does not span the window.",
    "SOURCE_CONFLICT": "Authoritative sources conflict.",
    "IPE_FAILED": "Required IPE not verified (failed).",
    "IPE_INCONCLUSIVE": "Required IPE inconclusive.",
    "MANUAL_EVIDENCE_INSUFFICIENT": "Automation-required control; manual evidence alone.",
    "DEGRADATION_RULE": "Approved degradation rule matched.",
    "COMPENSATION_EXPIRED": "Compensation outside approved interval or not approved.",
    "CONTROL_NOT_IMPLEMENTED": "No control implementation active in segment.",
    "OBLIGATION_NOT_APPLICABLE": "Obligation not active for interval.",
    "REQUIRED_CONTROL_NOT_EVALUATED": "A required control could not be evaluated.",
    "OCCURRENCE_MISSED": "Required occurrence window elapsed without fulfilment.",
    "OCCURRENCE_PENDING": "Occurrence window active / evidence incomplete.",
}

IPE_REASON_CODES = {
    "IPE_SCOPE_INSUFFICIENT": "Actual auth/source scope does not cover intended population.",
    "IPE_SNAPSHOT_UNPROVEN": "No stable source consistency boundary.",
    "IPE_CURSOR_INCOMPLETE": "Terminal enumeration not reached or gap detected.",
    "IPE_COUNT_MISMATCH": "Authoritative total and reconciled count differ.",
    "IPE_DUPLICATE_KEYS": "Unexpected duplicate identities.",
    "IPE_REJECTED_RECORDS": "Records could not be parsed/mapped.",
    "IPE_MAPPING_MISMATCH": "Relied-upon field maps incorrectly or schema drifted.",
    "IPE_TRANSFORM_MISMATCH": "Deterministic reperformance differs.",
    "IPE_SOURCE_CHECKSUM_MISMATCH": "Source-provided checksum verification failed.",
    "IPE_SOURCE_LIMITATION": "Known source limitation prevents required proof.",
    "IPE_ZERO_ROWS_UNPROVEN": "Empty extraction without positive empty-population proof.",
    "IPE_PARTIAL_RUN": "Run produced only part of intended extraction.",
}

PROVENANCE_CLASSES = ["P1_SYSTEM_DIRECT", "P2_SYSTEM_DERIVED", "P3_CUSTOMER_ATTESTED",
                      "P4_MANUAL_UNATTESTED", "P5_INFERRED_AI"]

ACTOR_TYPES = ["HUMAN", "SERVICE", "AGENT", "SIGNED_CONTENT", "EXTERNAL_AUDITOR"]


def assert_code(dimension: str, code: str) -> str:
    """Reject undeclared controlled codes (GLS-REQ-001)."""
    if code not in DIMENSIONS[dimension]:
        raise ValueError(f"Undeclared {dimension} code: {code}")
    return code


def dictionary_payload() -> dict:
    return {
        "dictionary_version": DICTIONARY_VERSION,
        "dimensions": DIMENSIONS,
        "reason_codes": REASON_CODES,
        "ipe_reason_codes": IPE_REASON_CODES,
        "provenance_classes": PROVENANCE_CLASSES,
        "actor_types": ACTOR_TYPES,
    }
