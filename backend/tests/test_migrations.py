"""Versioned schema migrations: Stage 1 of docs/backend-architecture-roadmap.md (M-01; DAT-REQ-023/039/040, QA-REQ-035).

PostgreSQL only (skipped on SQLite), except the plain-alembic guard. Tests that build, break or roll back a schema do it
in a throwaway schema of the disposable test database, created and dropped here. The migration tool resolves every
unqualified name through the search_path it sets, so the same revisions build `public` or a throwaway schema; the
shared `public` schema the rest of the suite uses is only read (one probe ALTER is rolled back).
"""
from __future__ import annotations

import re
import secrets
from functools import partial

import pytest
from sqlalchemy import text

# Statements that change the catalogue. Multi-line so a script of several statements is caught too.
DDL = re.compile(r"^\s*(CREATE|ALTER|DROP|GRANT|REVOKE|COMMENT\s+ON|TRUNCATE|SECURITY\s+LABEL)\b", re.I | re.M)

FAILING_REVISION = '''"""Test-only revision that fails after it has already changed the schema and written a row."""
import sqlalchemy as sa
from alembic import op

revision = "9999_failing_probe"
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("za_failed_probe", sa.Column("id", sa.Integer, primary_key=True))
    op.execute("INSERT INTO za_failed_probe (id) VALUES (1)")
    raise RuntimeError("deliberate failure after DDL and DML")


def downgrade():
    op.drop_table("za_failed_probe")
'''


@pytest.fixture(scope="module")
def pg(client):  # the client fixture has migrated `public`, provisioned the runtime role and run start-up once
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine


@pytest.fixture
def schema(pg):
    name = "za_mig_" + secrets.token_hex(4)
    with pg.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    yield name
    with pg.begin() as c:
        c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')  # this test's throwaway schema only


@pytest.fixture
def executed_sql(monkeypatch):
    """Every statement any psycopg cursor sends during the test: SQLAlchemy and raw DBAPI cursors (the old start-up
    applied its RLS SQL through one), on both the owner and the runtime engine."""
    import psycopg
    seen, real = [], psycopg.Cursor.execute

    def spy(self, query, *args, **kwargs):
        seen.append(query.as_string(self) if hasattr(query, "as_string")
                    else query.decode() if isinstance(query, bytes) else str(query))
        return real(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", spy)
    return seen


def _fingerprint(pg, schema):
    from app import migrate
    with pg.connect() as c:
        try:
            return migrate.fingerprint(c, schema)
        finally:
            c.rollback()


def _has_version_table(pg, schema):
    with pg.connect() as c:
        return c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f'"{schema}".alembic_version'}).scalar()


# Columns that revisions after the 0002 baseline added to app/models.py. The old start-up never created them, so the
# legacy schema below drops them again after create_all. Every later model change must be listed here with its revision;
# an unlisted one still fails the baseline comparison.
POST_BASELINE_COLUMNS = {("auth_session", "token_hash"): "0006"}
POST_BASELINE_TABLES = {
    "assurance_exception_event": "0008",
    "assurance_compensation_event": "0008",
    "assurance_issue_event": "0008",
    "evidence_legal_hold_event": "0008",
    "evidence_conflict_event": "0008",
    "assurance_obligation_occurrence_event": "0008",
    "iam_role_assignment": "0009",
    "iam_role_assignment_event": "0009",
    "ai_authority_grant_event": "0011",
    "integration_connector_certification_event": "0012",
}
# Constraints that revisions after the baseline added to tables the old start-up created (dropped from the legacy schema).
POST_BASELINE_CONSTRAINTS = {
    ("ai_authority_grant", "ck_ai_authority_grant_level"): "0011",
    ("evidence_source_run", "fk_source_run_extraction_contract"): "0013",
    ("evidence_source_run", "fk_source_run_mapping_contract"): "0013",
    ("integration_extraction_contract_version", "uq_extraction_contract_connector_version"): "0013",
    ("integration_extraction_contract_version", "fk_extraction_contract_connector"): "0013",
    ("integration_extraction_contract_version", "ck_extraction_contract_certification"): "0013",
    ("integration_mapping_contract_version", "uq_mapping_contract_connector_version"): "0013",
    ("integration_mapping_contract_version", "fk_mapping_contract_connector"): "0013",
    ("integration_mapping_contract_version", "ck_mapping_contract_certification"): "0013",
}


