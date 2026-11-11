"""Redis connection management, the fail-safe cache and the catalogue read cache (app/redis_client.py, app/cache.py).

Requirements exercised: ZA-ENG-BRIEF-001 rule 01 (PostgreSQL authoritative; caches are rebuildable projections) and
"Cache: None initially ... after measured DB pressure" (the catalogue cache is opt-in); ZA-TEN-001 TEN-REQ-031 (tenant /
authorization context in keys; no object-ID-only keys); ZA-TIM-001 TIM-REQ-016/019 (rebuildable; never answers for data
newer than it holds); ZA-SEC-001 (no credential in logs, errors or health output).

No test uses a real Redis server: MemoryRedis below is an in-process double of the commands the code uses (with TTLs),
and outages are injected with a client whose every call fails.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import time

import pytest

from conftest import H

SECRET = "s3cr3t-Pa55word"
USER = "za-test-user"  # a non-default user name: it must never reach a log, an error or /api/health either


class MemoryRedis:
    """The redis-py commands app/redis_client.py and app/cache.py use, in memory, with expiry (decode_responses)."""

    def __init__(self, *a, **k):
        self._data: dict[str, tuple[str, float | None]] = {}
        self.connection_pool = k.get("connection_pool")

    def _live(self, key):
        v = self._data.get(key)
        if v and v[1] is not None and time.monotonic() >= v[1]:
            del self._data[key]
            return None
        return v

    def ping(self):
        return True

    def get(self, key):
        v = self._live(key)
        return v[0] if v else None

    def set(self, key, value, ex=None):
        self._data[key] = (str(value), time.monotonic() + ex if ex else None)
        return True

    def delete(self, *keys):
        return sum(1 for k in keys if self._live(k) and self._data.pop(k, None) is not None)

    def exists(self, key):
        return 1 if self._live(key) else 0

    def expire(self, key, seconds):
        v = self._live(key)
        if not v:
            return False
        self._data[key] = (v[0], time.monotonic() + seconds)
        return True

    def ttl(self, key):
        v = self._live(key)
        if not v:
            return -2
        return -1 if v[1] is None else max(0, round(v[1] - time.monotonic()))

    def keys(self, pattern="*"):
        return [k for k in list(self._data) if self._live(k) and fnmatch.fnmatchcase(k, pattern)]


class Broken:
    """A client whose every call fails the way an unreachable server does."""

    def __getattr__(self, name):
        def fail(*a, **k):
            import redis
            raise redis.exceptions.ConnectionError("connection refused")
        return fail


@pytest.fixture
def fake():
    from app import redis_client
    client = MemoryRedis()
    redis_client.manager.use_client(client)
    yield client
    redis_client.manager.close()


@pytest.fixture
def no_redis():
    from app import redis_client
    redis_client.manager.close()
    yield
    redis_client.manager.close()


# ------------------------------------------------------------------------------------------------ configuration
@pytest.mark.parametrize("url, problem", [
    ("http://default:" + SECRET + "@redis.example:6379", "redis:// or rediss://"),
    ("redis://default:" + SECRET + "@:6379", "no host"),
    ("redis://default:" + SECRET + "@redis.example", "no port"),
    ("redis://default:" + SECRET + "@redis.example:notaport", "not a valid URL"),
    ("redis://default:" + SECRET + "@redis.example:6379/abc", "database must be a number"),
    # No "//": urlsplit reads the user name as the scheme and the password as the path; neither may be repeated.
    (USER + ":" + SECRET + "@redis.example:6379", "redis:// or rediss://"),
    (USER + ":" + SECRET + "@redis.example:6379/0", "redis:// or rediss://"),
    ("redis:" + USER + ":" + SECRET + "@redis.example:6379", "no host"),
])
def test_malformed_configuration_is_refused_without_leaking_the_credential(monkeypatch, caplog, url, problem):
    from app import redis_client
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    with pytest.raises(redis_client.RedisConfigError) as e:
        redis_client.load_settings()
    assert problem in str(e.value) and SECRET not in str(e.value) and USER not in str(e.value)
    mgr = redis_client.RedisManager()
    with caplog.at_level(logging.DEBUG):
        assert mgr.start() == redis_client.MISCONFIGURED
    assert mgr.client is None and SECRET not in caplog.text and USER not in caplog.text
    status = mgr.status()
    assert status["state"] == "misconfigured" and SECRET not in json.dumps(status) and "redis.example" not in json.dumps(status)


@pytest.mark.parametrize("value", [None, "", "   "])  # unset, empty, whitespace only
def test_a_missing_url_disables_redis_and_connects_to_nothing(monkeypatch, value):
    from app import redis_client
    if value is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", value)
    mgr = redis_client.RedisManager()
    assert redis_client.load_settings() is None
    assert mgr.start() == redis_client.DISABLED and mgr.client is None  # never a default localhost connection
    assert mgr.status()["state"] == "disabled"


def test_a_missing_client_library_is_reported_and_does_not_stop_the_api(monkeypatch, caplog):
    import sys
    from app import redis_client
    monkeypatch.setenv("REDIS_URL", f"redis://default:{SECRET}@redis.example:6379")
    monkeypatch.setitem(sys.modules, "redis", None)  # `import redis` raises ImportError
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    mgr = redis_client.RedisManager()
    with caplog.at_level(logging.DEBUG):
        assert mgr.start() == redis_client.MISCONFIGURED
    assert mgr.client is None and "not installed" in caplog.text and SECRET not in caplog.text
    assert mgr.status()["state"] == "misconfigured" and SECRET not in json.dumps(mgr.status())
    monkeypatch.setenv("REDIS_REQUIRED", "true")
    with pytest.raises(RuntimeError):
        redis_client.RedisManager().start()


@pytest.mark.parametrize("url, tls_flag, tls, scheme", [
    ("redis://default:CHANGE_ME@redis.example:6379", None, False, "redis://"),
    ("redis://default:CHANGE_ME@redis.example:6379", "true", True, "rediss://"),   # the `redis-cli --tls -u redis://` case
    ("rediss://default:CHANGE_ME@redis.example:6379", None, True, "rediss://"),
    ("rediss://default:CHANGE_ME@redis.example:6379", "false", True, "rediss://"),  # rediss:// always means TLS
])
def test_tls_follows_the_url_or_the_explicit_flag(monkeypatch, url, tls_flag, tls, scheme):
    from app import redis_client
    monkeypatch.setenv("REDIS_URL", url)
    if tls_flag is None:
        monkeypatch.delenv("REDIS_TLS", raising=False)
    else:
        monkeypatch.setenv("REDIS_TLS", tls_flag)
    s = redis_client.load_settings()
    assert s.tls is tls and redis_client._connection_url(s).startswith(scheme)
    assert "CHANGE_ME" not in s.safe_url and s.safe_url.startswith(url.split("://")[0] + "://***@redis.example:6379")


@pytest.mark.parametrize("name, value", [
    ("REDIS_TLS", "maybe"), ("REDIS_REQUIRED", "maybe"),
    ("REDIS_SOCKET_TIMEOUT_SECONDS", "0"), ("REDIS_SOCKET_TIMEOUT_SECONDS", "61"),
    ("REDIS_CONNECT_TIMEOUT_SECONDS", "abc"), ("REDIS_CONNECT_TIMEOUT_SECONDS", "-1"),
    ("REDIS_MAX_CONNECTIONS", "0"), ("REDIS_MAX_CONNECTIONS", "1001"), ("REDIS_MAX_CONNECTIONS", "2.5"),
    ("REDIS_HEALTH_CHECK_INTERVAL", "-1"), ("REDIS_HEALTH_CHECK_INTERVAL", "3601"),
    ("REDIS_HEALTH_CHECK_INTERVAL", "often"), ("REDIS_HEALTH_CHECK_INTERVAL", "1.5"),
])
def test_invalid_tuning_values_are_refused(monkeypatch, name, value):
    from app import redis_client
    monkeypatch.setenv("REDIS_URL", "redis://default:CHANGE_ME@redis.example:6379")
    if name != "REDIS_REQUIRED":
        monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(redis_client.RedisConfigError) as e:
        redis_client.load_settings()
    assert name in str(e.value)  # the operator is told which setting to fix
    mgr = redis_client.RedisManager()
    assert mgr.start() == redis_client.MISCONFIGURED and mgr.client is None  # a configuration error, not an outage


def test_valid_tuning_values_reach_the_shared_pool(monkeypatch):
    """Every REDIS_* tuning value is applied to the pool redis-py builds (the defaults are asserted further down)."""
    import redis
    from app import redis_client
    monkeypatch.setattr(redis.Redis, "ping", lambda self, **kw: True)  # no server needed: pool and client are real
    monkeypatch.setenv("REDIS_URL", "redis://default:CHANGE_ME@redis.example:6379/2")
    monkeypatch.delenv("REDIS_TLS", raising=False)
    for name, value in (("REDIS_CONNECT_TIMEOUT_SECONDS", "3.5"), ("REDIS_SOCKET_TIMEOUT_SECONDS", "4"),
                        ("REDIS_HEALTH_CHECK_INTERVAL", "0"), ("REDIS_MAX_CONNECTIONS", "7")):
        monkeypatch.setenv(name, value)
    mgr = redis_client.RedisManager()
    try:
        assert mgr.start() == redis_client.HEALTHY
        pool = mgr._pool
        kw = pool.connection_kwargs
        assert (kw["socket_connect_timeout"], kw["socket_timeout"], kw["health_check_interval"]) == (3.5, 4.0, 0)
        assert pool.max_connections == 7 and kw["db"] == 2
    finally:
        mgr.close()


@pytest.mark.parametrize("query, problem", [
    ("ssl_cert_reqs=none", "TLS options"), ("ssl_cert_reqs=optional", "TLS options"),
    ("ssl_check_hostname=false", "TLS options"), ("ssl_ca_certs=ca.pem", "TLS options"),
    ("socket_timeout=1&ssl_cert_reqs=none", "TLS options"),
    ("no_such_option=1", "not supported"), ("socket_timeout=1", "not supported"), ("db=3", "not supported"),
])
def test_url_query_options_are_refused_before_any_connection(monkeypatch, caplog, query, problem):
    """redis-py lets REDIS_URL query options override the keyword arguments the manager passes, so ssl_cert_reqs=none
    or ssl_check_hostname=false would silently switch verification off, and an unknown option would only fail at the
    first connection, looking like an outage. Any query option is a configuration error, found before connecting."""
    import redis
    from app import redis_client
    pings = []
    monkeypatch.setattr(redis.Redis, "ping", lambda self, **kw: pings.append(self) or True)
    monkeypatch.setenv("REDIS_URL", f"rediss://{USER}:{SECRET}@redis.example:6380/0?{query}")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    with pytest.raises(redis_client.RedisConfigError) as e:
        redis_client.load_settings()
    assert problem in str(e.value)
    mgr = redis_client.RedisManager()
    with caplog.at_level(logging.DEBUG):
        assert mgr.start() == redis_client.MISCONFIGURED
    status = mgr.status()
    assert mgr.client is None and pings == [] and status["state"] == "misconfigured" and "tls" not in status
    for text in (str(e.value), caplog.text, json.dumps(status)):
        assert SECRET not in text and USER not in text
    monkeypatch.setenv("REDIS_REQUIRED", "true")  # required: the API refuses to start, still without the credential
    with pytest.raises(redis_client.RedisConfigError) as e:
        redis_client.RedisManager().start()
    assert SECRET not in str(e.value) and USER not in str(e.value)


def test_an_unrecognised_query_key_is_never_repeated(monkeypatch):
    """A key outside redis-py's option names (text pasted into the wrong part of the URL) is not echoed."""
    from app import redis_client
    monkeypatch.setenv("REDIS_URL", f"rediss://default:CHANGE_ME@redis.example:6380/0?{SECRET}")
    with pytest.raises(redis_client.RedisConfigError) as e:
        redis_client.load_settings()
    assert SECRET not in str(e.value) and "unrecognised option" in str(e.value)


