"""Global connector certification registry: append-only history, guarded registry row, platform-only writes (Stage 3c).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-03

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tables carry RLS and explicit runtime-role
grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.
DAT-REQ-039 requires a security/data/QA review of this revision before it is applied outside development.
Design and open decisions: docs/connector-certification-history-design.md.

Finding (3 November 2026, live development database at 0010): ``integration_connector_definition`` (the registry every
organisation reads; ZA-GLS-001 §07, ZA-DAT-001 §10) had no RLS and no row trigger, and the runtime role held table-wide
UPDATE and DELETE on it. Any runtime context, a tenant request included, could rewrite or delete a connector's
certification at the database layer, and no history of certification changes existed.

This revision:
1. Creates ``integration_connector_certification_event`` (global, no tenant): ENABLE + FORCE RLS; SELECT and INSERT only in
   a verified platform-principal context; runtime SELECT/INSERT privileges only; the ``append_only`` trigger.
2. On ``integration_connector_definition``: ENABLE + FORCE RLS. Every runtime context may read (the registry is global);
   UPDATE only in a verified platform-principal context; no runtime INSERT (review correction R039-03). Revokes table-wide UPDATE and DELETE from
   ``assure_app_rw`` and grants UPDATE (certification, runtime_health) only; the ``immutable_columns`` trigger (0003
   function) makes every other column immutable and refuses DELETE, for every role.

"Verified platform principal" reuses the 0007 signed-context predicates, as the 0005/0007 platform-row policies do: a
verified context with no tenant and an authenticated actor (``assure_platform_reader()``), and not system scope. Which
platform role the actor holds is checked by the application (``certify_connectors``). Tenant RLS does not apply: the rows
belong to no organisation.

No transition rule and no terminal-state trigger: no controlled document defines certification transitions (design D1).
No backfill: past changes were never recorded and have no record time (DAT-REQ-024). Downgrade restores 0011 exactly.
DAT-REQ-039 review correction (3 November 2026, R039-03; not applied outside disposable test databases): no runtime
path creates registry rows (the seed runs as the migration role), so the runtime role loses INSERT on the registry
(DAT-REQ-027 least privilege). A platform-principal context could otherwise create a CERTIFIED connector directly,
with no matrix check (FR-CON-002) and no history. The ``registry_platform_insert`` policy is therefore not created.

The owner can still disable triggers with DDL (audit A10, open).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

REGISTRY, EVENT = "integration_connector_definition", "integration_connector_certification_event"
APP_RW, APP_RO = "assure_app_rw", "assure_app_ro"
PLATFORM_PRINCIPAL = "(SELECT assure_platform_reader()) AND NOT (SELECT assure_system())"
MUTABLE = ("certification", "runtime_health")


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0011: `%` stays literal
        cur.execute(sql)


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.create_table(
        EVENT,
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("build_version", sa.String(length=32), nullable=False),
        sa.Column("from_certification", sa.String(length=32), nullable=False),
        sa.Column("to_certification", sa.String(length=32), nullable=False),
        sa.Column("from_runtime_health", sa.String(length=32), nullable=False),
        sa.Column("to_runtime_health", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("prior_event_id", sa.String(length=36), nullable=True),
        sa.Column("record_seq", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(op.f("ix_integration_connector_certification_event_connector_id"), EVENT, ["connector_id"],
                    unique=False)
    op.create_index(op.f("ix_integration_connector_certification_event_record_seq"), EVENT, ["record_seq"], unique=False)

    if op.get_bind().dialect.name != "postgresql":
        return
    s = '"' + _schema().replace('"', '""') + '"'
    p = PLATFORM_PRINCIPAL
    _execute(f"""
-- 1. history: global, platform principals only, append-only
ALTER TABLE {s}.{EVENT} OWNER TO assure_table_owner;
ALTER TABLE {s}.{EVENT} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.{EVENT} FORCE ROW LEVEL SECURITY;
CREATE POLICY registry_history_read ON {s}.{EVENT} FOR SELECT TO {APP_RW}, {APP_RO} USING ({p});
CREATE POLICY registry_history_insert ON {s}.{EVENT} FOR INSERT TO {APP_RW} WITH CHECK ({p});
CREATE POLICY migrator_all ON {s}.{EVENT} TO assure_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON TABLE {s}.{EVENT} TO {APP_RW};
GRANT SELECT ON TABLE {s}.{EVENT} TO {APP_RO};
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {s}.{EVENT}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation();

-- 2. the registry: readable by every runtime context, writable only by a verified platform principal
ALTER TABLE {s}.{REGISTRY} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.{REGISTRY} FORCE ROW LEVEL SECURITY;
CREATE POLICY registry_read ON {s}.{REGISTRY} FOR SELECT TO {APP_RW}, {APP_RO} USING (true);
CREATE POLICY registry_platform_update ON {s}.{REGISTRY} FOR UPDATE TO {APP_RW} USING ({p}) WITH CHECK ({p});
CREATE POLICY migrator_all ON {s}.{REGISTRY} TO assure_migrator USING (true) WITH CHECK (true);
REVOKE INSERT, UPDATE, DELETE ON TABLE {s}.{REGISTRY} FROM {APP_RW};
GRANT UPDATE ({", ".join(MUTABLE)}) ON TABLE {s}.{REGISTRY} TO {APP_RW};
CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {s}.{REGISTRY}
  FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation_except({", ".join(map(_lit, MUTABLE))});
""")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        s = '"' + _schema().replace('"', '""') + '"'
        _execute(f"""
DROP TRIGGER IF EXISTS immutable_columns ON {s}.{REGISTRY};
REVOKE UPDATE ({", ".join(MUTABLE)}) ON TABLE {s}.{REGISTRY} FROM {APP_RW};
GRANT INSERT, UPDATE, DELETE ON TABLE {s}.{REGISTRY} TO {APP_RW};
DROP POLICY IF EXISTS migrator_all ON {s}.{REGISTRY};
DROP POLICY IF EXISTS registry_platform_update ON {s}.{REGISTRY};
DROP POLICY IF EXISTS registry_read ON {s}.{REGISTRY};
ALTER TABLE {s}.{REGISTRY} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {s}.{REGISTRY} DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS append_only ON {s}.{EVENT};
""")  # the history table's policies and grants go with it
    op.drop_index(op.f("ix_integration_connector_certification_event_record_seq"), table_name=EVENT)
    op.drop_index(op.f("ix_integration_connector_certification_event_connector_id"), table_name=EVENT)
    op.drop_table(EVENT)
