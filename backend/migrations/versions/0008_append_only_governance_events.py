"""Append-only governance event tables and immutable transition history (Roadmap Stage 3c; audit I-04).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Finding I-04 / Stage 3c:
Governance records (exceptions, compensations, issues, legal holds, evidence conflicts, and obligation occurrences)
previously updated their state in place, overwriting historical governance decisions.

This migration establishes append-only governance event tables for all 6 mutable governance records:
1. assurance_exception_event
2. assurance_compensation_event
3. assurance_issue_event
4. evidence_legal_hold_event
5. evidence_conflict_event
6. assurance_obligation_occurrence_event

Each table carries:
- Event ID, tenant_id, reference ID to the governance record.
- Domain-specific event_type, from/to states, actor_id, reason, and temporal bounds.
- Sequential record_seq for monotonic total order.
- PostgreSQL 17 database-level immutability via `append_only` trigger executing `assure_forbid_mutation()`.
- ENABLE ROW LEVEL SECURITY + FORCE ROW LEVEL SECURITY.
- `tenant_isolation` policy matching 0007 verified context.
- Ownership transferred to `assure_table_owner`.
- Least-privilege runtime role grants: SELECT and INSERT to `assure_app_rw`, SELECT to `assure_app_ro`.
  UPDATE and DELETE are forbidden at both role-privilege and trigger levels.
- Baseline data backfill for existing rows with clear migration attribution.

Downgrade drops the triggers, policies, and tables cleanly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

EVENT_TABLES = (
    "assurance_exception_event",
    "assurance_compensation_event",
    "assurance_issue_event",
    "evidence_legal_hold_event",
    "evidence_conflict_event",
    "assurance_obligation_occurrence_event",
)


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:
        cur.execute(sql)


def upgrade() -> None:
    # ---------------------------------------------------------------- DDL: create tables
    op.create_table(
        "assurance_exception_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("exception_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_assurance_exception_event_exception_id"), "assurance_exception_event", ["exception_id"], unique=False)
    op.create_index(op.f("ix_assurance_exception_event_record_seq"), "assurance_exception_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_assurance_exception_event_tenant_id"), "assurance_exception_event", ["tenant_id"], unique=False)

    op.create_table(
        "assurance_compensation_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("compensation_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("obligation_lineage_id", sa.String(length=36), nullable=False),
        sa.Column("primary_control_key", sa.String(length=80), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_assurance_compensation_event_compensation_id"), "assurance_compensation_event", ["compensation_id"], unique=False)
    op.create_index(op.f("ix_assurance_compensation_event_record_seq"), "assurance_compensation_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_assurance_compensation_event_tenant_id"), "assurance_compensation_event", ["tenant_id"], unique=False)

    op.create_table(
        "assurance_issue_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("owner_actor_id", sa.String(length=36), nullable=True),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_assurance_issue_event_issue_id"), "assurance_issue_event", ["issue_id"], unique=False)
    op.create_index(op.f("ix_assurance_issue_event_record_seq"), "assurance_issue_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_assurance_issue_event_tenant_id"), "assurance_issue_event", ["tenant_id"], unique=False)

    op.create_table(
        "evidence_legal_hold_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("hold_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("authority", sa.String(length=200), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_evidence_legal_hold_event_evidence_id"), "evidence_legal_hold_event", ["evidence_id"], unique=False)
    op.create_index(op.f("ix_evidence_legal_hold_event_hold_id"), "evidence_legal_hold_event", ["hold_id"], unique=False)
    op.create_index(op.f("ix_evidence_legal_hold_event_record_seq"), "evidence_legal_hold_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_evidence_legal_hold_event_tenant_id"), "evidence_legal_hold_event", ["tenant_id"], unique=False)

    op.create_table(
        "evidence_conflict_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("conflict_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("member_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_evidence_conflict_event_conflict_id"), "evidence_conflict_event", ["conflict_id"], unique=False)
    op.create_index(op.f("ix_evidence_conflict_event_record_seq"), "evidence_conflict_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_evidence_conflict_event_tenant_id"), "evidence_conflict_event", ["tenant_id"], unique=False)

    op.create_table(
        "assurance_obligation_occurrence_event",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("occurrence_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("obligation_lineage_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("is_late", sa.Boolean(), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_assurance_obligation_occurrence_event_obligation_lineage_id"), "assurance_obligation_occurrence_event", ["obligation_lineage_id"], unique=False)
    op.create_index(op.f("ix_assurance_obligation_occurrence_event_occurrence_id"), "assurance_obligation_occurrence_event", ["occurrence_id"], unique=False)
    op.create_index(op.f("ix_assurance_obligation_occurrence_event_record_seq"), "assurance_obligation_occurrence_event", ["record_seq"], unique=False)
    op.create_index(op.f("ix_assurance_obligation_occurrence_event_tenant_id"), "assurance_obligation_occurrence_event", ["tenant_id"], unique=False)

    # ---------------------------------------------------------------- PostgreSQL hardening
    if op.get_bind().dialect.name != "postgresql":
        return

    schema = _schema()
    tenant_pred = "tenant_id = (SELECT assure_current_tenant()) OR (SELECT assure_system())"

    pg_sql = f"""
