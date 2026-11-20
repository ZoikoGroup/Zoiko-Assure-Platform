"""Obligations, controls, tests, period assurance, exceptions, compensation, issues (ZA-STA-001)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import func, or_, select

from ..engines import assurance as asr
from ..db import CELL_ID
from ..engines import catalog, decision_history, governance, satisfaction, selection, test_results
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot,
                      Compensation, RuleVersion,
                      CompensationEvent, ControlDefinition, ControlEvaluation, ControlImplementation, ControlTestPolicy,
                      EvidenceArtifact, ExceptionEvent, ExceptionRecord, Issue, IssueEvent, ObligationDefinition,
                      ObligationInstance, ObligationOccurrence, ObligationOccurrenceEvent, RiskAcceptance,
                      SatisfactionExpressionVersion, SourceRun, Subject, TestDefinition, TestRun)
from ..platform import audit, current_watermark, digest, ensure_utc, next_record_seq, to_dict
from ..security import DOSSIER_READ, Ctx, EntityOf, authorize, entity_of
from .applicability import Instant

router = APIRouter()


def _find_obl(db, tenant, lineage) -> ObligationInstance | None:
    """The current revision of one of this organisation's obligations (record_to_seq IS NULL; ZA-DAT-001 §14), None if
    there is none. Every lifecycle change closes the prior revision in the transaction that records the next, under the
    decision-scope lock, so a lineage has one current revision; with two, neither is chosen (selection.AmbiguousSelection):
    which one answers decides the dossier shown and the legal entity authorization resolves to."""
    return selection.one_or_none(db.execute(select(ObligationInstance).where(
        ObligationInstance.tenant_id == tenant, ObligationInstance.lineage_id == lineage,
        ObligationInstance.record_to_seq.is_(None))).scalars(),
        record_type="ObligationInstance", selection="obligation_current", id_attr="revision_id")


def _obl_as_of(db, tenant, lineage, as_of: int) -> ObligationInstance | None:
    """The revision of one of this organisation's obligations that was current at record sequence ``as_of``: the one
    whose record slice [record_from_seq, record_to_seq) contains it (ZA-TIM-001 half-open semantics; a revision closed at
    K was replaced at K). None if the lineage was not yet recorded. Record slices of one lineage do not overlap, so two
    revisions current at one sequence are refused, never one picked by recency (selection.AmbiguousSelection)."""
    return selection.one_or_none(db.execute(select(ObligationInstance).where(
        ObligationInstance.tenant_id == tenant, ObligationInstance.lineage_id == lineage,
        ObligationInstance.record_from_seq <= as_of,
        or_(ObligationInstance.record_to_seq.is_(None), ObligationInstance.record_to_seq > as_of))).scalars(),
        record_type="ObligationInstance", selection="obligation_as_of", id_attr="revision_id")


def _current_obl(db, tenant, lineage) -> ObligationInstance:
    o = _find_obl(db, tenant, lineage) if lineage else None
    if not o:
        raise HTTPException(404, "obligation not found")
    return o


def _missing(id_field: str, value: str | None, *fields: str) -> dict:
    """A referenced record that cannot be found: its ID, the display fields as null and ``missing: true``."""
    return {id_field: value, **dict.fromkeys(fields), "missing": True}


def _lineage_entity(db, tenant, lineage) -> str | None:
    o = _find_obl(db, tenant, lineage) if lineage else None
    return o.entity_id if o else None


def _exception_entity(db, x: ExceptionRecord) -> str | None:
    # An exception on an obligation belongs to that obligation's legal entity; any other target resolves to no single
    # entity, so only organisation-wide roles reach it (fail closed).
    return _lineage_entity(db, x.tenant_id, x.target_id) if x.target_type == "OBLIGATION" else None


# Stage 4: how each dossier resource resolves to its legal entity, so entity-scoped roles reach their entity only.
OBLIGATION = EntityOf("obligation", lambda db, tenant, p: _find_obl(db, tenant, p["lineage"]) if p.get("lineage") else None)
SNAPSHOT = entity_of(AssuranceSnapshot, "snapshot_id", "snapshot")
CONTROL = entity_of(ControlImplementation, "ci_id", "control")


def _evaluation_entity(db, e: ControlEvaluation) -> str | None:
    # A control evaluation belongs to the legal entity of the control implementation it evaluated (this organisation's).
    ci = db.get(ControlImplementation, e.control_implementation_id)
    return ci.entity_id if ci is not None and ci.tenant_id == e.tenant_id else None


CONTROL_EVALUATION = entity_of(ControlEvaluation, "control_evaluation_id", "control evaluation",
                               entity=_evaluation_entity)
OCCURRENCE = entity_of(ObligationOccurrence, "occurrence_id", "occurrence",
                       entity=lambda db, o: _lineage_entity(db, o.tenant_id, o.obligation_lineage_id))
COMPENSATION = entity_of(Compensation, "comp_id", "compensation",
                         entity=lambda db, c: _lineage_entity(db, c.tenant_id, c.obligation_lineage_id))
EXCEPTION = entity_of(ExceptionRecord, "exception_id", "exception", entity=_exception_entity)


# The register shows a snapshot without its segment trace and dependency manifest (read by the snapshot and dossier
# views), so it never loads those two columns.
_REGISTER_SNAPSHOT_COLUMNS = tuple(c for c in AssuranceSnapshot.__table__.columns
                                   if c.key not in ("segments", "dependency_manifest"))


@router.get("/obligations")
def obligations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    """The organisation's current obligation revisions. Each related table is read once for the whole register, not
    once per obligation (one query per row for the definition, the subject, the latest snapshot and the approved
    exceptions grew with the register); every row is built exactly as it was from per-row reads."""
    db, tenant = ctx.db, ctx.tenant
    current = (ObligationInstance.tenant_id == tenant, ObligationInstance.record_to_seq.is_(None))
    # Record order (adoption/revision sequence), then revision ID: never physical row order.
    rows = db.execute(select(ObligationInstance).where(*current)
                      .order_by(ObligationInstance.record_from_seq, ObligationInstance.revision_id)).scalars().all()
    if not rows:
        return []
    # The related rows of exactly these obligations, selected by the same predicate as the register itself.
    definitions = {r.obligation_definition_id: r for r in db.execute(
        select(ObligationDefinition.obligation_definition_id, ObligationDefinition.key, ObligationDefinition.title,
               ObligationDefinition.schedule_kind)
        .where(ObligationDefinition.obligation_definition_id.in_(
            select(ObligationInstance.obligation_definition_id).where(*current))))}
    subjects = {s.subject_id: s for s in db.execute(select(Subject).where(
        Subject.subject_id.in_(select(ObligationInstance.subject_id).where(*current)))).scalars()}
    # Each lineage's latest snapshot in the order the per-row read used (created_at, then snapshot ID, both descending),
    # and the approved exceptions per target, over the organisation's rows. Every lineage of the organisation has a
    # current revision, so these are the register's rows; a row of another target is never looked up. Neither read is
    # joined to the obligation table: a correlated join, planned before a bulk load is analysed, re-read the obligation
    # rows once per snapshot or exception. Scoped to the caller's organisation in the query itself, as every other read
    # here, not only by row-level security (which the SQLite runtime does not have).
    ranked = (select(*_REGISTER_SNAPSHOT_COLUMNS, func.row_number().over(
        partition_by=AssuranceSnapshot.obligation_lineage_id,
        order_by=(AssuranceSnapshot.created_at.desc(), AssuranceSnapshot.snapshot_id.desc())).label("latest_rank"))
        .where(AssuranceSnapshot.tenant_id == tenant).subquery())
    latest = {r.obligation_lineage_id: r for r in db.execute(select(ranked).where(ranked.c.latest_rank == 1))}
    open_exceptions = dict(db.execute(
        select(ExceptionRecord.target_id, func.count()).where(ExceptionRecord.tenant_id == tenant,
                                                              ExceptionRecord.state == "APPROVED")
        .group_by(ExceptionRecord.target_id)).all())
    out = []
    for o in rows:
        d = to_dict(o)
        od, subject, snap = (definitions.get(o.obligation_definition_id), subjects.get(o.subject_id),
                             latest.get(o.lineage_id))
        # Referenced by ID only (no foreign key from tenant records to the catalogue): a definition or subject that
        # cannot be found is reported as missing, never a failure of the whole register.
        d["definition"] = ({"key": od.key, "title": od.title, "schedule_kind": od.schedule_kind} if od else
                           _missing("obligation_definition_id", o.obligation_definition_id, "key", "title", "schedule_kind"))
        d["subject"] = to_dict(subject) if subject else _missing("subject_id", o.subject_id, "display_name")
        # Serialised by to_dict from an unattached snapshot holding the register's columns, as the full row was.
        d["latest_snapshot"] = ({k: v for k, v in to_dict(AssuranceSnapshot(**{
            c.key: snap._mapping[c.key] for c in _REGISTER_SNAPSHOT_COLUMNS})).items()
            if k not in ("segments", "dependency_manifest")} if snap else None)
        d["open_exceptions"] = open_exceptions.get(o.lineage_id, 0)
        out.append(d)
    return out


@router.get("/obligations/{lineage}")
def dossier(lineage: str, as_of_record_seq: int | None = Query(default=None, ge=0),
            ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):
    """WF-STA-01 Obligation Assurance Dossier, with historical bitemporal replay support (ZA-TIM-001 §12)."""
    now_seq = current_watermark(ctx.db)["record_seq"]
    if as_of_record_seq is not None and as_of_record_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"as_of_record_seq is beyond the current record sequence {now_seq}"})
    if as_of_record_seq is not None:
        o = _obl_as_of(ctx.db, ctx.tenant, lineage, as_of_record_seq)
        if not o:
            raise HTTPException(404, "obligation not found as of requested record sequence")
    else:
        o = _current_obl(ctx.db, ctx.tenant, lineage)
    od = ctx.db.get(ObligationDefinition, o.obligation_definition_id)
    det = ctx.db.get(ApplicabilityDetermination, o.determination_revision_id)
    att = ctx.db.get(AdoptionAttestation, o.attestation_id)
    # Only this organisation's records are shown; another's is treated as not found (DOM-INV-11), as on PostgreSQL.
    det = det if det is not None and det.tenant_id == ctx.tenant else None
    att = att if att is not None and att.tenant_id == ctx.tenant else None
    exprs = satisfaction.versions(ctx.db, ctx.tenant, lineage)
    if as_of_record_seq is not None:
        exprs = [e for e in exprs if e.record_seq <= as_of_record_seq]
    keys = set()
    for e in exprs:
        keys |= asr._control_keys(e.ast)
    subject = ctx.db.get(Subject, o.subject_id)
    controls = []
    now = datetime.now(timezone.utc)
    for k in sorted(keys) if subject else []:  # without its subject no implementation can be resolved
        ci = asr._resolve_impl(ctx.db, ctx.tenant, k, subject, now - timedelta(days=1), now,
                               as_of_record_seq if as_of_record_seq is not None else 10**15)
        cd = ctx.db.execute(select(ControlDefinition).where(ControlDefinition.key == k)).scalars().first()
        controls.append({"control_key": k, "title": cd.title if cd else k,
                         "implementation": to_dict(ci) if ci else None})
    rev_q = select(ObligationInstance).where(ObligationInstance.tenant_id == ctx.tenant,
                                             ObligationInstance.lineage_id == lineage)
    if as_of_record_seq is not None:
        rev_q = rev_q.where(ObligationInstance.record_from_seq <= as_of_record_seq)
    as_known = lambda row: _as_known(to_dict(row), as_of_record_seq)  # noqa: E731
    revisions = [as_known(r) for r in ctx.db.execute(rev_q.order_by(ObligationInstance.record_from_seq,
                                                                    ObligationInstance.revision_id)).scalars()]

    snap_q = select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == ctx.tenant,
                                             AssuranceSnapshot.obligation_lineage_id == lineage)
    if as_of_record_seq is not None:
        snap_q = snap_q.where(AssuranceSnapshot.record_cutoff_seq <= as_of_record_seq)
    by_id = {e.expression_version_id: e for e in satisfaction.versions(ctx.db, ctx.tenant, lineage)}
    snap_rows = ctx.db.execute(snap_q.order_by(AssuranceSnapshot.created_at.desc(),
                                               AssuranceSnapshot.snapshot_id.desc())).scalars().all()
    unplaced = 0
    if as_of_record_seq is not None:
        # A snapshot computed later for an earlier cutoff (AS_KNOWN_THEN) was not known at that cutoff: a historical read
        # lists only snapshots recorded by it (TIM-REQ-005/006). Their record sequence is the one their creation took
        # (the assurance.snapshot_created event written in the same transaction; the row has no column for it).
        recorded = _snapshot_record_seqs(ctx.db, ctx.tenant, [s.snapshot_id for s in snap_rows])
        unplaced = sum(1 for s in snap_rows if s.snapshot_id not in recorded)
        snap_rows = [s for s in snap_rows if recorded.get(s.snapshot_id, as_of_record_seq + 1) <= as_of_record_seq]
    snapshots = [{**{k: v for k, v in to_dict(s).items() if k != "dependency_manifest"},
                  "expression_pins": _expression_pins(s, by_id)} for s in snap_rows]

    # Exceptions, compensations and occurrences: a historical read lists those recorded by the cutoff, with their state
    # fields as their append-only event history recorded them by then (decision_history.governance_records_at); the
    # in-place projections are never shown as historical state. The current perspective lists the rows as they are.
    def known(q, model):
        return q.where(model.record_seq <= as_of_record_seq) if as_of_record_seq is not None else q
    # The orders GET /exceptions and GET /compensations use (newest valid_from first, then ID).
    exceptions = ctx.db.execute(known(select(ExceptionRecord).where(ExceptionRecord.tenant_id == ctx.tenant,
                                                                    ExceptionRecord.target_id == lineage),
                                      ExceptionRecord).order_by(ExceptionRecord.valid_from.desc(),
                                                                ExceptionRecord.exception_id)).scalars().all()
    compensations = ctx.db.execute(known(select(Compensation).where(Compensation.tenant_id == ctx.tenant,
                                                                    Compensation.obligation_lineage_id == lineage),
                                         Compensation).order_by(Compensation.valid_from.desc(),
                                                                Compensation.compensation_id)).scalars().all()
    occurrences = ctx.db.execute(known(select(ObligationOccurrence).where(
        ObligationOccurrence.tenant_id == ctx.tenant, ObligationOccurrence.obligation_lineage_id == lineage),
        ObligationOccurrence).order_by(ObligationOccurrence.due_at, ObligationOccurrence.occurrence_id)).scalars().all()
    then = decision_history.governance_records_at(
        ctx.db, ctx.tenant, exceptions=exceptions, compensations=compensations, occurrences=occurrences,
        as_of=as_of_record_seq) if as_of_record_seq is not None else None

    def at_cutoff(kind: str, key: str, row) -> dict:
        return {**to_dict(row), **then[kind][getattr(row, key)]} if then is not None else to_dict(row)
    provenance = _provenance(ctx, det, as_of_record_seq, o=o)
    history = decision_history.obligation_decision_history(ctx.db, ctx.tenant, lineage, as_of_record_seq)
    issues, unplaced_issues = _issues_as_of(ctx, lineage, as_of_record_seq)
    return {
        "obligation": as_known(o),
        "definition": to_dict(od) if od else _missing("obligation_definition_id", o.obligation_definition_id, "key", "title"),
        "subject": to_dict(subject) if subject else _missing("subject_id", o.subject_id, "display_name"),
        # In the definition's link order, never the physical row order an UPDATE can change (catalog.definition_provisions).
        "provisions": [to_dict(p) for p in catalog.definition_provisions(ctx.db, od)] if od else [],
        "determination": as_known(det) if det else None, "attestation": to_dict(att) if att else None,
        "revisions": revisions,
        "expression_versions": [{**to_dict(e), "control_mappings": satisfaction.view(ctx.db, e)["control_mappings"]}
                                for e in exprs], "controls": controls,
        "snapshots": snapshots,
        "exceptions": [at_cutoff("exceptions", "exception_id", x) for x in exceptions],
        "compensations": [at_cutoff("compensations", "compensation_id", c) for c in compensations],
        "occurrences": [at_cutoff("occurrences", "occurrence_id", x) for x in occurrences],
        "issues": issues,
        "regulatory_provenance": provenance,
        "decision_history": history,
        "as_of_record_seq": as_of_record_seq,
        # TIM-REQ-006/017: the record-time perspective this answer resolved to, and the valid time the control
        # resolution used. A historical read pins record time only; controls are resolved for the last day.
        "record_watermark": {"cell_id": CELL_ID, "record_seq": as_of_record_seq if as_of_record_seq is not None else now_seq,
                             "perspective": "AS_OF_RECORD_SEQ" if as_of_record_seq is not None else "CURRENT"},
        "controls_valid_time": [now - timedelta(days=1), now],
        "historical_limitations": [
            "Exceptions, compensations and occurrences are listed if recorded by the cutoff, with state, approver, "
            "fulfilment, evidence and dispute reconstructed from their recorded events at the cutoff; where that "
            "history is missing, incomplete (a migration-0008 baseline that cannot be placed at the cutoff) or "
            "inconsistent, those fields are null and historical_state.problem says why.",
            "Issues are listed if recorded by the cutoff, with state and owner reconstructed from their recorded "
            "events at the cutoff; an issue whose history does not prove itself shows state null and its "
            "historical_state.problem.",
            *([f"{unplaced} assurance snapshot(s) have no recorded creation sequence and are not listed: whether they "
               "were known at the cutoff cannot be shown."] if unplaced else []),
            *([f"{unplaced_issues} issue(s) have no recorded history that places them at or before the cutoff and are "
               "not listed: whether they existed then, and in which state, cannot be shown."] if unplaced_issues else [])]
        if as_of_record_seq is not None else [],
    }


def _issues_as_of(ctx: Ctx, lineage: str, as_of: int | None) -> tuple[list[dict], int]:
    """The obligation's issues, current or as known at ``as_of`` (governance.issue_states_at). A historical read lists
    only issues recorded by the cutoff, with the state and owner recorded by then; later remediation, verification or
    closure events are not shown (TIM-REQ-006/015). Returns (issues, number not placeable at the cutoff)."""
    rows = ctx.db.execute(select(Issue).where(Issue.tenant_id == ctx.tenant, Issue.dedupe_key.like(f"%{lineage}%"))
                          .order_by(Issue.created_at, Issue.issue_id)).scalars().all()
    # STA §17 constituent control issues of each obligation-level issue (governance.constituent_control_issues: derived
    # read-only from the issue's source snapshot; nothing is stored).
    seqs = _snapshot_record_seqs(ctx.db, ctx.tenant, sorted({i.source_ref_id for i in rows
                                                             if i.source_ref_type == "AssuranceSnapshot"}))

    def with_trace(i: Issue, d: dict) -> dict:
        trace = governance.constituent_control_issues(ctx.db, ctx.tenant, i, snapshot_seq=seqs.get(i.source_ref_id),
                                                      as_of=as_of)
        return {**d, "constituent_control_issues": trace} if trace is not None else d
    if as_of is None:
        return [with_trace(i, to_dict(i)) for i in rows], 0
    hist = governance.issue_states_at(ctx.db, ctx.tenant, rows, as_of)
    out, unplaced = [], 0
    for i in rows:
        h = hist[i.issue_id]
        if h["known"] is None:
            unplaced += 1
        if not h["known"]:
            continue
        out.append(with_trace(i, {**to_dict(i), "state": h["state"], "owner_actor_id": h["owner_actor_id"],
                                  "historical_state": {k: h[k] for k in ("source", "problem", "event_ids", "created_seq")}}))
    return out, unplaced


def _snapshot_record_seqs(db, tenant: str, snapshot_ids: list[str]) -> dict[str, int]:
    """The record sequence at which each snapshot was persisted: that of its assurance.snapshot_created event, which
    asr.compute_snapshot writes in the snapshot's own transaction (DAT-REQ-014). A snapshot without one is left out."""
    from ..models import OutboxEvent
    if not snapshot_ids:
        return {}
    out: dict[str, int] = {}
    for sid, seq in db.execute(select(OutboxEvent.aggregate_id, OutboxEvent.record_seq).where(
            OutboxEvent.tenant_id == tenant, OutboxEvent.aggregate_type == "AssuranceSnapshot",
            OutboxEvent.event_type == "assurance.snapshot_created", OutboxEvent.aggregate_id.in_(snapshot_ids))):
        out[sid] = min(seq, out.get(sid, seq))
    return out


