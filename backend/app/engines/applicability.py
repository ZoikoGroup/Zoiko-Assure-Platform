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
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot,
                      ContentRelease, FactDefinition, FactRequest, Issue, ObligationDefinition, ObligationInstance,
                      RuleVersion, SatisfactionExpressionVersion, SetVersion, Subject, new_id)
from ..platform import audit, canonical_json, digest, emit, ensure_utc, next_record_seq, sha256_hex
from . import idempotency, rules, selection
from .facts import display_value, overlaps, resolve_fact, visible_at
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
    # The highest version in force (the existing supersession policy). Two rows sharing that number are refused, never
    # one of them bound by row order (DAT-REQ-010; DOM §13 "exactly one released rule version is effective").
    rv = selection.highest_version(db.execute(published.where(effective_now)).scalars(), selection="rule_version_bound")
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
    # 5. the source-mapping records stored on the RuleVersion are the ones the release bound (ZA-RUL-001 §12 "source
    # mappings ... remain release-bound"). A release that predates the binding has nothing to compare (reported by the
    # verifier and the trace as unverifiable, not refused here).
    from .catalog import source_mapping_binding_check  # local import: catalog imports this module lazily
    binding = source_mapping_binding_check(entries[0], rv.source_mappings)["status"]
    if binding == "MISMATCH":
        return "SOURCE_MAPPINGS_MISMATCH", rv.rule_version_id
    if binding == "UNKNOWN_PROFILE":
        return "SOURCE_MAPPINGS_PROFILE_UNSUPPORTED", cr.content_release_id
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
    return display_value(fd["sensitivity"], value)  # the one redaction policy (facts.REDACTED_SENSITIVITIES)


# ---------------------------------------------------------------- decision-scope serialisation
def lock_decision(db: Session, tenant_id: str, subject_id: str, odef_id: str) -> None:
    """Serialise every writer of one subject + obligation definition's determination lineage and obligation: evaluation
    persistence (with the determination it derives), attestation and challenge. Each reads the current revision only
    after taking the lock, so none can close a revision another has already closed (a forked lineage) or decide on a
    stale one. APP-001 §15: "Only one current adopted/rejected determination per subject + obligation definition";
    APP-REQ-016: concurrent identical evaluations collapse to one. No partial unique index enforces either yet (that is a
    schema change: docs/backend-remediation-report.md R-01), so this transaction-level lock does (engines/locks.py)."""
    xact_lock(db, "obligation", tenant_id, subject_id, odef_id)


def lock_rule_scope(db: Session, odef_id: str, *, exclusive: bool = False) -> None:
    """One obligation definition's binding-rule scope. A publication of a new RuleVersion for it holds this exclusively
    from before it reads which subjects to re-evaluate until it commits; an evaluation that will persist holds it shared
    from before it resolves the RuleVersion. So an evaluation either commits before the publication reads its targets
    (and is re-evaluated by it), or starts after the publication committed (and resolves the new version). None commits
    a result of the previous version after the new version's impact was computed (APP §10, ZA-RUL-001 §14)."""
    xact_lock(db, "rule-scope", odef_id, shared=not exclusive)


def lock_decisions(db: Session, scopes) -> None:
    """Take several decision-scope locks in the one global order, so transactions that need more than one never wait
    on each other in opposite orders (no deadlock):

        rule scope (per obligation definition, sorted)  ->  decision scope (tenant, subject, definition, sorted)
        ->  the cell record-sequence row (taken by next_record_seq and held until commit).

    A caller takes these BEFORE its first next_record_seq: the sequence row is itself a lock, and taking a scope lock
    after it inverts the order against a transaction that holds the scope and waits for the sequence (a deadlock)."""
    scopes = sorted(set(scopes))
    for odef_id in sorted({o for _, _, o in scopes}):
        lock_rule_scope(db, odef_id)
    for tenant_id, subject_id, odef_id in scopes:
        lock_decision(db, tenant_id, subject_id, odef_id)


def require_supported_versions(rv: RuleVersion) -> None:
    """RUL-REQ-028: a RuleVersion whose declared RuleSchemaVersion or OperatorSetVersion this evaluator does not
    implement is refused explicitly, never run under the versions it does implement. The refusal is the APP §12 error
    SCHEMA_INCOMPATIBLE ("Rule references unsupported/retired FactDefinition or operator version"): an error, not an
    applicability outcome, so it is never UNCERTAIN (APP-REQ-019), nothing is persisted, and there is no fallback to an
    older RuleVersion (the resolved version is the binding one; APP-REQ-006). Not an integrity failure: a correctly
    signed release may target a newer runtime."""
    bad = rules.unsupported_versions(rv.rule_schema_version, rv.operator_set_version)
    if bad:
        raise HTTPException(409, {
            "error": "SCHEMA_INCOMPATIBLE", "rule_version_id": rv.rule_version_id, "unsupported": bad,
            "supported": {"rule_schema_version": sorted(rules.SUPPORTED_SCHEMA_VERSIONS),
                          "operator_set_version": sorted(rules.SUPPORTED_OPERATOR_SET_VERSIONS)},
            "message": f"RuleVersion {rv.rule_version_id} declares {', '.join(f'{k} {v}' for k, v in bad.items())}, which "
                       "this evaluator does not implement; it is not evaluated (RUL-REQ-028)."})


def resource_limit_refusal(rule_version_id: str, violations: list[dict]) -> HTTPException:
    """RUL-REQ-029: "budget failures are infrastructure errors, not UNCERTAIN". The refusal is the APP §12 error
    EVALUATOR_UNAVAILABLE ("Infrastructure error before deterministic result; do not store UNCERTAIN as a substitute"),
    naming the ZA-RUL-001 §18 cause RUL_RESOURCE_LIMIT and every limit exceeded. HTTP 500 like the other runtime content
    error (RUNTIME_RULE_INVARIANT_BREACH): ZA-API-001 keeps 503 for temporary unavailability, and a retry of the same
    RuleVersion under the same limits cannot succeed."""
    return HTTPException(500, {
        "error": "EVALUATOR_UNAVAILABLE", "reason": "RUL_RESOURCE_LIMIT", "rule_version_id": rule_version_id,
        "violations": [{k: v for k, v in x.items() if k != "message"} for x in violations],
        "limits": dict(rules.LIMITS), "retryable": False,
        "message": f"RuleVersion {rule_version_id} exceeds the runtime complexity limits; it was not evaluated "
                   f"(RUL-REQ-029). " + " ".join(x["message"] for x in violations)})


def draft_resource_limit_refusal(draft_id: str, draft_revision: int, violations: list[dict]) -> HTTPException:
    """The same RUL-REQ-029 refusal for a RuleDraft run on the workbench (/sandbox) or in shadow: the draft is named
    instead of a RuleVersion, and no outcome of any kind is returned."""
    return HTTPException(500, {
        "error": "EVALUATOR_UNAVAILABLE", "reason": "RUL_RESOURCE_LIMIT", "draft_id": draft_id,
        "draft_revision": draft_revision,
        "violations": [{k: v for k, v in x.items() if k != "message"} for x in violations],
        "limits": dict(rules.LIMITS), "retryable": False,
        "message": f"Draft {draft_id} revision {draft_revision} exceeds the runtime complexity limits; it was not "
                   f"evaluated (RUL-REQ-029). " + " ".join(x["message"] for x in violations)})