@pytest.mark.parametrize("url, tls_flag", [
    ("rediss://default:CHANGE_ME@redis.example:6380/0", None),
    ("redis://default:CHANGE_ME@redis.example:6379/0", "true"),  # REDIS_TLS=true on a redis:// URL
])
def test_tls_connections_verify_the_certificate_and_the_host_name(monkeypatch, url, tls_flag):
    """Read from the connection redis-py would open (constructed, never connected): the certificate chain and the host
    name are both verified, whatever the installed redis-py's own defaults are."""
    import ssl
    import redis
    from app import redis_client
    monkeypatch.setattr(redis.Redis, "ping", lambda self, **kw: True)
    monkeypatch.setenv("REDIS_URL", url)
    if tls_flag is None:
        monkeypatch.delenv("REDIS_TLS", raising=False)
    else:
        monkeypatch.setenv("REDIS_TLS", tls_flag)
    mgr = redis_client.RedisManager()
    try:
        assert mgr.start() == redis_client.HEALTHY and mgr.status()["tls"] is True
        pool = mgr._pool
        conn = pool.connection_class(**pool.connection_kwargs)
        assert isinstance(conn, redis.SSLConnection)
        assert conn.cert_reqs == ssl.CERT_REQUIRED and conn.check_hostname is True
    finally:
        mgr.close()


