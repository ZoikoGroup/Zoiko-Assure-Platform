"""Facts, applicability evaluations, determinations, challenges and fact requests (ZA-APP-001)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select

from .. import pagination
from ..db import CELL_ID
from ..engines import applicability as appl
from ..engines import catalog, idempotency, rules
from ..engines.facts import (assert_fact, assertion_view, display_value, input_bundle_view,
                             overlapping_current_revisions, resolve_fact)
from ..models import (AdoptionAttestation, ApplicabilityDetermination, ApplicabilityEvaluation, DeterminationChallenge,
                      FactAssertion, FactConflict, FactDefinition, FactRequest, ObligationDefinition, RuleVersion, Subject)
from ..platform import audit, current_watermark, emit, ensure_utc, next_record_seq, to_dict
from ..security import DOSSIER_READ, READ, Ctx, authorize, entity_of

router = APIRouter()
# Stage 4: how subjects and evaluations resolve to their legal entity, so entity-scoped roles reach their entity only.
SUBJECT = entity_of(Subject, "subject_id", "subject")
EVALUATION = entity_of(ApplicabilityEvaluation, "evaluation_id", "evaluation")


def _utc_representable(v: datetime | None) -> datetime | None:
    """An instant whose UTC conversion leaves the calendar (year 1 east of UTC, year 9999 west of it) has no UTC instant
    to store, freeze or read a legal date from. It used to be accepted here and then fail in that conversion with an
    unhandled OverflowError (a 500); it is a 422 now. Every other value is accepted and read exactly as before."""
    if v is not None:
        try:
            ensure_utc(v)
        except OverflowError:
            raise ValueError("this instant has no UTC equivalent within years 1-9999") from None
    return v


# The instants this router takes (effective_at, valid_at, fact validity): the same parsing as a plain datetime.
Instant = Annotated[datetime, AfterValidator(_utc_representable)]
# Record-sequence coordinates are compared with BIGINT record_seq columns. Below that range a value could not be bound to
# the query (PostgreSQL: "bigint out of range", SQLite: OverflowError; a 500 either way), so it is a 422; every value in
# the range is answered as before, and one beyond the watermark is still RECORD_SEQ_IN_FUTURE.
RECORD_SEQ_MIN = -(2 ** 63)


def _fact_row(db, a: FactAssertion, fd: FactDefinition) -> dict:
    d = assertion_view(a, fd)  # every typed value column redacted with `value`, not only `value` (facts.assertion_view)
    d["fact_key"] = fd.key
    d["fact_label"] = fd.label
    return d


@router.get("/subjects/{subject_id}/facts")
def fact_profile(subject_id: str, valid_at: Instant | None = None,
                 known_seq: int | None = Query(default=None, ge=RECORD_SEQ_MIN),
                 ctx: Ctx = Depends(authorize(*READ, entity=SUBJECT))):
    """Fact profile at an explicit bitemporal coordinate (CURRENT if both omitted; echoed back)."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    wm = current_watermark(ctx.db)
    if known_seq is not None and known_seq > wm["record_seq"]:
        # TIM-REQ-019: never answered from newer knowledge than the requested cutoff can have had (the same refusal as
        # every other historical read and as applicability evaluation).
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"known_seq is beyond the current record sequence {wm['record_seq']}"})
    valid_at = ensure_utc(valid_at) or datetime.now(timezone.utc)
    known = known_seq if known_seq is not None else wm["record_seq"]
    # Stable orders (fact key; conflicts by creation, then ID), never physical row order.
    fds = {f.fact_definition_id: f for f in ctx.db.execute(select(FactDefinition).order_by(FactDefinition.key)).scalars()}
    rows = []
    for fd in fds.values():
        if fd.subject_type != s.subject_type:
            continue
        r = resolve_fact(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, effective_at=valid_at, known_seq=known,
                         create_conflicts=False)
        rows.append({"fact_key": fd.key, "label": fd.label, "data_type": fd.data_type, "enum_domain": fd.enum_domain,
                     "sensitivity": fd.sensitivity, "allowed_provenance": fd.allowed_provenance,
                     "manual_attestation_policy": fd.manual_attestation_policy, "expiry_days": fd.expiry_days,
                     "resolution": {k: v for k, v in r.items() if k != "value"} | (
                         {"value": display_value(fd.sensitivity, r["value"])} if r["known"] else {})})
    conflicts = [to_dict(c) for c in ctx.db.execute(select(FactConflict).where(FactConflict.tenant_id == ctx.tenant,
                                                                               FactConflict.subject_id == subject_id)
                                                    .order_by(FactConflict.created_at, FactConflict.conflict_id)).scalars()]
    return {"subject": to_dict(s), "perspective": {"valid_at": valid_at, "known_seq": known, "cell_id": wm["cell_id"],
                                                   "perspective": "CURRENT" if known_seq is None else "CUSTOM_BITEMPORAL"},
            "facts": rows, "conflicts": conflicts}


