"""Scoped role registry API (ZA-IAM-001 §08, §20, §22, §23; roadmap Stage 4b).

Organisation-only: every route works inside the caller's own tenant, and a principal, assignment or entity of another
organisation answers 404 exactly like one that does not exist. Role management needs TENANT_SECURITY_ADMIN's
``manage_role_assignments`` (or, to appoint a TENANT_SECURITY_ADMIN, TENANT_OWNER's ``delegate_admin``); the per-role rules,
self-assignment refusal and history live in app/iam.py. Zoiko platform principals (Super Admin included) hold neither
permission, so no platform role can reach an organisation's role registry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .. import iam
from ..db import capability
from ..models import Actor, AuthSession, RoleAssignment, RoleAssignmentEvent
from ..platform import audit, ensure_utc, to_dict
from ..security import ROLE_PERMISSIONS, Ctx, Requirement, authenticated, authorize

router = APIRouter()

# TENANT_SECURITY_ADMIN manages roles; TENANT_OWNER delegates admin (appoints TENANT_SECURITY_ADMIN). The precise
# per-role rule is app/iam.py ASSIGNABILITY.
ROLE_ADMIN = ("manage_role_assignments", "delegate_admin")
# ZA-IAM-001 §23 POST /sessions/{id}/revoke: "Self or authorized security admin". §08 gives security administration to
# TENANT_SECURITY_ADMIN (manage_role_assignments); TENANT_OWNER's delegate_admin is not session administration.
SESSION_ADMIN = ("manage_role_assignments",)


class AssignIn(BaseModel):
    # Server-owned fields (tenant, grantor, source, status, record time) are not accepted (API-REQ-014).
    model_config = ConfigDict(extra="forbid")
    principal_id: str = Field(min_length=1, max_length=36)
    role_code: str = Field(min_length=1, max_length=64)
    scope_type: Literal["TENANT", "LEGAL_ENTITY"] = "TENANT"
    scope_id: str | None = Field(default=None, max_length=36)  # the legal entity for LEGAL_ENTITY scope
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


@router.get("/roles")
def role_catalogue(ctx: Ctx = Depends(authorize(*ROLE_ADMIN))):
    """The organisation-manageable role templates, their permissions and who may assign them, and the scopes they can
    be assigned at in this organisation: the organisation itself, or one of its legal entities (IAM-REQ-010; §08
    "every assignment includes an explicit scope"). Entity identity only (ID, name, country), so a role administrator
    can scope an assignment without a business read of the organisation record."""
    from ..models import LegalEntity
    entities = [{"entity_id": e.entity_id, "legal_name": e.legal_name, "country": e.country}
                for e in ctx.db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant)
                                        .order_by(LegalEntity.legal_name, LegalEntity.entity_id)).scalars()]
    out = []
    for code in sorted(set(iam.ASSIGNABILITY) | {"TENANT_OWNER"}):
        rule = iam.ASSIGNABILITY.get(code)
        out.append({"role_code": code, "permissions": sorted(ROLE_PERMISSIONS.get(code, set())),
                    "assignable": bool(rule and rule.assignable),
                    "assigned_by": f"permission {rule.by_permission}" if rule else "Zoiko Super Admin appointment",
                    # Machine-readable form of `assigned_by` (null: not assignable through this registry).
                    "assigned_by_permission": rule.by_permission if rule else None,
                    "principal_types": sorted(rule.principal_types) if rule else ["HUMAN"],
                    "scope_types": sorted(rule.scope_types) if rule else ["TENANT"]})
    return {"policy_version": iam.AUTHZ_POLICY_VERSION, "roles": out, "tenant_id": ctx.tenant, "entities": entities}


@router.get("/role-assignments")
def list_role_assignments(principal_id: str | None = None, include_closed: bool = False,
                          ctx: Ctx = Depends(authorize(*ROLE_ADMIN))):
    return [iam.assignment_view(a) for a in iam.assignments_of_tenant(ctx.db, ctx.tenant, principal_id, include_closed)]


@router.get("/role-assignments/{assignment_id}/events")
def role_assignment_events(assignment_id: str, ctx: Ctx = Depends(authorize(*ROLE_ADMIN))):
    ctx.scoped(ctx.db.get(RoleAssignment, assignment_id), "role assignment")
    return [to_dict(e) for e in ctx.db.execute(
        select(RoleAssignmentEvent).where(RoleAssignmentEvent.tenant_id == ctx.tenant,
                                          RoleAssignmentEvent.assignment_id == assignment_id)
        .order_by(RoleAssignmentEvent.record_seq, RoleAssignmentEvent.event_id)).scalars()]


@router.get("/principals/{principal_id}/effective-access")
def effective_access(principal_id: str, ctx: Ctx = Depends(authorize(*ROLE_ADMIN))):
    """ZA-IAM-001 §23: roles, scopes and the permissions they confer now; no credential material."""
    p = ctx.db.get(Actor, principal_id)
    if p is None or p.tenant_id != ctx.tenant:
        raise HTTPException(404, "principal not found")
    return iam.effective_access(ctx.db, p)


@router.post("/role-assignments", status_code=201)
def assign(body: AssignIn, ctx: Ctx = Depends(authorize(*ROLE_ADMIN, human=True))):
    a = iam.assign_role(ctx, principal_id=body.principal_id, role_code=body.role_code, scope_type=body.scope_type,
                        scope_id=body.scope_id, valid_from=body.valid_from, valid_to=body.valid_to, reason=body.reason)
    ctx.db.commit()
    return iam.assignment_view(a)


@router.post("/role-assignments/{assignment_id}/revoke")
def revoke(assignment_id: str, body: RevokeIn, ctx: Ctx = Depends(authorize(*ROLE_ADMIN, human=True))):
    a = iam.revoke_role(ctx, assignment_id, body.reason)
    ctx.db.commit()
    return iam.assignment_view(a)


# ---------------------------------------------------------------- sessions (ZA-IAM-001 §05, §23; WF-IAM-01)
# Session rows are reachable only under the signed `auth` capability, which sees every session in the cell (migration
# 0007 `auth_access`). Every handler here therefore decides the tenant boundary itself, from the principal's own row
# read outside that capability, before it touches a session.
def _iso(v: datetime) -> str:
    return ensure_utc(v).isoformat().replace("+00:00", "Z")


def _session_view(s: AuthSession, current: str | None) -> dict:
    # Never the token hash: the identifier names the session, it is not a credential (IAM-REQ-043).
    return {"session_id": s.session_id, "principal_id": s.actor_id, "created_at": _iso(s.created_at),
            "expires_at": _iso(s.expires_at), "user_agent": s.user_agent, "current": s.session_id == current}


def _live_sessions(db, principal_id: str) -> list[AuthSession]:
    """The principal's sessions that are neither revoked nor expired, newest first (expiry is filtered in the query)."""
    with capability(db, "auth"):
        return db.execute(select(AuthSession).where(AuthSession.actor_id == principal_id,
                                                    AuthSession.revoked_at.is_(None),
                                                    AuthSession.expires_at > datetime.now(timezone.utc))
                          .order_by(AuthSession.created_at.desc(), AuthSession.session_id)).scalars().all()