# ------------------------------------------------------------------------------------------------ connection lifecycle
def test_start_builds_one_shared_pool_verifies_it_and_close_releases_it(monkeypatch):
    """start() creates the pool once and PINGs it; every caller shares that client; close() disconnects the pool."""
    import redis
    from app import redis_client
    made = []

    class Pool:
        connection_class = redis.SSLConnection  # what from_url picks for rediss://

        def __init__(self, kw):
            self.disconnected, self.connection_kwargs = 0, kw  # the effective settings the manager checks

        def disconnect(self, *a, **k):
            self.disconnected += 1

    def from_url(url, **kw):
        made.append((url, kw))
        return Pool(kw)
    monkeypatch.setattr(redis.ConnectionPool, "from_url", staticmethod(from_url))
    monkeypatch.setattr(redis, "Redis", MemoryRedis)  # the client built on that pool
    monkeypatch.setenv("REDIS_URL", f"redis://default:{SECRET}@redis.example:6379/0")
    monkeypatch.setenv("REDIS_TLS", "true")
    mgr = redis_client.RedisManager()
    assert mgr.start() == redis_client.HEALTHY
    [(url, kw)] = made
    assert url.startswith("rediss://")  # TLS, as `redis-cli --tls`
    assert kw["socket_connect_timeout"] == 2.0 and kw["socket_timeout"] == 2.0 and kw["retry_on_timeout"] is True
    assert kw["ssl_cert_reqs"] == "required" and kw["ssl_check_hostname"] is True and kw["max_connections"] == 20
    assert kw["health_check_interval"] == 30
    client = mgr.client
    assert client.set("probe", "1") and mgr.client is client  # one shared client, not one per request
    pool = mgr._pool
    assert client.connection_pool is pool  # the client runs on the shared pool
    mgr.close()
    assert pool.disconnected == 1 and mgr.client is None and mgr.state == redis_client.DISABLED