def _as_known(row: dict, as_of: int | None) -> dict:
    """A revision as a historical perspective knew it: its record_to_seq is shown only if recorded by the cutoff
    (TIM-REQ-006; a later closure was not yet known)."""
    if as_of is not None and row.get("record_to_seq") is not None and row["record_to_seq"] > as_of:
        return {**row, "record_to_seq": None}
    return row


def _expression_pins(snap, by_id: dict) -> dict:
    """Whether the SatisfactionExpression versions a snapshot pinned still exist for this obligation and still hash to
    their recorded hash (STA-REQ-021). Snapshots pin version IDs only; pinning their hashes in the snapshot manifest is
    the unresolved snapshot-manifest decision (TIM-ADR-04), so each version's own recorded hash is what is checked."""
    pinned = sorted({*((snap.dependency_manifest or {}).get("expression_versions") or []),
                     *(seg.get("expression_version_id") for seg in (snap.segments or []) if seg.get("expression_version_id"))})
    missing = [i for i in pinned if i not in by_id]
    altered = [i for i in pinned if i in by_id and not satisfaction.hash_intact(by_id[i])]
    return {"pinned": pinned, "missing": missing, "hash_mismatch": altered,
            "status": "MISMATCH" if altered else "INCOMPLETE" if missing else "INTACT"}