@router.get("/sessions")
def my_sessions(ctx: Ctx = Depends(authenticated())):
    """The caller's own live sessions; `current` marks the one this request was made with."""
    current = ctx.db.info.get("auth_session_id")
    return [_session_view(s, current) for s in _live_sessions(ctx.db, ctx.actor.actor_id)]


@router.get("/principals/{principal_id}/sessions")
def principal_sessions(principal_id: str, ctx: Ctx = Depends(authorize(*SESSION_ADMIN))):
    """WF-IAM-01 principal drawer "active sessions": a principal of the caller's organisation only (404 otherwise)."""
    p = ctx.db.get(Actor, principal_id)
    if p is None or p.tenant_id != ctx.tenant:
        raise HTTPException(404, "principal not found")
    current = ctx.db.info.get("auth_session_id")
    return [_session_view(s, current) for s in _live_sessions(ctx.db, p.actor_id)]


class SessionRevokeIn(BaseModel):
    # Optional in the schema so that authorization is decided before validation (an unauthorised caller gets 403
    # whatever it sends); the security-admin path then requires it.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: str, body: SessionRevokeIn, ctx: Ctx = Depends(authenticated())):
    """ZA-IAM-001 §23: revoke a session. Allowed for the session's own principal, or for a TENANT_SECURITY_ADMIN of the
    same organisation (reason required). The revocation takes effect at the session's next request and before any of
    its in-flight writes commit (app/security.py `_revalidate_session`; IAM-REQ-015). Audited as auth.session_revoked.
    The session making this request is ended with POST /auth/logout instead."""
    db = ctx.db
    with capability(db, "auth"):
        s = db.get(AuthSession, session_id, with_for_update=True)
        owner_id = s.actor_id if s is not None else None
    own = owner_id == ctx.actor.actor_id
    if not own:
        # Permission before existence: anyone else is refused (and the refusal recorded) whether or not it exists.
        ctx.check(Requirement(SESSION_ADMIN, human=True))
        owner = db.get(Actor, owner_id) if owner_id else None  # outside the auth capability: tenant-scoped read
        if owner is None or owner.tenant_id is None or owner.tenant_id != ctx.actor.tenant_id:
            raise HTTPException(404, "session not found")
        if not (body.reason and len(body.reason) >= 3):
            raise HTTPException(422, "A reason of at least 3 characters is required to revoke another person's session.")
        db.info["authority"] = ctx.authority  # re-checked before commit (IAM-REQ-014)
    else:
        owner = ctx.actor
    if session_id == db.info.get("auth_session_id"):
        raise HTTPException(409, "This is the session you are using; sign out to end it.")
    now = datetime.now(timezone.utc)
    if s.revoked_at is not None or ensure_utc(s.expires_at) <= now:
        raise HTTPException(409, "This session has already ended.")
    with capability(db, "auth"):
        s.revoked_at = now
        db.flush()
    who = "its own principal" if own else f"security administrator {ctx.actor.actor_id}"
    audit(db, action="auth.session_revoked", object_type="AuthSession", object_id=s.session_id, actor=ctx.actor,
          tenant_id=owner.tenant_id, prior="ACTIVE", new="REVOKED",
          reason=(f"Session of {owner.actor_id} revoked by {who}"
                  + (f"; authority {','.join(ctx.authority)}" if not own else "")
                  + (f"; {body.reason}" if body.reason else ""))[:2000])
    db.commit()
    return _session_view(s, None)
