"""Executive exposure & readiness views (PRD §12, ZA-STA-001 §22 anti-misrepresentation rules)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ..engines import evidence as evd
from ..models import (ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot, ConnectorDefinition,
                      EvidenceArtifact, FactRequest, Issue, LegalEntity, ObligationDefinition, ObligationInstance,
                      OutboxEvent, Subject, TenantLedgerHead, TestRun)
from ..platform import current_watermark, ensure_utc
from ..security import DOSSIER_READ, Ctx, authorize

router = APIRouter()


def _open_issues(t: str) -> tuple:
    return Issue.tenant_id == t, Issue.state.notin_(["CLOSED", "CANCELLED"])


def _open_fact_requests(t: str) -> tuple:
    return FactRequest.tenant_id == t, FactRequest.status.in_(["OPEN", "IN_PROGRESS"])


@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    # Each table is read once for the whole view rather than once per entity, obligation or evidence artifact (which
    # grew with the tenant's data); every figure is computed as before.
    db, t = ctx.db, ctx.tenant
    now = datetime.now(timezone.utc)
    obls = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == t,
                                                       ObligationInstance.record_to_seq.is_(None))).scalars().all()
    latest = _latest_snapshots(db, t)
    uncertain = dict(db.execute(select(ApplicabilityEvaluation.entity_id, func.count()).where(
        ApplicabilityEvaluation.tenant_id == t, ApplicabilityEvaluation.outcome == "UNCERTAIN")
        .group_by(ApplicabilityEvaluation.entity_id)).all())
    ents = db.execute(select(LegalEntity).where(LegalEntity.tenant_id == t)).scalars().all()
    by_entity = []
    totals = {"PROVEN": 0, "NOT_PROVEN": 0, "FAILING": 0, "REQUIRES_REVIEW": 0, "NO_SNAPSHOT": 0}
    for e in ents:
        counts = {k: 0 for k in totals}
        adopted = [o for o in obls if o.entity_id == e.entity_id and o.lifecycle == "ADOPTED"]
        for o in adopted:
            s = latest.get(o.lineage_id)
            counts[s.state if s else "NO_SNAPSHOT"] += 1
        for k, v in counts.items():
            totals[k] += v
        by_entity.append({"entity_id": e.entity_id, "legal_name": e.legal_name, "country": e.country, "home_cell_id": e.home_cell_id,
                          "denominator": len(adopted), "assurance_counts": counts, "uncertain_evaluations": uncertain.get(e.entity_id, 0)})
    dets = db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == t,
                                                               ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()
    # The evidence figures need three columns, not whole artifact rows.
    arts = db.execute(select(EvidenceArtifact.evidence_id, EvidenceArtifact.valid_until, EvidenceArtifact.coverage_from)
                      .where(EvidenceArtifact.tenant_id == t)).all()
    lcs = evd.lifecycles(db, t, arts)
    ev_cond = {}
    for a in arts:
        c = evd.condition_from(a, lcs[a.evidence_id], now)
        ev_cond[c] = ev_cond.get(c, 0) + 1
    head = db.get(TenantLedgerHead, t)
    issues = db.execute(select(Issue).where(*_open_issues(t))).scalars().all()
    return {
        "as_of": {**current_watermark(db), "valid_at": now},
        "denominator_rule": "Counts cover ADOPTED applicable obligations for the tenant; UNCERTAIN applicability is reported separately, never silently excluded.",
        "assurance_totals": totals, "by_entity": by_entity,
        "determinations": {s: sum(1 for d in dets if d.state == s) for s in ("PROPOSED", "IN_REVIEW", "ADOPTED", "REJECTED")}
        | {"needs_review": sum(1 for d in dets if d.needs_review)},
        "open_fact_requests": db.query(FactRequest).filter(*_open_fact_requests(t)).count(),
        "evidence_conditions": ev_cond,
        "ledger": {"head_seq": head.current_seq if head else 0, "last_checkpointed_seq": head.last_checkpointed_seq if head else 0,
                   "last_verified_seq": head.last_verified_seq if head else 0},
        "open_issues": {"total": len(issues), "by_severity": {s: sum(1 for i in issues if i.severity == s)
                                                              for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}},
        "connectors": [{"name": c.name, "certification": c.certification, "release": c.release_availability,
                        "runtime_health": c.runtime_health} for c in db.execute(select(ConnectorDefinition)).scalars()],
        **_trends(db, t, arts, obls, latest),
        "language_note": "Assure reports evidential states against named requirement versions and periods — never an unqualified compliance claim.",
    }


@router.get("/dashboard/attention")
def attention(ctx: Ctx = Depends(authorize(*DOSSIER_READ))):
    """The two open-work counts the header bell shows on every page, counted as /dashboard counts them, without the
    rest of the dashboard aggregation."""
    db, t = ctx.db, ctx.tenant
    return {"open_issues": db.query(Issue).filter(*_open_issues(t)).count(),
            "open_fact_requests": db.query(FactRequest).filter(*_open_fact_requests(t)).count()}


def _latest_snapshots(db, t: str) -> dict[str, AssuranceSnapshot]:
    """Each obligation lineage's newest assurance snapshot, from one query."""
    latest: dict[str, AssuranceSnapshot] = {}
    for s in db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.tenant_id == t)
                        .order_by(AssuranceSnapshot.created_at.desc())).scalars():
        latest.setdefault(s.obligation_lineage_id, s)
    return latest


