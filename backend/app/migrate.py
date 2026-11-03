"""Versioned schema migrations for Zoiko Assure (ZA-DAT-001 DAT-REQ-023/039/040; docs/database-migrations.md).

The API never creates or alters its schema. Schema history is the ordered Alembic revisions in ``backend/migrations``,
applied by this tool with the owner/migration role (``DATABASE_ADMIN_URL``) in ONE transaction that holds the
migration lock, so a failed run leaves the database exactly as it was:

    python -m app.migrate status                     read-only: what state is the database in?
    python -m app.migrate upgrade                    bring it to the head revision
    python -m app.migrate check                      read-only: at head, no drift from the models, RLS coverage
    python -m app.migrate downgrade REV --confirm    disposable databases and rehearsals only
    python -m app.migrate fingerprint [--write]      maintainers: the normalised schema description

Database states (``inspect``):

* EMPTY                 no tables: ``upgrade`` builds the schema from revision 0001.
* CURRENT / BEHIND      under version control: ``upgrade`` applies the pending revisions in order.
* UNVERSIONED_BASELINE  created by the old API start-up (``create_all`` + RLS SQL) and identical to the baseline
                        recorded in ``migrations/baseline_schema.json``: ``upgrade`` records revision 0002 (the
                        baseline) and continues from there. Nothing is re-created.
* UNVERSIONED_MISMATCH  tables exist without version control and do NOT match the baseline (drift, a partial schema,
                        another application): refused, nothing is changed. Reconcile with a reviewed corrective
                        migration or restore, then retry.
* UNKNOWN_REVISION      the recorded revision is not one this build knows (a newer build ran, or a branch): refused.
* INCONSISTENT          a version table with no revision next to existing tables: refused.

The runtime role (``assure_app``) is provisioned and checked here, as the API start-up used to do, and never holds DDL
rights (DAT-REQ-004). From revision 0007 it holds its privileges through ``assure_app_rw`` (ZA-DAT-001 §13), and
``upgrade`` also provisions the database copy of the context key that the API signs each transaction's tenant context
with. ``upgrade`` from the command line then runs ``provision_data``: the one-time bootstrap accounts and, with
ZA_DEMO_MODE=1, the demo fixture. The API itself holds no migration credential.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BACKEND_DIR / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"
BASELINE_FILE = MIGRATIONS_DIR / "baseline_schema.json"
BASELINE_REVISION = "0002"   # the schema the old API start-up produced: create_all (0001) + postgres_rls.sql (0002)
MIGRATION_LOCK = 724501      # the advisory lock the old start-up bootstrap held, so the two can never interleave
RUNTIME_ROLE = "assure_app"  # named by the RLS policies and grants in revision 0002
RLS_EXEMPT: set[str] = set()  # none since 0007 (org_actor: identity discovery is the signed `auth` capability)
OWNER_ROLE = "assure_table_owner"  # migration 0007: the non-login owner of every object in the schema (ZA-DAT-001 §13)
NOT_MODELLED = {"alembic_version", "assure_context_key"}  # tables the migrations own outside app/models.py

EMPTY, CURRENT, BEHIND = "EMPTY", "CURRENT", "BEHIND"
UNVERSIONED_BASELINE, UNVERSIONED_MISMATCH = "UNVERSIONED_BASELINE", "UNVERSIONED_MISMATCH"
UNVERSIONED, UNKNOWN_REVISION, INCONSISTENT = "UNVERSIONED", "UNKNOWN_REVISION", "INCONSISTENT"
REFUSED = {UNVERSIONED_MISMATCH, UNKNOWN_REVISION, INCONSISTENT}


class SchemaVersionError(RuntimeError):
    """The database is not at the schema revision this build requires; the API must not start."""


class MigrationRefused(RuntimeError):
    """The database is in a state the tool will not migrate automatically; nothing was changed."""

    def __init__(self, state: "DbState"):
        self.state = state
        super().__init__(state.describe())


@dataclass
class DbState:
    kind: str
    head: str
    revision: str | None = None
    pending: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    tables: int = 0

    def describe(self, limit: int = 25) -> str:
        msg = {
            EMPTY: f"empty database ({self.tables} tables); `upgrade` builds the schema up to {self.head}",
            CURRENT: f"at the head revision {self.head}",
            BEHIND: f"at revision {self.revision}; pending {', '.join(self.pending)} (head {self.head})",
            UNVERSIONED_BASELINE: (f"not under version control, {self.tables} tables matching the {BASELINE_REVISION} "
                                   f"baseline exactly; `upgrade` records {BASELINE_REVISION} without re-creating anything"
                                   + ("" if self.head == BASELINE_REVISION else f", then continues to {self.head}")),
            UNVERSIONED_MISMATCH: (f"not under version control and NOT matching the {BASELINE_REVISION} baseline "
                                   f"({len(self.differences)} difference(s)); refused, nothing changed"),
            UNVERSIONED: f"not under version control ({self.tables} tables); run `python -m app.migrate status`",
            UNKNOWN_REVISION: f"recorded revision {self.revision!r} is unknown to this build (head {self.head}); refused",
            INCONSISTENT: f"version table without a revision next to {self.tables} tables; refused",
        }[self.kind]
        if self.differences:
            shown = self.differences[:limit]
            more = len(self.differences) - len(shown)
            msg += "\n  " + "\n  ".join(shown) + (f"\n  ... and {more} more" if more > 0 else "")
        return msg


# ---------------------------------------------------------------- alembic plumbing
def alembic_config(connection=None, schema: str = "public", extra_version_locations=()) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("version_locations", os.pathsep.join([str(VERSIONS_DIR), *map(str, extra_version_locations)]))
    cfg.attributes.update(connection=connection, schema=schema, za_migrate_tool=True)
    return cfg


def head_revision(cfg: Config | None = None) -> str:
    return ScriptDirectory.from_config(cfg or alembic_config()).get_current_head()


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scoped(conn, schema: str) -> None:
    """Introspection and migrations resolve unqualified names in `schema` only, for the rest of this transaction."""
    conn.exec_driver_sql(f"SET LOCAL search_path TO {_qi(schema)}")


def app_tables(conn, schema: str = "public") -> set[str]:
    rows = conn.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'"),
                        {"s": schema}).scalars()
    return set(rows)


def _recorded_revisions(conn, schema: str) -> tuple[bool, list[str]]:
    exists = conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"{_qi(schema)}.alembic_version"}).scalar()
    if not exists:
        return False, []
    revs = conn.execute(text(f"SELECT version_num FROM {_qi(schema)}.alembic_version")).scalars().all()
    return True, list(revs)


# ---------------------------------------------------------------- schema fingerprint (read-only)
_FINGERPRINT_SQL = {
    "tables": """SELECT c.relname || '|kind=' || c.relkind::text || '|rls=' || c.relrowsecurity::text
                        || '|force=' || c.relforcerowsecurity::text
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = :s AND c.relkind IN ('r', 'p', 'v', 'm', 'S') AND c.relname <> 'alembic_version'""",
    "columns": """SELECT c.relname || '.' || a.attname || '|' || format_type(a.atttypid, a.atttypmod)
                        || CASE WHEN a.attnotnull THEN '|NOT NULL' ELSE '|NULL' END
                        || '|default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '')
                 FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                 LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                 WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
                   AND c.relname <> 'alembic_version'""",
    "constraints": """SELECT c.relname || '|' || con.conname || '|' || con.contype::text || '|' || pg_get_constraintdef(con.oid)
                      FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      WHERE n.nspname = :s AND c.relname <> 'alembic_version'""",
    "indexes": """SELECT t.relname || '|' || i.relname || '|' || pg_get_indexdef(i.oid)
                  FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
                  JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = :s AND t.relname <> 'alembic_version'""",
    "policies": """SELECT tablename || '|' || policyname || '|' || permissive || '|' || array_to_string(roles, ',')
                          || '|' || cmd || '|' || coalesce(qual, '') || '|' || coalesce(with_check, '')
                   FROM pg_policies WHERE schemaname = :s""",
    "triggers": """SELECT c.relname || '|' || t.tgname || '|' || pg_get_triggerdef(t.oid)
                   FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = :s AND NOT t.tgisinternal""",
    "functions": """SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')|' || pg_get_functiondef(p.oid)
                    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = :s""",
    "grants": """SELECT c.relname || '|' || a.privilege_type
                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee
                 WHERE n.nspname = :s AND r.rolname = :role AND c.relname <> 'alembic_version'""",
    "schema_grants": """SELECT a.privilege_type FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
                        JOIN pg_roles r ON r.oid = a.grantee WHERE n.nspname = :s AND r.rolname = :role""",
}


def fingerprint(conn, schema: str = "public") -> dict[str, list[str]]:
    """A normalised, order-independent description of every object in `schema` that the migrations own (tables, RLS
    flags, columns, constraints, indexes, policies, triggers, functions, runtime-role grants). Read-only. The schema
    name is removed from object definitions so a throwaway test schema and `public` compare equal."""
    _scoped(conn, schema)
    out = {}
    for key, sql in _FINGERPRINT_SQL.items():
        rows = conn.execute(text(sql), {"s": schema, "role": RUNTIME_ROLE}).scalars().all()
        out[key] = sorted(r.replace(f"{_qi(schema)}.", "").replace(f"{schema}.", "") for r in rows)
    return out


def fingerprint_digest(fp: dict) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_baseline() -> dict:
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def compare_fingerprints(expected: dict, actual: dict) -> list[str]:
    diffs = []
    for key in sorted(set(expected) | set(actual)):
        exp, act = set(expected.get(key, [])), set(actual.get(key, []))
        diffs += [f"{key}: missing from database: {x}" for x in sorted(exp - act)]
        diffs += [f"{key}: not in baseline: {x}" for x in sorted(act - exp)]
    return diffs


# ---------------------------------------------------------------- state detection
def inspect(conn, schema: str = "public", cfg: Config | None = None, validate: bool = True) -> DbState:
    """Classify the database. Read-only. `validate` compares an unversioned schema with the baseline fingerprint."""
    cfg = cfg or alembic_config(schema=schema)
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    tables = app_tables(conn, schema)
    has_vt, revs = _recorded_revisions(conn, schema)
    if has_vt and revs:
        if len(revs) != 1:
            return DbState(UNKNOWN_REVISION, head, ",".join(revs), tables=len(tables))
        rev = revs[0]
        try:
            ancestors = {r.revision for r in script.iterate_revisions(head, "base")}
        except CommandError:
            ancestors = set()
        if rev not in ancestors:
            return DbState(UNKNOWN_REVISION, head, rev, tables=len(tables))
        if rev == head:
            return DbState(CURRENT, head, rev, tables=len(tables))
        pending = [r.revision for r in script.iterate_revisions(head, rev)][::-1]
        return DbState(BEHIND, head, rev, pending=pending, tables=len(tables))
    if has_vt:
        return DbState(EMPTY if not tables else INCONSISTENT, head, tables=len(tables))
    if not tables:
        return DbState(EMPTY, head)
    if not validate:
        return DbState(UNVERSIONED, head, tables=len(tables))
    diffs = compare_fingerprints(load_baseline()["fingerprint"], fingerprint(conn, schema))
    return DbState(UNVERSIONED_MISMATCH if diffs else UNVERSIONED_BASELINE, head, differences=diffs, tables=len(tables))


# ---------------------------------------------------------------- operations
def _admin_engine():
    from .db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        raise SystemExit("Migrations run on PostgreSQL only (SQLite exists for isolated test runs).")
    return admin_engine


def upgrade(target: str = "head", *, schema: str = "public", engine=None, extra_version_locations=(),
            provision_role: bool = True) -> tuple[DbState, DbState]:
    """Validate, then migrate to `target` in one transaction under the migration lock. Returns (before, after).
    Raises MigrationRefused, with nothing changed, for any state it will not migrate automatically."""
    engine = engine or _admin_engine()
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
            if provision_role:
                from .pg_bootstrap import ensure_runtime_role
                ensure_runtime_role(conn)
            cfg = alembic_config(conn, schema, extra_version_locations)
            before = inspect(conn, schema, cfg)
            if before.kind in REFUSED:
                raise MigrationRefused(before)
            if before.kind == UNVERSIONED_BASELINE:
                command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, target)
            provision_context_key(conn, schema)
            after = inspect(conn, schema, cfg, validate=False)
    return before, after


def provision_context_key(conn, schema: str = "public") -> None:
    """The database copy of the API's context key (migration 0007), as HMAC pads only. Creates the key file on first use;
    rewrites the row only when the key changed. No-op below revision 0007."""
    if not conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"{_qi(schema)}.assure_context_key"}).scalar():
        return
    from .db import context_key, context_key_id, context_key_pads
    key = context_key(create=True)
    ipad, opad = context_key_pads(key)
    t = f"{_qi(schema)}.assure_context_key"
    conn.execute(text(f"INSERT INTO {t} (id, key_id, k_ipad, k_opad) VALUES (1, :i, :a, :b) ON CONFLICT (id) DO UPDATE "
                      f"SET key_id = EXCLUDED.key_id, k_ipad = EXCLUDED.k_ipad, k_opad = EXCLUDED.k_opad, "
                      f"provisioned_at = now() WHERE {t}.key_id IS DISTINCT FROM EXCLUDED.key_id"),
                 {"i": context_key_id(key), "a": ipad, "b": opad})


def provision_data(env=None) -> list[str]:
    """Operator step after migrating (not API start-up): the one-time bootstrap accounts from the environment and, with
    ZA_DEMO_MODE=1, the synthetic demo fixture. Runs as the migration role (or the one SQLite test database)."""
    from . import auth
    from .accounts import bootstrap_accounts
    from .db import AdminSession
    from .seed import seed_if_empty
    if auth.DEMO_MODE:
        with AdminSession() as db:
            seed_if_empty(db)
            auth.ensure_demo_credentials(db)
    with AdminSession() as db:
        return bootstrap_accounts(db, env if env is not None else os.environ)


def downgrade(target: str, *, confirm: bool = False, schema: str = "public", engine=None) -> DbState:
    """Disposable databases and rehearsals only. 0002 → 0001 removes RLS and the runtime grants; 0001 is irreversible."""
    if not confirm:
        raise SystemExit("Refusing to downgrade without --confirm. Production recovery is forward or a restore from "
                         "backup (docs/database-migrations.md).")
    engine = engine or _admin_engine()
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK})
            cfg = alembic_config(conn, schema)
            before = inspect(conn, schema, cfg, validate=False)
            if before.kind not in (CURRENT, BEHIND):
                raise MigrationRefused(before)
            command.downgrade(cfg, target)
            return inspect(conn, schema, cfg, validate=False)


def status(*, schema: str = "public", engine=None) -> DbState:
    engine = engine or _admin_engine()
    with engine.connect() as conn:
        try:
            return inspect(conn, schema)
        finally:
            conn.rollback()


def require_current(*, schema: str = "public", engine=None) -> str:
    """API start-up gate. Read-only and quick: the database must be at this build's head revision. Never creates,
    alters or stamps anything."""
    from sqlalchemy.exc import DBAPIError
    engine = engine or _admin_engine()
    with engine.connect() as conn:
        try:
            st = inspect(conn, schema, validate=False)
        except DBAPIError as e:  # the runtime role reads the version table only from revision 0007 on
            if getattr(e.orig, "sqlstate", None) != "42501":
                raise
            raise SchemaVersionError("The runtime role cannot read the schema version, so the database predates revision "
                                     "0007 (or the role lacks access). Start-up does not create or change the schema. "
                                     "Take a backup, then run `python -m app.migrate upgrade` "
                                     "(docs/database-migrations.md).") from None
        finally:
            conn.rollback()
    if st.kind == CURRENT:
        return st.revision
    raise SchemaVersionError(f"Database schema is not at the revision this build requires: {st.describe()}. "
                             "Start-up does not create or change the schema. Take a backup, then run "
                             "`python -m app.migrate upgrade` (docs/database-migrations.md).")


def rls_gaps(conn, schema: str = "public") -> list[str]:
    """Tables carrying tenant_id without row-level security or without a policy (generated coverage check)."""
    rows = conn.execute(text("""
        SELECT c.relname, c.relrowsecurity, EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = n.nspname
                                                     AND p.tablename = c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND EXISTS (
            SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
        ORDER BY 1"""), {"s": schema}).all()
    return [f"{name}: rls={'on' if rls else 'OFF'}, policy={'yes' if pol else 'NONE'}"
            for name, rls, pol in rows if name not in RLS_EXEMPT and not (rls and pol)]


def security_gaps(conn, schema: str = "public") -> list[str]:
    """Revision 0007 invariants (ZA-DAT-001 §12/§13, DAT-REQ-003): FORCE on every RLS table, no object owned by a role
    that can log in, and the context key provisioned."""
    s = {"s": schema}
    out = [f"{t}: RLS without FORCE" for t in conn.execute(text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s "
        "AND c.relkind IN ('r', 'p') AND c.relrowsecurity AND NOT c.relforcerowsecurity ORDER BY 1"), s).scalars()]
    out += [f"{name}: owned by login role {owner}" for name, owner in conn.execute(text(
        "SELECT c.relname, r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_roles r ON r.oid = c.relowner WHERE n.nspname = :s AND c.relkind IN ('r', 'p', 'v', 'm', 'S') "
        "AND r.rolcanlogin UNION ALL SELECT p.proname, r.rolname FROM pg_proc p JOIN pg_namespace n "
        "ON n.oid = p.pronamespace JOIN pg_roles r ON r.oid = p.proowner WHERE n.nspname = :s AND r.rolcanlogin "
        "UNION ALL SELECT n.nspname, r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner "
        "WHERE n.nspname = :s AND r.rolcanlogin ORDER BY 1"), s).all()]
    if conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"{_qi(schema)}.assure_context_key"}).scalar():
        if not conn.execute(text(f"SELECT count(*) FROM {_qi(schema)}.assure_context_key")).scalar():
            out.append("assure_context_key: not provisioned (run `python -m app.migrate upgrade`)")
    else:
        out.append("assure_context_key: missing (revision 0007 not applied)")
    return out


def model_drift(conn) -> list[str]:
    """Differences between app/models.py and the `public` schema, as `alembic revision --autogenerate` would see them.
    A non-empty result means a model changed without a migration (or the database drifted)."""
    from alembic.autogenerate import compare_metadata
    from . import models  # noqa: F401
    from .db import Base
    mc = MigrationContext.configure(conn, opts={"compare_type": True, "include_object":
                                                lambda o, n, t, r, c: not (t == "table" and n in NOT_MODELLED)})
    return [str(d) for d in compare_metadata(mc, Base.metadata)]


def check(*, schema: str = "public", engine=None) -> list[str]:
    """Read-only post-migration verification: head revision, no model drift, RLS on every tenant table."""
    engine = engine or _admin_engine()
    problems = []
    with engine.connect() as conn:
        try:
            st = inspect(conn, schema, validate=False)
            if st.kind != CURRENT:
                problems.append(f"schema version: {st.describe()}")
            if schema == "public":
                problems += [f"model drift: {d}" for d in model_drift(conn)]
            problems += [f"RLS coverage: {g}" for g in rls_gaps(conn, schema)]
            problems += [f"security: {g}" for g in security_gaps(conn, schema)]
        finally:
            conn.rollback()
    return problems


# ---------------------------------------------------------------- CLI
def _where() -> str:
    from .db import ADMIN_DATABASE_URL, describe_url
    return describe_url(ADMIN_DATABASE_URL)  # never contains the password


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.migrate", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="read-only: report the database state")
    up = sub.add_parser("upgrade", help="validate, then migrate to the head (or --to REV) in one transaction")
    up.add_argument("--to", default="head", metavar="REV")
    sub.add_parser("check", help="read-only: head revision, no model drift, RLS coverage")
    down = sub.add_parser("downgrade", help="disposable databases and rehearsals only")
    down.add_argument("target")
    down.add_argument("--confirm", action="store_true")
    fp = sub.add_parser("fingerprint", help="maintainers: print or write the normalised schema description")
    fp.add_argument("--write", action="store_true", help=f"write {BASELINE_FILE.name} (only for a reviewed baseline)")
    args = ap.parse_args(argv)
    os.environ.setdefault("ZA_ADMIN_TOOL", "1")  # an operator tool: backend/.env may supply DATABASE_ADMIN_URL

    try:
        from .db import check_database
        _admin_engine()                 # PostgreSQL only
        check_database(runtime=False)   # owner role only: the runtime role may not exist yet; message has no password
        if args.cmd == "status":
            st = status()
            print(f"{_where()}: {st.describe()}")
            return 0 if st.kind == CURRENT else 2 if st.kind in REFUSED else 1
        if args.cmd == "upgrade":
            before, after = upgrade(args.to)
            print(f"{_where()}: before: {before.describe(limit=0)}")
            print(f"{_where()}: after:  {after.describe()}")
            if after.kind == CURRENT:
                for line in provision_data():
                    print(f"{_where()}: account bootstrap: {line}")
            return 0
        if args.cmd == "check":
            problems = check()
            for p in problems:
                print(f"FAIL {p}")
            print(f"{_where()}: " + ("check passed" if not problems else f"{len(problems)} problem(s)"))
            return 0 if not problems else 1
        if args.cmd == "downgrade":
            st = downgrade(args.target, confirm=args.confirm)
            print(f"{_where()}: {st.describe()}")
            return 0
        if args.cmd == "fingerprint":
            with _admin_engine().connect() as conn:
                try:
                    st = inspect(conn, validate=False)
                    fp = fingerprint(conn)
                finally:
                    conn.rollback()
            doc = {"revision": st.revision or BASELINE_REVISION, "runtime_role": RUNTIME_ROLE,
                   "digest": fingerprint_digest(fp), "fingerprint": fp}
            if args.write:
                if st.kind != CURRENT or st.revision != BASELINE_REVISION:
                    raise SystemExit(f"--write needs a database at the baseline revision {BASELINE_REVISION} "
                                     f"(state: {st.describe(limit=0)})")
                BASELINE_FILE.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                print(f"wrote {BASELINE_FILE.name} (digest {doc['digest'][:16]})")
            else:
                print(json.dumps(doc, indent=1, sort_keys=True))
            return 0
    except MigrationRefused as e:
        print(f"REFUSED {_where()}: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:  # SchemaVersionError, RuntimeRoleError, connection failures: operator-facing messages
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
