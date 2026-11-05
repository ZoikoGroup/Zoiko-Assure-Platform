"""Rule authoring, validation, golden vectors, shadow impact and signed release (ZA-RUL-001 §§10-17)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .. import codes
from ..engines import applicability as appl
from ..engines import catalog, keys, rules
from ..models import (ApplicabilityEvaluation, ContentRelease, ObligationDefinition, RuleDraft, RuleVersion, Subject,
                      new_id, utcnow)
from ..platform import audit, canonical_json, emit, next_record_seq, sha256_hex, to_dict
from ..security import CONTENT_READERS, DOSSIER_READ, READ, Ctx, authorize, system_scope

router = APIRouter(prefix="/rules")
CONTENT_STAFF = ("content_author", "content_peer_review", "content_legal_review", "content_release")


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    obligation_definition_id: str
    authoring_mode: str = "AST"
    ast: dict | None = None
    decision_table: dict | None = None
    source_mappings: list = []
    golden_vectors: list = []
    valid_from: date
    valid_to: date | None = None
    change_class: str | None = "EXECUTABLE_OUTCOME"


def _compiled(d: RuleDraft) -> tuple[dict | None, list]:
    if d.authoring_mode == "DECISION_TABLE":
        if not d.decision_table:
            return None, [{"code": "RUL_SCHEMA_INVALID", "severity": "ERROR", "message": "decision_table missing"}]
        return rules.compile_decision_table(d.decision_table)
    return d.ast, []


def _validate(ctx: Ctx, d: RuleDraft) -> dict:
    ast, dt_issues = _compiled(d)
    if ast is None:
        return {"ok": False, "issues": dt_issues, "error_count": len(dt_issues)}
    rep = rules.validate(ast, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    rep["issues"] = dt_issues + rep["issues"]
    rep["error_count"] = sum(1 for i in rep["issues"] if i["severity"] == "ERROR")
    rep["ok"] = rep["error_count"] == 0
    rep["compiled_ast"] = ast
    rep["canonical_ast_hash"] = rules.ast_hash(ast, {s: appl.load_sets(ctx.db)[s]["hash"] for s in rep.get("set_refs", [])}) if rep["ok"] else None
    rep["validated_at"] = utcnow().isoformat()
    return rep


@router.get("/drafts")
def list_drafts(ctx: Ctx = Depends(authorize(*READ, *CONTENT_READERS))):
    out = []
    for d in ctx.db.execute(select(RuleDraft).order_by(RuleDraft.updated_at.desc())).scalars():
        x = to_dict(d)
        x["obligation_definition"] = ctx.db.get(ObligationDefinition, d.obligation_definition_id).key
        out.append(x)
    return out


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, ctx: Ctx = Depends(authorize(*READ, *CONTENT_READERS))):
    d = ctx.db.get(RuleDraft, draft_id)
    if not d:
        raise HTTPException(404)
    x = to_dict(d)
    x["obligation_definition"] = ctx.db.get(ObligationDefinition, d.obligation_definition_id).key
    x["compiled_ast"], _ = _compiled(d)
    return x


@router.post("/drafts")
def create_draft(body: DraftIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    if not ctx.db.get(ObligationDefinition, body.obligation_definition_id):
        raise HTTPException(404, "obligation definition")
    if body.decision_table and body.decision_table.get("table_mode") == "FIRST_MATCH":
        raise HTTPException(422, "FIRST_MATCH is prohibited (RUL-REQ-012)")
    d = RuleDraft(**body.model_dump(), author_id=ctx.actor.actor_id)
    cur = ctx.db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == body.obligation_definition_id)
                         .order_by(RuleVersion.version.desc())).scalars().first()
    d.supersedes_rule_version_id = cur.rule_version_id if cur else None
    ctx.db.add(d)
    ctx.db.flush()
    emit(ctx.db, event_type="rule.draft_created", aggregate_type="RuleDraft", aggregate_id=d.draft_id,
         payload={"obligation_definition_id": d.obligation_definition_id, "author": ctx.actor.actor_id})
    ctx.db.commit()
    return to_dict(d)


@router.put("/drafts/{draft_id}")
def update_draft(draft_id: str, body: DraftIn, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    if not d:
        raise HTTPException(404)
    if d.status in ("PUBLISHED", "RELEASE_READY"):
        raise HTTPException(409, "Frozen artifact; create a new draft")
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    d.revision += 1
    d.status = "DRAFT"  # any edit invalidates prior review/test approvals
    d.peer_reviewer_id = d.legal_reviewer_id = None
    d.validation_report = d.vector_report = d.shadow_report = None
    d.updated_at = utcnow()
    ctx.db.commit()
    return to_dict(d)


@router.post("/drafts/{draft_id}/validate")
def validate(draft_id: str, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    rep = _validate(ctx, d)
    d.validation_report = rep
    if not rep["ok"]:
        d.status = "VALIDATION_FAILED"
        emit(ctx.db, event_type="rule.validation_failed", aggregate_type="RuleDraft", aggregate_id=draft_id,
             payload={"errors": [i["code"] for i in rep["issues"] if i["severity"] == "ERROR"]})
    elif d.status == "VALIDATION_FAILED":
        d.status = "DRAFT"
    ctx.db.commit()
    return rep


@router.post("/drafts/{draft_id}/test")
def test_vectors(draft_id: str, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    rep = _validate(ctx, d)
    if not rep["ok"]:
        raise HTTPException(422, {"error": "RUL_SCHEMA_INVALID", "validation": rep})
    vr = rules.run_golden_vectors(rep["compiled_ast"], d.golden_vectors, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    d.vector_report = vr
    ctx.db.commit()
    return vr


class SandboxIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    facts: dict
    effective_at: str = "2027-12-02"


@router.post("/drafts/{draft_id}/sandbox")
def sandbox(draft_id: str, body: SandboxIn, ctx: Ctx = Depends(authorize(*CONTENT_STAFF, platform=True))):
    """Run the exact draft artifact on synthetic fixtures only (WF-RUL-04); no customer data access."""
    d = ctx.db.get(RuleDraft, draft_id)
    rep = _validate(ctx, d)
    if not rep["ok"]:
        raise HTTPException(422, {"validation": rep})
    vr = rules.run_golden_vectors(rep["compiled_ast"], [{"vector_id": "sandbox", "facts": body.facts,
                                                         "effective_at": body.effective_at, "expected_outcome": "APPLIES"}],
                                  appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    facts = {}
    for k, v in body.facts.items():
        facts[k] = {"known": False, "reason": v["unknown"]} if isinstance(v, dict) and "unknown" in v else \
            {"known": True, "value": v, "revision_id": "synthetic"}
    out = rules.evaluate(rep["compiled_ast"], facts, {k: x["data_type"] for k, x in appl.load_fact_defs(ctx.db).items()},
                         appl.load_sets(ctx.db), {"effective_at": body.effective_at})
    return {**out, "evaluator_build": codes.EVALUATOR_BUILD, "draft_revision": d.revision, "vector_probe": vr["results"][0]}


@router.post("/drafts/{draft_id}/submit-review")
def submit_review(draft_id: str, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    rep = _validate(ctx, d)
    d.validation_report = rep
    if not rep["ok"]:
        d.status = "VALIDATION_FAILED"
        ctx.db.commit()
        raise HTTPException(409, "Validation errors block review")
    vr = rules.run_golden_vectors(rep["compiled_ast"], d.golden_vectors, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    d.vector_report = vr
    if not vr["all_passed"] or vr["missing_mandatory_classes"]:
        ctx.db.commit()
        raise HTTPException(409, {"error": "RUL_GOLDEN_FAILURE", "vectors": vr})
    d.status = "IN_REVIEW"
    emit(ctx.db, event_type="rule.review_requested", aggregate_type="RuleDraft", aggregate_id=draft_id,
         payload={"revision": d.revision, "ast_hash": rep["canonical_ast_hash"]})
    ctx.db.commit()
    return to_dict(d)


@router.post("/drafts/{draft_id}/peer-approve")
def peer_approve(draft_id: str, ctx: Ctx = Depends(authorize("content_peer_review", platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    if d.status != "IN_REVIEW":
        raise HTTPException(409, "Draft not in review")
    if ctx.actor.actor_id == d.author_id:
        raise HTTPException(403, "Separation of duties: author cannot peer-approve")
    d.peer_reviewer_id = ctx.actor.actor_id
    ctx.db.commit()
    return to_dict(d)


@router.post("/drafts/{draft_id}/legal-approve")
def legal_approve(draft_id: str, ctx: Ctx = Depends(authorize("content_legal_review", human=True, platform=True))):
    d = ctx.db.get(RuleDraft, draft_id)
    if d.status != "IN_REVIEW" or not d.peer_reviewer_id:
        raise HTTPException(409, "Requires IN_REVIEW with peer approval")
    if ctx.actor.actor_id in (d.author_id, d.peer_reviewer_id):
        raise HTTPException(403, "Separation of duties")
    d.legal_reviewer_id = ctx.actor.actor_id
    d.status = "VERIFIED"
    emit(ctx.db, event_type="rule.verified", aggregate_type="RuleDraft", aggregate_id=draft_id, payload={"revision": d.revision})
    audit(ctx.db, action="rule.legal_approve", object_type="RuleDraft", object_id=draft_id, actor=ctx.actor,
          prior="IN_REVIEW", new="VERIFIED")
    ctx.db.commit()
    return to_dict(d)


@router.post("/drafts/{draft_id}/shadow")
def shadow(draft_id: str, ctx: Ctx = Depends(authorize("content_author", "content_release", "content_legal_review",
                                                        platform=True))):
    """Home-cell shadow execution; content ops receive aggregate buckets only (FR-APP-006)."""
    d = ctx.db.get(RuleDraft, draft_id)
    if d.status not in ("VERIFIED", "IN_REVIEW"):
        raise HTTPException(409, "Shadow requires a reviewed candidate")
    rep = _validate(ctx, d)
    odef = ctx.db.get(ObligationDefinition, d.obligation_definition_id)
    with system_scope(ctx.db, "shadow impact — aggregate buckets only"):
        d.shadow_report = appl.shadow_impact(ctx.db, odef=odef, candidate_ast=rep["compiled_ast"],
                                             candidate_required=rep["required_facts"]) | {"completed_at": utcnow().isoformat()}
    emit(ctx.db, event_type="rule.shadow_completed", aggregate_type="RuleDraft", aggregate_id=draft_id, payload=d.shadow_report)
    ctx.db.commit()
    return d.shadow_report


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    label: str
    changelog: str


@router.post("/drafts/{draft_id}/publish")
def publish(draft_id: str, body: PublishIn, ctx: Ctx = Depends(authorize("content_release", human=True, platform=True))):
    """Fail-closed publication gates + signed ContentRelease (§15)."""
    d = ctx.db.get(RuleDraft, draft_id)
    if d.status != "VERIFIED":
        raise HTTPException(409, "Only VERIFIED drafts can be released")
    if ctx.actor.actor_id in (d.author_id, d.peer_reviewer_id, d.legal_reviewer_id):
        raise HTTPException(403, "Separation of duties: release manager must be distinct")
    rep = _validate(ctx, d)
    if not rep["ok"]:
        missing = [i["message"] for i in rep["issues"] if i.get("code") == "RUL_SET_REF_INVALID"]
        if missing:
            raise HTTPException(409, {"error": "SET_VERSION_MISSING", "message": missing[0]})
        raise HTTPException(409, "Validation failure blocks publication")
    vr = rules.run_golden_vectors(rep["compiled_ast"], d.golden_vectors, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    if not vr["all_passed"]:
        raise HTTPException(409, "Mandatory golden-vector failure blocks publication")
    if d.change_class in ("EXECUTABLE_OUTCOME", "TEMPORAL", "CORRECTION", "DEPENDENCY") and not d.shadow_report:
        raise HTTPException(409, "Shadow impact required before publication")
    sets = appl.load_sets(ctx.db)
    missing_sets = [s for s in rep.get("set_refs") or [] if s not in sets]
    if missing_sets:
        raise HTTPException(409, {"error": "SET_VERSION_MISSING", "set_version_id": missing_sets[0],
                                  "message": f"Referenced SetVersion {missing_sets[0]} not found"})
    # INV-06 / INV-09 / FR-REG-001/002 release gate, before anything is written: every provision this release binds
    # belongs to a VERIFIED or PUBLISHED requirement version grounded in an intact source snapshot (app/engines/catalog.py).
    # The definition is row-locked first, the same lock link edits take, so the binding signed below is the committed
    # link set and no link edit can commit alongside this publication (DAT-REQ-012).
    binding = catalog.release_binding(ctx.db, catalog.lock_definition(ctx.db, d.obligation_definition_id))
    set_hashes = {s: sets[s]["hash"] for s in rep["set_refs"]}
    canon = rules.canonicalize(rep["compiled_ast"])
    h = rules.ast_hash(canon, set_hashes)
    prev = ctx.db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == d.obligation_definition_id)
                          .order_by(RuleVersion.version.desc())).scalars().first()
    seq = next_record_seq(ctx.db)
    # Both rows are written once, with final values: published catalogue rows are immutable (DAT-REQ-012, migration 0004).
    rv_id, cr_id = new_id(), new_id()
    rv = RuleVersion(rule_version_id=rv_id, obligation_definition_id=d.obligation_definition_id,
                     version=(prev.version + 1) if prev else 1,
                     valid_from=d.valid_from, valid_to=d.valid_to, coverage_mode="ENCODED",
                     rule_schema_version=rules.SCHEMA_VERSION, operator_set_version=rules.OPERATOR_SET_VERSION,
                     canonical_ast=canon, ast_hash=h, required_facts=rep["required_facts"],
                     set_refs=[{"set_version_id": k, "sha256": v} for k, v in set_hashes.items()],
                     source_mappings=d.source_mappings, golden_vectors=d.golden_vectors, status="PUBLISHED",
                     content_release_id=cr_id, draft_id=d.draft_id, record_seq=seq)
    ctx.db.add(rv)
    ctx.db.flush()
    manifest = {"label": body.label, "rule_versions": [{"rule_version_id": rv.rule_version_id, "ast_sha256": h,
                                                       "rule_schema_version": rv.rule_schema_version,
                                                       "operator_set_version": rv.operator_set_version,
                                                       "valid_from": d.valid_from, "valid_to": d.valid_to}],
                "set_refs": rv.set_refs, "golden_vector_result": {"total": vr["total"], "passed": vr["passed"]},
                "reviewers": {"author": d.author_id, "peer": d.peer_reviewer_id, "legal": d.legal_reviewer_id},
                "change_class": d.change_class, "supersedes": prev.rule_version_id if prev else None,
                "rollback_parent": prev.content_release_id if prev else None,
                # ZA-RUL-001 §02: the release is the signed package of the RequirementVersion(s) it rests on, each with
                # the exact source snapshot fingerprint and the provisions linked (FR-REG-001).
                "requirement_versions": binding}
    import json
    manifest = json.loads(canonical_json(manifest))
    mb = canonical_json(manifest)
    cr = ContentRelease(content_release_id=cr_id, label=body.label, manifest=manifest, manifest_hash=sha256_hex(mb),
                        signature=keys.sign(mb),
                        signer_key_id=keys.signer_key_id(), changelog=body.changelog, shadow_summary=d.shadow_report,
                        released_by=ctx.actor.actor_id, cell_received_seq=seq,
                        correction_of_release_id=prev.content_release_id if (prev and d.change_class == "CORRECTION") else None)
    ctx.db.add(cr)
    ctx.db.flush()
    requirements = catalog.publish_bound(ctx, binding, content_release_id=cr_id, record_seq=seq)
    d.status = "PUBLISHED"
    emit(ctx.db, event_type="content_release.published", aggregate_type="ContentRelease", aggregate_id=cr.content_release_id,
         record_seq=seq, payload={"manifest_hash": cr.manifest_hash, "rule_version_id": rv.rule_version_id, "label": body.label})
    if d.change_class == "CORRECTION":
        emit(ctx.db, event_type="content_release.corrected", aggregate_type="ContentRelease",
             aggregate_id=cr.content_release_id, payload={"corrects": cr.correction_of_release_id})
    audit(ctx.db, action="content.publish", object_type="ContentRelease", object_id=cr.content_release_id, actor=ctx.actor,
          new="PUBLISHED", record_seq=seq)
    ctx.db.flush()
    # impact: re-evaluate previously evaluated subjects in their home cell (system service actor)
    odef = ctx.db.get(ObligationDefinition, d.obligation_definition_id)
    reevaluated = 0
    # Tampered content met here fails the whole publication closed (SEC-REQ-048); it is not skipped like other errors.
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=None), \
            system_scope(ctx.db, "rule-release impact re-evaluation in subjects' home cells"):
        appl.verify_rule_integrity(ctx.db, rv)
        subj_ids = {e.subject_id for e in ctx.db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.obligation_definition_id == odef.obligation_definition_id)).scalars()}
        for sid in subj_ids:
            s = ctx.db.get(Subject, sid)
            try:
                with ctx.db.begin_nested():
                    appl.evaluate(ctx.db, tenant_id=s.tenant_id, subject=s, odef=odef, effective_at=datetime.now(timezone.utc),
                                  record_cutoff_seq=None, trigger="RULE_CHANGED", actor_id="system:impact-scheduler")
                reevaluated += 1
            except HTTPException:
                pass
    ctx.db.commit()
    return {"content_release": to_dict(cr), "rule_version": to_dict(rv), "impact_reevaluations": reevaluated,
            "requirement_versions": requirements}


@router.get("/versions")
def versions(ctx: Ctx = Depends(authorize(*DOSSIER_READ, *CONTENT_READERS))):
    out = []
    rows = ctx.db.execute(select(RuleVersion).order_by(RuleVersion.obligation_definition_id, RuleVersion.version)).scalars().all()
    for r in rows:
        x = to_dict(r)
        x["obligation_definition"] = ctx.db.get(ObligationDefinition, r.obligation_definition_id).key
        nxt = next((o for o in rows if o.obligation_definition_id == r.obligation_definition_id and o.version > r.version), None)
        x["superseded_by"] = nxt.rule_version_id if nxt else None
        out.append(x)
    return out


@router.get("/versions/{rv_id}")
def version(rv_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, *CONTENT_READERS))):
    r = ctx.db.get(RuleVersion, rv_id)
    if not r:
        raise HTTPException(404)
    return to_dict(r)


def _index(n, out):
    if isinstance(n, dict) and "node_id" in n:
        out[n["node_id"]] = {k: v for k, v in n.items() if k not in ("children", "args", "child")}
        for k in ("children", "args"):
            for c in n.get(k, []) or []:
                _index(c, out)
        if n.get("child"):
            _index(n["child"], out)
    return out


@router.get("/diff")
def diff(a: str, b: str, ctx: Ctx = Depends(authorize(*READ, *CONTENT_READERS))):
    """Semantic diff by stable node id between RuleVersions or drafts (WF-RUL-03)."""
    def load(x):
        rv = ctx.db.get(RuleVersion, x)
        if rv:
            return rv.canonical_ast, rv.source_mappings, rv.required_facts
        d = ctx.db.get(RuleDraft, x)
        if d:
            ast, _ = _compiled(d)
            return ast, d.source_mappings, sorted(_req(ast))
        raise HTTPException(404, x)
    def _req(n):
        s = set()
        if isinstance(n, dict):
            if n.get("node_type") == "FACT_REF":
                s.add(n["fact"])
            for k in ("children", "args"):
                for c in n.get(k, []) or []:
                    s |= _req(c)
            if n.get("child"):
                s |= _req(n["child"])
        return s
    (aa, asm, areq), (ba, bsm, breq) = load(a), load(b)
    ia, ib = _index(aa, {}), _index(ba, {})
    changed = [{"node_id": k, "before": ia[k], "after": ib[k]} for k in ia.keys() & ib.keys() if ia[k] != ib[k]]
    return {"added": [ib[k] for k in ib.keys() - ia.keys()], "removed": [ia[k] for k in ia.keys() - ib.keys()],
            "changed": changed, "facts_added": sorted(set(breq) - set(areq)), "facts_removed": sorted(set(areq) - set(breq)),
            "source_mappings_changed": asm != bsm}