def _months(n: int) -> list[str]:
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def _trends(db, t: str, arts: list, obls: list[ObligationInstance], latest: dict[str, AssuranceSnapshot]) -> dict:
    """Chart series for the overview. Counts only — never a blended compliance score. Reuses the artifacts, current
    obligations and newest snapshots the dashboard already read, and reads only the columns the charts count."""
    months = _months(6)
    ev = {m: 0 for m in months}
    for a in arts:
        k = ensure_utc(a.coverage_from).strftime("%Y-%m") if a.coverage_from else None
        if k in ev:
            ev[k] += 1
    tr = {m: {"PASS": 0, "FAIL": 0, "OTHER": 0} for m in months}
    for occurred_at, result in db.execute(select(TestRun.occurred_at, TestRun.result).where(TestRun.tenant_id == t)):
        k = ensure_utc(occurred_at).strftime("%Y-%m")
        if k in tr:
            tr[k]["PASS" if result == "PASS" else "FAIL" if result in ("FAIL", "ERROR") else "OTHER"] += 1
    outcomes = {"APPLIES": 0, "DOES_NOT_APPLY": 0, "UNCERTAIN": 0}
    latest_outcome: dict[tuple, str] = {}
    for subject_id, odef_id, outcome in db.execute(select(
            ApplicabilityEvaluation.subject_id, ApplicabilityEvaluation.obligation_definition_id, ApplicabilityEvaluation.outcome)
            .where(ApplicabilityEvaluation.tenant_id == t).order_by(ApplicabilityEvaluation.record_seq)):
        latest_outcome[(subject_id, odef_id)] = outcome
    for outcome in latest_outcome.values():
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    defs = {d.obligation_definition_id: d for d in db.execute(select(ObligationDefinition).where(
        ObligationDefinition.obligation_definition_id.in_({o.obligation_definition_id for o in obls}))).scalars()}
    subjects = {s.subject_id: s for s in db.execute(select(Subject).where(Subject.subject_id.in_({o.subject_id for o in obls}))).scalars()}
    obligations = []
    for o in obls:
        od = defs[o.obligation_definition_id]
        s = latest.get(o.lineage_id)
        split = None
        if s:
            total = (ensure_utc(s.period_to) - ensure_utc(s.period_from)).total_seconds() or 1
            split = {}
            for seg in s.segments:
                a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in seg["segment_range"])
                split[seg["assurance_state"]] = split.get(seg["assurance_state"], 0) + round((b - a).total_seconds() / total * 100, 1)
        obligations.append({"lineage_id": o.lineage_id, "title": od.title, "key": od.key,
                            "subject": subjects[o.subject_id].display_name, "lifecycle": o.lifecycle,
                            "state": s.state if s else None, "period": [s.period_from, s.period_to] if s else None,
                            "period_split_pct": split})
    recent = [{"event_type": e.event_type, "aggregate_type": e.aggregate_type, "aggregate_id": e.aggregate_id,
               "record_seq": e.record_seq, "at": e.available_at}
              for e in db.execute(select(OutboxEvent).where(OutboxEvent.tenant_id == t)
                                  .order_by(OutboxEvent.record_seq.desc()).limit(8)).scalars()]
    return {"evidence_by_coverage_month": [{"month": m, "count": ev[m]} for m in months],
            "test_runs_by_month": [{"month": m, **tr[m]} for m in months],
            "applicability_outcomes": outcomes, "obligations": obligations, "recent_events": recent}