def require_within_limits(rv: RuleVersion) -> None:
    """RUL-REQ-029: the released AST is measured against the complexity limits before input freeze, so a rule over a
    limit reads no facts, writes nothing and runs no node. Nothing falls back to another RuleVersion (APP-REQ-006)."""
    violations = rules.resource_limit_violations(rv.canonical_ast)
    if violations:
        raise resource_limit_refusal(rv.rule_version_id, violations)


def evaluate(db: Session, *, tenant_id: str, subject: Subject, odef: ObligationDefinition,
             effective_at: datetime, record_cutoff_seq: int | None, trigger: str, actor_id: str,
             rule_version: RuleVersion | None = None, persist: bool = True) -> ApplicabilityEvaluation | dict:
    if odef.subject_type != subject.subject_type:
        raise HTTPException(422, f"{odef.key} applies to subject type {odef.subject_type}, not {subject.subject_type}")
    effective_at = ensure_utc(effective_at)
    if persist:  # before the watermark is read and the RuleVersion resolved (see lock_rule_scope)
        lock_rule_scope(db, odef.obligation_definition_id)
        # The decision scope too, before the watermark is read and the facts are resolved (APP §10). A fact write locks
        # every scope it may re-evaluate before it takes the record sequence (fact_impact_plan), so this evaluation
        # either completes first and is then re-evaluated by the fact write, or reads after the fact is committed. It
        # never commits a result from knowledge older than a fact written meanwhile with nothing left to re-evaluate it
        # (a subject's first evaluation used to take this lock only after resolving its facts). Lock order unchanged:
        # rule scope -> decision scope -> record sequence.
        lock_decision(db, tenant_id, subject.subject_id, odef.obligation_definition_id)
    if record_cutoff_seq is None:
        from ..platform import current_watermark
        record_cutoff_seq = current_watermark(db)["record_seq"]
    # The legal date of the instant (Option U, rules.legal_date): the UTC calendar date, for rule selection and for the
    # rule's own effective date (CONTEXT_REF) alike, as every other path derives it.
    legal_day = rules.legal_date(effective_at)
    rv = rule_version or resolve_rule(db, odef, legal_day, record_cutoff_seq)
    verify_rule_integrity(db, rv)  # before input freeze (ZA-APP-001 §13); raises RuleIntegrityError
    require_supported_versions(rv)  # before input freeze; never an approximate interpretation (RUL-REQ-028)
    require_within_limits(rv)  # before input freeze; a budget breach is an infrastructure error (RUL-REQ-029)
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
    bundle_hash = digest(bundle)  # unchanged contract: the frozen bundle and its hash are not affected by the key below
    eval_key = evaluation_key(bundle)
    try:
        out = rules.evaluate(rv.canonical_ast, facts, {k: v["data_type"] for k, v in fact_defs.items()},
                             sets, {"effective_at": legal_day.isoformat()})
    except rules.RuleResourceLimitError as e:  # the evaluator's own guard (RUL-REQ-029); same refusal as above
        raise resource_limit_refusal(rv.rule_version_id, e.violations) from None
    except rules.RuleError as e:  # invariant breach is an infrastructure/content error, never UNCERTAIN
        refusal = HTTPException(500, {"error": e.code, "message": e.message, "node_id": e.node_id})
        # The hash of the inputs frozen before the refusal (not part of the response): a read-only replay compares it
        # with the record, so a refusal by another evaluator build is told apart from altered historical inputs.
        refusal.input_bundle_hash = bundle_hash
        raise refusal from e

    if not persist:
        return {"outcome": out["outcome"], "trace": out["trace"], "missing_facts": out["missing_facts"],
                "rule_version_id": rv.rule_version_id, "input_bundle_hash": bundle_hash}

    # The decision scope is already locked (above), before the duplicate check and the determination it derives
    # (APP-REQ-016; see lock_decision).
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


# ZA-APP-001 §06 "Idempotency and concurrency": "evaluation_key = hash(subject, obligation_definition, rule_version,
# effective_at, record_cutoff, exact fact revision set, evaluator_build). Concurrent identical jobs collapse to one
# committed evaluation ... A new FactAssertion revision, RuleVersion or evaluator build changes the key and creates a new
# immutable evaluation." Every component is a field of the frozen EvaluationInputBundle, so the key is computed from it:
# the exact effective_at instant and the exact record cutoff included. The exact fact revision set is fact_inputs (each
# assertion revision ID with its value hash and provenance) together with unknown_facts (no revision, and why).
EVALUATION_KEY_FIELDS = ("subject_ref", "obligation_definition_id", "rule_version_id", "effective_at",
                         "record_cutoff_seq", "fact_inputs", "unknown_facts", "evaluator_build")
# The key evaluations stored before this change carry (kept as recorded, never rewritten): the effective DATE instead of
# the instant, and no record cutoff. Used only to say which profile a stored key was made under.
LEGACY_EVALUATION_KEY_FIELDS = ("subject_ref", "obligation_definition_id", "rule_version_id", "fact_inputs",
                                "unknown_facts", "evaluator_build")


def evaluation_key(bundle: dict) -> str:
    """The APP-001 §06 deduplication key of an evaluation input bundle (in-memory or as stored: the stored bundle is the
    canonical JSON of the in-memory one, so both give the same digest). Separate from input_bundle_hash."""
    return digest({k: bundle[k] for k in EVALUATION_KEY_FIELDS})


def evaluation_key_profile(ev: ApplicabilityEvaluation) -> str:
    """Which key definition a stored evaluation's evaluation_key was made under, recomputed from its own frozen bundle
    (read-only): APP_001_06 (the full §06 key), LEGACY_EFFECTIVE_DATE (made before the full key: effective date only, no
    record cutoff; such a row is never matched by a new request, so it is never reinterpreted under the new key), or
    UNRECOGNISED (the stored key matches neither: the row or its bundle was not written by this code)."""
    b = ev.input_bundle if isinstance(ev.input_bundle, dict) else {}
    try:
        if evaluation_key(b) == ev.evaluation_key:
            return "APP_001_06"
        legacy = digest({k: b[k] for k in LEGACY_EVALUATION_KEY_FIELDS} | {"effective_date": str(b["effective_at"])[:10]})
        if legacy == ev.evaluation_key:
            return "LEGACY_EFFECTIVE_DATE"
    except (KeyError, TypeError):
        pass
    return "UNRECOGNISED"


def digest_safe(obj):
    import json
    from ..platform import canonical_json
    return json.loads(canonical_json(obj))


def _one_current_determination(rows) -> ApplicabilityDetermination | None:
    """The one current determination revision of a scope or lineage. This code keeps one lineage per (organisation,
    subject, obligation definition), revised under the decision-scope lock (lock_decision), and closes the prior revision
    in the transaction that records the next, so more than one current revision is a state it never writes. No valid-time
    slicing exists to choose between several (APP §15 "only one current adopted/rejected determination per subject +
    obligation definition + compatible valid slice"), so none is chosen (selection.AmbiguousSelection)."""
    return selection.one_or_none(rows, record_type="ApplicabilityDetermination", selection="determination_current",
                                 id_attr="revision_id")