# ------------------------------------------------------------------ satisfaction expressions and control mappings
class ExpressionVersionIn(BaseModel):
    """A new SatisfactionExpressionVersion (ZA-STA-001 §07). Control mappings are its CONTROL leaves: a selector
    (``control_key``) or an exact implementation (``control_key`` + ``control_implementation_id``)."""
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    ast: dict
    valid_from: datetime
    valid_to: datetime | None = None
    rationale: str = Field(min_length=1, max_length=4000)  # STA §16 SatisfactionExpressionVersion "mapping rationale"


@router.get("/obligations/{lineage}/expression-versions")
def expression_versions(lineage: str, as_of_record_seq: int | None = Query(default=None, ge=0),
                        ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):
    """Every SatisfactionExpressionVersion of one obligation, oldest first, with its control mappings. Versions are
    immutable; each snapshot names the version it used for each segment. ``in_force_now`` marks the version the
    evaluator would select for the present instant (the highest version whose valid period contains it)."""
    now_seq = current_watermark(ctx.db)["record_seq"]
    if as_of_record_seq is not None and as_of_record_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"as_of_record_seq is beyond the current record sequence {now_seq}"})
    if as_of_record_seq is not None:
        if not _obl_as_of(ctx.db, ctx.tenant, lineage, as_of_record_seq):
            raise HTTPException(404, "obligation not found as of requested record sequence")
    else:
        _current_obl(ctx.db, ctx.tenant, lineage)
    vs = satisfaction.versions(ctx.db, ctx.tenant, lineage)
    if as_of_record_seq is not None:
        vs = [e for e in vs if e.record_seq <= as_of_record_seq]
    current = satisfaction.in_force_at(vs, datetime.now(timezone.utc))
    return [{**satisfaction.view(ctx.db, e), "in_force_now": current is not None and e is current} for e in vs]


@router.post("/obligations/{lineage}/expression-versions", status_code=201)
def create_expression_version(lineage: str, body: ExpressionVersionIn,
                              ctx: Ctx = Depends(authorize("assurance_review", human=True, entity=OBLIGATION))):
    """Release a new expression version: create, change or retire control mappings (STA §07 "Expression versioning";
    DOM §9.2). An Assurance Reviewer approves the design (STA §22). Earlier versions and snapshots never change."""
    if not body.rationale.strip():
        raise HTTPException(422, {"error": "RATIONALE_REQUIRED", "message": "a mapping rationale is required"})
    _current_obl(ctx.db, ctx.tenant, lineage)
    e = satisfaction.create_version(ctx, lineage_id=lineage, ast=body.ast, valid_from=body.valid_from,
                                    valid_to=body.valid_to, rationale=body.rationale)
    ctx.db.commit()
    return satisfaction.view(ctx.db, e)


