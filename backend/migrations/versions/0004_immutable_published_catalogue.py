"""Published catalogue artefacts are immutable in the database (DAT-REQ-012; RUL-REQ-009; RUL-REQ-014).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tenant tables carry RLS and explicit
runtime-role grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.

ZA-DAT-001 lists, under immutability: "Runtime role cannot update/delete completed evaluations/snapshots/traces or
PUBLISHED catalogue artifacts", and its data doctrine is to enforce such invariants in the database rather than by
developer discipline. Revision 0003 covered the completed results. This revision covers the binding catalogue:

* catalog_rule_version: every row is a PUBLISHED RuleVersion ("PUBLISHED immutable"; RUL-REQ-009, RUL-REQ-010)
* catalog_content_release: the signed release manifest ("Cells verify before activation"; RUL-REQ-027)
* catalog_controlled_set_version: SetVersions pinned by hash in the release (RUL-REQ-014)
* catalog_fact_definition: every row is a RELEASED fact definition ("Released version immutable"; APP-REQ-005)

Each was checked insert-only. Publication used to point the new RuleVersion at its release with an in-place update;
routers/rules.py and app/seed.py now insert both rows with their final values. The same two layers as 0003: the
runtime role keeps SELECT and INSERT only, and a row trigger (0002's assure_forbid_mutation) stops UPDATE and DELETE by
every role, the owner included. The table owner can still disable the trigger with DDL (audit §12, finding A10).

Not covered: catalogue rows whose lifecycle still changes in place (source records, provisions, requirement
versions; audit I-04). They need an append-only lifecycle first (Stage 3c).

Downgrade restores the 0003 privileges exactly and removes the triggers.
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "assure_app"

PUBLISHED_CATALOGUE = {
    "catalog_rule_version": "DAT-REQ-012, RUL-REQ-009, RUL-REQ-010",
    "catalog_content_release": "DAT-REQ-012, RUL-REQ-027",
    "catalog_controlled_set_version": "DAT-REQ-012, RUL-REQ-014",
    "catalog_fact_definition": "DAT-REQ-012, APP-REQ-005",
}


def _execute(sql: str) -> None:
    if op.get_context().as_sql:  # `--sql` review output
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002 and 0003
        cur.execute(sql)


def _script(stmts: list[str]) -> str:
    return "".join(f"{s};\n" for s in stmts)


def upgrade() -> None:
    stmts = []
    for t in PUBLISHED_CATALOGUE:
        stmts.append(f"REVOKE UPDATE, DELETE ON TABLE {t} FROM {RUNTIME_ROLE}")
        stmts.append(f"CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {t} "
                     "FOR EACH ROW EXECUTE FUNCTION assure_forbid_mutation()")
    _execute(_script(stmts))


def downgrade() -> None:
    stmts = [f"DROP TRIGGER IF EXISTS append_only ON {t}" for t in PUBLISHED_CATALOGUE]
    stmts += [f"GRANT UPDATE, DELETE ON TABLE {t} TO {RUNTIME_ROLE}" for t in PUBLISHED_CATALOGUE]
    _execute(_script(stmts))
