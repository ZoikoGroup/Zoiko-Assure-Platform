"""Platform/core endpoints: dictionary, identity, watermark, outbox, audit, org."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .. import codes
from ..db import CELL_ID, get_db
from ..models import (Actor, AuditEvent, Cell, LegalEntity, OutboxEvent, Subject, Tenant)
from ..platform import current_watermark, emit, to_dict
from ..security import Ctx, get_ctx

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "cell_id": CELL_ID, "dictionary_version": codes.DICTIONARY_VERSION}


@router.get("/dictionary")
def dictionary():
    return codes.dictionary_payload()


@router.get("/actors")
def actors(ctx: Ctx = Depends(get_ctx)):
    """Actor directory for the caller's tenant (content staff see content-operations staff only)."""
    db = ctx.db
    out = []
    for a in db.execute(select(Actor).where(Actor.tenant_id == ctx.actor.tenant_id if ctx.actor.tenant_id
                                            else Actor.tenant_id.is_(None))).scalars():
        d = to_dict(a)
        t = db.get(Tenant, a.tenant_id) if a.tenant_id else None
        d["tenant_name"] = t.legal_name if t else "Zoiko content/platform operations"
        out.append(d)
    return out


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)):
    d = to_dict(ctx.actor)
    d["permissions"] = sorted(ctx.perms())
    return d


@router.get("/watermark")
def watermark(db=Depends(get_db)):
    return current_watermark(db)


@router.get("/outbox")
def outbox(limit: int = 100, ctx: Ctx = Depends(get_ctx)):
    q = select(OutboxEvent).where(OutboxEvent.tenant_id.in_([ctx.tenant]) | OutboxEvent.tenant_id.is_(None))
    return [to_dict(e) for e in ctx.db.execute(q.order_by(OutboxEvent.record_seq.desc()).limit(limit)).scalars()]


@router.get("/audit")
def audit_log(limit: int = 100, ctx: Ctx = Depends(get_ctx)):
    q = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant).order_by(AuditEvent.record_seq.desc()).limit(limit)
    return [to_dict(e) for e in ctx.db.execute(q).scalars()]


# ------------------------------------------------------------------ org / tenancy (ZA-TEN-001 baseline)
@router.get("/org/tenant")
def tenant(ctx: Ctx = Depends(get_ctx)):
    t = ctx.db.get(Tenant, ctx.tenant)
    ents = ctx.db.execute(select(LegalEntity).where(LegalEntity.tenant_id == ctx.tenant)).scalars().all()
    cells = {c.cell_id: to_dict(c) for c in ctx.db.execute(select(Cell)).scalars()}
    return {"tenant": to_dict(t), "entities": [to_dict(e) for e in ents], "cells": list(cells.values())}


@router.get("/org/subjects")
def subjects(subject_type: str | None = None, ctx: Ctx = Depends(get_ctx)):
    q = select(Subject).where(Subject.tenant_id == ctx.tenant)
    if subject_type:
        q = q.where(Subject.subject_type == subject_type)
    return [to_dict(s) for s in ctx.db.execute(q.order_by(Subject.subject_type, Subject.display_name)).scalars()]


class SubjectIn(BaseModel):
    entity_id: str
    subject_type: str
    display_name: str
    parent_subject_id: str | None = None


SUBJECT_TYPES = {"BUSINESS_UNIT", "REGULATED_ACTIVITY", "PRODUCT_SERVICE", "ENVIRONMENT", "AI_ASSET", "CONTRACT_THIRD_PARTY"}


@router.post("/org/subjects")
def create_subject(body: SubjectIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_facts")
    if body.subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"subject_type must be one of {sorted(SUBJECT_TYPES)}")
    ent = ctx.scoped(ctx.db.get(LegalEntity, body.entity_id), "entity")
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
    model_config = ConfigDict(extra="forbid")
    legal_name: str
    country: str
    registration_ref: str | None = None


@router.post("/org/entities")
def create_entity(body: EntityIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_tenant")
    if not ctx.db.get(Cell, CELL_ID):  # the server's own cell must be provisioned before entities are placed in it
        raise HTTPException(409, "This cell is not provisioned")
    e = LegalEntity(tenant_id=ctx.tenant, legal_name=body.legal_name, country=body.country,
                    registration_ref=body.registration_ref, home_cell_id=CELL_ID)
    ctx.db.add(e)
    ctx.db.flush()
    s = Subject(tenant_id=ctx.tenant, entity_id=e.entity_id, subject_type="LEGAL_ENTITY", display_name=body.legal_name)
    ctx.db.add(s)
    ctx.db.flush()
    e.subject_id = s.subject_id
    emit(ctx.db, event_type="entity.registered", aggregate_type="LegalEntity", aggregate_id=e.entity_id,
         tenant_id=ctx.tenant, entity_id=e.entity_id, payload={"home_cell_id": e.home_cell_id})
    ctx.db.commit()
    return to_dict(e)
