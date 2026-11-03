"""Logical/physical data model (ZA-DAT-001).

Bounded schemas are expressed as table-name prefixes so the same model works on
SQLite (dev) and PostgreSQL (where migration 0002 adds RLS and the append-only
triggers). Authoritative fields are typed relational columns; JSON is used
only for validated canonical artifacts (AST, traces, manifests) per DAT-REQ-006.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer,
                        LargeBinary, Numeric, String, Text, UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    gen = getattr(uuid, "uuid7", None)  # UUIDv7 where runtime supports it (DAT-ADR-01)
    return str(gen() if gen else uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id():
    return mapped_column(String(36), primary_key=True, default=new_id)


TS = DateTime(timezone=True)


# ---------------------------------------------------------------- platform
class RecordSequence(Base):
    """Cell-local monotonic record sequence (TIM-REQ-005)."""
    __tablename__ = "platform_record_sequence"
    cell_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_seq: Mapped[int] = mapped_column(BigInteger, default=0)


class OutboxEvent(Base):
    __tablename__ = "platform_outbox_event"
    event_id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    cell_id: Mapped[str] = mapped_column(String(64))
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(96), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    dictionary_version: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[dict | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(TS)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class IdempotencyRecord(Base):
    __tablename__ = "platform_idempotency_record"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_code", "idempotency_key"),)
    id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36))
    operation_code: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuditEvent(Base):
    """State-transition audit (NFR-010)."""
    __tablename__ = "platform_audit_event"
    id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_type: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(96))
    object_type: Mapped[str] = mapped_column(String(64))
    object_id: Mapped[str] = mapped_column(String(64))
    prior_state: Mapped[str | None] = mapped_column(String(64))
    new_state: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- org
class Cell(Base):
    __tablename__ = "org_cell"
    cell_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    isolation_tier: Mapped[str] = mapped_column(String(32), default="POOLED")


class Tenant(Base):
    __tablename__ = "org_tenant"
    tenant_id: Mapped[str] = _id()
    legal_name: Mapped[str] = mapped_column(String(200))
    contracting_region: Mapped[str] = mapped_column(String(64))
    isolation_tier: Mapped[str] = mapped_column(String(32), default="POOLED")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LegalEntity(Base):
    __tablename__ = "org_legal_entity"
    entity_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(ForeignKey("org_tenant.tenant_id"), index=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(8))
    registration_ref: Mapped[str | None] = mapped_column(String(100))
    home_cell_id: Mapped[str] = mapped_column(ForeignKey("org_cell.cell_id"))
    subject_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class Subject(Base):
    """Canonical typed subject registry (SubjectRef integrity)."""
    __tablename__ = "org_subject_registry"
    subject_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(ForeignKey("org_tenant.tenant_id"), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    subject_type: Mapped[str] = mapped_column(String(40))  # LEGAL_ENTITY, BUSINESS_UNIT, REGULATED_ACTIVITY, PRODUCT_SERVICE, ENVIRONMENT, AI_ASSET, CONTRACT_THIRD_PARTY
    display_name: Mapped[str] = mapped_column(String(200))
    parent_subject_id: Mapped[str | None] = mapped_column(String(36))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Actor(Base):
    __tablename__ = "org_actor"
    actor_id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)  # null = Zoiko content/platform staff
    actor_type: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200))
    # Cache of the current organisation-/platform-wide roles since migration 0009; authority is iam_role_assignment.
    roles: Mapped[list] = mapped_column(JSON, default=list)
    authority_basis: Mapped[str | None] = mapped_column(String(200))


class AuthCredential(Base):
    """Interactive sign-in credential (scrypt). Only HUMAN / EXTERNAL_AUDITOR actors hold one;
    agents and services authenticate with workload identity, never passwords."""
    __tablename__ = "auth_credential"
    actor_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS)
    must_change: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuthSession(Base):
    """Server-side session record so logout / revocation is effective before token expiry.
    Holds only the SHA-256 of the bearer token (migration 0006); the token itself is never stored."""
    __tablename__ = "auth_session"
    session_id: Mapped[str] = _id()
    actor_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    user_agent: Mapped[str | None] = mapped_column(String(300))


# ---------------------------------------------------------------- IAM role registry (ZA-IAM-001 §02, §22; 0009)
ROLE_ASSIGNMENT_SCOPE_CHECK = (
    "(scope_type = 'PLATFORM' AND tenant_id IS NULL AND scope_id IS NULL)"
    " OR (scope_type = 'TENANT' AND tenant_id IS NOT NULL AND scope_id = tenant_id)"
    " OR (scope_type = 'LEGAL_ENTITY' AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)")


class RoleAssignment(Base):
    """A principal's role at an explicit scope for a valid-time period (IAM-REQ-010). The authority source for
    authorisation (app/iam.py); ``org_actor.roles`` is only a cache. Revocation closes the row (status, valid_to and the
    revoked_* columns, the only columns a database trigger lets change); history is in RoleAssignmentEvent."""
    __tablename__ = "iam_role_assignment"
    __table_args__ = (CheckConstraint(ROLE_ASSIGNMENT_SCOPE_CHECK, name="ck_iam_role_assignment_scope"),
                      CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_iam_role_assignment_status"),
                      CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_iam_role_assignment_period"))
    assignment_id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)  # NULL: Zoiko platform principal
    principal_id: Mapped[str] = mapped_column(String(36), index=True)
    role_code: Mapped[str] = mapped_column(String(64))
    scope_type: Mapped[str] = mapped_column(String(16))  # TENANT, LEGAL_ENTITY, PLATFORM
    scope_id: Mapped[str | None] = mapped_column(String(36))  # tenant_id, entity_id, or NULL for PLATFORM
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    source: Mapped[str] = mapped_column(String(32))  # MIGRATION, BOOTSTRAP, SEED, PLATFORM_APPOINTMENT, ADMIN
    granted_by: Mapped[str | None] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_by: Mapped[str | None] = mapped_column(String(36))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    revoked_record_seq: Mapped[int | None] = mapped_column(BigInteger)


class RoleAssignmentEvent(Base):
    """Append-only history of role assignments: who assigned or revoked what, at which scope, on whose authority and
    under which policy version (IAM-INV-09, IAM-REQ-037, IAM §21)."""
    __tablename__ = "iam_role_assignment_event"
    event_id: Mapped[str] = _id()
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)
    principal_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(16))  # ASSIGNED, REVOKED
    role_code: Mapped[str] = mapped_column(String(64))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str | None] = mapped_column(String(36))
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    authorized_by: Mapped[list] = mapped_column(JSON, default=list)  # the actor's authorising assignment IDs
    policy_version: Mapped[str] = mapped_column(String(40))
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- catalog
class ContentRelease(Base):
    __tablename__ = "catalog_content_release"
    content_release_id: Mapped[str] = _id()
    label: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="PUBLISHED")
    manifest: Mapped[dict] = mapped_column(JSON)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)
    signer_key_id: Mapped[str] = mapped_column(String(64))
    changelog: Mapped[str | None] = mapped_column(Text)
    shadow_summary: Mapped[dict | None] = mapped_column(JSON)
    released_by: Mapped[str] = mapped_column(String(36))
    published_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    cell_received_seq: Mapped[int] = mapped_column(BigInteger)
    correction_of_release_id: Mapped[str | None] = mapped_column(String(36))


class RegulatorySource(Base):
    __tablename__ = "catalog_source_record"
    source_id: Mapped[str] = _id()
    authority: Mapped[str] = mapped_column(String(200))
    jurisdiction: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(300))
    locator: Mapped[str] = mapped_column(String(500))
    fingerprint: Mapped[str] = mapped_column(String(64))
    rights_class: Mapped[str] = mapped_column(String(48))  # e.g. OFFICIAL_REUSE_PERMITTED, REFERENCE_ONLY, LICENSED
    rights_record: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_text: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    lifecycle: Mapped[str] = mapped_column(String(32), default="INGESTED")
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class Instrument(Base):
    __tablename__ = "catalog_regulatory_instrument"
    instrument_id: Mapped[str] = _id()
    identifier: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    jurisdiction: Mapped[str] = mapped_column(String(32))
    authority: Mapped[str] = mapped_column(String(200))
    coverage_mode: Mapped[str] = mapped_column(String(32), default="ENCODED")
    regime_owner: Mapped[str | None] = mapped_column(String(120))


class RequirementVersion(Base):
    __tablename__ = "catalog_requirement_version"
    requirement_version_id: Mapped[str] = _id()
    requirement_key: Mapped[str] = mapped_column(String(120), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("catalog_regulatory_instrument.instrument_id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("catalog_source_record.source_id"))
    title: Mapped[str] = mapped_column(String(300))
    locator: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)  # LEGAL_DATE_RANGE
    valid_to: Mapped[date | None] = mapped_column(Date)
    lifecycle: Mapped[str] = mapped_column(String(32), default="REVIEW_REQUIRED")
    origin: Mapped[str] = mapped_column(String(32), default="ZOIKO_ENCODED")
    content_release_id: Mapped[str | None] = mapped_column(String(36))
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger)


class Provision(Base):
    __tablename__ = "catalog_provision"
    provision_id: Mapped[str] = _id()
    requirement_version_id: Mapped[str] = mapped_column(ForeignKey("catalog_requirement_version.requirement_version_id"))
    locator: Mapped[str] = mapped_column(String(200))
    duty_bearer: Mapped[str] = mapped_column(String(64))
    modality: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(120))
    regulated_object: Mapped[str] = mapped_column(String(200))
    timing: Mapped[str | None] = mapped_column(String(120))
    normalized_meaning: Mapped[str] = mapped_column(Text)
    evidence_expectation: Mapped[str | None] = mapped_column(Text)
    semantic_origin: Mapped[str] = mapped_column(String(32), default="ZOIKO_ENCODED")
    lifecycle: Mapped[str] = mapped_column(String(32), default="REVIEW_REQUIRED")


class ObligationDefinition(Base):
    __tablename__ = "catalog_obligation_definition"
    obligation_definition_id: Mapped[str] = _id()
    key: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("catalog_regulatory_instrument.instrument_id"))
    provision_ids: Mapped[list] = mapped_column(JSON, default=list)
    subject_type: Mapped[str] = mapped_column(String(40))
    coverage_mode: Mapped[str] = mapped_column(String(32), default="ENCODED")
    schedule_kind: Mapped[str] = mapped_column(String(32), default="CONTINUOUS")  # CONTINUOUS, RECURRING, EVENT_TRIGGERED
    recurrence_days: Mapped[int | None] = mapped_column(Integer)
    material: Mapped[bool] = mapped_column(Boolean, default=True)
    satisfaction_template: Mapped[dict | None] = mapped_column(JSON)  # expression over control definition keys


class FactDefinition(Base):
    __tablename__ = "catalog_fact_definition"
    fact_definition_id: Mapped[str] = _id()
    key: Mapped[str] = mapped_column(String(120), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(48))  # BOOLEAN, ENUM, STRING, INTEGER, DECIMAL, DATE, COUNTRY, JURISDICTION, SET<..>
    enum_domain: Mapped[list | None] = mapped_column(JSON)
    subject_type: Mapped[str] = mapped_column(String(40))
    sensitivity: Mapped[str] = mapped_column(String(32), default="CUSTOMER_INTERNAL")
    allowed_provenance: Mapped[list] = mapped_column(JSON, default=lambda: ["P1_SYSTEM_DIRECT", "P2_SYSTEM_DERIVED", "P3_CUSTOMER_ATTESTED"])
    manual_attestation_policy: Mapped[str] = mapped_column(String(32), default="ALLOWED")
    expiry_days: Mapped[int | None] = mapped_column(Integer)
    conflict_policy: Mapped[str] = mapped_column(String(32), default="ERROR_TO_UNKNOWN")
    owner_role: Mapped[str] = mapped_column(String(64), default="REGULATORY_ANALYST")
    status: Mapped[str] = mapped_column(String(32), default="RELEASED")


class SetVersion(Base):
    __tablename__ = "catalog_controlled_set_version"
    set_version_id: Mapped[str] = mapped_column(String(80), primary_key=True)  # e.g. set_annex_iii@4
    set_key: Mapped[str] = mapped_column(String(80))
    element_type: Mapped[str] = mapped_column(String(48))
    members: Mapped[list] = mapped_column(JSON)
    canonical_hash: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[date] = mapped_column(Date)
    source_note: Mapped[str | None] = mapped_column(Text)


class RuleDraft(Base):
    __tablename__ = "catalog_rule_draft"
    draft_id: Mapped[str] = _id()
    obligation_definition_id: Mapped[str] = mapped_column(ForeignKey("catalog_obligation_definition.obligation_definition_id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    authoring_mode: Mapped[str] = mapped_column(String(32), default="AST")  # AST | DECISION_TABLE
    ast: Mapped[dict | None] = mapped_column(JSON)
    decision_table: Mapped[dict | None] = mapped_column(JSON)
    source_mappings: Mapped[list] = mapped_column(JSON, default=list)
    golden_vectors: Mapped[list] = mapped_column(JSON, default=list)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    change_class: Mapped[str | None] = mapped_column(String(48))
    validation_report: Mapped[dict | None] = mapped_column(JSON)
    vector_report: Mapped[dict | None] = mapped_column(JSON)
    shadow_report: Mapped[dict | None] = mapped_column(JSON)
    author_id: Mapped[str] = mapped_column(String(36))
    peer_reviewer_id: Mapped[str | None] = mapped_column(String(36))
    legal_reviewer_id: Mapped[str | None] = mapped_column(String(36))
    supersedes_rule_version_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class RuleVersion(Base):
    __tablename__ = "catalog_rule_version"
    rule_version_id: Mapped[str] = _id()
    obligation_definition_id: Mapped[str] = mapped_column(ForeignKey("catalog_obligation_definition.obligation_definition_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    coverage_mode: Mapped[str] = mapped_column(String(32), default="ENCODED")
    rule_schema_version: Mapped[str] = mapped_column(String(16))
    operator_set_version: Mapped[str] = mapped_column(String(16))
    canonical_ast: Mapped[dict] = mapped_column(JSON)
    ast_hash: Mapped[str] = mapped_column(String(64))
    required_facts: Mapped[list] = mapped_column(JSON)
    set_refs: Mapped[list] = mapped_column(JSON, default=list)
    source_mappings: Mapped[list] = mapped_column(JSON, default=list)
    golden_vectors: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="PUBLISHED")
    content_release_id: Mapped[str] = mapped_column(String(36))
    draft_id: Mapped[str | None] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger)


class ControlDefinition(Base):
    __tablename__ = "catalog_control_definition"
    control_definition_id: Mapped[str] = _id()
    key: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))


class FrameworkMapping(Base):
    __tablename__ = "catalog_framework_mapping"
    id: Mapped[str] = _id()
    control_definition_id: Mapped[str] = mapped_column(ForeignKey("catalog_control_definition.control_definition_id"))
    framework: Mapped[str] = mapped_column(String(64))
    reference: Mapped[str] = mapped_column(String(120))
    rights_note: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------- applicability
class FactAssertion(Base):
    """Bitemporal fact revision (OPERATIONAL_INSTANT_RANGE profile)."""
    __tablename__ = "applicability_fact_assertion_revision"
    revision_id: Mapped[str] = _id()
    lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    fact_definition_id: Mapped[str] = mapped_column(ForeignKey("catalog_fact_definition.fact_definition_id"))
    value_type: Mapped[str] = mapped_column(String(48))
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_integer: Mapped[int | None] = mapped_column(BigInteger)
    value_decimal: Mapped[str | None] = mapped_column(Numeric(38, 10).with_variant(String(64), "sqlite"))
    value_text: Mapped[str | None] = mapped_column(String(500))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_set: Mapped[list | None] = mapped_column(JSON)  # only for controlled SET<T> types
    provenance_class: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(300))
    semantic_origin: Mapped[str] = mapped_column(String(32), default="CUSTOMER_FACT")
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    record_from_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    record_to_seq: Mapped[int | None] = mapped_column(BigInteger)
    record_batch_id: Mapped[str] = mapped_column(String(36))
    change_kind: Mapped[str] = mapped_column(String(40))
    change_reason: Mapped[str | None] = mapped_column(Text)
    correction_of_revision_id: Mapped[str | None] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(String(36))


class FactConflict(Base):
    __tablename__ = "applicability_fact_conflict"
    conflict_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_id: Mapped[str] = mapped_column(String(36))
    fact_definition_id: Mapped[str] = mapped_column(String(36))
    candidate_revision_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ApplicabilityEvaluation(Base):
    """Immutable evaluation (DOM-INV-02, APP-REQ-003)."""
    __tablename__ = "applicability_evaluation"
    __table_args__ = (UniqueConstraint("tenant_id", "evaluation_key"),)
    evaluation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_definition_id: Mapped[str] = mapped_column(String(36), index=True)
    rule_version_id: Mapped[str] = mapped_column(String(36))
    effective_at: Mapped[datetime] = mapped_column(TS)
    record_cutoff_seq: Mapped[int] = mapped_column(BigInteger)
    evaluator_build: Mapped[str] = mapped_column(String(64))
    input_bundle: Mapped[dict] = mapped_column(JSON)
    input_bundle_hash: Mapped[str] = mapped_column(String(64))
    evaluation_key: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    trace: Mapped[dict] = mapped_column(JSON)
    missing_facts: Mapped[list] = mapped_column(JSON, default=list)
    non_material_unknowns: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(String(40))
    requested_by: Mapped[str] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ApplicabilityDetermination(Base):
    __tablename__ = "applicability_determination_revision"
    revision_id: Mapped[str] = _id()
    lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_id: Mapped[str] = mapped_column(String(36))
    obligation_definition_id: Mapped[str] = mapped_column(String(36))
    evaluation_id: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    basis: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    record_from_seq: Mapped[int] = mapped_column(BigInteger)
    record_to_seq: Mapped[int | None] = mapped_column(BigInteger)
    actor_id: Mapped[str] = mapped_column(String(36))
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AdoptionAttestation(Base):
    __tablename__ = "governance_attestation"
    attestation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_type: Mapped[str] = mapped_column(String(48))
    target_revision_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(String(36))
    authority_basis: Mapped[str] = mapped_column(String(200))
    statement_version: Mapped[str] = mapped_column(String(32))
    statement: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(32))  # ATTESTED, DECLINED
    signed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class DeterminationChallenge(Base):
    __tablename__ = "applicability_determination_challenge"
    challenge_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    determination_lineage_id: Mapped[str] = mapped_column(String(36))
    evaluation_id: Mapped[str] = mapped_column(String(36))
    challenge_class: Mapped[str] = mapped_column(String(40))  # FACT_WRONG, FACT_MISSING, RULE_WRONG, INTERPRETATION, DECLINE
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    disposition: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class FactRequest(Base):
    __tablename__ = "applicability_fact_request"
    request_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_id: Mapped[str] = mapped_column(String(36))
    fact_definition_id: Mapped[str] = mapped_column(String(36))
    reason_code: Mapped[str] = mapped_column(String(48))
    owner_role: Mapped[str] = mapped_column(String(64))
    due_at: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    linked_evaluation_ids: Mapped[list] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- assurance
class ObligationInstance(Base):
    __tablename__ = "assurance_obligation_instance_revision"
    revision_id: Mapped[str] = _id()
    lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    subject_id: Mapped[str] = mapped_column(String(36))
    obligation_definition_id: Mapped[str] = mapped_column(String(36))
    determination_revision_id: Mapped[str] = mapped_column(String(36))
    attestation_id: Mapped[str] = mapped_column(String(36))
    owner_actor_id: Mapped[str | None] = mapped_column(String(36))
    lifecycle: Mapped[str] = mapped_column(String(32), default="ADOPTED")
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    record_from_seq: Mapped[int] = mapped_column(BigInteger)
    record_to_seq: Mapped[int | None] = mapped_column(BigInteger)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SatisfactionExpressionVersion(Base):
    __tablename__ = "assurance_satisfaction_expression_version"
    expression_version_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    ast: Mapped[dict] = mapped_column(JSON)
    ast_hash: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class ObligationOccurrence(Base):
    __tablename__ = "assurance_obligation_occurrence"
    occurrence_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    window_from: Mapped[datetime] = mapped_column(TS)
    due_at: Mapped[datetime] = mapped_column(TS)
    fulfilled_at: Mapped[datetime | None] = mapped_column(TS)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class ObligationOccurrenceEvent(Base):
    __tablename__ = "assurance_obligation_occurrence_event"
    event_id: Mapped[str] = _id()
    occurrence_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # SCHEDULED, FULFILLED, DISPUTED, BASELINE
    actor_id: Mapped[str] = mapped_column(String(36))
    window_from: Mapped[datetime] = mapped_column(TS)
    due_at: Mapped[datetime] = mapped_column(TS)
    fulfilled_at: Mapped[datetime | None] = mapped_column(TS)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ControlImplementation(Base):
    __tablename__ = "assurance_control_implementation_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "control_definition_id", "subject_id", "variant"),)  # INV-08
    control_implementation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    subject_id: Mapped[str] = mapped_column(String(36))
    control_definition_id: Mapped[str] = mapped_column(ForeignKey("catalog_control_definition.control_definition_id"))
    variant: Mapped[str] = mapped_column(String(64), default="default")
    owner_actor_id: Mapped[str | None] = mapped_column(String(36))
    method: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class ControlTestPolicy(Base):
    __tablename__ = "assurance_control_test_policy_version"
    policy_version_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    control_implementation_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    aggregation_mode: Mapped[str] = mapped_column(String(40), default="ALL_REQUIRED")
    k_threshold: Mapped[int | None] = mapped_column(Integer)
    automation_requirement: Mapped[str] = mapped_column(String(16), default="PREFERRED")  # NONE/PREFERRED/REQUIRED
    evidence_validity_days: Mapped[int] = mapped_column(Integer, default=90)
    status: Mapped[str] = mapped_column(String(32), default="RELEASED")
    record_seq: Mapped[int] = mapped_column(BigInteger)


class TestDefinition(Base):
    __tablename__ = "assurance_test_definition_version"
    __test__ = False
    test_definition_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    policy_version_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_class: Mapped[str] = mapped_column(String(48))  # CONNECTOR, EXTERNAL_GRC, MANUAL, NATIVE
    cadence_days: Mapped[int] = mapped_column(Integer, default=30)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    primary: Mapped[bool] = mapped_column(Boolean, default=False)
    degrades_on_fail: Mapped[bool] = mapped_column(Boolean, default=False)
    ipe_required: Mapped[bool] = mapped_column(Boolean, default=False)
    criterion: Mapped[dict | None] = mapped_column(JSON)  # {"quantifier":"ALL","field":"mfa_enabled","op":"EQ","value":true}


class TestRun(Base):
    __tablename__ = "assurance_test_run"
    __test__ = False
    test_run_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    test_definition_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = mapped_column(TS)
    window_from: Mapped[datetime] = mapped_column(TS)
    window_to: Mapped[datetime] = mapped_column(TS)
    result: Mapped[str] = mapped_column(String(16))
    source_system: Mapped[str] = mapped_column(String(80))
    external_status: Mapped[str | None] = mapped_column(String(64))  # vendor "PASS" kept as input only
    source_run_id: Mapped[str | None] = mapped_column(String(36))
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[str | None] = mapped_column(Text)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class Compensation(Base):
    __tablename__ = "assurance_compensation_revision"
    compensation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_lineage_id: Mapped[str] = mapped_column(String(36))
    primary_control_key: Mapped[str] = mapped_column(String(80))
    compensating_expression: Mapped[dict] = mapped_column(JSON)
    equivalence_rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING_APPROVAL")
    approved_by: Mapped[str | None] = mapped_column(String(36))
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class CompensationEvent(Base):
    __tablename__ = "assurance_compensation_event"
    event_id: Mapped[str] = _id()
    compensation_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # SUBMITTED, APPROVED, REJECTED, REVOKED, BASELINE
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    obligation_lineage_id: Mapped[str] = mapped_column(String(36))
    primary_control_key: Mapped[str] = mapped_column(String(80))
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ControlEvaluation(Base):
    """Immutable derived control state (DAT-REQ-011)."""
    __tablename__ = "assurance_control_evaluation"
    evaluation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    control_implementation_id: Mapped[str] = mapped_column(String(36), index=True)
    policy_version_id: Mapped[str] = mapped_column(String(36))
    window_from: Mapped[datetime] = mapped_column(TS)
    window_to: Mapped[datetime] = mapped_column(TS)
    record_cutoff_seq: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(32))
    reason_codes: Mapped[list] = mapped_column(JSON)
    inputs: Mapped[dict] = mapped_column(JSON)
    precedence_rule: Mapped[str] = mapped_column(String(64))
    evaluator_version: Mapped[str] = mapped_column(String(64))
    dependency_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AssuranceSnapshot(Base):
    __tablename__ = "assurance_snapshot"
    snapshot_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    obligation_lineage_id: Mapped[str] = mapped_column(String(36), index=True)
    obligation_revision_id: Mapped[str] = mapped_column(String(36))
    period_from: Mapped[datetime] = mapped_column(TS)
    period_to: Mapped[datetime] = mapped_column(TS)
    record_cutoff_seq: Mapped[int] = mapped_column(BigInteger)
    perspective: Mapped[str] = mapped_column(String(32))
    expression_version_id: Mapped[str] = mapped_column(String(36))
    rollup_engine_version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    lifecycle: Mapped[str] = mapped_column(String(40), default="FROZEN")
    segments: Mapped[list] = mapped_column(JSON)
    dependency_manifest: Mapped[dict] = mapped_column(JSON)
    dependency_digest: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    exception_treatments: Mapped[list] = mapped_column(JSON, default=list)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ExceptionRecord(Base):
    __tablename__ = "assurance_exception_revision"
    exception_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_type: Mapped[str] = mapped_column(String(32))  # OBLIGATION, CONTROL
    target_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT")
    requested_by: Mapped[str] = mapped_column(String(36))
    approved_by: Mapped[str | None] = mapped_column(String(36))
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime] = mapped_column(TS)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class ExceptionEvent(Base):
    __tablename__ = "assurance_exception_event"
    event_id: Mapped[str] = _id()
    exception_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # REQUESTED, APPROVED, REJECTED, REVOKED, BASELINE
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(36))
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime] = mapped_column(TS)
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class RiskAcceptance(Base):
    __tablename__ = "governance_risk_acceptance_revision"
    risk_acceptance_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    scope_ref: Mapped[str] = mapped_column(String(200))
    residual_risk_statement: Mapped[str] = mapped_column(Text)
    approver_id: Mapped[str] = mapped_column(String(36))
    valid_from: Mapped[datetime] = mapped_column(TS)
    valid_to: Mapped[datetime | None] = mapped_column(TS)


class Issue(Base):
    __tablename__ = "assurance_issue"
    issue_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(300))
    issue_type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    priority: Mapped[str] = mapped_column(String(8), default="P2")
    state: Mapped[str] = mapped_column(String(32), default="OPEN")
    source_ref_type: Mapped[str] = mapped_column(String(48))
    source_ref_id: Mapped[str] = mapped_column(String(36))
    owner_actor_id: Mapped[str | None] = mapped_column(String(36))
    due_at: Mapped[datetime | None] = mapped_column(TS)
    dedupe_key: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class IssueEvent(Base):
    __tablename__ = "assurance_issue_event"
    event_id: Mapped[str] = _id()
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # CREATED, TRANSITIONED, ASSIGNED, RESOLVED, CANCELLED, BASELINE
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    owner_actor_id: Mapped[str | None] = mapped_column(String(36))
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- evidence
class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifact"
    __table_args__ = (UniqueConstraint("tenant_id", "capture_key"),)
    evidence_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str] = mapped_column(String(36))
    home_cell: Mapped[str] = mapped_column(String(64))
    capture_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    origin_class: Mapped[str] = mapped_column(String(32))  # SYSTEM_DIRECT, SYSTEM_DERIVED, HUMAN_UPLOAD, ATTESTATION, GENERATED_DOCUMENT, EXTERNAL_IMPORT
    acquisition_method: Mapped[str] = mapped_column(String(32))
    provenance_class: Mapped[str] = mapped_column(String(32))
    source_system: Mapped[str | None] = mapped_column(String(120))
    source_run_id: Mapped[str | None] = mapped_column(String(36))
    coverage_from: Mapped[datetime] = mapped_column(TS)
    coverage_to: Mapped[datetime] = mapped_column(TS)
    source_observed_at: Mapped[datetime | None] = mapped_column(TS)
    captured_at: Mapped[datetime] = mapped_column(TS)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    valid_until: Mapped[datetime] = mapped_column(TS)
    ledger_seq: Mapped[int] = mapped_column(BigInteger)
    data_classification: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    plaintext_hmac: Mapped[str] = mapped_column(String(64))
    fingerprint_key_version: Mapped[str] = mapped_column(String(32))
    ciphertext_digest: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(80))
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary)
    encryption_profile: Mapped[str] = mapped_column(String(48), default="AES-256-GCM/envelope-v1")
    source_checksum: Mapped[str | None] = mapped_column(String(128))
    record_seq: Mapped[int] = mapped_column(BigInteger)
    uploader_id: Mapped[str] = mapped_column(String(36))
    review_status: Mapped[str | None] = mapped_column(String(32))


class TenantLedgerHead(Base):
    __tablename__ = "evidence_tenant_ledger_head"
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    current_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    current_event_hash: Mapped[str] = mapped_column(String(64))
    last_checkpointed_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    last_checkpointed_hash: Mapped[str | None] = mapped_column(String(64))
    last_verified_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvidenceLedgerEvent(Base):
    __tablename__ = "evidence_ledger_event"
    __table_args__ = (UniqueConstraint("tenant_id", "ledger_seq"), UniqueConstraint("tenant_id", "event_hash"))
    event_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    ledger_seq: Mapped[int] = mapped_column(BigInteger)
    evidence_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    previous_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    canonical_event_body: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(Text)
    record_batch_id: Mapped[str] = mapped_column(String(36))
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LedgerCheckpoint(Base):
    __tablename__ = "evidence_ledger_checkpoint"
    checkpoint_id: Mapped[str] = _id()
    cell_id: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[datetime] = mapped_column(TS)
    window_end: Mapped[datetime] = mapped_column(TS)
    leaf_count: Mapped[int] = mapped_column(Integer)
    leaves: Mapped[list] = mapped_column(JSON)  # [{tenant_id, head_seq, head_hash, leaf}]
    merkle_algorithm: Mapped[str] = mapped_column(String(64))
    merkle_root: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    cell_signature: Mapped[str] = mapped_column(Text)
    signer_key_id: Mapped[str] = mapped_column(String(64))
    tsa_status: Mapped[str] = mapped_column(String(32))
    tsa_note: Mapped[str | None] = mapped_column(Text)
    worm_object_ref: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvidenceBinding(Base):
    __tablename__ = "evidence_binding_revision"
    binding_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    evidence_id: Mapped[str] = mapped_column(String(36), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(36))
    rationale: Mapped[str | None] = mapped_column(Text)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class EvidenceConflict(Base):
    __tablename__ = "evidence_conflict"
    conflict_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    member_evidence_ids: Mapped[list] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    disposition: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvidenceConflictEvent(Base):
    __tablename__ = "evidence_conflict_event"
    event_id: Mapped[str] = _id()
    conflict_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # OPENED, RESOLVED, BASELINE
    status: Mapped[str] = mapped_column(String(32))      # OPEN, RESOLVED
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    member_evidence_ids: Mapped[list] = mapped_column(JSON)
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LegalHold(Base):
    __tablename__ = "evidence_legal_hold_revision"
    hold_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    evidence_id: Mapped[str] = mapped_column(String(36), index=True)
    authority: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    placed_by: Mapped[str] = mapped_column(String(36))
    placed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(TS)


class LegalHoldEvent(Base):
    __tablename__ = "evidence_legal_hold_event"
    event_id: Mapped[str] = _id()
    hold_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    evidence_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # HOLD_PLACED, HOLD_RELEASED, BASELINE
    status: Mapped[str] = mapped_column(String(32))      # ACTIVE, RELEASED
    authority: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    record_seq: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvidencePackage(Base):
    __tablename__ = "evidence_package"
    package_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str] = mapped_column(String(36))
    period_from: Mapped[datetime] = mapped_column(TS)
    period_to: Mapped[datetime] = mapped_column(TS)
    record_cutoff_seq: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[dict] = mapped_column(JSON)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    signer_key_id: Mapped[str] = mapped_column(String(64))
    file_ref: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="SIGNED")
    verification_report: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- integration / IPE
class ConnectorDefinition(Base):
    __tablename__ = "integration_connector_definition"
    connector_id: Mapped[str] = _id()
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(120))
    source_class: Mapped[str] = mapped_column(String(48))
    build_version: Mapped[str] = mapped_column(String(32))
    release_availability: Mapped[str] = mapped_column(String(32), default="PRIVATE_BETA")
    certification: Mapped[str] = mapped_column(String(32), default="UNASSESSED")
    delivery_model: Mapped[str] = mapped_column(String(32), default="STANDARD")
    runtime_health: Mapped[str] = mapped_column(String(32), default="HEALTHY")
    supported_objects: Mapped[list] = mapped_column(JSON, default=list)
    certification_matrix: Mapped[dict] = mapped_column(JSON, default=dict)
    known_limitations: Mapped[list] = mapped_column(JSON, default=list)


class ConnectorInstallation(Base):
    __tablename__ = "integration_connector_installation_revision"
    installation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str] = mapped_column(String(36))
    connector_id: Mapped[str] = mapped_column(String(36))
    granted_scopes: Mapped[list] = mapped_column(JSON)
    credential_ref: Mapped[str] = mapped_column(String(120))  # vault reference only, never secret bytes
    schedule_days: Mapped[int] = mapped_column(Integer, default=1)
    last_run_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class ExtractionContractVersion(Base):
    __tablename__ = "integration_extraction_contract_version"
    extraction_contract_version_id: Mapped[str] = _id()
    connector_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    source_object: Mapped[str] = mapped_column(String(120))
    endpoint_id: Mapped[str] = mapped_column(String(200))
    query_template: Mapped[str] = mapped_column(Text)
    record_identity: Mapped[str] = mapped_column(String(80))
    required_scopes: Mapped[list] = mapped_column(JSON)
    ordering: Mapped[str] = mapped_column(String(80))
    consistency_strategy: Mapped[str] = mapped_column(String(40))
    pagination_method: Mapped[str] = mapped_column(String(80))
    population_proof_methods: Mapped[list] = mapped_column(JSON)
    known_limitations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="CERTIFIED")


class MappingContractVersion(Base):
    __tablename__ = "integration_mapping_contract_version"
    mapping_contract_version_id: Mapped[str] = _id()
    connector_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    source_schema_fingerprint: Mapped[str] = mapped_column(String(64))
    source_fields: Mapped[list] = mapped_column(JSON)
    field_mappings: Mapped[list] = mapped_column(JSON)  # [{source, target, type, transform, enum_map?}]
    relied_upon_fields: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="CERTIFIED")


class IPEPolicyVersion(Base):
    __tablename__ = "evidence_ipe_policy_version"
    ipe_policy_version_id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text)
    completeness_required: Mapped[bool] = mapped_column(Boolean, default=True)
    required_completeness_checks: Mapped[list] = mapped_column(JSON)
    accuracy_required: Mapped[bool] = mapped_column(Boolean, default=True)
    required_accuracy_checks: Mapped[list] = mapped_column(JSON)
    allowed_exclusions: Mapped[list] = mapped_column(JSON, default=list)
    allow_partial: Mapped[bool] = mapped_column(Boolean, default=False)


class SourceRun(Base):
    __tablename__ = "evidence_source_run"
    source_run_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_id: Mapped[str] = mapped_column(String(36))
    installation_id: Mapped[str] = mapped_column(String(36))
    connector_build: Mapped[str] = mapped_column(String(32))
    extraction_contract_version_id: Mapped[str] = mapped_column(String(36))
    mapping_contract_version_id: Mapped[str] = mapped_column(String(36))
    ipe_policy_version_id: Mapped[str] = mapped_column(String(36))
    actual_scopes: Mapped[list] = mapped_column(JSON)
    request_parameters: Mapped[dict] = mapped_column(JSON)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    consistency_boundary: Mapped[dict | None] = mapped_column(JSON)
    lifecycle: Mapped[str] = mapped_column(String(32))
    run_outcome: Mapped[str] = mapped_column(String(32))
    pages: Mapped[list] = mapped_column(JSON, default=list)
    coverage_from: Mapped[datetime] = mapped_column(TS)
    coverage_to: Mapped[datetime] = mapped_column(TS)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    record_seq: Mapped[int] = mapped_column(BigInteger)


class PopulationManifest(Base):
    __tablename__ = "evidence_population_manifest"
    population_manifest_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_run_id: Mapped[str] = mapped_column(String(36), index=True)
    population_definition: Mapped[str] = mapped_column(Text)
    scope_manifest: Mapped[dict] = mapped_column(JSON)
    consistency_boundary: Mapped[dict | None] = mapped_column(JSON)
    source_reported_total: Mapped[int | None] = mapped_column(BigInteger)
    extracted_raw_count: Mapped[int] = mapped_column(BigInteger)
    unique_identity_count: Mapped[int] = mapped_column(BigInteger)
    duplicate_count: Mapped[int] = mapped_column(BigInteger)
    rejected_count: Mapped[int] = mapped_column(BigInteger)
    excluded_count: Mapped[int] = mapped_column(BigInteger)
    terminal_state: Mapped[dict] = mapped_column(JSON)
    source_checksum_refs: Mapped[list] = mapped_column(JSON, default=list)  # source-provided only
    assure_population_digest: Mapped[str] = mapped_column(String(64))  # Assure-generated, labeled separately
    assure_raw_response_digest: Mapped[str] = mapped_column(String(64))
    mapping_output_digest: Mapped[str] = mapped_column(String(64))
    digest_algorithm: Mapped[str] = mapped_column(String(64), default="SHA-256/canonical-sorted-identity-v1")
    duplicate_keys: Mapped[list] = mapped_column(JSON, default=list)
    rejected_keys: Mapped[list] = mapped_column(JSON, default=list)


class IPEEvaluation(Base):
    __tablename__ = "evidence_ipe_evaluation"
    ipe_evaluation_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_run_id: Mapped[str] = mapped_column(String(36), index=True)
    ipe_policy_version_id: Mapped[str] = mapped_column(String(36))
    mapping_contract_version_id: Mapped[str] = mapped_column(String(36))
    lifecycle: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    completeness_result: Mapped[str] = mapped_column(String(32))
    accuracy_result: Mapped[str] = mapped_column(String(32))
    overall_result: Mapped[str] = mapped_column(String(32))
    checks: Mapped[list] = mapped_column(JSON)
    reason_codes: Mapped[list] = mapped_column(JSON)
    engine_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class IPEAnomaly(Base):
    __tablename__ = "evidence_ipe_anomaly"
    anomaly_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_run_id: Mapped[str] = mapped_column(String(36), index=True)
    ipe_evaluation_id: Mapped[str] = mapped_column(String(36))
    check_id: Mapped[str] = mapped_column(String(64))
    anomaly_class: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(32))
    affected_count: Mapped[int] = mapped_column(BigInteger, default=0)
    affected_keys: Mapped[list] = mapped_column(JSON, default=list)
    severity: Mapped[str] = mapped_column(String(16), default="HIGH")
    disposition_code: Mapped[str | None] = mapped_column(String(48))
    disposition_rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- AI / agents
class AIAssetVersion(Base):
    __tablename__ = "ai_asset_version"
    version_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_subject_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_kind: Mapped[str] = mapped_column(String(48))
    version_label: Mapped[str] = mapped_column(String(48))
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(120))
    environment: Mapped[str] = mapped_column(String(64))
    owner_actor_id: Mapped[str | None] = mapped_column(String(36))
    agbom: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuthorityGrant(Base):
    __tablename__ = "ai_authority_grant"
    grant_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_subject_id: Mapped[str] = mapped_column(String(36), index=True)
    action_class: Mapped[str] = mapped_column(String(120))
    authority_level: Mapped[str] = mapped_column(String(8))  # A0..A4
    scope: Mapped[str] = mapped_column(String(200))
    reversibility: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(TS)
    granted_by: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    fail_mode: Mapped[str] = mapped_column(String(16), default="FAIL_CLOSED")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PolicyDecisionReceipt(Base):
    __tablename__ = "ai_policy_decision_receipt"
    receipt_id: Mapped[str] = _id()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_subject_id: Mapped[str] = mapped_column(String(36))
    action_class: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(String(16))
    grant_id: Mapped[str | None] = mapped_column(String(36))
    obligation_refs: Mapped[list] = mapped_column(JSON, default=list)
    request_digest: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


Index("ix_fact_current", FactAssertion.tenant_id, FactAssertion.subject_id,
      FactAssertion.fact_definition_id, FactAssertion.record_to_seq)
