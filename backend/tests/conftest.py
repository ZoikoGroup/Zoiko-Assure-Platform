import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="za-test-")
os.environ["ZA_DATA_DIR"] = _tmp
os.environ["ZA_DEV_IDENTITY_HEADER"] = "1"  # tests simulate agents/services via the dev header
os.environ["ZA_SKIP_DOTENV"] = "1"          # never touch the real database configured in backend/.env
os.environ["ZA_DEMO_MODE"] = "1"            # tests run on the synthetic Customer Zero fixture
os.environ["ZA_DEMO_PASSWORD"] = secrets.token_urlsafe(18)  # per-session test password; none is built into the app
# Default: throwaway SQLite. To run against PostgreSQL + RLS, point these at a DISPOSABLE test database:
#   ZA_TEST_DATABASE_URL=postgresql+psycopg://assure_app:...@host/assure_test
#   ZA_TEST_DATABASE_ADMIN_URL=postgresql+psycopg://assure:...@host/assure_test
if os.environ.get("ZA_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["ZA_TEST_DATABASE_URL"]
    os.environ["DATABASE_ADMIN_URL"] = os.environ["ZA_TEST_DATABASE_ADMIN_URL"]
else:
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATABASE_ADMIN_URL", None)
    os.environ["ZA_ALLOW_SQLITE"] = "1"  # explicit opt-in: the app never falls back to SQLite on its own
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:
        _reset_test_credentials()
        yield c
    shutil.rmtree(_tmp, ignore_errors=True)


def _reset_test_credentials():
    """Tests own their sign-in credentials: every interactive actor gets this session's password, unlocked.
    (A persistent PostgreSQL test database may still hold credentials from an earlier session.)"""
    from app.auth import DEMO_PASSWORD, INTERACTIVE_TYPES, hash_password
    from app.db import AdminSession
    from app.models import Actor, AuthCredential
    with AdminSession() as db:
        for a in db.query(Actor).filter(Actor.actor_type.in_(INTERACTIVE_TYPES)):
            cred = db.get(AuthCredential, a.actor_id) or AuthCredential(actor_id=a.actor_id)
            cred.password_hash, cred.failed_attempts, cred.locked_until = hash_password(DEMO_PASSWORD), 0, None
            db.merge(cred)
        db.commit()


def H(actor):
    return {"X-Actor-Id": actor}
