"""Optional ``Idempotency-Key`` for governed actions (ZA-API-001 §08), with the semantics POST /test-runs established
(engines/test_results.py), reused rather than redefined:

  * scope: tenant + operation + key ("Scope is tenant + endpoint/operation class + key"; platform_idempotency_record is
    unique on exactly that);
  * binding: the normalized request hash is stored with the result reference; the same key and request return the
    original result, the same key with a materially different request is 409 ``idempotency_conflict`` and changes
    nothing (API-REQ-005);
  * in progress: a concurrent duplicate waits on the key until the first commits or rolls back (PostgreSQL advisory
    lock; on SQLite the unique constraint turns a racing twin into the same 409);
  * the record is written in the action's own transaction: a failed request leaves no record and a retry runs again; a
    committed one is found by the retry ("5xx after unknown commit: server resolves committed result");
  * keys are 1-200 characters (the column; §08 allows 255) and are never deleted.

Not decided here: whether the key is required (API-REQ-004 says MUST; existing clients send none), the retention window
§08 leaves to the endpoint contract, and the problem+json error model (§12). Without a key nothing changes.

Replay limits (what the storage holds): result_ref only, not the response. A replay re-reads the records the request
created and reports them as committed (an attestation's determination and obligation revisions were current then; see
applicability.as_committed). That is the committed result only while those rows keep their committed columns and the
serialisation is unchanged: nothing enforces it on PostgreSQL for determination, obligation and challenge rows (they are
not append-only; record_to_seq must stay writable) or for this table. Exact replay independent of current rows needs an
immutable stored response (a schema change)."""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import IdempotencyRecord
from ..platform import sha256_hex
from .locks import xact_lock

MAX_KEY = 200  # platform_idempotency_record.idempotency_key is varchar(200), as engines/test_results.py


def request_hash(request: dict) -> str:
    """Canonical JSON of the request as the action reads it (path identifiers included)."""
    return sha256_hex(json.dumps(request, sort_keys=True, separators=(",", ":"), default=str))


def replay(db, *, tenant_id: str, operation: str, key: str | None, request: dict) -> str | None:
    """None without a key, or for a key not used yet: the action proceeds (and calls remember()). The recorded result
    reference when this key already completed this request: the caller returns that result and writes nothing.
    Refuses an invalid key (422 IDEMPOTENCY_KEY_INVALID) and a key used with another request (409 idempotency_conflict)."""
    if key is None:
        return None
    if not 1 <= len(key) <= MAX_KEY:
        raise HTTPException(422, {"error": "IDEMPOTENCY_KEY_INVALID",
                                  "message": f"Idempotency-Key must be 1-{MAX_KEY} characters"})
    xact_lock(db, "idempotency", tenant_id, operation, key)  # a concurrent duplicate waits here
    prior = db.execute(select(IdempotencyRecord).where(
        IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.operation_code == operation,
        IdempotencyRecord.idempotency_key == key)).scalars().first()
    if prior is None:
        return None
    if prior.request_hash != request_hash(request):
        raise HTTPException(409, {"error": "idempotency_conflict",
                                  "message": "This Idempotency-Key was used with a different request."})
    return prior.result_ref


def remember(db, *, tenant_id: str, operation: str, key: str | None, request: dict, result_ref: str) -> None:
    """Record the key with the action's result, in the action's transaction (no record if it does not commit)."""
    if key is None:
        return
    # The action's own pending rows (audit, outbox) first: an error in them is the action's and propagates as it does
    # without a key. Only the key's own insert may be answered as a concurrent duplicate below.
    db.flush()
    db.add(IdempotencyRecord(tenant_id=tenant_id, operation_code=operation, idempotency_key=key,
                             request_hash=request_hash(request), result_ref=result_ref))
    try:
        db.flush()
    except IntegrityError:  # only reachable without the advisory lock (SQLite): a racing twin committed first
        db.rollback()
        raise HTTPException(409, {"error": "idempotency_conflict",
                                  "message": "A request with this Idempotency-Key is already being processed."}) from None
