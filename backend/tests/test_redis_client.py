"""Optional Redis connection (app/redis_client.py): connection only, never fatal, and never shows the URL's secrets.

No test needs a Redis server. PING is replaced in-process; the command-line cases connect for real, in a fresh
interpreter, to a port where nothing listens. tests/conftest.py removes any real REDIS_URL from the environment.
"""
import logging
import os
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest
import redis

from app import redis_client

BACKEND = Path(__file__).resolve().parent.parent
SECRET, USER, HOST = "Not-A-Real-Secret-42", "za-test-user", "redis-test.internal.example"
URL = f"redis://{USER}:{SECRET}@{HOST}:6379/0"
TLS_URL = f"rediss://{USER}:{SECRET}@{HOST}:6380/0"


def assert_no_secrets(text: str, *secrets: str):
    for s in secrets or (SECRET, USER, HOST):
        assert s.lower() not in text.lower()


@pytest.fixture(autouse=True)
def no_shared_client():
    redis_client.close()
    yield
    redis_client.close()


@pytest.fixture
def ping_ok(monkeypatch):
    calls = []
    monkeypatch.setattr(redis.Redis, "ping", lambda self, **kw: calls.append(self) or True)
    return calls


@pytest.mark.parametrize("environ", [{}, {"REDIS_URL": ""}, {"REDIS_URL": "   "}])
def test_disabled_without_redis_url(environ):
    assert redis_client.start(environ) == "Redis: disabled (REDIS_URL is not set)"
    with pytest.raises(redis_client.RedisNotConfigured):
        redis_client.get_client()  # no fallback store
    redis_client.close()  # a no-op when disabled