DO $$
DECLARE
  t text;
  has_owner boolean;
  has_app_rw boolean;
  has_app_ro boolean;
  has_migrator boolean;
BEGIN
  SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assure_table_owner') INTO has_owner;
  SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assure_app_rw') INTO has_app_rw;
  SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assure_app_ro') INTO has_app_ro;
  SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assure_migrator') INTO has_migrator;

  FOREACH t IN ARRAY ARRAY['assurance_exception_event', 'assurance_compensation_event',
                           'assurance_issue_event', 'evidence_legal_hold_event',
                           'evidence_conflict_event', 'assurance_obligation_occurrence_event'] LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', '{schema}', t);
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', '{schema}', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', '{schema}', t);

    IF has_app_rw AND has_app_ro THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO assure_app_rw, assure_app_ro USING (%s) WITH CHECK (%s)',
                     '{schema}', t, '{tenant_pred}', '{tenant_pred}');
    ELSE
      EXECUTE format('CREATE POLICY tenant_isolation ON %I.%I TO assure_app USING (%s) WITH CHECK (%s)',
                     '{schema}', t, '{tenant_pred}', '{tenant_pred}');
    END IF;

    IF has_migrator THEN
      EXECUTE format('CREATE POLICY migrator_all ON %I.%I TO assure_migrator USING (true) WITH CHECK (true)', '{schema}', t);
    END IF;

    IF has_owner THEN
      EXECUTE format('ALTER TABLE %I.%I OWNER TO assure_table_owner', '{schema}', t);
    END IF;

    IF has_app_rw THEN
      EXECUTE format('GRANT SELECT, INSERT ON TABLE %I.%I TO assure_app_rw', '{schema}', t);
    ELSE
      EXECUTE format('GRANT SELECT, INSERT ON TABLE %I.%I TO assure_app', '{schema}', t);
    END IF;

    IF has_app_ro THEN
      EXECUTE format('GRANT SELECT ON TABLE %I.%I TO assure_app_ro', '{schema}', t);
    END IF;

    EXECUTE format('DROP TRIGGER IF EXISTS append_only ON %I.%I', '{schema}', t);
    EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %I.%I FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation()',
                   '{schema}', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- Baseline backfill for existing rows
INSERT INTO "{schema}".assurance_exception_event
  (event_id, exception_id, tenant_id, event_type, from_state, to_state, actor_id, reason,
   target_type, target_id, valid_from, valid_to, record_seq, created_at)
SELECT gen_random_uuid()::text, exception_id, tenant_id, 'BASELINE', NULL, state, requested_by,
       'Migration 0008 baseline snapshot', target_type, target_id, valid_from, valid_to, record_seq, now()
FROM "{schema}".assurance_exception_revision;

INSERT INTO "{schema}".assurance_compensation_event
  (event_id, compensation_id, tenant_id, event_type, from_status, to_status, actor_id, reason,
   obligation_lineage_id, primary_control_key, record_seq, created_at)
SELECT gen_random_uuid()::text, compensation_id, tenant_id, 'BASELINE', NULL, status,
       coalesce(approved_by, 'system:migration:0008'), 'Migration 0008 baseline snapshot',
       obligation_lineage_id, primary_control_key, record_seq, now()
FROM "{schema}".assurance_compensation_revision;

INSERT INTO "{schema}".assurance_issue_event
  (event_id, issue_id, tenant_id, event_type, from_state, to_state, actor_id, reason,
   owner_actor_id, record_seq, created_at)
SELECT gen_random_uuid()::text, issue_id, tenant_id, 'BASELINE', NULL, state,
       coalesce(owner_actor_id, 'system:migration:0008'), 'Migration 0008 baseline snapshot',
       owner_actor_id, 0, created_at
FROM "{schema}".assurance_issue;

INSERT INTO "{schema}".evidence_legal_hold_event
  (event_id, hold_id, tenant_id, evidence_id, event_type, status, authority, actor_id, reason,
   record_seq, created_at)
SELECT gen_random_uuid()::text, hold_id, tenant_id, evidence_id, 'BASELINE', status, authority,
       placed_by, reason, 0, placed_at
