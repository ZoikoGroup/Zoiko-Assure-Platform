"""Automatic migration of explicitly approved, disposable local development databases.

``python -m app.migrate upgrade`` stays the one migration runner; this module only decides whether it may run on its
own, then calls it. Automatic migration needs ALL of:

1. ``ZA_ENVIRONMENT=development`` (exactly). Production, staging, test, unset or anything else is refused.
2. ``ZA_AUTO_MIGRATE`` set to ``1``/``true``/``yes``. Unset: the API behaves exactly as before.
3. ``DATABASE_ADMIN_URL`` and ``DATABASE_URL``: PostgreSQL, the same host, port and database.
4. A loopback host (``127.0.0.1``, ``localhost``, ``::1``). A shared or remote database is never approved.
5. ``host:port/database`` listed exactly in ``ZA_AUTO_MIGRATE_DATABASES`` (comma-separated).
6. Not protected: the database name ``assure`` (the canonical local database) is protected on every host, as is anything
   in ``ZA_PROTECTED_DATABASES`` (names or ``host:port/database`` entries).
7. The connected server says it is that database (``current_database()``), and the database carries the approval
   marker as its comment: ``COMMENT ON DATABASE <name> IS 'zoiko-assure:auto-migrate-approved'``, which only the
   database's owner can set. The environment label alone never makes a database disposable.

Why a separate process. The API holds no owner or migration credential (DAT-REQ-004; ZA-DAT-001 §13: the migrator has
"no normal application use"), and backend/.env loading skips ``DATABASE_ADMIN_URL`` unless the process is an operator
tool. At start-up the API therefore runs ``python -m app.devmigrate`` as a child process. The child alone reads the
migration credential; the API process never does. The API then performs its usual read-only checks, so the schema is
verified independently of the child.

Concurrency: ``migrate.upgrade`` already runs in one transaction holding the migration advisory lock (724501), and
re-inspects the database after taking it. Processes starting together serialise; the first applies the revisions and
the others find the database current. A failed revision rolls back completely and the API does not start.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("uvicorn.error")

BACKEND_DIR = Path(__file__).resolve().parent.parent
APPROVAL_MARKER = "zoiko-assure:auto-migrate-approved"
PROTECTED_NAMES = frozenset({"assure"})  # the canonical local database: migrated only by a person, after a backup
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
TRUE = frozenset({"1", "true", "yes"})
NEVER_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage", "preprod", "pre-production"})
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 3
_URL_SECRET = re.compile(r"(://[^:/@\s]+):[^@\s]*@")


class Refused(RuntimeError):
    """Automatic migration is not permitted here. Nothing was changed."""


@dataclass(frozen=True)
class Outcome:
    where: str       # user@host:port/database, never a password
    action: str      # "CURRENT" (nothing pending) or "MIGRATED"
    before: str      # state description before
    after: str       # state description after


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove URL passwords and any known secret value from text bound for logs or errors."""
    out = _URL_SECRET.sub(r"\1:***@", text or "")
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "***")
    return out


def enabled(environ=os.environ) -> bool:
    return (environ.get("ZA_AUTO_MIGRATE") or "").strip().lower() in TRUE


def environment_refusal(environ=os.environ) -> str | None:
    env = (environ.get("ZA_ENVIRONMENT") or "").strip().lower()
    if env == "development":
        return None
    if env in NEVER_ENVIRONMENTS:
        return f"ZA_ENVIRONMENT is {env!r}: automatic migration never runs there; migrations are a reviewed deployment step"
    return (f"ZA_ENVIRONMENT is {env or 'unset'!r}: automatic migration runs only when ZA_ENVIRONMENT is exactly "
            "'development' (fail closed)")


def _key(host: str | None, port: int | None, database: str | None) -> str:
    return f"{(host or '').strip().lower()}:{port or 5432}/{database or ''}"


