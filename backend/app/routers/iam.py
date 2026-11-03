"""Scoped role registry API (ZA-IAM-001 §08, §20, §22, §23; roadmap Stage 4b).

Organisation-only: every route works inside the caller's own tenant, and a principal, assignment or entity of another
organisation answers 404 exactly like one that does not exist. Role management needs TENANT_SECURITY_ADMIN's
``manage_role_assignments`` (or, to appoint a TENANT_SECURITY_ADMIN, TENANT_OWNER's ``delegate_admin``); the per-role rules,
self-assignment refusal and history live in app/iam.py. Zoiko platform principals (Super Admin included) hold neither
permission, so no platform role can reach an organisation's role registry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .. import iam
from ..models import Actor, RoleAssignment, RoleAssignmentEvent
from ..platform import to_dict
from ..security import ROLE_PERMISSIONS, Ctx, authorize

router = APIRouter()

# TENANT_SECURITY_ADMIN manages roles; TENANT_OWNER delegates admin (appoints TENANT_SECURITY_ADMIN). The precise
# per-role rule is app/iam.py ASSIGNABILITY.
ROLE_ADMIN = ("manage_role_assignments", "delegate_admin")


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
    """The organisation-manageable role templates, their permissions and who may assign them."""
    out = []
    for code in sorted(set(iam.ASSIGNABILITY) | {"TENANT_OWNER"}):
        rule = iam.ASSIGNABILITY.get(code)
        out.append({"role_code": code, "permissions": sorted(ROLE_PERMISSIONS.get(code, set())),
                    "assignable": bool(rule and rule.assignable),
                    "assigned_by": f"permission {rule.by_permission}" if rule else "Zoiko Super Admin appointment",
                    "principal_types": sorted(rule.principal_types) if rule else ["HUMAN"],
                    "scope_types": sorted(rule.scope_types) if rule else ["TENANT"]})
    return {"policy_version": iam.AUTHZ_POLICY_VERSION, "roles": out}


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