@router.get("/subjects/{subject_id}/facts/history")
def fact_history(subject_id: str, ctx: Ctx = Depends(authorize(*READ, entity=SUBJECT))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    fds = {f.fact_definition_id: f for f in ctx.db.execute(select(FactDefinition)).scalars()}
    rows = ctx.db.execute(select(FactAssertion).where(FactAssertion.tenant_id == ctx.tenant,
                                                      FactAssertion.subject_id == s.subject_id)
                          .order_by(FactAssertion.record_from_seq, FactAssertion.revision_id)).scalars().all()
    return [_fact_row(ctx.db, a, fds[a.fact_definition_id]) for a in rows]


@router.get("/subjects/{subject_id}/facts/impact")
def fact_change_impact(subject_id: str, fact_key: str | None = None, valid_from: Instant | None = None,
                       valid_to: Instant | None = None, ctx: Ctx = Depends(authorize(*READ, entity=SUBJECT))):
    """Impact preview of a proposed change of one fact over [valid_from, valid_to) (ZA-APP-001 §16 WF-APP-02, a region
    of the fact profile and read under its authorization; dependents per ZA-TIM-001 §17): the current revisions such a
    write would close and carve, and the obligation definitions, rule versions, recorded evaluations, current
    determinations and obligations, and assurance snapshots that depend on the fact over that interval
    (appl.fact_change_dependents). Dependencies only: nothing is evaluated, written or locked, no value is taken and no
    outcome is predicted ("no predicted legal result shown unless shadow evaluation is explicitly run"; no fact-change
    shadow evaluation is specified). The valid interval is required (ZA-TIM-001 §16: a historical question without a
    valid time is refused, never defaulted) and is validated as a fact write validates it; the record watermark the
    answer was read at is echoed (TIM-REQ-017). fact_key and valid_from are required, checked after the subject is
    scoped, so another organisation's subject ID is answered 404 whatever else is sent (QA-REQ-019)."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    missing = [n for n, v in (("fact_key", fact_key), ("valid_from", valid_from)) if v is None]
    if missing:
        raise HTTPException(422, f"Required: {', '.join(missing)} (the fact and the valid time of the proposed change)")
    fd = ctx.db.execute(select(FactDefinition).where(FactDefinition.key == fact_key)).scalars().first()
    if not fd:
        raise HTTPException(404, "FactDefinition not found")
    if fd.subject_type != s.subject_type:
        raise HTTPException(422, f"{fd.key} attaches to {fd.subject_type}")
    valid_from, valid_to = ensure_utc(valid_from), ensure_utc(valid_to)
    if valid_to is not None and valid_to <= valid_from:
        raise HTTPException(422, "Empty/negative valid range prohibited")
    # Every record below is read as known at this one watermark, the one echoed (TIM-REQ-017): records first written
    # after it, by a commit landing while this request runs, are not part of the answer.
    wm = current_watermark(ctx.db)
    known = wm["record_seq"]
    revisions = overlapping_current_revisions(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, valid_from=valid_from,
                                              valid_to=valid_to, known_seq=known)
    dependents = appl.fact_change_dependents(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd, valid_from=valid_from,
                                             valid_to=valid_to, known_seq=known)
    return {"perspective": {"perspective": "CURRENT", "known_seq": known, "cell_id": wm["cell_id"]},
            "proposed_change": {"subject_id": s.subject_id, "fact_key": fd.key,
                                "fact_definition_id": fd.fact_definition_id, "valid_from": valid_from,
                                "valid_to": valid_to},
            # Current at the watermark, so a closure recorded after it is not shown (as the dossier's _as_known).
            "overlapping_revisions": [{**_fact_row(ctx.db, a, fd), "record_to_seq": None} for a in revisions],
            "dependents": dependents,
            "counts": {"overlapping_revisions": len(revisions), "scopes": len(dependents),
                       "evaluations": sum(len(d["evaluations"]) for d in dependents),
                       "determinations": sum(d["determination"] is not None for d in dependents),
                       "obligations": sum(d["obligation"] is not None for d in dependents),
                       "assurance_snapshots": sum(len(d["assurance_snapshots"]) for d in dependents)},
            "note": "Dependencies only: nothing was evaluated or written and no outcome is predicted (ZA-APP-001 "
                    "WF-APP-02: no predicted legal result unless a shadow evaluation is explicitly run)."}


# ------------------------------------------------------------------ subject applicability history
SUBJECT_APPLICABILITY_PAGE_DEFAULT, SUBJECT_APPLICABILITY_PAGE_MAX = 50, 200
# Position of each record kind among the records of one record sequence, so the merged order is total: an evaluation
# and the determination revision it produced share the sequence of the transaction that recorded both, and the
# evaluation comes first because the revision rests on it.
_HISTORY_KIND_RANK = {"EVALUATION": 0, "DETERMINATION": 1}
# The stored fields each summary shows. Not listed on purpose: an evaluation's trace, input bundle, input_bundle_hash
# and evaluation_key (opened by ID from GET /applicability/evaluations/{id}, which applies the fact-value redaction).
_EVALUATION_SUMMARY_FIELDS = ("effective_at", "record_cutoff_seq", "outcome", "evaluator_build", "trigger",
                              "missing_facts", "non_material_unknowns", "requested_by")
_DETERMINATION_SUMMARY_FIELDS = ("revision_id", "lineage_id", "evaluation_id", "outcome", "state", "basis",
                                 "needs_review", "review_reason", "valid_from", "valid_to", "record_from_seq",
                                 "recorded_at", "actor_id")


def _after_position(seq_col, id_col, rank: int, position: tuple[int, int, str]):
    """The rows of one record kind that come after ``position`` (record_seq, kind rank, id) in the merged order."""
    seq, pos_rank, last_id = position
    if rank > pos_rank:
        return seq_col >= seq
    if rank < pos_rank:
        return seq_col > seq
    return or_(seq_col > seq, and_(seq_col == seq, id_col > last_id))


@router.get("/subjects/{subject_id}/applicability")
def subject_applicability(subject_id: str,
                          obligation_definition_id: str | None = Query(None, min_length=1, max_length=36),
                          as_of_record_seq: int | None = Query(None, ge=0),
                          limit: int = Query(SUBJECT_APPLICABILITY_PAGE_DEFAULT, ge=1,
                                             le=SUBJECT_APPLICABILITY_PAGE_MAX),
                          cursor: str | None = Query(None, min_length=1, max_length=2048),
                          ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SUBJECT))):
    """One subject's applicability history: its recorded evaluations and determination revisions, current or as known at
    a past record sequence, cursor-paginated (ZA-API-001 §14 "GET /v1/subjects/{type}/{id}/applicability: List
    current/historical evaluations and determinations. Cursor + temporal perspective"; ZA-TIM-001 §16 "Returns
    lineage/revisions with valid and record intervals"; the WF-APP-01 History region). Read-only.

    Record time: without ``as_of_record_seq`` the current watermark is pinned on the first page (CURRENT); with it, that
    sequence (AS_OF_RECORD_SEQ; one beyond the watermark is 422 RECORD_SEQ_IN_FUTURE, TIM-REQ-019). Either way the
    pinned watermark and perspective are echoed in ``page.watermark`` (TIM-REQ-017) and carried in the signed cursor, so
    a later page never reads newer knowledge (API-REQ-012). Only records recorded by the pinned sequence are listed; a
    determination revision closed after it is shown open (its later ``record_to_seq`` was not yet known), and
    ``current_at_watermark`` says whether it was the lineage's current revision then. Determination revisions change
    only by being closed (every other change is a new revision), so this is exactly the record as then known.

    Valid time: each record carries its own (an evaluation's ``effective_at``, a revision's [valid_from, valid_to)); the
    listing resolves no state at a valid point (TIM §16: that is a point query with both coordinates).

    Order: (record sequence, evaluation before determination, ID), oldest first, a total order (API §11); filtered by an
    exact ``obligation_definition_id`` (API-REQ-023). Items are summaries: the trace, input bundle, input-bundle hash
    and evaluation key stay on GET /applicability/evaluations/{id}, so this list adds no new route to fact values or
    their hashes. Definition key and title are display labels read as stored (catalogue rows carry no record time).
    Scoped to the caller's organisation in every query, not only by row-level security; the subject's legal entity
    decides entity-scoped access."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if as_of_record_seq is not None and as_of_record_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"as_of_record_seq is beyond the current record sequence {now_seq}"})
    filters = {"subject_id": s.subject_id, "obligation_definition_id": obligation_definition_id,
               "as_of_record_seq": as_of_record_seq, "limit": limit, "sort": "record_seq,kind,id"}
    fp, scope = pagination.query_fingerprint(filters), f"subject-applicability:{ctx.tenant}"
    position = None
    if cursor:
        c = pagination.decode_cursor(cursor, scope=scope, query=fp)
        pinned = c["watermark"]
        try:
            position = (int(c["position"][0]), int(c["position"][1]), str(c["position"][2]))
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, {"error": "invalid_cursor", "message": "The cursor is not valid."}) from None
        if position[1] not in _HISTORY_KIND_RANK.values():
            raise HTTPException(400, {"error": "invalid_cursor", "message": "The cursor is not valid."})
    else:
        pinned = as_of_record_seq if as_of_record_seq is not None else now_seq

    E, D = ApplicabilityEvaluation, ApplicabilityDetermination
    ev_q = select(E).where(E.tenant_id == ctx.tenant, E.subject_id == s.subject_id, E.record_seq <= pinned)
    det_q = select(D).where(D.tenant_id == ctx.tenant, D.subject_id == s.subject_id, D.record_from_seq <= pinned)
    if obligation_definition_id:
        ev_q = ev_q.where(E.obligation_definition_id == obligation_definition_id)
        det_q = det_q.where(D.obligation_definition_id == obligation_definition_id)
    if position is not None:
        ev_q = ev_q.where(_after_position(E.record_seq, E.evaluation_id, _HISTORY_KIND_RANK["EVALUATION"], position))
        det_q = det_q.where(_after_position(D.record_from_seq, D.revision_id, _HISTORY_KIND_RANK["DETERMINATION"],
                                            position))
    # The first limit+1 of each kind in its own order contain the first limit+1 of the merged order. The merge sorts on
    # (record sequence, kind) only and is stable, so records of one kind keep the database's ID order: the same order
    # the cursor predicate above compares IDs in.
    evs = ctx.db.execute(ev_q.order_by(E.record_seq, E.evaluation_id).limit(limit + 1)).scalars().all()
    dets = ctx.db.execute(det_q.order_by(D.record_from_seq, D.revision_id).limit(limit + 1)).scalars().all()
    merged = sorted([(e.record_seq, _HISTORY_KIND_RANK["EVALUATION"], e.evaluation_id, e) for e in evs]
                    + [(d.record_from_seq, _HISTORY_KIND_RANK["DETERMINATION"], d.revision_id, d) for d in dets],
                    key=lambda t: (t[0], t[1]))
    more = len(merged) > limit
    merged = merged[:limit]

    # What the page's records refer to, read once for the page (a fixed number of statements per page).
    odef_ids = sorted({row.obligation_definition_id for *_, row in merged})
    defs = {o.obligation_definition_id: o for o in ctx.db.execute(select(ObligationDefinition).where(
        ObligationDefinition.obligation_definition_id.in_(odef_ids or ["-"]))).scalars()}
    rv_ids = sorted({row.rule_version_id for *_, row in merged if isinstance(row, E)})
    rvs = {r.rule_version_id: r for r in ctx.db.execute(select(RuleVersion).where(
        RuleVersion.rule_version_id.in_(rv_ids or ["-"]))).scalars()}

    def definition(odef_id: str) -> dict:
        od = defs.get(odef_id)
        return ({"obligation_definition_id": odef_id, "key": od.key, "title": od.title} if od else
                missing_ref("obligation_definition_id", odef_id, "key", "title"))

    items = []
    for seq, _, _, row in merged:
        d = to_dict(row)  # the same serialisation (UTC instants) as the evaluation and determination views
        if isinstance(row, E):
            rv = rvs.get(row.rule_version_id)
            items.append({
                "kind": "EVALUATION", "record_seq": seq, "evaluation_id": d["evaluation_id"],
                "obligation_definition": definition(row.obligation_definition_id),
                "rule_version": ({"rule_version_id": rv.rule_version_id, "version": rv.version,
                                  "content_release_id": rv.content_release_id} if rv else
                                 {"rule_version_id": row.rule_version_id, "missing": True}),
                **{k: d[k] for k in _EVALUATION_SUMMARY_FIELDS}, "recorded_at": d["created_at"]})
        else:
            closed_by_then = row.record_to_seq is not None and row.record_to_seq <= pinned
            items.append({
                "kind": "DETERMINATION", "record_seq": seq,
                "obligation_definition": definition(row.obligation_definition_id),
                **{k: d[k] for k in _DETERMINATION_SUMMARY_FIELDS},
                "record_to_seq": d["record_to_seq"] if closed_by_then else None,
                "current_at_watermark": not closed_by_then})
    last = merged[-1] if merged else None
    nxt = (pagination.encode_cursor(scope=scope, query=fp, position=[last[0], last[1], last[2]], watermark=pinned)
           if more else None)
    return pagination.page(items, next_cursor=nxt, limit=limit, watermark={
        "cell_id": CELL_ID, "record_seq": pinned,
        "perspective": "AS_OF_RECORD_SEQ" if as_of_record_seq is not None else "CURRENT"})


class FactIn(BaseModel):
    # Provenance is server-derived (APP-REQ-005, API-REQ-014): a client-supplied provenance_class is rejected.
    model_config = ConfigDict(extra="forbid")
    fact_key: str
    value: object
    valid_from: Instant
    valid_to: Instant | None = None
    source_ref: str | None = Field(default=None, max_length=300)  # API-REQ-033: applicability_fact_assertion_revision.source_ref
    change_kind: str = "CREATE"
    reason: str | None = None


@router.post("/subjects/{subject_id}/facts")
def assert_fact_ep(subject_id: str, body: FactIn, ctx: Ctx = Depends(authorize("manage_facts"))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    fd = ctx.db.execute(select(FactDefinition).where(FactDefinition.key == body.fact_key)).scalars().first()
    if not fd:
        raise HTTPException(404, "FactDefinition not found")
    if fd.subject_type != s.subject_type:
        raise HTTPException(422, f"{fd.key} attaches to {fd.subject_type}")
    if ctx.actor.actor_type == "AGENT":
        provenance = "P5_INFERRED_AI"  # agent-proposed facts are never binding
    elif ctx.actor.actor_type == "HUMAN":
        provenance = "P3_CUSTOMER_ATTESTED"
        if fd.manual_attestation_policy == "DISALLOWED":
            raise HTTPException(422, f"{fd.key} does not allow manual attestation; use a certified source")
    else:
        raise HTTPException(403, "System-sourced facts must arrive through the certified connector path, not this endpoint")
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        # The dependents' scopes are locked before the fact write takes the record sequence (lock order:
        # engines/applicability.lock_decisions), so this cannot deadlock with an attestation of one of them.
        plan = appl.fact_impact_plan(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd)
        appl.lock_fact_impact(ctx.db, ctx.tenant, plan)
        created = assert_fact(ctx.db, tenant_id=ctx.tenant, subject=s, fd=fd, value=body.value, valid_from=body.valid_from,
                              valid_to=body.valid_to, provenance_class=provenance, source_ref=body.source_ref,
                              actor_id=ctx.actor.actor_id, change_kind=body.change_kind, reason=body.reason)
        reeval = appl.reevaluate_for_fact(ctx.db, tenant_id=ctx.tenant, holder=s, fd=fd, actor_id=ctx.actor.actor_id,
                                          plan=plan)
        ctx.db.commit()
    return {"revisions": [_fact_row(ctx.db, a, fd) for a in created], "impact_reevaluations": reeval}


class EvalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    subject_id: str
    obligation_definition_id: str
    effective_at: Instant | None = None
    record_cutoff_seq: int | None = Field(default=None, ge=RECORD_SEQ_MIN)
    # ZA-APP-001 §06 EvaluationRequest.trigger: "Enum; required ONBOARDING, FACT_CHANGED, RULE_CHANGED, CORRECTION,
    # RECLASSIFICATION, EXPLICIT_REQUEST, SCHEDULED_REVIEW, RECONSTRUCTION" (stored on the immutable evaluation).
    trigger: Literal["ONBOARDING", "FACT_CHANGED", "RULE_CHANGED", "CORRECTION", "RECLASSIFICATION",
                     "EXPLICIT_REQUEST", "SCHEDULED_REVIEW", "RECONSTRUCTION"] = "EXPLICIT_REQUEST"


_LOOK_UP = object()


def _fact_sensitivities(db, evaluations) -> dict[str, str | None]:
    """FactDefinition key -> sensitivity for every fact the evaluations' frozen bundles consumed (one query)."""
    keys = {fi.get("fact_definition") for e in evaluations if isinstance(e.input_bundle, dict)
            for fi in (e.input_bundle.get("fact_inputs") or []) if isinstance(fi, dict)} - {None}
    if not keys:
        return {}
    return dict(db.execute(select(FactDefinition.key, FactDefinition.sensitivity)
                           .where(FactDefinition.key.in_(keys))).all())


def _eval_view(db, e: ApplicabilityEvaluation, det=_LOOK_UP, traces: dict | None = None,
               sensitivities: dict | None = None) -> dict:
    """`traces`: provenance already traced in this request, by rule version (a list shares a few rule versions).
    `sensitivities`: FactDefinition key -> sensitivity already read for this request (a list reads them once)."""
    d = to_dict(e)
    # The frozen bundle as a response may show it: a redacted fact's value hash is withheld (facts.input_bundle_view).
    # A new structure; the stored bundle, which replay re-hashes below, is never modified.
    d["input_bundle"] = input_bundle_view(e.input_bundle, _fact_sensitivities(db, [e]) if sensitivities is None
                                          else sensitivities)
    # Which APP-001 §06 key definition the stored evaluation_key was made under (recomputed from the frozen bundle; a
    # record made before the full key is reported as such, never as if made under it).
    d["evaluation_key_profile"] = appl.evaluation_key_profile(e)
    od = db.get(ObligationDefinition, e.obligation_definition_id)
    s = db.get(Subject, e.subject_id)
    rv = db.get(RuleVersion, e.rule_version_id)
    # The definition and subject are referenced by ID only (no foreign key across these domains): one that cannot be
    # found is reported as missing, so one broken reference never fails the whole view or list.
    d["obligation_definition"] = ({"key": od.key, "title": od.title, "material": od.material} if od else
                                  missing_ref("obligation_definition_id", e.obligation_definition_id, "key", "title"))
    d["subject"] = ({"display_name": s.display_name, "subject_type": s.subject_type} if s else
                    missing_ref("subject_id", e.subject_id, "display_name", "subject_type"))
    # A rule version the evaluation names but that cannot be found is reported as missing, never replaced by another
    # version (APP-REQ-003; no foreign key stops it, so the read must not assume it).
    d["rule_version"] = {"version": rv.version, "ast_hash": rv.ast_hash, "content_release_id": rv.content_release_id,
                         "source_mappings": rv.source_mappings} if rv else {"rule_version_id": e.rule_version_id,
                                                                            "missing": True}
    if det is _LOOK_UP:
        det = appl.current_determination(db, e.tenant_id, e.subject_id, e.obligation_definition_id)
    d["current_determination"] = to_dict(det) if det else None
    if rv is None:
        d["regulatory_provenance"] = None
    elif traces is not None:
        if rv.rule_version_id not in traces:
            traces[rv.rule_version_id] = catalog.trace_rule_version(db, rv)
        d["regulatory_provenance"] = evaluation_provenance(e, rv, traces[rv.rule_version_id])
    else:
        d["regulatory_provenance"] = evaluation_provenance(e, rv, catalog.trace_rule_version(db, rv))
    return d


def missing_ref(id_field: str, value: str | None, *fields: str) -> dict:
    """A referenced record that cannot be found: its ID, the display fields as null and ``missing: true``."""
    return {id_field: value, **dict.fromkeys(fields), "missing": True}


def evaluation_provenance(e: ApplicabilityEvaluation, rule: RuleVersion, trace: dict) -> dict:
    """The rule trace as this evaluation's provenance: plus whether the rule belongs to the evaluated definition."""
    return catalog.with_problems(trace, catalog.evaluation_rule_problems(e, rule))


@router.post("/applicability/evaluations")
def request_evaluation(body: EvalIn, ctx: Ctx = Depends(authorize("request_evaluation"))):
    s = ctx.scoped(ctx.db.get(Subject, body.subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, body.obligation_definition_id)
    if not od:
        raise HTTPException(404, "obligation definition")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if body.record_cutoff_seq is not None and body.record_cutoff_seq > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"record_cutoff_seq is beyond the current record sequence {now_seq}"})
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od,
                           effective_at=body.effective_at or datetime.now(timezone.utc),
                           record_cutoff_seq=body.record_cutoff_seq, trigger=body.trigger, actor_id=ctx.actor.actor_id)
    ctx.db.commit()
    return _eval_view(ctx.db, ev)


