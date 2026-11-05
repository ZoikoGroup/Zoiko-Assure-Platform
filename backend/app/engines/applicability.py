"""Applicability Engine coordinator (ZA-APP-001).

request -> resolve subject -> resolve facts (bitemporal) -> freeze EvaluationInputBundle
-> pure evaluator -> atomic persistence (evaluation + trace + derived records + outbox).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import codes
from ..db import CELL_ID
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, ContentRelease,
                      FactDefinition, FactRequest, Issue, ObligationDefinition, ObligationInstance, RuleVersion,
                      SatisfactionExpressionVersion, SetVersion, Subject, new_id)
from ..platform import audit, canonical_json, digest, emit, ensure_utc, next_record_seq, sha256_hex
from . import rules
from .facts import resolve_fact
from .locks import xact_lock

ATTESTATION_STATEMENT_V = "ATT-STMT-1.0"
log = logging.getLogger(__name__)


def load_fact_defs(db: Session) -> dict[str, dict]:
    out = {}
    for fd in db.execute(select(FactDefinition)).scalars():
        out[fd.key] = {"fact_definition_id": fd.fact_definition_id, "data_type": fd.data_type,
                       "enum_domain": fd.enum_domain, "status": fd.status, "sensitivity": fd.sensitivity,
                       "subject_type": fd.subject_type, "label": fd.label}
    return out


def load_sets(db: Session) -> dict[str, dict]:
    return {s.set_version_id: {"element_type": s.element_type, "members": s.members, "hash": s.canonical_hash}
            for s in db.execute(select(SetVersion)).scalars()}


def resolve_rule(db: Session, odef: ObligationDefinition, effective: date, cutoff_seq: int) -> RuleVersion:
    published = select(RuleVersion).where(RuleVersion.obligation_definition_id == odef.obligation_definition_id,
                                          RuleVersion.status == "PUBLISHED", RuleVersion.record_seq <= cutoff_seq)
    effective_now = (RuleVersion.valid_from <= effective) & or_(RuleVersion.valid_to.is_(None), RuleVersion.valid_to > effective)
    rv = db.execute(published.where(effective_now).order_by(RuleVersion.version.desc())).scalars().first()
    # The effective period is read from the mutable row. A newer PUBLISHED version passed over because its period does
    # not cover `effective` is skipped only if that period is the one its signed release declares; otherwise its row was
    # altered below the application and binding an older version would be a silent fallback (Task 0; APP-REQ-006,
    # SEC-REQ-048). verify_rule_integrity raises RuleIntegrityError, which fails the operation closed.
    newer = published.where(~effective_now)
    if rv is not None:
        newer = newer.where(RuleVersion.version > rv.version)
    for skipped in db.execute(newer.order_by(RuleVersion.version.desc())).scalars():
        verify_rule_integrity(db, skipped)
    if rv is None:
        raise HTTPException(409, {"error": "RULE_NOT_EFFECTIVE",
                                  "message": f"No PUBLISHED RuleVersion for {odef.key} effective {effective} at record seq {cutoff_seq}"})
    if rv.coverage_mode != "ENCODED" or odef.coverage_mode != "ENCODED":
        raise HTTPException(409, {"error": "COVERAGE_NOT_ENCODED", "message": "MONITORING_ONLY content cannot bind applicability"})
    return rv


# ---------------------------------------------------------------- rule integrity before input freeze (ZA-APP-001 §13)
class RuleIntegrityError(Exception):
    """A binding RuleVersion failed integrity verification: RUL_SIGNATURE_INVALID (ZA-RUL-001 §18). Deliberately not an
    HTTPException, so no caller can absorb it as an ordinary evaluation error: the operation fails closed (SEC-REQ-048),
    with no fallback to another RuleVersion and never UNCERTAIN (APP-REQ-019). See integrity_fail_closed."""
    code = "RUL_SIGNATURE_INVALID"

    def __init__(self, rv: RuleVersion, check: str, artifact_id: str):
        super().__init__(f"{self.code}: {check} ({artifact_id}, rule version {rv.rule_version_id})")
        self.check = check
        self.artifact_id = artifact_id  # the artifact that failed: the RuleVersion, a SetVersion or the release
        self.rule_version_id = rv.rule_version_id
        self.content_release_id = rv.content_release_id


def _integrity_failure(db: Session, rv: RuleVersion) -> tuple[str, str] | None:
    """(check, failing artifact ID) for the first failed check, or None."""
    pins = {}
    for ref in rv.set_refs or []:  # 1. each pinned SetVersion still hashes to its pin (RUL-REQ-014)
        sv = db.get(SetVersion, ref["set_version_id"])
        if sv is None:
            return "SET_VERSION_MISSING", ref["set_version_id"]
        h = rules.set_hash(sv.element_type, sv.members)
        if h != ref["sha256"] or h != sv.canonical_hash:
            return "SET_HASH_MISMATCH", sv.set_version_id
        pins[sv.set_version_id] = h
    # 2. the authoritative AST hash, recomputed under the declared versions (ZA-RUL-001 §12; RUL-REQ-016)
    if rules.ast_hash(rv.canonical_ast, pins, rule_schema_version=rv.rule_schema_version,
                      operator_set_version=rv.operator_set_version) != rv.ast_hash:
        return "AST_HASH_MISMATCH", rv.rule_version_id
    # 3. the release the RuleVersion names: PUBLISHED, signed, manifest hash recomputes (RUL-REQ-027)
    cr = db.get(ContentRelease, rv.content_release_id)
    if cr is None or cr.status != "PUBLISHED":
        return "RELEASE_NOT_PUBLISHED", rv.content_release_id
    if sha256_hex(canonical_json(cr.manifest)) != cr.manifest_hash:
        return "MANIFEST_HASH_MISMATCH", cr.content_release_id
    if not cr.signature:
        return "RELEASE_UNSIGNED", cr.content_release_id
    # 4. the manifest covers exactly this RuleVersion: ID, AST hash, every field it declares, the same SetVersion pins
    entries = [e for e in cr.manifest["rule_versions"] if e["rule_version_id"] == rv.rule_version_id]
    if len(entries) != 1 or entries[0]["ast_sha256"] != rv.ast_hash:
        return "RELEASE_DOES_NOT_COVER_RULE_VERSION", cr.content_release_id
    declared = {"rule_schema_version": rv.rule_schema_version, "operator_set_version": rv.operator_set_version,
                "valid_from": rv.valid_from.isoformat(), "valid_to": rv.valid_to.isoformat() if rv.valid_to else None}
    if any(k in entries[0] and entries[0][k] != v for k, v in declared.items()):
        return "RELEASE_ENTRY_MISMATCH", cr.content_release_id
    pinned = sorted((r["set_version_id"], r["sha256"]) for r in cr.manifest.get("set_refs") or [])
    if pinned != sorted(pins.items()):
        return "RELEASE_SET_PINS_MISMATCH", cr.content_release_id
    return None


def verify_rule_integrity(db: Session, rv: RuleVersion) -> None:
    """APP-REQ-006 / RUL-REQ-027 hash and release binding of a binding RuleVersion, checked before input freeze.
    Malformed released content fails the same way. The release signature must be present but is NOT verified: that needs
    the content-release trust decision (A9b; docs/backend-decision-brief-content-signature-trust.md)."""
    try:
        failure = _integrity_failure(db, rv)
    except (KeyError, TypeError, AttributeError, ValueError):
        failure = "MALFORMED_CONTENT", rv.rule_version_id
    if failure:
        raise RuleIntegrityError(rv, *failure)


def record_integrity_failure(db: Session, err: RuleIntegrityError, *, actor, tenant_id: str | None) -> None:
    """SEC-REQ-048's security signal: `security.content_signature_failed` (ZA-SEC-001 §23; ZA-EVT-001 §19,
    SECURITY_CRITICAL). The failed operation's transaction is rolled back first, so nothing it wrote survives. The
    verification result is then committed on its own, in the same session and role: an audit record plus the event in
    the transactional outbox (ZA-EVT-001: security-critical events originate from the outbox, tied to committed security
    state). No second connection, owner credential or other store is involved."""
    db.rollback()
    seq = next_record_seq(db)
    audit(db, action="content.verify", object_type="RuleVersion", object_id=err.rule_version_id, actor=actor,
          tenant_id=tenant_id, new=err.code, reason=f"{err.check}: {err.artifact_id}", record_seq=seq)
    emit(db, event_type="security.content_signature_failed", aggregate_type="ContentRelease",
         aggregate_id=err.content_release_id, tenant_id=tenant_id, record_seq=seq,
         payload={"content_release_id": err.content_release_id, "rule_version_id": err.rule_version_id,
                  "artifact_id": err.artifact_id, "cell_id": CELL_ID, "verification_result": err.check})
    db.commit()


@contextmanager
def integrity_fail_closed(db: Session, *, actor, tenant_id: str | None):
    """Wraps every request path that evaluates a binding RuleVersion. On RUL_SIGNATURE_INVALID the whole operation fails
    closed: its transaction is rolled back, the security signal is committed, and the caller gets an error, never a
    partial or fallback result. The refusal does not depend on the signal being recorded."""
    try:
        yield
    except RuleIntegrityError as err:
        log.error("%s: %s on %s for rule version %s (content release %s); evaluation refused",
                  err.code, err.check, err.artifact_id, err.rule_version_id, err.content_release_id)
        try:
            record_integrity_failure(db, err, actor=actor, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001 - still refuse; the failure to record is logged
            db.rollback()
            log.exception("could not record security.content_signature_failed for rule version %s", err.rule_version_id)
        raise HTTPException(500, {"error": err.code, "rule_version_id": err.rule_version_id,
                                  "message": "Released rule content failed integrity verification; the evaluation "
                                             "was refused."}) from None


def _display(fd: dict, value):
    if fd["sensitivity"] in ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"):
        return "••• (redacted)"
    return value


def evaluate(db: Session, *, tenant_id: str, subject: Subject, odef: ObligationDefinition,
             effective_at: datetime, record_cutoff_seq: int | None, trigger: str, actor_id: str,
             rule_version: RuleVersion | None = None, persist: bool = True) -> ApplicabilityEvaluation | dict:
    if odef.subject_type != subject.subject_type:
        raise HTTPException(422, f"{odef.key} applies to subject type {odef.subject_type}, not {subject.subject_type}")
    effective_at = ensure_utc(effective_at)
    if record_cutoff_seq is None:
        from ..platform import current_watermark
        record_cutoff_seq = current_watermark(db)["record_seq"]
    rv = rule_version or resolve_rule(db, odef, effective_at.date(), record_cutoff_seq)
    verify_rule_integrity(db, rv)  # before input freeze (ZA-APP-001 §13); raises RuleIntegrityError
    fact_defs = load_fact_defs(db)
    sets = load_sets(db)
    fdmodels = {fd.key: fd for fd in db.execute(select(FactDefinition)).scalars()}

    facts, fact_inputs, unknowns = {}, [], []
    for key in rv.required_facts:
        fd = fdmodels.get(key)
        if fd is None:
            raise HTTPException(409, {"error": "SCHEMA_INCOMPATIBLE", "message": f"FactDefinition {key} unavailable"})
        r = resolve_fact(db, tenant_id=tenant_id, subject=subject, fd=fd, effective_at=effective_at,
                         known_seq=record_cutoff_seq, create_conflicts=persist)
        if r["known"]:
            facts[key] = {"known": True, "value": r["value"], "revision_id": r["revision_id"],
                          "display": _display(fact_defs[key], r["value"])}
            fact_inputs.append({"fact_definition": key, "fact_assertion_revision_id": r["revision_id"],
                                "typed_value_hash": digest({"v": r["value"]}), "provenance_class": r["provenance_class"]})
        else:
            facts[key] = {"known": False, "reason": r["reason"]}
            unknowns.append({"fact_definition": key, "reason_code": r["reason"]})

    bundle = {"tenant_id": tenant_id, "subject_ref": {"type": subject.subject_type, "id": subject.subject_id},
              "obligation_definition_id": odef.obligation_definition_id, "rule_version_id": rv.rule_version_id,
              "rule_ast_hash": rv.ast_hash, "effective_at": effective_at, "record_cutoff_seq": record_cutoff_seq,
              "fact_inputs": sorted(fact_inputs, key=lambda x: x["fact_definition"]),
              "unknown_facts": sorted(unknowns, key=lambda x: x["fact_definition"]),
              "evaluator_build": codes.EVALUATOR_BUILD}
    bundle_hash = digest(bundle)
    eval_key = digest({k: bundle[k] for k in ("subject_ref", "obligation_definition_id", "rule_version_id",
                                               "fact_inputs", "unknown_facts", "evaluator_build")}
                      | {"effective_date": effective_at.date().isoformat()})
    try:
        out = rules.evaluate(rv.canonical_ast, facts, {k: v["data_type"] for k, v in fact_defs.items()},
                             sets, {"effective_at": effective_at.date().isoformat()})
    except rules.RuleError as e:  # invariant breach is an infrastructure/content error, never UNCERTAIN
        raise HTTPException(500, {"error": e.code, "message": e.message, "node_id": e.node_id})

    if not persist:
        return {"outcome": out["outcome"], "trace": out["trace"], "missing_facts": out["missing_facts"],
                "rule_version_id": rv.rule_version_id, "input_bundle_hash": bundle_hash}

    existing = db.execute(select(ApplicabilityEvaluation).where(
        ApplicabilityEvaluation.tenant_id == tenant_id,
        ApplicabilityEvaluation.evaluation_key == eval_key)).scalars().first()
    if existing:
        return existing  # idempotent: identical inputs collapse to one authoritative evaluation

    seq = next_record_seq(db)
    ev = ApplicabilityEvaluation(
        tenant_id=tenant_id, entity_id=subject.entity_id, subject_id=subject.subject_id,
        obligation_definition_id=odef.obligation_definition_id, rule_version_id=rv.rule_version_id,
        effective_at=effective_at, record_cutoff_seq=record_cutoff_seq, evaluator_build=codes.EVALUATOR_BUILD,
        input_bundle=digest_safe(bundle), input_bundle_hash=bundle_hash, evaluation_key=eval_key,
        outcome=out["outcome"], trace=out["trace"], missing_facts=out["missing_facts"],
        non_material_unknowns=out["non_material_unknowns"], trigger=trigger, requested_by=actor_id, record_seq=seq)
    db.add(ev)
    db.flush()
    emit(db, event_type="applicability.evaluated", aggregate_type="ApplicabilityEvaluation",
         aggregate_id=ev.evaluation_id, tenant_id=tenant_id, entity_id=subject.entity_id, record_seq=seq,
         payload={"subject_ref": bundle["subject_ref"], "obligation_definition_id": odef.obligation_definition_id,
                  "outcome": ev.outcome, "rule_version_id": rv.rule_version_id, "effective_at": effective_at,
                  "trigger": trigger})
    _derive(db, ev, subject, odef, seq, actor_id)
    return ev


def digest_safe(obj):
    import json
    from ..platform import canonical_json
    return json.loads(canonical_json(obj))


def current_determination(db: Session, tenant_id: str, subject_id: str, odef_id: str) -> ApplicabilityDetermination | None:
    return db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == tenant_id, ApplicabilityDetermination.subject_id == subject_id,
        ApplicabilityDetermination.obligation_definition_id == odef_id,
        ApplicabilityDetermination.record_to_seq.is_(None)).order_by(
        ApplicabilityDetermination.record_from_seq.desc())).scalars().first()


def current_determinations(db: Session, tenant_id: str, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], ApplicabilityDetermination]:
    """current_determination() for many (subject_id, obligation_definition_id) pairs in one query (list views)."""
    out: dict[tuple[str, str], ApplicabilityDetermination] = {}
    if not pairs:
        return out
    for d in db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == tenant_id,
            ApplicabilityDetermination.subject_id.in_({s for s, _ in pairs}),
            ApplicabilityDetermination.record_to_seq.is_(None)).order_by(
            ApplicabilityDetermination.record_from_seq.desc())).scalars():
        key = (d.subject_id, d.obligation_definition_id)
        if key in pairs:
            out.setdefault(key, d)
    return out


def _new_det_revision(db, prev: ApplicabilityDetermination | None, *, tenant_id, subject_id, odef_id, evaluation,
                      state, seq, actor_id, basis=None, needs_review=False, review_reason=None):
    if prev is not None:
        prev.record_to_seq = seq
    rev = ApplicabilityDetermination(
        lineage_id=prev.lineage_id if prev else new_id(), tenant_id=tenant_id, subject_id=subject_id,
        obligation_definition_id=odef_id, evaluation_id=evaluation.evaluation_id, outcome=evaluation.outcome,
        state=codes.assert_code("determination_state", state), basis=basis, needs_review=needs_review,
        review_reason=review_reason, valid_from=evaluation.effective_at, record_from_seq=seq, actor_id=actor_id)
    db.add(rev)
    db.flush()
    return rev


def _derive(db: Session, ev: ApplicabilityEvaluation, subject: Subject, odef: ObligationDefinition, seq: int, actor_id: str):
    cur = current_determination(db, ev.tenant_id, subject.subject_id, odef.obligation_definition_id)
    if ev.outcome == "UNCERTAIN":
        _fact_requests(db, ev, subject, odef)
        if cur and cur.state == "ADOPTED":
            _flag_review(db, cur, ev, seq, actor_id, "New evaluation is UNCERTAIN against adopted determination.")
        emit(db, event_type="applicability.uncertain_raised", aggregate_type="ApplicabilityEvaluation",
             aggregate_id=ev.evaluation_id, tenant_id=ev.tenant_id, record_seq=seq,
             payload={"missing_facts": ev.missing_facts})
        return
    if cur is None:
        rev = _new_det_revision(db, None, tenant_id=ev.tenant_id, subject_id=subject.subject_id,
                                odef_id=odef.obligation_definition_id, evaluation=ev, state="PROPOSED", seq=seq,
                                actor_id=actor_id)
        emit(db, event_type="determination.proposed", aggregate_type="ApplicabilityDetermination",
             aggregate_id=rev.lineage_id, tenant_id=ev.tenant_id, record_seq=seq,
             payload={"evaluation_id": ev.evaluation_id, "outcome": ev.outcome, "material": odef.material})
        return
    if cur.state in ("PROPOSED", "IN_REVIEW", "REJECTED"):
        _new_det_revision(db, cur, tenant_id=ev.tenant_id, subject_id=subject.subject_id,
                          odef_id=odef.obligation_definition_id, evaluation=ev, state="PROPOSED", seq=seq,
                          actor_id=actor_id, basis="Refreshed by newer evaluation")
        return
    if cur.state == "ADOPTED" and cur.outcome != ev.outcome:
        _flag_review(db, cur, ev, seq, actor_id,
                     f"New evaluation outcome {ev.outcome} differs from adopted {cur.outcome}.")
    if ev.outcome != cur.outcome:
        emit(db, event_type="applicability.changed", aggregate_type="ApplicabilityEvaluation",
             aggregate_id=ev.evaluation_id, tenant_id=ev.tenant_id, record_seq=seq,
             payload={"prior_evaluation_id": cur.evaluation_id, "prior_outcome": cur.outcome, "new_outcome": ev.outcome})


def _flag_review(db, cur, ev, seq, actor_id, reason):
    if cur.needs_review and cur.review_reason == reason:
        return
    rev = ApplicabilityDetermination(
        lineage_id=cur.lineage_id, tenant_id=cur.tenant_id, subject_id=cur.subject_id,
        obligation_definition_id=cur.obligation_definition_id, evaluation_id=cur.evaluation_id, outcome=cur.outcome,
        state=cur.state, basis=cur.basis, needs_review=True, review_reason=reason + f" (evaluation {ev.evaluation_id})",
        valid_from=cur.valid_from, record_from_seq=seq, actor_id=actor_id)
    cur.record_to_seq = seq
    db.add(rev)
    db.add(Issue(tenant_id=cur.tenant_id, title="Adopted determination requires review", issue_type="DETERMINATION_REVIEW",
                 severity="HIGH", priority="P1", source_ref_type="ApplicabilityEvaluation", source_ref_id=ev.evaluation_id,
                 dedupe_key=f"detreview:{cur.lineage_id}:{ev.evaluation_id}"))


def _fact_requests(db: Session, ev, subject, odef):
    fds = {fd.key: fd for fd in db.execute(select(FactDefinition)).scalars()}
    reasons = {u["fact_definition"]: u["reason_code"] for u in ev.input_bundle.get("unknown_facts", [])}
    for key in ev.missing_facts:
        fd = fds[key]
        from .facts import fact_subject_for
        holder = fact_subject_for(db, subject, fd) or subject
        fr = db.execute(select(FactRequest).where(FactRequest.tenant_id == ev.tenant_id,
                                                  FactRequest.subject_id == holder.subject_id,
                                                  FactRequest.fact_definition_id == fd.fact_definition_id,
                                                  FactRequest.status.in_(["OPEN", "IN_PROGRESS"]))).scalars().first()
        if fr:
            if ev.evaluation_id not in fr.linked_evaluation_ids:
                fr.linked_evaluation_ids = fr.linked_evaluation_ids + [ev.evaluation_id]
            continue
        days = 10 if odef.material else 30
        db.add(FactRequest(tenant_id=ev.tenant_id, subject_id=holder.subject_id, fact_definition_id=fd.fact_definition_id,
                           reason_code=reasons.get(key, "MISSING_REQUIRED_FACT"), owner_role=fd.owner_role,
                           due_at=datetime.now(timezone.utc) + timedelta(days=days),
                           linked_evaluation_ids=[ev.evaluation_id]))
        emit(db, event_type="fact.requested", aggregate_type="FactRequest", aggregate_id=holder.subject_id,
             tenant_id=ev.tenant_id, payload={"fact_definition": key, "reason_code": reasons.get(key)})


def attest(db: Session, *, ctx, determination_lineage_id: str, decision: str, authority_basis: str,
           reason: str | None = None) -> dict:
    """Customer adoption / rejection (FR-APP-004, INV-01, DOM-INV-03/04)."""
    ctx.require_human("Determination adoption")
    ctx.require("attest")

    def current():
        return db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == ctx.tenant,
            ApplicabilityDetermination.lineage_id == determination_lineage_id,
            ApplicabilityDetermination.record_to_seq.is_(None))
            .execution_options(populate_existing=True)).scalars().first()
    cur = current()
    if not cur:
        raise HTTPException(404, "Determination not found")
    # Audit finding F-05 (DOM §13: one obligation per adopted determination and canonical scope). Concurrent
    # attestations of one subject + obligation definition are serialised by a database-held lock taken before anything
    # is validated, then the determination is read again: a second ADOPT that waited sees the first one's committed
    # revision and is refused (409) instead of creating a second obligation lineage. A partial unique index on current
    # revisions would add a constraint-level guarantee; it is a schema change awaiting review
    # (docs/backend-remediation-report.md), so none is created here.
    xact_lock(db, "obligation", ctx.tenant, cur.subject_id, cur.obligation_definition_id)
    cur = current()
    if not cur:
        raise HTTPException(404, "Determination not found")
    if cur.state not in ("PROPOSED", "IN_REVIEW") and not (cur.state == "ADOPTED" and cur.needs_review):
        raise HTTPException(409, f"Determination in state {cur.state} cannot be attested")
    ev = db.get(ApplicabilityEvaluation, cur.evaluation_id)
    if ev.outcome == "UNCERTAIN":
        raise HTTPException(409, "UNCERTAIN cannot be adopted (DOM-INV-03); resolve facts first")
    # stale-revision protection: the backing evaluation must be the latest for this subject/definition
    latest = db.execute(select(ApplicabilityEvaluation).where(
        ApplicabilityEvaluation.tenant_id == ctx.tenant, ApplicabilityEvaluation.subject_id == cur.subject_id,
        ApplicabilityEvaluation.obligation_definition_id == cur.obligation_definition_id).order_by(
        ApplicabilityEvaluation.record_seq.desc())).scalars().first()
    if latest and latest.evaluation_id != ev.evaluation_id:
        raise HTTPException(409, "A newer evaluation exists; re-review before attesting")
    if decision == "REJECT" and not reason:
        raise HTTPException(422, "Rejection reason is mandatory")
    subject = db.get(Subject, cur.subject_id)
    odef = db.get(ObligationDefinition, cur.obligation_definition_id)
    seq = next_record_seq(db)
    statement = (f"I, {ctx.actor.display_name}, acting under {authority_basis}, "
                 f"{'adopt' if decision == 'ADOPT' else 'decline to adopt'} the applicability determination "
                 f"({ev.outcome}) for {subject.display_name} against {odef.key} for the stated valid-time scope, "
                 f"based on ApplicabilityEvaluation {ev.evaluation_id} and the facts recorded in that evaluation. "
                 f"I understand that later facts or regulatory-content corrections may require re-evaluation.")
    att = AdoptionAttestation(tenant_id=ctx.tenant, target_type="ApplicabilityDetermination",
                              target_revision_id=cur.revision_id, actor_id=ctx.actor.actor_id,
                              authority_basis=authority_basis, statement_version=ATTESTATION_STATEMENT_V,
                              statement=statement, decision="ATTESTED" if decision == "ADOPT" else "DECLINED",
                              record_seq=seq)
    db.add(att)
    db.flush()
    new_state = "ADOPTED" if decision == "ADOPT" else "REJECTED"
    rev = _new_det_revision(db, cur, tenant_id=ctx.tenant, subject_id=cur.subject_id,
                            odef_id=cur.obligation_definition_id, evaluation=ev, state=new_state, seq=seq,
                            actor_id=ctx.actor.actor_id, basis=reason or "Customer attestation")
    audit(db, action="determination.attest", object_type="ApplicabilityDetermination", object_id=rev.lineage_id,
          actor=ctx.actor, tenant_id=ctx.tenant, prior=cur.state, new=new_state, reason=reason, record_seq=seq)
    obligation = None
    if new_state == "ADOPTED":
        emit(db, event_type="determination.adopted", aggregate_type="ApplicabilityDetermination",
             aggregate_id=rev.lineage_id, tenant_id=ctx.tenant, actor=ctx.actor_ref(), record_seq=seq,
             payload={"determination_revision_id": rev.revision_id, "attestation_id": att.attestation_id,
                      "authority_basis": authority_basis, "outcome": ev.outcome})
        if ev.outcome == "APPLIES":
            obligation = _activate_obligation(db, rev, att, subject, odef, seq, ctx.actor.actor_id)
        else:
            _close_obligation(db, ctx.tenant, subject.subject_id, odef.obligation_definition_id, seq, rev.valid_from)
    return {"determination": rev, "attestation": att, "obligation": obligation}


def _close_obligation(db, tenant_id, subject_id, odef_id, seq, at):
    cur = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant_id,
                                                      ObligationInstance.subject_id == subject_id,
                                                      ObligationInstance.obligation_definition_id == odef_id,
                                                      ObligationInstance.record_to_seq.is_(None))).scalars().first()
    if cur and cur.lifecycle == "ADOPTED":
        cur.record_to_seq = seq
        db.add(ObligationInstance(lineage_id=cur.lineage_id, tenant_id=tenant_id, entity_id=cur.entity_id,
                                  subject_id=subject_id, obligation_definition_id=odef_id,
                                  determination_revision_id=cur.determination_revision_id, attestation_id=cur.attestation_id,
                                  owner_actor_id=cur.owner_actor_id, lifecycle="CLOSED", valid_from=cur.valid_from,
                                  valid_to=at, record_from_seq=seq))


def _activate_obligation(db, det, att, subject, odef, seq, actor_id) -> ObligationInstance:
    cur = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == det.tenant_id,
                                                      ObligationInstance.subject_id == subject.subject_id,
                                                      ObligationInstance.obligation_definition_id == odef.obligation_definition_id,
                                                      ObligationInstance.record_to_seq.is_(None))).scalars().first()
    lineage = cur.lineage_id if cur else new_id()
    if cur:
        cur.record_to_seq = seq
    # Valid time of the new revision (audit finding F-06; DOM §9.2, TIM-REQ-007/008). Re-attesting an obligation that
    # is still ADOPTED keeps its start. Re-adopting a CLOSED one must not reach back over the closure: it starts at the
    # adopted determination's valid_from, and never before the closed revision's end, so the closed interval stays out
    # of the active period. The closed revision itself stays in record-time history, unchanged.
    if cur is None:
        valid_from = det.valid_from
    elif cur.lifecycle == "CLOSED":
        valid_from = max(ensure_utc(det.valid_from), ensure_utc(cur.valid_to or cur.valid_from))
    else:
        valid_from = cur.valid_from
    oi = ObligationInstance(lineage_id=lineage, tenant_id=det.tenant_id, entity_id=subject.entity_id,
                            subject_id=subject.subject_id, obligation_definition_id=odef.obligation_definition_id,
                            determination_revision_id=det.revision_id, attestation_id=att.attestation_id,
                            owner_actor_id=actor_id, lifecycle="ADOPTED",
                            valid_from=valid_from, record_from_seq=seq)
    db.add(oi)
    has_expr = db.execute(select(SatisfactionExpressionVersion).where(
        SatisfactionExpressionVersion.tenant_id == det.tenant_id,
        SatisfactionExpressionVersion.obligation_lineage_id == lineage)).scalars().first()
    if not has_expr and odef.satisfaction_template:
        from .assurance import AssuranceConfigurationError, expression_problems
        problems = expression_problems(odef.satisfaction_template)
        if problems:  # STA §16: an invalid expression is blocked before production use, never stored as an obligation's
            raise AssuranceConfigurationError("SATISFACTION_EXPRESSION_INVALID", "; ".join(problems[:5]),
                                              obligation_definition=odef.key)
        db.add(SatisfactionExpressionVersion(tenant_id=det.tenant_id, obligation_lineage_id=lineage, version=1,
                                             ast=odef.satisfaction_template, ast_hash=digest(odef.satisfaction_template),
                                             rationale="Default template from ObligationDefinition " + odef.key,
                                             valid_from=oi.valid_from, record_seq=seq))
    db.flush()
    emit(db, event_type="obligation.adopted", aggregate_type="ObligationInstance", aggregate_id=lineage,
         tenant_id=det.tenant_id, entity_id=subject.entity_id, record_seq=seq,
         payload={"obligation_definition": odef.key, "subject_id": subject.subject_id})
    return oi


def reevaluate_for_fact(db: Session, *, tenant_id: str, holder: Subject, fd: FactDefinition, actor_id: str) -> list:
    """Impact scheduling: re-evaluate dependents of a changed FactDefinition (ZA-APP-001 §10)."""
    subjects = [holder]
    if holder.subject_type == "LEGAL_ENTITY":
        subjects += db.execute(select(Subject).where(Subject.tenant_id == tenant_id,
                                                     Subject.entity_id == holder.entity_id,
                                                     Subject.subject_id != holder.subject_id)
                               .order_by(Subject.subject_id)).scalars().all()
    results = []
    for s in subjects:
        odefs = db.execute(select(ObligationDefinition).where(ObligationDefinition.subject_type == s.subject_type)
                           .order_by(ObligationDefinition.key, ObligationDefinition.obligation_definition_id)).scalars().all()
        for od in odefs:
            # A dependent if any published version needs the fact (deterministic; the evaluation resolves the version).
            versions = db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == od.obligation_definition_id,
                                                            RuleVersion.status == "PUBLISHED")
                                  .order_by(RuleVersion.version.desc())).scalars().all()
            if not any(fd.key in v.required_facts for v in versions):
                continue
            had = db.execute(select(ApplicabilityEvaluation).where(
                ApplicabilityEvaluation.tenant_id == tenant_id,
                ApplicabilityEvaluation.subject_id == s.subject_id,
                ApplicabilityEvaluation.obligation_definition_id == od.obligation_definition_id)
                .order_by(ApplicabilityEvaluation.record_seq.desc())).scalars().first()
            if not had:
                continue
            try:
                ev = evaluate(db, tenant_id=tenant_id, subject=s, odef=od, effective_at=datetime.now(timezone.utc),
                              record_cutoff_seq=None, trigger="FACT_CHANGED", actor_id=actor_id)
                results.append({"subject": s.display_name, "obligation": od.key, "outcome": ev.outcome})
            except HTTPException as e:
                results.append({"subject": s.display_name, "obligation": od.key, "error": e.detail})
    # resolve fact requests satisfied by new knowledge
    for fr in db.execute(select(FactRequest).where(FactRequest.tenant_id == tenant_id,
                                                   FactRequest.subject_id == holder.subject_id,
                                                   FactRequest.fact_definition_id == fd.fact_definition_id,
                                                   FactRequest.status.in_(["OPEN", "IN_PROGRESS"]))).scalars():
        fr.status = "RESOLVED"
        fr.resolution = "Resolved by recorded FactAssertion; linked evaluations re-run."
    return results


def shadow_impact(db: Session, *, odef: ObligationDefinition, candidate_ast: dict, candidate_required: list) -> dict:
    """Shadow evaluation of a candidate rule; returns aggregate buckets only (APP-REQ-014)."""
    fact_defs = load_fact_defs(db)
    sets = load_sets(db)
    fdm = {fd.key: fd for fd in db.execute(select(FactDefinition)).scalars()}
    from ..platform import current_watermark
    seq = current_watermark(db)["record_seq"]
    now = datetime.now(timezone.utc)
    buckets: dict[str, int] = {}
    errors = 0
    total = 0
    new_required: dict[str, int] = {}
    adopted_conflicts = 0
    for s in db.execute(select(Subject).where(Subject.subject_type == odef.subject_type)).scalars():
        total += 1
        facts = {}
        for key in candidate_required:
            if key not in fdm:
                continue
            r = resolve_fact(db, tenant_id=s.tenant_id, subject=s, fd=fdm[key], effective_at=now, known_seq=seq,
                             create_conflicts=False)
            facts[key] = {"known": True, "value": r["value"], "revision_id": r["revision_id"]} if r["known"] else \
                {"known": False, "reason": r["reason"]}
        try:
            cand = rules.evaluate(candidate_ast, facts, {k: v["data_type"] for k, v in fact_defs.items()}, sets,
                                  {"effective_at": now.date().isoformat()})["outcome"]
        except rules.RuleError:
            errors += 1
            continue
        cur_ev = db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.subject_id == s.subject_id,
            ApplicabilityEvaluation.obligation_definition_id == odef.obligation_definition_id).order_by(
            ApplicabilityEvaluation.record_seq.desc())).scalars().first()
        prior = cur_ev.outcome if cur_ev else "NONE"
        k = f"{prior}->{cand}"
        buckets[k] = buckets.get(k, 0) + 1
        if cand == "UNCERTAIN":
            for f, v in facts.items():
                if not v["known"]:
                    new_required[f] = new_required.get(f, 0) + 1
        det = current_determination(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        if det and det.state == "ADOPTED" and det.outcome != cand:
            adopted_conflicts += 1
    return {"subjects_evaluated": total, "transition_buckets": buckets, "missing_fact_counts": new_required,
            "adopted_determination_conflicts": adopted_conflicts, "execution_errors": errors,
            "note": "Aggregate counts only; no tenant identities or fact values exposed to content operations."}
