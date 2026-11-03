"""Deterministic snapshot inputs: Stage 3a of docs/backend-architecture-roadmap.md (audit I-03(d)).

STA-REQ-020 (deterministic reasons and explanation trace), STA-REQ-021 and TIM-REQ-015 (a snapshot pins its exact
dependencies at a record cutoff), DAT-REQ-007 and TIM-REQ-005 (record_seq is the deterministic knowledge order), and
ZA-STA-001's uniqueness rule (an identical control-evaluation input digest may deduplicate to one authoritative
evaluation). PostgreSQL only.

Each test runs as the owner role inside one transaction that is always rolled back, so planted rows and table rewrites
never persist. `CLUSTER ... USING <table>_pkey` rewrites a table physically in id order. The planted rows get ids in
the reverse of their record order, so a rewrite always moves them, and the tests assert that it did.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

TENANT = "tenant-zoiko-group"  # synthetic Customer Zero tenant seeded by the suite
OTHER_TENANT = "tenant-someone-else"
PERIOD = (datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))
FIRST, SECOND = "zzzzzzzz-3a00-4000-8000-0000000000{:02d}", "aaaaaaaa-3a00-4000-8000-0000000000{:02d}"
# Every table whose rows feed a control evaluation or a snapshot and that the engine reads more than one row from.
SNAPSHOT_INPUTS = ("assurance_test_run", "assurance_obligation_occurrence", "assurance_exception_revision",
                   "assurance_obligation_instance_revision", "assurance_control_implementation_revision",
                   "assurance_compensation_revision", "assurance_satisfaction_expression_version",
                   "assurance_control_test_policy_version", "assurance_control_evaluation")


@pytest.fixture(scope="module")
def owner(client):  # the client fixture has migrated the database and seeded Customer Zero
    from app.db import IS_POSTGRES, admin_engine
    if not IS_POSTGRES:
        pytest.skip("PostgreSQL-only")
    return admin_engine


@pytest.fixture
def db(owner):
    """An owner-role session in one transaction that is always rolled back. The owner is not subject to RLS, so the
    tenant boundaries exercised here are the engine's own (SEC-REQ-008: enforced independently of RLS)."""
    conn = owner.connect()
    tx = conn.begin()
    session = Session(bind=conn, autoflush=False)
    try:
        yield session
    finally:
        session.close()
        tx.rollback()
        conn.close()


def _seq(db) -> int:
    from app.platform import next_record_seq
    return next_record_seq(db)


def _watermark(db) -> int:
    from app.platform import current_watermark
    return current_watermark(db)["record_seq"]


def _dora(db) -> str:
    from app.models import ObligationDefinition, ObligationInstance
    odef = db.execute(select(ObligationDefinition).where(ObligationDefinition.key == "DORA_ICT_RISK_FRAMEWORK")).scalar_one()
    return db.execute(select(ObligationInstance.lineage_id).where(ObligationInstance.tenant_id == TENANT,
                                                                 ObligationInstance.obligation_definition_id == odef.obligation_definition_id)
                      .order_by(ObligationInstance.lineage_id)).scalars().first()


def _snapshot(db, lineage, cutoff) -> dict:
    """compute_snapshot without persisting, as canonical JSON: equality means byte-identical output."""
    from app.engines import assurance as asr
    from app.platform import canonical_json
    r = asr.compute_snapshot(db, tenant_id=TENANT, obligation_lineage_id=lineage, period_from=PERIOD[0],
                             period_to=PERIOD[1], record_cutoff_seq=cutoff, perspective="RECONSTRUCTED_NOW",
                             actor_id="actor-cco", persist=False)
    return json.loads(canonical_json(r))


def _tested_control(db, lineage):
    """A control leaf of the DORA expression with a test policy and test definition, and that test definition."""
    from app.models import ControlImplementation, ControlTestPolicy, TestDefinition
    snap = _snapshot(db, lineage, _watermark(db))
    for seg in snap["segments"]:
        for leaf in seg["leaf_evaluations"]:
            ci = db.get(ControlImplementation, leaf.get("control_implementation_id") or "-")
            pol = ci and db.execute(select(ControlTestPolicy).where(
                ControlTestPolicy.control_implementation_id == ci.control_implementation_id)
                .order_by(ControlTestPolicy.version.desc())).scalars().first()
            td = pol and db.execute(select(TestDefinition).where(TestDefinition.policy_version_id == pol.policy_version_id)
                                    .order_by(TestDefinition.test_definition_id)).scalars().first()
            if td:
                return ci, td
    raise AssertionError("the seeded DORA obligation has no tested control")