def _parse_entries(raw: str | None) -> set[str]:
    out = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "/" in item and ":" in item.split("/", 1)[0]:
            hostport, db = item.split("/", 1)
            host, port = hostport.rsplit(":", 1)
            out.add(_key(host, int(port), db.strip()))
        else:
            out.add(item.lower())  # a bare database name (protected list only)
    return out


def static_refusal(environ=os.environ) -> str | None:
    """Every check that needs no connection. None means the database may be contacted for the identity check."""
    if not enabled(environ):
        return "ZA_AUTO_MIGRATE is not enabled"
    if (why := environment_refusal(environ)) is not None:
        return why
    from sqlalchemy.engine import make_url
    admin, runtime = environ.get("DATABASE_ADMIN_URL") or "", environ.get("DATABASE_URL") or ""
    if not admin.startswith("postgresql") or not runtime.startswith("postgresql"):
        return "DATABASE_ADMIN_URL and DATABASE_URL must both be PostgreSQL URLs (migrations run on PostgreSQL only)"
    a, r = make_url(admin), make_url(runtime)
    key = _key(a.host, a.port, a.database)
    if key != _key(r.host, r.port, r.database):
        return "DATABASE_ADMIN_URL and DATABASE_URL name different databases; the target cannot be identified"
    if (a.host or "").strip().lower() not in LOOPBACK:
        return f"{key}: not a loopback host; shared or remote databases are never migrated automatically"
    protected = PROTECTED_NAMES | _parse_entries(environ.get("ZA_PROTECTED_DATABASES"))
    if (a.database or "").lower() in protected or key in protected:
        return (f"{key}: a protected database; it is migrated only manually, after a verified backup "
                "(docs/database-migrations.md)")
    if key not in _parse_entries(environ.get("ZA_AUTO_MIGRATE_DATABASES")):
        return f"{key}: not listed in ZA_AUTO_MIGRATE_DATABASES"
    return None


def identity_refusal(conn, expected_database: str) -> str | None:
    """The connected server is the allowlisted database and its owner has marked it disposable."""
    from sqlalchemy import text
    actual, marker = conn.execute(text(
        "SELECT current_database(), shobj_description(d.oid, 'pg_database') FROM pg_database d "
        "WHERE d.datname = current_database()")).one()
    if actual != expected_database:
        return f"the connected database is {actual!r}, not the allowlisted {expected_database!r}"
    if (marker or "").strip() != APPROVAL_MARKER:
        return (f"database {actual!r} does not carry the approval marker; as its owner run: "
                f"COMMENT ON DATABASE \"{actual}\" IS '{APPROVAL_MARKER}'")
    return None


def run(environ=os.environ, *, engine=None, schema: str = "public", extra_version_locations=()) -> Outcome:
    """Apply pending revisions to an approved database with the existing runner, then verify. Raises Refused (nothing
    changed), migrate.MigrationRefused (the runner refused the database state), or the migration's own error (rolled
    back). Verification failures raise RuntimeError."""
    if (why := static_refusal(environ)) is not None:
        raise Refused(why)
    from sqlalchemy.engine import make_url
    from . import migrate
    from .db import describe_url
    where = describe_url(environ["DATABASE_ADMIN_URL"])
    engine = engine or migrate._admin_engine()
    with engine.connect() as conn:
        try:
            if (why := identity_refusal(conn, make_url(environ["DATABASE_ADMIN_URL"]).database)) is not None:
                raise Refused(why)
            # read-only, against the same revision locations the upgrade will use
            first = migrate.inspect(conn, schema, migrate.alembic_config(conn, schema, extra_version_locations))
        finally:
            conn.rollback()
    if first.kind == migrate.CURRENT:
        return Outcome(where, "CURRENT", first.describe(), first.describe())
    before, after = migrate.upgrade(schema=schema, engine=engine, extra_version_locations=extra_version_locations)
    final = migrate.status(schema=schema, engine=engine)
    if final.kind != migrate.CURRENT:
        raise RuntimeError(f"after the upgrade the database is not current: {final.describe()}")
    if problems := migrate.check(schema=schema, engine=engine):
        raise RuntimeError("post-migration check failed: " + "; ".join(problems))
    # before is inspected under the migration lock: CURRENT means another process applied the revisions first
    return Outcome(where, "CURRENT" if before.kind == migrate.CURRENT else "MIGRATED", before.describe(limit=0),
                   final.describe())