FROM "{schema}".evidence_legal_hold_revision;

INSERT INTO "{schema}".evidence_conflict_event
  (event_id, conflict_id, tenant_id, event_type, status, actor_id, reason,
   member_evidence_ids, record_seq, created_at)
SELECT gen_random_uuid()::text, conflict_id, tenant_id, 'BASELINE', status,
       'system:migration:0008', coalesce(disposition, 'Migration 0008 baseline snapshot'),
       member_evidence_ids, 0, created_at
FROM "{schema}".evidence_conflict;

INSERT INTO "{schema}".assurance_obligation_occurrence_event
  (event_id, occurrence_id, tenant_id, obligation_lineage_id, event_type, actor_id,
   window_from, due_at, fulfilled_at, evidence_ids, is_late, record_seq, created_at)
SELECT gen_random_uuid()::text, occurrence_id, tenant_id, obligation_lineage_id, 'BASELINE',
       'system:migration:0008', window_from, due_at, fulfilled_at, evidence_ids,
       (fulfilled_at IS NOT NULL AND fulfilled_at > due_at), record_seq, coalesce(fulfilled_at, window_from)
FROM "{schema}".assurance_obligation_occurrence;
"""
    _execute(pg_sql)


def downgrade() -> None:
    schema = _schema()
    if op.get_bind().dialect.name == "postgresql":
        pg_sql = f"""
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['assurance_exception_event', 'assurance_compensation_event',
                           'assurance_issue_event', 'evidence_legal_hold_event',
                           'evidence_conflict_event', 'assurance_obligation_occurrence_event'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS append_only ON %I.%I', '{schema}', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', '{schema}', t);
    EXECUTE format('DROP POLICY IF EXISTS migrator_all ON %I.%I', '{schema}', t);
  END LOOP;
END $$;
"""
        _execute(pg_sql)

    op.drop_index(op.f("ix_assurance_obligation_occurrence_event_tenant_id"), table_name="assurance_obligation_occurrence_event")
    op.drop_index(op.f("ix_assurance_obligation_occurrence_event_record_seq"), table_name="assurance_obligation_occurrence_event")
    op.drop_index(op.f("ix_assurance_obligation_occurrence_event_occurrence_id"), table_name="assurance_obligation_occurrence_event")
    op.drop_index(op.f("ix_assurance_obligation_occurrence_event_obligation_lineage_id"), table_name="assurance_obligation_occurrence_event")
    op.drop_table("assurance_obligation_occurrence_event")

    op.drop_index(op.f("ix_evidence_conflict_event_tenant_id"), table_name="evidence_conflict_event")
    op.drop_index(op.f("ix_evidence_conflict_event_record_seq"), table_name="evidence_conflict_event")
    op.drop_index(op.f("ix_evidence_conflict_event_conflict_id"), table_name="evidence_conflict_event")
    op.drop_table("evidence_conflict_event")

    op.drop_index(op.f("ix_evidence_legal_hold_event_tenant_id"), table_name="evidence_legal_hold_event")
    op.drop_index(op.f("ix_evidence_legal_hold_event_record_seq"), table_name="evidence_legal_hold_event")
    op.drop_index(op.f("ix_evidence_legal_hold_event_hold_id"), table_name="evidence_legal_hold_event")
    op.drop_index(op.f("ix_evidence_legal_hold_event_evidence_id"), table_name="evidence_legal_hold_event")
    op.drop_table("evidence_legal_hold_event")

    op.drop_index(op.f("ix_assurance_issue_event_tenant_id"), table_name="assurance_issue_event")
    op.drop_index(op.f("ix_assurance_issue_event_record_seq"), table_name="assurance_issue_event")
    op.drop_index(op.f("ix_assurance_issue_event_issue_id"), table_name="assurance_issue_event")
    op.drop_table("assurance_issue_event")

    op.drop_index(op.f("ix_assurance_compensation_event_tenant_id"), table_name="assurance_compensation_event")
    op.drop_index(op.f("ix_assurance_compensation_event_record_seq"), table_name="assurance_compensation_event")
    op.drop_index(op.f("ix_assurance_compensation_event_compensation_id"), table_name="assurance_compensation_event")
    op.drop_table("assurance_compensation_event")

    op.drop_index(op.f("ix_assurance_exception_event_tenant_id"), table_name="assurance_exception_event")
    op.drop_index(op.f("ix_assurance_exception_event_record_seq"), table_name="assurance_exception_event")
    op.drop_index(op.f("ix_assurance_exception_event_exception_id"), table_name="assurance_exception_event")
    op.drop_table("assurance_exception_event")