def _legacy_startup(pg, schema):
    """Build `schema` exactly as the API start-up did before Stage 1: create_all, then the RLS SQL (now revision 0002,
    byte-identical), through a raw cursor as the old bootstrap ran it. No version table."""
    from alembic.script import ScriptDirectory
    from app import migrate, models  # noqa: F401
    from app.db import Base
    rls = ScriptDirectory.from_config(migrate.alembic_config()).get_revision("0002").module.RLS_SQL
    with pg.begin() as c:
        c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        Base.metadata.create_all(c)
        for table in POST_BASELINE_TABLES:
            c.exec_driver_sql(f'DROP TABLE IF EXISTS "{schema}".{table} CASCADE')
        for table, column in POST_BASELINE_COLUMNS:
            c.exec_driver_sql(f'ALTER TABLE "{schema}".{table} DROP COLUMN {column}')
        for table, constraint in POST_BASELINE_CONSTRAINTS:
            c.exec_driver_sql(f'ALTER TABLE "{schema}".{table} DROP CONSTRAINT {constraint}')
        with c.connection.cursor() as cur:
            cur.execute(rls.replace("SCHEMA public", f'SCHEMA "{schema}"'))


def _up(schema, target="head", **kw):
    from app import migrate
    return migrate.upgrade(target, schema=schema, provision_role=False, **kw)


# ---------------------------------------------------------------- fresh install, order, repetition
def test_fresh_install_upgrades_in_order_through_the_baseline_to_head(pg, schema):
    from app import migrate
    head = migrate.head_revision()
    assert migrate.status(schema=schema).kind == migrate.EMPTY
    before, after = _up(schema, "0001")
    assert (before.kind, after.kind, after.revision) == (migrate.EMPTY, migrate.BEHIND, "0001")
    assert after.pending[0] == "0002" and after.pending[-1] == head
    with pytest.raises(migrate.SchemaVersionError, match="app.migrate upgrade"):
        migrate.require_current(schema=schema)  # what start-up would say: one revision behind
    _up(schema, migrate.BASELINE_REVISION)
    baseline, fp = migrate.load_baseline(), _fingerprint(pg, schema)
    assert migrate.compare_fingerprints(baseline["fingerprint"], fp) == []
    assert migrate.fingerprint_digest(fp) == baseline["digest"]
    _, after = _up(schema)
    assert (after.kind, after.revision) == (migrate.CURRENT, head)
    assert migrate.require_current(schema=schema) == head


def test_repeat_upgrade_changes_nothing(pg, schema):
    from app import migrate
    _up(schema)
    fp = _fingerprint(pg, schema)
    before, after = _up(schema)
    assert before.kind == after.kind == migrate.CURRENT
    assert _fingerprint(pg, schema) == fp
    with pg.connect() as c:
        assert c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalars().all() == [
            migrate.head_revision()]


# ---------------------------------------------------------------- existing (pre-migration) databases
def test_old_startup_schema_is_exactly_the_migration_baseline(pg, schema):
    """Roadmap Stage 1 acceptance: the migrations build what create_all + the RLS SQL built, object for object."""
    from app import migrate
    _legacy_startup(pg, schema)
    assert migrate.compare_fingerprints(migrate.load_baseline()["fingerprint"], _fingerprint(pg, schema)) == []


def test_existing_database_is_validated_then_adopted_without_rebuilding(pg, schema):
    from app import migrate
    _legacy_startup(pg, schema)
    with pg.begin() as c:
        c.execute(text(f'INSERT INTO "{schema}".org_tenant (tenant_id, legal_name, contracting_region, isolation_tier, '
                       "status, created_at) VALUES ('t-adopt', 'Adopted Ltd', 'UK', 'POOLED', 'ACTIVE', now())"))
    st = migrate.status(schema=schema)
    assert (st.kind, st.differences) == (migrate.UNVERSIONED_BASELINE, [])
    before, after = _up(schema)
    assert (before.kind, after.kind, after.revision) == (migrate.UNVERSIONED_BASELINE, migrate.CURRENT,
                                                         migrate.head_revision())
    with pg.connect() as c:  # stamped and upgraded in place: existing rows are untouched
        assert c.execute(text(f'SELECT legal_name FROM "{schema}".org_tenant')).scalars().all() == ["Adopted Ltd"]


