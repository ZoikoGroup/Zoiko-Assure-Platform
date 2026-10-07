"""Executive exposure & readiness views (PRD §12, ZA-STA-001 §22 anti-misrepresentation rules)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..engines import evidence as evd
from ..models import (ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot, ConnectorDefinition,
                      EvidenceArtifact, FactRequest, Issue, LegalEntity, ObligationDefinition, ObligationInstance,
                      OutboxEvent, Subject, TenantLedgerHead, TestRun)
from ..platform import current_watermark, ensure_utc
from ..security import Ctx, get_ctx

router = APIRouter()


@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(get_ctx)):
    db, t = ctx.db, ctx.tenant
    ents = db.execute(select(LegalEntity).where(LegalEntity.tenant_id == t)).scalars().all()
    by_entity = []
    totals = {"PROVEN": 0, "NOT_PROVEN": 0, "FAILING": 0, "REQUIRES_REVIEW": 0, "NO_SNAPSHOT": 0}
    for e in ents:
        counts = {k: 0 for k in totals}
        obls = db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == t, ObligationInstance.entity_id == e.entity_id,
                                                           ObligationInstance.lifecycle == "ADOPTED",
                                                           ObligationInstance.record_to_seq.is_(None))).scalars().all()
        for o in obls:
            s = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.obligation_lineage_id == o.lineage_id)
                           .order_by(AssuranceSnapshot.created_at.desc())).scalars().first()
            counts[s.state if s else "NO_SNAPSHOT"] += 1
        for k, v in counts.items():
            totals[k] += v
        uncertain = db.query(ApplicabilityEvaluation).filter(ApplicabilityEvaluation.tenant_id == t,
                                                             ApplicabilityEvaluation.entity_id == e.entity_id,
                                                             ApplicabilityEvaluation.outcome == "UNCERTAIN").count()
        by_entity.append({"entity_id": e.entity_id, "legal_name": e.legal_name, "country": e.country, "home_cell_id": e.home_cell_id,
                          "denominator": len(obls), "assurance_counts": counts, "uncertain_evaluations": uncertain})
    dets = db.execute(select(ApplicabilityDetermination).where(ApplicabilityDetermination.tenant_id == t,
                                                               ApplicabilityDetermination.record_to_seq.is_(None))).scalars().all()
    ev_cond = {}
    for a in db.execute(select(EvidenceArtifact).where(EvidenceArtifact.tenant_id == t)).scalars():
        c = evd.condition(db, a)
        ev_cond[c] = ev_cond.get(c, 0) + 1
    head = db.get(TenantLedgerHead, t)
    issues = db.execute(select(Issue).where(Issue.tenant_id == t, Issue.state.notin_(["CLOSED", "CANCELLED"]))).scalars().all()
    return {
        "as_of": {**current_watermark(db), "valid_at": datetime.now(timezone.utc)},
        "denominator_rule": "Counts cover ADOPTED applicable obligations for the tenant; UNCERTAIN applicability is reported separately, never silently excluded.",
        "assurance_totals": totals, "by_entity": by_entity,
        "determinations": {s: sum(1 for d in dets if d.state == s) for s in ("PROPOSED", "IN_REVIEW", "ADOPTED", "REJECTED")}
        | {"needs_review": sum(1 for d in dets if d.needs_review)},
        "open_fact_requests": db.query(FactRequest).filter(FactRequest.tenant_id == t, FactRequest.status.in_(["OPEN", "IN_PROGRESS"])).count(),
        "evidence_conditions": ev_cond,
        "ledger": {"head_seq": head.current_seq if head else 0, "last_checkpointed_seq": head.last_checkpointed_seq if head else 0,
                   "last_verified_seq": head.last_verified_seq if head else 0},
        "open_issues": {"total": len(issues), "by_severity": {s: sum(1 for i in issues if i.severity == s)
                                                              for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}},
        "connectors": [{"name": c.name, "certification": c.certification, "release": c.release_availability,
                        "runtime_health": c.runtime_health} for c in db.execute(select(ConnectorDefinition)).scalars()],
        **_trends(db, t),
        "language_note": "Assure reports evidential states against named requirement versions and periods — never an unqualified compliance claim.",
    }


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


def _trends(db, t: str) -> dict:
    """Chart series for the overview. Counts only — never a blended compliance score."""
    months = _months(6)
    ev = {m: 0 for m in months}
    for a in db.execute(select(EvidenceArtifact).where(EvidenceArtifact.tenant_id == t)).scalars():
        k = ensure_utc(a.coverage_from).strftime("%Y-%m") if a.coverage_from else None
        if k in ev:
            ev[k] += 1
    tr = {m: {"PASS": 0, "FAIL": 0, "OTHER": 0} for m in months}
    for r in db.execute(select(TestRun).where(TestRun.tenant_id == t)).scalars():
        k = ensure_utc(r.occurred_at).strftime("%Y-%m")
        if k in tr:
            tr[k]["PASS" if r.result == "PASS" else "FAIL" if r.result in ("FAIL", "ERROR") else "OTHER"] += 1
    outcomes = {"APPLIES": 0, "DOES_NOT_APPLY": 0, "UNCERTAIN": 0}
    latest: dict[tuple, ApplicabilityEvaluation] = {}
    for e in db.execute(select(ApplicabilityEvaluation).where(ApplicabilityEvaluation.tenant_id == t)
                        .order_by(ApplicabilityEvaluation.record_seq)).scalars():
        latest[(e.subject_id, e.obligation_definition_id)] = e
    for e in latest.values():
        outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1
    obligations = []
    for o in db.execute(select(ObligationInstance).where(ObligationInstance.tenant_id == t,
                                                         ObligationInstance.record_to_seq.is_(None))).scalars():
        od = db.get(ObligationDefinition, o.obligation_definition_id)
        s = db.execute(select(AssuranceSnapshot).where(AssuranceSnapshot.obligation_lineage_id == o.lineage_id)
                       .order_by(AssuranceSnapshot.created_at.desc())).scalars().first()
        split = None
        if s:
            total = (ensure_utc(s.period_to) - ensure_utc(s.period_from)).total_seconds() or 1
            split = {}
            for seg in s.segments:
                a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in seg["segment_range"])
                split[seg["assurance_state"]] = split.get(seg["assurance_state"], 0) + round((b - a).total_seconds() / total * 100, 1)
        obligations.append({"lineage_id": o.lineage_id, "title": od.title, "key": od.key,
                            "subject": db.get(Subject, o.subject_id).display_name, "lifecycle": o.lifecycle,
                            "state": s.state if s else None, "period": [s.period_from, s.period_to] if s else None,
                            "period_split_pct": split})
    recent = [{"event_type": e.event_type, "aggregate_type": e.aggregate_type, "aggregate_id": e.aggregate_id,
               "record_seq": e.record_seq, "at": e.available_at}
              for e in db.execute(select(OutboxEvent).where(OutboxEvent.tenant_id == t)
                                  .order_by(OutboxEvent.record_seq.desc()).limit(8)).scalars()]
    return {"evidence_by_coverage_month": [{"month": m, "count": ev[m]} for m in months],
            "test_runs_by_month": [{"month": m, **tr[m]} for m in months],
            "applicability_outcomes": outcomes, "obligations": obligations, "recent_events": recent}