@router.post("/applicability/evaluate-all")
def evaluate_all(subject_id: str, ctx: Ctx = Depends(authorize("request_evaluation"))):
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    out = []
    odefs = ctx.db.execute(select(ObligationDefinition).where(ObligationDefinition.subject_type == s.subject_type)
                           .order_by(ObligationDefinition.obligation_definition_id)).scalars().all()
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):  # no partial batch on tampering
        appl.lock_decisions(ctx.db, [(ctx.tenant, s.subject_id, od.obligation_definition_id) for od in odefs])
        for od in odefs:
            try:
                ev = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=datetime.now(timezone.utc),
                                   record_cutoff_seq=None, trigger="EXPLICIT_REQUEST", actor_id=ctx.actor.actor_id)
                out.append({"obligation": od.key, "outcome": ev.outcome, "evaluation_id": ev.evaluation_id})
            except HTTPException as e:
                out.append({"obligation": od.key, "error": e.detail})
    ctx.db.commit()
    return out


@router.get("/applicability/evaluations")
def list_evaluations(subject_id: str | None = None, ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    q = select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.tenant_id == ctx.tenant)
    if subject_id:
        q = q.where(ApplicabilityEvaluation.subject_id == subject_id)
    # Unique tie-break, so the 200-row cut and the order never depend on physical row order.
    evs = ctx.db.execute(q.order_by(ApplicabilityEvaluation.record_seq.desc(), ApplicabilityEvaluation.evaluation_id)
                         .limit(200)).scalars().all()
    # What the rows refer to, read once instead of per row: the subjects, obligation definitions and rule versions are
    # loaded into the session (and held here) so db.get() in _eval_view finds them without a query.
    refs = [*ctx.db.execute(select(Subject).where(Subject.subject_id.in_({e.subject_id for e in evs}))).scalars(),
            *ctx.db.execute(select(ObligationDefinition).where(
                ObligationDefinition.obligation_definition_id.in_({e.obligation_definition_id for e in evs}))).scalars(),
            *ctx.db.execute(select(RuleVersion).where(RuleVersion.rule_version_id.in_({e.rule_version_id for e in evs}))).scalars()]
    dets = appl.current_determinations(ctx.db, ctx.tenant, {(e.subject_id, e.obligation_definition_id) for e in evs})
    traces: dict = {}
    sensitivities = _fact_sensitivities(ctx.db, evs)
    out = [_eval_view(ctx.db, e, dets.get((e.subject_id, e.obligation_definition_id)), traces, sensitivities)
           for e in evs]
    del refs
    return out