def _evidence(db, n=2) -> list[str]:
    from app.models import EvidenceArtifact
    ids = db.execute(select(EvidenceArtifact.evidence_id).where(EvidenceArtifact.tenant_id == TENANT)
                     .order_by(EvidenceArtifact.record_seq, EvidenceArtifact.evidence_id).limit(n)).scalars().all()
    assert len(ids) == n
    return list(ids)


def _run(db, td, run_id, evidence, tenant=TENANT, window=PERIOD, record_seq=None):
    from app.models import TestRun
    db.add(TestRun(test_run_id=run_id, tenant_id=tenant, test_definition_id=td.test_definition_id, occurred_at=window[1],
                   window_from=window[0], window_to=window[1], result="PASS", source_system="stage-3a-test",
                   evidence_ids=evidence, record_seq=record_seq if record_seq is not None else _seq(db)))
    db.flush()


def _plant_order_sensitive_inputs(db, lineage, td):
    """Two of each order-sensitive input, inserted (and so recorded) in the reverse of their id order."""
    from app.models import ExceptionRecord, ObligationOccurrence
    ev = _evidence(db)
    for i, run_id in enumerate((FIRST.format(1), SECOND.format(1))):
        _run(db, td, run_id, [ev[i]])
    for occ_id in (FIRST.format(2), SECOND.format(2)):  # due together, so they share one occurrence trace node
        db.add(ObligationOccurrence(occurrence_id=occ_id, tenant_id=TENANT, obligation_lineage_id=lineage,
                                    window_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
                                    due_at=datetime(2026, 8, 15, tzinfo=timezone.utc), record_seq=_seq(db)))
        db.flush()
    for exc_id in (FIRST.format(3), SECOND.format(3)):  # both APPROVED and overlapping: listed in the same segments
        db.add(ExceptionRecord(exception_id=exc_id, tenant_id=TENANT, target_type="OBLIGATION", target_id=lineage,
                               reason="stage 3a ordering probe", state="APPROVED", requested_by="actor-cco",
                               approved_by="actor-risk", valid_from=PERIOD[0], valid_to=PERIOD[1], record_seq=_seq(db)))
        db.flush()


def _physical_order(db, table, key, ids) -> list[str]:
    return db.execute(text(f"SELECT {key} FROM {table} WHERE {key} = ANY(:ids) ORDER BY ctid"), {"ids": list(ids)}).scalars().all()


def _rewrite_in_id_order(db):
    for t in SNAPSHOT_INPUTS:
        db.execute(text(f"CLUSTER {t} USING {t}_pkey"))


# ---------------------------------------------------------------- order independence
def test_snapshot_and_control_evaluation_are_independent_of_physical_row_order(db):
    from app.engines import assurance as asr
    lineage = _dora(db)
    ci, td = _tested_control(db, lineage)
    _plant_order_sensitive_inputs(db, lineage, td)
    cutoff = _watermark(db)
    before = _snapshot(db, lineage, cutoff)
    ce_before = asr.evaluate_control(db, ci, *PERIOD, cutoff, persist=False)
    planted = {"assurance_test_run": ("test_run_id", 1), "assurance_obligation_occurrence": ("occurrence_id", 2),
               "assurance_exception_revision": ("exception_id", 3)}
    for table, (key, n) in planted.items():
        assert _physical_order(db, table, key, (FIRST.format(n), SECOND.format(n))) == [FIRST.format(n), SECOND.format(n)]
    _rewrite_in_id_order(db)
    for table, (key, n) in planted.items():  # the rewrite really reversed them, so this test can fail
        assert _physical_order(db, table, key, (FIRST.format(n), SECOND.format(n))) == [SECOND.format(n), FIRST.format(n)]
    ce_after = asr.evaluate_control(db, ci, *PERIOD, cutoff, persist=False)
    assert ce_after["dependency_digest"] == ce_before["dependency_digest"]
    assert ce_after["tests"] == ce_before["tests"]
    after = _snapshot(db, lineage, cutoff)
    assert after == before  # state, segments (traces, exception treatments, occurrences), manifest and digest
    assert {FIRST.format(1), SECOND.format(1)} <= set(after["dependency_manifest"]["test_runs"])
    assert {FIRST.format(2), SECOND.format(2)} <= set(after["dependency_manifest"]["occurrences"])
    assert {FIRST.format(3), SECOND.format(3)} <= set(after["dependency_manifest"]["exceptions"])