def test_an_unreachable_server_is_reported_and_does_not_stop_the_api(monkeypatch, caplog):
    import redis
    from app import redis_client
    monkeypatch.setattr(redis.ConnectionPool, "from_url", staticmethod(lambda url, **kw: redis.ConnectionPool()))
    monkeypatch.setattr(redis.Redis, "ping", lambda self: (_ for _ in ()).throw(redis.exceptions.ConnectionError("x")))
    monkeypatch.setenv("REDIS_URL", f"redis://default:{SECRET}@redis.example:6379")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    mgr = redis_client.RedisManager()
    with caplog.at_level(logging.DEBUG):
        assert mgr.start() == redis_client.UNAVAILABLE
    assert SECRET not in caplog.text and mgr.status() == {"state": "unavailable", "required": False, "tls": False,
                                                          "reason": "ConnectionError"}
    monkeypatch.setenv("REDIS_REQUIRED", "true")
    with pytest.raises(RuntimeError) as e:  # required: refuse to start, still without the credential
        redis_client.RedisManager().start()
    assert SECRET not in str(e.value)
    mgr.close()


@pytest.mark.parametrize("error", ["AuthenticationError", "TimeoutError", "ConnectionError"])
def test_a_failed_start_up_ping_is_named_by_its_class_without_the_credential(monkeypatch, caplog, error):
    """Wrong credentials, a timeout or a refused connection at start-up, through a real pool: the API still starts
    (REDIS_REQUIRED unset), the outage is reported by exception class only, and neither the password nor the user name
    reaches the log, an error or /api/health, even when the server's own message quotes them."""
    import redis
    from app import redis_client
    exc = getattr(redis.exceptions, error)

    def fail(self, **kw):
        raise exc(f"WRONGPASS {USER}:{SECRET} invalid username-password pair or user is disabled.")
    monkeypatch.setattr(redis.Redis, "ping", fail)
    monkeypatch.setenv("REDIS_URL", f"rediss://{USER}:{SECRET}@redis.example:6380/0")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    mgr = redis_client.RedisManager()
    try:
        with caplog.at_level(logging.DEBUG):
            assert mgr.start() == redis_client.UNAVAILABLE
        status = mgr.status()
        assert status == {"state": "unavailable", "required": False, "tls": True, "reason": error}
        for text in (caplog.text, json.dumps(status)):
            assert SECRET not in text and USER not in text
    finally:
        mgr.close()
    monkeypatch.setenv("REDIS_REQUIRED", "true")
    with pytest.raises(RuntimeError) as e:
        redis_client.RedisManager().start()
    assert SECRET not in str(e.value) and USER not in str(e.value)