def test_drifted_existing_database_is_refused_and_left_untouched(pg, schema):
    from app import migrate
    _legacy_startup(pg, schema)
    with pg.begin() as c:
        c.exec_driver_sql(f'ALTER TABLE "{schema}".assurance_issue ADD COLUMN local_note text')
        c.exec_driver_sql(f'DROP POLICY tenant_isolation ON "{schema}".evidence_artifact')
    fp = _fingerprint(pg, schema)
    st = migrate.status(schema=schema)
    assert st.kind == migrate.UNVERSIONED_MISMATCH
    assert any(d.startswith("columns: not in baseline: assurance_issue.local_note") for d in st.differences)
    assert any(d.startswith("policies: missing from database: evidence_artifact|tenant_isolation")
               for d in st.differences)
    with pytest.raises(migrate.MigrationRefused, match="NOT matching"):
        _up(schema)
    assert _fingerprint(pg, schema) == fp and not _has_version_table(pg, schema)  # never stamped, nothing changed


def test_partial_schema_is_refused(pg, schema):
    from app import migrate, models  # noqa: F401
    from app.db import Base
    with pg.begin() as c:
        c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        Base.metadata.tables["org_tenant"].create(c)
    st = migrate.status(schema=schema)
    assert (st.kind, st.tables) == (migrate.UNVERSIONED_MISMATCH, 1)
    with pytest.raises(migrate.MigrationRefused):
        _up(schema)
    with pg.connect() as c:
        assert migrate.app_tables(c, schema) == {"org_tenant"}
    assert not _has_version_table(pg, schema)


def test_unknown_or_missing_revision_is_refused(pg, schema):
    from app import migrate
    _up(schema)
    with pg.begin() as c:
        c.execute(text(f'UPDATE "{schema}".alembic_version SET version_num = :v'), {"v": "9999_from_a_newer_build"})
    st = migrate.status(schema=schema)
    assert (st.kind, st.revision) == (migrate.UNKNOWN_REVISION, "9999_from_a_newer_build")
    with pytest.raises(migrate.MigrationRefused, match="unknown to this build"):
        _up(schema)
    with pytest.raises(migrate.SchemaVersionError, match="unknown to this build"):
        migrate.require_current(schema=schema)
    with pg.begin() as c:
        c.exec_driver_sql(f'DELETE FROM "{schema}".alembic_version')
    assert migrate.status(schema=schema).kind == migrate.INCONSISTENT
    with pytest.raises(migrate.MigrationRefused, match="version table without a revision"):
        _up(schema)


# ---------------------------------------------------------------- failure, rollback, downgrade
def test_failed_migration_rolls_back_completely(pg, schema, tmp_path):
    from app import migrate
    head = migrate.head_revision()
    (tmp_path / "9999_failing_probe.py").write_text(FAILING_REVISION.format(down=head), encoding="utf-8")
    # From empty: 0001 and 0002 ran, then the probe failed. One transaction, so nothing at all remains.
    with pytest.raises(RuntimeError, match="deliberate"):
        _up(schema, extra_version_locations=[tmp_path])
    assert migrate.status(schema=schema).kind == migrate.EMPTY and not _has_version_table(pg, schema)
    # From head (a release adding a broken revision): the current schema survives, the probe's table and row do not.
    _up(schema)
    fp = _fingerprint(pg, schema)
    with pytest.raises(RuntimeError, match="deliberate"):
        _up(schema, extra_version_locations=[tmp_path])
    st = migrate.status(schema=schema)
    assert (st.kind, st.revision) == (migrate.CURRENT, head)
    assert _fingerprint(pg, schema) == fp


def test_newest_revision_downgrades_and_upgrades_again(pg, schema):
    from alembic.script import ScriptDirectory
    from app import migrate
    head = migrate.head_revision()
    previous = ScriptDirectory.from_config(migrate.alembic_config()).get_revision(head).down_revision
    _up(schema)
    fp = _fingerprint(pg, schema)
    with pytest.raises(SystemExit, match="--confirm"):
        migrate.downgrade(previous, schema=schema)
    st = migrate.downgrade(previous, confirm=True, schema=schema)
    assert (st.kind, st.revision) == (migrate.BEHIND, previous)
    _up(schema)
    assert _fingerprint(pg, schema) == fp


