"""Customer correction notices through the content-release mechanism (PRD-001 §3.1, FR-WRK-003; GLS-001 "Correction
Notice"; ZA-RUL-001 change class CORRECTION; ZA-TIM-001 WF-TIM-03; ZA-API-001 §17; WRK-REQ-043).

What the controlled documents fix, and how this module follows them:

  * A correction notice is "Zoiko-issued notice that released regulatory content or logic was corrected and identifies
    affected release(s). Must trigger scoped impact analysis" (GLS-001). Affected tenants receive it "through the content
    release mechanism" (PRD-001 §12). The notice is therefore the signed CORRECTION ContentRelease itself: its signed
    manifest names the corrected release, the corrected rule version and the correction reason (RUL WF-RUL-03:
    "Correction classification requires explicit correction reason/notice plan"). Nothing about the corrected release is
    changed (WRK-REQ-043; published catalogue rows are database-immutable, migration 0004).
  * The tenant-specific impact report is ``GET /content-releases/{id}/impact`` (API-001 §17: "Tenant-authorized impact
    summary after release/shadow processing. No other-tenant aggregate detail."). It is computed here, read-only, from
    the organisation's own records as known at an explicit record sequence, so a historical report can be reproduced.
  * It reports and never acts: no obligation, determination, control or assurance state is changed because a correction
    exists. Re-evaluation stays with the existing release impact re-evaluation; a changed outcome on an ADOPTED
    determination is flagged for review there (ZA-RUL-001 §: comparison → DeterminationReviewNeeded), not here.

Impact classes (WF-TIM-03 impact counts: evaluations, determinations, obligations, snapshots, audit packages):
  CONFIRMED  records that rest on the corrected content: applicability evaluations made with a corrected rule version,
             the determinations built on those evaluations and the obligation instances built on those determinations.
  POTENTIAL  records that depend on a confirmed record without referencing the corrected content themselves: assurance
             snapshots of an affected obligation and signed packages that contain such a snapshot; and any subject whose
             re-evaluation under the correcting release is not recorded yet (whether its outcome changes is unknown).
The report never says an organisation is unaffected: with no recorded dependency it says NONE_RECORDED, and when the
release's dependency information is missing or incomplete it says INCOMPLETE and lists why.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import (ApplicabilityDetermination, ApplicabilityEvaluation, AssuranceSnapshot, ContentRelease,
                      EvidencePackage, ObligationDefinition, ObligationInstance, RuleVersion)


def shadow_summary_for(caller_tenant_id: str | None, summary: dict | None) -> dict | None:
    """A release's ``shadow_summary`` or a draft's ``shadow_report``, as one caller may see it.

    Both are the content-operations shadow run (rules.shadow -> applicability.shadow_impact, under system scope): counts
    over every organisation's subjects (subjects evaluated, outcome transition buckets, missing facts, adopted
    determination conflicts). An organisation reader's impact view is "Tenant-authorized ... No other-tenant aggregate
    detail" (API-001 §17), and shadow impact belongs to content operations "aggregate/minimized" (API-001 privacy
    constraints), so an organisation principal never receives it. The field stays in the response, null, so the
    response shape does not change; platform principals (no tenant) keep it."""
    return None if caller_tenant_id else summary


def rule_version_ids(release: ContentRelease) -> list[str]:
    return [x["rule_version_id"] for x in (release.manifest or {}).get("rule_versions") or []
            if isinstance(x, dict) and x.get("rule_version_id")]


def corrected_by(db, release: ContentRelease) -> list[str]:
    """Releases published as corrections of ``release``, in record order."""
    return list(db.execute(select(ContentRelease.content_release_id)
                           .where(ContentRelease.correction_of_release_id == release.content_release_id)
                           .order_by(ContentRelease.cell_received_seq, ContentRelease.content_release_id)).scalars())


def correction_flags(db, release: ContentRelease) -> dict:
    """API-001 §17 "correction flags" for a release, plus the signed correction statement when it is a correction."""
    m = release.manifest or {}
    out = {"is_correction": release.correction_of_release_id is not None,
           "corrects_release_id": release.correction_of_release_id,
           "corrected_by_release_ids": corrected_by(db, release)}
    if release.correction_of_release_id is not None:
        c = m.get("correction") if isinstance(m.get("correction"), dict) else {}
        # A correction published before the reason was part of the signed manifest has none: say so, never invent one.
        out["correction"] = {"reason": c.get("reason"), "reason_recorded": bool(c.get("reason")),
                             "corrects_rule_version_id": c.get("corrects_rule_version_id") or m.get("supersedes"),
                             "manifest_hash": release.manifest_hash, "signer_key_id": release.signer_key_id}
    return out


def release_scope(db, release: ContentRelease) -> tuple[dict, list[dict]]:
    """The global content a release carries (rule versions, obligation definitions, requirement versions and their
    source snapshots), and the problems that make its dependency information incomplete."""
    problems: list[dict] = []
    m = release.manifest or {}
    rv_ids = rule_version_ids(release)
    if not rv_ids:
        problems.append({"code": "RELEASE_HAS_NO_RULE_VERSIONS", "content_release_id": release.content_release_id})
    odefs: dict[str, str | None] = {}
    for rid in rv_ids:
        rv = db.get(RuleVersion, rid)
        if rv is None:
            problems.append({"code": "RULE_VERSION_MISSING", "rule_version_id": rid})
            continue
        od = db.get(ObligationDefinition, rv.obligation_definition_id)
        odefs[rv.obligation_definition_id] = od.key if od else None
    bound = m.get("requirement_versions")
    if not isinstance(bound, list):
        problems.append({"code": "REQUIREMENT_BINDING_NOT_RECORDED", "content_release_id": release.content_release_id})
        bound = []
    reqs = [{k: b.get(k) for k in ("requirement_version_id", "requirement_key", "source_id", "source_fingerprint",
                                   "locator", "provision_ids")} for b in bound if isinstance(b, dict)]
    return ({"rule_version_ids": rv_ids,
             "obligation_definitions": [{"obligation_definition_id": k, "key": v} for k, v in sorted(odefs.items())],
             "requirement_versions": reqs}, problems)


def _prior_rule_versions(db, release: ContentRelease, problems: list[dict]) -> list[str]:
    """The rule versions whose content this release replaces: for a correction, the corrected release's; otherwise the
    rule version the release supersedes."""
    if release.correction_of_release_id is not None:
        prior = db.get(ContentRelease, release.correction_of_release_id)
        if prior is None:
            problems.append({"code": "CORRECTED_RELEASE_MISSING", "content_release_id": release.correction_of_release_id})
            return []
        ids = rule_version_ids(prior)
        if not ids:
            problems.append({"code": "CORRECTED_RELEASE_HAS_NO_RULE_VERSIONS",
                             "content_release_id": prior.content_release_id})
        return ids
    if (release.manifest or {}).get("change_class") == "CORRECTION":
        problems.append({"code": "CORRECTION_TARGET_MISSING", "content_release_id": release.content_release_id})
        return []
    s = (release.manifest or {}).get("supersedes")
    return [s] if s else []


def _visible(model, seq: int):
    """Revisions known at record sequence ``seq`` (TIM: as known then)."""
    return (model.record_from_seq <= seq) & ((model.record_to_seq.is_(None)) | (model.record_to_seq > seq))


def tenant_impact(db, *, tenant_id: str, release: ContentRelease, as_of_seq: int) -> dict:
    """The organisation's impact report for ``release`` as known at record sequence ``as_of_seq``. Read-only."""
    scope, problems = release_scope(db, release)
    prior_ids = _prior_rule_versions(db, release, problems)
    new_ids = scope["rule_version_ids"]

    def evaluations(ids):
        if not ids:
            return []
        return db.execute(select(ApplicabilityEvaluation).where(
            ApplicabilityEvaluation.tenant_id == tenant_id, ApplicabilityEvaluation.rule_version_id.in_(ids),
            ApplicabilityEvaluation.record_seq <= as_of_seq)
            .order_by(ApplicabilityEvaluation.record_seq, ApplicabilityEvaluation.evaluation_id)).scalars().all()

    prior_evs, new_evs = evaluations(prior_ids), evaluations(new_ids)
    latest_new: dict[tuple, ApplicabilityEvaluation] = {}
    for e in new_evs:
        latest_new[(e.subject_id, e.obligation_definition_id)] = e
    latest_prior: dict[tuple, ApplicabilityEvaluation] = {}
    for e in prior_evs:
        latest_prior[(e.subject_id, e.obligation_definition_id)] = e

    subjects = []
    for (sid, odef_id), pe in sorted(latest_prior.items()):
        ne = latest_new.get((sid, odef_id))
        subjects.append({"subject_id": sid, "entity_id": pe.entity_id, "obligation_definition_id": odef_id,
                         "prior_evaluation_id": pe.evaluation_id, "prior_outcome": pe.outcome,
                         "corrected_evaluation_id": ne.evaluation_id if ne else None,
                         "corrected_outcome": ne.outcome if ne else None,
                         "outcome_changed": None if ne is None else ne.outcome != pe.outcome})
    pending = [s for s in subjects if s["corrected_evaluation_id"] is None]
    if pending:
        problems.append({"code": "RE_EVALUATION_NOT_RECORDED", "subjects": len(pending)})

    prior_eval_ids = {e.evaluation_id for e in prior_evs}
    det_rows = db.execute(select(ApplicabilityDetermination).where(
        ApplicabilityDetermination.tenant_id == tenant_id, ApplicabilityDetermination.record_from_seq <= as_of_seq)
        .order_by(ApplicabilityDetermination.record_from_seq, ApplicabilityDetermination.revision_id)).scalars().all()
    lineages = {d.lineage_id for d in det_rows if d.evaluation_id in prior_eval_ids}
    det_revision_ids = {d.revision_id for d in det_rows if d.lineage_id in lineages}
    corrected_outcome = {(s["subject_id"], s["obligation_definition_id"]): s["corrected_outcome"] for s in subjects}
    determinations = []
    for d in db.execute(select(ApplicabilityDetermination).where(
            ApplicabilityDetermination.tenant_id == tenant_id, ApplicabilityDetermination.lineage_id.in_(lineages or ["-"]),
            _visible(ApplicabilityDetermination, as_of_seq))
            .order_by(ApplicabilityDetermination.lineage_id)).scalars():
        new_outcome = corrected_outcome.get((d.subject_id, d.obligation_definition_id))
        determinations.append({
            "lineage_id": d.lineage_id, "revision_id": d.revision_id, "subject_id": d.subject_id,
            "obligation_definition_id": d.obligation_definition_id, "evaluation_id": d.evaluation_id,
            "state": d.state, "outcome": d.outcome, "needs_review": d.needs_review,
            # WF-TIM-03 materiality: an adopted conclusion the correction would change. None = not yet known.
            "material_change": (None if new_outcome is None else (d.state == "ADOPTED" and new_outcome != d.outcome))})

    obligations = [{"lineage_id": o.lineage_id, "revision_id": o.revision_id, "subject_id": o.subject_id,
                    "entity_id": o.entity_id, "obligation_definition_id": o.obligation_definition_id,
                    "lifecycle": o.lifecycle, "determination_revision_id": o.determination_revision_id}
                   for o in db.execute(select(ObligationInstance).where(
                       ObligationInstance.tenant_id == tenant_id,
                       ObligationInstance.determination_revision_id.in_(det_revision_ids or ["-"]),
                       _visible(ObligationInstance, as_of_seq))
                       .order_by(ObligationInstance.lineage_id, ObligationInstance.revision_id)).scalars()]
    obl_lineages = {o["lineage_id"] for o in obligations}

    # Snapshots and packages have no record-sequence column of their own; their knowledge cutoff bounds them instead.
    snapshots = [{"snapshot_id": s.snapshot_id, "obligation_lineage_id": s.obligation_lineage_id,
                  "period_from": s.period_from, "period_to": s.period_to, "state": s.state,
                  "record_cutoff_seq": s.record_cutoff_seq}
                 for s in db.execute(select(AssuranceSnapshot).where(
                     AssuranceSnapshot.tenant_id == tenant_id,
                     AssuranceSnapshot.obligation_lineage_id.in_(obl_lineages or ["-"]),
                     AssuranceSnapshot.record_cutoff_seq <= as_of_seq)
                     .order_by(AssuranceSnapshot.record_cutoff_seq, AssuranceSnapshot.snapshot_id)).scalars()]
    snap_ids = {s["snapshot_id"] for s in snapshots}
    packages = []
    if snap_ids:
        for p in db.execute(select(EvidencePackage).where(EvidencePackage.tenant_id == tenant_id,
                                                         EvidencePackage.record_cutoff_seq <= as_of_seq)
                            .order_by(EvidencePackage.record_cutoff_seq, EvidencePackage.package_id)).scalars():
            hit = sorted(snap_ids & set((p.scope or {}).get("snapshot_ids") or []))
            if hit:
                packages.append({"package_id": p.package_id, "entity_id": p.entity_id, "snapshot_ids": hit,
                                 "status": p.status, "manifest_hash": p.manifest_hash})

    confirmed = {"evaluations": [{"evaluation_id": e.evaluation_id, "subject_id": e.subject_id, "entity_id": e.entity_id,
                                  "obligation_definition_id": e.obligation_definition_id,
                                  "rule_version_id": e.rule_version_id, "outcome": e.outcome,
                                  "record_seq": e.record_seq} for e in prior_evs],
                 "determinations": determinations, "obligations": obligations}
    potential = {"snapshots": snapshots, "packages": packages,
                 "subjects_pending_re_evaluation": [s["subject_id"] for s in pending]}
    has_confirmed = any(confirmed.values())
    if has_confirmed:
        impact = "CONFIRMED"
    elif problems:
        impact = "INCOMPLETE"
    elif any(potential.values()):
        impact = "POTENTIAL"
    else:
        impact = "NONE_RECORDED"
    return {"impact": impact, "as_of_record_seq": as_of_seq, "dependencies_complete": not problems,
            "problems": problems, "prior_rule_version_ids": prior_ids, "subjects": subjects,
            "confirmed": confirmed, "potential": potential,
            "counts": {"evaluations": len(prior_evs), "determinations": len(determinations),
                       "material_changes": sum(1 for d in determinations if d["material_change"]),
                       "obligations": len(obligations), "snapshots": len(snapshots), "packages": len(packages),
                       "subjects_pending_re_evaluation": len(pending)},
            "state_changed": False}
