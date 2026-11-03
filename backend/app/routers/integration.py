"""Connector certification registry, source runs and IPE (ZA-IPE-001, FR-CON-*)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..engines import connectors as registry
from ..engines import ipe as ipe_engine
from ..models import (ConnectorDefinition, ConnectorInstallation, ExtractionContractVersion, IPEAnomaly, IPEEvaluation,
                      IPEPolicyVersion, MappingContractVersion, PopulationManifest, SourceRun, TestDefinition)
from ..platform import audit, ensure_utc, to_dict
from ..security import DEV_HEADER, DOSSIER_READ, READ, Ctx, authorize, entity_of

router = APIRouter()
SOURCE_RUN = entity_of(SourceRun, "run_id", "source run")  # Stage 4: entity-scoped roles reach their entity's runs


@router.get("/connectors")
def connectors(ctx: Ctx = Depends(authorize(*READ, "certify_connectors"))):
    """Customer-visible status is read ONLY from the certification registry (INV-11)."""
    out = []
    for c in ctx.db.execute(select(ConnectorDefinition)).scalars():
        d = to_dict(c)
        d["extraction_contracts"] = [to_dict(x) for x in ctx.db.execute(select(ExtractionContractVersion).where(
            ExtractionContractVersion.connector_id == c.connector_id)).scalars()]
        d["mapping_contracts"] = [to_dict(x) for x in ctx.db.execute(select(MappingContractVersion).where(
            MappingContractVersion.connector_id == c.connector_id)).scalars()]
        if ctx.actor.tenant_id:
            d["installations"] = []
            for i in ctx.db.execute(select(ConnectorInstallation).where(ConnectorInstallation.tenant_id == ctx.tenant,
                                                                        ConnectorInstallation.connector_id == c.connector_id)).scalars():
                x = to_dict(i)
                last = ctx.db.execute(select(SourceRun).where(SourceRun.installation_id == i.installation_id)
                                      .order_by(SourceRun.started_at.desc())).scalars().first()
                lastv = None
                for r in ctx.db.execute(select(SourceRun).where(SourceRun.installation_id == i.installation_id)
                                        .order_by(SourceRun.started_at.desc())).scalars():
                    ie = ipe_engine.bound_evaluation(ctx.db, r)
                    if ie and ie.overall_result == "IPE_VERIFIED":
                        lastv = r
                        break
                due = ensure_utc(i.last_run_at) + timedelta(days=i.schedule_days) if i.last_run_at else None
                x["last_source_run"] = to_dict(last) if last else None
                x["last_ipe_verified_run"] = to_dict(lastv) if lastv else None
                x["freshness"] = ("STALE" if due and due < datetime.now(timezone.utc) else "CURRENT") if due else "NEVER_RUN"
                x["next_expected_run"] = due
                missing = set()
                for ext in d["extraction_contracts"]:
                    missing |= set(ext["required_scopes"]) - set(i.granted_scopes)
                x["scope_shortfall"] = sorted(missing)
                d["installations"].append(x)
        out.append(d)
    return out


@router.get("/ipe/policies")
def policies(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(p) for p in ctx.db.execute(select(IPEPolicyVersion)).scalars()]


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    installation_id: str
    ipe_policy_version_id: str
    test_definition_id: str | None = None
    payload: dict


@router.post("/ipe/source-runs")
def execute_run(body: RunIn, ctx: Ctx = Depends(authorize("submit_evidence"))):
    """Execute a connector extraction (payload = what the source returned) and evaluate IPE.

    DISABLED outside dev/test: a client-posted payload cannot prove system-direct provenance (audit F4, IPE-REQ-002/003).
    The certified connector-worker path replaces it (backend build plan, Week 3)."""
    if not DEV_HEADER:
        raise HTTPException(403, "Client-posted source runs are disabled; use the certified connector path")
    inst = ctx.scoped(ctx.db.get(ConnectorInstallation, body.installation_id), "installation")
    pol = ctx.db.get(IPEPolicyVersion, body.ipe_policy_version_id)
    if not pol:
        raise HTTPException(404, "IPE policy")
    r = ipe_engine.execute_source_run(ctx.db, tenant_id=ctx.tenant, installation=inst, payload=body.payload,
                                      actor_id=ctx.actor.actor_id, ipe_policy=pol, test_definition_id=body.test_definition_id)
    ctx.db.commit()
    return {"source_run": to_dict(r["source_run"]), "population_manifest": to_dict(r["population_manifest"]),
            "ipe_evaluation": to_dict(r["ipe_evaluation"]), "evidence_id": r["evidence"].evidence_id,
            "test_run": to_dict(r["test_run"]) if r["test_run"] else None}


@router.get("/ipe/source-runs")
def runs(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    out = []
    for r in ctx.db.execute(select(SourceRun).where(SourceRun.tenant_id == ctx.tenant).order_by(SourceRun.started_at.desc())).scalars():
        d = to_dict(r)
        ie = ipe_engine.bound_evaluation(ctx.db, r)
        d["ipe"] = {k: getattr(ie, k) for k in ("completeness_result", "accuracy_result", "overall_result", "reason_codes")} if ie else None
        inst = ctx.db.get(ConnectorInstallation, r.installation_id)
        d["connector"] = ctx.db.get(ConnectorDefinition, inst.connector_id).name
        out.append(d)
    return out


@router.get("/ipe/source-runs/{run_id}")
def run_detail(run_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SOURCE_RUN))):
    """WF-IPE-01 Source Run Inspector + WF-IPE-02 Population Reconciliation."""
    r = ctx.scoped(ctx.db.get(SourceRun, run_id), "source run")
    inst = ctx.db.get(ConnectorInstallation, r.installation_id)
    con = ctx.db.get(ConnectorDefinition, inst.connector_id)
    ext = ctx.db.get(ExtractionContractVersion, r.extraction_contract_version_id)
    mp = ctx.db.get(MappingContractVersion, r.mapping_contract_version_id)
    pm = ctx.db.execute(select(PopulationManifest).where(PopulationManifest.source_run_id == run_id)).scalars().first()
    evals = ctx.db.execute(select(IPEEvaluation).where(IPEEvaluation.source_run_id == run_id)
                           .order_by(IPEEvaluation.created_at.desc())).scalars().all()
    bound = ipe_engine.bound_evaluation(ctx.db, r)
    anomalies = ctx.db.execute(select(IPEAnomaly).where(IPEAnomaly.source_run_id == run_id)).scalars().all()
    eq = None
    if pm:
        gap = (pm.source_reported_total - pm.unique_identity_count - pm.excluded_count) if pm.source_reported_total is not None else None
        eq = {"intended_source_population": pm.source_reported_total, "extracted_unique_population": pm.unique_identity_count,
              "governed_exclusions": pm.excluded_count, "unresolved_gap_population": gap,
              "verified_condition": "unresolved_gap_population == 0 AND proof method demonstrates intended population"}
    return {"source_run": to_dict(r), "connector": to_dict(con), "installation": to_dict(inst),
            "extraction_contract": to_dict(ext), "mapping_contract": to_dict(mp), "ipe_policy": to_dict(ctx.db.get(IPEPolicyVersion, r.ipe_policy_version_id)),
            "population_manifest": to_dict(pm) if pm else None, "reconciliation_equation": eq,
            # bound = the run's own result under its own policy (what assurance relies on); the list is full history
            "bound_ipe_evaluation": to_dict(bound) if bound else None,
            "ipe_evaluations": [to_dict(e) for e in evals], "anomalies": [to_dict(a) for a in anomalies],
            "scope_check": {"required": ext.required_scopes, "actual": r.actual_scopes,
                            "missing": sorted(set(ext.required_scopes) - set(r.actual_scopes))},
            "digest_labels": {"source_checksum_refs": "Source-provided integrity values (only when the source provides them)",
                              "assure_population_digest": "Assure-generated identity digest — NOT a source checksum",
                              "assure_raw_response_digest": "Assure-generated raw-response digest — NOT a source checksum",
                              "mapping_output_digest": "Assure-generated mapping output digest"}}


class ReevalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    # Optional; when given it must be the run's own policy (anything else is refused with 409 IPE_POLICY_NOT_BOUND).
    ipe_policy_version_id: str | None = None


@router.post("/ipe/source-runs/{run_id}/evaluate-ipe")
def reevaluate(run_id: str, body: ReevalIn, ctx: Ctx = Depends(authorize("reevaluate_ipe", human=True))):
    r = ctx.scoped(ctx.db.get(SourceRun, run_id), "source run")
    ev = ipe_engine.reevaluate(ctx, r, body.ipe_policy_version_id)
    ctx.db.commit()
    return to_dict(ev)


@router.get("/ipe/anomalies")
def anomalies(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(a) for a in ctx.db.execute(select(IPEAnomaly).where(IPEAnomaly.tenant_id == ctx.tenant)
                                               .order_by(IPEAnomaly.created_at.desc())).scalars()]


class DispositionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    disposition_code: str
    rationale: str


DISPOSITIONS = {"SOURCE_DEFECT", "CONNECTOR_DEFECT", "CONFIG_SCOPE", "MAPPING_DEFECT", "ACCEPTED_LIMITATION", "FALSE_POSITIVE"}


@router.post("/ipe/anomalies/{anomaly_id}/dispositions")
def disposition(anomaly_id: str, body: DispositionIn, ctx: Ctx = Depends(authorize("assurance_review", human=True))):
    """Governed disposition; never edits the original check result (IPE-REQ-032)."""
    a = ctx.scoped(ctx.db.get(IPEAnomaly, anomaly_id), "anomaly")
    if body.disposition_code not in DISPOSITIONS:
        raise HTTPException(422, f"disposition_code must be one of {sorted(DISPOSITIONS)}")
    a.disposition_code, a.disposition_rationale = body.disposition_code, body.rationale
    audit(ctx.db, action="ipe.anomaly_disposition", object_type="IPEAnomaly", object_id=anomaly_id, actor=ctx.actor,
          tenant_id=ctx.tenant, new=body.disposition_code, reason=body.rationale)
    ctx.db.commit()
    return to_dict(a)


class CertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    certification: str
    runtime_health: str | None = None
    reason: str


@router.post("/connectors/{connector_id}/certification")
def set_certification(connector_id: str, body: CertIn,
                      ctx: Ctx = Depends(authorize("certify_connectors", human=True, platform=True))):
    """Global certification registry transition (Zoiko platform staff only); CERTIFIED requires the full matrix to pass
    (FR-CON-002). See app/engines/connectors.py."""
    c = registry.set_certification(ctx, connector_id, certification=body.certification,
                                   runtime_health=body.runtime_health, reason=body.reason)
    ctx.db.commit()
    return to_dict(c)
