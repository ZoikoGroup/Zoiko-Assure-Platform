"""Automatic migration of approved local development databases (app/devmigrate.py; docs/database-migrations.md).

Controls under test:
- Development only, opt-in, fail closed. Production, staging and unknown environments are never migrated.
- Explicit allowlist, a loopback host, and the owner-set approval marker on the database itself. The canonical `assure`
  database and the ZA_PROTECTED_DATABASES entries are never migrated automatically.
- The existing runner (`migrate.upgrade`: one transaction, migration advisory lock, rollback on failure) is reused.
  Repeated or concurrent start-ups apply each revision once; a failure stops start-up.
- The API process never holds the migration credential (DAT-REQ-004; ZA-DAT-001 §13 "no normal application use").
- No password appears in output or logs.

PostgreSQL tests migrate throwaway schemas inside the disposable test database. The approval marker is set on that test
database for the duration of a test and its previous comment restored. No other database is contacted.
"""
from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent.parent
FAKE_PW = "s3cret-" + secrets.token_hex(6)


def _env(db="za_dev_scratch", host="127.0.0.1", port=55432, **over):
    url = f"postgresql+psycopg://assure:{FAKE_PW}@{host}:{port}/{db}"
    env = {"ZA_ENVIRONMENT": "development", "ZA_AUTO_MIGRATE": "1",
           "DATABASE_ADMIN_URL": url, "DATABASE_URL": url.replace("assure:", "assure_app:"),
           "ZA_AUTO_MIGRATE_DATABASES": f"{host}:{port}/{db}"}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


# ------------------------------------------------------------------------------------------------ the gates (no database)
def test_an_approved_configuration_passes_the_static_gates():
    from app.devmigrate import static_refusal
    assert static_refusal(_env()) is None
    assert static_refusal(_env(host="localhost", ZA_AUTO_MIGRATE_DATABASES="LOCALHOST:55432/za_dev_scratch")) is None


@pytest.mark.parametrize("value, why", [
    ("production", "never runs there"), ("Production", "never runs there"), ("staging", "never runs there"),
    ("prod", "never runs there"), ("test", "fail closed"), ("dev", "fail closed"), ("", "fail closed"),
    (None, "fail closed")])
def test_only_the_development_environment_may_auto_migrate(value, why):
    from app.devmigrate import static_refusal
    env = _env(ZA_ENVIRONMENT=value)
    assert why in static_refusal(env)


@pytest.mark.parametrize("flag", [None, "", "0", "false", "no", "maybe"])
def test_auto_migration_is_opt_in(flag):
    from app.devmigrate import enabled, static_refusal, startup_auto_migrate
    env = _env(ZA_AUTO_MIGRATE=flag)
    assert not enabled(env) and "not enabled" in static_refusal(env)
    called = []
    assert startup_auto_migrate(env, runner=lambda: called.append(1)) is None  # existing behaviour: nothing runs
    assert called == []


@pytest.mark.parametrize("over, why", [
    ({"db": "assure", "port": 5433, "ZA_AUTO_MIGRATE_DATABASES": "127.0.0.1:5433/assure"}, "protected"),
    ({"db": "assure", "port": 55432, "ZA_AUTO_MIGRATE_DATABASES": "127.0.0.1:55432/assure"}, "protected"),  # any host
    ({"ZA_PROTECTED_DATABASES": "za_dev_scratch"}, "protected"),
    ({"ZA_PROTECTED_DATABASES": "127.0.0.1:55432/za_dev_scratch"}, "protected"),
    ({"ZA_AUTO_MIGRATE_DATABASES": "127.0.0.1:55432/some_other_db"}, "not listed"),
    ({"ZA_AUTO_MIGRATE_DATABASES": None}, "not listed"),
    ({"host": "db.dev.internal", "ZA_AUTO_MIGRATE_DATABASES": "db.dev.internal:55432/za_dev_scratch"}, "loopback"),
])
def test_protected_unlisted_and_shared_databases_are_refused(over, why):
    from app.devmigrate import static_refusal
    assert why in static_refusal(_env(**over))


