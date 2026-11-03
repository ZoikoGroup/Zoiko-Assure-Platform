"""Database bootstrap (ZA-DAT-001).

PostgreSQL is the system of record and the only runtime database (``DATABASE_URL``). There is no silent fallback:
without a configured database the API refuses to start. SQLite exists only for isolated test runs, which must ask for
it explicitly with ``ZA_ALLOW_SQLITE=1`` (``tests/conftest.py`` does).

On PostgreSQL two login roles are used (ZA-DAT-001 §13; migration 0007):

* ``DATABASE_URL``        — the API runtime login (``assure_app``, a member of ``assure_app_rw``). Row-level security
  (FORCE) applies to it. Every transaction is stamped with a **signed** context (``app.context``): tenant, actor,
  session, capability flags and a per-transaction nonce, HMAC-SHA256 under the context key. The policies trust only
  what the database itself verifies (``assure_context()``), so SQL run as this role cannot choose a tenant, switch on
  system scope or reach identity tables without the key, which only this process holds (finding I-01).
* ``DATABASE_ADMIN_URL``  — the migration login (a member of ``assure_migrator``). Operator tooling only:
  ``python -m app.migrate`` and the test harness. The API never reads, holds or uses it: backend/.env loading skips it
  unless the process is an operator tool (``ZA_ADMIN_TOOL=1``).

A small number of documented cell-wide platform jobs (rule-release impact re-evaluation, shadow
impact, ledger checkpoints) run under an explicit ``system_scope``; see ``app.security``.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent


ADMIN_TOOL = os.environ.get("ZA_ADMIN_TOOL") == "1"  # python -m app.migrate and alembic set it; the API never does


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables always win. The owner/migration credential is
    loaded only into operator tools, never into the API process (DAT-REQ-004; Task 2)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "DATABASE_ADMIN_URL" and not ADMIN_TOOL:
            continue
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if os.environ.get("ZA_SKIP_DOTENV") != "1":
    _load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("ZA_DATA_DIR", BASE_DIR / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOW_SQLITE = os.environ.get("ZA_ALLOW_SQLITE") == "1"  # isolated test runs only
DATABASE_URL = os.environ.get("DATABASE_URL") or (f"sqlite:///{(DATA_DIR / 'assure.db').as_posix()}" if ALLOW_SQLITE else "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Zoiko Assure runs on PostgreSQL: set DATABASE_URL (runtime role) in "
                       "backend/.env or the environment, and DATABASE_ADMIN_URL (migration role) for `python -m "
                       "app.migrate`; see backend/.env.example.")
if DATABASE_URL.startswith("sqlite") and not ALLOW_SQLITE:
    raise RuntimeError("DATABASE_URL points at SQLite, which is only for isolated test runs (ZA_ALLOW_SQLITE=1). "
                       "Zoiko Assure runs on PostgreSQL; see backend/.env.example.")
CELL_ID = os.environ.get("ZA_CELL_ID", "eu-west-cell-a")

IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
# Operator tools and the test harness only; empty in the API process on PostgreSQL. SQLite test runs use one database.
ADMIN_DATABASE_URL = os.environ.get("DATABASE_ADMIN_URL", "" if IS_POSTGRES else DATABASE_URL)
if ADMIN_DATABASE_URL and IS_POSTGRES != ADMIN_DATABASE_URL.startswith("postgresql"):
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
    eng = create_engine(url, future=True, pool_pre_ping=True, pool_size=10, max_overflow=10,
                        connect_args={"connect_timeout": int(os.environ.get("ZA_DB_CONNECT_TIMEOUT", "10"))})

    @event.listens_for(eng, "reset")
    def _reset_on_checkin(dbapi_conn, _record, reset_state):  # pragma: no cover - driver hook
        """ZA-DAT-001 §12 "Connection pooling": no tenant/principal state survives on a pooled connection. The tenant
        context is transaction-local already (_stamp); this also clears what a transaction-local stamp cannot: a
        session-level setting (e.g. a non-local set_config of app.tenant_id/app.system) and temporary tables, which
        would shadow real tables for the next request on this connection. Runs before the pool's own rollback."""
        dbapi_conn.rollback()
        if reset_state.terminate_only:
            return
        with dbapi_conn.cursor() as cur:
            cur.execute("RESET ALL")
            cur.execute("DISCARD TEMP")
        dbapi_conn.commit()
    return eng