def current_determination(db: Session, tenant_id: str, subject_id: str, odef_id: str) -> ApplicabilityDetermination | None:
    return _one_current_determination(db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == tenant_id, ApplicabilityDetermination.subject_id == subject_id,
        ApplicabilityDetermination.obligation_definition_id == odef_id,
        ApplicabilityDetermination.record_to_seq.is_(None))).scalars())


def current_determinations(db: Session, tenant_id: str, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], ApplicabilityDetermination]:
    """current_determination() for many (subject_id, obligation_definition_id) pairs in one query (list views)."""
    found: dict[tuple[str, str], list[ApplicabilityDetermination]] = {}
    if not pairs:
        return {}
    for d in db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == tenant_id,
            ApplicabilityDetermination.subject_id.in_({s for s, _ in pairs}),
            ApplicabilityDetermination.record_to_seq.is_(None))).scalars():
        key = (d.subject_id, d.obligation_definition_id)
        if key in pairs:
            found.setdefault(key, []).append(d)
    return {key: _one_current_determination(rows) for key, rows in found.items()}


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


def latest_evaluation(db: Session, tenant_id: str, subject_id: str, odef_id: str) -> ApplicabilityEvaluation | None:
    """The evaluation made with the newest knowledge: the highest record cutoff, then the latest recorded. An evaluation
    requested at an earlier record cutoff is history ("what did Assure know then?", TIM-REQ-006), recorded later but
    never the latest knowledge."""
    return db.execute(select(ApplicabilityEvaluation).where(
        ApplicabilityEvaluation.tenant_id == tenant_id, ApplicabilityEvaluation.subject_id == subject_id,
        ApplicabilityEvaluation.obligation_definition_id == odef_id).order_by(
        ApplicabilityEvaluation.record_cutoff_seq.desc(), ApplicabilityEvaluation.record_seq.desc())).scalars().first()


def _derive(db: Session, ev: ApplicabilityEvaluation, subject: Subject, odef: ObligationDefinition, seq: int, actor_id: str):
    cur = current_determination(db, ev.tenant_id, subject.subject_id, odef.obligation_definition_id)
    if cur is not None:
        basis = db.get(ApplicabilityEvaluation, cur.evaluation_id)
        if basis is not None and ev.record_cutoff_seq < basis.record_cutoff_seq:
            # Older knowledge than the determination already rests on: stored as history, but it neither refreshes,
            # flags nor raises requests against the current decision (TIM-REQ-007; APP §10 "no silent rewrite").
            return
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
    iss = Issue(tenant_id=cur.tenant_id, title="Adopted determination requires review", issue_type="DETERMINATION_REVIEW",
                severity="HIGH", priority="P1", source_ref_type="ApplicabilityEvaluation", source_ref_id=ev.evaluation_id,
                dedupe_key=f"detreview:{cur.lineage_id}:{ev.evaluation_id}")
    db.add(iss)
    db.flush()  # assigns the issue ID and its initial state, which the CREATED event records
    # Its creation record, as every other issue producer writes it (governance.record_issue_created: the CREATED event,
    # the issue.create audit record and the outbox event, in this transaction): raised by the system, with the review
    # reason this flag recorded on the determination as its reason. Without it the issue had no history to reconstruct.
    from .governance import record_issue_created
    record_issue_created(db, issue=iss, actor_id="system", reason=rev.review_reason)


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


# ZA-GLS-001 §05 "Determination workflow" transition rules, for the two attestation decisions:
#   PROPOSED  "May move to IN_REVIEW or ADOPTED where attestation policy permits."
#   IN_REVIEW "May move to ADOPTED, REJECTED or SUPERSEDED."
#   ADOPTED   flagged for review by a differing newer evaluation: a new decision is collected and recorded as a new
#             ADOPTED determination revision on that evaluation (ZA-APP-001 §11 C steps 4-6 "collect new decision ...
#             AD revision / attestation"; §09 "Supersession: new determination revision"). GLS gives ADOPTED no path to
#             REJECTED; withdrawal is REVOKED "by authorized process", which is not specified for implementation.
#   REJECTED  "New facts/rule version may create a new proposal" (an evaluation, never an attestation).
ATTESTABLE = {"ADOPT": frozenset({"PROPOSED", "IN_REVIEW", "ADOPTED_REVIEW"}), "REJECT": frozenset({"IN_REVIEW"})}


def _transition_refused(state: str, decision: str) -> HTTPException:
    hint = {("PROPOSED", "REJECT"): "Challenge the determination to move it to IN_REVIEW first (GLS: PROPOSED may "
                                    "move to IN_REVIEW or ADOPTED).",
            ("ADOPTED_REVIEW", "REJECT"): "An adopted determination cannot become REJECTED (GLS); adopt the newer "
                                          "evaluation's result, or resolve the review through a new evaluation."}
    return HTTPException(409, {"error": "DETERMINATION_TRANSITION_NOT_PERMITTED", "from_state": state.replace(
        "ADOPTED_REVIEW", "ADOPTED"), "decision": decision,
        "message": f"{decision} is not a permitted transition from {state.replace('_REVIEW', ' (needs review)')}. "
                   + hint.get((state, decision), "")})


ATTEST_OPERATION = "POST /applicability/determinations/{lineage_id}/attest"


def _attestation_result(db: Session, tenant_id: str, lineage_id: str, attestation_id: str | None) -> dict | None:
    """What one attestation produced, for an idempotent replay (ZA-API-001 §08: "same attestation_id / same resulting
    determination revision"): the attestation, the determination revision recorded with it, and the obligation revision
    it activated (none for a rejection, a non-APPLIES adoption or a closure, as the original response)."""
    att = db.get(AdoptionAttestation, attestation_id) if attestation_id else None
    if att is None or att.tenant_id != tenant_id:
        return None
    rev = db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == tenant_id, ApplicabilityDetermination.lineage_id == lineage_id,
        ApplicabilityDetermination.record_from_seq == att.record_seq)).scalars().first()
    if rev is None:
        return None
    obligation = db.execute(select(ObligationInstance).where(
        ObligationInstance.tenant_id == tenant_id, ObligationInstance.attestation_id == att.attestation_id,
        ObligationInstance.record_from_seq == att.record_seq)).scalars().first()
    return {"determination": rev, "attestation": att, "obligation": obligation, "replayed": True}


def as_committed(response: dict) -> dict:
    """A replayed attestation's response as it was committed. The determination revision and the obligation revision the
    attestation created were current when it committed (record_to_seq null); the only column of either that changes
    afterwards is record_to_seq, set when a later revision supersedes it in record time (no code path updates any other,
    and the attestation itself is immutable). The replay reports them as committed, never as they are now. Applied to the
    serialised response only: the session rows are not touched."""
    return {**response, **{part: {**response[part], "record_to_seq": None}
                           for part in ("determination", "obligation") if response.get(part)}}


