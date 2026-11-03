"""Row-level security, runtime-role grants and append-only triggers: the SQL the API used to apply at every start-up.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

This is the former ``backend/db/postgres_rls.sql``, moved here unchanged (statements below the original header). It
runs through a raw DBAPI cursor, as the old start-up did, so ``%`` in PL/pgSQL stays literal. Behaviour is the same
as before Stage 1: 41 ``tenant_isolation`` policies (ENABLE, not FORCE), the ``assure_system()`` scope, the
SELECT/INSERT/UPDATE/DELETE grant to ``assure_app`` and the two append-only triggers. Changing any of that is Stage 2
of ``docs/backend-architecture-roadmap.md`` and happens in later revisions.

One addition, needed for migration correctness only: the runtime role gets no privilege on ``alembic_version``. On a
fresh database that table exists before the ``GRANT ... ON ALL TABLES`` below runs; on a database adopted from the old
start-up it did not. Revoking makes both identical, and the API role cannot read or alter the migration state.

Downgrade removes the policies, functions, triggers and runtime grants, so tenant isolation is OFF afterwards. It
exists for disposable databases and rehearsals; production recovery is forward (docs/database-migrations.md).
"""
from __future__ import annotations

import re

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "assure_app"  # the policies below name this role; DATABASE_URL must use it

# The former db/postgres_rls.sql, from its first statement on. Its original header said:
# Zoiko Assure — PostgreSQL row-level security (ZA-ARCH-001 §5, INV-14).
# Applied automatically on every API start-up by app/pg_bootstrap.py using the OWNER role
# (DATABASE_ADMIN_URL); idempotent. The API connects as the non-owner role `assure_app`
# (DATABASE_URL), so these policies always apply to request traffic.
# 
# Each transaction is stamped by app/db.py with:
# app.tenant_id = <caller's tenant>   ('' for content/platform staff -> no tenant rows)
# app.system    = 'on' only inside documented cell-wide platform jobs (app/security.py:system_scope)
# The owner role is not subject to RLS (no FORCE) so migrations and demo seeding work.
# org_actor is intentionally not RLS-protected: sign-in must resolve an identity before a tenant is known.
#
RLS_SQL = r"""CREATE OR REPLACE FUNCTION assure_current_tenant() RETURNS text
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.tenant_id', true), '') $$;
CREATE OR REPLACE FUNCTION assure_system() RETURNS boolean
  LANGUAGE sql STABLE AS $$ SELECT coalesce(current_setting('app.system', true), 'off') = 'on' $$;

-- ---------------------------------------------------------------- tenant-scoped tables

ALTER TABLE ai_asset_version ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ai_asset_version;
CREATE POLICY tenant_isolation ON ai_asset_version TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE ai_authority_grant ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ai_authority_grant;
CREATE POLICY tenant_isolation ON ai_authority_grant TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE ai_policy_decision_receipt ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ai_policy_decision_receipt;
CREATE POLICY tenant_isolation ON ai_policy_decision_receipt TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_determination_challenge ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_determination_challenge;
CREATE POLICY tenant_isolation ON applicability_determination_challenge TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_determination_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_determination_revision;
CREATE POLICY tenant_isolation ON applicability_determination_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_evaluation ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_evaluation;
CREATE POLICY tenant_isolation ON applicability_evaluation TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_fact_conflict ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_fact_conflict;
CREATE POLICY tenant_isolation ON applicability_fact_conflict TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_fact_request ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_fact_request;
CREATE POLICY tenant_isolation ON applicability_fact_request TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_compensation_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_compensation_revision;
CREATE POLICY tenant_isolation ON assurance_compensation_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_control_evaluation ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_control_evaluation;
CREATE POLICY tenant_isolation ON assurance_control_evaluation TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_control_test_policy_version ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_control_test_policy_version;
CREATE POLICY tenant_isolation ON assurance_control_test_policy_version TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_exception_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_exception_revision;
CREATE POLICY tenant_isolation ON assurance_exception_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_issue ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_issue;
CREATE POLICY tenant_isolation ON assurance_issue TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_obligation_instance_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_obligation_instance_revision;
CREATE POLICY tenant_isolation ON assurance_obligation_instance_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_obligation_occurrence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_obligation_occurrence;
CREATE POLICY tenant_isolation ON assurance_obligation_occurrence TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_satisfaction_expression_version ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_satisfaction_expression_version;
CREATE POLICY tenant_isolation ON assurance_satisfaction_expression_version TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_snapshot ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_snapshot;
CREATE POLICY tenant_isolation ON assurance_snapshot TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_test_definition_version ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_test_definition_version;
CREATE POLICY tenant_isolation ON assurance_test_definition_version TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_test_run ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_test_run;
CREATE POLICY tenant_isolation ON assurance_test_run TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_artifact ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_artifact;
CREATE POLICY tenant_isolation ON evidence_artifact TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_binding_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_binding_revision;
CREATE POLICY tenant_isolation ON evidence_binding_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_conflict ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_conflict;
CREATE POLICY tenant_isolation ON evidence_conflict TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_ipe_anomaly ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_ipe_anomaly;
CREATE POLICY tenant_isolation ON evidence_ipe_anomaly TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_ipe_evaluation ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_ipe_evaluation;
CREATE POLICY tenant_isolation ON evidence_ipe_evaluation TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_ledger_event ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_ledger_event;
CREATE POLICY tenant_isolation ON evidence_ledger_event TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_legal_hold_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_legal_hold_revision;
CREATE POLICY tenant_isolation ON evidence_legal_hold_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_package ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_package;
CREATE POLICY tenant_isolation ON evidence_package TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_population_manifest ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_population_manifest;
CREATE POLICY tenant_isolation ON evidence_population_manifest TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_source_run ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_source_run;
CREATE POLICY tenant_isolation ON evidence_source_run TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE evidence_tenant_ledger_head ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_tenant_ledger_head;
CREATE POLICY tenant_isolation ON evidence_tenant_ledger_head TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE governance_attestation ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON governance_attestation;
CREATE POLICY tenant_isolation ON governance_attestation TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE governance_risk_acceptance_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON governance_risk_acceptance_revision;
CREATE POLICY tenant_isolation ON governance_risk_acceptance_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE integration_connector_installation_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON integration_connector_installation_revision;
CREATE POLICY tenant_isolation ON integration_connector_installation_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE platform_idempotency_record ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON platform_idempotency_record;
CREATE POLICY tenant_isolation ON platform_idempotency_record TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE applicability_fact_assertion_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON applicability_fact_assertion_revision;
CREATE POLICY tenant_isolation ON applicability_fact_assertion_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE assurance_control_implementation_revision ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assurance_control_implementation_revision;
CREATE POLICY tenant_isolation ON assurance_control_implementation_revision TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE org_legal_entity ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON org_legal_entity;
CREATE POLICY tenant_isolation ON org_legal_entity TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE org_subject_registry ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON org_subject_registry;
CREATE POLICY tenant_isolation ON org_subject_registry TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id = assure_current_tenant() OR assure_system());

-- platform logs: tenant_id NULL rows are platform/content-operations records (no customer data)

ALTER TABLE platform_audit_event ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON platform_audit_event;
CREATE POLICY tenant_isolation ON platform_audit_event TO assure_app
  USING (tenant_id IS NULL OR tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id IS NULL OR tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE platform_outbox_event ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON platform_outbox_event;
CREATE POLICY tenant_isolation ON platform_outbox_event TO assure_app
  USING (tenant_id IS NULL OR tenant_id = assure_current_tenant() OR assure_system())
  WITH CHECK (tenant_id IS NULL OR tenant_id = assure_current_tenant() OR assure_system());

ALTER TABLE org_tenant ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON org_tenant;
CREATE POLICY tenant_isolation ON org_tenant TO assure_app
  USING (tenant_id = assure_current_tenant() OR assure_system());

-- ---------------------------------------------------------------- append-only ledgers (EVD-REQ-005)
CREATE OR REPLACE FUNCTION assure_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS append_only ON evidence_ledger_event;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON evidence_ledger_event
  FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation();
DROP TRIGGER IF EXISTS append_only ON platform_audit_event;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON platform_audit_event
  FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation();

-- ---------------------------------------------------------------- bitemporal integrity (ZA-TIM-001)
-- Concurrent assertions from different provenance classes may legitimately overlap in valid time
-- (resolution picks between them), so no exclusion constraint is placed on fact revisions.

-- ---------------------------------------------------------------- runtime role privileges
GRANT USAGE ON SCHEMA public TO assure_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO assure_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO assure_app;
"""