def _provenance(ctx: Ctx, det, as_of_record_seq: int | None = None, *, o: ObligationInstance | None = None) -> dict:
    """Obligation -> adopted determination -> its evaluation -> the exact RuleVersion -> requirement versions,
    provisions and source snapshots (DOM §1 canonical chain; DOM §16 end-to-end trace).

    A link that cannot be followed is reported (INCOMPLETE, with the ID that could not be resolved), never shown as an
    absent provenance: another organisation's determination or evaluation is treated as not found (DOM-INV-11; row-level
    security hides it on PostgreSQL). The checks that the decision records agree with each other and with the obligation
    are decision_history's; this section adds that the evaluation ran a rule of the obligation's own definition."""
    p = catalog._problem
    head = [] if o is None or ctx.db.get(ObligationDefinition, o.obligation_definition_id) else [
        p("OBLIGATION_DEFINITION_MISSING", "INCOMPLETE", obligation_definition_id=o.obligation_definition_id)]
    if det is not None and det.tenant_id != ctx.tenant:
        det = None
    ev = ctx.db.get(ApplicabilityEvaluation, det.evaluation_id) if det else None
    if ev is not None and ev.tenant_id != ctx.tenant:
        ev = None
    if ev is None:
        det_ref = det.revision_id if det else (o.determination_revision_id if o else None)
        missing = (p("DETERMINATION_MISSING", "INCOMPLETE", determination_revision_id=det_ref)
                   if det is None else p("EVALUATION_MISSING", "INCOMPLETE", evaluation_id=det.evaluation_id))
        return {"binding_basis": "INCOMPLETE", "missing": True, "rule_version_id": None,
                "determination_revision_id": det_ref, "integrity": catalog.integrity_verdict([*head, missing])}
    rule = ctx.db.get(RuleVersion, ev.rule_version_id)
    evaluation = {"evaluation_id": ev.evaluation_id, "outcome": ev.outcome, "effective_at": ev.effective_at,
                  "record_cutoff_seq": ev.record_cutoff_seq, "input_bundle_hash": ev.input_bundle_hash,
                  "evaluator_build": ev.evaluator_build}
    # The determination must rest on this evaluation's result for the same subject and definition (DOM §1 chain).
    chain = [] if (ev.outcome, ev.subject_id, ev.obligation_definition_id) == (
        det.outcome, det.subject_id, det.obligation_definition_id) else [
        catalog._problem("EVALUATION_MISMATCH", "MISMATCH", evaluation_id=ev.evaluation_id,
                         determination_revision_id=det.revision_id)]
    chain = [*head, *chain, *catalog.evaluation_rule_problems(ev, rule)]
    if rule is not None:
        out = catalog.with_problems(catalog.trace_rule_version(ctx.db, rule, as_of_record_seq), chain)
    else:
        out = {"binding_basis": "INCOMPLETE", "rule_version_id": ev.rule_version_id, "missing": True,
               "integrity": catalog.integrity_verdict(
                   [catalog._problem("RULE_VERSION_MISSING", "INCOMPLETE", rule_version_id=ev.rule_version_id), *chain])}
    out["evaluation"] = evaluation
    out["determination_revision_id"] = det.revision_id
    return out


class PeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    period_from: datetime
    period_to: datetime
    record_cutoff_seq: int | None = None
    # ZA-TIM-001 §01: "User-facing perspectives are explicit: CURRENT, AS_KNOWN_THEN, RECONSTRUCTED_NOW or
    # CUSTOM_BITEMPORAL" (persisted on the immutable snapshot, assurance_snapshot.perspective varchar(32)).
    perspective: Literal["CURRENT", "AS_KNOWN_THEN", "RECONSTRUCTED_NOW", "CUSTOM_BITEMPORAL"] = "RECONSTRUCTED_NOW"


def _require_known_cutoff(db, cutoff: int | None) -> None:
    """TIM-REQ-019: no answer for a record cutoff newer than the data watermark, so a snapshot never pins knowledge that
    does not exist yet (records written later would otherwise fall inside its cutoff and it could not be reproduced).
    The same refusal as every historical read and as applicability evaluation (422 RECORD_SEQ_IN_FUTURE)."""
    now_seq = current_watermark(db)["record_seq"]
    if cutoff is not None and cutoff > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"record_cutoff_seq is beyond the current record sequence {now_seq}"})


@router.post("/obligations/{lineage}/snapshots")
def create_snapshot(lineage: str, body: PeriodIn, ctx: Ctx = Depends(authorize("request_evaluation", "package"))):
    # A persisted snapshot is an assurance record and can open issues; read-only roles may only preview.
    _require_known_cutoff(ctx.db, body.record_cutoff_seq)
    persp = body.perspective if body.record_cutoff_seq is None else "AS_KNOWN_THEN"
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq, perspective=persp,
                             actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return r


@router.post("/obligations/{lineage}/preview")
def preview(lineage: str, body: PeriodIn, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):  # computes and rolls back
    _require_known_cutoff(ctx.db, body.record_cutoff_seq)
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
                             period_to=body.period_to, record_cutoff_seq=body.record_cutoff_seq,
                             perspective=body.perspective, actor_id=ctx.actor.actor_id, persist=False)
    ctx.db.rollback()
    return r


class ComparisonSideIn(BaseModel):
    """One knowledge state (ZA-TIM-001 WF-TIM-02 "Two explicit record watermarks / snapshot IDs"): a frozen snapshot of
    this obligation and period, or an explicit record cutoff to reconstruct at (named as PeriodIn names it). Exactly
    one."""
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    record_cutoff_seq: StrictInt | None = Field(default=None, ge=0)  # strict: "5", 5.0 or true are refused

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.snapshot_id is None) == (self.record_cutoff_seq is None):
            raise ValueError("A comparison side is exactly one of snapshot_id or record_cutoff_seq")
        return self

    def spec(self) -> dict:
        """The side as engines/assurance_comparison.compare takes it."""
        return {"snapshot_id": self.snapshot_id} if self.snapshot_id is not None else {"record_seq": self.record_cutoff_seq}


class ComparisonIn(BaseModel):
    """The same valid period on both sides (TIM §12), as the obligation's other assurance requests take it (PeriodIn)."""
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    period_from: datetime
    period_to: datetime
    left: ComparisonSideIn
    right: ComparisonSideIn


@router.post("/obligations/{lineage}/historical-comparisons")
def historical_comparison(lineage: str, body: ComparisonIn,
                          ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OBLIGATION))):
    """Compare this obligation's assurance over one valid period between two knowledge states (ZA-TIM-001 §16 "POST
    /historical-comparisons: Compares same valid period across two record cutoffs/snapshots"; WF-STA-01 History
    "compare-current-reconstruction action"; WF-STA-05 "Audit compare: Compare AS_KNOWN_THEN vs RECONSTRUCTED_NOW for
    same period"; WF-TIM-02). Each side is a frozen snapshot, read as stored, or a reconstruction at an explicit record
    cutoff; the answer is engines/assurance_comparison.compare's, unchanged: each side's known_at and perspective, the
    outcome difference (state, reason codes, roll-up version, aligned segment timeline) and the exact dependency
    references added or removed with the cutoff that first recorded each, plus what changed between the cutoffs for
    records both sides share. Reconstructions decide occurrence due times at one instant, the request instant, which is
    echoed as evaluation_instant.

    The ZA-TIM-001 §12 difference classes are not assigned: ``differences`` is null with ``differences_note`` (no
    controlled document maps assurance dependency changes onto them; owners ZA-TIM-001 Data Architect and Head of
    Assurance). Read-only, like POST /obligations/{lineage}/preview and under its authorization: nothing is stored, no
    snapshot is persisted, no issue is raised, and the transaction is rolled back. Refusals are the engine's: 422 for an
    empty period, a snapshot of another obligation or period, a record cutoff beyond the watermark
    (RECORD_SEQ_IN_FUTURE); 404 for an obligation or snapshot that is not this organisation's."""
    from ..engines import assurance_comparison
    try:
        return assurance_comparison.compare(
            ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=lineage, period_from=body.period_from,
            period_to=body.period_to, left=body.left.spec(), right=body.right.spec(), actor_id=ctx.actor.actor_id)
    finally:
        ctx.db.rollback()  # read-only: nothing the reconstruction derived is kept


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SNAPSHOT))):
    return to_dict(ctx.scoped(ctx.db.get(AssuranceSnapshot, snapshot_id), "snapshot"))


