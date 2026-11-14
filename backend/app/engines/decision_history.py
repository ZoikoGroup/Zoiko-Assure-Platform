"""Historical obligation decision provenance (read-only).

Explains every recorded revision of one obligation lineage from the records the decisions wrote, and reports anything
that no longer proves itself. Nothing here writes, repairs or substitutes a record.

Canonical chain (ZA-DOM-001 §1, "Every link preserves the exact versions used"): SourceSnapshot -> ProvisionVersion ->
RuleVersion -> ApplicabilityEvaluation -> ApplicabilityDetermination -> ObligationInstance -> control satisfaction.
An ObligationInstance "references exact content + attestation" (DOM §5) and is created from an adopted determination
(APP §4: "Adoption: Determination = ADOPTED + AdoptionAttestation ... If APPLIES, activates/revises ObligationInstance").

How each revision is explained:
  * ADOPTED: the determination revision and attestation the revision names (its origin), and the evaluation that
    determination rests on.
  * CLOSED: written by the same attestation transaction that adopted a non-APPLIES determination
    (engines/applicability.attest -> _close_obligation), at the same record sequence. The revision keeps naming the
    original adoption (its columns are unchanged here); the closing decision is the ADOPTED determination revision and
    the attestation recorded at that record sequence in the same determination lineage. Resolved by that sequence, never
    by "latest" (TIM-REQ-015 prohibits latest joins for historical answers).

Problems use catalog.integrity_verdict's classes (MISMATCH, INCOMPLETE, UNVERIFIABLE).
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, ObligationInstance,
                      RuleVersion)
from . import catalog


def evaluation_summary(db, ev: ApplicabilityEvaluation | None) -> dict | None:
    if ev is None:
        return None
    rv = db.get(RuleVersion, ev.rule_version_id)
    return {"evaluation_id": ev.evaluation_id, "outcome": ev.outcome, "rule_version_id": ev.rule_version_id,
            "rule_version": rv.version if rv else None, "content_release_id": rv.content_release_id if rv else None,
            "effective_at": ev.effective_at, "record_cutoff_seq": ev.record_cutoff_seq, "record_seq": ev.record_seq,
            "input_bundle_hash": ev.input_bundle_hash, "evaluator_build": ev.evaluator_build}


def _known(seq: int | None, as_of: int | None) -> int | None:
    """A record_to_seq as the historical perspective knew it: one recorded after the cutoff was not yet known then."""
    return seq if as_of is None or (seq is not None and seq <= as_of) else None


def _det_summary(d: ApplicabilityDetermination | None, as_of: int | None = None) -> dict | None:
    if d is None:
        return None
    return {"revision_id": d.revision_id, "lineage_id": d.lineage_id, "state": d.state, "outcome": d.outcome,
            "needs_review": d.needs_review, "evaluation_id": d.evaluation_id, "valid_from": d.valid_from,
            "record_from_seq": d.record_from_seq, "record_to_seq": _known(d.record_to_seq, as_of), "actor_id": d.actor_id}


def _att_summary(a: AdoptionAttestation | None) -> dict | None:
    if a is None:
        return None
    return {"attestation_id": a.attestation_id, "actor_id": a.actor_id, "decision": a.decision,
            "authority_basis": a.authority_basis, "statement_version": a.statement_version, "signed_at": a.signed_at,
            "record_seq": a.record_seq, "target_revision_id": a.target_revision_id}


def _decision(db, tenant_id: str, det: ApplicabilityDetermination | None, att: AdoptionAttestation | None,
              expect_outcome: set[str], role: str, as_of: int | None = None) -> tuple[dict, list[dict]]:
    """One adoption decision (determination + attestation + evaluation) and the problems that stop it proving itself."""
    p = catalog._problem
    problems: list[dict] = []
    # Another organisation's record is never summarised or described here, only reported (DOM-INV-11). On PostgreSQL
    # row-level security already hides it (it reads as missing); the same answer is given on every backend.
    if det is not None and det.tenant_id != tenant_id:
        problems.append(p("DETERMINATION_MISMATCH", "MISMATCH", role=role, determination_revision_id=det.revision_id))
        det, foreign_det = None, True
    else:
        foreign_det = False
    if att is not None and att.tenant_id != tenant_id:
        att = None
    ev = db.get(ApplicabilityEvaluation, det.evaluation_id) if det else None
    if ev is not None and ev.tenant_id != tenant_id:
        ev = None
    if det is None:
        if not foreign_det:
            problems.append(p("DETERMINATION_MISSING", "INCOMPLETE", role=role))
    else:
        if det.state != "ADOPTED":
            problems.append(p("DETERMINATION_NOT_ADOPTED", "MISMATCH", role=role, state=det.state,
                              determination_revision_id=det.revision_id))
        if det.outcome not in expect_outcome:
            problems.append(p("OUTCOME_DOES_NOT_SUPPORT_DECISION", "MISMATCH", role=role, outcome=det.outcome,
                              determination_revision_id=det.revision_id))
        if ev is None:
            problems.append(p("EVALUATION_MISSING", "INCOMPLETE", role=role, evaluation_id=det.evaluation_id))
        else:
            if (ev.outcome, ev.subject_id, ev.obligation_definition_id) != (det.outcome, det.subject_id,
                                                                            det.obligation_definition_id):
                problems.append(p("EVALUATION_MISMATCH", "MISMATCH", role=role, evaluation_id=ev.evaluation_id))
            # The evaluation must have run a rule of the definition it evaluated (catalog.evaluation_rule_problems).
            problems += [{**x, "role": role}
                         for x in catalog.evaluation_rule_problems(ev, db.get(RuleVersion, ev.rule_version_id))]
    if att is None:
        problems.append(p("ATTESTATION_MISSING", "INCOMPLETE", role=role))
    else:
        if att.decision != "ATTESTED":
            problems.append(p("ATTESTATION_NOT_ADOPTING", "MISMATCH", role=role, attestation_id=att.attestation_id))
        target = db.get(ApplicabilityDetermination, att.target_revision_id)
        if det is not None and (target is None or target.lineage_id != det.lineage_id
                                or att.record_seq != det.record_from_seq):
            # The attestation must sign a revision of this determination lineage, in the transaction that adopted it.
            problems.append(p("ATTESTATION_TARGET_MISMATCH", "MISMATCH", role=role, attestation_id=att.attestation_id))
    return {"determination": _det_summary(det, as_of), "attestation": _att_summary(att),
            "evaluation": evaluation_summary(db, ev)}, problems


def _closing(db, tenant_id: str, rev: ObligationInstance, origin: ApplicabilityDetermination | None):
    """The adopted non-APPLIES determination revision and its attestation recorded at the closure's record sequence."""
    if origin is None:
        return None, None
    det = db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == tenant_id,
        ApplicabilityDetermination.lineage_id == origin.lineage_id,
        ApplicabilityDetermination.record_from_seq == rev.record_from_seq,
        ApplicabilityDetermination.state == "ADOPTED")
        .order_by(ApplicabilityDetermination.revision_id)).scalars().first()
    att = db.execute(select(AdoptionAttestation).where(
        AdoptionAttestation.tenant_id == tenant_id, AdoptionAttestation.record_seq == rev.record_from_seq,
        AdoptionAttestation.target_type == "ApplicabilityDetermination")
        .order_by(AdoptionAttestation.attestation_id)).scalars().all()
    lineage_revs = {r for r in db.execute(select(ApplicabilityDetermination.revision_id).where(
        ApplicabilityDetermination.tenant_id == tenant_id,
        ApplicabilityDetermination.lineage_id == origin.lineage_id)).scalars()}
    att = next((a for a in att if a.target_revision_id in lineage_revs), None)
    return det, att