@pytest.mark.parametrize("tamper", ["certificate check off", "host-name check off", "plain connection"])
def test_a_pool_whose_effective_tls_settings_do_not_verify_is_refused(monkeypatch, caplog, tamper):
    """Defence in depth: were a redis-py release ever to drop or override the verification arguments, the manager reads
    the pool's effective settings and reports misconfigured, never a healthy TLS connection, without connecting."""
    import redis
    from app import redis_client
    real_from_url = redis.ConnectionPool.from_url

    def from_url(url, **kw):
        pool = real_from_url(url, **kw)
        if tamper == "certificate check off":
            pool.connection_kwargs["ssl_cert_reqs"] = "none"
        elif tamper == "host-name check off":
            pool.connection_kwargs["ssl_check_hostname"] = False
        else:
            pool.connection_class = redis.Connection
        return pool
    pings = []
    monkeypatch.setattr(redis.ConnectionPool, "from_url", staticmethod(from_url))
    monkeypatch.setattr(redis.Redis, "ping", lambda self, **kw: pings.append(self) or True)
    monkeypatch.setenv("REDIS_URL", f"rediss://{USER}:{SECRET}@redis.example:6380/0")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    mgr = redis_client.RedisManager()
    with caplog.at_level(logging.DEBUG):
        assert mgr.start() == redis_client.MISCONFIGURED
    assert mgr.client is None and pings == [] and mgr.status()["state"] == "misconfigured"
    assert SECRET not in caplog.text and USER not in caplog.text
    monkeypatch.setenv("REDIS_REQUIRED", "true")
    with pytest.raises(RuntimeError) as e:
        redis_client.RedisManager().start()
    assert SECRET not in str(e.value) and USER not in str(e.value)


# ------------------------------------------------------------------------------------------------ cache operations
def test_get_set_exists_expire_delete_and_json_round_trip(fake):
    from app.cache import cache, tenant_key
    k = tenant_key("tenant-a", "probe", "1")
    assert cache.get(k) is None and cache.exists(k) is False
    assert cache.set(k, {"a": [1, 2], "when": "2026-01-01T00:00:00+00:00"}, ttl=60)
    assert cache.exists(k) and cache.get(k) == {"a": [1, 2], "when": "2026-01-01T00:00:00+00:00"}
    assert 0 < fake.ttl(k) <= 60
    assert cache.expire(k, 5) and fake.ttl(k) <= 5
    assert cache.delete(k) and cache.get(k) is None and not cache.exists(k)


def test_entries_expire(fake):
    from app.cache import cache, global_key
    k = global_key("probe", "ttl")
    assert cache.set(k, [1], ttl=1)
    time.sleep(1.2)
    assert cache.get(k) is None and not cache.exists(k)


@pytest.mark.parametrize("raw", ["not json", json.dumps([1, 2]), json.dumps({"v": "v0", "data": 1}),
                                 json.dumps({"v": "v1"})])
def test_malformed_values_are_misses_and_are_discarded(fake, raw):
    from app.cache import cache, global_key
    k = global_key("probe", "malformed")
    fake.set(k, raw)
    assert cache.get(k) is None and not fake.exists(k)