@router.get("/snapshots/{snapshot_id}/reproduce")
def reproduce(snapshot_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SNAPSHOT))):
    """Rebuild from the pinned record cutoff and compare (TIM-REQ-015/016). The rebuild reads exception and occurrence
    state as recorded by that cutoff and decides due times at the snapshot's own evaluation instant (its stored
    created_at), so later events and the passage of time do not change the answer."""
    s = ctx.scoped(ctx.db.get(AssuranceSnapshot, snapshot_id), "snapshot")
    r = asr.compute_snapshot(ctx.db, tenant_id=ctx.tenant, obligation_lineage_id=s.obligation_lineage_id,
                             period_from=s.period_from, period_to=s.period_to, record_cutoff_seq=s.record_cutoff_seq,
                             perspective=s.perspective, actor_id=ctx.actor.actor_id, persist=False,
                             evaluated_at=s.created_at)
    ctx.db.rollback()
    ok_digest = r["dependency_digest"] == s.dependency_digest
    # The snapshot pins the roll-up engine version it was computed with (ZA-TIM-001 §10 "evaluator / rollup versions";
    # STA-REQ-032: engine/version changes need ADR, golden-vector regression and explicit snapshot versioning). This
    # runtime holds one engine; when it is not the recorded one, the rebuild is another engine's answer, so it is
    # reported as ENGINE_CHANGED and never as a reproduction (as applicability replay reports BUILD_CHANGED).
    from .. import codes
    engine_matches = s.rollup_engine_version == codes.ROLLUP_ENGINE_VERSION
    same = ok_digest and r["state"] == s.state
    return {"snapshot_id": snapshot_id, "original_state": s.state, "reproduced_state": r["state"],
            "dependency_digest_match": ok_digest, "reproducible": same and engine_matches,
            "status": "ENGINE_CHANGED" if not engine_matches else "REPRODUCED" if same else "NOT_REPRODUCED",
            "rollup_engine_version": {"recorded": s.rollup_engine_version, "running": codes.ROLLUP_ENGINE_VERSION},
            "record_cutoff_seq": s.record_cutoff_seq, "evaluation_instant": r["evaluation_instant"],
            "history_problems": r["history_problems"]}


# ------------------------------------------------------------------ occurrences
class OccIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    window_from: datetime
    due_at: datetime


@router.post("/obligations/{lineage}/occurrences")
def add_occurrence(lineage: str, body: OccIn, ctx: Ctx = Depends(authorize("manage_controls", "manage_facts"))):
    _current_obl(ctx.db, ctx.tenant, lineage)
    o, _ = governance.schedule_occurrence(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                          obligation_lineage_id=lineage, window_from=body.window_from,
                                          due_at=body.due_at)
    ctx.db.commit()
    return to_dict(o)


class FulfilIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    fulfilled_at: datetime
    evidence_ids: list[str]


@router.post("/occurrences/{occurrence_id}/fulfil")
def fulfil(occurrence_id: str, body: FulfilIn, ctx: Ctx = Depends(authorize("manage_controls", "manage_facts"))):
    """Records fulfilment as a new occurrence fact; a missed occurrence is never overwritten by later success."""
    ctx.scoped(ctx.db.get(ObligationOccurrence, occurrence_id), "occurrence")
    o, _ = governance.fulfil_occurrence(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                        occurrence_id=occurrence_id, fulfilled_at=body.fulfilled_at,
                                        evidence_ids=body.evidence_ids)
    ctx.db.commit()
    return to_dict(o)


@router.get("/occurrences/{occurrence_id}/events")
def occurrence_events(occurrence_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=OCCURRENCE))):
    ctx.scoped(ctx.db.get(ObligationOccurrence, occurrence_id), "occurrence")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(ObligationOccurrenceEvent).where(
            ObligationOccurrenceEvent.tenant_id == ctx.tenant,
            ObligationOccurrenceEvent.occurrence_id == occurrence_id,
        ).order_by(ObligationOccurrenceEvent.record_seq.asc(), ObligationOccurrenceEvent.event_id)
    ).scalars()]


# ------------------------------------------------------------------ controls
@router.get("/controls")
def controls(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    out = []
    now = datetime.now(timezone.utc)
    cis = ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant)
                         .order_by(ControlImplementation.record_seq,
                                   ControlImplementation.control_implementation_id)).scalars().all()
    # The definitions and subjects the rows refer to, read once and held here so db.get() below finds them in the
    # session instead of querying for every control.
    refs = [*ctx.db.execute(select(ControlDefinition).where(
                ControlDefinition.control_definition_id.in_({ci.control_definition_id for ci in cis}))).scalars(),
            *ctx.db.execute(select(Subject).where(Subject.subject_id.in_({ci.subject_id for ci in cis}))).scalars()]
    for ci in cis:
        d = to_dict(ci)
        cd = ctx.db.get(ControlDefinition, ci.control_definition_id)
        d["definition"] = to_dict(cd)
        d["subject"] = ctx.db.get(Subject, ci.subject_id).display_name
        try:
            ce = asr.evaluate_control(ctx.db, ci, now - timedelta(days=1), now, 10**15, persist=False)
            d["current_state"] = {"state": ce["state"], "reason_codes": ce["reason_codes"], "window": "last 24h",
                                  "note": "Convenience view; authoritative state is the immutable ControlEvaluation."}
        except asr.AssuranceConfigurationError as e:  # shown as an error, never as a control state (STA §16)
            d["current_state"] = {"state": None, "configuration_error": e.detail, "window": "last 24h"}
        out.append(d)
    del refs
    return out


@router.get("/controls/{ci_id}")
def control_detail(ci_id: str, days: int = 30, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=CONTROL))):
    """WF-STA-02 Control Evaluation."""
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    now = datetime.now(timezone.utc)
    try:  # a day count beyond the calendar is invalid input, never an unhandled OverflowError (HTTP 500)
        window_from = now - timedelta(days=days)
    except OverflowError:
        raise HTTPException(422, "days is out of range for a control window") from None
    try:  # days <= 0 is an empty or inverted window: the engine refuses it (422) before any state is derived
        ce = asr.evaluate_control(ctx.db, ci, window_from, now, 10**15, persist=False)
    except asr.AssuranceConfigurationError as e:  # an error object, never a control state (STA §16)
        ce = {"state": None, "configuration_error": e.detail}
    # Every read is scoped to the caller's organisation in the query itself, not only by row-level security (which the
    # SQLite runtime does not have): a row of another organisation that names this control, policy or test definition
    # is never listed as part of it.
    pols = ctx.db.execute(select(ControlTestPolicy).where(ControlTestPolicy.tenant_id == ctx.tenant,
                                                          ControlTestPolicy.control_implementation_id == ci_id)
                          .order_by(ControlTestPolicy.version, ControlTestPolicy.policy_version_id)).scalars().all()
    tests = []
    for p in pols:
        for td in ctx.db.execute(select(TestDefinition).where(TestDefinition.tenant_id == ctx.tenant,
                                                              TestDefinition.policy_version_id == p.policy_version_id)).scalars():
            t = to_dict(td)
            t["runs"] = [to_dict(r) for r in ctx.db.execute(select(TestRun).where(TestRun.tenant_id == ctx.tenant,
                                                                                  TestRun.test_definition_id == td.test_definition_id)
                                                            .order_by(TestRun.occurred_at.desc(), TestRun.test_run_id)).scalars()]
            tests.append(t)
    return {"implementation": to_dict(ci), "definition": to_dict(ctx.db.get(ControlDefinition, ci.control_definition_id)),
            "subject": to_dict(ctx.db.get(Subject, ci.subject_id)), "policies": [to_dict(p) for p in pols], "tests": tests,
            "evaluation_preview": ce, "window_days": days,
            "evaluations": [to_dict(e) for e in ctx.db.execute(select(ControlEvaluation).where(
                ControlEvaluation.tenant_id == ctx.tenant, ControlEvaluation.control_implementation_id == ci_id)
                .order_by(ControlEvaluation.created_at.desc(), ControlEvaluation.evaluation_id.desc()).limit(20)).scalars()]}


@router.get("/controls/{ci_id}/state")
def control_state(ci_id: str, window_from: Instant | None = None, window_to: Instant | None = None,
                  as_of_record_seq: int | None = Query(default=None, ge=0),
                  ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=CONTROL))):
    """WF-STA-02 Control Evaluation for an explicit valid window, current or as known at a past record sequence
    (ZA-STA-001 §18 "GET /controls/{id}/state: Current/historical control state. Never accepts manual state mutation";
    FR-CTL-005). Read-only: derived exactly as POST /controls/{id}/evaluate derives it, with nothing stored and no issue
    raised. The window is required (ZA-TIM-001 §16: no ambiguous historical query); without ``as_of_record_seq`` the
    current watermark is used. Either way the resolved watermark and perspective are echoed (TIM-REQ-017,
    STA-REQ-030), with the stored evaluation that has exactly these inputs and this result (STA-REQ-028)."""
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    # Checked here, after the control is resolved: another organisation's control answers 404 whatever is sent.
    missing = [n for n, v in (("window_from", window_from), ("window_to", window_to)) if v is None]
    if missing:
        raise HTTPException(422, f"Required: {', '.join(missing)} (a control state is for an explicit valid window; "
                                 "ZA-TIM-001 §16)")
    a, b = ensure_utc(window_from), ensure_utc(window_to)
    if b <= a:
        raise HTTPException(422, "The control window must be a non-empty half-open interval")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if as_of_record_seq is not None and as_of_record_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"as_of_record_seq is beyond the current record sequence {now_seq}"})
    cutoff = as_of_record_seq if as_of_record_seq is not None else now_seq
    if ci.record_seq > cutoff:
        raise HTTPException(404, "control not found as of requested record sequence")
    from .. import codes
    out = {"control_implementation_id": ci.control_implementation_id, "window": [a, b],
           **asr.control_state_at(ctx.db, ci, a, b, cutoff), "evaluator_version": codes.CONTROL_EVALUATOR_VERSION,
           "record_watermark": {"cell_id": CELL_ID, "record_seq": cutoff,
                                "perspective": "AS_OF_RECORD_SEQ" if as_of_record_seq is not None else "CURRENT"}}
    ctx.db.rollback()  # read-only: nothing this request derived is kept
    return out


