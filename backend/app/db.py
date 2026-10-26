"""Database bootstrap (ZA-DAT-001).

PostgreSQL is the system of record and the only runtime database (``DATABASE_URL``). There is no silent fallback:
without a configured database the API refuses to start. SQLite exists only for isolated test runs, which must ask for
it explicitly with ``ZA_ALLOW_SQLITE=1`` (``tests/conftest.py`` does).

On PostgreSQL two roles are used:

* ``DATABASE_URL``        — the API runtime role (``assure_app``). Row-level security applies to it:
  every transaction is stamped with the caller's tenant (``app.tenant_id``), so a request can only
  read/write its own tenant's rows even if an application-layer check were missed.
* ``DATABASE_ADMIN_URL``  — the owner/migration role. Creates tables, applies ``db/postgres_rls.sql``
  and seeds demo data. Never used to serve requests.

A small number of documented cell-wide platform jobs (rule-release impact re-evaluation, shadow
impact, ledger checkpoints) run under an explicit ``system_scope``; see ``app.security``.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if os.environ.get("ZA_SKIP_DOTENV") != "1":
    _load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("ZA_DATA_DIR", BASE_DIR / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOW_SQLITE = os.environ.get("ZA_ALLOW_SQLITE") == "1"  # isolated test runs only
DATABASE_URL = os.environ.get("DATABASE_URL") or (f"sqlite:///{(DATA_DIR / 'assure.db').as_posix()}" if ALLOW_SQLITE else "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Zoiko Assure runs on PostgreSQL: set DATABASE_URL (runtime role) and "
                       "DATABASE_ADMIN_URL (owner role) in backend/.env or the environment; see backend/.env.example.")
if DATABASE_URL.startswith("sqlite") and not ALLOW_SQLITE:
    raise RuntimeError("DATABASE_URL points at SQLite, which is only for isolated test runs (ZA_ALLOW_SQLITE=1). "
                       "Zoiko Assure runs on PostgreSQL; see backend/.env.example.")
ADMIN_DATABASE_URL = os.environ.get("DATABASE_ADMIN_URL", DATABASE_URL)
CELL_ID = os.environ.get("ZA_CELL_ID", "eu-west-cell-a")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
if IS_POSTGRES != ADMIN_DATABASE_URL.startswith("postgresql"):
    raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL must both be PostgreSQL URLs.")


def describe_url(url: str) -> str:
    """A database URL with its password removed, safe for logs and error messages."""
    from sqlalchemy.engine import make_url
    u = make_url(url)
    return f"{u.get_backend_name()} {u.database}" if u.get_backend_name() == "sqlite" else f"{u.username}@{u.host}:{u.port}/{u.database}"


def _make_engine(url: str):
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - driver hook
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
        return eng
    # Bounded connect: on Windows a connection to a stopped server can otherwise wait forever instead of failing.
    return create_engine(url, future=True, pool_pre_ping=True, pool_size=10, max_overflow=10,
                         connect_args={"connect_timeout": int(os.environ.get("ZA_DB_CONNECT_TIMEOUT", "10"))})


engine = _make_engine(DATABASE_URL)
admin_engine = engine if ADMIN_DATABASE_URL == DATABASE_URL else _make_engine(ADMIN_DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AdminSession = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)


def check_database(runtime: bool = True) -> str:
    """Connect with both roles and confirm they reach the same PostgreSQL database. Fails with a clear message that
    never contains a password. Returns a description for the start-up log.

    Start-up calls it twice: first with ``runtime=False`` (owner role only, because on a fresh database the runtime
    role does not exist until ``pg_bootstrap`` provisions it), then with both roles once bootstrap has run."""
    from sqlalchemy.engine import make_url
    if IS_SQLITE:
        return f"SQLite {make_url(DATABASE_URL).database} (isolated test run)"
    seen = {}
    roles = (("DATABASE_ADMIN_URL", admin_engine, ADMIN_DATABASE_URL), ("DATABASE_URL", engine, DATABASE_URL))
    for name, eng, url in roles if runtime else roles[:1]:
        try:
            with eng.connect() as conn:
                seen[name] = conn.execute(text("SELECT current_database(), inet_server_port(), current_setting('server_version')")).one()
        except Exception as e:  # noqa: BLE001 - re-raised below with a secret-free message
            reason = str(getattr(e, "orig", None) or e).strip().splitlines()[0]
            raise RuntimeError(f"Cannot connect to PostgreSQL with {name} ({describe_url(url)}): {reason}. Is the "
                               "PostgreSQL service running, and do the role, password and database name match?") from None
    if not runtime:
        return f"owner role reachable at {describe_url(ADMIN_DATABASE_URL)}"
    if seen["DATABASE_URL"][:2] != seen["DATABASE_ADMIN_URL"][:2]:
        raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL reach different databases "
                           f"({describe_url(DATABASE_URL)} vs {describe_url(ADMIN_DATABASE_URL)}); they must be the same one.")
    u = make_url(DATABASE_URL)
    return (f"PostgreSQL {seen['DATABASE_URL'][2]} at {u.host}:{u.port}/{u.database} "
            f"(runtime role {u.username}, owner role {make_url(ADMIN_DATABASE_URL).username})")


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- RLS scope stamping (PostgreSQL)
_SCOPE_SQL = text("SELECT set_config('app.tenant_id', :t, true), set_config('app.system', :s, true)")


def _stamp(conn, session: Session) -> None:
    conn.execute(_SCOPE_SQL, {"t": session.info.get("tenant_id") or "",
                              "s": "on" if session.info.get("system") else "off"})


if IS_POSTGRES:
    @event.listens_for(SessionLocal, "after_begin")
    def _apply_scope(session, _transaction, connection):  # every transaction carries the scope
        _stamp(connection, session)


def set_tenant_scope(db: Session, tenant_id: str | None) -> None:
    db.info["tenant_id"] = tenant_id
    if IS_POSTGRES and db.in_transaction():
        _stamp(db.connection(), db)


def set_system_scope(db: Session, on: bool) -> None:
    db.info["system"] = on
    if IS_POSTGRES and db.in_transaction():
        _stamp(db.connection(), db)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