def test_tenant_keys_isolate_organisations_and_refuse_unsafe_keys(fake):
    from app.cache import CacheKeyError, cache, global_key, tenant_key
    a, b = tenant_key("tenant-a", "facts", "subj-1"), tenant_key("tenant-b", "facts", "subj-1")
    assert a != b and ":tenant:tenant-a:" in a and ":tenant:tenant-b:" in b  # same object ID, different namespaces
    assert tenant_key("tenant-a", "x", entity_id="e1") != tenant_key("tenant-a", "x", entity_id="e2")
    cache.set(a, {"owner": "A"})
    assert cache.get(b) is None and cache.get(a) == {"owner": "A"}
    assert ":global:" in global_key("catalog", "x") and ":tenant:" not in global_key("catalog", "x")
    for bad in (lambda: tenant_key("", "x"), lambda: tenant_key("tenant a", "x"), lambda: global_key("x", "a:b"),
                lambda: global_key("x", "*")):
        with pytest.raises(CacheKeyError):
            bad()


def test_an_outage_is_a_miss_never_an_error_and_is_rechecked(monkeypatch):

    from app import redis_client
    from app.cache import Cache, global_key
    mgr = redis_client.RedisManager()
    mgr.use_client(Broken())
    c = Cache(mgr)
    k = global_key("probe", "down")
    assert c.get(k) is None and c.set(k, 1) is False and c.exists(k) is False and c.expire(k, 5) is False
    assert c.delete(k) is False
    assert mgr.state == redis_client.UNAVAILABLE and c.available is False  # bypassed, no per-request timeouts
    assert c.get_or_build(k, lambda: {"from": "postgresql"}) == ({"from": "postgresql"}, False)
    # Recovery: the next re-check after the interval finds the server again.
    mgr._client = MemoryRedis()
    mgr._checked_at -= mgr.RECHECK_SECONDS + 1
    assert c.available is True and c.set(k, 2) and c.get(k) == 2


# ------------------------------------------------------------------------------------------------ catalogue cache
URLS = ["/api/catalog/obligation-definitions", "/api/catalog/controls"]
READER = "actor-content-analyst"


def test_the_catalogue_cache_is_off_by_default(client, fake, monkeypatch):
    monkeypatch.delenv("ZA_CATALOG_CACHE", raising=False)
    for url in URLS:
        assert client.get(url, headers=H(READER)).status_code == 200
    assert not [k for k in fake.keys("*") if ":catalog:" in k]


def test_cached_catalogue_reads_equal_uncached_ones_and_are_keyed_by_watermark(client, fake, monkeypatch):
    for url in URLS:
        monkeypatch.delenv("ZA_CATALOG_CACHE", raising=False)
        direct = client.get(url, headers=H(READER)).json()
        monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
        first = client.get(url, headers=H(READER))
        second = client.get(url, headers=H(READER))
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json() == direct  # byte-for-byte the same content, cached or not
    keys = [k for k in fake.keys("*") if ":catalog:" in k]
    assert keys and all(":global:catalog:" in k and ":seq-" in k for k in keys)


def test_a_catalogue_write_invalidates_the_cached_read(client, fake, monkeypatch):
    """Authoritative writes advance the record watermark, so the next read cannot be served from an older entry."""
    from app.db import AdminSession
    from app.models import ControlDefinition
    from app.platform import next_record_seq
    monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
    before = client.get("/api/catalog/controls", headers=H(READER)).json()
    key = f"C_CACHE_PROBE_{int(time.time() * 1000)}"
    with AdminSession() as db:  # an authoritative catalogue write, with its record sequence, as the application does
        db.add(ControlDefinition(key=key, title="Cache probe", objective="probe", category="Probe"))
        next_record_seq(db)
        db.commit()
    after = client.get("/api/catalog/controls", headers=H(READER)).json()
    assert key not in [c["key"] for c in before] and key in [c["key"] for c in after]


def test_authorisation_is_checked_before_the_cache_on_every_request(client, fake, monkeypatch):
    monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
    for url in URLS:
        assert client.get(url, headers=H(READER)).status_code == 200  # warm
        assert client.get(url).status_code == 401
        for actor in ("oda-super", "actor-agent", "rt-super"):
            r = client.get(url, headers=H(actor))
            assert r.status_code in (401, 403), (actor, r.status_code)  # never served from the warm cache