# ---------------------------------------------------------------- record cutoff, dependencies and tenant boundary
def test_snapshot_pins_its_record_cutoff_dependencies_and_tenant(db):
    lineage = _dora(db)
    ci, td = _tested_control(db, lineage)
    cutoff = _watermark(db)
    base = _snapshot(db, lineage, cutoff)
    _run(db, td, FIRST.format(4), _evidence(db, 1))
    assert _snapshot(db, lineage, cutoff) == base  # recorded after the cutoff: not known then (TIM-REQ-015)
    later = _watermark(db)
    with_run = _snapshot(db, lineage, later)
    assert FIRST.format(4) in with_run["dependency_manifest"]["test_runs"]  # a new dependency is pinned (STA-REQ-021)
    assert with_run["dependency_digest"] != base["dependency_digest"]
    # Records that are not inputs leave the snapshot byte-identical at the same cutoff. (Digests include the cutoff, the
    # evaluation's temporal context, so the comparison is made at one record position, with and without them.)
    position = _seq(db)
    clean = _snapshot(db, lineage, position)
    # Another tenant's run naming this test definition: never an input, even without RLS (owner role here).
    _run(db, td, FIRST.format(5), [], tenant=OTHER_TENANT, record_seq=position)
    # A run whose window lies outside the period.
    _run(db, td, FIRST.format(6), [], window=(PERIOD[0] - timedelta(days=60), PERIOD[0] - timedelta(days=30)),
         record_seq=position)
    assert _snapshot(db, lineage, position) == clean


# ---------------------------------------------------------------- deduplication of control evaluations
def test_duplicate_control_evaluations_resolve_to_the_earliest(db):
    """A concurrent race can store two evaluations with one input digest (nothing makes the digest unique). Whatever
    the physical order, the engine keeps resolving to the earliest one and does not add a third."""
    from app.engines import assurance as asr
    from app.models import ControlEvaluation
    lineage = _dora(db)
    ci, _ = _tested_control(db, lineage)
    cutoff = _watermark(db)
    first = asr.evaluate_control(db, ci, *PERIOD, cutoff, persist=True)
    original = db.get(ControlEvaluation, first["evaluation_id"])
    twin = ControlEvaluation(evaluation_id="00000000-3a00-4000-8000-000000000007", tenant_id=original.tenant_id,
                             control_implementation_id=original.control_implementation_id,
                             policy_version_id=original.policy_version_id, window_from=original.window_from,
                             window_to=original.window_to, record_cutoff_seq=original.record_cutoff_seq,
                             state=original.state, reason_codes=original.reason_codes, inputs=original.inputs,
                             precedence_rule=original.precedence_rule, evaluator_version=original.evaluator_version,
                             dependency_digest=original.dependency_digest,
                             created_at=original.created_at + timedelta(seconds=1))
    db.add(twin)
    db.flush()
    _rewrite_in_id_order(db)  # the later twin now comes first physically
    again = asr.evaluate_control(db, ci, *PERIOD, cutoff, persist=True)
    assert again["evaluation_id"] == original.evaluation_id
    assert len(db.execute(select(ControlEvaluation.evaluation_id).where(
        ControlEvaluation.dependency_digest == original.dependency_digest)).scalars().all()) == 2


# ---------------------------------------------------------------- end to end through the API
def test_repeated_snapshot_requests_reproduce_exactly(client):
    """Two persisted snapshots of the same obligation, period and record cutoff: same digest, same hash, same state,
    each its own immutable record (ZA-STA-001 uniqueness: each request keeps its own reference), both reproducible."""
    from conftest import H
    from app.db import AdminSession
    from app.models import ControlEvaluation
    lineage = next(o for o in client.get("/api/obligations", headers=H("actor-cco")).json()
                   if o["definition"]["key"] == "DORA_ICT_RISK_FRAMEWORK")["lineage_id"]
    cutoff = client.get("/api/watermark", headers=H("actor-cco")).json()["record_seq"]  # signed-in only (I-10)
    body = {"period_from": PERIOD[0].isoformat(), "period_to": PERIOD[1].isoformat(), "record_cutoff_seq": cutoff}
    a = client.post(f"/api/obligations/{lineage}/snapshots", headers=H("actor-cco"), json=body).json()
    with AdminSession() as s:
        evaluations = s.execute(select(ControlEvaluation.evaluation_id)).scalars().all()
    b = client.post(f"/api/obligations/{lineage}/snapshots", headers=H("actor-cco"), json=body).json()
    with AdminSession() as s:
        assert sorted(s.execute(select(ControlEvaluation.evaluation_id)).scalars().all()) == sorted(evaluations)
    assert a["snapshot_id"] != b["snapshot_id"]
    assert (a["dependency_digest"], a["snapshot_hash"], a["state"]) == (b["dependency_digest"], b["snapshot_hash"], b["state"])
    for snap in (a, b):
        rep = client.get(f"/api/snapshots/{snap['snapshot_id']}/reproduce", headers=H("actor-audit")).json()
        assert rep["reproducible"] and rep["dependency_digest_match"], rep
