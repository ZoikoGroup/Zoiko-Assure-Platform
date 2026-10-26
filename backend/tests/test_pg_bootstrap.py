"""PostgreSQL bootstrap regression (P0-8A; skipped on SQLite).

Start-up must be repeatable with a least-privilege owner (NOSUPERUSER, CREATEROLE). On PostgreSQL 16+ a non-superuser
may not even mention SUPERUSER/BYPASSRLS in ALTER ROLE, so every start after the first used to fail (DAT-REQ-004/027).
Role changes in the fail-closed and create-path tests run inside transactions that are always rolled back.
"""
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="module")
def pg_admin(client):  # client fixture ensures this session's first bootstrap + seed have run
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine


def _runtime_role():
    from app.db import DATABASE_URL
    return make_url(DATABASE_URL).username


def _attrs(conn, role):
    return conn.execute(text("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                             "FROM pg_roles WHERE rolname = :r"), {"r": role}).one()


def test_bootstrap_repeats_with_least_privilege_owner(pg_admin):
    from app import pg_bootstrap
    with pg_admin.connect() as c:
        owner = c.execute(text("SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user")).one()
    assert owner == (False, True), "owner role must be NOSUPERUSER + CREATEROLE (least privilege)"
    pg_bootstrap.bootstrap()  # this session's lifespan already ran #1; these are #2 and #3
    pg_bootstrap.bootstrap()
    role = _runtime_role()
    with pg_admin.connect() as c:
        # LOGIN, SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS
        assert tuple(_attrs(c, role)) == (True, False, False, False, False, False)
        assert c.execute(text("SELECT count(*) FROM pg_database "
                              "WHERE datdba = (SELECT oid FROM pg_roles WHERE rolname = :r)"), {"r": role}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM pg_class "
                              "WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = :r)"), {"r": role}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM pg_proc "
                              "WHERE proowner = (SELECT oid FROM pg_roles WHERE rolname = :r)"), {"r": role}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM pg_auth_members "
                              "WHERE member = (SELECT oid FROM pg_roles WHERE rolname = :r)"), {"r": role}).scalar() == 0


def test_existing_runtime_role_with_unsafe_privilege_fails_closed(pg_admin):
    from app import pg_bootstrap
    role = _runtime_role()
    with pg_admin.connect() as c:
        tx = c.begin()
        try:
            c.exec_driver_sql(f'ALTER ROLE "{role}" CREATEROLE')  # an unsafe attribute this owner is able to grant
            with pytest.raises(pg_bootstrap.RuntimeRoleError, match="CREATEROLE"):
                pg_bootstrap._provision_runtime_role(c, role, secrets.token_hex(16))
        finally:
            tx.rollback()
        assert _attrs(c, role).rolcreaterole is False  # rolled back; the password was never touched


def test_owner_check_before_bootstrap_does_not_need_the_runtime_role(pg_admin, monkeypatch):
    """On a fresh database the runtime role only exists once bootstrap provisions it, so start-up's first check must
    use the owner role alone (CI creates no runtime role; checking it first failed every run before any test ran)."""
    from sqlalchemy import create_engine
    from app import db
    from app.db import DATABASE_URL
    missing = make_url(DATABASE_URL).set(password=secrets.token_hex(16))  # PostgreSQL rejects it like an absent role
    unusable = create_engine(missing, connect_args={"connect_timeout": 5})
    monkeypatch.setattr(db, "engine", unusable)
    try:
        assert "owner role reachable" in db.check_database(runtime=False)
        with pytest.raises(RuntimeError, match="with DATABASE_URL") as e:
            db.check_database()
        assert missing.password not in str(e.value)
    finally:
        unusable.dispose()


def test_new_runtime_role_is_created_least_privilege(pg_admin):
    from app import pg_bootstrap
    probe = "za_bootstrap_probe_" + secrets.token_hex(4)
    with pg_admin.connect() as c:
        tx = c.begin()
        try:
            pg_bootstrap._provision_runtime_role(c, probe, secrets.token_hex(16))  # create path, then repeat path
            pg_bootstrap._provision_runtime_role(c, probe, secrets.token_hex(16))
            assert tuple(_attrs(c, probe)) == (True, False, False, False, False, False)
        finally:
            tx.rollback()
        assert c.execute(text("SELECT count(*) FROM pg_roles WHERE rolname = :r"), {"r": probe}).scalar() == 0
