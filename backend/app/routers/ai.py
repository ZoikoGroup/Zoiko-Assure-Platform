"""AI asset registry, authority grants and deterministic policy decision point (ZA-ARCH-001 §12).

No model call sits on the decision path (INV-02). Enforcement stays in the customer
runtime; Assure returns the decision + legal-obligation context + an evidence receipt.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..engines import keys
from ..models import (AIAssetVersion, ApplicabilityEvaluation, AuthorityGrant, ObligationDefinition, ObligationInstance,
                      PolicyDecisionReceipt, Subject)
from ..platform import canonical_json, digest, emit, ensure_utc, to_dict
from ..security import Ctx, get_ctx

router = APIRouter(prefix="/ai")
POLICY_VERSION = "za-agent-pdp:1.0"
AUTHORITY = ["A0", "A1", "A2", "A3", "A4"]  # OBSERVE, RECOMMEND, PREPARE, EXECUTE_REVERSIBLE, MATERIAL (excluded)


@router.get("/assets")
def assets(ctx: Ctx = Depends(get_ctx)):
    out = []
    for s in ctx.db.execute(select(Subject).where(Subject.tenant_id == ctx.tenant, Subject.subject_type == "AI_ASSET")).scalars():
        d = to_dict(s)
        d["versions"] = [to_dict(v) for v in ctx.db.execute(select(AIAssetVersion).where(AIAssetVersion.asset_subject_id == s.subject_id)
                                                            .order_by(AIAssetVersion.created_at.desc())).scalars()]
        d["obligations"] = [{**to_dict(o), "definition": ctx.db.get(ObligationDefinition, o.obligation_definition_id).key}
                            for o in ctx.db.execute(select(ObligationInstance).where(ObligationInstance.subject_id == s.subject_id,
                                                                                     ObligationInstance.record_to_seq.is_(None))).scalars()]
        d["evaluations"] = [{"evaluation_id": e.evaluation_id, "outcome": e.outcome,
                             "obligation": ctx.db.get(ObligationDefinition, e.obligation_definition_id).key}
                            for e in ctx.db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.subject_id == s.subject_id)
                                                    .order_by(ApplicabilityEvaluation.record_seq.desc())).scalars()]
        d["grants"] = [to_dict(g) for g in ctx.db.execute(select(AuthorityGrant).where(AuthorityGrant.agent_subject_id == s.subject_id)).scalars()]
        d["receipts"] = [to_dict(r) for r in ctx.db.execute(select(PolicyDecisionReceipt).where(PolicyDecisionReceipt.agent_subject_id == s.subject_id)
                                                            .order_by(PolicyDecisionReceipt.decided_at.desc()).limit(20)).scalars()]
        out.append(d)
    return out


class AssetIn(BaseModel):
    entity_id: str
    display_name: str
    asset_kind: str
    version_label: str
    purpose: str
    provider: str
    model: str
    environment: str
    agbom: list = []


@router.post("/assets")
def register(body: AssetIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage_ai")
    s = Subject(tenant_id=ctx.tenant, entity_id=body.entity_id, subject_type="AI_ASSET", display_name=body.display_name)
    ctx.db.add(s)
    ctx.db.flush()
    v = AIAssetVersion(tenant_id=ctx.tenant, asset_subject_id=s.subject_id, asset_kind=body.asset_kind,
                       version_label=body.version_label, purpose=body.purpose, provider=body.provider, model=body.model,
                       environment=body.environment, owner_actor_id=ctx.actor.actor_id, agbom=body.agbom)
    ctx.db.add(v)
    emit(ctx.db, event_type="ai_asset.registered", aggregate_type="AIAsset", aggregate_id=s.subject_id, tenant_id=ctx.tenant,
         payload={"kind": body.asset_kind, "version": body.version_label})
    ctx.db.commit()
    return to_dict(s)


class VersionIn(BaseModel):
    version_label: str
    purpose: str
    provider: str
    model: str
    environment: str
    agbom: list = []


@router.post("/assets/{subject_id}/versions")
def new_version(subject_id: str, body: VersionIn, ctx: Ctx = Depends(get_ctx)):
    """AgBOM change may trigger reclassification -> emits re-evaluation signal."""
    ctx.require("manage_ai")
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "asset")
    prev = ctx.db.execute(select(AIAssetVersion).where(AIAssetVersion.asset_subject_id == subject_id)
                          .order_by(AIAssetVersion.created_at.desc())).scalars().first()
    v = AIAssetVersion(tenant_id=ctx.tenant, asset_subject_id=subject_id, asset_kind=prev.asset_kind if prev else "AGENT",
                       owner_actor_id=ctx.actor.actor_id, **body.model_dump())
    ctx.db.add(v)
    changed = prev is None or prev.agbom != body.agbom or prev.model != body.model
    if changed:
        emit(ctx.db, event_type="ai_asset.reclassification_required", aggregate_type="AIAsset", aggregate_id=subject_id,
             tenant_id=ctx.tenant, payload={"reason": "AgBOM/model change"})
    ctx.db.commit()
    return {"version": to_dict(v), "reclassification_required": changed}


class GrantIn(BaseModel):
    agent_subject_id: str
    action_class: str
    authority_level: str
    scope: str
    reversibility: str
    expires_in_days: int = 90
    fail_mode: str = "FAIL_CLOSED"


@router.post("/grants")
def grant(body: GrantIn, ctx: Ctx = Depends(get_ctx)):
    """Authority is per tuple agent x action x scope x reversibility x expiry x grantor (FR-AI-004)."""
    ctx.require_human("Authority grant")
    ctx.require("grant_authority")
    ctx.scoped(ctx.db.get(Subject, body.agent_subject_id), "agent")
    if body.authority_level not in AUTHORITY:
        raise HTTPException(422, "authority_level must be A0..A4")
    if body.authority_level == "A4":
        raise HTTPException(422, "A4 material autonomy is excluded from initial production scope")
    g = AuthorityGrant(tenant_id=ctx.tenant, agent_subject_id=body.agent_subject_id, action_class=body.action_class,
                       authority_level=body.authority_level, scope=body.scope, reversibility=body.reversibility,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days),
                       granted_by=ctx.actor.actor_id, fail_mode=body.fail_mode)
    ctx.db.add(g)
    ctx.db.flush()
    emit(ctx.db, event_type="agent.grant_issued", aggregate_type="AuthorityGrant", aggregate_id=g.grant_id, tenant_id=ctx.tenant,
         actor=ctx.actor_ref(), payload={"action_class": body.action_class, "authority": body.authority_level})
    ctx.db.commit()
    return to_dict(g)


@router.post("/grants/{grant_id}/revoke")
def revoke(grant_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require("grant_authority")
    g = ctx.scoped(ctx.db.get(AuthorityGrant, grant_id), "grant")
    g.status = "REVOKED"
    ctx.db.commit()
    return to_dict(g)


class DecisionReq(BaseModel):
    agent_subject_id: str
    action_class: str
    scope: str
    requested_authority: str = "A2"
    nonce: str | None = None


def _decide(ctx: Ctx, req: DecisionReq) -> PolicyDecisionReceipt:
    now = datetime.now(timezone.utc)
    grants = ctx.db.execute(select(AuthorityGrant).where(AuthorityGrant.tenant_id == ctx.tenant,
                                                         AuthorityGrant.agent_subject_id == req.agent_subject_id,
                                                         AuthorityGrant.action_class == req.action_class,
                                                         AuthorityGrant.status == "ACTIVE")).scalars().all()
    match = next((g for g in grants if ensure_utc(g.expires_at) > now and g.scope == req.scope
                  and AUTHORITY.index(g.authority_level) >= AUTHORITY.index(req.requested_authority)), None)
    obls = [o.lineage_id for o in ctx.db.execute(select(ObligationInstance).where(
        ObligationInstance.subject_id == req.agent_subject_id, ObligationInstance.lifecycle == "ADOPTED",
        ObligationInstance.record_to_seq.is_(None))).scalars()]
    if match:
        decision, why = "PERMIT", f"Active grant {match.grant_id} ({match.authority_level}) covers action/scope; expires {match.expires_at.isoformat()}."
    elif any(ensure_utc(g.expires_at) <= now for g in grants):
        decision, why = "DENY", "Grant expired for this action class."
    else:
        decision, why = "DENY", "No active grant for agent x action class x scope at requested authority (fail-closed)."
    r = PolicyDecisionReceipt(tenant_id=ctx.tenant, agent_subject_id=req.agent_subject_id, action_class=req.action_class,
                              scope=req.scope, decision=decision, grant_id=match.grant_id if match else None,
                              obligation_refs=obls, request_digest=digest(req.model_dump()), rationale=why,
                              policy_version=POLICY_VERSION)
    ctx.db.add(r)
    return r


@router.post("/decisions")
def decide(req: DecisionReq, ctx: Ctx = Depends(get_ctx)):
    """Deterministic PDP (p99 target <=150ms; no model dependency)."""
    r = _decide(ctx, req)
    ctx.db.commit()
    return to_dict(r)


@router.post("/acs/evaluate")
def acs_adapter(body: dict, ctx: Ctx = Depends(get_ctx)):
    """OWASP ACS-compatible adapter: external schema mapped onto the stable internal decision contract."""
    try:
        req = DecisionReq(agent_subject_id=body["agent"]["id"], action_class=body["action"]["type"],
                          scope=body.get("resource", {}).get("scope", ""), requested_authority=body.get("authority", "A2"))
    except (KeyError, TypeError):
        raise HTTPException(422, "Unsupported ACS request shape")
    r = _decide(ctx, req)
    ctx.db.commit()
    return {"decision": {"PERMIT": "allow", "DENY": "deny"}[r.decision], "receipt_id": r.receipt_id,
            "obligations": r.obligation_refs, "policy_version": r.policy_version, "reason": r.rationale}


@router.get("/policy-bundle/{agent_subject_id}")
def bundle(agent_subject_id: str, ctx: Ctx = Depends(get_ctx)):
    """Signed, short-validity local policy bundle so an Assure outage is never an uncontrolled fail-open."""
    ctx.scoped(ctx.db.get(Subject, agent_subject_id), "agent")
    now = datetime.now(timezone.utc)
    grants = [{"grant_id": g.grant_id, "action_class": g.action_class, "authority_level": g.authority_level, "scope": g.scope,
               "expires_at": g.expires_at, "fail_mode": g.fail_mode}
              for g in ctx.db.execute(select(AuthorityGrant).where(AuthorityGrant.agent_subject_id == agent_subject_id,
                                                                   AuthorityGrant.status == "ACTIVE")).scalars()
              if ensure_utc(g.expires_at) > now]
    payload = json.loads(canonical_json({"agent": agent_subject_id, "issued_at": now, "valid_until": now + timedelta(minutes=15),
                                         "policy_version": POLICY_VERSION, "grants": grants}))
    return {"bundle": payload, "signature": keys.sign(canonical_json(payload)), "signer_key_id": keys.signer_key_id()}
