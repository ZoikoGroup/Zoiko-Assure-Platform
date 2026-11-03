"""Evidence Completeness & Accuracy — IPE (ZA-IPE-001).

The unit of IPE is a SourceRun executed under exact Extraction + Mapping + IPEPolicy
versions. Completeness and accuracy are separate subresults; overall IPE is derived
(never manually set). INCONCLUSIVE is not VERIFIED. Assure digests are always labeled
Assure-generated and never presented as source checksums.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import codes
from ..models import (ConnectorDefinition, ConnectorInstallation, ExtractionContractVersion, IPEAnomaly, IPEEvaluation,
                      IPEPolicyVersion, MappingContractVersion, PopulationManifest, SourceRun, TestDefinition, TestRun)
from ..platform import audit, canonical_json, digest, emit, ensure_utc, next_record_seq, sha256_hex
from ..security import Requirement
from . import evidence as evd

V, F, I, NR = "VERIFIED", "FAILED", "INCONCLUSIVE", "NOT_REQUIRED"
REEVALUATE_IPE = Requirement(("reevaluate_ipe",), human=True)


def bound_evaluation(db: Session, run: SourceRun | None) -> IPEEvaluation | None:
    """The IPE result that belongs to a SourceRun: its first evaluation under the IPEPolicyVersion the run was executed
    with (ZA-IPE-001: the unit of IPE is one SourceRun under an exact IPEPolicyVersion, and the IPEEvaluation is
    immutable; ZA-EVD-001: a re-evaluation needs a new run).

    Every reader that relies on a run's IPE result uses this, never "the newest evaluation", so a later evaluation —
    or one under another, weaker policy — cannot replace it. The bound evaluation is written in the same transaction
    as the run and its evidence (execute_source_run), so it exists at every record cutoff at which that evidence is
    visible."""
    if run is None:
        return None
    return db.execute(select(IPEEvaluation).where(IPEEvaluation.source_run_id == run.source_run_id,
                                                  IPEEvaluation.ipe_policy_version_id == run.ipe_policy_version_id)
                      .order_by(IPEEvaluation.created_at, IPEEvaluation.ipe_evaluation_id)).scalars().first()


def schema_fingerprint(fields) -> str:
    return sha256_hex(canonical_json(sorted(set(fields))))


def request_fingerprint(**kw) -> str:
    """HASHv1(source_system, endpoint, template, params, scope, auth scopes, boundary, build)."""
    return digest({"v": "HASHv1", **kw})


def apply_mapping(mapping: MappingContractVersion, record: dict) -> tuple[dict | None, str | None]:
    """Deterministic, versioned transform library. Unknown enums / missing fields never silently default."""
    out = {}
    for m in mapping.field_mappings:
        src, tgt, typ = m["source"], m["target"], m["type"]
        if src not in record or record[src] is None:
            if m.get("nullable"):
                out[tgt] = None
                continue
            return None, f"MISSING_FIELD:{src}"
        v = record[src]
        tr = m.get("transform")
        if tr == "ENUM_MAP":
            if v not in m["enum_map"]:
                return None, f"UNKNOWN_ENUM:{src}={v}"
            v = m["enum_map"][v]
        elif tr == "TIME_UTC":
            try:
                v = ensure_utc(datetime.fromisoformat(str(v).replace("Z", "+00:00"))).isoformat()
            except ValueError:
                return None, f"TYPE_ERROR:{src}"
        elif tr == "BYTES_TO_GB":
            v = round(int(v) / 1_000_000_000, 6)
        if typ == "BOOLEAN" and not isinstance(v, bool):
            return None, f"TYPE_ERROR:{src}"
        if typ == "STRING" and not isinstance(v, str):
            return None, f"TYPE_ERROR:{src}"
        out[tgt] = v
    return out, None


def derive_subresult(checks: list[dict], required: bool) -> str:
    if not required:
        return NR
    res = [c["result"] for c in checks]
    if not res:
        return I
    if F in res:
        return F
    if I in res:
        return I
    return V


def derive_overall(policy: IPEPolicyVersion, comp: str, acc: str) -> str:
    """Overall result table v1 (§3)."""
    if comp == F or acc == F:
        return "IPE_FAILED"
    if comp == I or acc == I:
        return "IPE_INCONCLUSIVE"
    if comp == NR and acc == NR:
        return "IPE_NOT_REQUIRED"
    return "IPE_VERIFIED"


def execute_source_run(db: Session, *, tenant_id: str, installation: ConnectorInstallation, payload: dict,
                       actor_id: str, ipe_policy: IPEPolicyVersion, test_definition_id: str | None = None) -> dict:
    connector = db.get(ConnectorDefinition, installation.connector_id)
    ext = db.execute(select(ExtractionContractVersion).where(ExtractionContractVersion.connector_id == connector.connector_id)
                     .order_by(ExtractionContractVersion.version.desc())).scalars().first()
    mp = db.execute(select(MappingContractVersion).where(MappingContractVersion.connector_id == connector.connector_id)
                    .order_by(MappingContractVersion.version.desc())).scalars().first()
    if not ext or not mp:
        raise HTTPException(409, "Connector lacks certified extraction/mapping contracts")
    pages = payload.get("pages", [])
    now = datetime.now(timezone.utc)
    cov_from = ensure_utc(datetime.fromisoformat(payload["coverage_from"].replace("Z", "+00:00"))) if payload.get("coverage_from") else now - timedelta(days=1)
    cov_to = ensure_utc(datetime.fromisoformat(payload["coverage_to"].replace("Z", "+00:00"))) if payload.get("coverage_to") else now
    boundary = payload.get("consistency_boundary")
    actual_scopes = payload.get("actual_scopes", installation.granted_scopes)
    params = payload.get("parameters", {})
    seq = next_record_seq(db)
    cursors = [p.get("cursor") for p in pages if p.get("cursor")]
    loop = len(cursors) != len(set(cursors))
    terminal = bool(payload.get("terminal_reached")) and not loop
    outcome = "FAILED" if loop else ("SUCCESS" if terminal else "PARTIAL")
    run = SourceRun(tenant_id=tenant_id, entity_id=installation.entity_id, installation_id=installation.installation_id,
                    connector_build=connector.build_version, extraction_contract_version_id=ext.extraction_contract_version_id,
                    mapping_contract_version_id=mp.mapping_contract_version_id, ipe_policy_version_id=ipe_policy.ipe_policy_version_id,
                    actual_scopes=actual_scopes, request_parameters=params,
                    request_fingerprint=request_fingerprint(source_system=connector.key, endpoint=ext.endpoint_id,
                                                            template=ext.query_template, params=params, scope=params.get("scope"),
                                                            auth=sorted(actual_scopes), boundary=boundary, build=connector.build_version),
                    consistency_boundary=boundary, lifecycle="RECONCILING", run_outcome=outcome,
                    pages=[{"page": p.get("page"), "cursor": p.get("cursor"), "count": len(p.get("records", []))} for p in pages],
                    coverage_from=cov_from, coverage_to=cov_to, record_seq=seq)
    db.add(run)
    db.flush()

    # ---- population manifest (§13)
    key = ext.record_identity
    raw = [r for p in pages for r in p.get("records", [])]
    seen, dups, uniq = {}, [], []
    for r in raw:
        k = str(r.get(key))
        if k in seen:
            dups.append(k)
            continue
        seen[k] = r
        uniq.append(r)
    mapped, rejected = [], []
    for r in uniq:
        out, err = apply_mapping(mp, r)
        if err:
            rejected.append({"key": str(r.get(key)), "error": err})
        else:
            mapped.append(out)
    excluded = payload.get("governed_exclusions", [])
    pop_lines = sorted(f"{ext.source_object}|{r.get(key)}|{r.get('etag', '')}" for r in uniq)
    h = hashlib.sha256()
    for line in pop_lines:  # streaming identity digest (IPE-ADR-01)
        h.update(line.encode() + b"\n")
    rel = mp.relied_upon_fields
    mapped_sorted = sorted(mapped, key=lambda m: str(m.get(rel[0]) if rel else ""))
    mapping_digest = digest({"mapping_contract": mp.mapping_contract_version_id,
                             "rows": [{f: m.get(f) for f in rel} for m in mapped_sorted]})
    pm = PopulationManifest(tenant_id=tenant_id, source_run_id=run.source_run_id,
                            population_definition=payload.get("population_definition", f"All {ext.source_object} in scope"),
                            scope_manifest=params.get("scope", {}), consistency_boundary=boundary,
                            source_reported_total=payload.get("source_reported_total"), extracted_raw_count=len(raw),
                            unique_identity_count=len(uniq), duplicate_count=len(dups), rejected_count=len(rejected),
                            excluded_count=len(excluded), terminal_state={"terminal_reached": terminal, "cursor_loop": loop,
                                                                          "last_cursor": cursors[-1] if cursors else None},
                            source_checksum_refs=[{"value": payload["source_checksum"], "algorithm": payload.get("source_checksum_algorithm", "source-defined"),
                                                   "label": "Source-provided integrity value"}] if payload.get("source_checksum") else [],
                            assure_population_digest=h.hexdigest(), assure_raw_response_digest=digest(pages),
                            mapping_output_digest=mapping_digest, duplicate_keys=dups[:200], rejected_keys=rejected[:200])
    db.add(pm)

    # ---- checks (§15)
    checks: list[dict] = []
    def chk(cid, family, dim, ok, reason, detail, inconclusive=False):
        res = V if ok else (I if inconclusive else F)
        checks.append({"check_id": cid, "family": family, "dimension": dim, "result": res,
                       "reason_code": None if ok else reason, "detail": detail})
    req_c, req_a = set(ipe_policy.required_completeness_checks), set(ipe_policy.required_accuracy_checks)
    if "SCOPE_AUTH" in req_c:
        missing = sorted(set(ext.required_scopes) - set(actual_scopes))
        chk("SCOPE_AUTH", "Scope/auth", "COMPLETENESS", not missing, "IPE_SCOPE_INSUFFICIENT",
            f"required={ext.required_scopes} actual={actual_scopes} missing={missing}")
    if "CONSISTENCY" in req_c:
        ok = ext.consistency_strategy != "LIMITED" and bool(boundary)
        chk("CONSISTENCY", "Consistency", "COMPLETENESS", ok, "IPE_SNAPSHOT_UNPROVEN",
            f"strategy={ext.consistency_strategy} boundary={boundary}", inconclusive=True)
    if "ENUMERATION" in req_c:
        if outcome == "PARTIAL" and ipe_policy.allow_partial:
            chk("ENUMERATION", "Enumeration", "COMPLETENESS", True, None, "partial-population purpose permitted")
        else:
            chk("ENUMERATION", "Enumeration", "COMPLETENESS", terminal, "IPE_CURSOR_INCOMPLETE",
                f"terminal_reached={terminal} cursor_loop={loop} pages={len(pages)}")
    if "SOURCE_TOTAL_RECONCILIATION" in req_c:
        tot = payload.get("source_reported_total")
        if tot is None:
            chk("SOURCE_TOTAL_RECONCILIATION", "Counts/identity", "COMPLETENESS", False, "IPE_SOURCE_LIMITATION",
                "source provides no authoritative total under same boundary", inconclusive=True)
        else:
            rec = len(uniq) + len(excluded)
            chk("SOURCE_TOTAL_RECONCILIATION", "Counts/identity", "COMPLETENESS", tot == rec, "IPE_COUNT_MISMATCH",
                f"source_total={tot} unique={len(uniq)} governed_exclusions={len(excluded)} unresolved_gap={tot - rec}")
    if "DUPLICATES" in req_c:
        chk("DUPLICATES", "Counts/identity", "COMPLETENESS", not dups, "IPE_DUPLICATE_KEYS",
            f"duplicate_count={len(dups)}", inconclusive=True)
    if "REJECTED_RECORDS" in req_c:
        chk("REJECTED_RECORDS", "Counts/identity", "COMPLETENESS", not rejected, "IPE_REJECTED_RECORDS",
            f"rejected_count={len(rejected)}")
    if "ZERO_ROWS" in req_c and not uniq:
        positive = payload.get("source_reported_total") == 0 and terminal
        chk("ZERO_ROWS", "Counts/identity", "COMPLETENESS", positive, "IPE_ZERO_ROWS_UNPROVEN",
            "empty extraction requires positive empty-population proof", inconclusive=True)
    observed_fields = sorted({f for r in raw for f in r.keys()})
    if "SCHEMA_DRIFT" in req_a:
        fp = schema_fingerprint(observed_fields)
        chk("SCHEMA_DRIFT", "Mapping", "ACCURACY", fp == mp.source_schema_fingerprint, "IPE_MAPPING_MISMATCH",
            f"observed fields={observed_fields}", inconclusive=True)
    if "FIELD_MAPPING" in req_a:
        enum_errs = [r for r in rejected if r["error"].startswith(("UNKNOWN_ENUM", "MISSING_FIELD", "TYPE_ERROR"))]
        chk("FIELD_MAPPING", "Mapping", "ACCURACY", not enum_errs, "IPE_MAPPING_MISMATCH",
            f"mapping errors={enum_errs[:5]}", inconclusive=True)
    if "TRANSFORM_REPERFORMANCE" in req_a:
        claimed = payload.get("connector_mapped_records")
        if claimed is None:
            chk("TRANSFORM_REPERFORMANCE", "Transform", "ACCURACY", True, None,
                "DETERMINISTIC_REPERFORMANCE: canonical output produced by versioned transform library")
        else:
            cl_sorted = sorted(claimed, key=lambda m: str(m.get(rel[0]) if rel else ""))
            cl_digest = digest({"mapping_contract": mp.mapping_contract_version_id,
                                "rows": [{f: m.get(f) for f in rel} for m in cl_sorted]})
            chk("TRANSFORM_REPERFORMANCE", "Transform", "ACCURACY", cl_digest == mapping_digest, "IPE_TRANSFORM_MISMATCH",
                f"reperformed digest {mapping_digest[:12]}… vs connector output {cl_digest[:12]}…")
    if "SOURCE_CHECKSUM" in req_a and payload.get("source_checksum"):
        computed = sha256_hex(canonical_json(raw))
        chk("SOURCE_CHECKSUM", "Source integrity", "ACCURACY", computed == payload["source_checksum"],
            "IPE_SOURCE_CHECKSUM_MISMATCH", "source-provided checksum compared to captured response")
    comp = derive_subresult([c for c in checks if c["dimension"] == "COMPLETENESS"], ipe_policy.completeness_required)
    acc = derive_subresult([c for c in checks if c["dimension"] == "ACCURACY"], ipe_policy.accuracy_required)
    overall = derive_overall(ipe_policy, comp, acc)
    reasons = sorted({c["reason_code"] for c in checks if c["reason_code"]})
    ev = IPEEvaluation(tenant_id=tenant_id, source_run_id=run.source_run_id, ipe_policy_version_id=ipe_policy.ipe_policy_version_id,
                       mapping_contract_version_id=mp.mapping_contract_version_id, completeness_result=comp,
                       accuracy_result=acc, overall_result=overall, checks=json.loads(canonical_json(checks)),
                       reason_codes=reasons, engine_version=codes.IPE_ENGINE_VERSION)
    db.add(ev)
    db.flush()
    for c in checks:
        if c["result"] != V:
            keys = dups if c["check_id"] == "DUPLICATES" else [r["key"] for r in rejected] if c["check_id"] in ("REJECTED_RECORDS", "FIELD_MAPPING") else []
            db.add(IPEAnomaly(tenant_id=tenant_id, source_run_id=run.source_run_id, ipe_evaluation_id=ev.ipe_evaluation_id,
                              check_id=c["check_id"], anomaly_class=c["reason_code"] or c["check_id"], dimension=c["dimension"],
                              affected_count=len(keys), affected_keys=keys[:200], severity="HIGH" if c["result"] == F else "MEDIUM"))
    # ---- evidence capture (canonical export; exact canonicalization version recorded)
    export = {"canonicalization": "za-canonical-export-v1", "source_run_id": run.source_run_id,
              "mapping_contract_version_id": mp.mapping_contract_version_id, "records": mapped_sorted}
    art = evd.capture_evidence(db, tenant_id=tenant_id, entity_id=installation.entity_id, content=canonical_json(export),
                               title=f"{connector.name} — {ext.source_object} export", content_type="application/json",
                               origin_class="SYSTEM_DIRECT", acquisition_method="API", provenance_class="P1_SYSTEM_DIRECT",
                               coverage_from=cov_from, coverage_to=cov_to, valid_until=cov_to + timedelta(days=payload.get("validity_days", 35)),
                               actor_id=actor_id, source_system=connector.key, source_run_id=run.source_run_id,
                               source_observed_at=now, source_checksum=payload.get("source_checksum"),
                               idempotency_key=run.request_fingerprint + str(seq))
    run.evidence_ids = [art.evidence_id]
    run.lifecycle = "COMPLETED"
    run.completed_at = datetime.now(timezone.utc)
    installation.last_run_at = run.completed_at
    test_run = None
    if test_definition_id:
        td = db.get(TestDefinition, test_definition_id)
        if td and td.tenant_id == tenant_id and td.criterion:
            result, detail = evaluate_criterion(td.criterion, mapped_sorted)
            if outcome == "FAILED":
                result, detail = "ERROR", "source extraction error"
            test_run = TestRun(tenant_id=tenant_id, test_definition_id=td.test_definition_id, occurred_at=now,
                               window_from=cov_from, window_to=cov_to, result=result, source_system=connector.key,
                               source_run_id=run.source_run_id, evidence_ids=[art.evidence_id], detail=detail,
                               record_seq=next_record_seq(db))
            db.add(test_run)
    emit(db, event_type=f"source_run.{'completed' if outcome != 'PARTIAL' else 'partial'}", aggregate_type="SourceRun",
         aggregate_id=run.source_run_id, tenant_id=tenant_id, entity_id=installation.entity_id,
         payload={"connector": connector.key, "build": connector.build_version, "outcome": outcome,
                  "population_manifest_id": pm.population_manifest_id})
    emit(db, event_type="ipe.evaluated", aggregate_type="IPEEvaluation", aggregate_id=ev.ipe_evaluation_id, tenant_id=tenant_id,
         payload={"source_run_id": run.source_run_id, "completeness": comp, "accuracy": acc, "overall": overall, "reason_codes": reasons})
    if overall in ("IPE_FAILED", "IPE_INCONCLUSIVE"):
        emit(db, event_type="ipe.failed" if overall == "IPE_FAILED" else "ipe.inconclusive", aggregate_type="IPEEvaluation",
             aggregate_id=ev.ipe_evaluation_id, tenant_id=tenant_id,
             payload={"reason_codes": reasons, "affected_evidence_ids": [art.evidence_id]})
    db.flush()
    return {"source_run": run, "population_manifest": pm, "ipe_evaluation": ev, "evidence": art, "test_run": test_run}


def evaluate_criterion(crit: dict, records: list[dict]) -> tuple[str, str]:
    """Deterministic test criterion over mapped relied-upon fields."""
    field, op, val = crit["field"], crit.get("op", "EQ"), crit.get("value")
    def ok(r):
        v = r.get(field)
        return v == val if op == "EQ" else v != val
    bad = [r for r in records if not ok(r)]
    if not records:
        return "INCONCLUSIVE", "empty population"
    if crit.get("quantifier", "ALL") == "ALL":
        return ("PASS" if not bad else "FAIL"), f"{len(records) - len(bad)}/{len(records)} records satisfy {field} {op} {val}"
    return ("PASS" if len(bad) < len(records) else "FAIL"), "ANY quantifier"


def reevaluate(ctx, run: SourceRun, ipe_policy_version_id: str | None = None) -> IPEEvaluation:
    """Re-derive a run's IPE result under the policy the run was executed with, recorded as a new immutable evaluation.

    The policy is fixed by the SourceRun (ZA-IPE-001 §14); a caller cannot substitute another one, because a weaker
    policy drops the failing checks. Proof under a different policy needs a new run. The run's bound evaluation is
    preserved and stays the one assurance uses (`bound_evaluation`). Authorization is checked here as well as on the
    route, so internal callers cannot bypass it."""
    ctx.check(REEVALUATE_IPE)
    db = ctx.db
    if ipe_policy_version_id is not None and ipe_policy_version_id != run.ipe_policy_version_id:
        raise HTTPException(409, {"error": "IPE_POLICY_NOT_BOUND",
                                  "message": "A source run is evaluated only under the IPE policy it was executed with; "
                                             "proof under a different policy needs a new source run.",
                                  "bound_ipe_policy_version_id": run.ipe_policy_version_id})
    policy = db.get(IPEPolicyVersion, run.ipe_policy_version_id)
    base = bound_evaluation(db, run)
    if policy is None or base is None:
        raise HTTPException(409, {"error": "IPE_BOUND_EVALUATION_MISSING",
                                  "message": "This source run has no evaluation under its own IPE policy to re-derive."})
    checks = [c for c in base.checks if (c["dimension"] == "COMPLETENESS" and c["check_id"] in policy.required_completeness_checks)
              or (c["dimension"] == "ACCURACY" and c["check_id"] in policy.required_accuracy_checks)]
    comp = derive_subresult([c for c in checks if c["dimension"] == "COMPLETENESS"], policy.completeness_required)
    acc = derive_subresult([c for c in checks if c["dimension"] == "ACCURACY"], policy.accuracy_required)
    ev = IPEEvaluation(tenant_id=run.tenant_id, source_run_id=run.source_run_id, ipe_policy_version_id=policy.ipe_policy_version_id,
                       mapping_contract_version_id=run.mapping_contract_version_id, completeness_result=comp,
                       accuracy_result=acc, overall_result=derive_overall(policy, comp, acc), checks=checks,
                       reason_codes=sorted({c["reason_code"] for c in checks if c["reason_code"]}),
                       engine_version=codes.IPE_ENGINE_VERSION)
    db.add(ev)
    db.flush()
    audit(db, action="ipe.reevaluated", object_type="SourceRun", object_id=run.source_run_id, actor=ctx.actor,
          tenant_id=run.tenant_id, prior=base.overall_result, new=ev.overall_result,
          reason=f"Re-derived under bound IPE policy {policy.ipe_policy_version_id} as evaluation {ev.ipe_evaluation_id}; "
                 f"bound evaluation {base.ipe_evaluation_id} unchanged")
    return ev
