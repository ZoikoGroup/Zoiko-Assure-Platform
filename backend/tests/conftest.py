import os
import shutil
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="za-test-")
os.environ["ZA_DATA_DIR"] = _tmp
os.environ["ZA_DEV_IDENTITY_HEADER"] = "1"  # tests simulate agents/services via the dev header
os.environ["ZA_SKIP_DOTENV"] = "1"          # never touch the real database configured in backend/.env
# Default: throwaway SQLite. To run against PostgreSQL + RLS, point these at a DISPOSABLE test database:
#   ZA_TEST_DATABASE_URL=postgresql+psycopg://assure_app:...@host/assure_test
#   ZA_TEST_DATABASE_ADMIN_URL=postgresql+psycopg://assure:...@host/assure_test
if os.environ.get("ZA_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["ZA_TEST_DATABASE_URL"]
    os.environ["DATABASE_ADMIN_URL"] = os.environ["ZA_TEST_DATABASE_ADMIN_URL"]
else:
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATABASE_ADMIN_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_tmp, ignore_errors=True)


def H(actor):
    return {"X-Actor-Id": actor}