def attest(db: Session, *, ctx, determination_lineage_id: str, decision: str, authority_basis: str,
           reason: str | None = None, expected_revision_id: str | None = None,
           expected_evaluation_id: str | None = None, idempotency_key: str | None = None) -> dict:
    """Customer adoption / rejection (FR-APP-004, INV-01, DOM-INV-03/04) of the exact current determination revision.

    `expected_revision_id` / `expected_evaluation_id`: the revision the attestor reviewed and the evaluation it is based
    on. When given, a decision on anything else is refused (409) instead of being applied to a newer state: APP §12
    "Adopt/reject exact determination revision"; APP §16 "stale/superseded revision cannot be silently attested".

    `idempotency_key` (optional; engines/idempotency.py, ZA-API-001 §08): a retry of a committed attestation with the same
    key and request returns that attestation's result and writes nothing; the key with another request is 409."""
    ctx.require_human("Determination adoption")
    ctx.require("attest")
    request = {"determination_lineage_id": determination_lineage_id, "decision": decision,
               "authority_basis": (authority_basis or "").strip(), "reason": (reason or "").strip() or None,
               "revision_id": expected_revision_id, "evaluation_id": expected_evaluation_id}
    done = idempotency.replay(db, tenant_id=ctx.tenant, operation=ATTEST_OPERATION, key=idempotency_key, request=request)
    if done is not None:
        result = _attestation_result(db, ctx.tenant, determination_lineage_id, done)
        if result is not None:
            return result
    if decision not in ATTESTABLE:
        raise HTTPException(422, "decision must be ADOPT or REJECT")
    authority_basis = (authority_basis or "").strip()
    reason = (reason or "").strip() or None
    if not authority_basis:
        raise HTTPException(422, "authority_basis is required (APP §13: named actor, authority basis, exact revision)")

    def current():
        return _one_current_determination(db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == ctx.tenant,
            ApplicabilityDetermination.lineage_id == determination_lineage_id,
            ApplicabilityDetermination.record_to_seq.is_(None))
            .execution_options(populate_existing=True)).scalars())
    cur = current()
    if not cur:
        raise HTTPException(404, "Determination not found")
    # Audit finding F-05 (DOM §13: one obligation per adopted determination and canonical scope). Concurrent
    # attestations of one subject + obligation definition are serialised by a database-held lock taken before anything
    # is validated, then the determination is read again: a second ADOPT that waited sees the first one's committed
    # revision and is refused (409) instead of creating a second obligation lineage. A partial unique index on current
    # revisions would add a constraint-level guarantee; it is a schema change awaiting review
    # (docs/backend-remediation-report.md), so none is created here.
    lock_decision(db, ctx.tenant, cur.subject_id, cur.obligation_definition_id)
    cur = current()
    if not cur:
        raise HTTPException(404, "Determination not found")
    if cur.state not in ("PROPOSED", "IN_REVIEW") and not (cur.state == "ADOPTED" and cur.needs_review):
        # No attestation decision leaves this state (GLS §05: ADOPTED is decided until a differing newer evaluation flags
        # it; REJECTED re-opens only through a new proposal). Refused with the transition code every other refusal of this
        # endpoint carries, so a repeated or late request is identified by its code; status and message are unchanged.
        raise HTTPException(409, {"error": "DETERMINATION_TRANSITION_NOT_PERMITTED", "from_state": cur.state,
                                  "decision": decision, "message": f"Determination in state {cur.state} cannot be attested"})
    state = "ADOPTED_REVIEW" if cur.state == "ADOPTED" else cur.state
    if state not in ATTESTABLE[decision]:
        raise _transition_refused(state, decision)
    if expected_revision_id is not None and expected_revision_id != cur.revision_id:
        raise HTTPException(409, {"error": "STALE_DETERMINATION_REVISION", "current_revision_id": cur.revision_id,
                                  "message": "The determination changed after it was reviewed; review the current "
                                             "revision before attesting."})
    latest = latest_evaluation(db, ctx.tenant, cur.subject_id, cur.obligation_definition_id)
    if cur.state == "ADOPTED" and cur.needs_review:
        ev = latest or db.get(ApplicabilityEvaluation, cur.evaluation_id)
    else:
        ev = db.get(ApplicabilityEvaluation, cur.evaluation_id)
        # stale-revision protection: the backing evaluation must be the latest for this subject/definition
        if latest and latest.evaluation_id != ev.evaluation_id:
            raise HTTPException(409, "A newer evaluation exists; re-review before attesting")
    if expected_evaluation_id is not None and expected_evaluation_id != ev.evaluation_id:
        raise HTTPException(409, {"error": "STALE_EVALUATION", "evaluation_id": ev.evaluation_id,
                                  "message": "The decision would rest on a different evaluation than the one reviewed."})
    if ev.outcome == "UNCERTAIN":
        raise HTTPException(409, "UNCERTAIN cannot be adopted (DOM-INV-03); resolve facts first")
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
    obligation = closed = None
    if new_state == "ADOPTED":
        if ev.outcome == "APPLIES":
            obligation = _activate_obligation(db, rev, att, subject, odef, seq, ctx.actor.actor_id)
        else:
            closed = _close_obligation(db, ctx.tenant, subject.subject_id, odef.obligation_definition_id, seq,
                                       rev.valid_from, closing_determination=rev, closing_attestation=att)
        # ZA-EVT-001 / APP §12 minimum payload: "determination revision, attestation ID, actor, authority basis,
        # resulting obligation instance ID if applicable".
        resulting = obligation or closed
        emit(db, event_type="determination.adopted", aggregate_type="ApplicabilityDetermination",
             aggregate_id=rev.lineage_id, tenant_id=ctx.tenant, actor=ctx.actor_ref(), record_seq=seq,
             payload={"determination_revision_id": rev.revision_id, "attestation_id": att.attestation_id,
                      "authority_basis": authority_basis, "outcome": ev.outcome, "evaluation_id": ev.evaluation_id,
                      "obligation_lineage_id": resulting.lineage_id if resulting else None,
                      "obligation_revision_id": resulting.revision_id if resulting else None})
    else:
        # ZA-EVT-001 "determination.rejected: Authorized customer rejected proposed determination."
        emit(db, event_type="determination.rejected", aggregate_type="ApplicabilityDetermination",
             aggregate_id=rev.lineage_id, tenant_id=ctx.tenant, actor=ctx.actor_ref(), record_seq=seq,
             payload={"determination_revision_id": rev.revision_id, "attestation_id": att.attestation_id,
                      "authority_basis": authority_basis, "outcome": ev.outcome, "evaluation_id": ev.evaluation_id})
    idempotency.remember(db, tenant_id=ctx.tenant, operation=ATTEST_OPERATION, key=idempotency_key, request=request,
                         result_ref=att.attestation_id)
    return {"determination": rev, "attestation": att, "obligation": obligation}


