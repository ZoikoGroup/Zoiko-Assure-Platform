"""A small, fail-safe cache over the shared Redis pool (app/redis_client.py).

Rules (ZA-ENG-BRIEF-001 rule 01 and its "Cache: None initially ... Redis-class cache after measured DB pressure";
ZA-TEN-001 TEN-REQ-031 and §12 "Caches"; ZA-TIM-001 TIM-REQ-016/019; ZA-STA-001 STA-REQ-028):

* PostgreSQL stays authoritative. A cached value is a rebuildable copy, never read as truth, and any Redis failure or
  malformed value is a cache MISS: the caller computes the answer from PostgreSQL as if Redis did not exist.
* Keys are namespaced ``za:<environment>:<cell>:<scope>:<name>:...``. Tenant data uses ``tenant_key()``, which puts the
  tenant (and entity, where given) in the namespace; an object ID alone is never a key (TEN-REQ-031). Global catalogue
  content uses ``global_key()`` and must not vary by caller.
* Authorization is never cached: routes authorize every request first, then consult the cache.
* Assurance decisions, evidence, test results, obligation states, audit history and secrets are not cached.
* Catalogue reads are keyed by the cell's record watermark (PostgreSQL ``platform_record_sequence``). Every
  authoritative write advances it, so a write makes every older entry unreachable: invalidation is driven by the
  database, not by a best-effort Redis call, and a cache can never answer for data newer than it holds (TIM-REQ-019).
  TTLs bound memory and any change made outside the application.
* Use of the cache for a read path is opt-in (``ZA_CATALOG_CACHE=true``). ZA-ENG-BRIEF-001 sets no cache initially and a
  Redis-class cache after measured database pressure, so it stays off until that measurement justifies it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder

from .redis_client import manager

log = logging.getLogger("zoiko.cache")

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 86_400
_SEGMENT = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_VERSION = "v1"  # bump to invalidate every entry after a serialization change


class CacheKeyError(ValueError):
    pass


def _seg(v) -> str:
    s = str(v)
    if not _SEGMENT.match(s):
        raise CacheKeyError(f"invalid cache key segment {s!r}")
    return s


def _prefix() -> str:
    from .db import CELL_ID
    env = os.environ.get("ZA_ENVIRONMENT", "production")
    return f"za:{_seg(env)}:{_seg(CELL_ID)}:{_VERSION}"


def tenant_key(tenant_id: str, name: str, *parts, entity_id: str | None = None) -> str:
    """A key for tenant-scoped data: the tenant (and entity) are part of the namespace (TEN-REQ-031)."""
    if not tenant_id:
        raise CacheKeyError("tenant-scoped cache keys need a tenant")
    scope = f"tenant:{_seg(tenant_id)}" + (f":entity:{_seg(entity_id)}" if entity_id else "")
    return ":".join([_prefix(), scope, _seg(name), *(_seg(p) for p in parts)])


def global_key(name: str, *parts) -> str:
    """A key for global, caller-independent content (the shared regulatory catalogue)."""
    return ":".join([_prefix(), "global", _seg(name), *(_seg(p) for p in parts)])


def _ttl(ttl) -> int:
    if ttl is None:
        raw = os.environ.get("ZA_CACHE_TTL_SECONDS")
        try:
            ttl = int(raw) if raw else DEFAULT_TTL_SECONDS
        except ValueError:
            ttl = DEFAULT_TTL_SECONDS
    return max(1, min(int(ttl), MAX_TTL_SECONDS))


class Cache:
    """get / set / delete / exists / expire over the shared pool. Every method is fail-safe: on any Redis error it
    behaves like an empty cache (get -> None, set/delete/expire -> False, exists -> False) and logs without values."""

    def __init__(self, mgr=manager):
        self._mgr = mgr

    @property
    def available(self) -> bool:
        return self._mgr.usable()

    def _call(self, op: str, key: str, fn: Callable[[Any], Any], default):
        if not self._mgr.usable():
            return default
        try:
            return fn(self._mgr.client)
        except Exception as e:  # noqa: BLE001 - an outage is a miss, never an error for the request
            log.warning("cache %s failed for %s: %s", op, key.split(":")[-2:], type(e).__name__)
            self._mgr.mark_unavailable(type(e).__name__)  # bypass until the next re-check
            return default

    def get(self, key: str) -> Any | None:
        """The JSON value stored at ``key``, or None (miss, expired, unavailable, or malformed: deleted)."""
        raw = self._call("get", key, lambda c: c.get(key), None)
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or envelope.get("v") != _VERSION or "data" not in envelope:
                raise ValueError("not a cache envelope")
            return envelope["data"]
        except (ValueError, TypeError):
            log.warning("cache value malformed for %s; discarded", key.split(":")[-2:])
            self.delete(key)
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Store a JSON-serialisable value with a TTL (seconds; default ZA_CACHE_TTL_SECONDS or 300)."""
        payload = json.dumps({"v": _VERSION, "data": jsonable_encoder(value)}, separators=(",", ":"))
        return bool(self._call("set", key, lambda c: c.set(key, payload, ex=_ttl(ttl)), False))

    def delete(self, *keys: str) -> bool:
        if not keys:
            return False
        return bool(self._call("delete", keys[0], lambda c: c.delete(*keys), 0))

    def exists(self, key: str) -> bool:
        return bool(self._call("exists", key, lambda c: c.exists(key), 0))

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._call("expire", key, lambda c: c.expire(key, _ttl(ttl)), False))

    def get_or_build(self, key: str, build: Callable[[], Any], ttl: int | None = None) -> tuple[Any, bool]:
        """(value, hit). On a miss or any failure the value is built from PostgreSQL by ``build`` and stored best
        effort. The built value is returned exactly as a cached one would be (JSON-encoded form)."""
        cached = self.get(key)
        if cached is not None:
            return cached, True
        value = jsonable_encoder(build())
        self.set(key, value, ttl)
        return value, False


cache = Cache()


def catalog_cache_enabled() -> bool:
    return (os.environ.get("ZA_CATALOG_CACHE") or "").strip().lower() in {"1", "true", "yes", "on"}


def cached_catalog_read(db, name: str, build: Callable[[], Any]):
    """A global catalogue read through the cache when ZA_CATALOG_CACHE is on, keyed by the record watermark read from
    PostgreSQL in the caller's transaction. Off, or with Redis unavailable, it is exactly ``build()``."""
    if not catalog_cache_enabled() or not cache.available:
        return build()
    from .platform import current_watermark
    watermark = current_watermark(db)["record_seq"]
    value, _ = cache.get_or_build(global_key("catalog", name, f"seq-{watermark}"), build)
    return value