@router.get("/control-evaluations/{control_evaluation_id}")
def control_evaluation(control_evaluation_id: str,
                       ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=CONTROL_EVALUATION))):
    """One stored, immutable ControlEvaluation with what it was derived under (ZA-STA-001 §18 "GET
    /control-evaluations/{id}: Retrieve immutable evaluation and authorized explanation. Includes exact
    tests/evidence/policy version"; STA-REQ-019/020): its state, reason codes, precedence rule, window, record cutoff,
    evaluator version and exact test-run/evidence inputs as recorded, the control implementation, definition and
    subject, and the exact test policy version with its test definitions. What a snapshot leaf's control_evaluation_id
    opens (WF-STA-03 evidence trace). Nothing is recomputed."""
    e = ctx.scoped(ctx.db.get(ControlEvaluation, control_evaluation_id), "control evaluation")
    ci = ctx.db.get(ControlImplementation, e.control_implementation_id)
    ci = ci if ci is not None and ci.tenant_id == ctx.tenant else None
    cd = ctx.db.get(ControlDefinition, ci.control_definition_id) if ci is not None else None
    subject = ctx.db.get(Subject, ci.subject_id) if ci is not None else None
    subject = subject if subject is not None and subject.tenant_id == ctx.tenant else None
    policy = ctx.db.get(ControlTestPolicy, e.policy_version_id)
    policy = policy if policy is not None and policy.tenant_id == ctx.tenant else None
    tds = ctx.db.execute(select(TestDefinition).where(TestDefinition.tenant_id == ctx.tenant,
                                                      TestDefinition.policy_version_id == e.policy_version_id)
                         .order_by(TestDefinition.test_definition_id)).scalars().all() if policy is not None else []
    return {"evaluation": to_dict(e),
            "implementation": to_dict(ci) if ci is not None else _missing(
                "control_implementation_id", e.control_implementation_id, "control_definition_id", "subject_id"),
            "definition": to_dict(cd) if cd is not None else None,
            "subject": to_dict(subject) if subject is not None else None,
            "policy": to_dict(policy) if policy is not None else _missing(
                "policy_version_id", e.policy_version_id, "version", "aggregation_mode"),
            "test_definitions": [to_dict(td) for td in tds]}


class WindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    window_from: datetime
    window_to: datetime


@router.post("/controls/{ci_id}/evaluate")
def evaluate_control(ci_id: str, body: WindowIn,
                     ctx: Ctx = Depends(authorize("manage_controls", "request_evaluation", "package"))):
    ci = ctx.scoped(ctx.db.get(ControlImplementation, ci_id), "control")
    from ..platform import current_watermark
    # control.evaluated is written by the engine with the evaluation it stores (ZA-EVT-001 §17), and only then: a window
    # with no state derived stores no evaluation and publishes nothing (it used to name this implementation as a
    # ControlEvaluation aggregate). An empty or inverted window is refused (422) before anything is derived.
    r = asr.evaluate_control(ctx.db, ci, body.window_from, body.window_to, current_watermark(ctx.db)["record_seq"],
                             emit_event=True)
    ctx.db.commit()
    return r


class TestDefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    # API-REQ-033: the column limits of assurance_test_definition_version (name varchar(200), source_class varchar(48),
    # cadence_days a 32-bit integer), so an out-of-range value is a 422 before anything is written.
    name: str = Field(max_length=200)
    source_class: str = Field(default="CONNECTOR", max_length=48)
    cadence_days: int = Field(default=30, ge=-(2 ** 31), le=2 ** 31 - 1)
    required: bool = True
    primary: bool = False
    degrades_on_fail: bool = False
    ipe_required: bool = False
    criterion: dict | None = None

    @model_validator(mode="after")
    def _criterion(self):
        # The grammar engines/ipe.evaluate_criterion implements; it refuses the same criteria if stored without this check.
        from ..engines.ipe import criterion_problems
        problems = criterion_problems(self.criterion) if self.criterion is not None else []
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ControlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    control_key: str
    subject_id: str
    method: str
    valid_from: datetime
    aggregation_mode: str = "ALL_REQUIRED"
    k_threshold: StrictInt | None = None  # strict: "3", 3.0 or true are refused, never coerced
    automation_requirement: str = "PREFERRED"
    tests: list[TestDefIn]

    @model_validator(mode="after")
    def _policy(self):
        # ZA-STA-001 §03 vocabularies and the K/primary invariants; the evaluator enforces the same rules on stored data.
        problems = asr.policy_problems(self.aggregation_mode, self.k_threshold, self.automation_requirement,
                                       [(t.required, t.primary) for t in self.tests])
        if problems:
            raise ValueError("; ".join(problems))
        return self


@router.post("/controls")
def create_control(body: ControlIn, ctx: Ctx = Depends(authorize("manage_controls"))):
    from sqlalchemy.exc import IntegrityError
    cd = ctx.db.execute(select(ControlDefinition).where(ControlDefinition.key == body.control_key)).scalars().first()
    if not cd:
        raise HTTPException(404, "control definition")
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    dupe_msg = "Implementation already exists for this control/scope; frameworks are mappings, not clones (INV-08)"
    dupe = ctx.db.execute(select(ControlImplementation).where(ControlImplementation.tenant_id == ctx.tenant,
                                                              ControlImplementation.control_definition_id == cd.control_definition_id,
                                                              ControlImplementation.subject_id == s.subject_id)).scalars().first()
    if dupe:
        raise HTTPException(409, dupe_msg)
    seq = next_record_seq(ctx.db)
    ci = ControlImplementation(tenant_id=ctx.tenant, entity_id=s.entity_id, subject_id=s.subject_id,
                               control_definition_id=cd.control_definition_id, owner_actor_id=ctx.actor.actor_id,
                               method=body.method, valid_from=body.valid_from, record_seq=seq)
    ctx.db.add(ci)
    try:
        ctx.db.flush()
    except IntegrityError:  # a concurrent creation took the (tenant, definition, subject, variant) key (INV-08)
        ctx.db.rollback()
        raise HTTPException(409, dupe_msg) from None
    pol = ControlTestPolicy(tenant_id=ctx.tenant, control_implementation_id=ci.control_implementation_id,
                            aggregation_mode=body.aggregation_mode, k_threshold=body.k_threshold,
                            automation_requirement=body.automation_requirement, record_seq=seq)
    ctx.db.add(pol)
    ctx.db.flush()
    for t in body.tests:
        ctx.db.add(TestDefinition(tenant_id=ctx.tenant, policy_version_id=pol.policy_version_id, **t.model_dump()))
    audit(ctx.db, action="control_implementation.create", object_type="ControlImplementation",
          object_id=ci.control_implementation_id, actor=ctx.actor, tenant_id=ctx.tenant, record_seq=seq, reason=(f"{cd.key} for subject {s.subject_id}; policy {pol.policy_version_id} "
                                  f"{body.aggregation_mode} k={body.k_threshold} automation={body.automation_requirement}; "
                                  f"{len(body.tests)} test(s)"))
    ctx.db.commit()
    return to_dict(ci)


class TestRunIn(BaseModel):
    """One test result (ZA-STA-001 §01/§04; DOM §7.1 "source provenance and evaluated period required"). Field limits
    are the column sizes, so an over-long value is a 422, never a database error."""
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    test_definition_id: str = Field(min_length=1, max_length=36)
    occurred_at: datetime
    window_from: datetime
    window_to: datetime
    result: Literal["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"]  # GLS §06 / STA §01: these "only"
    source_system: str = Field(min_length=1, max_length=80)
    external_status: str | None = Field(default=None, max_length=64)  # vendor status kept as input only
    evidence_ids: list[str] = Field(default_factory=list, max_length=500)
    source_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    detail: str | None = None