def _close_obligation(db, tenant_id, subject_id, odef_id, seq, at, *, closing_determination, closing_attestation):
    """Close the active obligation after a non-APPLIES determination is adopted: a new CLOSED revision ending at `at`
    (prospective; the retroactive valid-time policy of APP §11 C step 6 is not specified). The revision keeps naming
    its originating adoption; the closing decision is recorded at the same record sequence and in the
    `obligation.revised` event (ZA-EVT-001 "Bitemporal obligation revision recorded")."""
    cur = current_obligation_for_scope(db, tenant_id, subject_id, odef_id)
    if not (cur and cur.lifecycle == "ADOPTED"):
        return None
    if ensure_utc(at) <= ensure_utc(cur.valid_from):
        # TIM-REQ-002: the CLOSED revision would be [start, at), empty or inverted. ZA-APP-001 §11 C step 6 does not yet
        # specify the valid-time policy for a closure dated at or before the obligation's start, so it is refused and
        # the caller's transaction writes nothing. A future valid-time policy may replace this refusal; no history is
        # rewritten either way.
        raise HTTPException(409, {
            "error": "CLOSURE_PRECEDES_OBLIGATION_START", "obligation_revision_id": cur.revision_id,
            "obligation_valid_from": ensure_utc(cur.valid_from).isoformat(),
            "closing_valid_from": ensure_utc(at).isoformat(),
            "message": "This determination would close the obligation at or before the date it starts. How such a "
                       "closure is recorded is not yet decided (ZA-APP-001 §11 C step 6), so it was not recorded."})
    cur.record_to_seq = seq
    closed = ObligationInstance(lineage_id=cur.lineage_id, tenant_id=tenant_id, entity_id=cur.entity_id,
                                subject_id=subject_id, obligation_definition_id=odef_id,
                                determination_revision_id=cur.determination_revision_id, attestation_id=cur.attestation_id,
                                owner_actor_id=cur.owner_actor_id, lifecycle="CLOSED", valid_from=cur.valid_from,
                                valid_to=at, record_from_seq=seq)
    db.add(closed)
    db.flush()
    emit(db, event_type="obligation.revised", aggregate_type="ObligationInstance", aggregate_id=cur.lineage_id,
         tenant_id=tenant_id, entity_id=cur.entity_id, record_seq=seq,
         payload={"obligation_revision_id": closed.revision_id, "prior_revision_id": cur.revision_id,
                  "lifecycle": "CLOSED", "valid_to": at,
                  "closing_determination_revision_id": closing_determination.revision_id,
                  "closing_attestation_id": closing_attestation.attestation_id})
    return closed


def current_obligation_for_scope(db, tenant_id: str, subject_id: str, odef_id: str) -> ObligationInstance | None:
    """The one current obligation revision of (organisation, subject, obligation definition): the revision an adoption
    revises or a closure closes. DOM §13 "Adopted Determination -> ObligationInstance 1 : 0..1 per canonical scoped
    obligation revision" (audit F-05); attestation creates and revises it under the decision-scope lock, closing the
    prior revision in the same transaction. Two current revisions are never written by this code; revising or closing an
    arbitrary one of them would leave the other current, so neither is chosen (selection.AmbiguousSelection)."""
    return selection.one_or_none(db.execute(select(ObligationInstance).where(
        ObligationInstance.tenant_id == tenant_id, ObligationInstance.subject_id == subject_id,
        ObligationInstance.obligation_definition_id == odef_id, ObligationInstance.record_to_seq.is_(None))).scalars(),
        record_type="ObligationInstance", selection="obligation_current", id_attr="revision_id")


def _activate_obligation(db, det, att, subject, odef, seq, actor_id) -> ObligationInstance:
    cur = current_obligation_for_scope(db, det.tenant_id, subject.subject_id, odef.obligation_definition_id)
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
        from .satisfaction import leaves
        problems = expression_problems(odef.satisfaction_template)
        # Catalogue content maps control roles only; an exact implementation binding in a stored template (legacy data:
        # catalog.validate_template refuses it) would point this organisation at a record it may not own (DOM-INV-11).
        if any("control_implementation_id" in n for _, n in leaves(odef.satisfaction_template)):
            raise AssuranceConfigurationError("CONTROL_MAPPING_INVALID", "the definition's template binds an exact "
                                              "control implementation; catalogue templates map control roles only",
                                              obligation_definition=odef.key)
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


def fact_impact_plan(db: Session, *, tenant_id: str, holder: Subject, fd: FactDefinition) -> list[tuple]:
    """The (subject, obligation definition) pairs a change of `fd` on `holder` re-evaluates (ZA-APP-001 §10, TIM §17
    impact index): the holder and, for a legal entity, its subordinate subjects, for every definition with a published
    version that needs the fact. Read-only.

    Every such scope is planned (and locked by lock_fact_impact), not only those already evaluated: a subject's first
    evaluation may be running while the fact is written, and holding its scope makes it either finish first, and be
    re-evaluated, or start after the fact. reevaluate_for_fact re-evaluates only the scopes that have an evaluation by
    then; a content or fact change never evaluates a subject for the first time."""
    subjects = [holder]
    if holder.subject_type == "LEGAL_ENTITY":
        subjects += db.execute(select(Subject).where(Subject.tenant_id == tenant_id,
                                                     Subject.entity_id == holder.entity_id,
                                                     Subject.subject_id != holder.subject_id)
                               .order_by(Subject.subject_id)).scalars().all()
    # Two reads, whatever the number of subjects: the definitions of the subjects' types, and the required facts of
    # their published versions (only that column: a version's AST and golden vectors are not needed). The plan is the
    # one reading them per subject and definition gave, in the same order (subjects as listed; definitions by key, ID),
    # so the scopes lock_fact_impact locks, and their order, are unchanged.
    by_type: dict[str, list[ObligationDefinition]] = {}
    for od in db.execute(select(ObligationDefinition).where(
            ObligationDefinition.subject_type.in_(sorted({s.subject_type for s in subjects})))
            .order_by(ObligationDefinition.key, ObligationDefinition.obligation_definition_id)).scalars():
        by_type.setdefault(od.subject_type, []).append(od)
    ids = [od.obligation_definition_id for ods in by_type.values() for od in ods]
    needs = {od_id for od_id, required in db.execute(select(RuleVersion.obligation_definition_id,
                                                            RuleVersion.required_facts).where(
        RuleVersion.obligation_definition_id.in_(ids or ["-"]), RuleVersion.status == "PUBLISHED"))
        if fd.key in required}
    return [(s, od) for s in subjects for od in by_type.get(s.subject_type, []) if od.obligation_definition_id in needs]


def lock_fact_impact(db: Session, tenant_id: str, plan: list[tuple]) -> None:
    """Lock every scope a fact change will re-evaluate. Must run before the fact is written, which takes the record
    sequence (lock_decisions)."""
    lock_decisions(db, [(tenant_id, s.subject_id, od.obligation_definition_id) for s, od in plan])


