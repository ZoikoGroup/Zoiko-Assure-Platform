"""Rule authoring, validation, golden vectors, shadow impact and signed release (ZA-RUL-001 §§10-17)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .. import codes
from ..engines import applicability as appl
from ..engines import catalog, keys, rules
from ..engines.corrections import shadow_summary_for as _shadow_for
from ..models import (ApplicabilityEvaluation, ContentRelease, ObligationDefinition, RuleDraft, RuleVersion, Subject,
                      new_id, utcnow)
from ..platform import audit, canonical_json, emit, next_record_seq, sha256_hex, to_dict
from ..security import CONTENT_READERS, DOSSIER_READ, READ, Ctx, authorize, system_scope

router = APIRouter(prefix="/rules")
CONTENT_STAFF = ("content_author", "content_peer_review", "content_legal_review", "content_release")


# ZA-RUL-001 §14 change classes (the only values a draft may carry; RUL §15 legal approval "approves interpretation and
# change classification"). Which of them need a shadow impact before release is unchanged (publish): the three below may
# publish without one; every other value, including a stored value that is not a §14 class, needs one (fail closed,
# RUL-REQ-020).
CHANGE_CLASSES = ("EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME", "EXECUTABLE_OUTCOME", "TEMPORAL",
                  "DEPENDENCY", "CORRECTION")
SHADOW_OPTIONAL_CHANGE_CLASSES = ("EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME")


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    obligation_definition_id: str
    # The two authoring formats the compiler implements (_compiled); any other value used to be stored and silently
    # compiled as AST (and exceeded catalog_rule_draft.authoring_mode, varchar(32), on PostgreSQL).
    authoring_mode: Literal["AST", "DECISION_TABLE"] = "AST"
    ast: dict | None = None
    decision_table: dict | None = None
    source_mappings: list = []
    golden_vectors: list = []
    valid_from: date
    valid_to: date | None = None
    change_class: Literal["EDITORIAL_METADATA", "SOURCE_MAPPING", "EXECUTABLE_NON_OUTCOME", "EXECUTABLE_OUTCOME",
                          "TEMPORAL", "DEPENDENCY", "CORRECTION"] = "EXECUTABLE_OUTCOME"

    @model_validator(mode="after")
    def _valid_period(self):
        # TIM-REQ-002: canonical half-open [valid_from, valid_to); an empty or inverted period is never effective and
        # would otherwise be signed into a release.
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from (a non-empty half-open valid period)")
        return self


def _compiled(d: RuleDraft) -> tuple[dict | None, list]:
    if d.authoring_mode == "DECISION_TABLE":
        if not d.decision_table:
            return None, [{"code": "RUL_SCHEMA_INVALID", "severity": "ERROR", "message": "decision_table missing"}]
        return rules.compile_decision_table(d.decision_table)
    return d.ast, []


def _validate(ctx: Ctx, d: RuleDraft, *, mappings: bool = True) -> dict:
    """Static validation of the draft's current content (ZA-RUL-001 §11 stages 1-8): rules.validate on the compiled AST
    and, with `mappings`, stage 2 "Reference resolution" of the draft's source mappings (catalog.
    draft_source_mapping_problems(release=False): the §08 fields, IDs, AST references, grounding, requirement version /
    provision existence and relationship, instrument and source fingerprint; whether the cited content is VERIFIED stays
    the publication gate's). Every issue is an ERROR (§18: "Blocks review/release"); a mapping issue
    keeps its RULE_SOURCE_MAPPING_* code and fields and lists the AST nodes citing the mapping. `mappings=False` is for
    the golden-vector workbench (a DRAFT "Can run sandbox vectors only", §10) and for publish, which runs the same
    mapping check as its own RELEASE_CONTENT_NOT_VERIFIED gate."""
    ast, dt_issues = _compiled(d)
    if ast is None:
        return {"ok": False, "issues": dt_issues, "error_count": len(dt_issues)}
    rep = rules.validate(ast, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    # A row issue both report, once; a table limit the compiler already reported is not repeated by the AST check.
    dt_limits = {i.get("limit") for i in dt_issues if i.get("limit")}
    rep["issues"] = dt_issues + [i for i in rep["issues"] if i not in dt_issues and i.get("limit") not in dt_limits]
    if mappings:
        citing: dict[str, list] = {}
        for node, _ in catalog._walk(ast):
            own = node.get("source_mappings")
            for ref in own if isinstance(own, list) else ():
                if isinstance(ref, str):
                    citing.setdefault(ref, []).append(node.get("node_id"))
        for p in catalog.draft_source_mapping_problems(ctx.db, d, ast, release=False):
            node_ids = citing.get(p.get("source_mapping_id"), [])
            rep["issues"].append({"severity": "ERROR", "node_id": node_ids[0] if node_ids else None, **p,
                                  **({"node_ids": node_ids} if node_ids else {})})
        # §08 "the release tooling must show coverage and orphaned mappings": shown, never an issue here (whether an
        # orphaned mapping record blocks publication is an open content-governance decision).
        rep["source_mapping_coverage"] = catalog.coverage_of(ast, d.source_mappings)
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
        x["shadow_report"] = _shadow_for(ctx.actor.tenant_id, d.shadow_report)  # cross-organisation counts (API-001 §17)
        out.append(x)
    return out


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, ctx: Ctx = Depends(authorize(*READ, *CONTENT_READERS))):
    d = ctx.db.get(RuleDraft, draft_id)
    if not d:
        raise HTTPException(404)
    x = to_dict(d)
    x["obligation_definition"] = ctx.db.get(ObligationDefinition, d.obligation_definition_id).key
    x["shadow_report"] = _shadow_for(ctx.actor.tenant_id, d.shadow_report)  # cross-organisation counts (API-001 §17)
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
    # Locked like the approvals: an edit and an approval are ordered, so the review reset below always sees, and clears,
    # an approval committed just before it (unlocked, the reset could miss it and leave an approval of older content).
    d = _locked_draft(ctx, draft_id)
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
    # Locked like edits, review and publish, so the status read below is the committed one (a validation racing a
    # publication waits for it). RUL §10: PUBLISHED is immutable and RELEASE_READY frozen; a validation of either reports
    # on the content as it stands today and never moves it back to an editable state (VALIDATION_FAILED -> DRAFT).
    d = _locked_draft(ctx, draft_id)
    rep = _validate(ctx, d)
    if d.status in ("PUBLISHED", "RELEASE_READY"):
        return rep
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
    if d is None:
        raise HTTPException(404, "rule draft not found")
    rep = _validate(ctx, d, mappings=False)
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
    """Run the exact draft artifact on synthetic fixtures only (WF-RUL-04); no customer data access. Nothing is written.
    A draft over a ZA-RUL-001 §11 complexity limit fails validation (422); should the runtime guard still refuse it,
    the answer is the structured RUL-REQ-029 refusal (EVALUATOR_UNAVAILABLE / RUL_RESOURCE_LIMIT), never an outcome."""
    d = ctx.db.get(RuleDraft, draft_id)
    if d is None:
        raise HTTPException(404, "rule draft not found")
    rep = _validate(ctx, d, mappings=False)
    if not rep["ok"]:
        raise HTTPException(422, {"validation": rep})
    over = rules.resource_limit_violations(rep["compiled_ast"])
    if over:  # before any vector or fixture is evaluated
        raise appl.draft_resource_limit_refusal(d.draft_id, d.revision, over)
    # The fixture is checked as a golden vector's input is (RUL §13 "Typed inputs or deliberate UNKNOWN reason"): a
    # malformed one is refused before it is evaluated, never run with an implicit coercion or a 500.
    problems = rules.vector_input_problems({"facts": body.facts, "effective_at": body.effective_at,
                                            "expected_outcome": "APPLIES"}, appl.load_fact_defs(ctx.db))
    if problems:
        raise HTTPException(422, {"error": problems[0]["code"], "message": "Malformed sandbox fixture",
                                  "validation": {"ok": False, "issues": problems, "error_count": len(problems)}})
    vr = rules.run_golden_vectors(rep["compiled_ast"], [{"vector_id": "sandbox", "facts": body.facts,
                                                         "effective_at": body.effective_at, "expected_outcome": "APPLIES"}],
                                  appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    facts = {}
    for k, v in body.facts.items():
        facts[k] = {"known": False, "reason": v["unknown"]} if isinstance(v, dict) and "unknown" in v else \
            {"known": True, "value": v, "revision_id": "synthetic"}
    try:
        out = rules.evaluate(rep["compiled_ast"], facts,
                             {k: x["data_type"] for k, x in appl.load_fact_defs(ctx.db).items()},
                             appl.load_sets(ctx.db), {"effective_at": body.effective_at})
    except rules.RuleResourceLimitError as e:  # the evaluator's own guard (RUL-REQ-029)
        raise appl.draft_resource_limit_refusal(d.draft_id, d.revision, e.violations) from None
    except rules.RuleError as e:  # invariant breach: a content error, never an outcome (as appl.evaluate)
        raise HTTPException(500, {"error": e.code, "message": e.message, "node_id": e.node_id}) from None
    return {**out, "evaluator_build": codes.EVALUATOR_BUILD, "draft_revision": d.revision, "vector_probe": vr["results"][0]}


@router.post("/drafts/{draft_id}/submit-review")
def submit_review(draft_id: str, ctx: Ctx = Depends(authorize("content_author", platform=True))):
    # Locked like edits and approvals: the content validated here is the content that enters review (rule.review_requested
    # "Exact revision frozen for reviewer"); an edit committing in between would otherwise enter review unvalidated.
    d = _locked_draft(ctx, draft_id)
    # RUL §10: PUBLISHED is immutable (and RELEASE_READY frozen), as update_draft already refuses; §16: no role modifies
    # a published rule. Re-entering review would let the same draft publish a second RuleVersion on the shadow report and
    # peer approval it was first released with.
    if d.status in ("PUBLISHED", "RELEASE_READY"):
        raise HTTPException(409, "Frozen artifact; create a new draft")
    rep = _validate(ctx, d)
    d.validation_report = rep
    if not rep["ok"]:
        d.status = "VALIDATION_FAILED"  # §10: "Static/type/lint error; cannot advance"
        ctx.db.commit()
        raise _validation_refusal(rep, "review")
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


def _locked_draft(ctx: Ctx, draft_id: str) -> RuleDraft:
    """The draft row, locked and re-read (as publish does): a concurrent edit commits before this read or waits until
    this transaction ends, so what is validated below is exactly what the approval is recorded against."""
    d = ctx.db.execute(select(RuleDraft).where(RuleDraft.draft_id == draft_id).with_for_update()
                       .execution_options(populate_existing=True)).scalars().first()
    if d is None:
        raise HTTPException(404, "rule draft not found")
    return d


def _require_valid_for_approval(ctx: Ctx, d: RuleDraft, stage: str) -> None:
    """Revalidate the draft's current content before an approval (ZA-RUL-001 §10: VALIDATION_FAILED "cannot advance";
    the Qualified Legal Reviewer may not "Bypass technical validation"). The draft's status, stored validation report
    and earlier approvals prove nothing about its current content: an IN_REVIEW draft may have been validated by an
    older validator (e.g. before inherits_mapping stopped counting as a mapping, RUL-REQ-008, or before source mappings
    were resolved at review). Same static validation as validate, submit-review and shadow (_validate: the AST and the
    §11 stage 2 source-mapping resolution); a refusal writes nothing and lists every issue with the node_id and, for a
    mapping, the source_mapping_id it concerns."""
    rep = _validate(ctx, d)
    if not rep["ok"]:
        raise _validation_refusal(rep, stage)


def _validation_refusal(rep: dict, stage: str) -> HTTPException:
    """409 {"error": the first ERROR's code (ZA-RUL-001 §18 RUL_* or a RULE_SOURCE_MAPPING_* code), "message",
    "validation": the full report, each issue with its node_id / source_mapping_id}."""
    errors = [i for i in rep["issues"] if i["severity"] == "ERROR"]
    return HTTPException(409, {"error": errors[0]["code"], "message": f"Validation errors block {stage}", "validation": rep})


@router.post("/drafts/{draft_id}/peer-approve")
def peer_approve(draft_id: str, ctx: Ctx = Depends(authorize("content_peer_review", platform=True))):
    d = _locked_draft(ctx, draft_id)
    if d.status != "IN_REVIEW":
        raise HTTPException(409, "Draft not in review")
    if ctx.actor.actor_id == d.author_id:
        raise HTTPException(403, "Separation of duties: author cannot peer-approve")
    _require_valid_for_approval(ctx, d, "peer approval")
    d.peer_reviewer_id = ctx.actor.actor_id
    ctx.db.commit()
    return to_dict(d)


@router.post("/drafts/{draft_id}/legal-approve")
def legal_approve(draft_id: str, ctx: Ctx = Depends(authorize("content_legal_review", human=True, platform=True))):
    d = _locked_draft(ctx, draft_id)
    if d.status != "IN_REVIEW" or not d.peer_reviewer_id:
        raise HTTPException(409, "Requires IN_REVIEW with peer approval")
    if ctx.actor.actor_id in (d.author_id, d.peer_reviewer_id):
        raise HTTPException(403, "Separation of duties")
    _require_valid_for_approval(ctx, d, "legal approval")
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
    """Home-cell shadow execution; content ops receive aggregate buckets only (FR-APP-006).

    Row-locked like edits, approvals and publish (RUL §16: "any edit invalidates approvals/tests"): an edit waits for
    the run and then discards its report, and a publication waits and releases with it. Unlocked, a report computed on
    one revision could be stored on the next, or replace the one a release was already signed with."""
    d = _locked_draft(ctx, draft_id)
    if d.status not in ("VERIFIED", "IN_REVIEW"):
        raise HTTPException(409, "Shadow requires a reviewed candidate")
    rep = _validate(ctx, d)
    if not rep["ok"]:  # §15: only a candidate that passes static validation (incl. source completeness) is shadowed
        raise _validation_refusal(rep, "shadow impact")
    odef = ctx.db.get(ObligationDefinition, d.obligation_definition_id)
    try:
        with system_scope(ctx.db, "shadow impact — aggregate buckets only"):
            d.shadow_report = appl.shadow_impact(ctx.db, odef=odef, candidate_ast=rep["compiled_ast"],
                                                 candidate_required=rep["required_facts"]) | {"completed_at": utcnow().isoformat()}
    except rules.RuleResourceLimitError as e:  # refused before any subject's facts are read (RUL-REQ-029)
        raise appl.draft_resource_limit_refusal(d.draft_id, d.revision, e.violations) from None
    emit(ctx.db, event_type="rule.shadow_completed", aggregate_type="RuleDraft", aggregate_id=draft_id, payload=d.shadow_report)
    ctx.db.commit()
    return d.shadow_report


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    # API-REQ-033: the parser enforces the column size (catalog_content_release.label is varchar(64)), so an over-long
    # label is a 422 before anything is written, not a database error inside the publication transaction after the rule
    # version was written and the manifest signed (and not a silently stored over-long value on SQLite).
    label: str = Field(max_length=64)
    changelog: str
    # ZA-RUL-001 WF-RUL-03: "Correction classification requires explicit correction reason/notice plan". Required for a
    # CORRECTION release, refused for any other; it is signed into the release manifest (the customer correction notice).
    correction_reason: str | None = Field(default=None, max_length=2000)


@router.post("/drafts/{draft_id}/publish")
def publish(draft_id: str, body: PublishIn, ctx: Ctx = Depends(authorize("content_release", human=True, platform=True))):
    """Fail-closed publication gates + signed ContentRelease (§15).

    A CORRECTION release is the customer correction notice (PRD-001 §12, FR-WRK-003, GLS-001): it must correct an
    existing published release of the same obligation definition and state its reason, both signed into the manifest.
    The corrected release is never modified (WRK-REQ-043)."""
    # Row-locked and re-read: two concurrent publications of one draft serialise here, and the second sees PUBLISHED.
    d = ctx.db.execute(select(RuleDraft).where(RuleDraft.draft_id == draft_id).with_for_update()
                       .execution_options(populate_existing=True)).scalars().first()
    if d is None:
        raise HTTPException(404, "rule draft not found")
    if d.status != "VERIFIED":
        raise HTTPException(409, "Only VERIFIED drafts can be released")
    is_correction = d.change_class == "CORRECTION"
    correction_reason = (body.correction_reason or "").strip()
    if is_correction and not correction_reason:
        raise HTTPException(422, {"error": "CORRECTION_REASON_REQUIRED",
                                  "message": "A CORRECTION release needs correction_reason (ZA-RUL-001 WF-RUL-03)."})
    if not is_correction and body.correction_reason is not None:
        raise HTTPException(422, {"error": "CORRECTION_REASON_NOT_APPLICABLE",
                                  "message": f"correction_reason applies only to CORRECTION releases, not {d.change_class}."})
    if ctx.actor.actor_id in (d.author_id, d.peer_reviewer_id, d.legal_reviewer_id):
        raise HTTPException(403, "Separation of duties: release manager must be distinct")
    rep = _validate(ctx, d, mappings=False)  # the mappings are checked below, as the RELEASE_CONTENT_NOT_VERIFIED gate
    if not rep["ok"]:
        missing = [i["message"] for i in rep["issues"] if i.get("code") == "RUL_SET_REF_INVALID"]
        if missing:
            raise HTTPException(409, {"error": "SET_VERSION_MISSING", "message": missing[0]})
        lossy = next((i for i in rep["issues"] if i.get("code") == "RUL_DECIMAL_CANONICALIZATION_LOSS"), None)
        if lossy:  # owner decision: DECIMAL precision-loss guard (its own code, as SET_VERSION_MISSING above)
            raise HTTPException(409, {"error": "RUL_DECIMAL_CANONICALIZATION_LOSS", "node_id": lossy["node_id"],
                                      "message": lossy["message"]})
        raise HTTPException(409, "Validation failure blocks publication")
    # Owner decision "canonicalized-AST golden-vector verification": the mandatory vectors run on the canonical AST that
    # is stored, hashed and signed below (this one object, never re-derived), so the release is the artifact that passed.
    canon = rules.canonicalize(rep["compiled_ast"])
    vr = rules.run_golden_vectors(canon, d.golden_vectors, appl.load_fact_defs(ctx.db), appl.load_sets(ctx.db))
    if not vr["all_passed"]:
        raise HTTPException(409, "Mandatory golden-vector failure blocks publication")
    # Unchanged for the §14 classes (EXECUTABLE_OUTCOME, TEMPORAL, CORRECTION and DEPENDENCY need a shadow impact); a
    # stored class that is not a §14 class (null, or written before the request schema refused it) needs one too, so an
    # unclassified change can never skip the gate (RUL §15, RUL-REQ-020: publication fails closed).
    if d.change_class not in SHADOW_OPTIONAL_CHANGE_CLASSES and not d.shadow_report:
        raise HTTPException(409, "Shadow impact required before publication")
    # RUL §15 "Shadow completion: ... no unexplained runtime invariant breach" and fail-closed "Any SINGLE_MATCH runtime
    # overlap in shadow testing blocks publication" (RUL-REQ-013/020). A subject the candidate could not evaluate is an
    # execution error (applicability.shadow_impact), never "no impact"; no governed disposition of one exists, so a
    # shadow report that carries any blocks the release, whatever the change class.
    shadow_errors = (d.shadow_report or {}).get("execution_errors") or 0
    if shadow_errors:
        raise HTTPException(409, {"error": "SHADOW_IMPACT_INCOMPLETE", "execution_errors": shadow_errors,
                                  "message": "The shadow impact recorded execution errors (runtime invariant "
                                             "breaches); they block publication (ZA-RUL-001 §15)."})
    sets = appl.load_sets(ctx.db)
    missing_sets = [s for s in rep.get("set_refs") or [] if s not in sets]
    if missing_sets:
        raise HTTPException(409, {"error": "SET_VERSION_MISSING", "set_version_id": missing_sets[0],
                                  "message": f"Referenced SetVersion {missing_sets[0]} not found"})
    # INV-06 / INV-09 / FR-REG-001/002 release gate, before anything is written: every provision this release binds
    # belongs to a VERIFIED or PUBLISHED requirement version grounded in an intact source snapshot (app/engines/catalog.py).
    # The definition is row-locked first, the same lock link edits take, so the binding signed below is the committed
    # link set and no link edit can commit alongside this publication (DAT-REQ-012). Then every requirement version and
    # provision the release validates or changes is row-locked and re-read (catalog.lock_release_content), so the
    # release gate and the source-mapping validation below judge exactly the rows that are bound and signed, and no
    # concurrent supersession or withdrawal can change them before this release commits.
    odef_locked = catalog.lock_definition(ctx.db, d.obligation_definition_id)
    catalog.lock_release_content(ctx.db, odef_locked, d)
    binding = catalog.release_binding(ctx.db, odef_locked)
    set_hashes = {s: sets[s]["hash"] for s in rep["set_refs"]}
    catalog.validate_draft_source_mappings(ctx.db, d, canon)
    h = rules.ast_hash(canon, set_hashes)
    prev = ctx.db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == d.obligation_definition_id)
                          .order_by(RuleVersion.version.desc())).scalars().first()
    if is_correction and (prev is None or prev.content_release_id is None):
        raise HTTPException(409, {"error": "CORRECTION_TARGET_MISSING",
                                  "message": "A CORRECTION must correct a published release of this obligation "
                                             "definition; none exists."})
    # Impact targets, read and locked BEFORE the record sequence is taken (lock order: engines/applicability.
    # lock_decisions). The rule scope is held exclusively first, so an evaluation of this definition in progress
    # commits before the targets are read (and is re-evaluated), and none can start until this release commits.
    appl.lock_rule_scope(ctx.db, d.obligation_definition_id, exclusive=True)
    odef = ctx.db.get(ObligationDefinition, d.obligation_definition_id)
    with system_scope(ctx.db, "rule-release impact targeting across subjects' home cells"):
        targets = _impact_targets(ctx.db, odef)
        appl.lock_decisions(ctx.db, [(s.tenant_id, s.subject_id, odef.obligation_definition_id) for s in targets])
        candidates = _unevaluated_candidates(ctx.db, odef)
    seq = next_record_seq(ctx.db)
    # One instant for the whole publication: the version in force before this release, by the selection applicability
    # makes (catalog.effective_rule_version = appl.resolve_rule), is decided on its legal date (Option U, rules.legal_date:
    # the UTC date, never the host's local date), and the impact below re-evaluates at this same instant. Two clock reads
    # could fall either side of a UTC midnight: no rule.superseded while the impact already bound the new version.
    now = datetime.now(timezone.utc)
    today = rules.legal_date(now)
    in_force_before = catalog.effective_rule_version(ctx.db, d.obligation_definition_id, today)
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
                                                       "valid_from": d.valid_from, "valid_to": d.valid_to,
                                                       # ZA-RUL-001 §12: source mappings "remain release-bound" (the
                                                       # records stored on the immutable RuleVersion, signed below)
                                                       **catalog.source_mapping_binding(rv.source_mappings)}],
                # RUL-REQ-017 / §23 G1 "target evaluator build recorded": the build the vectors passed on at release
                "set_refs": rv.set_refs, "golden_vector_result": {"total": vr["total"], "passed": vr["passed"],
                                                                  "evaluator_build": vr["evaluator_build"]},
                "reviewers": {"author": d.author_id, "peer": d.peer_reviewer_id, "legal": d.legal_reviewer_id},
                "change_class": d.change_class, "supersedes": prev.rule_version_id if prev else None,
                "rollback_parent": prev.content_release_id if prev else None,
                # ZA-RUL-001 §02: the release is the signed package of the RequirementVersion(s) it rests on, each with
                # the exact source snapshot fingerprint and the provisions linked (FR-REG-001).
                "requirement_versions": binding}
    if is_correction:
        manifest["correction"] = {"corrects_release_id": prev.content_release_id,
                                  "corrects_rule_version_id": prev.rule_version_id, "reason": correction_reason}
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
    if is_correction:
        # ZA-EVT-001 CONTENT_RELEASE payload profile: release ID, signed manifest hash, rule/requirement IDs, legal valid
        # range, correction/supersession refs.
        emit(ctx.db, event_type="content_release.corrected", aggregate_type="ContentRelease",
             aggregate_id=cr.content_release_id, record_seq=seq,
             payload={"content_release_id": cr.content_release_id, "manifest_hash": cr.manifest_hash,
                      "rule_version_ids": [rv.rule_version_id],
                      "requirement_version_ids": [b["requirement_version_id"] for b in binding],
                      "valid_from": d.valid_from.isoformat(), "valid_to": d.valid_to.isoformat() if d.valid_to else None,
                      "corrects": cr.correction_of_release_id, "supersedes": prev.rule_version_id})
    audit(ctx.db, action="content.publish", object_type="ContentRelease", object_id=cr.content_release_id, actor=ctx.actor,
          new="PUBLISHED", record_seq=seq,
          reason=(f"correction of release {cr.correction_of_release_id} (rule version {prev.rule_version_id}): "
                  f"{correction_reason}") if is_correction else None)
    # ZA-EVT-001 "rule.superseded: Prior RuleVersion no longer current for new binding evaluations". Emitted only when
    # this release changes which version new binding evaluations select today: the new version is now the one in force,
    # and it replaces the version that was in force (not merely the latest-numbered one, which may never have been in
    # force). A future-effective version, or one whose valid period has already ended, leaves the version in force
    # current (TIM-REQ-026); nothing yet records a later activation (content_release.activated is not modelled).
    in_force_now = catalog.effective_rule_version(ctx.db, d.obligation_definition_id, today)
    if (in_force_before is not None and in_force_now is not None
            and in_force_now.rule_version_id == rv.rule_version_id != in_force_before.rule_version_id):
        emit(ctx.db, event_type="rule.superseded", aggregate_type="RuleVersion", aggregate_id=in_force_before.rule_version_id,
             record_seq=seq, payload={"rule_version_id": in_force_before.rule_version_id,
                                      "superseded_by_rule_version_id": rv.rule_version_id,
                                      "content_release_id": cr.content_release_id, "obligation_definition_id": odef.obligation_definition_id,
                                      "effective_from": d.valid_from.isoformat()})
    ctx.db.flush()
    # Impact: re-evaluate the targeted subjects in their home cells (system service actor). Tampered content met here
    # fails the whole publication closed (SEC-REQ-048); it is not skipped like other errors.
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=None), \
            system_scope(ctx.db, "rule-release impact re-evaluation in subjects' home cells"):
        appl.verify_rule_integrity(ctx.db, rv)
        impact = _reevaluate_targets(ctx.db, odef, rv, targets, candidates, now)
    # The impact outcome is part of the release record: an incomplete impact is never left as "no impact" (ZA-RUL-001 §15).
    audit(ctx.db, action="content.impact", object_type="ContentRelease", object_id=cr.content_release_id, actor=ctx.actor,
          new=impact["status"], record_seq=seq, reason=_impact_summary(impact))
    ctx.db.commit()
    return {"content_release": to_dict(cr), "rule_version": to_dict(rv), "impact_reevaluations": impact["evaluated"],
            "impact": impact, "requirement_versions": requirements}


# ---------------------------------------------------------------- publication impact (APP §10, RUL §14/§15, TIM §17)
def _impact_targets(db, odef: ObligationDefinition) -> list:
    """Subjects already evaluated against the definition, in every organisation: the confirmed re-evaluation targets
    (APP §10 "RuleVersion published/corrected"; TIM §17 "RuleVersion/content correction: evaluations/determinations/
    obligations for affected obligation definition"). Sorted, so their locks follow the global order."""
    ids = sorted({sid for sid in db.execute(select(ApplicabilityEvaluation.subject_id).where(
        ApplicabilityEvaluation.obligation_definition_id == odef.obligation_definition_id)).scalars()})
    return [db.get(Subject, sid) for sid in ids]


def _unevaluated_candidates(db, odef: ObligationDefinition) -> int:
    """How many subjects of the definition's subject type have never been evaluated against it. APP §10 targets
    subjects "currently or prospectively in scope ... based on indexed coarse selectors; exact result only after engine
    run": they are potential impact. They are counted, not evaluated, here (no subject is evaluated for the first time
    by a content release)."""
    from sqlalchemy import func
    evaluated = select(ApplicabilityEvaluation.subject_id).where(
        ApplicabilityEvaluation.obligation_definition_id == odef.obligation_definition_id)
    return db.execute(select(func.count()).select_from(Subject).where(
        Subject.subject_type == odef.subject_type, Subject.subject_id.not_in(evaluated))).scalar_one()


def _reevaluate_targets(db, odef, rv: RuleVersion, targets: list, candidates: int, at: datetime) -> dict:
    """Re-evaluate every target at the publication's instant `at` and classify the result in aggregate buckets only:
    APP-REQ-014 (routine impact output to content operations carries no organisation, subject or fact values) and RUL
    §15 impact review ("APPLIES/DNP changes, new UNCERTAINs and adopted-determination conflicts"). A target that could
    not be evaluated is counted as failed with its error code, and the impact is INCOMPLETE, never "no impact" (RUL §15)."""
    out = {"rule_version_id": rv.rule_version_id, "targeted": len(targets), "evaluated": 0, "unchanged": 0,
           "transitions": {}, "new_uncertain": 0, "adopted_determination_conflicts": 0, "not_yet_effective": 0,
           "failed": {}, "potential_unevaluated_subjects": candidates}
    for s in targets:
        prior = appl.latest_evaluation(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        before = appl.current_determination(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        try:
            with db.begin_nested():
                ev = appl.evaluate(db, tenant_id=s.tenant_id, subject=s, odef=odef, effective_at=at,
                                   record_cutoff_seq=None, trigger="RULE_CHANGED", actor_id="system:impact-scheduler")
        except HTTPException as e:
            code = e.detail.get("error") if isinstance(e.detail, dict) else None
            code = code or f"HTTP_{e.status_code}"
            out["failed"][code] = out["failed"].get(code, 0) + 1
            continue
        out["evaluated"] += 1
        if ev.rule_version_id != rv.rule_version_id:
            out["not_yet_effective"] += 1  # the new version is not in force at this instant (TIM-REQ-026)
            continue
        if prior is None or prior.outcome == ev.outcome:
            out["unchanged"] += 1
        else:
            key = f"{prior.outcome}->{ev.outcome}"
            out["transitions"][key] = out["transitions"].get(key, 0) + 1
            if ev.outcome == "UNCERTAIN":
                out["new_uncertain"] += 1
        after = appl.current_determination(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        if after is not None and after.state == "ADOPTED" and after.needs_review and (
                before is None or before.revision_id != after.revision_id):
            out["adopted_determination_conflicts"] += 1
    out["status"] = "INCOMPLETE" if out["failed"] else "COMPLETE"
    return out


def _impact_summary(i: dict) -> str:
    return (f"rule version {i['rule_version_id']}: targeted {i['targeted']}; evaluated {i['evaluated']}; not yet effective "
            f"{i['not_yet_effective']}; unchanged {i['unchanged']}; transitions {i['transitions']}; new UNCERTAIN "
            f"{i['new_uncertain']}; adopted-determination conflicts {i['adopted_determination_conflicts']}; failed "
            f"{i['failed']}; never-evaluated candidates {i['potential_unevaluated_subjects']}")[:2000]


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
