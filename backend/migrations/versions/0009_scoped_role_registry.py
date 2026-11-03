"""Scoped role registry: role assignments with explicit scope and validity, and their append-only history (Stage 4b).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Roadmap Stage 4b, audit finding P-01 ("static roles, no scoped role assignments"). ZA-IAM-001 §02 RoleAssignment
("principal + role template + scope + valid time + approval/source"), §22 ``iam.role_assignment_revision``
("Bitemporal; revocation creates successor/closure"), IAM-REQ-010 (explicit scope), IAM-INV-09 (role changes
versioned and auditable).

1. ``iam_role_assignment``: one row per assignment. CHECK constraints make the scope explicit and consistent: PLATFORM
   rows have no tenant; TENANT rows name their own tenant; LEGAL_ENTITY rows name an entity. The runtime role may
   INSERT, and UPDATE only the closure columns (status, valid_to, revoked_*); a ``assure_forbid_mutation_except``
   trigger enforces the same below the privilege layer, and nothing may DELETE.
2. ``iam_role_assignment_event``: append-only (``assure_forbid_mutation``), SELECT/INSERT for the runtime role.
3. Both: ENABLE + FORCE ROW LEVEL SECURITY; ``tenant_isolation`` and ``migrator_all`` exactly as 0007 defines them;
   Zoiko platform rows (tenant_id NULL) are readable only by a verified platform principal and never writable by the
   runtime role (no API grants a platform role; ZA-IAM-001 WF-IAM-01). Owned by ``assure_table_owner``.
4. Backfill (DAT-REQ-024, never fabricating history): every role in ``org_actor.roles`` becomes an ACTIVE assignment
   at TENANT scope (tenant principals) or PLATFORM scope (Zoiko staff), valid from this migration, source MIGRATION,
   with an ASSIGNED event attributed to ``system:migration:0009``. Nothing before the migration is invented: when or by
   whom those roles were first granted was never recorded, and the reason says so. Each row takes a new cell record
   sequence number.

``org_actor.roles`` stays as a cache that the application keeps equal to each principal's current organisation- or
platform-wide roles, so downgrading restores the previous authorisation behaviour with the latest roles; entity-scoped
assignments, which the old code cannot express, are dropped (fail closed).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ASSIGNMENT, EVENT = "iam_role_assignment", "iam_role_assignment_event"
CLOSURE_COLUMNS = ("status", "valid_to", "revoked_at", "revoked_by", "revoke_reason", "revoked_record_seq")
SCOPE_CHECK = ("(scope_type = 'PLATFORM' AND tenant_id IS NULL AND scope_id IS NULL)"
               " OR (scope_type = 'TENANT' AND tenant_id IS NOT NULL AND scope_id = tenant_id)"
               " OR (scope_type = 'LEGAL_ENTITY' AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)")
TENANT_PREDICATE = "tenant_id = (SELECT assure_current_tenant()) OR (SELECT assure_system())"  # as 0007


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0008: `%` stays literal
        cur.execute(sql)


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.create_table(
        ASSIGNMENT,
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("granted_by", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=36), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("revoked_record_seq", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(SCOPE_CHECK, name="ck_iam_role_assignment_scope"),
        sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_iam_role_assignment_status"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_iam_role_assignment_period"),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index(op.f("ix_iam_role_assignment_principal_id"), ASSIGNMENT, ["principal_id"], unique=False)
    op.create_index(op.f("ix_iam_role_assignment_record_seq"), ASSIGNMENT, ["record_seq"], unique=False)
    op.create_index(op.f("ix_iam_role_assignment_tenant_id"), ASSIGNMENT, ["tenant_id"], unique=False)

    op.create_table(
        EVENT,
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("authorized_by", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_iam_role_assignment_event_assignment_id"), EVENT, ["assignment_id"], unique=False)
    op.create_index(op.f("ix_iam_role_assignment_event_principal_id"), EVENT, ["principal_id"], unique=False)
    op.create_index(op.f("ix_iam_role_assignment_event_record_seq"), EVENT, ["record_seq"], unique=False)
    op.create_index(op.f("ix_iam_role_assignment_event_tenant_id"), EVENT, ["tenant_id"], unique=False)

    if op.get_bind().dialect.name != "postgresql":
        return
    from app.db import CELL_ID  # the cell whose record sequence the backfill draws from (same default as the API)
    s = '"' + _schema().replace('"', '""') + '"'
    tenant = _lit(TENANT_PREDICATE)
    sql = f"""