def test_unidentifiable_targets_are_refused():
    from app.devmigrate import static_refusal
    env = _env()
    env["DATABASE_URL"] = env["DATABASE_URL"].replace("/za_dev_scratch", "/another_db")
    assert "different databases" in static_refusal(env)
    assert "PostgreSQL" in static_refusal(_env(DATABASE_ADMIN_URL=None))
    assert "PostgreSQL" in static_refusal(_env(DATABASE_ADMIN_URL="sqlite:///x.db", DATABASE_URL="sqlite:///x.db"))


@pytest.mark.parametrize("environment", ["production", "staging", None])
def test_the_api_refuses_to_start_when_the_flag_is_set_outside_development(environment):
    from app.devmigrate import startup_auto_migrate
    called = []
    with pytest.raises(RuntimeError, match="Refusing to start: ZA_AUTO_MIGRATE is set"):
        startup_auto_migrate(_env(ZA_ENVIRONMENT=environment), runner=lambda: called.append(1))
    assert called == []  # no child process, so no connection and no migration


def test_startup_reports_a_refusal_and_stops_on_a_failure_without_leaking_secrets(caplog):
    from app.devmigrate import EXIT_FAILED, EXIT_REFUSED, startup_auto_migrate
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    leak = f"postgresql+psycopg://assure:{FAKE_PW}@127.0.0.1:55432/za_dev_scratch"

    def child(code, err):
        return lambda: subprocess.CompletedProcess([], code, stdout="", stderr=err)
    reason = startup_auto_migrate(_env(), runner=child(EXIT_REFUSED, f"AUTO-MIGRATE REFUSED: x ({leak}). Nothing."))
    assert "AUTO-MIGRATE REFUSED" in reason and FAKE_PW not in reason
    with pytest.raises(RuntimeError, match="automatic development migration failed") as e:
        startup_auto_migrate(_env(), runner=child(EXIT_FAILED, f"AUTO-MIGRATE FAILED: boom at {leak}"))
    assert FAKE_PW not in str(e.value)
    assert FAKE_PW not in caplog.text


def test_redaction():
    from app.devmigrate import redact
    assert redact(f"x postgresql://u:{FAKE_PW}@h:1/d y") == "x postgresql://u:***@h:1/d y"
    assert redact(f"password is {FAKE_PW}", (FAKE_PW,)) == "password is ***"


def test_the_api_process_does_not_load_the_migration_credential():
    """The start-up hook runs the migration in a child process; app.db (API process) still skips DATABASE_ADMIN_URL
    from backend/.env unless ZA_ADMIN_TOOL=1, and main.py never sets it."""
    src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    assert "ZA_ADMIN_TOOL" not in src and "startup_auto_migrate()" in src
    dm = (BACKEND / "app" / "devmigrate.py").read_text(encoding="utf-8")
    assert 'ZA_ADMIN_TOOL="1"' in dm and "migrate.upgrade(" in dm  # the child is the operator tool; one runner


# ------------------------------------------------------------------------------------------------ PostgreSQL
@pytest.fixture(scope="module")
def pg(client):
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine


