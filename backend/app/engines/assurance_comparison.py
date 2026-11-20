"""Historical assurance comparison: one obligation and one valid period, as known at two record cutoffs or as frozen in
two snapshots.

Governing text: ZA-TIM-001 §12 ("What changed between those answers? Same valid time; compare two record cutoffs";
the historical compare response: valid_period, left / right known_at + snapshot_id), §16 ("POST /historical-comparisons:
Compares same valid period across two record cutoffs/snapshots"), §11 (every joined record filtered on both axes with
the same record cutoff; correction join), TIM-REQ-017 (echo the resolved watermark), TIM-REQ-019 (never answer a cutoff
beyond the data); ZA-STA-001 §20 WF-STA-01 History ("Prior snapshots and compare-current-reconstruction action"), §21
WF-STA-05 ("Audit compare: Compare AS_KNOWN_THEN vs RECONSTRUCTED_NOW for same period"), STA-REQ-019 (historical
snapshots are immutable); WF-TIM-02 (left / right knowledge state, dependency diff with exact revision references,
outcome diff with deterministic reasons).

What this computes, and only this:
  * each side exactly as that knowledge state answers: a frozen AssuranceSnapshot as stored (never recomputed or
    rewritten), or a reconstruction at an explicit record cutoff (asr.compute_snapshot, persist=False, which reads every
    dependency as recorded by that cutoff), with its watermark echoed;
  * the outcome difference: period state, reason codes, roll-up engine version and the segment timeline aligned on
    both sides' boundaries (half-open intervals). Each side's dependency_digest is shown, not compared: it includes the
    record cutoff of every control evaluation it digests, so two cutoffs always differ there;
  * the dependency difference: exact revision references added to or no longer in the result, per dependency class of
    the snapshot manifest, each with the record sequence at which it was first recorded; the control test policy
    version each side was derived under (the evaluator's own rule: the highest version recorded by the cutoff); and,
    for records kept by both sides, the evidence lifecycle, evidence-conflict state and governance states that differ
    between the two cutoffs.
    These are recorded facts, so "evidence erased" (lifecycle ERASED, still referenced) is told apart from "evidence no
    longer part of the result" (not referenced by the later side, lifecycle unchanged).

What it does not compute: the ZA-TIM-001 §12 difference classes (KNOWLEDGE_ADDED, KNOWLEDGE_CORRECTED,
LEGAL_VERSION_CHANGED, SOURCE_CONFLICT_RESOLVED, EVIDENCE_ADDED_OR_ERASED, NO_SEMANTIC_CHANGE). TIM-REQ-027 requires
them, but no controlled document maps assurance dependency changes (test runs, control evaluations, test policies,
satisfaction expressions, obligation revisions, evidence lifecycle, exceptions, compensations, occurrences) onto them,
nor says how several classes combine or which takes precedence. ``differences`` is therefore None, never an empty list
(which would read as "no difference"). Decision owners: ZA-TIM-001 Data Architect with the Head of Assurance.

Exposed as POST /obligations/{lineage}/historical-comparisons (routers/assurance.py): TIM §16's resource under the
obligation it compares, with the period and side representation of the obligation's other assurance requests and the
authorization of its read-only preview. ZA-API-001 lists no comparison endpoint, so the contract awaits the API /
Platform Engineering Lead's review, and read policy stays provisional until G-08 (IAM Lead).

Read-only: nothing is stored, no snapshot is persisted, no issue is raised. The caller rolls its transaction back."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import CELL_ID
from ..models import (AssuranceSnapshot, Compensation, ControlImplementation, ControlTestPolicy, EvidenceArtifact,
                      ExceptionRecord, ObligationInstance, ObligationOccurrence, SatisfactionExpressionVersion, TestRun)
from ..platform import current_watermark, ensure_utc
from . import assurance as asr

UNCLASSIFIED = ("ZA-TIM-001 §12 difference classes are not assigned to assurance comparisons: no controlled document "
                "maps assurance dependency changes onto them (TIM-REQ-027). Decision owners: ZA-TIM-001 Data Architect "
                "and Head of Assurance.")

# The record sequence at which each referenced record of a manifest class was first recorded.
_RECORDED = {
    "obligation_revisions": (ObligationInstance, ObligationInstance.revision_id, ObligationInstance.record_from_seq),
    "expression_versions": (SatisfactionExpressionVersion, SatisfactionExpressionVersion.expression_version_id,
                            SatisfactionExpressionVersion.record_seq),
    "control_implementations": (ControlImplementation, ControlImplementation.control_implementation_id,
                                ControlImplementation.record_seq),
    "test_runs": (TestRun, TestRun.test_run_id, TestRun.record_seq),
    "evidence": (EvidenceArtifact, EvidenceArtifact.evidence_id, EvidenceArtifact.record_seq),
    "compensations": (Compensation, Compensation.compensation_id, Compensation.record_seq),
    "exceptions": (ExceptionRecord, ExceptionRecord.exception_id, ExceptionRecord.record_seq),
    "occurrences": (ObligationOccurrence, ObligationOccurrence.occurrence_id, ObligationOccurrence.record_seq),
    "control_test_policies": (ControlTestPolicy, ControlTestPolicy.policy_version_id, ControlTestPolicy.record_seq),
}


def compare(db: Session, *, tenant_id: str, obligation_lineage_id: str, period_from: datetime, period_to: datetime,
            left: dict, right: dict, actor_id: str, evaluated_at: datetime | None = None) -> dict:
    """Compare the obligation's assurance over [period_from, period_to) between two knowledge states. A side is
    {"snapshot_id": id} (a frozen snapshot of this obligation and exactly this period) or {"record_seq": n} (a
    reconstruction as known at that cutoff, which must not exceed the current watermark). ``evaluated_at`` is the one
    instant both reconstructions decide occurrence due times at (default: now; echoed), so they differ only in what
    was recorded; a snapshot keeps its own (its created_at)."""
    period_from, period_to = ensure_utc(period_from), ensure_utc(period_to)
    if period_to <= period_from:
        raise HTTPException(422, "Assurance period must be a non-empty half-open interval")
    if db.execute(select(ObligationInstance.revision_id).where(
            ObligationInstance.tenant_id == tenant_id,
            ObligationInstance.lineage_id == obligation_lineage_id).limit(1)).first() is None:
        raise HTTPException(404, "obligation not found")  # another organisation's obligation reads as unknown
    now_seq = current_watermark(db)["record_seq"]
    instant = ensure_utc(evaluated_at) if evaluated_at is not None else datetime.now(timezone.utc)
    sides = {name: _side(db, tenant_id, obligation_lineage_id, period_from, period_to, spec, now_seq, instant, actor_id)
             for name, spec in (("left", left), ("right", right))}
    _policies(db, tenant_id, sides)
    return {"valid_period": {"kind": "INSTANT_RANGE", "from": period_from, "until_exclusive": period_to},
            "obligation_lineage_id": obligation_lineage_id, "evaluation_instant": instant,
            "left": _public(sides["left"]), "right": _public(sides["right"]),
            "outcome": _outcome(sides["left"], sides["right"], period_from, period_to),
            "dependencies": _dependencies(db, tenant_id, sides["left"], sides["right"]),
            "differences": None, "differences_note": UNCLASSIFIED}


def _side(db, tenant_id, lineage, period_from, period_to, spec, now_seq, instant, actor_id) -> dict:
    if not isinstance(spec, dict) or len(spec) != 1 or not ({"snapshot_id", "record_seq"} & set(spec)):
        raise HTTPException(422, "A comparison side is exactly one of snapshot_id or record_seq")
    if "snapshot_id" in spec:
        snap = db.get(AssuranceSnapshot, spec["snapshot_id"]) if isinstance(spec["snapshot_id"], str) else None
        if snap is None or snap.tenant_id != tenant_id:
            raise HTTPException(404, "snapshot not found")
        if snap.obligation_lineage_id != lineage:
            raise HTTPException(422, "The snapshot is of another obligation")
        if (ensure_utc(snap.period_from), ensure_utc(snap.period_to)) != (period_from, period_to):
            raise HTTPException(422, "The snapshot covers another period: both sides compare the same valid period "
                                     "(ZA-TIM-001 §12)")
        return {"known_at": {"cell_id": CELL_ID, "record_seq": snap.record_cutoff_seq}, "snapshot_id": snap.snapshot_id,
                "lifecycle": snap.lifecycle, "perspective": snap.perspective, "state": snap.state,
                "reason_codes": list(snap.reason_codes or []), "obligation_revision_id": snap.obligation_revision_id,
                "rollup_engine_version": snap.rollup_engine_version, "evaluation_instant": snap.created_at,
                "dependency_digest": snap.dependency_digest, "segments": snap.segments or [],
                "exception_treatments": snap.exception_treatments or [],
                "_manifest": dict(snap.dependency_manifest or {})}
    seq = spec["record_seq"]
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise HTTPException(422, "record_seq must be a non-negative integer")
    if seq > now_seq:  # TIM-REQ-019: never an answer for knowledge that does not exist yet
        raise HTTPException(422, {"error": "RECORD_SEQ_IN_FUTURE",
                                  "message": f"record_seq is beyond the current record sequence {now_seq}"})
    known_at = {"cell_id": CELL_ID, "record_seq": seq}
    perspective = "RECONSTRUCTED_NOW" if seq == now_seq else "AS_KNOWN_THEN"
    try:
        r = asr.compute_snapshot(db, tenant_id=tenant_id, obligation_lineage_id=lineage, period_from=period_from,
                                 period_to=period_to, record_cutoff_seq=seq, perspective=perspective,
                                 actor_id=actor_id, persist=False, evaluated_at=instant)
    except HTTPException as e:  # e.g. the obligation not yet known at this cutoff: an answer for this side, not a failure
        return {"known_at": known_at, "snapshot_id": None, "perspective": perspective, "error": e.detail,
                "segments": [], "_manifest": {}}
    return {"known_at": known_at, "snapshot_id": None, "perspective": perspective, "state": r["state"],
            "reason_codes": list(r["reason_codes"]), "obligation_revision_id": r["obligation_revision_id"],
            "rollup_engine_version": r["rollup_engine_version"], "evaluation_instant": instant,
            "dependency_digest": r["dependency_digest"], "segments": r["segments"],
            "exception_treatments": r["exception_treatments"], "history_problems": r["history_problems"],
            "_manifest": dict(r["dependency_manifest"])}


def _policies(db, tenant_id, sides) -> None:
    """The control test policy version each side derived each control under: the highest version recorded by its
    cutoff (asr.evaluate_control's rule), for the control implementations in its manifest."""
    impls = sorted({i for s in sides.values() for i in s["_manifest"].get("control_implementations", [])})
    rows = db.execute(select(ControlTestPolicy).where(ControlTestPolicy.tenant_id == tenant_id,
                                                      ControlTestPolicy.control_implementation_id.in_(impls or ["-"]))
                      ).scalars().all()
    for s in sides.values():
        cutoff, chosen = s["known_at"]["record_seq"], {}
        for ci in s["_manifest"].get("control_implementations", []):
            known = sorted((p for p in rows if p.control_implementation_id == ci and p.record_seq <= cutoff),
                           key=lambda p: (-p.version, p.policy_version_id))
            if known:
                chosen[ci] = known[0].policy_version_id
        s["control_test_policies"] = chosen
        if "error" not in s:
            s["_manifest"]["control_test_policies"] = sorted(chosen.values())


def _public(side: dict) -> dict:
    return {k: v for k, v in side.items() if not k.startswith("_")}


def _instants(segment) -> tuple[datetime, datetime]:
    a, b = segment["segment_range"]
    return tuple(ensure_utc(datetime.fromisoformat(x.replace("Z", "+00:00")) if isinstance(x, str) else x)
                 for x in (a, b))


def _state_at(segments, a, b):
    for s in segments:
        f, t = _instants(s)
        if f <= a and b <= t:
            return s.get("assurance_state")
    return None


def _outcome(left, right, period_from, period_to) -> dict:
    """Result difference only: what each side concluded, where on the timeline the conclusions differ, and the
    reason codes that appear or no longer appear. No cause is attributed."""
    bounds = {period_from, period_to}
    for side in (left, right):
        for s in side["segments"]:
            bounds |= {x for x in _instants(s) if period_from < x < period_to}
    points = sorted(bounds)
    timeline = []
    for a, b in zip(points, points[1:]):
        pair = (_state_at(left["segments"], a, b), _state_at(right["segments"], a, b))
        if timeline and timeline[-1]["left"] == pair[0] and timeline[-1]["right"] == pair[1]:
            timeline[-1]["until_exclusive"] = b
        else:
            timeline.append({"from": a, "until_exclusive": b, "left": pair[0], "right": pair[1]})
    for t in timeline:
        t["changed"] = t["left"] != t["right"]
    lr, rr = set(left.get("reason_codes") or []), set(right.get("reason_codes") or [])
    return {"state": {"left": left.get("state"), "right": right.get("state"),
                      "changed": left.get("state") != right.get("state")},
            "reason_codes": {"added": sorted(rr - lr), "removed": sorted(lr - rr)},
            "rollup_engine_version": {"left": left.get("rollup_engine_version"),
                                      "right": right.get("rollup_engine_version"),
                                      "equal": left.get("rollup_engine_version") == right.get("rollup_engine_version")},
            "timeline": timeline}


def _dependencies(db, tenant_id, left, right) -> dict:
    """Exact references per dependency class: added (only the right side's result rests on it), removed (only the
    left side's does), and how many both share; each added or removed reference with the record sequence at which it
    was first recorded. Then the facts that changed between the two cutoffs: whether a referenced artifact was in an
    open evidence conflict; for records both sides reference, an evidence artifact's lifecycle, an exception's or
    compensation's state, an occurrence's fulfilment or dispute."""
    lm, rm = left["_manifest"], right["_manifest"]
    classes, placed = {}, {}
    # control_evaluation_digests are left out: each digest includes the record cutoff it was derived at
    # (asr.evaluate_control), so they differ between any two cutoffs even when no recorded dependency changed. The
    # records they digest (implementations, test policies, test runs, evidence) are compared here instead. For the same
    # reason the sides' own dependency_digest values are shown but not compared.
    for cls in sorted((set(lm) | set(rm)) - {"control_evaluation_digests"}):
        lset, rset = set(lm.get(cls) or []), set(rm.get(cls) or [])
        classes[cls] = {"added": sorted(rset - lset), "removed": sorted(lset - rset), "unchanged": len(lset & rset)}
        if cls in _RECORDED and (lset ^ rset):
            model, key, seq = _RECORDED[cls]
            placed[cls] = dict(db.execute(select(key, seq).where(model.tenant_id == tenant_id,
                                                                 key.in_(sorted(lset ^ rset)))).all())
    for cls, d in classes.items():
        if cls in placed:
            for change in ("added", "removed"):
                d[change] = [{"id": i, "first_recorded_seq": placed[cls].get(i)} for i in d[change]]
    lcut, rcut = left["known_at"]["record_seq"], right["known_at"]["record_seq"]
    shared = lambda cls: sorted(set(lm.get(cls) or []) & set(rm.get(cls) or []))  # noqa: E731
    changes = {}
    referenced = sorted(set(lm.get("evidence") or []) | set(rm.get("evidence") or []))
    lk = asr._EvidenceKnowledge(db, tenant_id, lcut) if referenced else None
    rk = asr._EvidenceKnowledge(db, tenant_id, rcut) if referenced else None
    if referenced:
        # Whether each referenced artifact was in an OPEN evidence conflict at each cutoff (or its conflict history
        # cannot be placed there: EVIDENCE_HISTORY_UNRESOLVED), from evidence_conflict_event as recorded by then.
        (lo, lu), (ro, ru) = lk.conflicts(), rk.conflicts()

        def in_conflict(open_ids, unknown, i):
            return "OPEN" if i in open_ids else asr.EVIDENCE_HISTORY_UNRESOLVED if i in unknown else None
        changes["evidence_conflicts"] = [{"id": i, "left": in_conflict(lo, lu, i), "right": in_conflict(ro, ru, i)}
                                         for i in referenced if in_conflict(lo, lu, i) != in_conflict(ro, ru, i)]
    ids = shared("evidence")
    if ids:
        arts = {a.evidence_id: a for a in db.execute(select(EvidenceArtifact).where(
            EvidenceArtifact.tenant_id == tenant_id, EvidenceArtifact.evidence_id.in_(ids))).scalars()}

        def lifecycle(knowledge, art):
            # The lifecycle known at that cutoff, or the reason it cannot be placed there (EVIDENCE_HISTORY_UNRESOLVED).
            state, problem = knowledge.lifecycle(art)
            return state if problem is None else problem
        changes["evidence_lifecycle"] = [
            {"id": i, "left": lifecycle(lk, arts[i]), "right": lifecycle(rk, arts[i])} for i in ids if i in arts
            if lifecycle(lk, arts[i]) != lifecycle(rk, arts[i])]
    for cls, model, key, fn, fields in (
            ("exceptions", ExceptionRecord, ExceptionRecord.exception_id, asr.exception_states_at, ("state", "problem")),
            ("compensations", Compensation, Compensation.compensation_id, asr.compensation_states_at,
             ("state", "problem")),
            ("occurrences", ObligationOccurrence, ObligationOccurrence.occurrence_id, asr.occurrence_states_at,
             ("fulfilled_at", "disputed", "problem"))):
        ids = shared(cls)
        if not ids:
            continue
        rows = db.execute(select(model).where(model.tenant_id == tenant_id, key.in_(ids))
                          .order_by(model.record_seq, key)).scalars().all()
        at_l, at_r = fn(db, tenant_id, rows, lcut), fn(db, tenant_id, rows, rcut)
        diff = []
        for i in ids:
            lv = {f: at_l[i][f] for f in fields} if i in at_l else None
            rv = {f: at_r[i][f] for f in fields} if i in at_r else None
            if lv != rv:
                diff.append({"id": i, "left": lv, "right": rv})
        if diff:
            changes[f"{cls[:-1]}_states"] = diff
    return {"classes": classes, "changes_between_cutoffs": {k: v for k, v in changes.items() if v}}
