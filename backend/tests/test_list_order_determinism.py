"""Deterministic ordering of the applicability, obligation/assurance/governance and rule list endpoints.

Every list keeps its existing primary order and ends in a unique column, so rows that tie on the primary key come back
in the same order on every read and on both databases, never in physical row order (which an UPDATE changes on
PostgreSQL). Lists that had no ORDER BY at all now use record/creation order, then ID: /obligations,
/applicability/determinations (record_from_seq), /controls (record_seq), /risk-acceptances (valid_from descending, as
the sibling /exceptions and /compensations), and the fact profile's facts (fact key) and conflicts.

Two kinds of test:
  * behavioural: tied rows inserted for a probe organisation in an order that is neither sorted nor reversed, so
    insertion/physical order cannot pass; the response must be the primary order, then ascending ID;
  * statement-level: the ORDER BY each changed query sends to the database ends in the table's unique column. This
    covers the queries whose tied rows are costly to construct (the 200-row cut of /applicability/evaluations, the
    fact profile, the evaluation detail, controls, occurrences and rule versions).
No issue type, state, lifecycle, event or authorization behaviour is involved; only the order of rows already returned.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from conftest import H, add_principal

TAG = secrets.token_hex(3).lower()
TP = f"tenant-lod-{TAG}"
CCO = f"lod-cco-{TAG}"
SUBJECT = f"subj-lod-{TAG}"
CONTENT = "actor-content-analyst"
NEW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
OLD = NEW - timedelta(days=30)
SEQ = 10 ** 12 + 7  # far above any real record sequence: these rows are ordered among themselves only
TIED = ("c", "a", "b")  # insertion order of the tied rows: neither sorted nor reversed


def _id(kind: str, s: str) -> str:
    return f"lod-{TAG}-{kind}-{s}"


@pytest.fixture(scope="module")
def world(client):
    from app.db import CELL_ID, AdminSession
    from app.models import (Actor, ApplicabilityDetermination, Compensation, CompensationEvent, ExceptionEvent,
                            ExceptionRecord, FactDefinition, FactRequest, Instrument, LegalEntity, ObligationDefinition,
                            ObligationInstance, RiskAcceptance, RuleDraft, Subject, Tenant)
    with AdminSession() as db:
        db.add(Tenant(tenant_id=TP, legal_name=f"{TP} Group", contracting_region="EU"))
        db.flush()
        db.add(LegalEntity(entity_id=f"entity-{TP}", tenant_id=TP, legal_name=f"{TP} AG", country="DE",
                           home_cell_id=CELL_ID, subject_id=SUBJECT))
        db.add(Subject(subject_id=SUBJECT, tenant_id=TP, entity_id=f"entity-{TP}", subject_type="LEGAL_ENTITY",
                       display_name=f"{TP} AG"))
        add_principal(db, actor_id=CCO, tenant_id=TP, actor_type="HUMAN", display_name=CCO, roles=["CCO"])
        fd = db.execute(select(FactDefinition).where(FactDefinition.subject_type == "LEGAL_ENTITY")
                        .order_by(FactDefinition.key)).scalars().first()
        inst = Instrument(identifier=f"LOD-{TAG}", title="Ordering probe instrument", jurisdiction="EU",
                          authority="Probe", coverage_mode="ENCODED")
        db.add(inst)
        db.flush()
        odef = ObligationDefinition(key=f"LOD_{TAG}", title="Ordering probe definition", instrument_id=inst.instrument_id,
                                    provision_ids=[], subject_type="LEGAL_ENTITY")
        db.add(odef)
        db.flush()

        def rows(make, kind, primary_new, primary_old=None):
            """The tied rows (primary value `primary_new`) in TIED order, then one row with `primary_old`."""
            for s in TIED:
                db.add(make(_id(kind, s), primary_new))
                db.flush()  # one INSERT per row, in this order
            if primary_old is not None:
                db.add(make(_id(kind, "old"), primary_old))
                db.flush()
        rows(lambda i, seq: ObligationInstance(
            revision_id=i, lineage_id=f"lin-{i}", tenant_id=TP, subject_id=SUBJECT, obligation_definition_id="-",
            determination_revision_id="-", attestation_id="-", valid_from=OLD, record_from_seq=seq),
            "obl", SEQ, SEQ - 1)
        rows(lambda i, seq: ApplicabilityDetermination(
            revision_id=i, lineage_id=f"lin-{i}", tenant_id=TP, subject_id=SUBJECT, obligation_definition_id="-",
            evaluation_id="-", outcome="APPLIES", state="PROPOSED", valid_from=OLD, record_from_seq=seq, actor_id=CCO),
            "det", SEQ, SEQ - 1)
        rows(lambda i, at: ExceptionRecord(
            exception_id=i, tenant_id=TP, target_type="OBLIGATION", target_id="-", reason="ordering probe",
            requested_by=CCO, valid_from=at, valid_to=at + timedelta(days=90), record_seq=SEQ), "exc", NEW, OLD)
        rows(lambda i, at: Compensation(
            compensation_id=i, tenant_id=TP, obligation_lineage_id="-", primary_control_key="C_PROBE",
            compensating_expression={}, equivalence_rationale="ordering probe", valid_from=at, record_seq=SEQ),
            "cmp", NEW, OLD)
        rows(lambda i, at: RiskAcceptance(
            risk_acceptance_id=i, tenant_id=TP, scope_ref="-", residual_risk_statement="ordering probe",
            approver_id=CCO, valid_from=at), "rsk", NEW, OLD)
        rows(lambda i, at: FactRequest(
            request_id=i, tenant_id=TP, subject_id=SUBJECT, fact_definition_id=fd.fact_definition_id,
            reason_code="MISSING_REQUIRED_FACT", owner_role="REGULATORY_ANALYST", due_at=NEW, created_at=at),
            "frq", NEW, OLD)
        rows(lambda i, seq: ExceptionEvent(
            event_id=i, exception_id=_id("exc", "a"), tenant_id=TP, event_type="REQUESTED", to_state="REQUESTED",
            actor_id=CCO, target_type="OBLIGATION", target_id="-", valid_from=NEW, valid_to=NEW + timedelta(days=90),
            record_seq=seq), "exev", SEQ)
        rows(lambda i, seq: CompensationEvent(
            event_id=i, compensation_id=_id("cmp", "a"), tenant_id=TP, event_type="SUBMITTED", to_status="PROPOSED",
            actor_id=CCO, obligation_lineage_id="-", primary_control_key="C_PROBE", record_seq=seq), "cpev", SEQ)
        rows(lambda i, at: RuleDraft(
            draft_id=i, obligation_definition_id=odef.obligation_definition_id, valid_from=date(2025, 1, 1),
            author_id=CONTENT, updated_at=at), "drf", NEW, OLD)
        db.commit()
    return True


def _ids(client, path, actor=CCO, prefix=None, key=None):
    r = client.get(path, headers=H(actor))
    assert r.status_code == 200, (path, r.text)
    body = r.json()
    key = key or next(k for k in ("revision_id", "exception_id", "compensation_id", "risk_acceptance_id", "request_id",
                                  "draft_id") if body and k in body[0])
    out = [x[key] for x in body]
    return [i for i in out if i.startswith(prefix)] if prefix else out


SORTED = [s for s in sorted(TIED)]


@pytest.mark.parametrize("path,kind,old_first", [
    ("/api/obligations", "obl", True),                       # record_from_seq ascending, then revision_id
    ("/api/applicability/determinations", "det", True),      # record_from_seq ascending, then revision_id
    ("/api/exceptions", "exc", False),                       # valid_from descending, then exception_id
    ("/api/compensations", "cmp", False),                    # valid_from descending, then compensation_id
    ("/api/risk-acceptances", "rsk", False),                 # valid_from descending, then risk_acceptance_id
    ("/api/fact-requests", "frq", False),                    # created_at descending, then request_id
])
def test_tenant_lists_keep_their_primary_order_and_break_ties_by_id(client, world, path, kind, old_first):
    tied = [_id(kind, s) for s in SORTED]
    expected = [_id(kind, "old"), *tied] if old_first else [*tied, _id(kind, "old")]
    got = _ids(client, path)
    assert got == expected, (path, got)
    assert all(_ids(client, path) == got for _ in range(2))  # the same on every read


@pytest.mark.parametrize("path,kind", [
    (lambda: f"/api/exceptions/{_id('exc', 'a')}/events", "exev"),
    (lambda: f"/api/compensations/{_id('cmp', 'a')}/events", "cpev"),
])
def test_governance_event_histories_break_record_sequence_ties_by_event_id(client, world, path, kind):
    assert _ids(client, path(), key="event_id") == [_id(kind, s) for s in SORTED]


def test_rule_drafts_keep_newest_first_and_break_ties_by_draft_id(client, world):
    got = _ids(client, "/api/rules/drafts", actor=CONTENT, prefix=f"lod-{TAG}-drf-")
    assert got == [*(_id("drf", s) for s in SORTED), _id("drf", "old")]


# ------------------------------------------------------------------------------------------- statement level
def _order_bys(client, path, actor) -> list[str]:
    from app import db as dbmod
    seen: list[str] = []

    def capture(conn, cursor, statement, *args):
        seen.append(statement)
    event.listen(dbmod.engine, "before_cursor_execute", capture)
    try:
        r = client.get(path, headers=H(actor))
    finally:
        event.remove(dbmod.engine, "before_cursor_execute", capture)
    assert r.status_code == 200, (path, r.text)
    out = []
    for s in seen:
        m = re.search(r"ORDER BY (.+?)(?:\s+LIMIT\b|\s+OFFSET\b|\s+FOR\b|$)", " ".join(s.split()))
        if m:
            out.append(m.group(1))
    return out


def _ends_with(clauses: list[str], column: str) -> bool:
    """Whether some ORDER BY ends in `column` (identifier quoting, e.g. of the reserved word "key", ignored)."""
    return any(re.sub(r"\s+(ASC|DESC)$", "", c.split(",")[-1].strip()).replace('"', "") == column for c in clauses)


def test_every_changed_list_query_ends_in_a_unique_column(client, world):
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation, ObligationInstance, ObligationOccurrence, Subject
    zoiko = "tenant-zoiko-group"
    with AdminSession() as db:
        subject = db.execute(select(Subject.subject_id).where(Subject.tenant_id == zoiko,
                                                              Subject.subject_type == "LEGAL_ENTITY")
                             .order_by(Subject.subject_id)).scalars().first()
        evaluation = db.execute(select(ApplicabilityEvaluation.evaluation_id).where(
            ApplicabilityEvaluation.tenant_id == zoiko).order_by(ApplicabilityEvaluation.evaluation_id)).scalars().first()
        lineage = db.execute(select(ObligationInstance.lineage_id).where(
            ObligationInstance.tenant_id == zoiko, ObligationInstance.record_to_seq.is_(None))
            .order_by(ObligationInstance.lineage_id)).scalars().first()
        occurrence = db.execute(select(ObligationOccurrence.occurrence_id).where(
            ObligationOccurrence.tenant_id == zoiko).order_by(ObligationOccurrence.occurrence_id)).scalars().first()
    assert subject and evaluation and lineage and occurrence  # the seeded Customer Zero records
    checks = [
        ("actor-cco", "/api/applicability/evaluations", ["applicability_evaluation.evaluation_id"]),
        ("actor-cco", f"/api/subjects/{subject}/facts", ["catalog_fact_definition.key",
                                                          "applicability_fact_conflict.conflict_id"]),
        ("actor-cco", f"/api/subjects/{subject}/facts/history", ["applicability_fact_assertion_revision.revision_id"]),
        ("actor-cco", f"/api/applicability/evaluations/{evaluation}", [
            "applicability_determination_revision.revision_id", "applicability_determination_challenge.challenge_id",
            "governance_attestation.attestation_id"]),
        ("actor-cco", "/api/applicability/determinations", ["applicability_determination_revision.revision_id"]),
        ("actor-cco", "/api/fact-requests", ["applicability_fact_request.request_id"]),
        ("actor-cco", "/api/obligations", ["assurance_obligation_instance_revision.revision_id"]),
        ("actor-cco", f"/api/obligations/{lineage}", ["assurance_obligation_occurrence.occurrence_id"]),
        ("actor-cco", f"/api/occurrences/{occurrence}/events", ["assurance_obligation_occurrence_event.event_id"]),
        ("actor-cco", "/api/controls", ["assurance_control_implementation_revision.control_implementation_id"]),
        ("actor-cco", "/api/exceptions", ["assurance_exception_revision.exception_id"]),
        (CCO, f"/api/exceptions/{_id('exc', 'a')}/events", ["assurance_exception_event.event_id"]),
        ("actor-cco", "/api/compensations", ["assurance_compensation_revision.compensation_id"]),
        (CCO, f"/api/compensations/{_id('cmp', 'a')}/events", ["assurance_compensation_event.event_id"]),
        ("actor-cco", "/api/risk-acceptances", ["governance_risk_acceptance_revision.risk_acceptance_id"]),
        ("actor-cco", "/api/issues", ["assurance_issue.issue_id"]),
        (CONTENT, "/api/rules/drafts", ["catalog_rule_draft.draft_id"]),
        (CONTENT, "/api/rules/versions", ["catalog_rule_version.rule_version_id"]),
    ]
    missing = []
    for actor, path, columns in checks:
        clauses = _order_bys(client, path, actor)
        missing += [(path, c) for c in columns if not _ends_with(clauses, c)]
    assert missing == [], missing
