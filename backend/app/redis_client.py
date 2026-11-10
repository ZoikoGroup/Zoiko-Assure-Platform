"""Redis connection management (one shared, thread-safe connection pool per process).

Redis is an optional, non-authoritative projection store. ZA-ENG-BRIEF-001 rule 01: "PostgreSQL is authoritative.
Graph, search, caches and analytics are projections. Rebuild them; never let them become legal truth"; TIM-REQ-016.
Nothing in Redis is ever read as regulatory or assurance truth, and a Redis failure never changes a business outcome:
callers fall back to PostgreSQL (app/cache.py).

Configuration (environment, or backend/.env for local development; real environment variables win):
  REDIS_URL                      redis://[user:password@]host:port[/db] or rediss://... (TLS). Unset or empty: Redis is
                                 DISABLED. There is deliberately no default, so a missing URL never connects to an
                                 unrelated local Redis.
  REDIS_TLS                      true/false. true forces TLS for a redis:// URL (the equivalent of `redis-cli --tls`);
                                 rediss:// always uses TLS. Certificates are verified.
  REDIS_CONNECT_TIMEOUT_SECONDS  connect timeout (default 2.0)
  REDIS_SOCKET_TIMEOUT_SECONDS   per-command timeout (default 2.0)
  REDIS_HEALTH_CHECK_INTERVAL    seconds between pooled-connection liveness checks (default 30)
  REDIS_MAX_CONNECTIONS          pool size (default 20)
  REDIS_REQUIRED                 true: refuse to start when Redis is configured but unreachable or misconfigured
                                 (default false: start and report the outage in /api/health)

Credentials never leave this module: every message uses ``redact()``, and nothing returns the URL.

Route handlers in this backend are synchronous (FastAPI runs them in its thread pool over a synchronous SQLAlchemy
session), so the synchronous redis-py client over one shared ConnectionPool is used. It is thread-safe, needs no
event-loop bridging inside handlers, and is never created per request.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("zoiko.redis")

DISABLED, HEALTHY, UNAVAILABLE, MISCONFIGURED = "disabled", "healthy", "unavailable", "misconfigured"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class RedisConfigError(ValueError):
    """REDIS_* configuration that cannot be used. The message never contains a credential."""


def redact(url: str | None) -> str:
    """A URL safe to log: scheme, host, port and database only (no user, no password)."""
    if not url:
        return "<unset>"
    try:
        p = urlsplit(url)
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://***@{host}{port}{p.path or ''}"
    except ValueError:
        return "<unparseable REDIS_URL>"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise RedisConfigError(f"{name} must be true or false")


def _number(name: str, default: float, *, minimum: float, maximum: float, integer: bool = False):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return int(default) if integer else default
    try:
        v = int(raw) if integer else float(raw)
    except ValueError:
        raise RedisConfigError(f"{name} must be a number") from None
    if not minimum <= v <= maximum:
        raise RedisConfigError(f"{name} must be between {minimum} and {maximum}")
    return v


@dataclass(frozen=True)
class RedisSettings:
    url: str
    tls: bool
    connect_timeout: float
    socket_timeout: float
    health_check_interval: int
    max_connections: int
    required: bool

    @property
    def safe_url(self) -> str:
        return redact(self.url)


def load_settings() -> RedisSettings | None:
    """REDIS_* from the environment. None when REDIS_URL is unset or empty (Redis disabled). Raises RedisConfigError
    for a malformed URL or setting."""
    url = (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        p = urlsplit(url)
        port = p.port  # ValueError for a non-numeric or out-of-range port
    except ValueError:
        raise RedisConfigError(f"REDIS_URL is not a valid URL ({redact(url)})") from None
    if p.scheme not in ("redis", "rediss"):
        raise RedisConfigError(f"REDIS_URL must use redis:// or rediss:// ({redact(url)})")
    if not p.hostname:
        raise RedisConfigError(f"REDIS_URL has no host ({redact(url)})")
    if port is None:
        raise RedisConfigError(f"REDIS_URL has no port ({redact(url)})")
    if p.path not in ("", "/") and not p.path.lstrip("/").isdigit():
        raise RedisConfigError(f"REDIS_URL database must be a number ({redact(url)})")
    return RedisSettings(
        url=url, tls=p.scheme == "rediss" or _flag("REDIS_TLS", False),
        connect_timeout=_number("REDIS_CONNECT_TIMEOUT_SECONDS", 2.0, minimum=0.1, maximum=60),
        socket_timeout=_number("REDIS_SOCKET_TIMEOUT_SECONDS", 2.0, minimum=0.1, maximum=60),
        health_check_interval=_number("REDIS_HEALTH_CHECK_INTERVAL", 30, minimum=0, maximum=3600, integer=True),
        max_connections=_number("REDIS_MAX_CONNECTIONS", 20, minimum=1, maximum=1000, integer=True),
        required=_flag("REDIS_REQUIRED", False))


def _connection_url(s: RedisSettings) -> str:
    """REDIS_TLS=true on a redis:// URL means TLS to the same endpoint, exactly as `redis-cli --tls -u redis://...`."""
    if s.tls and s.url.startswith("redis://"):
        return "rediss://" + s.url[len("redis://"):]
    return s.url