def test_start_pings_once_and_close_releases_the_shared_client(ping_ok, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    line = redis_client.start({"REDIS_URL": URL})
    assert line.startswith("Redis: PING ok (") and line.endswith(" ms, no TLS)")
    client = redis_client.get_client()
    assert ping_ok == [client] and redis_client.get_client() is client  # one PING, one shared client
    kw = client.connection_pool.connection_kwargs
    assert (kw["socket_connect_timeout"], kw["socket_timeout"], kw["health_check_interval"]) == (5.0, 5.0, 30)
    assert client.connection_pool.max_connections == redis_client.MAX_CONNECTIONS
    assert "unencrypted" in caplog.text  # redis:// to another machine is flagged
    redis_client.close()
    assert "Redis: connection pool closed" in caplog.text
    with pytest.raises(redis_client.RedisNotConfigured):
        redis_client.get_client()
    assert_no_secrets(line + caplog.text)


def test_timeouts_are_configurable_and_validated(ping_ok):
    client = redis_client.connect(URL, {"ZA_REDIS_CONNECT_TIMEOUT": "2", "ZA_REDIS_SOCKET_TIMEOUT": "3.5"})
    kw = client.connection_pool.connection_kwargs
    assert (kw["socket_connect_timeout"], kw["socket_timeout"]) == (2.0, 3.5)
    client.close()
    with pytest.raises(redis_client.RedisNotConfigured, match="ZA_REDIS_CONNECT_TIMEOUT"):
        redis_client.connect(URL, {"ZA_REDIS_CONNECT_TIMEOUT": "soon"})


def test_unreachable_redis_does_not_stop_start_up_and_hides_the_url(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    def refuse(self, **kw):  # worded as redis-py does, plus the credential, to prove both are redacted
        message = f"Error 11001 connecting to {HOST.upper()}:6379. getaddrinfo failed. {USER}:{SECRET}"
        raise redis.ConnectionError(message)
    monkeypatch.setattr(redis.Redis, "ping", refuse)
    line = redis_client.start({"REDIS_URL": URL})
    assert line.startswith("Redis: unavailable at start-up") and "cannot connect" in line
    assert "ConnectionError: Error 11001 connecting to <redacted>:6379" in line
    assert redis_client.get_client()  # the pool stays and reconnects on first use once Redis is back
    assert_no_secrets(line + caplog.text)


def test_authentication_failure_is_named_without_the_credential(monkeypatch):
    def wrongpass(self, **kw):
        raise redis.AuthenticationError("WRONGPASS invalid username-password pair or user is disabled.")
    monkeypatch.setattr(redis.Redis, "ping", wrongpass)
    client = redis_client.connect(URL)
    try:
        with pytest.raises(redis_client.RedisUnavailable, match="authentication failed") as e:
            redis_client.ping(client)
    finally:
        client.close()
    assert_no_secrets(str(e.value))


def test_rediss_keeps_certificate_and_host_name_verification():
    client = redis_client.connect(TLS_URL)
    try:
        pool = client.connection_pool
        conn = pool.connection_class(**pool.connection_kwargs)  # the settings a real connection gets; no I/O
        assert isinstance(conn, redis.SSLConnection)
        assert conn.cert_reqs == ssl.CERT_REQUIRED and conn.check_hostname is True
        assert not redis_client._plaintext_remote(client)
    finally:
        client.close()


@pytest.mark.parametrize("option", ["ssl_cert_reqs=none", "ssl_cert_reqs=optional", "ssl_check_hostname=false"])
def test_a_url_that_turns_off_tls_verification_is_refused(option, ping_ok):
    with pytest.raises(redis_client.RedisNotConfigured, match="verification") as e:
        redis_client.connect(f"{TLS_URL}?{option}")
    assert_no_secrets(str(e.value))
    line = redis_client.start({"REDIS_URL": f"{TLS_URL}?{option}"})
    assert line.startswith("Redis: not started:") and ping_ok == []  # refused before any connection; never fatal


@pytest.mark.parametrize("url", [f"http://{USER}:{SECRET}@{HOST}:6379/0", f"{USER}:{SECRET}@{HOST}:6379",
                                 f"redis://{USER}:{SECRET}@{HOST}:port/0", f"{URL}?no_such_option=1"])
def test_an_invalid_url_is_refused_without_its_secrets(url):
    with pytest.raises(redis_client.RedisNotConfigured, match="REDIS_URL") as e:
        redis_client.connect(url)
    assert_no_secrets(str(e.value))


def test_the_api_lifespan_opens_and_closes_the_shared_client(client, monkeypatch, ping_ok):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("REDIS_URL", URL)
    with TestClient(app) as c:
        assert set(c.get("/api/health").json()) == {"status", "cell_id", "dictionary_version"}  # unchanged
        assert ping_ok == [redis_client.get_client()]
    with pytest.raises(redis_client.RedisNotConfigured):
        redis_client.get_client()


def _cli(tmp_path, **env):
    clean = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DATABASE_ADMIN_URL", "REDIS_URL")}
    clean.update({"ZA_SKIP_DOTENV": "1", "ZA_ALLOW_SQLITE": "1", "ZA_DATA_DIR": str(tmp_path), **env})
    return subprocess.run([sys.executable, "-m", "app.redis_client"], cwd=BACKEND, env=clean, capture_output=True,
                          text=True, timeout=120)


def test_cli_without_redis_url_is_refused(tmp_path):
    r = _cli(tmp_path)
    assert r.returncode == 2
    assert "REDIS_URL is not set" in r.stderr


def test_cli_unreachable_redis_fails_clearly_and_bounded_without_secrets(tmp_path):
    # Nothing listens on port 1: a real connection attempt, bounded by the connect timeout and one retry.
    started = time.monotonic()
    r = _cli(tmp_path, REDIS_URL=f"redis://{USER}:{SECRET}@127.0.0.1:1/0", ZA_REDIS_CONNECT_TIMEOUT="2")
    assert r.returncode == 1, r.stderr
    # Linux refuses at once; Windows retries a refused loopback connection until the connect timeout.
    assert ("ERROR Redis PING failed: cannot connect" in r.stderr
            or "ERROR Redis PING failed: no answer within the timeout" in r.stderr), r.stderr
    assert time.monotonic() - started < 60
    assert_no_secrets(r.stdout + r.stderr, SECRET, USER, "127.0.0.1")