engine = _make_engine(DATABASE_URL)
admin_engine = (engine if ADMIN_DATABASE_URL == DATABASE_URL
                else _make_engine(ADMIN_DATABASE_URL) if ADMIN_DATABASE_URL else None)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AdminSession = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)


def check_database(runtime: bool = True) -> str:
    """Connect with both roles and confirm they reach the same PostgreSQL database. Fails with a clear message that
    never contains a password. Returns a description for the start-up log.

    ``runtime=False`` checks the migration role only (on a fresh database the runtime role does not exist until
    ``python -m app.migrate upgrade`` provisions it). The API has no migration credential and checks the runtime role
    only."""
    from sqlalchemy.engine import make_url
    if IS_SQLITE:
        return f"SQLite {make_url(DATABASE_URL).database} (isolated test run)"
    seen = {}
    roles = (("DATABASE_ADMIN_URL", admin_engine, ADMIN_DATABASE_URL), ("DATABASE_URL", engine, DATABASE_URL))
    if not ADMIN_DATABASE_URL:
        roles = roles[1:]
    elif not runtime:
        roles = roles[:1]
    for name, eng, url in roles:
        try:
            with eng.connect() as conn:
                seen[name] = conn.execute(text("SELECT current_database(), inet_server_port(), current_setting('server_version')")).one()
        except Exception as e:  # noqa: BLE001 - re-raised below with a secret-free message
            reason = str(getattr(e, "orig", None) or e).strip().splitlines()[0]
            hint = (" On a new database, run `python -m app.migrate upgrade` first: it provisions the runtime role."
                    if name == "DATABASE_URL" else "")
            raise RuntimeError(f"Cannot connect to PostgreSQL with {name} ({describe_url(url)}): {reason}. Is the "
                               "PostgreSQL service running, and do the role, password and database name match?"
                               + hint) from None
    if "DATABASE_URL" not in seen:
        return f"migration role reachable at {describe_url(ADMIN_DATABASE_URL)}"
    if "DATABASE_ADMIN_URL" not in seen:
        return f"PostgreSQL {seen['DATABASE_URL'][2]} at {describe_url(DATABASE_URL)} (runtime role only)"
    if seen["DATABASE_URL"][:2] != seen["DATABASE_ADMIN_URL"][:2]:
        raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL reach different databases "
                           f"({describe_url(DATABASE_URL)} vs {describe_url(ADMIN_DATABASE_URL)}); they must be the same one.")
    u = make_url(DATABASE_URL)
    return (f"PostgreSQL {seen['DATABASE_URL'][2]} at {u.host}:{u.port}/{u.database} "
            f"(runtime role {u.username}, owner role {make_url(ADMIN_DATABASE_URL).username})")


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- verified transaction context (PostgreSQL, 0007)
# The key never reaches the database in usable form for the runtime role: migration 0007 keeps only the HMAC inner and
# outer pads, in a table only the non-login owner can read. `python -m app.migrate upgrade` provisions both sides.
CONTEXT_KEY_FILE = DATA_DIR / "keys" / "db_context.key"
_context_key_cache: bytes | None = None


def context_key(create: bool = False) -> bytes:
    """The context key: ZA_DB_CONTEXT_KEY (hex) or the key file. Only provisioning (`create=True`) may generate it."""
    global _context_key_cache
    if _context_key_cache is not None and not create:
        return _context_key_cache
    if os.environ.get("ZA_DB_CONTEXT_KEY"):
        key = bytes.fromhex(os.environ["ZA_DB_CONTEXT_KEY"])
    elif CONTEXT_KEY_FILE.exists():
        key = CONTEXT_KEY_FILE.read_bytes()
    elif create:
        CONTEXT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        CONTEXT_KEY_FILE.write_bytes(key)
        os.chmod(CONTEXT_KEY_FILE, 0o600)
    else:
        raise RuntimeError(f"No database context key ({CONTEXT_KEY_FILE} or ZA_DB_CONTEXT_KEY). Run `python -m "
                           "app.migrate upgrade`, which provisions it for the database and this process.")
    if not 32 <= len(key) <= 64:
        raise RuntimeError("The database context key must be 32 to 64 bytes.")
    _context_key_cache = key
    return key


