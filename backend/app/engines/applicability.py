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
from . import idempotency, rules
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
    if fd["sensitivity"] in ("RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"):
        return "••• (redacted)"
    return value


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
    cur = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == tenant_id,
                                                      ObligationInstance.subject_id == subject_id,
                                                      ObligationInstance.obligation_definition_id == odef_id,
                                                      ObligationInstance.record_to_seq.is_(None))).scalars().first()
    if not (cur and cur.lifecycle == "ADOPTED"):
        return None
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
    plan = []
    for s in subjects:
        for od in db.execute(select(ObligationDefinition).where(ObligationDefinition.subject_type == s.subject_type)
                             .order_by(ObligationDefinition.key, ObligationDefinition.obligation_definition_id)).scalars():
            versions = db.execute(select(RuleVersion).where(RuleVersion.obligation_definition_id == od.obligation_definition_id,
                                                            RuleVersion.status == "PUBLISHED")
                                  .order_by(RuleVersion.version.desc())).scalars().all()
            if any(fd.key in v.required_facts for v in versions):
                plan.append((s, od))
    return plan


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
    # resolve fact requests satisfied by new knowledge
    for fr in db.execute(select(FactRequest).where(FactRequest.tenant_id == tenant_id,
                                                   FactRequest.subject_id == holder.subject_id,
                                                   FactRequest.fact_definition_id == fd.fact_definition_id,
                                                   FactRequest.status.in_(["OPEN", "IN_PROGRESS"]))).scalars():
        fr.status = "RESOLVED"
        fr.resolution = "Resolved by recorded FactAssertion; linked evaluations re-run."
    return results


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