class RedisManager:
    """The process's Redis pool: created at start-up, checked, shared by every request, closed at shutdown."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None
        self._pool = None
        self.settings: RedisSettings | None = None
        self.state = DISABLED
        self.detail: str | None = None
        self._checked_at = 0.0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> str:
        """Read the configuration, build the pool and verify it with PING. Never raises for an outage unless
        REDIS_REQUIRED=true; never raises with a credential in the message."""
        with self._lock:
            self._close_locked()
            try:
                self.settings = load_settings()
            except RedisConfigError as e:
                self.settings, self.state, self.detail = None, MISCONFIGURED, str(e)
                log.error("Redis misconfigured: %s", e)
                if _flag_safe("REDIS_REQUIRED"):
                    raise
                return self.state
            if self.settings is None:
                self.state, self.detail = DISABLED, "REDIS_URL is not set"
                log.info("Redis disabled (REDIS_URL not set)")
                return self.state
            try:
                import redis
                from redis.backoff import ExponentialBackoff
                from redis.retry import Retry
            except ImportError:  # configured, but the client library is not installed in this environment
                self.state, self.detail = MISCONFIGURED, "the redis package is not installed (pip install -r requirements.txt)"
                log.error("Redis configured but %s", self.detail)
                if self.settings.required:
                    raise RuntimeError(f"Redis is required but {self.detail}") from None
                return self.state
            s = self.settings
            self._pool = redis.ConnectionPool.from_url(
                _connection_url(s), max_connections=s.max_connections, socket_connect_timeout=s.connect_timeout,
                socket_timeout=s.socket_timeout, health_check_interval=s.health_check_interval,
                retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), retries=2), retry_on_timeout=True,
                decode_responses=True, **({"ssl_cert_reqs": "required"} if s.tls else {}))
            self._client = redis.Redis(connection_pool=self._pool)
        state = self.check()
        if state != HEALTHY:
            log.warning("Redis %s at start-up (%s): %s", state, self.settings.safe_url, self.detail)
            if self.settings.required:
                raise RuntimeError(f"Redis is required but {state} ({self.settings.safe_url})")
        else:
            log.info("Redis connected (%s, tls=%s)", self.settings.safe_url, self.settings.tls)
        return state

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            if self.state != MISCONFIGURED:
                self.state, self.detail = DISABLED, "closed"

    def _close_locked(self) -> None:
        if self._pool is not None:
            try:
                self._pool.disconnect()
            except Exception:  # noqa: BLE001 - shutdown must not fail on a dead socket
                pass
        self._pool = self._client = None

    def use_client(self, client, settings: RedisSettings | None = None) -> None:
        """Install a ready client (tests use fakeredis). Production code calls start()."""
        with self._lock:
            self._close_locked()
            self._client = client
            self.settings = settings or RedisSettings(url="redis://test.invalid:6379/0", tls=False,
                                                      connect_timeout=1, socket_timeout=1, health_check_interval=0,
                                                      max_connections=1, required=False)
            self.state, self.detail = HEALTHY, None

    # ---------------------------------------------------------------- availability
    @property
    def client(self):
        """The shared client, or None when Redis is disabled or misconfigured. Callers must handle redis errors."""
        return self._client

    RECHECK_SECONDS = 15.0

    def usable(self) -> bool:
        """Whether callers should use Redis now. After an outage the cache is bypassed (no per-request timeouts) and
        the server is probed again at most every RECHECK_SECONDS, so a recovered Redis is picked up automatically."""
        if self._client is None:
            return False
        if self.state == HEALTHY:
            return True
        if time.monotonic() - self._checked_at >= self.RECHECK_SECONDS:
            self.check()
        return self.state == HEALTHY

    def mark_unavailable(self, reason: str) -> None:
        if self._client is not None:
            self.state, self.detail, self._checked_at = UNAVAILABLE, reason, time.monotonic()

    def check(self) -> str:
        """PING the server; updates and returns the state. Never raises."""
        if self._client is None:
            return self.state
        self._checked_at = time.monotonic()
        try:
            ok = bool(self._client.ping())
            self.state, self.detail = (HEALTHY, None) if ok else (UNAVAILABLE, "PING returned false")
        except Exception as e:  # noqa: BLE001 - any client error is an outage, reported without the URL
            self.state, self.detail = UNAVAILABLE, type(e).__name__
        return self.state

    def status(self) -> dict:
        """For /api/health: state, TLS and a reason. Never the URL, host or credentials."""
        out = {"state": self.check() if self._client is not None else self.state,
               "required": bool(self.settings.required) if self.settings else _flag_safe("REDIS_REQUIRED")}
        if self.settings is not None:
            out["tls"] = self.settings.tls
        if out["state"] == MISCONFIGURED:  # the precise message (with the redacted host) is in the server log only
            out["reason"] = "Redis cannot be used with the current configuration or installation; see the server log"
        elif self.detail and out["state"] != HEALTHY:
            out["reason"] = self.detail  # a state or exception class name, never a URL
        return out


def _flag_safe(name: str) -> bool:
    try:
        return _flag(name, False)
    except RedisConfigError:
        return False


manager = RedisManager()
