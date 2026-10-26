"""Super Admin: the customer-organisation registry (ZA-TEN-001, ZA-IAM-001).

Only a HUMAN platform principal (tenant_id NULL) holding `manage_organisations` (role SUPER_ADMIN) may use these
endpoints. They manage organisation metadata only: legal name, status, legal entities, cell/isolation placement and
Organisation Admins (TENANT_OWNER). Nothing here reads or returns customer evidence, facts, assurance or audit content
(INV-14, IAM-REQ-030); the Super Admin role holds no tenant-data permission at all.

PostgreSQL RLS: writes stamp only the target organisation's tenant scope; the cross-organisation list is the single
documented `system_scope` read of registry metadata. Every change is audited under the affected organisation, so that
organisation's own audit trail shows what the platform did. There is no endpoint that grants SUPER_ADMIN
(WF-IAM-01: no "make super admin" button).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select

from ..accounts import ORG_ADMIN_ROLE
from ..auth import generate_password, hash_password
from ..db import CELL_ID, set_tenant_scope
from ..models import Actor, AuditEvent, AuthCredential, AuthSession, Cell, LegalEntity, Subject, Tenant, new_id
from ..platform import audit, emit
from ..security import Ctx, get_ctx, system_scope

router = APIRouter(prefix="/platform")

# ZA-TEN-001 defines no organisation lifecycle vocabulary beyond ACTIVE; SUSPENDED is the reversible deactivated state.
STATUSES = ("ACTIVE", "SUSPENDED")


def _platform_admin(ctx: Ctx) -> None:
    if ctx.actor.tenant_id is not None or ctx.actor.actor_type != "HUMAN":
        raise HTTPException(403, "Organisation management is a platform Super Admin function")
    ctx.require("manage_organisations")


def _admins(db, tenant_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in tenant_ids}
    if tenant_ids:
        for a in db.execute(select(Actor).where(Actor.tenant_id.in_(tenant_ids))).scalars():
            if ORG_ADMIN_ROLE in (a.roles or []):
                out[a.tenant_id].append({"actor_id": a.actor_id, "display_name": a.display_name, "email": a.email})
    return out


def _view(t: Tenant, entities: list[LegalEntity], admins: list[dict], cells: dict[str, Cell]) -> dict:
    return {
        "tenant_id": t.tenant_id, "legal_name": t.legal_name, "status": t.status,
        "contracting_region": t.contracting_region, "isolation_tier": t.isolation_tier, "created_at": t.created_at,
        "entities": [{"entity_id": e.entity_id, "legal_name": e.legal_name, "country": e.country,
                      "registration_ref": e.registration_ref, "home_cell_id": e.home_cell_id, "status": e.status,
                      "region": cells[e.home_cell_id].region if e.home_cell_id in cells else None} for e in entities],
        "cells": sorted({e.home_cell_id for e in entities}),
        "admins": admins,
    }


def _load(ctx: Ctx, tenant_id: str) -> Tenant:
    set_tenant_scope(ctx.db, tenant_id)  # narrow: only this organisation's registry rows are visible
    t = ctx.db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(404, "Organisation not found")
    return t


def _detail(ctx: Ctx, t: Tenant) -> dict:
    db = ctx.db
    entities = db.execute(select(LegalEntity).where(LegalEntity.tenant_id == t.tenant_id)).scalars().all()
    cells = {c.cell_id: c for c in db.execute(select(Cell)).scalars()}
    view = _view(t, entities, _admins(db, [t.tenant_id])[t.tenant_id], cells)
    # Only the platform's own administrative record of this organisation, never the customer's audit trail.
    events = db.execute(select(AuditEvent).where(AuditEvent.tenant_id == t.tenant_id,
                                                 AuditEvent.action.like("organisation.%")
                                                 | (AuditEvent.action == "account.bootstrap_created"))
                        .order_by(AuditEvent.record_seq.desc()).limit(50)).scalars()
    view["admin_events"] = [{"action": e.action, "actor_id": e.actor_id, "prior_state": e.prior_state,
                             "new_state": e.new_state, "reason": e.reason, "recorded_at": e.recorded_at,
                             "record_seq": e.record_seq} for e in events]
    return view


def _email_taken(db, email: str) -> bool:
    return db.execute(select(Actor.actor_id).where(func.lower(Actor.email) == email)).first() is not None


def _appoint_admin(ctx: Ctx, tenant_id: str, display_name: str, email: str) -> str:
    """Create a TENANT_OWNER with a one-time temporary password (hash stored, change required at first sign-in)."""
    temp = generate_password()
    admin = Actor(tenant_id=tenant_id, actor_type="HUMAN", display_name=display_name.strip(), email=email,
                  roles=[ORG_ADMIN_ROLE], authority_basis=f"Organisation Admin appointed by {ctx.actor.display_name}")
    ctx.db.add(admin)
    ctx.db.flush()
    ctx.db.add(AuthCredential(actor_id=admin.actor_id, password_hash=hash_password(temp), must_change=True))
    audit(ctx.db, action="organisation.admin_appointed", object_type="Actor", object_id=admin.actor_id, actor=ctx.actor,
          tenant_id=tenant_id, new=ORG_ADMIN_ROLE, reason=f"Organisation Admin {email}")
    return temp


class EntitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: str = Field(min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    registration_ref: str | None = Field(default=None, max_length=100)


class AdminSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=2, max_length=120)
    email: EmailStr


class OrganisationIn(BaseModel):
    # No role, cell, tier or tenant id fields: the server assigns them (TEN-REQ-005, WF-IAM-01).
    model_config = ConfigDict(extra="forbid")
    legal_name: str = Field(min_length=2, max_length=200)
    entity: EntitySpec
    admin: AdminSpec


class OrganisationEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: str = Field(min_length=2, max_length=200)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "SUSPENDED"]
    reason: str = Field(min_length=3, max_length=500)


@router.get("/organisations")
def list_organisations(ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    db = ctx.db
    with system_scope(db, "Super Admin organisation registry: org_tenant / org_legal_entity metadata only"):
        tenants = db.execute(select(Tenant).order_by(Tenant.created_at)).scalars().all()
        entities = db.execute(select(LegalEntity)).scalars().all()
    cells = {c.cell_id: c for c in db.execute(select(Cell)).scalars()}
    admins = _admins(db, [t.tenant_id for t in tenants])
    by_tenant: dict[str, list[LegalEntity]] = {}
    for e in entities:
        by_tenant.setdefault(e.tenant_id, []).append(e)
    return [_view(t, by_tenant.get(t.tenant_id, []), admins[t.tenant_id], cells) for t in tenants]


@router.get("/organisations/{tenant_id}")
def get_organisation(tenant_id: str, ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    return _detail(ctx, _load(ctx, tenant_id))


@router.post("/organisations", status_code=201)
def create_organisation(body: OrganisationIn, ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    db = ctx.db
    cell = db.get(Cell, CELL_ID)
    if cell is None:  # the server's own cell must be provisioned before an organisation is placed in it
        raise HTTPException(409, "This cell is not provisioned")
    email = body.admin.email.strip().lower()
    if _email_taken(db, email):
        raise HTTPException(409, "An account with this email already exists")
    tenant_id = new_id()
    set_tenant_scope(db, tenant_id)  # writes are confined to the new organisation's scope
    t = Tenant(tenant_id=tenant_id, legal_name=body.legal_name.strip(), contracting_region=cell.region,
               isolation_tier="POOLED", status="ACTIVE")
    db.add(t)
    db.flush()
    e = LegalEntity(tenant_id=tenant_id, legal_name=body.entity.legal_name.strip(), country=body.entity.country.upper(),
                    registration_ref=(body.entity.registration_ref or "").strip() or None, home_cell_id=CELL_ID)
    db.add(e)
    db.flush()
    s = Subject(tenant_id=tenant_id, entity_id=e.entity_id, subject_type="LEGAL_ENTITY", display_name=e.legal_name)
    db.add(s)
    db.flush()
    e.subject_id = s.subject_id
    emit(db, event_type="entity.registered", aggregate_type="LegalEntity", aggregate_id=e.entity_id,
         tenant_id=tenant_id, entity_id=e.entity_id, payload={"home_cell_id": e.home_cell_id})
    audit(db, action="organisation.created", object_type="Tenant", object_id=tenant_id, actor=ctx.actor,
          tenant_id=tenant_id, new="ACTIVE", reason=f"Organisation {t.legal_name} provisioned in {CELL_ID}")
    temp = _appoint_admin(ctx, tenant_id, body.admin.display_name, email)
    db.commit()
    out = _detail(ctx, t)
    # Shown once to the Super Admin for secure hand-over; only its hash is stored and it must be changed at first sign-in.
    out["initial_admin_password"] = temp
    return out


@router.patch("/organisations/{tenant_id}")
def edit_organisation(tenant_id: str, body: OrganisationEdit, ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    t = _load(ctx, tenant_id)
    new_name = body.legal_name.strip()
    if new_name != t.legal_name:
        audit(ctx.db, action="organisation.updated", object_type="Tenant", object_id=tenant_id, actor=ctx.actor,
              tenant_id=tenant_id, reason=f"Legal name changed from {t.legal_name!r} to {new_name!r}")
        t.legal_name = new_name
        ctx.db.commit()
    return _detail(ctx, t)


@router.post("/organisations/{tenant_id}/status")
def set_status(tenant_id: str, body: StatusIn, ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    db = ctx.db
    t = _load(ctx, tenant_id)
    if body.status == t.status:
        raise HTTPException(409, f"Organisation is already {t.status}")
    prior, t.status = t.status, body.status
    revoked = 0
    if body.status == "SUSPENDED":  # end every live session of this organisation's people now
        now = datetime.now(timezone.utc)
        ids = [a for (a,) in db.execute(select(Actor.actor_id).where(Actor.tenant_id == tenant_id))]
        for s in db.execute(select(AuthSession).where(AuthSession.actor_id.in_(ids), AuthSession.revoked_at.is_(None))).scalars():
            s.revoked_at, revoked = now, revoked + 1
    audit(db, action="organisation.status_changed", object_type="Tenant", object_id=tenant_id, actor=ctx.actor,
          tenant_id=tenant_id, prior=prior, new=body.status,
          reason=body.reason.strip() + (f" ({revoked} session(s) revoked)" if revoked else ""))
    db.commit()
    return _detail(ctx, t)


@router.post("/organisations/{tenant_id}/admins", status_code=201)
def appoint_admin(tenant_id: str, body: AdminSpec, ctx: Ctx = Depends(get_ctx)):
    _platform_admin(ctx)
    t = _load(ctx, tenant_id)
    email = body.email.strip().lower()
    if _email_taken(ctx.db, email):
        raise HTTPException(409, "An account with this email already exists")
    temp = _appoint_admin(ctx, tenant_id, body.display_name, email)
    ctx.db.commit()
    out = _detail(ctx, t)
    out["initial_admin_password"] = temp
    return out