@router.get("/applicability/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=EVALUATION))):
    e = ctx.scoped(ctx.db.get(ApplicabilityEvaluation, evaluation_id), "evaluation")
    d = _eval_view(ctx.db, e)
    d["determination_history"] = [to_dict(x) for x in ctx.db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == ctx.tenant, ApplicabilityDetermination.subject_id == e.subject_id,
        ApplicabilityDetermination.obligation_definition_id == e.obligation_definition_id)
        .order_by(ApplicabilityDetermination.record_from_seq, ApplicabilityDetermination.revision_id)).scalars()]
    d["challenges"] = [to_dict(c) for c in ctx.db.execute(select(DeterminationChallenge).where(
        DeterminationChallenge.tenant_id == ctx.tenant, DeterminationChallenge.evaluation_id == evaluation_id)
        .order_by(DeterminationChallenge.created_at, DeterminationChallenge.challenge_id)).scalars()]
    d["attestations"] = [to_dict(a) for a in ctx.db.execute(select(AdoptionAttestation).where(
        AdoptionAttestation.tenant_id == ctx.tenant,
        AdoptionAttestation.target_revision_id.in_([x["revision_id"] for x in d["determination_history"]] or ["-"]))
        .order_by(AdoptionAttestation.record_seq, AdoptionAttestation.attestation_id)).scalars()]
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        d["replay"] = _replay(ctx, e)
    # The exact RuleVersion this evaluation ran, traced to the requirement versions, provisions and source snapshots it
    # rests on and to how each required fact maps to source content (DOM §1 canonical chain; ZA-RUL-001 §02/§08).
    rule = ctx.db.get(RuleVersion, e.rule_version_id)
    d["regulatory_provenance"] = (evaluation_provenance(e, rule, catalog.trace_rule_version(ctx.db, rule)) if rule
                                  else _missing_rule_provenance(e))
    d["record_watermark"] = _watermark(ctx.db)
    return d


