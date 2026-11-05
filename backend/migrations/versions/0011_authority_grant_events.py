"""Agent authority grants: append-only event history, guarded projection, immutable decision receipts (Stage 3c).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-03

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.
DAT-REQ-039 requires a security/data/QA review of this revision before it is applied outside development.

Finding (3 November 2026, checked against the live development database at 0010): the runtime role held table-wide
UPDATE and DELETE on ``ai_authority_grant`` and ``ai_policy_decision_receipt`` with no row trigger. A revoked agent grant
could therefore be set back to ACTIVE, have its scope, level or expiry widened, or be deleted, and a decision receipt
could be rewritten, all without any record. Revocation also wrote no audit record and no outbox event.

Requirements (specification text, ZA-IAM-001 / ZA-EVT-001 / ZA-DAT-001 / ZA-SEC-001):
- IAM-INV-09: authorisation policies and role/grant changes are versioned and auditable.
- IAM-REQ-030: agent authority is an explicit grant per action class + scope + expiry + authority level.
- IAM-REQ-015 / IAM-INV-07: revoking a grant invalidates new authorisation immediately; IAM §05: prior grants are not
  automatically restored.
- IAM §21: ``authority_grant.issued`` / ``revoked`` carry grant, action/authority, scope, expiry, grantor/approver.
- DAT-REQ-037: ordinary workflows do not hard-delete governed history. SEC-REQ-029: completed records keep their
  immutability protections. DAT-REQ-027 / DAT §13: least privilege; no runtime TRUNCATE.

This revision:
1. Creates ``ai_authority_grant_event`` (tenant-scoped): ENABLE + FORCE RLS, the 0007 ``tenant_isolation`` predicate,
   ``migrator_all``, owner ``assure_table_owner``, runtime SELECT/INSERT only, and the ``append_only`` trigger.
2. On ``ai_authority_grant``: revokes table-wide UPDATE and DELETE from ``assure_app_rw`` and grants UPDATE (status)
   only; ``immutable_columns`` (every other column immutable, DELETE refused, for every role) and ``lifecycle_final``
   (REVOKED is final) row triggers, reusing the 0003 and 0010 functions.
3. On ``ai_policy_decision_receipt``: revokes UPDATE and DELETE (no code path updates a receipt) and adds
   ``append_only``.

No backfill. Existing grants carry no observed record time (the table has no ``record_seq``), so an ISSUED event built
from them would fabricate Assure record time (DAT-REQ-024). Their history begins with the first event written after
this revision. Downgrade restores 0010 exactly; it drops the event table and so loses post-migration grant history,
which is acceptable only before production (roadmap Stage 3, rollback).

DAT-REQ-039 review correction (3 November 2026, R039-02; this revision had not been applied outside disposable test
databases): CHECK ``ck_ai_authority_grant_level`` holds ``authority_level`` to the ZA-DOM-001 §10.2 vocabulary A0-A4.
An out-of-vocabulary row made every policy decision for that agent and action fail. A4 stays refused by the API
(ZA-ARCH-001: "excluded from initial production scope"). A database holding another value fails this upgrade, which
rolls back.

The owner can still disable a trigger with DDL (audit finding A10, open).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

GRANT, EVENT, RECEIPT = "ai_authority_grant", "ai_authority_grant_event", "ai_policy_decision_receipt"
APP_RW = "assure_app_rw"
TENANT_PREDICATE = "tenant_id = (SELECT assure_current_tenant()) OR (SELECT assure_system())"  # as 0007-0009
LEVEL_CHECK = "authority_level IN ('A0', 'A1', 'A2', 'A3', 'A4')"  # ZA-DOM-001 §10.2


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0010: `%` stays literal
        cur.execute(sql)


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.create_table(
        EVENT,
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("grant_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("agent_subject_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("action_class", sa.String(length=120), nullable=False),
        sa.Column("authority_level", sa.String(length=8), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_ai_authority_grant_event_grant_id"), EVENT, ["grant_id"], unique=False)
    op.create_index(op.f("ix_ai_authority_grant_event_record_seq"), EVENT, ["record_seq"], unique=False)
    op.create_index(op.f("ix_ai_authority_grant_event_tenant_id"), EVENT, ["tenant_id"], unique=False)
    op.create_check_constraint("ck_ai_authority_grant_level", GRANT, LEVEL_CHECK)  # R039-02

    if op.get_bind().dialect.name != "postgresql":
        return
    s = '"' + _schema().replace('"', '""') + '"'
    tenant = _lit(TENANT_PREDICATE)
    _execute(f"""
-- 1. the event table: owner, FORCE RLS, tenant policy, least-privilege grants, append-only rows
ALTER TABLE {s}.{EVENT} OWNER TO assure_table_owner;
ALTER TABLE {s}.{EVENT} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.{EVENT} FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  EXECUTE format('CREATE POLICY tenant_isolation ON {s}.{EVENT} TO assure_app_rw, assure_app_ro USING (%s) WITH CHECK (%s)',
                 {tenant}, {tenant});
END $$;
CREATE POLICY migrator_all ON {s}.{EVENT} TO assure_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON TABLE {s}.{EVENT} TO {APP_RW};
GRANT SELECT ON TABLE {s}.{EVENT} TO assure_app_ro;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {s}.{EVENT}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation();

-- 2. the grant projection: status only, never deleted, REVOKED is final
REVOKE UPDATE, DELETE ON TABLE {s}.{GRANT} FROM {APP_RW};
GRANT UPDATE (status) ON TABLE {s}.{GRANT} TO {APP_RW};
CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {s}.{GRANT}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation_except('status');
CREATE TRIGGER lifecycle_final BEFORE UPDATE ON {s}.{GRANT}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_freeze_final('status', 'REVOKED');

-- 3. decision receipts are completed results
REVOKE UPDATE, DELETE ON TABLE {s}.{RECEIPT} FROM {APP_RW};
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {s}.{RECEIPT}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation();
""")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        s = '"' + _schema().replace('"', '""') + '"'
        _execute(f"""
DROP TRIGGER IF EXISTS append_only ON {s}.{RECEIPT};
GRANT UPDATE, DELETE ON TABLE {s}.{RECEIPT} TO {APP_RW};
DROP TRIGGER IF EXISTS lifecycle_final ON {s}.{GRANT};
DROP TRIGGER IF EXISTS immutable_columns ON {s}.{GRANT};
REVOKE UPDATE (status) ON TABLE {s}.{GRANT} FROM {APP_RW};
GRANT UPDATE, DELETE ON TABLE {s}.{GRANT} TO {APP_RW};
DROP TRIGGER IF EXISTS append_only ON {s}.{EVENT};
""")  # the event table's policies and grants go with it
    op.drop_constraint("ck_ai_authority_grant_level", GRANT, type_="check")
    op.drop_index(op.f("ix_ai_authority_grant_event_tenant_id"), table_name=EVENT)
    op.drop_index(op.f("ix_ai_authority_grant_event_record_seq"), table_name=EVENT)
    op.drop_index(op.f("ix_ai_authority_grant_event_grant_id"), table_name=EVENT)
    op.drop_table(EVENT)