@pytest.fixture
def approved(pg):
    """The disposable test database, approved: its URL allowlisted and the owner's marker set (previous comment kept)."""
    from app.db import ADMIN_DATABASE_URL, DATABASE_URL
    from app.devmigrate import APPROVAL_MARKER
    u = make_url(ADMIN_DATABASE_URL)
    with pg.begin() as c:
        old = c.execute(text("SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                             "WHERE datname = current_database()")).scalar()
        c.exec_driver_sql(f"COMMENT ON DATABASE \"{u.database}\" IS '{APPROVAL_MARKER}'")
    env = {"ZA_ENVIRONMENT": "development", "ZA_AUTO_MIGRATE": "true", "DATABASE_ADMIN_URL": ADMIN_DATABASE_URL,
           "DATABASE_URL": DATABASE_URL, "ZA_AUTO_MIGRATE_DATABASES": f"{u.host}:{u.port or 5432}/{u.database}"}
    yield env
    with pg.begin() as c:
        value = "NULL" if old is None else "'" + old.replace("'", "''") + "'"
        c.exec_driver_sql(f"COMMENT ON DATABASE \"{u.database}\" IS {value}")


@pytest.fixture
def schema(pg):
    name = "za_auto_" + secrets.token_hex(4)
    with pg.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    yield name
    with pg.begin() as c:
        c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')  # this test's throwaway schema only


def _state(schema):
    from app import migrate
    st = migrate.status(schema=schema)
    return st.kind, st.revision


def test_a_fresh_database_is_migrated_to_head_and_verified(pg, approved, schema):
    from app import devmigrate, migrate
    out = devmigrate.run(approved, engine=pg, schema=schema)
    assert out.action == "MIGRATED" and "empty database" in out.before
    assert _state(schema) == (migrate.CURRENT, migrate.head_revision())
    assert migrate.check(schema=schema, engine=pg) == []  # RLS coverage, FORCE, ownership: intact


def test_pending_revisions_are_applied_and_a_repeat_changes_nothing(pg, approved, schema):
    from alembic.script import ScriptDirectory
    from app import devmigrate, migrate
    head = migrate.head_revision()
    previous = ScriptDirectory.from_config(migrate.alembic_config()).get_revision(head).down_revision
    migrate.upgrade(previous, schema=schema, provision_role=False)
    assert _state(schema) == (migrate.BEHIND, previous)
    out = devmigrate.run(approved, engine=pg, schema=schema)
    assert out.action == "MIGRATED" and _state(schema) == (migrate.CURRENT, head)
    with pg.connect() as c:
        fp = migrate.fingerprint(c, schema)
        c.rollback()
    again = devmigrate.run(approved, engine=pg, schema=schema)  # a second start-up
    assert again.action == "CURRENT"
    with pg.connect() as c:
        assert migrate.fingerprint(c, schema) == fp
        assert c.execute(text(f'SELECT count(*) FROM "{schema}".alembic_version')).scalar() == 1
        c.rollback()


def test_without_the_marker_or_with_another_identity_nothing_is_touched(pg, approved, schema):
    from app import devmigrate, migrate
    from app.devmigrate import Refused
    u = make_url(approved["DATABASE_ADMIN_URL"])
    with pg.begin() as c:
        c.exec_driver_sql(f'COMMENT ON DATABASE "{u.database}" IS NULL')
    with pytest.raises(Refused, match="approval marker"):
        devmigrate.run(approved, engine=pg, schema=schema)
    assert _state(schema)[0] == migrate.EMPTY
    # the URL names another database than the server the engine reaches: refused before any change
    other = {k: v.replace(f"/{u.database}", "/za_not_this_one") if "URL" in k else v for k, v in approved.items()}
    other["ZA_AUTO_MIGRATE_DATABASES"] = f"{u.host}:{u.port or 5432}/za_not_this_one"
    with pytest.raises(Refused, match="not the allowlisted"):
        devmigrate.run(other, engine=pg, schema=schema)
    assert _state(schema)[0] == migrate.EMPTY


def test_a_failed_migration_rolls_back_and_leaves_the_schema_unverified(pg, approved, schema, tmp_path):
    from test_migrations import FAILING_REVISION
    from app import devmigrate, migrate
    migrate.upgrade(schema=schema, provision_role=False)
    head = migrate.head_revision()
    (tmp_path / "9999_failing_probe.py").write_text(FAILING_REVISION.format(down=head), encoding="utf-8")
    with pg.connect() as c:
        fp = migrate.fingerprint(c, schema)
        c.rollback()
    with pytest.raises(RuntimeError, match="deliberate"):
        devmigrate.run(approved, engine=pg, schema=schema, extra_version_locations=[tmp_path])
    assert _state(schema) == (migrate.CURRENT, head)  # never recorded as migrated, never skipped
    with pg.connect() as c:
        assert migrate.fingerprint(c, schema) == fp
        c.rollback()


def test_concurrent_startups_apply_the_revisions_once(pg, approved, schema):
    from app import devmigrate, migrate
    barrier, results, errors = threading.Barrier(3), [], []

    def start():
        try:
            barrier.wait()
            results.append(devmigrate.run(approved, engine=pg, schema=schema))
        except Exception as e:  # noqa: BLE001 - asserted below
            errors.append(e)
    threads = [threading.Thread(target=start) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(300)
    assert errors == []
    assert sorted(r.action for r in results) == ["CURRENT", "CURRENT", "MIGRATED"]
    assert _state(schema) == (migrate.CURRENT, migrate.head_revision())


def _child(env, schema=None):
    run_env = {k: v for k, v in os.environ.items() if not k.startswith(("ZA_", "DATABASE_"))}
    run_env.update(env, ZA_SKIP_DOTENV="1", ZA_DATA_DIR=os.environ.get("ZA_DATA_DIR") or tempfile.mkdtemp(),
                   PYTHONDONTWRITEBYTECODE="1")
    args = [sys.executable, "-m", "app.devmigrate"] + (["--schema", schema] if schema else [])
    return subprocess.run(args, cwd=BACKEND, env=run_env, capture_output=True, text=True, timeout=600)


def test_the_real_child_process_migrates_and_never_prints_a_password(pg, approved, schema):
    from app import migrate
    from app.devmigrate import EXIT_OK
    passwords = {make_url(approved[k]).password for k in ("DATABASE_ADMIN_URL", "DATABASE_URL")} - {None}
    r = _child(approved, schema)
    assert r.returncode == EXIT_OK, r.stderr[-600:]
    assert "AUTO-MIGRATE" in r.stdout and "applied" in r.stdout
    assert not any(p in r.stdout + r.stderr for p in passwords)
    assert _state(schema) == (migrate.CURRENT, migrate.head_revision())
    r2 = _child(approved, schema)  # repeated start-up
    assert r2.returncode == EXIT_OK and "nothing to apply" in r2.stdout


def test_a_failure_in_the_real_child_stops_startup(pg, approved, schema):
    """A database the runner refuses (unversioned tables that match no baseline) makes the child fail and the start-up
    hook raise, so the API does not start on an unverified schema."""
    from app import devmigrate, migrate
    with pg.begin() as c:
        c.exec_driver_sql(f'CREATE TABLE "{schema}".za_unrelated (id int)')
    with pytest.raises(RuntimeError, match="automatic development migration failed"):
        devmigrate.startup_auto_migrate(approved, runner=lambda: _child(approved, schema))
    assert _state(schema)[0] == migrate.UNVERSIONED_MISMATCH  # unchanged


def test_the_canonical_assure_database_is_refused_without_a_connection():
    """Static refusal happens before any engine connects: the child never reaches 127.0.0.1:5433 (the password here
    is fake; a connection attempt would surface as a failure, not a refusal)."""
    from app.devmigrate import EXIT_REFUSED
    env = _env(db="assure", port=5433, ZA_AUTO_MIGRATE_DATABASES="127.0.0.1:5433/assure")
    r = _child(env)
    assert r.returncode == EXIT_REFUSED and "protected database" in r.stderr, r.stderr[-400:]
    assert FAKE_PW not in r.stdout + r.stderr
    r = _child({**_env(), "ZA_ENVIRONMENT": "production"})
    assert r.returncode == EXIT_REFUSED and "never runs there" in r.stderr
