"""Governance state projections: lifecycle columns only, no DELETE, final states frozen (pre-push remediation).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-02

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Finding (pre-push audit, 2 November 2026): migration 0008 made the six governance *event* tables append-only but left
their current-state projections fully writable: the runtime role (assure_app via assure_app_rw) held table-wide UPDATE
and DELETE, so a legal hold, an exception decision or an issue could be rewritten or deleted without an event, and
nothing below the application stopped it (DAT-REQ-009/037, STA-REQ-027, EVD-REQ-018, SEC-REQ-029; roadmap Stage 3
acceptance "no governance status column is written outside its event writer: grep gate plus database grants").

Each table was checked against every write path in the code (app/engines/governance.py is the only writer; nothing
deletes these rows). For each table this migration:

1. Revokes table-wide UPDATE and DELETE from assure_app_rw and grants UPDATE on exactly the lifecycle columns its event
   writer changes. INSERT and SELECT are unchanged; RLS (ENABLE + FORCE, tenant_isolation) is not touched.
2. Adds ``immutable_columns`` (``assure_forbid_mutation_except``, migration 0003): every other column is immutable and
   DELETE is refused, for every role including the owner, below the privilege layer.
3. Adds ``lifecycle_final`` (``assure_freeze_final``, new; named to fire after ``immutable_columns``, so a
   change to a protected column is always reported as such): once a row reaches a terminal state of its lifecycle it cannot
   change again, matching the engine's state machines (a RELEASED hold, a RESOLVED conflict, a fulfilled occurrence,
   a REJECTED/REVOKED exception or compensation, a CLOSED/CANCELLED issue).

The owner can still disable a trigger with DDL (audit finding A10, open). Downgrade restores 0009 exactly.
"""
from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_RW = "assure_app_rw"
# table -> the columns its event writer updates in place (app/engines/governance.py)
LIFECYCLE = {
    "assurance_exception_revision": ("state", "approved_by"),         # decide_exception
    "assurance_compensation_revision": ("status", "approved_by"),     # decide_compensation
    "assurance_issue": ("state", "owner_actor_id"),                   # transition_issue
    "evidence_legal_hold_revision": ("status", "released_at"),        # release_legal_hold
    "evidence_conflict": ("status", "disposition"),                   # resolve_evidence_conflict
    "assurance_obligation_occurrence": ("fulfilled_at", "evidence_ids"),  # fulfil_occurrence
}
# table -> (state column, terminal values...); "*" = any non-null value is terminal
FINAL = {
    "assurance_exception_revision": ("state", "REJECTED", "REVOKED"),
    "assurance_compensation_revision": ("status", "REJECTED", "REVOKED"),
    "assurance_issue": ("state", "CLOSED", "CANCELLED"),
    "evidence_legal_hold_revision": ("status", "RELEASED"),
    "evidence_conflict": ("status", "RESOLVED"),
    "assurance_obligation_occurrence": ("fulfilled_at", "*"),
}

FREEZE_FUNCTION = r"""
CREATE FUNCTION {s}.assure_freeze_final() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  v text := to_jsonb(OLD) ->> TG_ARGV[0];
BEGIN
  -- TG_ARGV[0] names the lifecycle column; the remaining arguments are its terminal values ('*': any non-null value).
  IF v IS NOT NULL AND (TG_ARGV[1] = '*' OR v = ANY (TG_ARGV[1:TG_NARGS - 1]))
     AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
    RAISE EXCEPTION '% is final once % is %', TG_TABLE_NAME, TG_ARGV[0], v;
  END IF;
  RETURN NEW;
END $$"""


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0009: `%` stays literal
        cur.execute(sql)


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    s = '"' + _schema().replace('"', '""') + '"'
    stmts = [FREEZE_FUNCTION.format(s=s),
             f"ALTER FUNCTION {s}.assure_freeze_final() OWNER TO assure_table_owner"]
    for t, cols in LIFECYCLE.items():
        stmts += [
            f"REVOKE UPDATE, DELETE ON TABLE {s}.{t} FROM {APP_RW}",
            f"GRANT UPDATE ({', '.join(cols)}) ON TABLE {s}.{t} TO {APP_RW}",
            f"CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {s}.{t} FOR EACH ROW "
            f"EXECUTE FUNCTION {s}.assure_forbid_mutation_except({', '.join(map(_lit, cols))})",
            f"CREATE TRIGGER lifecycle_final BEFORE UPDATE ON {s}.{t} FOR EACH ROW "
            f"EXECUTE FUNCTION {s}.assure_freeze_final({', '.join(map(_lit, FINAL[t]))})",
        ]
    _execute(";\n".join(stmts) + ";")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    s = '"' + _schema().replace('"', '""') + '"'
    stmts = []
    for t, cols in LIFECYCLE.items():
        stmts += [f"DROP TRIGGER IF EXISTS lifecycle_final ON {s}.{t}",
                  f"DROP TRIGGER IF EXISTS immutable_columns ON {s}.{t}",
                  f"REVOKE UPDATE ({', '.join(cols)}) ON TABLE {s}.{t} FROM {APP_RW}",
                  f"GRANT UPDATE, DELETE ON TABLE {s}.{t} TO {APP_RW}"]
    stmts.append(f"DROP FUNCTION IF EXISTS {s}.assure_freeze_final()")
    _execute(";\n".join(stmts) + ";")