def test_catalogue_reads_fall_back_to_postgresql_when_redis_is_down(client, monkeypatch):
    from app import redis_client
    monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
    redis_client.manager.close()
    try:
        expected = [client.get(url, headers=H(READER)).json() for url in URLS]
        redis_client.manager.use_client(Broken())
        for url, exp in zip(URLS, expected):
            r = client.get(url, headers=H(READER))
            assert r.status_code == 200 and r.json() == exp
    finally:
        redis_client.manager.close()


def test_existing_assurance_behaviour_is_unchanged_without_redis(client, no_redis, monkeypatch):
    """Obligation evaluation never touches Redis: the same preview with Redis disabled or broken."""
    from app import redis_client
    monkeypatch.setenv("ZA_CATALOG_CACHE", "true")
    obls = client.get("/api/obligations", headers=H("actor-cco")).json()
    lineage = next(o["lineage_id"] for o in obls if o["definition"]["key"] == "DORA_ICT_RISK_FRAMEWORK")
    body = {"period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z"}
    a = client.post(f"/api/obligations/{lineage}/preview", headers=H("actor-cco"), json=body).json()
    redis_client.manager.use_client(Broken())
    b = client.post(f"/api/obligations/{lineage}/preview", headers=H("actor-cco"), json=body).json()
    assert (a["state"], a["dependency_digest"]) == (b["state"], b["dependency_digest"])


# ------------------------------------------------------------------------------------------------ health
def test_health_distinguishes_application_postgresql_and_redis(client, monkeypatch):

    from app import redis_client
    redis_client.manager.close()
    try:
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        comps = r.json()["components"]
        assert comps["application"]["state"] == "healthy" and comps["postgresql"]["state"] == "healthy"
        assert comps["redis"]["state"] == "disabled"
        redis_client.manager.use_client(MemoryRedis())
        r = client.get("/api/health").json()
        assert r["status"] == "ok" and r["components"]["redis"]["state"] == "healthy"
        redis_client.manager.use_client(Broken())  # configured but down: not reported as fully healthy
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "degraded"
        assert r.json()["components"]["redis"] == {"state": "unavailable", "required": False, "tls": False,
                                                   "reason": "ConnectionError"}
        required = redis_client.RedisSettings(url="redis://test.invalid:6379/0", tls=False, connect_timeout=1,
                                              socket_timeout=1, health_check_interval=0, max_connections=1, required=True)
        redis_client.manager.use_client(Broken(), required)
        r = client.get("/api/health")
        assert r.status_code == 503 and r.json()["status"] == "unavailable"
        assert "test.invalid" not in r.text
    finally:
        redis_client.manager.close()


# ------------------------------------------------------------------------------------------------ API lifespan
@pytest.mark.parametrize("up", [True, False], ids=["redis-up", "redis-down"])
def test_the_api_lifespan_starts_and_closes_the_shared_pool(client, monkeypatch, up):
    """app/main.py: start-up builds the pool and PINGs it once when REDIS_URL is set, and still starts during an outage
    (REDIS_REQUIRED unset; reported as degraded); shutdown closes the pool. No credential reaches /api/health."""
    import redis
    from fastapi.testclient import TestClient
    from app import redis_client
    from app.main import app
    pings = []

    def ping(self, **kw):
        pings.append(self)
        if not up:
            raise redis.exceptions.ConnectionError("connection refused")
        return True
    monkeypatch.setattr(redis.Redis, "ping", ping)
    monkeypatch.setenv("REDIS_URL", f"rediss://{USER}:{SECRET}@redis.example:6380/0")
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    redis_client.manager.close()
    try:
        with TestClient(app) as c:
            assert len(pings) == 1 and redis_client.manager.client is not None  # one PING at start-up, one shared client
            r = c.get("/api/health")
            assert r.status_code == 200 and r.json()["status"] == ("ok" if up else "degraded")
            assert r.json()["components"]["redis"]["state"] == ("healthy" if up else "unavailable")
            assert SECRET not in r.text and USER not in r.text
        assert redis_client.manager.client is None and redis_client.manager.state == redis_client.DISABLED  # closed
    finally:
        redis_client.manager.close()
