"""Request context, tenant scoping and authorization (ZA-IAM-001 baseline, DAT-REQ-005).

Identity: a signed session token (``Authorization: Bearer``) issued by ``app/auth.py``.
The ``X-Actor-Id`` development header is accepted ONLY when ``ZA_DEV_IDENTITY_HEADER=1``
(used by the test-suite and for agent/service simulation); it is off by default. Every tenant-scoped query MUST go through
``Ctx.tenant`` so cross-tenant reads are impossible from the application layer;
PostgreSQL RLS (db/postgres_rls.sql) enforces the same predicate below it.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db, set_system_scope, set_tenant_scope
from .models import Actor, AuthCredential

# Role catalogue -> permitted actions (separation of duties; role names are not authority by themselves)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "CCO": {"read", "attest", "approve_exception", "request_evaluation", "manage_facts", "challenge", "package"},
    "LEGAL_REVIEWER": {"read", "attest", "challenge", "request_evaluation", "legal_disposition"},
    "REGULATORY_ANALYST": {"read", "manage_facts", "request_evaluation", "challenge"},
    "CONTROL_OWNER": {"read", "submit_evidence", "manage_controls", "manage_issues"},
    "INTERNAL_AUDIT": {"read", "package", "verify"},
    "AI_GOVERNANCE_LEAD": {"read", "manage_ai", "manage_facts", "request_evaluation", "grant_authority"},
    "PLATFORM_ADMIN": {"read", "manage_connectors", "manage_tenant", "checkpoint"},
    # Organisation Admin = ZA-IAM-001 §08 role template TENANT_OWNER: organisation administration within its own tenant;
    # no attest/approve rights, so it cannot bypass SoD or attest legal determinations.
    "TENANT_OWNER": {"read", "manage_tenant"},
    "RISK_APPROVER": {"read", "approve_exception", "approve_compensation"},
    "EXTERNAL_AUDITOR": {"read", "verify"},
    # Zoiko content operations (tenant_id NULL): no standing customer-evidence access (INV-14)
    "CONTENT_ANALYST": {"content_author"},
    "ENCODING_ENGINEER": {"content_author", "content_peer_review"},
    "CONTENT_LEGAL_REVIEWER": {"content_legal_review"},
    "CONTENT_RELEASE_MANAGER": {"content_release"},
    # Zoiko platform Super Admin (tenant_id NULL): the customer-organisation registry only. No tenant-data permission,
    # so evidence/facts/assurance stay out of reach (INV-14, IAM-REQ-030). Created only by the one-time .env bootstrap
    # (app/accounts.py); no API grants it (WF-IAM-01: no "make super admin" button).
    "SUPER_ADMIN": {"manage_organisations"},
    "SERVICE": {"read", "submit_evidence", "request_evaluation", "manage_facts", "checkpoint"},
}


@dataclass
class Ctx:
    db: Session
    actor: Actor

    @property
    def tenant(self) -> str:
        if not self.actor.tenant_id:
            raise HTTPException(403, "Content/platform staff have no standing tenant data access (INV-14).")
        return self.actor.tenant_id

    def perms(self) -> set[str]:
        out: set[str] = set()
        for r in self.actor.roles or []:
            out |= ROLE_PERMISSIONS.get(r, set())
        return out

    def require(self, perm: str) -> None:
        if perm not in self.perms():
            raise HTTPException(403, f"Actor {self.actor.display_name} lacks permission '{perm}'.")

    def require_human(self, what: str) -> None:
        """INV-01: AI/agent/service actors can never be the accountable authorizer."""
        if self.actor.actor_type != "HUMAN":
            raise HTTPException(403, f"{what} requires a named HUMAN actor (INV-01); got {self.actor.actor_type}.")

    def actor_ref(self) -> dict:
        return {"actor_id": self.actor.actor_id, "actor_type": self.actor.actor_type, "name": self.actor.display_name}

    def scoped(self, obj, label: str = "object"):
        if obj is None or getattr(obj, "tenant_id", None) != self.tenant:
            raise HTTPException(404, f"{label} not found")
        return obj


DEV_HEADER = os.environ.get("ZA_DEV_IDENTITY_HEADER", "0") == "1"


def get_ctx(authorization: str | None = Header(default=None), x_actor_id: str | None = Header(default=None),
            db: Session = Depends(get_db)) -> Ctx:
    from .auth import bearer, resolve_token, tenant_active
    tok = bearer(authorization)
    if tok:
        actor, _ = resolve_token(db, tok)  # also refuses sessions of a suspended organisation
    elif x_actor_id and DEV_HEADER:
        actor = db.get(Actor, x_actor_id)
        if actor is None:
            raise HTTPException(401, "Unknown actor")
    else:
        raise HTTPException(401, "Sign in required")
    set_tenant_scope(db, actor.tenant_id)  # PostgreSQL RLS: this request sees only its tenant's rows
    if not tenant_active(db, actor):
        raise HTTPException(403, "This organisation is suspended; access is disabled.")
    if tok:
        cred = db.get(AuthCredential, actor.actor_id)
        if cred is not None and cred.must_change:
            raise HTTPException(403, {"error": "PASSWORD_CHANGE_REQUIRED",
                                      "message": "Change your temporary password before continuing."})
    return Ctx(db=db, actor=actor)


@contextmanager
def system_scope(db: Session, reason: str):
    """Documented cell-wide platform job (bypasses tenant RLS for the enclosed work only).

    Allowed uses: rule-release impact re-evaluation, shadow impact (aggregate output only),
    cell ledger checkpoints, and the Super Admin organisation-registry read (org_tenant / org_legal_entity
    metadata only, app/routers/platform.py). Never wrap request handlers that return tenant data to a caller.
    """
    set_system_scope(db, True)
    try:
        yield
    finally:
        set_system_scope(db, False)
