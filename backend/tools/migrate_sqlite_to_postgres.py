"""Copy the legacy SQLite demo database (backend/var/assure.db) into the configured PostgreSQL database.

    .venv\\Scripts\\python.exe tools\\migrate_sqlite_to_postgres.py            # dry run: migrate, verify, roll back
    .venv\\Scripts\\python.exe tools\\migrate_sqlite_to_postgres.py --apply    # migrate, verify, commit

Target: the owner role in DATABASE_ADMIN_URL (real environment variables override backend/.env, as for the API).
The owner is not subject to row-level security, as for the schema migrations and demo seed. The target must already be
at the head schema revision (`python -m app.migrate upgrade`); this tool never creates or alters the schema.

Guarantees
* Insert-only and repeatable. A row whose primary key already exists in PostgreSQL is never updated: a repeat run
  inserts nothing, and rows that changed in PostgreSQL since are reported and kept as they are.
* One transaction. Any error (foreign key, unique constraint, oversize value) rolls everything back.
* Accounts. Login looks accounts up by email, so an SQLite actor whose email belongs to a different PostgreSQL actor is
  a conflict. Only one is resolved automatically: the bootstrap Super Admin (both tenant-less, role SUPER_ADMIN only).
  The PostgreSQL account is kept; the SQLite duplicate, its credential and its sessions are not copied. Audit events
  that name the SQLite id are copied verbatim, because the audit log is append-only history. Any other conflict stops.
* Fidelity. SQLite stores UTC timestamps without a zone; they are written as UTC whatever the server's TimeZone.
  JSON is copied as its exact stored text, so hashes over canonical bodies still verify. Password hashes are copied
  unchanged (same scrypt format), so existing passwords keep working.
* platform_record_sequence is raised to the larger of the two counters, never lowered, so new records continue after
  every migrated record_seq.
* The SQLite file is opened read-only and never modified. Passwords and connection secrets are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("ZA_ADMIN_TOOL", "1")  # an operator tool: backend/.env may supply DATABASE_ADMIN_URL

from sqlalchemy import (JSON, Numeric, String, Text, bindparam, cast, create_engine, func, inspect, select, text,  # noqa: E402
                        tuple_, type_coerce)
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.db import ADMIN_DATABASE_URL, Base, admin_engine  # noqa: E402

SEQ_TABLE = "platform_record_sequence"
SUPER_ADMIN = ["SUPER_ADMIN"]
ACCOUNT_TABLES = ("org_actor", "auth_credential", "auth_session")  # rows keyed to an actor, skipped with that actor


def count(db: Session, table, where=None) -> int:
    q = select(func.count()).select_from(table)
    return db.execute(q if where is None else q.where(where)).scalar_one()


def safe_target(url: str) -> str:
    u = make_url(url)
    return f"{u.drivername}://{u.username}@{u.host}:{u.port}/{u.database}"


def utc(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    return v


def norm(column, v):
    """UTC timestamps; exact Decimals for NUMERIC columns that SQLite stores as text (a String variant on SQLite)."""
    if isinstance(column.type, Numeric) and isinstance(v, str):
        return Decimal(v)
    return utc(v)


def read_rows(src, table) -> list[dict]:
    """Rows as written, with JSON columns as their raw stored text (None = SQL NULL) and timestamps made UTC."""
    cols = [type_coerce(c, Text).label(c.name) if isinstance(c.type, JSON) else c for c in table.c]
    with src.connect() as conn:
        return [{c.name: norm(c, r._mapping[c.name]) for c in table.c} for r in conn.execute(select(*cols))]


def comparable(table, row: dict, json_parsed: bool) -> dict:
    out = {}
    for c in table.c:
        v = row[c.name]
        if isinstance(c.type, JSON) and not json_parsed:
            v = None if v is None else json.loads(v)
        out[c.name] = norm(c, v)
    return out


def insert_stmt(table):
    # JSON values travel as text and are cast server-side, so PostgreSQL stores exactly the SQLite text.
    return table.insert().values({c.name: cast(bindparam(f"p_{c.name}", type_=Text), c.type) if isinstance(c.type, JSON)
                                  else bindparam(f"p_{c.name}", type_=c.type) for c in table.c})


def oversize(table, rows) -> list[str]:
    bad = []
    for c in table.c:
        if isinstance(c.type, String) and c.type.length:
            n = sum(1 for r in rows if isinstance(r[c.name], str) and len(r[c.name]) > c.type.length)
            if n:
                bad.append(f"{table.name}.{c.name}: {n} value(s) longer than {c.type.length}")
    return bad


def account_conflicts(src_actors: list[dict], dst: Session) -> tuple[set[str], list[str], list[str]]:
    """SQLite actor ids not to copy, notes on resolved conflicts, and unresolved conflicts."""
    pg = {}
    for a in dst.execute(text("SELECT actor_id, tenant_id, roles::text AS roles, lower(email) AS email FROM org_actor "
                              "WHERE email IS NOT NULL")).mappings():
        pg[a["email"]] = a
    skip, notes, problems = set(), [], []
    for a in src_actors:
        email = (a["email"] or "").lower()
        other = pg.get(email)
        if not email or other is None or other["actor_id"] == a["actor_id"]:
            continue
        src_roles, dst_roles = json.loads(a["roles"]) if a["roles"] else [], json.loads(other["roles"])
        if a["tenant_id"] is None and other["tenant_id"] is None and src_roles == dst_roles == SUPER_ADMIN:
            skip.add(a["actor_id"])
            notes.append(f"{email}: kept the existing PostgreSQL Super Admin ({other['actor_id'][:8]}…); the SQLite "
                         f"duplicate ({a['actor_id'][:8]}…), its credential and sessions are not copied")
        else:
            problems.append(f"{email}: SQLite actor {a['actor_id'][:8]}… ({a['tenant_id']}, {src_roles}) and PostgreSQL "
                            f"actor {other['actor_id'][:8]}… ({other['tenant_id']}, {dst_roles}) share this email")
    return skip, notes, problems


def migrate(sqlite_path: Path, apply: bool) -> int:
    if not make_url(ADMIN_DATABASE_URL).drivername.startswith("postgresql"):
        print("DATABASE_ADMIN_URL is not a PostgreSQL URL; nothing to migrate into.")
        return 2
    if not sqlite_path.exists():
        print(f"SQLite source not found: {sqlite_path}")
        return 2
    print(f"source : {sqlite_path} (read-only)")
    print(f"target : {safe_target(ADMIN_DATABASE_URL)}  [{'APPLY' if apply else 'DRY RUN, rolled back'}]")

    # The schema comes only from the versioned migrations (docs/database-migrations.md); this tool never creates it.
    from app.migrate import SchemaVersionError, require_current
    try:
        require_current()
    except SchemaVersionError as e:
        print(f"STOP: {e}")
        return 2

    src = create_engine(f"sqlite:///file:{sqlite_path.resolve().as_posix()}?mode=ro&uri=true")
    src_tables = set(inspect(src).get_table_names())
    for t in Base.metadata.sorted_tables:
        if t.name not in src_tables:
            continue
        have = {c["name"] for c in inspect(src).get_columns(t.name)}
        extra, lacking = have - set(t.c.keys()), set(t.c.keys()) - have
        if extra or lacking:
            print(f"STOP: {t.name} columns differ from the application model (extra {sorted(extra)}, missing {sorted(lacking)})")
            return 1

    report, problems = [], []
    with Session(admin_engine) as dst:
        dst.execute(text("SET LOCAL TimeZone = 'UTC'"))
        dst.execute(text("SELECT pg_advisory_xact_lock(724501)"))  # serialise with schema migrations (app.migrate)
        dst.execute(text("SELECT pg_advisory_xact_lock(724502)"))  # and with account bootstrap
        # Requests that need a record_seq wait until this transaction ends, so no new record can interleave.
        dst.execute(text(f"LOCK TABLE {SEQ_TABLE} IN EXCLUSIVE MODE"))

        src_rows = {t.name: read_rows(src, t) for t in Base.metadata.sorted_tables if t.name in src_tables}
        skip_actors, notes, conflicts = account_conflicts(src_rows.get("org_actor", []), dst)
        if conflicts:
            print("STOP: account conflicts that cannot be resolved safely:")
            for c in conflicts:
                print("  -", c)
            dst.rollback()
            return 1

        for t in Base.metadata.sorted_tables:
            if t.name not in src_rows or t.name == SEQ_TABLE:
                continue
            rows = src_rows[t.name]
            before = count(dst, t)
            skip = t.name in ACCOUNT_TABLES
            candidates = [r for r in rows if not (skip and r["actor_id"] in skip_actors)]
            skipped = len(rows) - len(candidates)
            pk = list(t.primary_key.columns)
            existing = {}
            if candidates:
                keys = [tuple(r[c.name] for c in pk) for r in candidates]
                for i in range(0, len(keys), 500):
                    for e in dst.execute(select(t).where(tuple_(*pk).in_(keys[i:i + 500]))).mappings():
                        existing[tuple(e[c.name] for c in pk)] = dict(e)
            new = [r for r in candidates if tuple(r[c.name] for c in pk) not in existing]
            same = differs = 0
            for r in candidates:
                e = existing.get(tuple(r[c.name] for c in pk))
                if e is not None:
                    if comparable(t, r, json_parsed=False) == comparable(t, e, json_parsed=True):
                        same += 1
                    else:
                        differs += 1
            problems += oversize(t, new)
            if new and not problems:
                dst.execute(insert_stmt(t), [{f"p_{k}": v for k, v in r.items()} for r in new])
            after = count(dst, t)
            report.append((t.name, len(rows), same, differs, skipped, len(new), before, after))

        if problems:
            print("STOP: values that do not fit the PostgreSQL schema:")
            for p in problems:
                print("  -", p)
            dst.rollback()
            return 1

        # Record watermark: continue after every migrated record_seq; never lower the PostgreSQL counter.
        src_seq = {r["cell_id"]: r["current_seq"] for r in src_rows.get(SEQ_TABLE, [])}
        seq_notes = []
        for cell, s in src_seq.items():
            cur = dst.execute(text(f"SELECT current_seq FROM {SEQ_TABLE} WHERE cell_id = :c"), {"c": cell}).scalar()
            if cur is None:
                dst.execute(text(f"INSERT INTO {SEQ_TABLE} (cell_id, current_seq) VALUES (:c, :s)"), {"c": cell, "s": s})
                seq_notes.append(f"{cell}: created at {s}")
            elif s > cur:
                dst.execute(text(f"UPDATE {SEQ_TABLE} SET current_seq = :s WHERE cell_id = :c"), {"c": cell, "s": s})
                seq_notes.append(f"{cell}: raised {cur} -> {s}")
            else:
                seq_notes.append(f"{cell}: kept {cur} (SQLite {s})")

        # Every source row that was meant to be copied must now be present.
        missing_rows = []
        for t in Base.metadata.sorted_tables:
            if t.name not in src_rows or t.name == SEQ_TABLE:
                continue
            pk = list(t.primary_key.columns)
            want = [tuple(r[c.name] for c in pk) for r in src_rows[t.name]
                    if not (t.name in ACCOUNT_TABLES and r["actor_id"] in skip_actors)]
            found = sum(count(dst, t, tuple_(*pk).in_(want[i:i + 500])) for i in range(0, len(want), 500))
            if found != len(want):
                missing_rows.append(f"{t.name}: {len(want) - found} row(s)")

        width = max(len(r[0]) for r in report)
        print(f"\n{'table'.ljust(width)}  sqlite  same  differs  skipped  inserted   pg before -> after")
        for name, n, same, differs, skipped, ins, before, after in report:
            if n or before or after:
                print(f"{name.ljust(width)}  {n:6}  {same:4}  {differs:7}  {skipped:7}  {ins:8}   {before:9} -> {after}")
        tot = [sum(r[i] for r in report) for i in range(1, 8)]
        print(f"{'TOTAL'.ljust(width)}  {tot[0]:6}  {tot[1]:4}  {tot[2]:7}  {tot[3]:7}  {tot[4]:8}   {tot[5]:9} -> {tot[6]}")
        print("record sequence:", "; ".join(seq_notes) or "none in source")
        for n in notes:
            print("account conflict resolved:", n)
        if any(r[3] for r in report):
            print("rows that already existed but differ were kept as they are in PostgreSQL (never overwritten)")
        if missing_rows:
            print("STOP: rows missing after copy:", "; ".join(missing_rows))
            dst.rollback()
            return 1
        if apply:
            dst.commit()
            print("\ncommitted")
        else:
            dst.rollback()
            print("\ndry run complete: rolled back, nothing written (run with --apply to commit)")

    if apply:
        verify_ledgers()
    return 0


def verify_ledgers() -> None:
    """Re-verify every tenant's evidence hash chain in PostgreSQL (in a transaction that is rolled back)."""
    from app.engines.evidence import verify_chain
    with Session(admin_engine) as db:
        for (tenant_id,) in db.execute(text("SELECT tenant_id FROM evidence_tenant_ledger_head ORDER BY 1")).all():
            r = verify_chain(db, tenant_id)
            print(f"evidence ledger {tenant_id}: {r['result']} ({r['events_checked']} events, head #{r['head_seq']})")
        db.rollback()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--sqlite", type=Path, default=BACKEND / "var" / "assure.db", help="SQLite source (default: var/assure.db)")
    p.add_argument("--apply", action="store_true", help="commit the migration (default: dry run, rolled back)")
    a = p.parse_args()
    sys.exit(migrate(a.sqlite, a.apply))