def context_key_id(key: bytes) -> str:
    """A non-secret identifier of the key, so start-up can tell whether the database holds the same one."""
    return hashlib.sha256(b"za-db-context-key:" + key).hexdigest()[:16]


def context_key_pads(key: bytes) -> tuple[bytes, bytes]:
    """HMAC-SHA256 inner and outer key pads (RFC 2104): what the database stores instead of the key."""
    k = key.ljust(64, b"\0")
    return bytes(b ^ 0x36 for b in k), bytes(b ^ 0x5C for b in k)


def context_string(nonce: str, *, tenant: str | None = None, actor: str | None = None, session: str | None = None,
                   flags=()) -> str:
    """``v1|tenant|actor|session|flags|nonce|mac``, verified by the database's assure_context()."""
    parts = ["v1", tenant or "", actor or "", session or "", ",".join(sorted(flags)), nonce]
    if any("|" in p for p in parts) or any("," in p for p in parts[1:4]):
        raise ValueError("context values cannot contain '|' or ','")
    body = "|".join(parts)
    return body + "|" + hmac.new(context_key(), body.encode(), hashlib.sha256).hexdigest()


_NONCE_SQL = text("SELECT assure_context_nonce()")
_CONTEXT_SQL = text("SELECT set_config('app.context', :c, true)")


def _stamp(conn, session: Session, *, fresh: bool = False) -> None:
    if fresh or "_ctx_nonce" not in session.info:  # one nonce per transaction: backend PID + transaction start
        session.info["_ctx_nonce"] = conn.execute(_NONCE_SQL).scalar_one()
    flags = set(session.info.get("caps", ()))
    if session.info.get("system"):
        flags.add("system")
    conn.execute(_CONTEXT_SQL, {"c": context_string(
        session.info["_ctx_nonce"], tenant=session.info.get("tenant_id"), actor=session.info.get("actor_id"),
        session=session.info.get("auth_session_id"), flags=flags)})


if IS_POSTGRES:
    @event.listens_for(SessionLocal, "after_begin")
    def _apply_scope(session, _transaction, connection):  # every transaction carries the scope
        _stamp(connection, session, fresh=True)


def _restamp(db: Session, *, quiet: bool = False) -> None:
    if not (IS_POSTGRES and db.in_transaction()):
        return  # the next transaction is stamped from db.info when it begins
    try:
        _stamp(db.connection(), db)
    except SQLAlchemyError:
        if not quiet:
            raise  # quiet: the transaction has already failed and will be rolled back; the next one is stamped afresh


def set_tenant_scope(db: Session, tenant_id: str | None) -> None:
    db.info["tenant_id"] = tenant_id
    _restamp(db)


def set_principal(db: Session, tenant_id: str | None, actor_id: str | None, session_id: str | None = None) -> None:
    """The authenticated principal, its organisation and its session, in the database context (one re-stamp)."""
    db.info["tenant_id"], db.info["actor_id"], db.info["auth_session_id"] = tenant_id, actor_id, session_id
    _restamp(db)


def set_system_scope(db: Session, on: bool) -> None:
    db.info["system"] = on
    _restamp(db)


@contextmanager
def capability(db: Session, name: str):
    """A signed capability for the enclosed work only, e.g. ``auth``: the identity tables (auth_session,
    auth_credential, sign-in lookup of org_actor) are reachable only inside such a block (migration 0007)."""
    before = frozenset(db.info.get("caps", ()))
    db.info["caps"] = before | {name}
    _restamp(db)
    try:
        yield
    finally:
        db.info["caps"] = before
        _restamp(db, quiet=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