def _missing_rule_provenance(e: ApplicabilityEvaluation) -> dict:
    """The provenance of an evaluation whose RuleVersion cannot be found: explicitly incomplete, never derived from
    another (newer or older) version of the rule."""
    return {"binding_basis": "INCOMPLETE", "rule_version_id": e.rule_version_id, "missing": True,
            "integrity": catalog.integrity_verdict([catalog._problem("RULE_VERSION_MISSING", "INCOMPLETE",
                                                                     rule_version_id=e.rule_version_id)])}


def _watermark(db) -> dict:
    """The record watermark this response was read at (TIM-REQ-017)."""
    wm = current_watermark(db)
    return {"cell_id": wm["cell_id"], "record_seq": wm["record_seq"]}


def _replay(ctx: Ctx, e: ApplicabilityEvaluation) -> dict:
    """Deterministic reproducibility check (TIM-REQ-012, APP-REQ-003): re-resolve the exact fact revisions known at the
    evaluation's record cutoff under its own RuleVersion and evaluator, and compare with what was recorded. Nothing is
    written. `matches` keeps its meaning (same outcome and same input bundle hash); `checks` and `status` add the
    stored-record checks:

      RULE_VERSION_PRESENT  the RuleVersion the evaluation names still exists (never substituted by another);
      RUNTIME_SUPPORTS_VERSIONS / RUNTIME_WITHIN_LIMITS  this runtime may execute it at all (RUL-REQ-028 / -029); if
                            not, nothing is run and the status is NOT_REPRODUCED;
      BUNDLE_BINDS_RULE     the stored bundle names that RuleVersion and its AST hash, and the evaluator build the row
                            records (the build its inputs were frozen and hashed under: a row naming another build, or
                            none, does not establish which evaluator produced it);
      STORED_BUNDLE_HASH    the stored frozen bundle still hashes to the recorded input_bundle_hash;
      EVALUATOR_BUILD       the running evaluator build is the one recorded (replay across builds is a governed
                            regression, STA-REQ-032);
      REDERIVED_BUNDLE      the fact revisions known at the cutoff re-derive the recorded inputs. Compared under one
                            build: the bundle hash includes the build, so comparing the running build's hash with the
                            record's would fail for every evaluation of another build and hide altered inputs there;
      OUTCOME, TRACE        the evaluator returns the recorded outcome and the same trace tree.

    status: REPRODUCED (every check passed); BUILD_CHANGED (the evaluation was recorded by another evaluator build and its
    stored and re-derived inputs are intact: ZA-APP-001 §14 makes the result deterministic only for "Same evaluator build +
    RuleVersion + EvaluationInputBundle hash", so whatever this build answers, including a refusal, neither reproduces nor
    refutes the record); NOT_REPRODUCED (a stored-record or input check failed, or this build gives another answer for
    the build the record names); INCOMPLETE (a referenced record is missing)."""
    from ..codes import EVALUATOR_BUILD
    from ..platform import digest
    s = ctx.db.get(Subject, e.subject_id)
    od = ctx.db.get(ObligationDefinition, e.obligation_definition_id)
    rv = ctx.db.get(RuleVersion, e.rule_version_id)
    if rv is None or s is None or od is None:
        missing = "RULE_VERSION_MISSING" if rv is None else "SUBJECT_MISSING" if s is None else "OBLIGATION_DEFINITION_MISSING"
        return {"reproduced_outcome": None, "matches": False, "error": missing, "rule_version_id": e.rule_version_id,
                "status": "INCOMPLETE", "checks": [{"check": "RULE_VERSION_PRESENT", "passed": rv is not None}]}
    stored = e.input_bundle or {}
    unsupported = rules.unsupported_versions(rv.rule_schema_version, rv.operator_set_version)
    if unsupported:
        # RUL-REQ-028: this runtime cannot run the rule version, so it cannot reproduce the evaluation (and does not
        # try an approximate run). The stored record is unchanged and still readable.
        return {"reproduced_outcome": None, "matches": False, "error": "SCHEMA_INCOMPATIBLE", "unsupported": unsupported,
                "status": "NOT_REPRODUCED", "failed_checks": ["RUNTIME_SUPPORTS_VERSIONS"],
                "checks": [{"check": "RULE_VERSION_PRESENT", "passed": True},
                           {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": False, **unsupported}]}
    over = rules.resource_limit_violations(rv.canonical_ast)
    if over:
        # RUL-REQ-029: the rule version exceeds this runtime's complexity limits, so it is not run (a budget breach is
        # an infrastructure error) and the evaluation cannot be reproduced here; no approximate or partial run.
        return {"reproduced_outcome": None, "matches": False, "error": "EVALUATOR_UNAVAILABLE",
                "reason": "RUL_RESOURCE_LIMIT", "violations": [{k: v for k, v in x.items() if k != "message"} for x in over],
                "status": "NOT_REPRODUCED", "failed_checks": ["RUNTIME_WITHIN_LIMITS"],
                "checks": [{"check": "RULE_VERSION_PRESENT", "passed": True},
                           {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": True},
                           {"check": "RUNTIME_WITHIN_LIMITS", "passed": False}]}
    checks = [{"check": "RULE_VERSION_PRESENT", "passed": True},
              {"check": "RUNTIME_SUPPORTS_VERSIONS", "passed": True},
              {"check": "RUNTIME_WITHIN_LIMITS", "passed": True},
              {"check": "BUNDLE_BINDS_RULE", "passed": stored.get("rule_version_id") == e.rule_version_id
               and stored.get("rule_ast_hash") == rv.ast_hash and stored.get("evaluator_build") == e.evaluator_build},
              {"check": "STORED_BUNDLE_HASH", "passed": digest(stored) == e.input_bundle_hash},
              {"check": "EVALUATOR_BUILD", "passed": e.evaluator_build == EVALUATOR_BUILD,
               "recorded": e.evaluator_build, "running": EVALUATOR_BUILD}]
    # The recorded inputs as this build would have frozen them: the record's own hash when it names this build, else the
    # stored bundle (whose integrity STORED_BUNDLE_HASH checks) re-hashed with this build's identifier.
    recorded_inputs = e.input_bundle_hash if stored.get("evaluator_build") == EVALUATOR_BUILD \
        else digest({**stored, "evaluator_build": EVALUATOR_BUILD})

    def classified(result: dict) -> dict:
        failed = [c["check"] for c in checks if not c["passed"]]
        if not failed:
            status = "REPRODUCED"
        elif "EVALUATOR_BUILD" in failed and set(failed) <= {"EVALUATOR_BUILD", "OUTCOME", "TRACE"}:
            status = "BUILD_CHANGED"
        else:
            status = "NOT_REPRODUCED"
        return {**result, "status": status, "checks": checks, "failed_checks": failed}

    try:
        out = appl.evaluate(ctx.db, tenant_id=e.tenant_id, subject=s, odef=od, effective_at=e.effective_at,
                            record_cutoff_seq=e.record_cutoff_seq, trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id,
                            rule_version=rv, persist=False)
    except HTTPException as x:
        if not isinstance(x.__cause__, rules.RuleError):
            raise
        # The evaluator refuses the stored inputs it was given (e.g. a non-finite DECIMAL stored before fact writes
        # refused it): this runtime cannot reproduce the recorded outcome. NOT_REPRODUCED with the evaluator's code, or
        # BUILD_CHANGED when another build recorded it from the same intact inputs; the stored record stays readable and
        # nothing is substituted.
        checks.append({"check": "REDERIVED_BUNDLE", "passed": getattr(x, "input_bundle_hash", None) == recorded_inputs})
        checks.append({"check": "OUTCOME", "passed": False, "error": x.__cause__.code, "node_id": x.__cause__.node_id})
        return classified({"reproduced_outcome": None, "matches": False, "error": x.__cause__.code})
    checks += [{"check": "REDERIVED_BUNDLE", "passed": out["input_bundle_hash"] == recorded_inputs},
               {"check": "OUTCOME", "passed": out["outcome"] == e.outcome},
               {"check": "TRACE", "passed": digest(out["trace"]) == digest(e.trace)}]
    return classified({"reproduced_outcome": out["outcome"],
                       "matches": out["outcome"] == e.outcome and out["input_bundle_hash"] == e.input_bundle_hash,
                       "input_bundle_hash": out["input_bundle_hash"]})


@router.get("/applicability/determinations")
def determinations(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    rows = ctx.db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == ctx.tenant,
                                                                   ApplicabilityDetermination.record_to_seq.is_(None))
                          .order_by(ApplicabilityDetermination.record_from_seq,
                                    ApplicabilityDetermination.revision_id)).scalars().all()
    out = []
    for r in rows:
        d = to_dict(r)
        s = ctx.db.get(Subject, r.subject_id)
        od = ctx.db.get(ObligationDefinition, r.obligation_definition_id)
        d["subject"] = s.display_name if s else None
        d["obligation_definition"] = od.key if od else None
        d["obligation_title"] = od.title if od else None
        # A reference that cannot be found is named, never a failure of the whole register (no foreign key covers it).
        d["missing_references"] = [k for k, v in (("subject_id", s), ("obligation_definition_id", od)) if v is None]
        out.append(d)
    return out


class AttestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    decision: str  # ADOPT | REJECT
    authority_basis: str = Field(max_length=200)  # column size (API-REQ-033)
    reason: str | None = None
    # The exact determination revision reviewed and the evaluation it rests on (APP §12 "exact determination revision";
    # APP §16 "stale/superseded revision cannot be silently attested"). Optional, so existing callers keep working;
    # when sent, a decision on any other revision or evaluation is refused with 409.
    revision_id: str | None = Field(default=None, max_length=36)
    evaluation_id: str | None = Field(default=None, max_length=36)


# ZA-API-001 §08: an optional Idempotency-Key on the two determination actions (engines/idempotency.py).
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/applicability/determinations/{lineage_id}/attest")
def attest(lineage_id: str, body: AttestIn, ctx: Ctx = Depends(authorize("attest", human=True)),
           idempotency_key: IdempotencyKey = None):
    if body.decision not in ("ADOPT", "REJECT"):
        raise HTTPException(422, "decision must be ADOPT or REJECT")
    r = appl.attest(ctx.db, ctx=ctx, determination_lineage_id=lineage_id, decision=body.decision,
                    authority_basis=body.authority_basis, reason=body.reason, expected_revision_id=body.revision_id,
                    expected_evaluation_id=body.evaluation_id, idempotency_key=idempotency_key)
    ctx.db.commit()
    out = {"determination": to_dict(r["determination"]), "attestation": to_dict(r["attestation"]),
           "obligation": to_dict(r["obligation"]) if r["obligation"] else None}
    return appl.as_committed(out) if r.get("replayed") else out


class ChallengeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # API-REQ-014: unknown fields are refused
    challenge_class: str  # one of CHALLENGE_DISPOSITIONS: FACT_WRONG, FACT_MISSING, RULE_WRONG, INTERPRETATION, DECLINE
    detail: str


# APP §09 "Challenge types", one code each (the five rows of that table; the codes are this API's names for them).
CHALLENGE_DISPOSITIONS = {
    "FACT_WRONG": "Record a corrected FactAssertion; a new evaluation will be created.",
    "FACT_MISSING": "Resolve the linked FactRequest.",
    "RULE_WRONG": "Routed to regulatory-content operations; released rule unchanged until corrected via governance.",
    "INTERPRETATION": "Requires governed legal disposition; evaluation remains unchanged.",
    "DECLINE": "Customer declines to adopt: an authorised attestor records REJECTED with a reason; the evaluation is "
               "not changed.",
}


CHALLENGE_OPERATION = "POST /applicability/determinations/{lineage_id}/challenge"


@router.post("/applicability/determinations/{lineage_id}/challenge")
def challenge(lineage_id: str, body: ChallengeIn, ctx: Ctx = Depends(authorize("challenge")),
              idempotency_key: IdempotencyKey = None):
    """Challenge creates a governed record; never rewrites the evaluation (FR-APP-005, APP-REQ-011). A PROPOSED
    determination moves to IN_REVIEW (GLS: PROPOSED "May move to IN_REVIEW"); in any other state the challenge is
    recorded without a state change. Serialised with evaluation and attestation of the same scope. Without an
    Idempotency-Key every request is a new challenge; with one, a retry returns the challenge it recorded (API-001 §08)."""
    request = {"determination_lineage_id": lineage_id, "challenge_class": body.challenge_class,
               "detail": body.detail.strip()}
    done = idempotency.replay(ctx.db, tenant_id=ctx.tenant, operation=CHALLENGE_OPERATION, key=idempotency_key,
                              request=request)
    if done is not None:
        prior = ctx.db.get(DeterminationChallenge, done)
        if prior is not None and prior.tenant_id == ctx.tenant:
            return to_dict(prior)
    if body.challenge_class not in CHALLENGE_DISPOSITIONS:
        raise HTTPException(422, {"error": "CHALLENGE_CLASS_INVALID", "allowed": sorted(CHALLENGE_DISPOSITIONS)})
    detail = body.detail.strip()
    if not detail:
        raise HTTPException(422, {"error": "CHALLENGE_DETAIL_REQUIRED", "message": "Describe the challenge."})

    def current():  # the one current revision; two are refused, never one challenged by row order
        return appl._one_current_determination(ctx.db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == ctx.tenant, ApplicabilityDetermination.lineage_id == lineage_id,
            ApplicabilityDetermination.record_to_seq.is_(None)).execution_options(populate_existing=True)).scalars())
    cur = current()
    if not cur:
        raise HTTPException(404)
    appl.lock_decision(ctx.db, ctx.tenant, cur.subject_id, cur.obligation_definition_id)
    cur = current()  # read again under the lock: the revision challenged is the one current now
    if not cur:
        raise HTTPException(404)
    seq = next_record_seq(ctx.db)
    c = DeterminationChallenge(tenant_id=ctx.tenant, determination_lineage_id=lineage_id, evaluation_id=cur.evaluation_id,
                               challenge_class=body.challenge_class, detail=detail, actor_id=ctx.actor.actor_id,
                               disposition=CHALLENGE_DISPOSITIONS[body.challenge_class])
    ctx.db.add(c)
    ctx.db.flush()
    idempotency.remember(ctx.db, tenant_id=ctx.tenant, operation=CHALLENGE_OPERATION, key=idempotency_key,
                         request=request, result_ref=c.challenge_id)
    new_state = cur.state
    if cur.state == "PROPOSED":
        appl._new_det_revision(ctx.db, cur, tenant_id=ctx.tenant, subject_id=cur.subject_id, odef_id=cur.obligation_definition_id,
                               evaluation=ctx.db.get(ApplicabilityEvaluation, cur.evaluation_id), state="IN_REVIEW",
                               seq=seq, actor_id=ctx.actor.actor_id, basis="Challenged: " + body.challenge_class)
        new_state = "IN_REVIEW"
    # APP §14: "Every human adoption/challenge ... is separate from operational telemetry and retained under audit policy".
    audit(ctx.db, action="determination.challenge", object_type="ApplicabilityDetermination", object_id=lineage_id,
          actor=ctx.actor, tenant_id=ctx.tenant, prior=cur.state, new=new_state, record_seq=seq,
          reason=f"{body.challenge_class} (challenge {c.challenge_id})")
    # APP §12 minimum payload: "challenge ID, determination/evaluation IDs, challenge class, actor".
    emit(ctx.db, event_type="determination.challenged", aggregate_type="ApplicabilityDetermination", aggregate_id=lineage_id,
         tenant_id=ctx.tenant, actor=ctx.actor_ref(), record_seq=seq,
         payload={"challenge_id": c.challenge_id, "challenge_class": body.challenge_class,
                  "determination_revision_id": cur.revision_id, "evaluation_id": cur.evaluation_id})
    ctx.db.commit()
    return to_dict(c)


@router.get("/fact-requests")
def fact_requests(ctx: Ctx = Depends(authorize(*READ))):
    out = []
    for fr in ctx.db.execute(select(FactRequest).where(FactRequest.tenant_id == ctx.tenant)
                             .order_by(FactRequest.created_at.desc(), FactRequest.request_id)).scalars():
        d = to_dict(fr)
        fd = ctx.db.get(FactDefinition, fr.fact_definition_id)
        d["fact_key"], d["fact_label"], d["data_type"], d["enum_domain"] = fd.key, fd.label, fd.data_type, fd.enum_domain
        d["manual_attestation_policy"] = fd.manual_attestation_policy
        d["subject"] = ctx.db.get(Subject, fr.subject_id).display_name
        out.append(d)
    return out


def _fact_request_entity(db, fr: FactRequest) -> str | None:
    # A fact request belongs to the legal entity of the subject that holds the fact (one of this organisation's).
    s = db.get(Subject, fr.subject_id)
    return s.entity_id if s is not None and s.tenant_id == fr.tenant_id else None


FACT_REQUEST = entity_of(FactRequest, "request_id", "fact request", entity=_fact_request_entity)
_SOURCE_MAPPING_FIELDS = ("source_mapping_id", "requirement_version_id", "provision_id", "authoritative_reference")


@router.get("/fact-requests/{request_id}")
def fact_request_view(request_id: str, ctx: Ctx = Depends(authorize(*READ, entity=FACT_REQUEST))):
    """One missing-fact request with why Assure needs the fact (ZA-APP-001 §08 resolution wireframe: "Why Assure needs
    this fact: Requirement/provision, rule branch, effective date, current result = UNCERTAIN"; "Fact definition:
    Canonical name, expected type/domain, acceptable sources, sensitivity and why it is material"; "Current candidates:
    Existing assertions, provenance, validity, conflicts, expiry; sensitive values masked by role"; WF-APP-02 "Missing
    facts: Open FactRequests with why-needed explanation"). Read-only: nothing is evaluated, resolved or written.

    ``needed_by``: each evaluation the request links (this organisation's, in record order; a linked ID that cannot be
    found is listed as missing, never dropped), with what it recorded: outcome, effective date, record cutoff, the
    obligation definition and exact rule version, whether the fact is material to it (its stored missing_facts), how
    its frozen input bundle recorded the fact (the unknown reason; never a value or value hash), every rule branch from
    the root of its stored trace to the fact (rules.fact_ref_paths: recorded node results, no values) and the source
    mappings on those branches resolved against the rule version's own mapping records (requirement version and
    provision).

    ``current_resolution``: the fact as the deterministic resolution algorithm (APP §04 steps 1-6, facts.resolve_fact)
    resolves it today for that evaluation's subject at that evaluation's effective date: the record watermark read
    once for the whole response, the valid instant explicit (TIM-REQ-006/017). It lists the resolved assertion, or the
    reason it is unknown with every admissible candidate when they disagree (FACT_CONFLICT: the engine applies no
    precedence, "latest record wins" being prohibited) and every assertion the policy filter or expiry rejected, each
    with its reason. Assertions are shown as every other fact read shows them (facts.assertion_view: a redacted
    sensitivity has no value). It does not predict an outcome; which evaluations a new assertion re-runs is the fact
    impact preview (GET /subjects/{id}/facts/impact).

    The request row itself (status, owner role, due date, links) is shown as stored: it keeps no record time or state
    history, so it has no historical perspective. Authorization is the fact-request register's and the fact profile's
    (READ); the holding subject's legal entity decides entity-scoped access; another organisation's request is 404."""
    db = ctx.db
    fr = ctx.scoped(db.get(FactRequest, request_id), "fact request")
    # Read after the request row: every evaluation the row links was recorded by this watermark.
    wm = current_watermark(db)
    known = wm["record_seq"]
    fd = db.get(FactDefinition, fr.fact_definition_id)
    holder = db.get(Subject, fr.subject_id)
    holder = holder if holder is not None and holder.tenant_id == ctx.tenant else None
    linked = [str(i) for i in (fr.linked_evaluation_ids or [])]
    E = ApplicabilityEvaluation
    evs = db.execute(select(E).where(E.tenant_id == ctx.tenant, E.evaluation_id.in_(linked or ["-"]))
                     .order_by(E.record_seq, E.evaluation_id)).scalars().all()
    odefs = {o.obligation_definition_id: o for o in db.execute(select(ObligationDefinition).where(
        ObligationDefinition.obligation_definition_id.in_(sorted({e.obligation_definition_id for e in evs}) or ["-"])))
        .scalars()}
    rvs = {r.rule_version_id: r for r in db.execute(select(RuleVersion).where(
        RuleVersion.rule_version_id.in_(sorted({e.rule_version_id for e in evs}) or ["-"]))).scalars()}
    subjects = {s.subject_id: s for s in db.execute(select(Subject).where(
        Subject.tenant_id == ctx.tenant, Subject.subject_id.in_(sorted({e.subject_id for e in evs}) or ["-"])))
        .scalars()}

    # One resolution per (subject, valid instant) the linked evaluations need, all at the one watermark.
    resolutions: dict[tuple, dict] = {}
    for e in evs:
        point = (e.subject_id, ensure_utc(e.effective_at))
        if fd is not None and e.subject_id in subjects and point not in resolutions:
            resolutions[point] = resolve_fact(db, tenant_id=ctx.tenant, subject=subjects[e.subject_id], fd=fd,
                                              effective_at=e.effective_at, known_seq=known, create_conflicts=False)
    shown = sorted({rid for r in resolutions.values() for rid in (
        *([r["revision_id"]] if r.get("known") else []), *(r.get("candidates") or []),
        *(x["revision_id"] for x in r.get("rejected") or []))})
    assertions = {a.revision_id: a for a in db.execute(select(FactAssertion).where(
        FactAssertion.tenant_id == ctx.tenant, FactAssertion.revision_id.in_(shown or ["-"]))).scalars()}

    def assertion(rid: str) -> dict:
        a = assertions.get(rid)
        if a is None:
            return missing_ref("revision_id", rid)
        row = _fact_row(db, a, fd)
        # Known at the watermark: a closure recorded after it is not shown (as the impact preview reads it).
        return {**row, "record_to_seq": None} if a.record_to_seq is not None and a.record_to_seq > known else row

    def resolution_view(e: ApplicabilityEvaluation, valid_at: str) -> dict | None:
        r = resolutions.get((e.subject_id, ensure_utc(e.effective_at)))
        if r is None:
            return None
        return {"valid_at": valid_at, "known_seq": known, "known": r["known"],
                "reason": None if r["known"] else r.get("reason"),
                "assertion": assertion(r["revision_id"]) if r["known"] else None,
                "candidates": [assertion(rid) for rid in r.get("candidates") or []],
                "rejected": [{"reason": x["reason"], "assertion": assertion(x["revision_id"])}
                             for x in r.get("rejected") or []]}

    def needed_by(e: ApplicabilityEvaluation) -> dict:
        effective_at = ensure_utc(e.effective_at).isoformat().replace("+00:00", "Z")  # as to_dict serialises it
        od, rv = odefs.get(e.obligation_definition_id), rvs.get(e.rule_version_id)
        branches = rules.fact_ref_paths(e.trace, fd.key) if fd is not None else []
        records = {m.get("source_mapping_id"): m for m in (rv.source_mappings or []) if isinstance(m, dict)} if rv else {}
        mapping_ids = list(dict.fromkeys(m for b in branches for step in b["path"]
                                         for m in step.get("source_mappings") or []))
        return {
            "evaluation_id": e.evaluation_id, "record_seq": e.record_seq, "subject_id": e.subject_id,
            "effective_at": effective_at, "record_cutoff_seq": e.record_cutoff_seq, "outcome": e.outcome,
            "obligation_definition": ({"obligation_definition_id": od.obligation_definition_id, "key": od.key,
                                       "title": od.title, "material": od.material} if od else
                                      missing_ref("obligation_definition_id", e.obligation_definition_id, "key",
                                                  "title", "material")),
            "rule_version": ({"rule_version_id": rv.rule_version_id, "version": rv.version,
                              "content_release_id": rv.content_release_id} if rv else
                             {"rule_version_id": e.rule_version_id, "missing": True}),
            "material": fd is not None and fd.key in (e.missing_facts or []),
            "consumed": appl._consumed(e, fd.key) if fd is not None else None,
            "rule_branches": branches,
            "source_mappings": [{k: records[m].get(k) for k in _SOURCE_MAPPING_FIELDS} if m in records else
                                {"source_mapping_id": m, "missing": True} for m in mapping_ids],
            "current_resolution": resolution_view(e, effective_at)}

    found = {e.evaluation_id for e in evs}
    return {
        "fact_request": to_dict(fr),
        "subject": ({"subject_id": holder.subject_id, "display_name": holder.display_name,
                     "subject_type": holder.subject_type, "entity_id": holder.entity_id} if holder else
                    missing_ref("subject_id", fr.subject_id, "display_name", "subject_type", "entity_id")),
        "fact_definition": ({k: getattr(fd, k) for k in (
            "fact_definition_id", "key", "label", "data_type", "enum_domain", "subject_type", "sensitivity",
            "allowed_provenance", "manual_attestation_policy", "expiry_days", "conflict_policy", "owner_role")}
            if fd is not None else missing_ref("fact_definition_id", fr.fact_definition_id, "key", "label")),
        "needed_by": [needed_by(e) for e in evs]
                     + [{"evaluation_id": i, "missing": True} for i in dict.fromkeys(linked) if i not in found],
        "record_watermark": {"cell_id": wm["cell_id"], "record_seq": known, "perspective": "CURRENT"}}


@router.get("/history/compare")
def compare(subject_id: str, obligation_definition_id: str, valid_at: Instant,
            left_seq: int = Query(ge=0), right_seq: int = Query(ge=0),
            ctx: Ctx = Depends(authorize(*DOSSIER_READ, entity=SUBJECT))):
    """Same valid time, two record cutoffs: AS_KNOWN_THEN vs RECONSTRUCTED_NOW (ZA-TIM-001 §12).

    Each side is answered as known at its record cutoff and echoes it (TIM-REQ-017), so a cutoff that has not been
    reached yet is refused (422 RECORD_SEQ_IN_FUTURE, as every other historical read): answering it with today's
    records would label a later answer with a sequence nothing was recorded at. An unknown definition is 404.

    Differences use the TIM §12 classes, each of which says what a later record did, so they are assigned from the
    earlier cutoff to the later one whichever side the caller put it on (the sides are returned as requested;
    LEGAL_VERSION_CHANGED runs from the earlier answer's rule version to the later one's). A fact is compared by what was
    known about it at each cutoff (its resolution there, as the evaluation freezes it: the assertion revision, or
    UNKNOWN), for every fact either answer's trace consulted: a fact that one answer's rule path skipped is not a change
    of knowledge. Read-only: no evaluation or fact conflict is recorded."""
    s = ctx.scoped(ctx.db.get(Subject, subject_id), "subject")
    od = ctx.db.get(ObligationDefinition, obligation_definition_id)
    if od is None:
        raise HTTPException(404, "obligation definition")
    now_seq = current_watermark(ctx.db)["record_seq"]
    if max(left_seq, right_seq) > now_seq:
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"left_seq/right_seq is beyond the current record sequence {now_seq}"})
    sides = {}
    with appl.integrity_fail_closed(ctx.db, actor=ctx.actor, tenant_id=ctx.tenant):
        for name, seq in (("left", left_seq), ("right", right_seq)):
            try:
                r = appl.evaluate(ctx.db, tenant_id=ctx.tenant, subject=s, odef=od, effective_at=valid_at,
                                  record_cutoff_seq=seq, trigger="RECONSTRUCTION", actor_id=ctx.actor.actor_id, persist=False)
                sides[name] = {"known_seq": seq, **r}
            except HTTPException as e:
                sides[name] = {"known_seq": seq, "error": e.detail}
    diffs = []
    if "rule_version_id" in sides["left"] and "rule_version_id" in sides["right"]:
        # TIM §12: "A later record introduced ..." / "Later record corrected ..." / "Later ... rule release changed ...".
        earlier, later = ("left", "right") if left_seq <= right_seq else ("right", "left")
        if sides[earlier]["rule_version_id"] != sides[later]["rule_version_id"]:
            diffs.append({"type": "LEGAL_VERSION_CHANGED", "from": sides[earlier]["rule_version_id"],
                          "to": sides[later]["rule_version_id"]})
        consulted = sorted(set(_fact_ids(sides["left"]["trace"])) | set(_fact_ids(sides["right"]["trace"])))
        known = {name: _known_facts(ctx.db, ctx.tenant, s, consulted, valid_at, seq)
                 for name, seq in (("left", left_seq), ("right", right_seq))}
        for k in consulted:
            if known["left"][k] != known["right"][k]:
                diffs.append({"type": "KNOWLEDGE_ADDED" if known[earlier][k] == "UNKNOWN" else "KNOWLEDGE_CORRECTED",
                              "fact": k, "left": known["left"][k], "right": known["right"][k]})
        if not diffs:
            diffs.append({"type": "NO_SEMANTIC_CHANGE"})
    return {"valid_at": valid_at, "left": sides["left"], "right": sides["right"], "differences": diffs}


def _known_facts(db, tenant_id: str, subject: Subject, keys: list[str], valid_at: datetime,
                 known_seq: int) -> dict[str, str]:
    """Fact key -> the assertion revision known for it at ``known_seq`` for ``valid_at``, or "UNKNOWN": the resolution
    an evaluation at that cutoff freezes (appl.evaluate fact_inputs / unknown_facts), never recording a conflict."""
    fds = {f.key: f for f in db.execute(select(FactDefinition).where(FactDefinition.key.in_(keys or ["-"]))).scalars()}
    out = {}
    for k in keys:
        r = resolve_fact(db, tenant_id=tenant_id, subject=subject, fd=fds[k], effective_at=valid_at,
                         known_seq=known_seq, create_conflicts=False) if k in fds else {"known": False}
        out[k] = r["revision_id"] if r["known"] else "UNKNOWN"
    return out


def _fact_ids(trace, out=None):
    out = {} if out is None else out
    if trace.get("node_type") == "FACT_REF":
        out[trace["fact"]] = trace.get("fact_revision_id") or "UNKNOWN"
    for c in trace.get("children", []) or []:
        _fact_ids(c, out)
    return out
