"""Schema + RLS bootstrap, run at API start-up with the OWNER role (idempotent)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import make_url

from .db import ADMIN_DATABASE_URL, BASE_DIR, DATABASE_URL, IS_POSTGRES, Base, admin_engine

RLS_SQL = BASE_DIR / "db" / "postgres_rls.sql"


def _ensure_runtime_role(conn) -> None:
    app_url = make_url(DATABASE_URL)
    if app_url.username == make_url(ADMIN_DATABASE_URL).username:
        raise RuntimeError("DATABASE_URL must use a non-owner role (e.g. assure_app) so row-level security applies; "
                           "use DATABASE_ADMIN_URL for the owner role.")
    role, pw = app_url.username, app_url.password or ""
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
    q = conn.dialect.identifier_preparer.quote(role)
    lit = "'" + pw.replace("'", "''") + "'"
    # NOBYPASSRLS / non-owner: the runtime role can never see across tenants outside system scope
    if exists:
        conn.execute(text(f"ALTER ROLE {q} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {lit}"))
    else:
        conn.execute(text(f"CREATE ROLE {q} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {lit}"))


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
