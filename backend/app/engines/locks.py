"""Transaction-scoped, database-backed serialisation for the regulatory and obligation core.

``xact_lock(db, *parts)`` takes a PostgreSQL transaction-level advisory lock on a key derived from ``parts``. The lock
is held by the database until the transaction commits or rolls back, so it serialises concurrent requests across API
processes, not only threads. A transaction that waited then reads with a fresh statement snapshot under READ COMMITTED
and sees what the earlier holder committed.

It is used where the "one current revision" rule has no database constraint yet (no partial unique index exists in
migrations 0001-0013; adding one is a schema change that needs review: docs/backend-remediation-report.md). Callers
take the lock BEFORE they read the state they validate.

SQLite (the no-PostgreSQL test mode) serialises writers itself, so this is a no-op there.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import IS_POSTGRES

_NAMESPACE = "zoiko-assure:regulatory-core"


def xact_lock(db: Session, *parts: str, shared: bool = False) -> None:
    """Exclusive by default. ``shared=True`` takes the shared mode: shared holders run together and only wait for (and
    block) an exclusive holder of the same key, e.g. many evaluations of one obligation definition against one
    publication of a new RuleVersion for it."""
    if not IS_POSTGRES:
        return
    key = "|".join((_NAMESPACE, *(str(p) for p in parts)))
    fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    # hashtextextended (PostgreSQL 11+) maps the key to a bigint; a collision only over-serialises, never under-locks.
    db.execute(text(f"SELECT {fn}(hashtextextended(:k, 0))"), {"k": key})