# ---------------------------------------------------------------- CLI: the child process the API starts
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m app.devmigrate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--schema", default="public", help=argparse.SUPPRESS)  # tests: a throwaway schema
    args = ap.parse_args(argv)
    os.environ.setdefault("ZA_ADMIN_TOOL", "1")  # an operator tool: backend/.env may supply DATABASE_ADMIN_URL
    secrets = ()
    try:
        from sqlalchemy.engine import make_url
        from . import db  # noqa: F401 - loads backend/.env as an operator tool
        secrets = tuple(p for p in (make_url(u).password for u in (os.environ.get("DATABASE_ADMIN_URL"),
                                                                    os.environ.get("DATABASE_URL")) if u) if p)
        out = run(os.environ, schema=args.schema)
        if out.action == "MIGRATED":
            print(f"AUTO-MIGRATE {out.where}: applied; before: {out.before}; after: {out.after}; check passed")
            if args.schema == "public":  # as `python -m app.migrate upgrade` does: idempotent account bootstrap
                from .migrate import provision_data
                for line in provision_data():
                    print(f"AUTO-MIGRATE {out.where}: account bootstrap: {line}")
        else:
            print(f"AUTO-MIGRATE {out.where}: nothing to apply ({out.after})")
        return EXIT_OK
    except Refused as e:
        print(f"AUTO-MIGRATE REFUSED: {redact(str(e), secrets)}. Nothing was changed.", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:  # noqa: BLE001 - the migration rolled back; report the reason without secrets
        print(f"AUTO-MIGRATE FAILED: {type(e).__name__}: {redact(str(e), secrets)}. The migration transaction rolled "
              "back; the database is at its previous revision.", file=sys.stderr)
        return EXIT_FAILED


# ---------------------------------------------------------------- API start-up hook
def _spawn() -> subprocess.CompletedProcess:
    env = dict(os.environ, ZA_ADMIN_TOOL="1")
    return subprocess.run([sys.executable, "-m", "app.devmigrate"], cwd=BACKEND_DIR, env=env, capture_output=True,
                          text=True, timeout=int(os.environ.get("ZA_AUTO_MIGRATE_TIMEOUT", "600")))


def startup_auto_migrate(environ=os.environ, runner=_spawn) -> str | None:
    """Called by the API before its read-only schema check. Returns None when nothing stands in the way, or the reason
    automatic migration was refused (the API then starts only if the schema is already current). Raises RuntimeError,
    so the API does not start, when the flag is set outside development or when a migration failed."""
    if not enabled(environ):
        return None  # unchanged behaviour
    if (why := environment_refusal(environ)) is not None:
        raise RuntimeError(f"Refusing to start: ZA_AUTO_MIGRATE is set but {why}. Unset ZA_AUTO_MIGRATE.")
    r = runner()
    lines = [ln for ln in (redact(r.stdout) + "\n" + redact(r.stderr)).splitlines() if ln.strip()]
    if r.returncode == EXIT_OK:
        for ln in lines:
            log.info("%s", ln)
        return None
    if r.returncode == EXIT_REFUSED:
        reason = next((ln for ln in lines if "AUTO-MIGRATE REFUSED" in ln), "automatic migration refused")
        log.warning("%s", reason)
        return reason
    tail = " | ".join(lines[-5:]) or f"exit code {r.returncode}"
    raise RuntimeError(f"Refusing to start: automatic development migration failed: {tail}")


if __name__ == "__main__":
    sys.exit(main())
