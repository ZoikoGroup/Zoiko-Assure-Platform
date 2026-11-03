"""Generated per-role read authorization matrix (roadmap Stage 4 test row: "generated route x role matrix").

Two checks over every non-public GET operation and one principal per role:

1. Behaviour: each request is refused (403, recorded) exactly when the operation's declared requirement says so, and
   never refused when it allows the role. This proves the guards enforce the operation registry.
2. Review: the resulting role -> readable-operations table must equal tests/read_authorization_matrix.json. A change to
   read policy therefore shows up as a reviewed diff of that file (ZA-IAM-001 §07: policy changes produce a semantic
   diff of newly allowed/denied action classes). The table is the PROVISIONAL read policy pending decision G-08
   (IAM Lead); it records what the code does, not an approved policy.

Regenerate the file deliberately with:  python tests/test_read_authorization_matrix.py  (from backend/)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import H, add_principal

GOLDEN = Path(__file__).with_name("read_authorization_matrix.json")
# One principal per role. Seeded by the Customer Zero fixture, or created here (rm-*).
PRINCIPALS = {
    "CCO": "actor-cco", "LEGAL_REVIEWER": "actor-gc", "REGULATORY_ANALYST": "actor-analyst",
    "CONTROL_OWNER": "actor-owner", "INTERNAL_AUDIT": "actor-audit", "AI_GOVERNANCE_LEAD": "actor-aigov",
    "PLATFORM_ADMIN": "actor-admin", "RISK_APPROVER": "actor-risk", "SERVICE": "actor-svc",
    "EXTERNAL_AUDITOR": "actor-ext-auditor", "AGENT (role CCO)": "actor-agent",
    "CONTENT_ANALYST": "actor-content-analyst", "ENCODING_ENGINEER": "actor-encoder",
    "CONTENT_LEGAL_REVIEWER": "actor-content-legal", "CONTENT_RELEASE_MANAGER": "actor-release",
    "TENANT_OWNER": "rm-owner", "TENANT_SECURITY_ADMIN": "rm-tsa", "SUPER_ADMIN": "rm-super",
    "CONNECTOR_CERTIFICATION_MANAGER": "rm-certifier",
}
CREATED = {"rm-owner": ("tenant-zoiko-group", "TENANT_OWNER"), "rm-tsa": ("tenant-zoiko-group", "TENANT_SECURITY_ADMIN"),
           "rm-super": (None, "SUPER_ADMIN"), "rm-certifier": (None, "CONNECTOR_CERTIFICATION_MANAGER")}


def _role_of(label: str) -> str:
    return "CCO" if label.startswith("AGENT") else label


def expected_matrix() -> dict[str, list[str]]:
    """role -> operationIds of the GET operations the declared requirements allow (computed from the registry)."""
    from app.main import ROUTERS
    from app.security import ROLE_PERMISSIONS, operations
    ops = [o for o in operations(ROUTERS) if o.method == "GET" and o.public is None]
    out = {}
    for label, actor in PRINCIPALS.items():
        agent = label.startswith("AGENT")
        platform_principal = actor in ("rm-super", "rm-certifier") or label.startswith("CONTENT") or label == "ENCODING_ENGINEER"
        perms = ROLE_PERMISSIONS[_role_of(label)]
        allowed = []
        for o in ops:
            r = o.requirement
            if agent:
                continue
            if r.self_service:
                allowed.append(o.operation_id)
                continue
            if r.platform and not platform_principal:
                continue
            if r.human and label in ("SERVICE", "EXTERNAL_AUDITOR"):
                continue
            if perms & set(r.permissions):
                allowed.append(o.operation_id)
        out[label] = sorted(allowed)
    return out


@pytest.fixture(scope="module")
def principals(client):
    from app.db import AdminSession
    from app.models import Actor
    with AdminSession() as db:
        for aid, (tenant, role) in CREATED.items():
            if db.get(Actor, aid) is None:
                add_principal(db, actor_id=aid, tenant_id=tenant, actor_type="HUMAN", display_name=aid, roles=[role])
        db.commit()
    return True


def test_read_matrix_matches_the_reviewed_policy_table():
    assert GOLDEN.exists(), "run `python tests/test_read_authorization_matrix.py` and review the generated table"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["matrix"] == expected_matrix(), "read policy changed: regenerate the table and review its diff"


def test_every_role_is_refused_or_served_exactly_as_the_registry_declares(client, principals):
    from app.main import ROUTERS
    from app.security import operations
    from test_authorization import denials
    ops = [o for o in operations(ROUTERS) if o.method == "GET" and o.public is None]
    expected = expected_matrix()
    wrong, checked = [], 0
    for label, actor in PRINCIPALS.items():
        allowed = set(expected[label])
        n_denied = denials(actor)
        refused = 0
        for o in ops:
            r = client.get(re.sub(r"\{[^}]+\}", "matrix-probe", o.path), headers=H(actor))
            checked += 1
            if o.operation_id in allowed:
                if r.status_code in (401, 403):
                    wrong.append(f"{label}: {o.operation_id} refused {r.status_code} but declared allowed")
            else:
                refused += 1
                if r.status_code != 403:
                    wrong.append(f"{label}: {o.operation_id} answered {r.status_code} but declared refused")
        if denials(actor) - n_denied != refused:
            wrong.append(f"{label}: {refused} refusals but {denials(actor) - n_denied} authorization.denied records")
    assert not wrong, wrong[:20]
    assert checked >= 60 * len(PRINCIPALS)
    # No database-unchanged check: some reads are audited by design (e.g. the Super Admin's registry read).


if __name__ == "__main__":  # regenerate the reviewed table (run from backend/)
    import os
    import sys
    import tempfile
    os.environ.update(ZA_ALLOW_SQLITE="1", ZA_SKIP_DOTENV="1", ZA_DATA_DIR=tempfile.mkdtemp())
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    m = expected_matrix()
    GOLDEN.write_text(json.dumps({
        "_status": "PROVISIONAL read policy pending G-08 (IAM Lead): records what the code enforces, not an approved policy",
        "_generated_by": "python tests/test_read_authorization_matrix.py",
        "matrix": m}, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN} ({sum(map(len, m.values()))} allowed role/operation pairs)")