-- 1-3. ownership, RLS (FORCE), policies, least-privilege grants and row guards
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['{ASSIGNMENT}', '{EVENT}'] LOOP
    EXECUTE format('ALTER TABLE {s}.%I OWNER TO assure_table_owner', t);
    EXECUTE format('ALTER TABLE {s}.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE {s}.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON {s}.%I TO assure_app_rw, assure_app_ro USING (%s) WITH CHECK (%s)',
                   t, {tenant}, {tenant});
    EXECUTE format('CREATE POLICY migrator_all ON {s}.%I TO assure_migrator USING (true) WITH CHECK (true)', t);
    EXECUTE format('CREATE POLICY platform_rows_select ON {s}.%I FOR SELECT TO assure_app_rw, assure_app_ro '
                   'USING (tenant_id IS NULL AND (SELECT assure_platform_reader()))', t);
    EXECUTE format('GRANT SELECT, INSERT ON TABLE {s}.%I TO assure_app_rw', t);
    EXECUTE format('GRANT SELECT ON TABLE {s}.%I TO assure_app_ro', t);
  END LOOP;
END $$;
GRANT UPDATE ({", ".join(CLOSURE_COLUMNS)}) ON TABLE {s}.{ASSIGNMENT} TO assure_app_rw;
CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {s}.{ASSIGNMENT}
  FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation_except({", ".join(map(_lit, CLOSURE_COLUMNS))});
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {s}.{EVENT}
  FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation();

-- 4. backfill from org_actor.roles: one ACTIVE assignment per role, valid from now, each with its own record sequence
INSERT INTO {s}.platform_record_sequence (cell_id, current_seq)
  SELECT {_lit(CELL_ID)}, 0 WHERE EXISTS (SELECT 1 FROM {s}.org_actor WHERE roles IS NOT NULL
                                           AND json_typeof(roles) = 'array' AND json_array_length(roles) > 0)
  ON CONFLICT (cell_id) DO NOTHING;
WITH src AS (
  SELECT a.actor_id, a.tenant_id, r.role_code,
         row_number() OVER (ORDER BY a.tenant_id NULLS FIRST, a.actor_id, r.role_code) AS n
  FROM {s}.org_actor a
  CROSS JOIN LATERAL json_array_elements_text(
    CASE WHEN a.roles IS NOT NULL AND json_typeof(a.roles) = 'array' THEN a.roles ELSE '[]'::json END) AS r(role_code)
), bump AS (
  UPDATE {s}.platform_record_sequence SET current_seq = current_seq + (SELECT count(*) FROM src)
  WHERE cell_id = {_lit(CELL_ID)} RETURNING current_seq - (SELECT count(*) FROM src) AS base
)
INSERT INTO {s}.{ASSIGNMENT} (assignment_id, tenant_id, principal_id, role_code, scope_type, scope_id, valid_from,
                              valid_to, status, source, granted_by, reason, record_seq, recorded_at)
SELECT gen_random_uuid()::text, src.tenant_id, src.actor_id, src.role_code,
       CASE WHEN src.tenant_id IS NULL THEN 'PLATFORM' ELSE 'TENANT' END, src.tenant_id, now(), NULL, 'ACTIVE',
       'MIGRATION', NULL,
       'Migrated from org_actor.roles by migration 0009; when and by whom this role was first granted was not recorded',
       bump.base + src.n, now()
FROM src CROSS JOIN bump;
INSERT INTO {s}.{EVENT} (event_id, tenant_id, assignment_id, principal_id, event_type, role_code, scope_type, scope_id,
                         valid_from, valid_to, source, actor_id, reason, authorized_by, policy_version, record_seq,
                         recorded_at)
SELECT gen_random_uuid()::text, a.tenant_id, a.assignment_id, a.principal_id, 'ASSIGNED', a.role_code, a.scope_type,
       a.scope_id, a.valid_from, a.valid_to, a.source, 'system:migration:0009', a.reason, '[]'::json,
       'migration-0009', a.record_seq, a.recorded_at
FROM {s}.{ASSIGNMENT} a WHERE a.source = 'MIGRATION';
"""
    _execute(sql)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        s = '"' + _schema().replace('"', '""') + '"'
        _execute(f"""
DROP TRIGGER IF EXISTS immutable_columns ON {s}.{ASSIGNMENT};
DROP TRIGGER IF EXISTS append_only ON {s}.{EVENT};
""")  # the tables' policies and grants go with them
    op.drop_index(op.f("ix_iam_role_assignment_event_tenant_id"), table_name=EVENT)
    op.drop_index(op.f("ix_iam_role_assignment_event_record_seq"), table_name=EVENT)
    op.drop_index(op.f("ix_iam_role_assignment_event_principal_id"), table_name=EVENT)
    op.drop_index(op.f("ix_iam_role_assignment_event_assignment_id"), table_name=EVENT)
    op.drop_table(EVENT)
    op.drop_index(op.f("ix_iam_role_assignment_tenant_id"), table_name=ASSIGNMENT)
    op.drop_index(op.f("ix_iam_role_assignment_record_seq"), table_name=ASSIGNMENT)
    op.drop_index(op.f("ix_iam_role_assignment_principal_id"), table_name=ASSIGNMENT)
    op.drop_table(ASSIGNMENT)