@router.post("/test-runs")
def ingest_test_run(body: TestRunIn, ctx: Ctx = Depends(authorize("submit_evidence")),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """External GRC/vendor and manual results are inputs with provenance, never direct control state (STA-REQ-008).
    A TestRun is immutable: a correction or retest is a new run. Rules: app/engines/test_results.py. With an
    ``Idempotency-Key`` (API-001 §08) a retry returns the original run; a changed request is 409 idempotency_conflict."""
    if not body.source_system.strip():
        raise HTTPException(422, {"error": "SOURCE_PROVENANCE_REQUIRED", "message": "source_system is required"})
    run, _ = test_results.record_test_run(ctx.db, tenant_id=ctx.tenant, actor=ctx.actor, body=body.model_dump(),
                                          idempotency_key=idempotency_key)
    ctx.db.commit()
    return to_dict(run)


def _test_run_chain(db, run: TestRun) -> tuple:
    """The test definition, policy version and control implementation a run was recorded against, each only if it is
    one of the run's organisation's rows (None from the first link that cannot be followed)."""
    td = db.get(TestDefinition, run.test_definition_id)
    td = td if td is not None and td.tenant_id == run.tenant_id else None
    pol = db.get(ControlTestPolicy, td.policy_version_id) if td is not None else None
    pol = pol if pol is not None and pol.tenant_id == run.tenant_id else None
    ci = db.get(ControlImplementation, pol.control_implementation_id) if pol is not None else None
    ci = ci if ci is not None and ci.tenant_id == run.tenant_id else None
    return td, pol, ci


def _test_run_entity(db, run: TestRun) -> str | None:
    # A test run belongs to the legal entity of the control implementation it tests; a chain that cannot be followed
    # resolves to no entity, so only organisation-wide roles reach that run (fail closed).
    ci = _test_run_chain(db, run)[2]
    return ci.entity_id if ci is not None else None


TEST_RUN = entity_of(TestRun, "test_run_id", "test run", entity=_test_run_entity)


@router.get("/test-runs/{test_run_id}")
def get_test_run(test_run_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=TEST_RUN))):
    """One stored TestRun, exactly the record named (WF-STA-03 Evidence trace: "Leaf opens exact ControlEvaluation
    TestRun Evidence references, without exposing unauthorized payload"; the run IDs are those a stored
    ControlEvaluation's inputs and a snapshot's dependency manifest name). A TestRun is "One execution/imported result.
    Immutable; source provenance and evaluated period required" (DOM §7.1): it is read by its ID and never replaced by
    a later run, a retest or a correction of the same test, each of which is its own record. Nothing is recomputed.

    Returned as stored, with the test definition (required/optional, primary, source class, IPE requirement), its
    policy version and the control implementation, definition and subject it was recorded against: the fields
    GET /controls/{id} and GET /control-evaluations/{id} already show the same readers. Evidence and the source run
    are referenced by ID only (``evidence_ids``, ``source_run_id``), never their payload; they open through their own
    reads and authorization (evidence, IPE source runs). A related record that cannot be found is reported as missing,
    never substituted. Read like the control it tests (DOSSIER_READ, entity-scoped); another organisation's run is
    404."""
    db = ctx.db
    run = ctx.scoped(db.get(TestRun, test_run_id), "test run")
    td, pol, ci = _test_run_chain(db, run)
    cd = db.get(ControlDefinition, ci.control_definition_id) if ci is not None else None
    subject = db.get(Subject, ci.subject_id) if ci is not None else None
    subject = subject if subject is not None and subject.tenant_id == ctx.tenant else None
    return {"test_run": to_dict(run),
            "test_definition": to_dict(td) if td is not None else _missing(
                "test_definition_id", run.test_definition_id, "name", "required", "policy_version_id"),
            "policy": to_dict(pol) if pol is not None else (_missing(
                "policy_version_id", td.policy_version_id, "version", "aggregation_mode") if td is not None else None),
            "implementation": to_dict(ci) if ci is not None else (_missing(
                "control_implementation_id", pol.control_implementation_id, "control_definition_id", "subject_id")
                if pol is not None else None),
            "definition": to_dict(cd) if cd is not None else None,
            "subject": to_dict(subject) if subject is not None else None}


# ------------------------------------------------------------------ exceptions (separate governance treatment)
class ExceptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    target_type: str
    target_id: str
    reason: str
    valid_from: datetime
    valid_to: datetime


@router.get("/exceptions")
def list_exceptions(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(x) for x in ctx.db.execute(select(ExceptionRecord).where(ExceptionRecord.tenant_id == ctx.tenant)
                                               .order_by(ExceptionRecord.valid_from.desc(),
                                                         ExceptionRecord.exception_id)).scalars()]


@router.post("/exceptions")
def create_exception(body: ExceptionIn, ctx: Ctx = Depends(authorize("request_exception"))):
    if ensure_utc(body.valid_to) <= ensure_utc(body.valid_from):
        raise HTTPException(422, "Exception must be time-bounded")
    # The target must be one of this organisation's obligations or control implementations.
    if body.target_type == "OBLIGATION":
        _current_obl(ctx.db, ctx.tenant, body.target_id)
    elif body.target_type == "CONTROL":
        ctx.scoped(ctx.db.get(ControlImplementation, body.target_id), "control")
    else:
        raise HTTPException(422, "target_type must be OBLIGATION or CONTROL")
    x, _ = governance.request_exception(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                        target_type=body.target_type, target_id=body.target_id,
                                        reason=body.reason, valid_from=body.valid_from, valid_to=body.valid_to)
    ctx.db.commit()
    return to_dict(x)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    decision: str  # APPROVE | REJECT | REVOKE
    reason: str | None = None


@router.post("/exceptions/{exception_id}/decide")
def decide_exception(exception_id: str, body: DecisionIn, ctx: Ctx = Depends(authorize("approve_exception", human=True))):
    ctx.scoped(ctx.db.get(ExceptionRecord, exception_id), "exception")
    x, _ = governance.decide_exception(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                       exception_id=exception_id, decision=body.decision, reason=body.reason)
    ctx.db.commit()
    return to_dict(x)


@router.get("/exceptions/{exception_id}/events")
def exception_events(exception_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EXCEPTION))):
    ctx.scoped(ctx.db.get(ExceptionRecord, exception_id), "exception")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(ExceptionEvent).where(
            ExceptionEvent.tenant_id == ctx.tenant,
            ExceptionEvent.exception_id == exception_id,
        ).order_by(ExceptionEvent.record_seq.asc(), ExceptionEvent.event_id)
    ).scalars()]


# ------------------------------------------------------------------ compensation (explicit expression branch)
class CompIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    obligation_lineage_id: str
    primary_control_key: str
    # ZA-STA-001 §09: "One or more controls". Existence, duplicates and scope are checked by the domain layer
    # (governance.compensation_problems), which also re-checks at approval.
    compensating_control_keys: list[str] = Field(min_length=1)
    equivalence_rationale: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/compensations")
def propose_compensation(body: CompIn, ctx: Ctx = Depends(authorize("request_exception"))):
    _current_obl(ctx.db, ctx.tenant, body.obligation_lineage_id)
    if len(body.equivalence_rationale.strip()) < 20:
        raise HTTPException(422, "Equivalence rationale required")
    kids = [{"node_type": "CONTROL", "node_id": f"comp_{k}", "control_key": k} for k in body.compensating_control_keys]
    expr = kids[0] if len(kids) == 1 else {"node_type": "ALL", "node_id": "comp_all", "children": kids}
    c, _ = governance.propose_compensation(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id,
                                           obligation_lineage_id=body.obligation_lineage_id,
                                           primary_control_key=body.primary_control_key, compensating_expression=expr,
                                           equivalence_rationale=body.equivalence_rationale, valid_from=body.valid_from,
                                           valid_to=body.valid_to)
    ctx.db.commit()
    return to_dict(c)


@router.get("/compensations")
def list_compensations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    return [to_dict(c) for c in ctx.db.execute(select(Compensation).where(Compensation.tenant_id == ctx.tenant)
                                               .order_by(Compensation.valid_from.desc(),
                                                         Compensation.compensation_id)).scalars()]


@router.post("/compensations/{comp_id}/decide")
def decide_compensation(comp_id: str, body: DecisionIn,
                        ctx: Ctx = Depends(authorize("approve_compensation", human=True))):
    """Approval creates a NEW SatisfactionExpressionVersion (versioned, never a manual green flag)."""
    ctx.scoped(ctx.db.get(Compensation, comp_id), "compensation")
    c, _ = governance.decide_compensation(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                          comp_id=comp_id, decision=body.decision, reason=body.reason)
    ctx.db.commit()
    return to_dict(c)


