"""Shared platform primitives: record watermark, canonical JSON, outbox, audit (ZA-TIM-001 §3, ZA-DAT-001 §16)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import codes
from .db import CELL_ID
from .models import AuditEvent, OutboxEvent, RecordSequence, utcnow


def _default(o: Any):
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not canonicalizable: {type(o)}")


def canonical_json(obj: Any) -> bytes:
    """Versioned canonical JSON profile v1: UTF-8, sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def digest(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def next_record_seq(db: Session) -> int:
    """Server-authoritative monotonic cell-local record sequence (TIM-REQ-004/005)."""
    row = db.get(RecordSequence, CELL_ID)
    if row is None:
        row = RecordSequence(cell_id=CELL_ID, current_seq=0)
        db.add(row)
        db.flush()
    db.execute(update(RecordSequence).where(RecordSequence.cell_id == CELL_ID)
               .values(current_seq=RecordSequence.current_seq + 1))
    db.flush()
    return db.execute(select(RecordSequence.current_seq).where(RecordSequence.cell_id == CELL_ID)).scalar_one()


def current_watermark(db: Session) -> dict:
    row = db.get(RecordSequence, CELL_ID)
    return {"cell_id": CELL_ID, "record_seq": row.current_seq if row else 0,
            "recorded_at": utcnow().isoformat()}


def emit(db: Session, *, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict,
         tenant_id: str | None = None, entity_id: str | None = None, actor: dict | None = None,
         record_seq: int | None = None, correlation_id: str | None = None) -> OutboxEvent:
    """Transactional outbox insert — same transaction as the state change (DAT-REQ-014)."""
    ev = OutboxEvent(tenant_id=tenant_id, entity_id=entity_id, cell_id=CELL_ID,
                     aggregate_type=aggregate_type, aggregate_id=aggregate_id, event_type=event_type,
                     dictionary_version=codes.DICTIONARY_VERSION, actor_ref=actor,
                     record_seq=record_seq if record_seq is not None else next_record_seq(db),
                     correlation_id=correlation_id, payload=json.loads(canonical_json(payload)))
    db.add(ev)
    return ev


def audit(db: Session, *, action: str, object_type: str, object_id: str, actor=None,
          tenant_id: str | None = None, prior: str | None = None, new: str | None = None,
          reason: str | None = None, record_seq: int | None = None) -> None:
    db.add(AuditEvent(tenant_id=tenant_id, actor_id=getattr(actor, "actor_id", None),
                      actor_type=getattr(actor, "actor_type", None), action=action,
                      object_type=object_type, object_id=object_id, prior_state=prior, new_state=new,
                      reason=reason, record_seq=record_seq if record_seq is not None else next_record_seq(db)))


def to_dict(obj) -> dict:
    """Serialize an ORM row (bytes columns are never exposed)."""
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.key)
        if isinstance(v, (bytes, bytearray)):
            continue
        if isinstance(v, datetime):
            v = ensure_utc(v).isoformat().replace("+00:00", "Z")
        elif isinstance(v, date):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        out[col.key] = v
    return out
