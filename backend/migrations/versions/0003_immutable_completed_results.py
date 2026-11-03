"""Completed results are immutable in the database, not only by application convention (audit I-02; roadmap Stage 2c).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

Two independent layers, so that neither alone is load-bearing:

1. Privileges. The runtime role ``assure_app`` loses ``UPDATE`` and ``DELETE`` on these tables and keeps ``SELECT`` and
   ``INSERT`` (it never had ``TRUNCATE``; ZA-DAT-001 §13). Where a code path still legitimately completes a row in
   place, the role keeps ``UPDATE`` on exactly those columns.
2. A ``BEFORE UPDATE OR DELETE`` row trigger. It also stops DML by the owner role and by any role granted more than it
   should be (SEC-REQ-029: ordinary security/admin roles must not rewrite completed records). It is not a barrier
   against the table owner itself, which can disable the trigger with DDL (audit §12, finding A10).

Every table below was checked insert-only in the code, apart from the listed column writes (write-path inventory in
docs/backend-architecture-audit.md §12). No data, RLS policy or column changes. Revision tables that are still
overwritten in place (audit I-04) are not included: they follow once Stage 3 replaces those writes with events.

Downgrade restores exactly the 0002 privileges and removes the triggers and the new function; completed results are then
writable by the runtime role again. For rehearsals and disposable databases (docs/database-migrations.md).
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "assure_app"

# Completed results: no UPDATE or DELETE at all.
IMMUTABLE = {
    "applicability_evaluation": "DAT-REQ-011, APP-REQ-012, API-REQ-019",
    "assurance_control_evaluation": "DAT-REQ-011, STA-REQ-019",
    "assurance_snapshot": "DAT-REQ-011, STA-REQ-019, TIM-REQ-015/016",
    "evidence_ipe_evaluation": "IPE-REQ-025, IPE-INV-11",
    "evidence_package": "EVD-REQ-026 (created SIGNED; a change is a new package)",
    "governance_attestation": "DAT-REQ-036, API-REQ-019",
    "evidence_ledger_checkpoint": "EVD-REQ-009 (append-only integrity store)",
}
# Append-only by trigger since 0002; this revision adds the privilege layer.
APPEND_ONLY_SINCE_0002 = {
    "evidence_ledger_event": "EVD-REQ-002",
    "platform_audit_event": "audit history (IAM-REQ-023, SEC-REQ-035)",
}
# A legitimate code path still writes these columns in place; every other column is immutable and rows are never deleted.
GUARDED = {
    # Run completion, same transaction as the capture (engines/ipe.py). Version bindings and provenance stay fixed.
    "evidence_source_run": (("lifecycle", "completed_at", "evidence_ids"), "IPE-INV-11, DAT-REQ-035"),
    # Governed disposition (routers/integration.py); the original check result stays fixed. Interim: the spec wants an
    # appended disposition event (IPE-REQ-034), which is Stage 3 work with the other in-place writes (audit I-04).
    "evidence_ipe_anomaly": (("disposition_code", "disposition_rationale"), "IPE-REQ-025, IPE-REQ-034"),
}

GUARD_FUNCTION_SQL = r"""
CREATE FUNCTION assure_forbid_mutation_except() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
  END IF;
  -- TG_ARGV names the columns that may still change; any other difference is a rewrite of a completed record.
  IF (to_jsonb(OLD) - TG_ARGV) IS DISTINCT FROM (to_jsonb(NEW) - TG_ARGV) THEN
    RAISE EXCEPTION '% is append-only except %', TG_TABLE_NAME, array_to_string(TG_ARGV, ', ');
  END IF;
  RETURN NEW;
END $$"""


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor: `%` in PL/pgSQL stays literal
        cur.execute(sql)


def _script(stmts: list[str]) -> str:
    return "".join(f"{s};\n" for s in stmts)


def upgrade() -> None:
    stmts = [GUARD_FUNCTION_SQL.strip()]
    for t in (*IMMUTABLE, *APPEND_ONLY_SINCE_0002, *GUARDED):
        stmts.append(f"REVOKE UPDATE, DELETE ON TABLE {t} FROM {RUNTIME_ROLE}")
    for t in IMMUTABLE:
        stmts.append(f"CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {t} "
                     "FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation()")
    for t, (cols, _) in GUARDED.items():
        stmts.append(f"GRANT UPDATE ({', '.join(cols)}) ON TABLE {t} TO {RUNTIME_ROLE}")
        stmts.append(f"CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {t} "
                     f"FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation_except({', '.join(map(repr, cols))})")
    _execute(_script(stmts))


def downgrade() -> None:
    stmts = [f"DROP TRIGGER IF EXISTS immutable_columns ON {t}" for t in GUARDED]
    stmts += [f"DROP TRIGGER IF EXISTS append_only ON {t}" for t in IMMUTABLE]
    stmts.append("DROP FUNCTION IF EXISTS assure_forbid_mutation_except()")
    stmts += [f"REVOKE UPDATE ({', '.join(cols)}) ON TABLE {t} FROM {RUNTIME_ROLE}" for t, (cols, _) in GUARDED.items()]
    stmts += [f"GRANT UPDATE, DELETE ON TABLE {t} TO {RUNTIME_ROLE}" for t in (*IMMUTABLE, *APPEND_ONLY_SINCE_0002, *GUARDED)]
    _execute(_script(stmts))