@router.get("/compensations/{comp_id}/events")
def compensation_events(comp_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=COMPENSATION))):
    ctx.scoped(ctx.db.get(Compensation, comp_id), "compensation")
    return [governance.event_view(e) for e in ctx.db.execute(
        select(CompensationEvent).where(
            CompensationEvent.tenant_id == ctx.tenant,
            CompensationEvent.compensation_id == comp_id,
        ).order_by(CompensationEvent.record_seq.asc(), CompensationEvent.event_id)
    ).scalars()]


# ------------------------------------------------------------------ issues
@router.get("/issues")
def issues(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    # Newest first, as before; issues created at the same instant are ordered by their unique issue_id, never by
    # PostgreSQL's physical row order, which an UPDATE (a transition, an assignment) changes.
    return [to_dict(i) for i in ctx.db.execute(select(Issue).where(Issue.tenant_id == ctx.tenant)
                                               .order_by(Issue.created_at.desc(), Issue.issue_id)).scalars()]


class IssueRaiseIn(BaseModel):
    """WF-03 "request remediation" / WF-05 "create issue": the persisted assurance result the issue is raised from, the
    obligation revision the caller reviewed and why. Issue type, severity, priority, owner, due date and state are not
    accepted (unknown fields are refused, API-REQ-014): they follow the result, as for issues snapshots raise."""
    model_config = ConfigDict(extra="forbid")
    assurance_snapshot_id: str = Field(min_length=1, max_length=36)
    obligation_revision_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/obligations/{lineage}/issues", status_code=201)
def raise_obligation_issue(lineage: str, body: IssueRaiseIn, response: Response,
                           ctx: Ctx = Depends(authorize("manage_issues")),
                           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Raise the obligation-level issue for an assurance result (ZA-PRD-001 §13 WF-03 / WF-05; ZA-STA-001 §17). 201 with
    the new issue, or 200 with the open issue that already covers the condition (linked, nothing duplicated). Rules:
    engines/governance.py raise_obligation_issue. Never changes assurance, control or obligation state (STA-REQ-023)."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, {"error": "REASON_REQUIRED", "message": "A reason is required."})
    i, created = governance.raise_obligation_issue(
        ctx.db, tenant_id=ctx.tenant, actor=ctx.actor, obligation_lineage_id=lineage,
        obligation_revision_id=body.obligation_revision_id, snapshot_id=body.assurance_snapshot_id, reason=reason,
        idempotency_key=idempotency_key, actor_ref=ctx.actor_ref())
    ctx.db.commit()
    if not created:
        response.status_code = 200
    return to_dict(i)


ISSUE_FLOW = {"OPEN": {"TRIAGED", "CANCELLED"}, "TRIAGED": {"IN_REMEDIATION", "CANCELLED"},
              "IN_REMEDIATION": {"PENDING_VERIFICATION"}, "PENDING_VERIFICATION": {"CLOSED", "IN_REMEDIATION"}}


class IssueMove(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    state: str
    reason: str | None = None


@router.post("/issues/{issue_id}/transition")
def move_issue(issue_id: str, body: IssueMove, ctx: Ctx = Depends(authorize("manage_issues"))):
    """Closure never changes assurance state; a new evaluation must prove remediation (STA-REQ-023)."""
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    i, _ = governance.transition_issue(ctx.db, tenant_id=ctx.tenant, actor_id=ctx.actor.actor_id, actor=ctx.actor,
                                       issue_id=issue_id, to_state=body.state, reason=body.reason)
    ctx.db.commit()
    return to_dict(i)


class IssueAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields (e.g. due_at, state) are refused
    owner_actor_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/issues/{issue_id}/assign")
def assign_issue(issue_id: str, body: IssueAssignIn, ctx: Ctx = Depends(authorize("manage_issues"))):
    """Record the accountable owner (STA §17; GLS TRIAGED needs ownership established). An ASSIGNED event, never a
    state change; no due date or escalation policy is invented (ZA-WRK-001 §06 needs a versioned deadline policy)."""
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    if not body.reason.strip():
        raise HTTPException(422, {"error": "REASON_REQUIRED", "message": "A reason is required."})
    i, _ = governance.assign_issue(ctx.db, tenant_id=ctx.tenant, actor=ctx.actor, issue_id=issue_id,
                                   owner_actor_id=body.owner_actor_id, reason=body.reason.strip())
    ctx.db.commit()
    return to_dict(i)


class IssueCloseIn(BaseModel):
    """The new result that verifies remediation (STA §17): an AssuranceSnapshot for obligation-level issues, or a
    ControlEvaluation for control-level issues. Exactly one."""
    model_config = ConfigDict(extra="forbid")  # API-REQ-014
    verification_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    verification_control_evaluation_id: str | None = Field(default=None, min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _one_reference(self):
        if (self.verification_snapshot_id is None) == (self.verification_control_evaluation_id is None):
            raise ValueError("give exactly one of verification_snapshot_id or verification_control_evaluation_id")
        return self


@router.post("/issues/{issue_id}/close")
def close_issue(issue_id: str, body: IssueCloseIn, ctx: Ctx = Depends(authorize("manage_issues"))):
    """Close a remediated issue against the new AssuranceSnapshot that verifies it (STA §17 closure rule; STA-REQ-023).
    Closing changes the issue only; the snapshot already holds the authoritative updated state."""
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    if not body.reason.strip():
        raise HTTPException(422, {"error": "REASON_REQUIRED", "message": "A reason is required."})
    i, _ = governance.close_issue(ctx.db, tenant_id=ctx.tenant, actor=ctx.actor, issue_id=issue_id,
                                  verification_snapshot_id=body.verification_snapshot_id,
                                  verification_control_evaluation_id=body.verification_control_evaluation_id,
                                  reason=body.reason.strip())
    ctx.db.commit()
    return to_dict(i)


@router.get("/issues/{issue_id}/events")
def issue_events(issue_id: str, as_of_record_seq: int | None = Query(default=None, ge=0),
                 ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    """The issue's append-only event history. With ``as_of_record_seq`` only the events recorded by that cutoff, so
    remediation, verification and closure recorded later are not shown (TIM-REQ-006); a cutoff beyond the current
    record sequence is refused rather than answered from newer knowledge."""
    ctx.scoped(ctx.db.get(Issue, issue_id), "issue")
    q = select(IssueEvent).where(IssueEvent.tenant_id == ctx.tenant, IssueEvent.issue_id == issue_id)
    if as_of_record_seq is not None:
        now_seq = current_watermark(ctx.db)["record_seq"]
        if as_of_record_seq > now_seq:
            raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                      "message": f"as_of_record_seq is beyond the current record sequence {now_seq}"})
        q = q.where(IssueEvent.record_seq <= as_of_record_seq)
    return [governance.event_view(e) for e in ctx.db.execute(
        q.order_by(IssueEvent.record_seq.asc(), IssueEvent.event_id)).scalars()]


class RiskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    scope_ref: str = Field(max_length=200)  # API-REQ-033: governance_risk_acceptance_revision.scope_ref varchar(200)
    residual_risk_statement: str
    valid_from: datetime
    valid_to: datetime | None = None


@router.post("/risk-acceptances")
def accept_risk(body: RiskIn, ctx: Ctx = Depends(authorize("approve_exception", human=True))):
    if body.valid_to is not None and ensure_utc(body.valid_to) <= ensure_utc(body.valid_from):
        raise HTTPException(422, {"error": "RISK_ACCEPTANCE_PERIOD_EMPTY", "message": "valid_to must be later than valid_from"})
    r = RiskAcceptance(tenant_id=ctx.tenant, approver_id=ctx.actor.actor_id, **body.model_dump())
    ctx.db.add(r)
    ctx.db.flush()
    # STA-REQ-024: recorded separately from assurance state, but recorded (audit finding F-13).
    audit(ctx.db, action="risk_acceptance.create", object_type="RiskAcceptance",
          object_id=r.risk_acceptance_id, actor=ctx.actor,
          tenant_id=ctx.tenant, record_seq=next_record_seq(ctx.db), reason=f"scope {body.scope_ref}")
    ctx.db.commit()
    return to_dict(r)


@router.get("/risk-acceptances")
def risks(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    # The same order as the sibling governance lists (/exceptions, /compensations): newest valid_from first, then ID.
    return [to_dict(r) for r in ctx.db.execute(select(RiskAcceptance).where(RiskAcceptance.tenant_id == ctx.tenant)
                                               .order_by(RiskAcceptance.valid_from.desc(),
                                                         RiskAcceptance.risk_acceptance_id)).scalars()]
