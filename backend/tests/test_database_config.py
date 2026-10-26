"""The API runs on PostgreSQL only (app/db.py): no silent SQLite fallback, and a clear, secret-free start-up failure.

Each case imports the app in a fresh interpreter, because the database is chosen at import time.
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _run(code: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DATABASE_ADMIN_URL", "ZA_ALLOW_SQLITE")}
    clean.update({"ZA_SKIP_DOTENV": "1", "ZA_DATA_DIR": str(tmp_path), **env})  # never read the real backend/.env
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=clean, capture_output=True, text=True, timeout=120)


def test_no_database_configured_refuses_to_start(tmp_path):
    r = _run("import app.db", tmp_path)
    assert r.returncode != 0
    assert "DATABASE_URL is not set" in r.stderr
    assert not (tmp_path / "assure.db").exists()  # and no SQLite file was quietly created


def test_sqlite_requires_the_explicit_test_opt_in(tmp_path):
    url = f"sqlite:///{(tmp_path / 'probe.db').as_posix()}"
    refused = _run("import app.db", tmp_path, DATABASE_URL=url)
    assert refused.returncode != 0
    assert "only for isolated test runs" in refused.stderr
    allowed = _run("import app.db; print(app.db.IS_SQLITE)", tmp_path, DATABASE_URL=url, ZA_ALLOW_SQLITE="1")
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout.strip() == "True"


def test_unreachable_postgres_fails_clearly_without_the_password(tmp_path):
    secret = "Not-A-Real-Secret-42"
    # Nothing listens on port 1. Without a connect timeout this hung forever on Windows instead of failing.
    r = _run("from app.db import check_database; check_database()", tmp_path, ZA_DB_CONNECT_TIMEOUT="3",
             DATABASE_URL=f"postgresql+psycopg://assure_app:{secret}@127.0.0.1:1/assure",
             DATABASE_ADMIN_URL=f"postgresql+psycopg://assure:{secret}@127.0.0.1:1/assure")
    assert r.returncode != 0
    assert "Cannot connect to PostgreSQL with DATABASE_ADMIN_URL (assure@127.0.0.1:1/assure)" in r.stderr
    assert secret not in r.stderr + r.stdout


def test_mixed_database_kinds_are_refused(tmp_path):
    placeholder = "not-a-secret"
    r = _run("import app.db", tmp_path, ZA_ALLOW_SQLITE="1",
             DATABASE_URL=f"sqlite:///{(tmp_path / 'probe.db').as_posix()}",
             DATABASE_ADMIN_URL=f"postgresql+psycopg://assure:{placeholder}@127.0.0.1:1/assure")
    assert r.returncode != 0
    assert "must both be PostgreSQL URLs" in r.stderr
