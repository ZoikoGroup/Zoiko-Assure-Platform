"""Schema + RLS bootstrap, run at API start-up with the OWNER role (idempotent)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from .db import ADMIN_DATABASE_URL, BASE_DIR, DATABASE_URL, IS_POSTGRES, Base, admin_engine

RLS_SQL = BASE_DIR / "db" / "postgres_rls.sql"

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
    member_of = conn.execute(text("SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
                                  "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = :r) ORDER BY 1"),
                             params).scalars().all()
    if member_of:
        problems.append("is a member of " + ", ".join(member_of))
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


def _ensure_runtime_role(conn) -> None:
    app_url = make_url(DATABASE_URL)
    if app_url.username == make_url(ADMIN_DATABASE_URL).username:
        raise RuntimeError("DATABASE_URL must use a non-owner role (e.g. assure_app) so row-level security applies; "
                           "use DATABASE_ADMIN_URL for the owner role.")
    # NOBYPASSRLS / non-owner: the runtime role can never see across tenants outside system scope
    _provision_runtime_role(conn, app_url.username, app_url.password or "")


def bootstrap() -> None:
    if not IS_POSTGRES:
        Base.metadata.create_all(admin_engine)
        return
    with admin_engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(724501)"))  # one bootstrapper at a time
        _ensure_runtime_role(conn)
        Base.metadata.create_all(conn)
        with conn.connection.cursor() as cur:  # raw DBAPI cursor: no param parsing, so '%' in plpgsql is literal
            cur.execute(RLS_SQL.read_text(encoding="utf-8"))
