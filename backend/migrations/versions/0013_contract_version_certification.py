"""Version-level connector certification (D2): contract versions become immutable certification subjects (Stage 3c).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-03

Review checklist (docs/database-migrations.md): expand/contract only; preserves historical reconstruction; RLS, temporal
constraints, controlled codes and immutable tables reviewed (DAT-REQ-039); new tables carry RLS and explicit runtime-role
grants; downgrade tested, or documented as irreversible with its forward-recovery procedure.
DAT-REQ-039 requires a security/data/QA review of this revision before it is applied outside development.
Design and open decisions: docs/connector-version-certification-design.md.

Requirements (specification text): IPE-REQ-028 (certification versioned by connector build, source API/report,
ExtractionContract and MappingContract compatibility); IPE-REQ-029 (suspension/recertification of affected contract
versions); ZA-IPE-001 §04 ("Immutable version referenced by SourceRun") and §11 ("Immutable version bound to connector
build/source schema version"); IPE-REQ-002 (SourceRun references the exact versions); ZA-GLS-001 §07 certification codes.

Finding (live development database at 0010): both contract-version tables had no RLS, no row trigger, runtime UPDATE and
DELETE, no (connector_id, version) uniqueness and no foreign keys; evidence_source_run referenced them without foreign keys.

This revision:
1. integration_extraction_contract_version, integration_mapping_contract_version: UNIQUE (connector_id, version); FOREIGN KEY
   connector_id -> integration_connector_definition; CHECK status in the GLS certification codes; ENABLE + FORCE RLS (read
   for every runtime context; UPDATE only for a verified platform principal, as 0012); runtime UPDATE (status) only,
   no INSERT (review correction R039-03), no DELETE; immutable_columns trigger (status excepted).
2. evidence_source_run: FOREIGN KEYs to both contract tables (validated against existing rows; a dangling reference fails
   the migration and rolls it back).
3. integration_connector_certification_event (0012), expand only: subject_type (default CONNECTOR_DEFINITION) and
   subject_id; runtime-health columns nullable; a CHECK binding subject and health. Existing rows are not rewritten.

Not decided here (open, design D2-e..i): build identity/hash/range, envelope composition, registry relationship, effect
on runs, per-version evidence. No build identifier is invented; no history is backfilled (DAT-REQ-024).
Downgrade restores 0012 exactly; it refuses if contract-level history rows exist (they would violate 0012's NOT NULL).

DAT-REQ-039 review correction (3 November 2026, R039-03; not applied outside disposable test databases): no runtime path
creates contract versions (the seed runs as the migration role), so the runtime role loses INSERT on both tables
(DAT-REQ-027). A platform-principal context could otherwise create a CERTIFIED contract version with no matrix check
and no history. The ``contract_platform_insert`` policy is therefore not created.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

EXT, MAP = "integration_extraction_contract_version", "integration_mapping_contract_version"
RUN, EVENT, REGISTRY = "evidence_source_run", "integration_connector_certification_event", "integration_connector_definition"
APP_RW, APP_RO = "assure_app_rw", "assure_app_ro"
PLATFORM_PRINCIPAL = "(SELECT assure_platform_reader()) AND NOT (SELECT assure_system())"  # as 0012
CERT_CHECK = "status IN ('UNASSESSED', 'CERTIFIED', 'SUSPENDED', 'REVOKED')"
SUBJECT_CHECK = (
    "(subject_type = 'CONNECTOR_DEFINITION' AND subject_id IS NULL AND from_runtime_health IS NOT NULL "
    "AND to_runtime_health IS NOT NULL) OR (subject_type IN ('EXTRACTION_CONTRACT_VERSION', 'MAPPING_CONTRACT_VERSION') "
    "AND subject_id IS NOT NULL AND from_runtime_health IS NULL AND to_runtime_health IS NULL)")
CONTRACTS = ((EXT, "extraction_contract", "extraction_contract_version_id"),
             (MAP, "mapping_contract", "mapping_contract_version_id"))


def _schema() -> str:
    return op.get_context().version_table_schema or "public"


def _execute(sql: str) -> None:
    if op.get_context().as_sql:
        op.execute(sql)
        return
    with op.get_bind().connection.cursor() as cur:  # raw DBAPI cursor, as in 0002-0012: `%` stays literal
        cur.execute(sql)


def upgrade() -> None:
    # identity and integrity (both dialects)
    for table, short, _pk in CONTRACTS:
        op.create_unique_constraint(f"uq_{short}_connector_version", table, ["connector_id", "version"])
        op.create_foreign_key(f"fk_{short}_connector", table, REGISTRY, ["connector_id"], ["connector_id"])
        op.create_check_constraint(f"ck_{short}_certification", table, CERT_CHECK)
    op.create_foreign_key("fk_source_run_extraction_contract", RUN, EXT, ["extraction_contract_version_id"],
                          ["extraction_contract_version_id"])
    op.create_foreign_key("fk_source_run_mapping_contract", RUN, MAP, ["mapping_contract_version_id"],
                          ["mapping_contract_version_id"])
    # history: expand only; existing rows take the default subject and keep their values
    op.add_column(EVENT, sa.Column("subject_type", sa.String(length=32), nullable=False,
                                   server_default="CONNECTOR_DEFINITION"))
    op.add_column(EVENT, sa.Column("subject_id", sa.String(length=36), nullable=True))
    op.alter_column(EVENT, "from_runtime_health", existing_type=sa.String(length=32), nullable=True)
    op.alter_column(EVENT, "to_runtime_health", existing_type=sa.String(length=32), nullable=True)
    op.create_check_constraint("ck_connector_certification_event_subject", EVENT, SUBJECT_CHECK)

    if op.get_bind().dialect.name != "postgresql":
        return
    s = '"' + _schema().replace('"', '""') + '"'
    p = PLATFORM_PRINCIPAL
    stmts = []
    for table, _short, _pk in CONTRACTS:
        stmts += [
            f"ALTER TABLE {s}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {s}.{table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY contract_read ON {s}.{table} FOR SELECT TO {APP_RW}, {APP_RO} USING (true)",
            f"CREATE POLICY contract_platform_update ON {s}.{table} FOR UPDATE TO {APP_RW} USING ({p}) WITH CHECK ({p})",
            f"CREATE POLICY migrator_all ON {s}.{table} TO assure_migrator USING (true) WITH CHECK (true)",
            f"REVOKE INSERT, UPDATE, DELETE ON TABLE {s}.{table} FROM {APP_RW}",
            f"GRANT UPDATE (status) ON TABLE {s}.{table} TO {APP_RW}",
            f"CREATE TRIGGER immutable_columns BEFORE UPDATE OR DELETE ON {s}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {s}.assure_forbid_mutation_except('status')",
        ]
    _execute(";\n".join(stmts) + ";")


def downgrade() -> None:
    bind = op.get_bind()
    s_plain = _schema()
    if bind.dialect.name == "postgresql":
        s = '"' + s_plain.replace('"', '""') + '"'
        n = bind.execute(sa.text(f"SELECT count(*) FROM {s}.{EVENT} WHERE subject_type <> 'CONNECTOR_DEFINITION'")).scalar()
        if n:
            raise RuntimeError(f"Refusing to downgrade 0013: {n} contract-version certification history row(s) exist and "
                               "cannot be represented at 0012 (history is append-only). Restore a pre-0013 backup instead.")
        stmts = []
        for table, _short, _pk in CONTRACTS:
            stmts += [f"DROP TRIGGER IF EXISTS immutable_columns ON {s}.{table}",
                      f"REVOKE UPDATE (status) ON TABLE {s}.{table} FROM {APP_RW}",
                      f"GRANT INSERT, UPDATE, DELETE ON TABLE {s}.{table} TO {APP_RW}",
                      f"DROP POLICY IF EXISTS migrator_all ON {s}.{table}",
                      f"DROP POLICY IF EXISTS contract_platform_update ON {s}.{table}",
                      f"DROP POLICY IF EXISTS contract_read ON {s}.{table}",
                      f"ALTER TABLE {s}.{table} NO FORCE ROW LEVEL SECURITY",
                      f"ALTER TABLE {s}.{table} DISABLE ROW LEVEL SECURITY"]
        _execute(";\n".join(stmts) + ";")
    op.drop_constraint("ck_connector_certification_event_subject", EVENT, type_="check")
    op.alter_column(EVENT, "to_runtime_health", existing_type=sa.String(length=32), nullable=False)
    op.alter_column(EVENT, "from_runtime_health", existing_type=sa.String(length=32), nullable=False)
    op.drop_column(EVENT, "subject_id")
    op.drop_column(EVENT, "subject_type")
    op.drop_constraint("fk_source_run_mapping_contract", RUN, type_="foreignkey")
    op.drop_constraint("fk_source_run_extraction_contract", RUN, type_="foreignkey")
    for table, short, _pk in reversed(CONTRACTS):
        op.drop_constraint(f"ck_{short}_certification", table, type_="check")
        op.drop_constraint(f"fk_{short}_connector", table, type_="foreignkey")
        op.drop_constraint(f"uq_{short}_connector_version", table, type_="unique")
