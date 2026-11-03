"""Runtime-role provisioning and verification (DAT-REQ-004/027, SEC-REQ-023).

The schema itself is no longer built here: tables, row-level security and grants are versioned migrations applied by
``python -m app.migrate`` (``ensure_runtime_role`` runs inside that transaction, with the owner role). API start-up
only calls the read-only ``verify_runtime_role`` and ``verify_context_key``, as the runtime role itself.

From revision 0007 (ZA-DAT-001 §13) the runtime login holds its privileges through exactly one membership,
``assure_app_rw`` (INHERIT, without SET or ADMIN), and that group role is itself a member of nothing: the runtime
credential cannot reach the owner, the migrator or any administrative role.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from .db import ADMIN_DATABASE_URL, DATABASE_URL

RUNTIME_ROLE = "assure_app"  # the runtime login (DATABASE_URL); its privileges come from APP_RW_ROLE since 0007
APP_RW_ROLE = "assure_app_rw"  # ZA-DAT-001 §13 "Approved DML under RLS; no BYPASSRLS; no DDL" (migration 0007)

# Attributes the runtime role must never hold (ZA-DAT-001 DAT-REQ-004/027, ZA-SEC-001 SEC-REQ-023).
_FORBIDDEN_ATTRS = {"rolsuper": "SUPERUSER", "rolcreatedb": "CREATEDB", "rolcreaterole": "CREATEROLE",
                    "rolreplication": "REPLICATION", "rolbypassrls": "BYPASSRLS"}


class RuntimeRoleError(RuntimeError):
    """The runtime role cannot be put into, or is not in, its least-privilege state; start-up must stop."""


def _role_ddl(conn, statement: str, role: str) -> None:
    # The statement carries the password literal: run it without SQLAlchemy parameter parsing and never let the SQL
    # text reach an exception message or traceback.
    try:
        conn.exec_driver_sql(statement)
    except DBAPIError as e:
        diag = getattr(e.orig, "diag", None)
        msg = diag.message_primary if diag else type(e.orig).__name__
        detail = f" ({diag.message_detail})" if diag and diag.message_detail else ""
        raise RuntimeRoleError(f"Could not provision runtime role {role!r}: {msg}{detail}") from None


def _assert_least_privilege(conn, role: str) -> None:
    """Fail closed unless `role` is least-privilege. Read-only: start-up never tries to remove privileges, because on
    PostgreSQL 16+ only a role holding an attribute may change it, and a misconfigured role needs a human anyway."""
    params = {"r": role}
    row = conn.execute(text("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                            "FROM pg_roles WHERE rolname = :r"), params).mappings().one()
    problems = [name for col, name in _FORBIDDEN_ATTRS.items() if row[col]]
    if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = current_database() "
                         "AND datdba = (SELECT oid FROM pg_roles WHERE rolname = :r)"), params).scalar():
        problems.append("owns the database")
    owned = conn.execute(text("SELECT count(*) FROM pg_class "
                              "WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = :r)"), params).scalar()
    if owned:
        problems.append(f"owns {owned} relation(s)")
    # One membership is expected from revision 0007: assure_app_rw, inherited only (no SET ROLE, no ADMIN OPTION).
    member_of = conn.execute(text("SELECT g.rolname, m.admin_option, m.set_option FROM pg_auth_members m "
                                  "JOIN pg_roles g ON g.oid = m.roleid "
                                  "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = :r) ORDER BY 1"),
                             params).all()
    unexpected = sorted({g for g, admin, can_set in member_of if g != APP_RW_ROLE or admin or can_set})
    if unexpected:
        problems.append("is a member of " + ", ".join(unexpected))
    if any(g == APP_RW_ROLE for g, _, _ in member_of):
        rw = conn.execute(text("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                               "(SELECT count(*) FROM pg_auth_members WHERE member = r.oid) FROM pg_roles r "
                               "WHERE rolname = :g"), {"g": APP_RW_ROLE}).one()
        if any(rw[:6]) or rw[6]:
            problems.append(f"inherits from {APP_RW_ROLE}, which has LOGIN, an elevated attribute or a membership")
    if problems:
        raise RuntimeRoleError(f"Refusing to start: runtime role {role!r} is not least-privilege ({'; '.join(problems)}). "
                               "Correct the role as a superuser; start-up never grants or tolerates these.")


def _provision_runtime_role(conn, role: str, password: str) -> None:
    q = conn.dialect.identifier_preparer.quote(role)
    lit = "'" + password.replace("'", "''") + "'"
    if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar():
        # Existing role: inspect first, then touch only LOGIN and PASSWORD, which a CREATEROLE owner holding ADMIN OPTION
        # may change. Never re-state SUPERUSER/BYPASSRLS/REPLICATION/CREATEDB here: on PostgreSQL 16+ a non-superuser may
        # not even mention an attribute it does not itself hold, even to switch it off.
        _assert_least_privilege(conn, role)
        _role_ddl(conn, f"ALTER ROLE {q} WITH LOGIN PASSWORD {lit}", role)
    else:
        # Switching privileges OFF is permitted at CREATE time for any CREATEROLE role; only switching them on is not.
        _role_ddl(conn, f"CREATE ROLE {q} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                        f"PASSWORD {lit}", role)
    _assert_least_privilege(conn, role)


def _runtime_url():
    app_url = make_url(DATABASE_URL)
    if ADMIN_DATABASE_URL and app_url.username == make_url(ADMIN_DATABASE_URL).username:
        raise RuntimeError("DATABASE_URL must use a non-owner role (e.g. assure_app) so row-level security applies; "
                           "use DATABASE_ADMIN_URL for the owner role.")
    if app_url.username != RUNTIME_ROLE:
        raise RuntimeRoleError(f"DATABASE_URL must connect as {RUNTIME_ROLE!r}: the row-level security policies and "
                               f"grants (migration 0002) name that role, so any other role would bypass them.")
    return app_url


def ensure_runtime_role(conn) -> None:
    """Create or update the runtime role, then prove it is least-privilege. Owner role, inside the migration transaction."""
    app_url = _runtime_url()
    # NOBYPASSRLS / non-owner: the runtime role can never see across tenants outside system scope
    _provision_runtime_role(conn, app_url.username, app_url.password or "")
    if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :g"), {"g": APP_RW_ROLE}).scalar():
        # a runtime login (re)created after revision 0007 gets its one membership (0007 grants it on first upgrade)
        _role_ddl(conn, f'GRANT {APP_RW_ROLE} TO {conn.dialect.identifier_preparer.quote(app_url.username)} '
                        "WITH INHERIT TRUE, SET FALSE", app_url.username)


def verify_context_key(conn) -> None:
    """Start-up check, as the runtime role: the database verifies contexts signed with this process's key (0007)."""
    from .db import context_key, context_key_id
    expected = context_key_id(context_key())
    found = conn.execute(text("SELECT assure_context_key_id()")).scalar()
    if found != expected:
        raise RuntimeRoleError("Refusing to start: the database does not hold this process's context key "
                               f"({'none provisioned' if found is None else 'a different key'}), so every tenant context "
                               "would be refused. Run `python -m app.migrate upgrade` with the same ZA_DATA_DIR or "
                               "ZA_DB_CONTEXT_KEY as the API.")


def verify_runtime_role(conn) -> None:
    """Start-up check, read-only: the runtime role exists and is least-privilege. Never creates or alters it."""
    role = _runtime_url().username
    if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar():
        raise RuntimeRoleError(f"Runtime role {role!r} does not exist. Run `python -m app.migrate upgrade`, which "
                               "provisions it (docs/database-migrations.md).")
    _assert_least_privilege(conn, role)