def _baseline_unplaced(h: dict, initial: bool) -> bool:
    """Whether a state taken from the migration-0008 BASELINE event cannot be placed at the cutoff. 0008 recorded each
    record's state at migration time but stamped it with the record's own record_seq, so at a cutoff before the
    migration a later state (an approval, a revocation, a fulfilment) would appear before it was recorded (STA §10
    "historical 'what was known then' still shows it was not approved before its record time"). Only the initial state
    is exact at every earlier cutoff, because these lifecycles only move forward."""
    from .assurance import BASELINE_SOURCE
    return h.get("source") == BASELINE_SOURCE and not initial


def governance_records_at(db, tenant_id: str, *, exceptions, compensations, occurrences, as_of: int) -> dict:
    """Exception, compensation and occurrence state as recorded by record sequence ``as_of``, for a historical dossier
    (TIM-REQ-006/015; STA §09, §10, §13). Only event history recorded by the cutoff is used; where it is missing,
    incomplete or inconsistent the state fields are None with a ``problem`` (…_HISTORY_MISSING / _INCOMPLETE /
    _INCONSISTENT), never the current projection. Read-only.

    Returns {"exceptions": {id: {"state", "approved_by", "historical_state"}}, "compensations": {id: {"status",
    "approved_by", "historical_state"}}, "occurrences": {id: {"fulfilled_at", "evidence_ids", "disputed",
    "historical_state"}}}; ``historical_state`` = {"source", "problem", "event_ids"}."""
    from ..models import ExceptionEvent
    from .assurance import compensation_states_at, exception_states_at, occurrence_states_at

    def hist(h, problem=None):
        return {"source": None if problem else h["source"], "problem": problem or h["problem"],
                "event_ids": h["event_ids"]}
    out: dict = {"exceptions": {}, "compensations": {}, "occurrences": {}}
    exc = exception_states_at(db, tenant_id, exceptions, as_of)
    approvals = {e.exception_id: e.actor_id for e in db.execute(select(ExceptionEvent).where(
        ExceptionEvent.tenant_id == tenant_id,
        ExceptionEvent.event_id.in_([i for h in exc.values() for i in h["event_ids"]] or ["-"]),
        ExceptionEvent.event_type == "APPROVED").order_by(ExceptionEvent.record_seq)).scalars()}
    for xid, h in exc.items():
        problem = "EXCEPTION_HISTORY_INCOMPLETE" if _baseline_unplaced(h, h["state"] == "PENDING_APPROVAL") else None
        ok = h["state"] is not None and problem is None
        out["exceptions"][xid] = {"state": h["state"] if ok else None,
                                  "approved_by": approvals.get(xid) if ok else None, "historical_state": hist(h, problem)}
    for cid, h in compensation_states_at(db, tenant_id, compensations, as_of).items():
        problem = "COMPENSATION_HISTORY_INCOMPLETE" if _baseline_unplaced(h, h["state"] == "PENDING_APPROVAL") else None
        ok = h["state"] is not None and problem is None
        out["compensations"][cid] = {"status": h["state"] if ok else None,
                                     "approved_by": h["approved_by"] if ok else None, "historical_state": hist(h, problem)}
    for oid, h in occurrence_states_at(db, tenant_id, occurrences, as_of).items():
        problem = "OCCURRENCE_HISTORY_INCOMPLETE" if _baseline_unplaced(h, h["fulfilled_at"] is None) else None
        ok = h["problem"] is None and problem is None
        out["occurrences"][oid] = {"fulfilled_at": h["fulfilled_at"] if ok else None,
                                   "evidence_ids": h["evidence_ids"] if ok else None,
                                   "disputed": h["disputed"] if ok else None, "historical_state": hist(h, problem)}
    return out


