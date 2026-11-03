"""Platform (no-tenant) audit and outbox rows are invisible and unwritable inside a tenant scope (ZA-DAT-001 §12, §16).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Defect (docs/backend-current-state-and-wireframe-gap-audit.md §10, "No-tenant event visibility"): 0002 gave
``platform_audit_event`` and ``platform_outbox_event`` one policy that admits ``tenant_id IS NULL`` in both USING and
WITH CHECK. Every organisation could therefore read the cell's platform records, including the SECURITY_CRITICAL
``security.content_signature_failed`` events of a failed publication (ZA-EVT-001 §19: not customer-visible), and could
insert, update or delete rows with no tenant, so forging or suppressing platform security records needed no more than
a tenant session.

After this revision each table has three permissive policies for the runtime role, one per purpose:

* ``tenant_isolation`` (ALL commands): ``tenant_id = assure_current_tenant() OR assure_system()``. The same predicate
  as the other 39 tenant tables, so a tenant sees and writes only its own rows.
* ``platform_rows_select`` (SELECT) and ``platform_rows_insert`` (INSERT): rows with no tenant, only while the
  transaction carries no tenant scope (Zoiko platform/content staff and pre-authentication sign-in). No policy lets an
  ordinary scope UPDATE or DELETE a no-tenant row.

``assure_system()`` is unchanged: removing it is the system-job design decision of Task 1 (audit I-01). FORCE ROW LEVEL
SECURITY is not added here either; it needs the Task 1 owner/role separation (DAT §13). Table grants are unchanged.

Downgrade restores the 0002 policies exactly.
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "assure_app"
PLATFORM_LOG_TABLES = ("platform_audit_event", "platform_outbox_event")

TENANT_ROWS = "tenant_id = assure_current_tenant() OR assure_system()"
PLATFORM_ROWS = "tenant_id IS NULL AND assure_current_tenant() IS NULL"
POLICY_0002 = "tenant_id IS NULL OR tenant_id = assure_current_tenant() OR assure_system()"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0004
        cur.execute(sql)


def _script(stmts: list[str]) -> str:
    return "".join(f"{s};\n" for s in stmts)


def upgrade() -> None:
    stmts = []
    for t in PLATFORM_LOG_TABLES:
        stmts += [f"DROP POLICY tenant_isolation ON {t}",
                  f"CREATE POLICY tenant_isolation ON {t} TO {RUNTIME_ROLE} USING ({TENANT_ROWS}) WITH CHECK ({TENANT_ROWS})",
                  f"CREATE POLICY platform_rows_select ON {t} FOR SELECT TO {RUNTIME_ROLE} USING ({PLATFORM_ROWS})",
                  f"CREATE POLICY platform_rows_insert ON {t} FOR INSERT TO {RUNTIME_ROLE} WITH CHECK ({PLATFORM_ROWS})"]
    _execute(_script(stmts))


def downgrade() -> None:
    stmts = []
    for t in PLATFORM_LOG_TABLES:
        stmts += [f"DROP POLICY IF EXISTS platform_rows_insert ON {t}",
                  f"DROP POLICY IF EXISTS platform_rows_select ON {t}",
                  f"DROP POLICY tenant_isolation ON {t}",
                  f"CREATE POLICY tenant_isolation ON {t} TO {RUNTIME_ROLE} USING ({POLICY_0002}) WITH CHECK ({POLICY_0002})"]
    _execute(_script(stmts))
