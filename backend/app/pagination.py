"""Opaque cursor pagination for collection endpoints (ZA-API-001 §04, §11; API-REQ-012, API-ADR-03).

A collection response is ``{"items": [...], "page": {"next_cursor", "has_more", "watermark", "limit"}}``. The cursor is
opaque to clients and signed by the server. It carries what ZA-API-001 §11 lists: a fingerprint of the query (filters
and sort), the authorized scope it was issued for, the stable sort position of the last item returned, the record
watermark pinned on the first page, and an expiry. A cursor presented with different filters, for another scope, after
expiry or altered in any way is refused with 400 ``invalid_cursor`` / ``cursor_expired``; it is never silently
reinterpreted.

Signing key: a purpose-bound key derived from the cell's database context key (HMAC-SHA256 over a fixed label), so the
context key itself never signs anything else and a cursor cannot be replayed as a database context string. Isolated
SQLite test runs, which have no context key, use a per-process random key instead. The key custody decision belongs to
ZA-SEC-001 (no ZA-KEY-001 exists yet); this module only needs *a* server-held secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import HTTPException

CURSOR_VERSION = 1
CURSOR_TTL_SECONDS = 3600          # a page session lasts an hour; a later request starts again from the first page
_LABEL = b"za-api-cursor-v1"
_process_key: bytes | None = None


def _key() -> bytes:
    global _process_key
    from .db import IS_POSTGRES, context_key
    try:
        base = context_key()
    except RuntimeError:
        if IS_POSTGRES:  # a PostgreSQL deployment always has the context key; never fall back silently there
            raise
        if _process_key is None:
            _process_key = secrets.token_bytes(32)
        base = _process_key
    return hmac.new(base, _LABEL, hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def query_fingerprint(query: dict[str, Any]) -> str:
    """A stable digest of the filters and sort a cursor belongs to (None values are dropped)."""
    canon = json.dumps({k: v for k, v in sorted(query.items()) if v is not None}, sort_keys=True, default=str,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


def encode_cursor(*, scope: str, query: str, position: list, watermark: int, now: float | None = None) -> str:
    body = json.dumps({"v": CURSOR_VERSION, "s": scope, "q": query, "p": position, "w": watermark,
                       "e": int((now if now is not None else time.time()) + CURSOR_TTL_SECONDS)},
                      sort_keys=True, separators=(",", ":")).encode()
    return _b64(body) + "." + _b64(hmac.new(_key(), body, hashlib.sha256).digest())


def _invalid(message: str, code: str = "invalid_cursor") -> HTTPException:
    return HTTPException(400, {"error": code, "message": message})


def decode_cursor(token: str, *, scope: str, query: str, now: float | None = None) -> dict:
    """The cursor's position and pinned watermark, if it is authentic, unexpired and issued for this exact scope and
    query; otherwise 400. The checks never reveal what another scope's cursor contained."""
    try:
        body_part, sig_part = token.split(".", 1)
        body, sig = _unb64(body_part), _unb64(sig_part)
    except (ValueError, TypeError):
        raise _invalid("The cursor is not valid. Start again from the first page.") from None
    if not hmac.compare_digest(sig, hmac.new(_key(), body, hashlib.sha256).digest()):
        raise _invalid("The cursor is not valid. Start again from the first page.")
    try:
        data = json.loads(body)
    except ValueError:
        raise _invalid("The cursor is not valid. Start again from the first page.") from None
    if data.get("v") != CURSOR_VERSION or data.get("s") != scope:
        raise _invalid("The cursor is not valid. Start again from the first page.")
    if data.get("q") != query:
        raise _invalid("The cursor belongs to a different filter or sort. Start again from the first page.")
    if int(data.get("e", 0)) <= (now if now is not None else time.time()):
        raise _invalid("The cursor has expired. Start again from the first page.", "cursor_expired")
    return {"position": data["p"], "watermark": int(data["w"])}


def page(items: list, *, next_cursor: str | None, watermark: dict, limit: int) -> dict:
    """The ZA-API-001 §04 collection envelope."""
    return {"items": items, "page": {"next_cursor": next_cursor, "has_more": next_cursor is not None,
                                     "watermark": watermark, "limit": limit}}
