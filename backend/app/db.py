"""Database bootstrap (ZA-DAT-001).

PostgreSQL is the system of record (``DATABASE_URL``); SQLite is a zero-setup fallback.

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

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'assure.db').as_posix()}")
ADMIN_DATABASE_URL = os.environ.get("DATABASE_ADMIN_URL", DATABASE_URL)
CELL_ID = os.environ.get("ZA_CELL_ID", "eu-west-cell-a")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")


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
    return create_engine(url, future=True, pool_pre_ping=True, pool_size=10, max_overflow=10)


engine = _make_engine(DATABASE_URL)
admin_engine = engine if ADMIN_DATABASE_URL == DATABASE_URL else _make_engine(ADMIN_DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AdminSession = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)


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