def reevaluate_for_fact(db: Session, *, tenant_id: str, holder: Subject, fd: FactDefinition, actor_id: str,
                        plan: list[tuple] | None = None) -> list:
    """Impact scheduling: re-evaluate dependents of a changed FactDefinition (ZA-APP-001 §10). `plan` is the
    fact_impact_plan the caller computed and locked before writing the fact; without it the plan is computed and locked
    here, which is only safe in a transaction that has not yet taken the record sequence."""
    results = []
    if plan is None:
        plan = fact_impact_plan(db, tenant_id=tenant_id, holder=holder, fd=fd)
        lock_fact_impact(db, tenant_id, plan)
    for s, od in plan:
        # Only scopes evaluated before (read now, under the scope lock, so an evaluation that committed while this
        # transaction waited for the lock is included). Unchanged rule: no subject is evaluated for the first time here.
        had = db.execute(select(ApplicabilityEvaluation.evaluation_id).where(
            ApplicabilityEvaluation.tenant_id == tenant_id, ApplicabilityEvaluation.subject_id == s.subject_id,
            ApplicabilityEvaluation.obligation_definition_id == od.obligation_definition_id).limit(1)).first()
        if not had:
            continue
        try:
            ev = evaluate(db, tenant_id=tenant_id, subject=s, odef=od, effective_at=datetime.now(timezone.utc),
                          record_cutoff_seq=None, trigger="FACT_CHANGED", actor_id=actor_id)
            results.append({"subject": s.display_name, "obligation": od.key, "outcome": ev.outcome})
        except HTTPException as e:
            results.append({"subject": s.display_name, "obligation": od.key, "error": e.detail})
    # Resolve the fact requests this write satisfies (ZA-APP-001 §11 B step 6 "Resolve FactRequest if requirement
    # satisfied"), and only those: a request another linked evaluation still needs stays actionable (APP-REQ-010).
    from ..platform import current_watermark
    known_seq = current_watermark(db)["record_seq"]  # this transaction's: the fact just written and the re-runs above
    for fr in db.execute(select(FactRequest).where(FactRequest.tenant_id == tenant_id,
                                                   FactRequest.subject_id == holder.subject_id,
                                                   FactRequest.fact_definition_id == fd.fact_definition_id,
                                                   FactRequest.status.in_(["OPEN", "IN_PROGRESS"]))).scalars():
        if _request_satisfied(db, tenant_id, fr, fd, known_seq):
            fr.status = "RESOLVED"
            fr.resolution = "Resolved by recorded FactAssertion; linked evaluations re-run."
    return results


def _request_satisfied(db: Session, tenant_id: str, fr: FactRequest, fd: FactDefinition, known_seq: int) -> bool:
    """Whether `fr` is satisfied: the fact is known (facts.resolve_fact, ZA-APP-001 §04: one admissible, unexpired,
    agreeing value) at `known_seq` for the subject and effective date of every evaluation the request links (§08: the
    request serves their valid-time need). A link that cannot be found in the organisation cannot be shown satisfied,
    and neither can a request with no links. No conflict record is created; nothing is written. This is the resolution
    the fact-request view shows for each linked evaluation (GET /fact-requests/{id}, current_resolution).

    This is the "known where needed" reading of §11 B step 6 ("Resolve FactRequest if requirement satisfied") adopted
    for APP-REQ-010."""
    linked = sorted({str(i) for i in (fr.linked_evaluation_ids or [])})
    if not linked:
        return False
    E = ApplicabilityEvaluation
    needs = db.execute(select(E.subject_id, E.effective_at).where(E.tenant_id == tenant_id,
                                                                    E.evaluation_id.in_(linked))).all()
    if len(needs) != len(linked):
        return False
    subjects = {s.subject_id: s for s in db.execute(select(Subject).where(
        Subject.tenant_id == tenant_id, Subject.subject_id.in_(sorted({sid for sid, _ in needs})))).scalars()}
    for sid, at in sorted({(sid, ensure_utc(at)) for sid, at in needs}):
        subject = subjects.get(sid)
        if subject is None or not resolve_fact(db, tenant_id=tenant_id, subject=subject, fd=fd, effective_at=at,
                                               known_seq=known_seq, create_conflicts=False)["known"]:
            return False
    return True


def _consumed(ev: ApplicabilityEvaluation, fact_key: str) -> dict | None:
    """How the evaluation's frozen bundle consumed `fact_key`: the exact assertion revision, or the unknown reason; None
    when the bundle does not name the fact. No value and no value hash (facts.input_bundle_view)."""
    return _consumed_in(ev.input_bundle, fact_key)


def _consumed_in(bundle, fact_key: str) -> dict | None:
    """_consumed for a frozen bundle as stored."""
    b = bundle if isinstance(bundle, dict) else {}
    for f in b.get("fact_inputs") or []:
        if isinstance(f, dict) and f.get("fact_definition") == fact_key:
            return {"fact_assertion_revision_id": f.get("fact_assertion_revision_id"),
                    "provenance_class": f.get("provenance_class")}
    for u in b.get("unknown_facts") or []:
        if isinstance(u, dict) and u.get("fact_definition") == fact_key:
            return {"unknown_reason": u.get("reason_code")}
    return None


def _bundle_may_name(db: Session, fact_key: str):
    """SQL condition: the evaluation's frozen bundle names `fact_key` in fact_inputs or unknown_facts. A prefilter only,
    so evaluations of other facts are neither fetched nor parsed here; _consumed_in still decides. It never excludes a
    bundle _consumed_in would accept: on PostgreSQL, JSON containment of {"fact_inputs": [{"fact_definition": key}]}
    (or unknown_facts) holds exactly when such an object element exists; on SQLite, json_each lists the elements and
    only object elements are read (any other element is skipped, never an error)."""
    import json
    from sqlalchemy import String, bindparam, cast, literal, text
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        b = cast(ApplicabilityEvaluation.input_bundle, JSONB)
        return or_(*(b.op("@>")(cast(literal(json.dumps({part: [{"fact_definition": fact_key}]}), String), JSONB))
                     for part in ("fact_inputs", "unknown_facts")))
    # Parenthesised as one condition: text() is not grouped, and a bare OR next to the statement's other AND terms
    # would let a matching bundle of any organisation, subject or record time through.
    return text("(" + " OR ".join(
        f"EXISTS (SELECT 1 FROM json_each(applicability_evaluation.input_bundle, '$.{part}') AS je "
        f"WHERE CASE WHEN je.type = 'object' THEN json_extract(je.value, '$.fact_definition') END = :consumed_fact)"
        for part in ("fact_inputs", "unknown_facts")) + ")").bindparams(bindparam("consumed_fact", fact_key))


def _snapshot_record_seqs(db: Session, tenant_id: str, snapshot_ids) -> dict[str, int]:
    """The record sequence at which each snapshot was persisted: that of its assurance.snapshot_created event, written
    in the snapshot's own transaction (asr.compute_snapshot; DAT-REQ-014). The rule of the dossier's historical read
    (routers/assurance._snapshot_record_seqs); a snapshot without one is absent from the result. `snapshot_ids`: a list
    or a SELECT of snapshot IDs (one statement either way)."""
    from ..models import OutboxEvent
    if isinstance(snapshot_ids, list) and not snapshot_ids:
        return {}
    out: dict[str, int] = {}
    for sid, seq in db.execute(select(OutboxEvent.aggregate_id, OutboxEvent.record_seq).where(
            OutboxEvent.tenant_id == tenant_id, OutboxEvent.aggregate_type == "AssuranceSnapshot",
            OutboxEvent.event_type == "assurance.snapshot_created", OutboxEvent.aggregate_id.in_(snapshot_ids))):
        out[sid] = min(seq, out.get(sid, seq))
    return out


def _within(point: datetime, valid_from: datetime, valid_to: datetime | None) -> bool:
    """point in the half-open valid interval [valid_from, valid_to) (TIM-REQ-002; None = open-ended)."""
    point = ensure_utc(point)
    return not point < valid_from and (valid_to is None or point < valid_to)