POLICY_TABLES = re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", RLS_SQL)
TRIGGER_TABLES = re.findall(r"BEFORE UPDATE OR DELETE ON (\w+)", RLS_SQL)


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor: no parameter parsing
        cur.execute(sql)


def upgrade() -> None:
    schema = _schema()
    # Identical bytes for the real schema; only the throwaway schemas of the migration tests are substituted.
    _execute(RLS_SQL if schema == "public" else RLS_SQL.replace("SCHEMA public", f'SCHEMA "{schema}"'))
    _execute(f'REVOKE ALL ON TABLE "{schema}".alembic_version FROM {RUNTIME_ROLE}')


def downgrade() -> None:
    schema = _schema()
    stmts = [f"DROP TRIGGER IF EXISTS append_only ON {t}" for t in TRIGGER_TABLES]
    stmts.append("DROP FUNCTION IF EXISTS assure_forbid_mutation()")
    for t in POLICY_TABLES:
        stmts += [f"DROP POLICY IF EXISTS tenant_isolation ON {t}", f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY"]
    stmts += ["DROP FUNCTION IF EXISTS assure_current_tenant()", "DROP FUNCTION IF EXISTS assure_system()",
              f'REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" FROM {RUNTIME_ROLE}',
              f'REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" FROM {RUNTIME_ROLE}',
              f'REVOKE USAGE ON SCHEMA "{schema}" FROM {RUNTIME_ROLE}']
    _execute("".join(f"{s};\n" for s in stmts))
