"""AI asset registry, authority grants and deterministic policy decision point (ZA-ARCH-001 §12).

No model call sits on the decision path (INV-02). Enforcement stays in the customer
runtime; Assure returns the decision + legal-obligation context + an evidence receipt.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..engines import governance as gov
from ..engines import keys
from ..models import (AIAssetVersion, ApplicabilityEvaluation, AuthorityGrant, LegalEntity, ObligationDefinition,
                      ObligationInstance, PolicyDecisionReceipt, Subject)
from ..platform import canonical_json, digest, emit, ensure_utc, to_dict
from ..security import READ, Ctx, authorize, entity_of

router = APIRouter(prefix="/ai")
AGENT_SUBJECT = entity_of(Subject, "agent_subject_id", "agent")  # Stage 4: the agent subject's legal entity
POLICY_VERSION = "za-agent-pdp:1.0"
AUTHORITY = ["A0", "A1", "A2", "A3", "A4"]  # OBSERVE, RECOMMEND, PREPARE, EXECUTE_REVERSIBLE, MATERIAL (excluded)


@router.get("/assets")
def assets(ctx: Ctx = Depends(authorize(*READ))):
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
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
def register(body: AssetIn, ctx: Ctx = Depends(authorize("manage_ai"))):
    ctx.scoped(ctx.db.get(LegalEntity, body.entity_id), "entity")  # the asset's entity must belong to this organisation
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    version_label: str
    purpose: str
    provider: str
    model: str
    environment: str
    agbom: list = []


@router.post("/assets/{subject_id}/versions")
def new_version(subject_id: str, body: VersionIn, ctx: Ctx = Depends(authorize("manage_ai"))):
    """AgBOM change may trigger reclassification -> emits re-evaluation signal."""
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
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    # API-REQ-033 (field length limits; DAT-REQ-039 review R039-04): the limits are the columns' own sizes, so an
    # oversized value is a 422 here instead of a database error (500).
    agent_subject_id: str = Field(max_length=36)
    action_class: str = Field(max_length=120)
    authority_level: str = Field(max_length=8)
    scope: str = Field(max_length=200)
    reversibility: str = Field(max_length=64)
    expires_in_days: int = 90
    fail_mode: str = Field(default="FAIL_CLOSED", max_length=16)


@router.post("/grants")
def grant(body: GrantIn, ctx: Ctx = Depends(authorize("grant_authority", human=True))):
    """Authority is per tuple agent x action x scope x reversibility x expiry x grantor (FR-AI-004)."""
    ctx.scoped(ctx.db.get(Subject, body.agent_subject_id), "agent")
    if body.authority_level not in AUTHORITY:
        raise HTTPException(422, "authority_level must be A0..A4")
    if body.authority_level == "A4":
        raise HTTPException(422, "A4 material autonomy is excluded from initial production scope")
    # Stage 3c (migration 0011): projection, ISSUED event, audit record and outbox event commit together.
    g, _ = gov.issue_authority_grant(
        ctx.db, tenant_id=ctx.tenant, agent_subject_id=body.agent_subject_id, action_class=body.action_class,
        authority_level=body.authority_level, scope=body.scope, reversibility=body.reversibility,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days), fail_mode=body.fail_mode,
        actor=ctx.actor, actor_ref=ctx.actor_ref())
    ctx.db.commit()
    return to_dict(g)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    reason: str | None = None


@router.post("/grants/{grant_id}/revoke")
def revoke(grant_id: str, body: RevokeIn | None = None,
           ctx: Ctx = Depends(authorize("grant_authority", human=True))):
    """Revocation takes effect for the next decision (IAM-REQ-015). The body is optional, so existing clients that send
    none keep working; a repeated revocation returns the revoked grant and records nothing new."""
    ctx.scoped(ctx.db.get(AuthorityGrant, grant_id), "grant")  # another organisation's grant: 404, as if absent
    g, _ = gov.revoke_authority_grant(ctx.db, tenant_id=ctx.tenant, grant_id=grant_id, actor=ctx.actor,
                                      actor_ref=ctx.actor_ref(), reason=body.reason if body else None)
    ctx.db.commit()
    return to_dict(g)


class DecisionReq(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    agent_subject_id: str = Field(max_length=36)   # API-REQ-033: the receipt columns' sizes (R039-04)
    action_class: str = Field(max_length=120)
    scope: str = Field(max_length=200)
    requested_authority: str = "A2"
    nonce: str | None = None


def _decide(ctx: Ctx, req: DecisionReq) -> PolicyDecisionReceipt:
    ctx.scoped(ctx.db.get(Subject, req.agent_subject_id), "agent")  # receipts only for this organisation's agents
    now = datetime.now(timezone.utc)
    # IAM-REQ-036 / IAM-REQ-015 (DAT-REQ-039 review R039-01): the grants are read under a share lock held until this
    # decision commits, so a concurrent revocation waits for it, and a decision that waits for a revocation re-reads the
    # row and no longer finds it ACTIVE. No PERMIT can commit after the revocation of the grant it cites.
    grants = ctx.db.execute(select(AuthorityGrant).where(AuthorityGrant.tenant_id == ctx.tenant,
                                                         AuthorityGrant.agent_subject_id == req.agent_subject_id,
                                                         AuthorityGrant.action_class == req.action_class,
                                                         AuthorityGrant.status == "ACTIVE")
                            .with_for_update(read=True)).scalars().all()
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
def decide(req: DecisionReq, ctx: Ctx = Depends(authorize("request_policy_decision"))):
    """Deterministic PDP (p99 target <=150ms; no model dependency)."""
    r = _decide(ctx, req)
    ctx.db.commit()
    return to_dict(r)


@router.post("/acs/evaluate")
def acs_adapter(body: dict, ctx: Ctx = Depends(authorize("request_policy_decision"))):
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
def bundle(agent_subject_id: str, ctx: Ctx = Depends(authorize(*READ, entity=AGENT_SUBJECT))):
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
