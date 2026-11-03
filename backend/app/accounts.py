"""One-time bootstrap of the initial Super Admin and Organisation Admin accounts (ZA-IAM-001).

Credentials come only from the backend environment (the git-ignored backend/.env or real environment variables):

    ZA_BOOTSTRAP_SUPER_ADMIN_EMAIL / _PASSWORD / _NAME     platform Super Admin (tenant-less, role SUPER_ADMIN)
    ZA_BOOTSTRAP_ORG_ADMIN_EMAIL   / _PASSWORD / _NAME     Organisation Admin (role TENANT_OWNER) of
    ZA_BOOTSTRAP_ORG_TENANT_ID                             the initial customer organisation (must already exist)

Repeat-safe: an account is created only when no actor with that email exists. An existing account is never modified,
its password is never reset, and nothing is recreated on later start-ups, so the environment values only matter on
first creation. Passwords are stored as scrypt hashes only and are never logged. A weak password or an unknown
organisation creates nothing (fail closed).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth import hash_password, password_policy_ok
from .db import IS_POSTGRES
from .iam import provision_roles
from .models import Actor, AuthCredential, Tenant
from .platform import audit

log = logging.getLogger(__name__)

SUPER_ADMIN_ROLE = "SUPER_ADMIN"
ORG_ADMIN_ROLE = "TENANT_OWNER"


def _create(db: Session, *, email: str, password: str, name: str, role: str, tenant_id: str | None,
            authority_basis: str) -> str:
    email = email.strip().lower()
    if db.execute(select(Actor).where(func.lower(Actor.email) == email)).scalars().first():
        return f"{role} {email}: already exists, left unchanged"
    if not password_policy_ok(password):
        log.error("Bootstrap %s %s skipped: the configured password does not meet the password policy", role, email)
        return f"{role} {email}: skipped (password does not meet policy)"
    actor = Actor(tenant_id=tenant_id, actor_type="HUMAN", display_name=name, email=email, roles=[],
                  authority_basis=authority_basis)
    db.add(actor)
    db.flush()
    db.add(AuthCredential(actor_id=actor.actor_id, password_hash=hash_password(password), must_change=False))
    audit(db, action="account.bootstrap_created", object_type="Actor", object_id=actor.actor_id, tenant_id=tenant_id,
          new=role, reason="Created once from the backend environment at start-up")
    # Its authority is a recorded, scoped assignment in the role registry (ZA-IAM-001 §22; Stage 4b).
    provision_roles(db, actor, [role], source="BOOTSTRAP", reason="One-time account bootstrap from the backend environment")
    return f"{role} {email}: created"


def bootstrap_accounts(db: Session, env: Mapping[str, str] = os.environ) -> list[str]:
    """Create the configured bootstrap accounts that do not exist yet. Returns one summary line per account."""
    done: list[str] = []
    if IS_POSTGRES:  # one bootstrapper at a time across workers
        db.execute(text("SELECT pg_advisory_xact_lock(724502)"))
    sa_email, sa_pw = env.get("ZA_BOOTSTRAP_SUPER_ADMIN_EMAIL", "").strip(), env.get("ZA_BOOTSTRAP_SUPER_ADMIN_PASSWORD", "")
    if sa_email and sa_pw:
        done.append(_create(db, email=sa_email, password=sa_pw, name=env.get("ZA_BOOTSTRAP_SUPER_ADMIN_NAME") or "Super Admin",
                            role=SUPER_ADMIN_ROLE, tenant_id=None,
                            authority_basis="Platform Super Admin bootstrapped from the backend environment"))
    oa_email, oa_pw = env.get("ZA_BOOTSTRAP_ORG_ADMIN_EMAIL", "").strip(), env.get("ZA_BOOTSTRAP_ORG_ADMIN_PASSWORD", "")
    tenant_id = env.get("ZA_BOOTSTRAP_ORG_TENANT_ID", "").strip()
    if oa_email and oa_pw:
        if not tenant_id or db.get(Tenant, tenant_id) is None:
            log.warning("Bootstrap Organisation Admin skipped: ZA_BOOTSTRAP_ORG_TENANT_ID does not name an existing organisation")
            done.append(f"{ORG_ADMIN_ROLE} {oa_email.lower()}: skipped (organisation not found)")
        else:
            done.append(_create(db, email=oa_email, password=oa_pw, name=env.get("ZA_BOOTSTRAP_ORG_ADMIN_NAME") or "Organisation Admin",
                                role=ORG_ADMIN_ROLE, tenant_id=tenant_id,
                                authority_basis="Initial Organisation Admin bootstrapped from the backend environment"))
    db.commit()
    for line in done:
        log.info("Account bootstrap: %s", line)
    return done