def obligation_decision_history(db, tenant_id: str, lineage_id: str, as_of_record_seq: int | None = None) -> dict:
    """Every revision of one obligation recorded by `as_of_record_seq` (all if None), oldest first, each with the
    decision that created it and the evaluation that decision rests on, plus an integrity verdict for the whole chain."""
    q = select(ObligationInstance).where(ObligationInstance.tenant_id == tenant_id,
                                         ObligationInstance.lineage_id == lineage_id)
    if as_of_record_seq is not None:
        q = q.where(ObligationInstance.record_from_seq <= as_of_record_seq)
    revs = db.execute(q.order_by(ObligationInstance.record_from_seq, ObligationInstance.revision_id)).scalars().all()
    entries, problems = [], []
    for rev in revs:
        origin = db.get(ApplicabilityDetermination, rev.determination_revision_id)
        origin_att = db.get(AdoptionAttestation, rev.attestation_id)
        origin_view, origin_problems = _decision(db, tenant_id, origin, origin_att, {"APPLIES"}, "origin",
                                                 as_of_record_seq)
        if origin is not None and origin.tenant_id == tenant_id and (origin.subject_id, origin.obligation_definition_id) != (
                rev.subject_id, rev.obligation_definition_id):  # another organisation's: already reported by _decision
            origin_problems.append(catalog._problem("DETERMINATION_MISMATCH", "MISMATCH", role="origin",
                                                    determination_revision_id=origin.revision_id))
        # Visible to the historical perspective: a record_to_seq after the cutoff was not yet known then.
        record_to = _known(rev.record_to_seq, as_of_record_seq)
        entry = {"revision_id": rev.revision_id, "lifecycle": rev.lifecycle, "valid_from": rev.valid_from,
                 "valid_to": rev.valid_to, "record_from_seq": rev.record_from_seq, "record_to_seq": record_to,
                 "origin": origin_view}
        rev_problems = list(origin_problems)
        if rev.lifecycle == "CLOSED":
            cdet, catt = _closing(db, tenant_id, rev, origin)
            if cdet is None and catt is None:
                rev_problems.append(catalog._problem("CLOSURE_DECISION_UNRESOLVED", "INCOMPLETE",
                                                     revision_id=rev.revision_id))
                entry["closure"] = None
            else:
                entry["closure"], closing_problems = _decision(db, tenant_id, cdet, catt,
                                                               {"DOES_NOT_APPLY"}, "closure", as_of_record_seq)
                rev_problems += closing_problems
        entry["problems"] = rev_problems
        problems += [{**x, "revision_id": rev.revision_id} for x in rev_problems]
        entries.append(entry)
    return {"obligation_lineage_id": lineage_id, "as_of_record_seq": as_of_record_seq, "revisions": entries,
            "integrity": catalog.integrity_verdict(problems)}