# Identifiers fact_change_dependents has already read are passed to a later statement as a list while there are at
# most this many (an index lookup per value), and as the equivalent subquery beyond that, so no statement carries an
# unbounded number of parameters. Either form selects the same rows.
IN_LIST_LIMIT = 900


def fact_change_dependents(db: Session, *, tenant_id: str, holder: Subject, fd: FactDefinition, valid_from: datetime,
                           valid_to: datetime | None, known_seq: int) -> list[dict]:
    """What depends on a proposed change of `fd` held by `holder` over the valid interval [valid_from, valid_to).

    ZA-APP-001 §16 WF-APP-02 "Impact preview": "Which obligation definitions/evaluations depend on a proposed fact
    change; no predicted legal result shown unless shadow evaluation is explicitly run". The dependents are the ZA-TIM-001
    §17 impact-index ones of a FactAssertion valid-range change: "Applicability evaluations/rules referencing
    FactDefinition for overlapping subject + valid period; assurance snapshots that depend on resulting obligations".
    Read-only: nothing is evaluated, written or locked, and no outcome is predicted.

    Record time: everything is read as known at `known_seq`, the watermark the response echoes (TIM-REQ-017; ZA-TIM-001
    "AS_KNOWN_THEN excludes revisions first recorded after K"), each by the record-time rule this code already applies to
    it: evaluations by their record_seq; determination and obligation revisions by their record slice (visible_at:
    record_from <= K < record_to, the ZA-TIM-001 canonical bitemporal lookup); rule versions by their record_seq, as
    resolve_rule binds a rule at a record cutoff; assurance snapshots by the record sequence of their
    assurance.snapshot_created event (the rule of the dossier's historical read, routers/assurance._snapshot_record_seqs;
    the snapshot row has no record column), a snapshot without one being left out. Subjects and obligation definitions
    carry no record time and are read as stored.

    Scopes, ordered by subject then definition: every (subject, obligation definition) the fact write plans
    (fact_impact_plan: the holder and, for a legal entity, its subordinate subjects, for every definition with a
    published rule version that needs the fact) where a version known at K needs it, plus any scope of those subjects
    with an evaluation recorded by K whose frozen bundle consumed the fact. Per scope:
      rule_versions        the published versions known at K that need the fact (a coarse prefilter, APP §14; their
                           legal validity is shown, not compared with the fact's instants: TIM-REQ-003);
      evaluations          evaluations recorded by K whose frozen bundle consumed the fact (revision or unknown reason)
                           and whose effective_at lies in [valid_from, valid_to); each with its recorded outcome, never a
                           predicted one;
      determination        the scope's determination revision current at K, with whether it rests on a listed
                           evaluation and whether its valid period overlaps the change;
      obligation           the scope's obligation revision current at K, with whether its valid period overlaps the
                           change;
      assurance_snapshots  snapshots recorded by K of the scope's obligation lineages known at K whose period overlaps
                           the change.

    Reads: a fixed number of statements whatever the number of subjects, scopes or evaluations (one per record class,
    each restricted in SQL by organisation, subjects, definitions, record time and valid time). Every SQL restriction is
    one the rules above already impose, and each is checked again in Python where it was decided before (bundle
    consumption, effective time, period overlap), so the answer is the one reading each scope separately gave. Only the
    columns the answer shows are read: no evaluation trace, no rule AST or golden vectors, no snapshot segments or
    manifest, and no frozen bundle of an evaluation that does not consume the fact."""
    valid_from, valid_to = ensure_utc(valid_from), ensure_utc(valid_to)
    plan = fact_impact_plan(db, tenant_id=tenant_id, holder=holder, fd=fd)
    subjects = {holder.subject_id: holder, **{s.subject_id: s for s, _ in plan}}
    entity_wide = holder.subject_type == "LEGAL_ENTITY"
    if entity_wide:
        subjects.update({s.subject_id: s for s in db.execute(select(Subject).where(
            Subject.tenant_id == tenant_id, Subject.entity_id == holder.entity_id)).scalars()})

    def of_subjects(column):
        # The subjects above, in SQL: a subquery for a legal entity, so no statement carries one parameter per subject.
        if not entity_wide:
            return column.in_(list(subjects))
        return or_(column == holder.subject_id, column.in_(select(Subject.subject_id).where(
            Subject.tenant_id == tenant_id, Subject.entity_id == holder.entity_id)))

    E = ApplicabilityEvaluation
    in_window = [E.effective_at >= valid_from] + ([E.effective_at < valid_to] if valid_to is not None else [])
    evaluations: dict[tuple[str, str], list[tuple]] = {}
    for e in db.execute(select(E.evaluation_id, E.subject_id, E.obligation_definition_id, E.effective_at,
                               E.record_cutoff_seq, E.record_seq, E.rule_version_id, E.evaluator_build, E.trigger,
                               E.outcome, E.input_bundle).where(
            E.tenant_id == tenant_id, of_subjects(E.subject_id), E.record_seq <= known_seq, *in_window,
            _bundle_may_name(db, fd.key)).order_by(E.record_seq, E.evaluation_id)):
        consumed = _consumed_in(e.input_bundle, fd.key)
        if consumed is not None and _within(e.effective_at, valid_from, valid_to):
            evaluations.setdefault((e.subject_id, e.obligation_definition_id), []).append((e, consumed))
    odefs = {od.obligation_definition_id: od for _, od in plan}
    missing = {od_id for _, od_id in evaluations} - set(odefs)
    if missing:
        odefs.update({od.obligation_definition_id: od for od in db.execute(select(ObligationDefinition).where(
            ObligationDefinition.obligation_definition_id.in_(list(missing)))).scalars()})
    needing: dict[str, list] = {od_id: [] for od_id in odefs}
    for v in db.execute(select(RuleVersion.obligation_definition_id, RuleVersion.rule_version_id, RuleVersion.version,
                               RuleVersion.valid_from, RuleVersion.valid_to, RuleVersion.required_facts).where(
            RuleVersion.obligation_definition_id.in_(list(odefs) or ["-"]), RuleVersion.status == "PUBLISHED",
            RuleVersion.record_seq <= known_seq).order_by(RuleVersion.version.desc(), RuleVersion.rule_version_id)):
        if fd.key in (v.required_facts or []):
            needing[v.obligation_definition_id].append(v)
    scopes = {(s.subject_id, od.obligation_definition_id) for s, od in plan if needing[od.obligation_definition_id]} | {
        k for k in evaluations if k[1] in odefs}
    scope_odefs = sorted({od_id for _, od_id in scopes}) or ["-"]

    def per_scope(rows) -> dict[tuple[str, str], list]:
        grouped: dict[tuple[str, str], list] = {}
        for r in rows:
            k = (r.subject_id, r.obligation_definition_id)
            if k in scopes:
                grouped.setdefault(k, []).append(r)
        return grouped
    D, O, S = ApplicabilityDetermination, ObligationInstance, AssuranceSnapshot
    dets, obls, lineage_scope, snapshots = {}, {}, {}, {}
    if scopes:
        dets = per_scope(db.execute(visible_at(select(D).where(
            D.tenant_id == tenant_id, of_subjects(D.subject_id), D.obligation_definition_id.in_(scope_odefs)), D,
            known_seq)).scalars())
        obls = per_scope(db.execute(visible_at(select(O).where(
            O.tenant_id == tenant_id, of_subjects(O.subject_id), O.obligation_definition_id.in_(scope_odefs)), O,
            known_seq)).scalars())
        # The lineages known at K of each scope: every revision recorded by K names its lineage.
        lineage_filter = [O.tenant_id == tenant_id, of_subjects(O.subject_id),
                          O.obligation_definition_id.in_(scope_odefs), O.record_from_seq <= known_seq]
        for r in db.execute(select(O.lineage_id, O.subject_id, O.obligation_definition_id).where(*lineage_filter)):
            k = (r.subject_id, r.obligation_definition_id)
            if k in scopes:
                lineage_scope[r.lineage_id] = k
        # Their snapshots, with the period overlap in SQL as a prefilter (overlaps() decides), in the order each scope's
        # own read used (record cutoff, creation, ID); each kept only once recorded by K.
        few = IN_LIST_LIMIT
        in_period = [S.period_to > valid_from] + ([S.period_from < valid_to] if valid_to is not None else [])
        snap_filter = [S.tenant_id == tenant_id, (S.obligation_lineage_id.in_(sorted(lineage_scope) or ["-"])
                                                  if len(lineage_scope) <= few else
                                                  S.obligation_lineage_id.in_(select(O.lineage_id).where(*lineage_filter))),
                       *in_period]
        candidates = [x for x in db.execute(select(S.snapshot_id, S.obligation_lineage_id, S.period_from, S.period_to,
                                                   S.record_cutoff_seq, S.state).where(*snap_filter)
                                            .order_by(S.record_cutoff_seq, S.created_at, S.snapshot_id))
                      if x.obligation_lineage_id in lineage_scope
                      and overlaps(x.period_from, x.period_to, valid_from, valid_to)]
        ids = [x.snapshot_id for x in candidates]
        recorded = _snapshot_record_seqs(db, tenant_id, ids if len(ids) <= few else
                                         select(S.snapshot_id).where(*snap_filter)) if candidates else {}
        for x in candidates:
            if recorded.get(x.snapshot_id, known_seq + 1) <= known_seq:
                snapshots.setdefault(lineage_scope[x.obligation_lineage_id], []).append(x)
    out = []
    for subject_id, od_id in sorted(scopes, key=lambda k: (k[0], odefs[k[1]].key, k[1])):
        s, od = subjects[subject_id], odefs[od_id]
        evs = evaluations.get((subject_id, od_id), [])
        listed = {e.evaluation_id for e, _ in evs}
        det = _one_current_determination(dets.get((subject_id, od_id), []))
        obl = selection.one_or_none(obls.get((subject_id, od_id), []), record_type="ObligationInstance",
                                    selection="obligation_as_of", id_attr="revision_id")
        versions = needing[od_id]
        out.append({
            "subject": {"subject_id": s.subject_id, "display_name": s.display_name, "subject_type": s.subject_type},
            "obligation_definition": {"obligation_definition_id": od_id, "key": od.key, "title": od.title},
            "rule_versions": [{"rule_version_id": v.rule_version_id, "version": v.version, "valid_from": v.valid_from,
                               "valid_to": v.valid_to} for v in versions],
            "evaluations": [{"evaluation_id": e.evaluation_id, "effective_at": e.effective_at,
                             "record_cutoff_seq": e.record_cutoff_seq, "record_seq": e.record_seq,
                             "rule_version_id": e.rule_version_id, "evaluator_build": e.evaluator_build,
                             "trigger": e.trigger, "recorded_outcome": e.outcome, "fact_input": consumed}
                            for e, consumed in evs],
            "determination": None if det is None else {
                "revision_id": det.revision_id, "lineage_id": det.lineage_id, "state": det.state,
                "recorded_outcome": det.outcome, "evaluation_id": det.evaluation_id, "valid_from": det.valid_from,
                "valid_to": det.valid_to, "needs_review": det.needs_review,
                "rests_on_listed_evaluation": det.evaluation_id in listed,
                "valid_period_overlaps_change": overlaps(det.valid_from, det.valid_to, valid_from, valid_to)},
            "obligation": None if obl is None else {
                "revision_id": obl.revision_id, "lineage_id": obl.lineage_id, "lifecycle": obl.lifecycle,
                "determination_revision_id": obl.determination_revision_id, "valid_from": obl.valid_from,
                "valid_to": obl.valid_to,
                "valid_period_overlaps_change": overlaps(obl.valid_from, obl.valid_to, valid_from, valid_to)},
            "assurance_snapshots": [{"snapshot_id": x.snapshot_id, "obligation_lineage_id": x.obligation_lineage_id,
                                     "period_from": x.period_from, "period_to": x.period_to,
                                     "record_cutoff_seq": x.record_cutoff_seq, "state": x.state}
                                    for x in snapshots.get((subject_id, od_id), [])]})
    return out


