"""Platform/core endpoints: dictionary, identity, watermark, outbox, audit, org."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select

from .. import codes, pagination
from ..db import CELL_ID
from ..models import (Actor, AuditEvent, Cell, LegalEntity, OutboxEvent, RoleAssignment, Subject, Tenant)
from ..platform import audit, current_watermark, emit, ensure_utc, to_dict
from ..security import CONTENT_READERS, DOSSIER_READ, PLATFORM_READERS, READ, Ctx, authenticated, authorize

router = APIRouter()

# ZA-IAM-001 §08/§25 WF-IAM-01: the Identity & Access Directory belongs to the organisation's role administrators
# (TENANT_SECURITY_ADMIN manage_role_assignments; TENANT_OWNER delegate_admin), who hold no business read.
ROLE_ADMIN = ("manage_role_assignments", "delegate_admin")
PRINCIPAL_TYPES = Literal["HUMAN", "EXTERNAL_AUDITOR", "AGENT", "SERVICE"]


@router.get("/health")
def health():
    return {"status": "ok", "cell_id": CELL_ID, "dictionary_version": codes.DICTIONARY_VERSION}


@router.get("/dictionary")
def dictionary():
    return codes.dictionary_payload()


@router.get("/actors")
def actors(actor_type: PRINCIPAL_TYPES | None = None,
           role: str | None = Query(None, min_length=2, max_length=64, pattern=r"^[A-Z][A-Z_]*$"),
           q: str | None = Query(None, min_length=1, max_length=100),
           ctx: Ctx = Depends(authorize(*READ, *CONTENT_READERS, *PLATFORM_READERS, *ROLE_ADMIN))):
    """Actor directory for the caller's tenant (content staff see content-operations staff only).

    Allowlisted, typed filters (API-REQ-023; WF-IAM-01 "Filters: principal type, ... role"): `actor_type`, `role` (a
    role assignment in force now, at any scope, read from the role registry rather than the org_actor.roles cache) and
    `q` (case-insensitive name or email substring). Without filters the response is unchanged."""
    db = ctx.db
    query = select(Actor).where(Actor.tenant_id == ctx.actor.tenant_id if ctx.actor.tenant_id
                                else Actor.tenant_id.is_(None))
    if actor_type:
        query = query.where(Actor.actor_type == actor_type)
    if q and q.strip():
        needle = q.strip().lower()
        query = query.where(or_(func.lower(Actor.display_name).contains(needle, autoescape=True),
                                func.lower(Actor.email).contains(needle, autoescape=True)))
    holders: set[str] | None = None
    if role:
        from ..iam import is_current
        holders = {a.principal_id for a in db.execute(select(RoleAssignment).where(
            RoleAssignment.tenant_id == ctx.actor.tenant_id if ctx.actor.tenant_id else RoleAssignment.tenant_id.is_(None),
            RoleAssignment.role_code == role, RoleAssignment.status == "ACTIVE")).scalars() if is_current(a)}
    out = []
    for a in db.execute(query).scalars():
        if holders is not None and a.actor_id not in holders:
            continue
        d = to_dict(a)
        t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
        d["tenant_name"] = t.legal_name if t else "Zoiko content/platform operations"
        out.append(d)
    return out


@router.get("/me")
def me(ctx: Ctx = Depends(authenticated())):
    from ..iam import assignment_view, wide_roles
    d = to_dict(ctx.actor)
    d["roles"] = wide_roles(ctx.grants())  # from the role registry, not the org_actor.roles cache
    d["permissions"] = sorted(ctx.perms())
    d["role_assignments"] = [assignment_view(a) for a in ctx.grants()]
    return d


@router.get("/watermark")
def watermark(ctx: Ctx = Depends(authorize(*DOSSIER_READ, *CONTENT_READERS))):
    # Finding I-10: the cell's record-sequence counter is an activity-volume signal; signed-in readers only.
    return current_watermark(ctx.db)


@router.get("/outbox")
def outbox(limit: int = Query(100, ge=1), ctx: Ctx = Depends(authorize(*READ))):
    # The caller's organisation only. Cell-wide platform events (tenant_id NULL), including SECURITY_CRITICAL ones such
    # as security.content_signature_failed, are not customer-visible (ZA-EVT-001 §19); RLS enforces the same (0005).
    q = select(OutboxEvent).where(OutboxEvent.tenant_id == ctx.tenant)
    return [to_dict(e) for e in ctx.db.execute(q.order_by(OutboxEvent.record_seq.desc()).limit(limit)).scalars()]


@router.get("/audit")
def audit_log(limit: int = Query(100, ge=1), ctx: Ctx = Depends(authorize(*READ))):
    """The organisation's latest audit records as a plain list, unchanged for existing consumers (no upper bound is
    imposed here; a non-positive limit is refused). Paginated, filterable reading is GET /audit-events."""
    q = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant).order_by(AuditEvent.record_seq.desc()).limit(limit)
    return [to_dict(e) for e in ctx.db.execute(q).scalars()]


AUDIT_PAGE_DEFAULT, AUDIT_PAGE_MAX = 50, 200


@router.get("/audit-events")
def audit_events(limit: int = Query(AUDIT_PAGE_DEFAULT, ge=1, le=AUDIT_PAGE_MAX),
                 cursor: str | None = Query(None, min_length=1, max_length=2048),
                 action: str | None = Query(None, min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.]*$"),
                 category: str | None = Query(None, min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$"),
                 actor_id: str | None = Query(None, min_length=1, max_length=36),
                 object_type: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$"),
                 object_id: str | None = Query(None, min_length=1, max_length=64),
                 recorded_from: datetime | None = None, recorded_to: datetime | None = None,
                 ctx: Ctx = Depends(authorize(*READ))):
    """The organisation's audit trail, newest first, cursor-paginated (ZA-API-001 §04/§11, API-REQ-012/023).

    Stable total order (record_seq DESC, id DESC). The record watermark is pinned on the first page, so later pages
    never include records committed after the listing started. Filters are allowlisted and typed: an exact `action`, an
    action `category` (the part before the first dot, e.g. ``role`` or ``authorization``), `actor_id`, `object_type`,
    `object_id`, and a half-open record-time window [`recorded_from`, `recorded_to`). The tenant comes from the
    authenticated principal only (API-REQ-008); platform rows (no tenant) are never included."""
    if recorded_from and recorded_to and ensure_utc(recorded_to) <= ensure_utc(recorded_from):
        raise HTTPException(422, "recorded_to must be later than recorded_from.")
    filters = {"action": action, "category": category, "actor_id": actor_id, "object_type": object_type,
               "object_id": object_id, "recorded_from": recorded_from and ensure_utc(recorded_from).isoformat(),
               "recorded_to": recorded_to and ensure_utc(recorded_to).isoformat(), "sort": "record_seq:desc,id:desc",
               "limit": limit}
    fp, scope = pagination.query_fingerprint(filters), f"audit-events:{ctx.tenant}"
    db = ctx.db
    q = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant)
    if action:
        q = q.where(AuditEvent.action == action)
    if category:
        q = q.where(AuditEvent.action.startswith(f"{category}.", autoescape=True))
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    if object_type:
        q = q.where(AuditEvent.object_type == object_type)
    if object_id:
        q = q.where(AuditEvent.object_id == object_id)
    if recorded_from:
        q = q.where(AuditEvent.recorded_at >= ensure_utc(recorded_from))
    if recorded_to:
        q = q.where(AuditEvent.recorded_at < ensure_utc(recorded_to))
    if cursor:
        c = pagination.decode_cursor(cursor, scope=scope, query=fp)
        pinned = c["watermark"]
        try:
            seq, last_id = int(c["position"][0]), str(c["position"][1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, {"error": "invalid_cursor", "message": "The cursor is not valid."}) from None
        q = q.where(or_(AuditEvent.record_seq < seq, and_(AuditEvent.record_seq == seq, AuditEvent.id < last_id)))
    else:
        pinned = current_watermark(db)["record_seq"]
    q = q.where(AuditEvent.record_seq <= pinned)
    rows = db.execute(q.order_by(AuditEvent.record_seq.desc(), AuditEvent.id.desc()).limit(limit + 1)).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = (pagination.encode_cursor(scope=scope, query=fp, position=[rows[-1].record_seq, rows[-1].id],
                                    watermark=pinned) if more else None)
    return pagination.page([to_dict(e) for e in rows], next_cursor=nxt, limit=limit,
                           watermark={"cell_id": CELL_ID, "record_seq": pinned})


# ------------------------------------------------------------------ org / tenancy (ZA-TEN-001 baseline)
@router.get("/org/tenant")
def tenant(ctx: Ctx = Depends(authorize(*READ))):
    t = ctx.db.get(Tenant, ctx.tenant)
    ents = ctx.db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant)).scalars().all()
    cells = {c.cell_id: to_dict(c) for c in ctx.db.execute(select(Cell)).scalars()}
    return {"tenant": to_dict(t), "entities": [to_dict(e) for e in ents], "cells": list(cells.values())}


@router.get("/org/subjects")
def subjects(subject_type: str | None = None, ctx: Ctx = Depends(authorize(*READ))):
    q = select(Subject).where(Subject.tenant_id == ctx.tenant)
    if subject_type:
        q = q.where(Subject.subject_type == subject_type)
    return [to_dict(s) for s in ctx.db.execute(q.order_by(Subject.subject_type, Subject.display_name)).scalars()]


class SubjectIn(BaseModel):
    # API-REQ-014: unknown fields are refused. API-REQ-033: field lengths are the column sizes.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    entity_id: str = Field(min_length=1, max_length=36)
    subject_type: str = Field(min_length=1, max_length=40)
    display_name: str = Field(min_length=2, max_length=200)
    parent_subject_id: str | None = Field(default=None, max_length=36)


SUBJECT_TYPES = {"BUSINESS_UNIT", "REGULATED_ACTIVITY", "PRODUCT_SERVICE", "ENVIRONMENT", "AI_ASSET", "CONTRACT_THIRD_PARTY"}


@router.post("/org/subjects")
def create_subject(body: SubjectIn, ctx: Ctx = Depends(authorize("manage_facts"))):
    if body.subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"subject_type must be one of {sorted(SUBJECT_TYPES)}")
    ent = ctx.scoped(ctx.db.get(LegalEntity, body.entity_id), "entity")
    if body.parent_subject_id:  # a client-supplied parent must be one of this organisation's subjects
        ctx.scoped(ctx.db.get(Subject, body.parent_subject_id), "parent subject")
    s = Subject(tenant_id=ctx.tenant, entity_id=ent.entity_id, subject_type=body.subject_type,
                display_name=body.display_name, parent_subject_id=body.parent_subject_id or ent.subject_id)
    ctx.db.add(s)
    ctx.db.flush()
    emit(ctx.db, event_type="subject.registered", aggregate_type="Subject", aggregate_id=s.subject_id,
         tenant_id=ctx.tenant, entity_id=ent.entity_id, payload={"subject_type": s.subject_type})
    ctx.db.commit()
    return to_dict(s)


class EntityIn(BaseModel):
    # Clients never choose the authoritative cell (TEN-REQ-005, API-REQ-009): a home_cell_id field is rejected.
    # API-REQ-033: field lengths are the column sizes. The country is ISO 3166-1 alpha-2, as the Super Admin's
    # organisation registration already requires (app/routers/platform.py EntitySpec).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    legal_name: str = Field(min_length=2, max_length=200)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    registration_ref: str | None = Field(default=None, max_length=100)


@router.post("/org/entities")
def create_entity(body: EntityIn, ctx: Ctx = Depends(authorize("manage_tenant"))):
    if not ctx.db.get(Cell, CELL_ID):  # the server's own cell must be provisioned before entities are placed in it
        raise HTTPException(409, "This cell is not provisioned")
    e = LegalEntity(tenant_id=ctx.tenant, legal_name=body.legal_name, country=body.country.upper(),
                    registration_ref=body.registration_ref or None, home_cell_id=CELL_ID, status="ACTIVE")
    ctx.db.add(e)
    ctx.db.flush()
    s = Subject(tenant_id=ctx.tenant, entity_id=e.entity_id, subject_type="LEGAL_ENTITY", display_name=body.legal_name)
    ctx.db.add(s)
    ctx.db.flush()
    e.subject_id = s.subject_id
    emit(ctx.db, event_type="entity.registered", aggregate_type="LegalEntity", aggregate_id=e.entity_id,
         tenant_id=ctx.tenant, entity_id=e.entity_id, payload={"home_cell_id": e.home_cell_id})
    # A change to the organisation's structure by an organisation administrator (IAM-REQ-037), recorded in its own
    # audit trail like the Super Admin's registration of the first entity (organisation.created).
    audit(ctx.db, action="entity.registered", object_type="LegalEntity", object_id=e.entity_id, actor=ctx.actor,
          tenant_id=ctx.tenant, new="ACTIVE",
          reason=(f"Legal entity {e.legal_name} ({e.country}) registered in {CELL_ID}; "
                  f"authority {','.join(ctx.authority) or '-'}")[:2000])
    ctx.db.commit()
    return to_dict(e)
