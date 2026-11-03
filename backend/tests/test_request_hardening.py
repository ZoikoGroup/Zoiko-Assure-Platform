"""Stage 4d/4e remediation: the development identity header guard (I-08) and unknown-field rejection (I-09).

Requirements: IAM-REQ-001, SEC-REQ-024 (no unauthenticated impersonation path outside development/test);
API-REQ-014 / SEC-REQ-022 (mutation request schemas reject unknown fields; server-owned fields not mass-assignable).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from conftest import H

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------------------ 4d dev header guard
@pytest.mark.parametrize("env, refused", [(None, True), ("", True), ("production", True), ("staging", True),
                                          ("Production", True), ("development", False), ("test", False), (" TEST ", False)])
def test_dev_identity_header_is_allowed_only_in_development_or_test(env, refused):
    from app.security import dev_header_violation
    environ = {} if env is None else {"ZA_ENVIRONMENT": env}
    assert (dev_header_violation(environ, enabled=True) is not None) is refused
    assert dev_header_violation(environ, enabled=False) is None  # header off: nothing to refuse


@pytest.mark.parametrize("env", ["production", None])
def test_the_api_refuses_to_start_with_the_dev_header_outside_development(env):
    """The real start-up path: importing the app in a fresh process with the header on and no development marker."""
    run_env = {k: v for k, v in os.environ.items() if not k.startswith(("ZA_", "DATABASE_"))}
    run_env.update(ZA_DEV_IDENTITY_HEADER="1", ZA_ALLOW_SQLITE="1", ZA_SKIP_DOTENV="1", ZA_DATA_DIR=tempfile.mkdtemp(),
                   PYTHONDONTWRITEBYTECODE="1")
    if env:
        run_env["ZA_ENVIRONMENT"] = env
    r = subprocess.run([sys.executable, "-c", "import app.main"], cwd=BACKEND, env=run_env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode != 0 and "ZA_DEV_IDENTITY_HEADER=1" in r.stderr, r.stderr[-400:]
    run_env["ZA_ENVIRONMENT"] = "development"
    ok = subprocess.run([sys.executable, "-c", "import app.main"], cwd=BACKEND, env=run_env, capture_output=True, text=True,
                        timeout=120)
    assert ok.returncode == 0, ok.stderr[-400:]


# ------------------------------------------------------------------------------------------------ 4e unknown fields
def _body_models():
    from app.main import ROUTERS
    seen: dict[type, set] = {}

    def walk(m, route):
        if isinstance(m, type) and issubclass(m, BaseModel):
            if route in seen.setdefault(m, set()):
                return
            seen[m].add(route)
            for f in m.model_fields.values():
                for a in getattr(f.annotation, "__args__", ()) or (f.annotation,):
                    walk(a, route)
    for r in ROUTERS:
        for rt in r.routes:
            if isinstance(rt, APIRoute):
                for bp in rt.dependant.body_params:
                    walk(bp.field_info.annotation if hasattr(bp, "field_info") else bp.type_, f"{sorted(rt.methods)} {rt.path}")
    return seen


SERVER_OWNED = {"tenant_id", "actor_id", "cell_id", "home_cell_id", "record_seq", "recorded_at", "granted_by",
                "created_by", "approved_by", "placed_by", "assurance_state"}


def test_every_request_model_rejects_unknown_and_server_owned_fields():
    models = _body_models()
    assert len(models) >= 45
    lax = sorted(f"{m.__module__}.{m.__name__}" for m in models if m.model_config.get("extra") != "forbid")
    assert lax == [], f"request models that silently accept unknown fields: {lax}"
    owned = sorted(f"{m.__name__}.{f}" for m in models for f in m.model_fields if f in SERVER_OWNED)
    assert owned == [], f"server-owned fields a client could set: {owned}"


@pytest.mark.parametrize("method, path, actor, body", [
    ("POST", "/api/auth/login", None, {"email": "x@example.com", "password": "y", "remember_me": True}),
    ("POST", "/api/exceptions", "actor-cco", {"target_type": "OBLIGATION", "target_id": "x", "reason": "probe",
                                              "valid_from": "2026-07-01T00:00:00Z", "valid_to": "2026-08-01T00:00:00Z",
                                              "state": "APPROVED"}),
    ("POST", "/api/issues/x/transition", "actor-owner", {"state": "TRIAGED", "owner_actor_id": "actor-cco"}),
    ("POST", "/api/evidence/x/hold", "actor-gc", {"reason": "probe", "tenant_id": "tenant-authz-b"}),
])
def test_unknown_fields_are_refused_before_anything_is_written(client, method, path, actor, body):
    from test_authorization import db_fingerprint
    before = db_fingerprint()
    r = client.request(method, path, headers=H(actor) if actor else {}, json=body)
    assert r.status_code == 422 and "extra" in r.text.lower(), (path, r.status_code, r.text[:200])
    assert db_fingerprint() == before