def test_baseline_tables_cannot_be_downgraded(pg, schema):
    from app import migrate
    _up(schema, "0001")
    fp = _fingerprint(pg, schema)
    with pytest.raises(RuntimeError, match="irreversible"):
        migrate.downgrade("base", confirm=True, schema=schema)
    st = migrate.status(schema=schema)
    assert (st.kind, st.revision) == (migrate.BEHIND, "0001")
    assert _fingerprint(pg, schema) == fp


# ---------------------------------------------------------------- API start-up is read-only
def test_api_startup_runs_no_ddl(pg, executed_sql):
    from fastapi.testclient import TestClient
    from app.main import app
    before = _fingerprint(pg, "public")
    executed_sql.clear()
    with TestClient(app):
        pass
    statements = list(executed_sql)
    assert statements, "the spy saw no SQL at all, so it could not have seen DDL either"
    assert [q for q in statements if DDL.search(q)] == []
    assert _fingerprint(pg, "public") == before


def test_api_refuses_to_start_on_a_database_not_at_head(pg, schema, monkeypatch, executed_sql):
    from fastapi.testclient import TestClient
    from app import migrate
    from app.main import app
    with pytest.raises(migrate.SchemaVersionError, match="empty database"):
        migrate.require_current(schema=schema)
    _up(schema, "0001")
    monkeypatch.setattr(migrate, "require_current", partial(migrate.require_current, schema=schema))
    executed_sql.clear()
    with pytest.raises(migrate.SchemaVersionError, match="Start-up does not create or change the schema"):
        with TestClient(app):
            pass
    assert [q for q in executed_sql if DDL.search(q)] == []
    st = migrate.status(schema=schema)
    assert (st.kind, st.revision) == (migrate.BEHIND, "0001")  # still behind: start-up migrated nothing


# ---------------------------------------------------------------- generated checks (CI gates)
def test_every_tenant_table_has_row_level_security(pg, schema):
    from app import migrate
    with pg.connect() as c:
        tenant_tables = c.execute(text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND EXISTS (SELECT 1 FROM pg_attribute a "
            "WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)")).scalar()
        assert tenant_tables > len(migrate.RLS_EXEMPT)
        assert migrate.rls_gaps(c, "public") == []
    _up(schema)  # the check must be able to fail: switch RLS off on one table of a throwaway copy
    with pg.begin() as c:
        c.exec_driver_sql(f'ALTER TABLE "{schema}".evidence_artifact DISABLE ROW LEVEL SECURITY')
    with pg.connect() as c:
        assert migrate.rls_gaps(c, schema) == ["evidence_artifact: rls=OFF, policy=yes"]


def test_models_and_migrations_agree(pg):
    """The no-drift gate: autogenerate would produce an empty migration. A model change without a migration fails."""
    from app import migrate
    with pg.connect() as c:
        try:
            assert migrate.model_drift(c) == []
            c.exec_driver_sql("ALTER TABLE assurance_issue ADD COLUMN za_drift_probe text")  # rolled back below
            assert any("za_drift_probe" in d for d in migrate.model_drift(c))
        finally:
            c.rollback()
    assert migrate.check() == []


def test_cli_reports_state_without_secrets(pg, capsys):
    from sqlalchemy.engine import make_url
    from app import migrate
    from app.db import ADMIN_DATABASE_URL
    assert migrate.main(["status"]) == 0
    assert migrate.main(["check"]) == 0
    out = capsys.readouterr()
    assert "at the head revision" in out.out and "check passed" in out.out
    assert make_url(ADMIN_DATABASE_URL).password not in out.out + out.err


def test_plain_alembic_cannot_upgrade_downgrade_or_stamp():
    """Only `python -m app.migrate` may write the version: it validates first, locks, and runs in one transaction."""
    from alembic.config import main as alembic_main
    from app.migrate import BACKEND_DIR
    for argv in (["upgrade", "head"], ["downgrade", "-1"], ["stamp", "head"]):
        with pytest.raises(SystemExit, match="use `python -m app.migrate`"):
            alembic_main(["-c", str(BACKEND_DIR / "alembic.ini"), *argv])