def shadow_impact(db: Session, *, odef: ObligationDefinition, candidate_ast: dict, candidate_required: list) -> dict:
    """Shadow evaluation of a candidate rule; returns aggregate buckets only (APP-REQ-014).
    Raises rules.RuleResourceLimitError before any subject's facts are read when the candidate exceeds a complexity
    limit (RUL-REQ-029): a budget breach is an infrastructure error, never a per-subject execution error."""
    violations = rules.resource_limit_violations(candidate_ast)
    if violations:
        raise rules.RuleResourceLimitError(violations)
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
            out = rules.evaluate(candidate_ast, facts, {k: v["data_type"] for k, v in fact_defs.items()}, sets,
                                 {"effective_at": rules.legal_date(now).isoformat()})
            cand = out["outcome"]
        except rules.RuleError:
            errors += 1
            continue
        # APP §10 step 3 "Compare current released outcome vs candidate outcome": the latest-knowledge evaluation, as
        # the publication impact and the determination use it. One requested later at an older record cutoff is history
        # (TIM-REQ-006/007), not the current outcome.
        cur_ev = latest_evaluation(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        prior = cur_ev.outcome if cur_ev else "NONE"
        k = f"{prior}->{cand}"
        buckets[k] = buckets.get(k, 0) + 1
        if cand == "UNCERTAIN":
            # APP §10 step 4 "new required fact counts": the material missing facts, exactly the ones an evaluation of
            # the candidate would raise fact requests for (APP §08, _fact_requests). An unknown fact the result does not
            # depend on (e.g. under a branch already decided) is not required.
            for f in out["missing_facts"]:
                new_required[f] = new_required.get(f, 0) + 1
        det = current_determination(db, s.tenant_id, s.subject_id, odef.obligation_definition_id)
        if det and det.state == "ADOPTED" and det.outcome != cand:
            adopted_conflicts += 1
    return {"subjects_evaluated": total, "transition_buckets": buckets, "missing_fact_counts": new_required,
            "adopted_determination_conflicts": adopted_conflicts, "execution_errors": errors,
            "note": "Aggregate counts only; no tenant identities or fact values exposed to content operations."}
