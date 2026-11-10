"""Governed test-result ingestion (ZA-STA-001 §01/§03/§04; ZA-DOM-001 §7.1; ZA-DAT-001 §07, DAT-REQ-035;
ZA-API-001 §08; ZA-TEN-001 §14).

A TestRun is "One execution/imported result. Immutable; source provenance and evaluated period required" (DOM §7.1;
DAT §07 "Immutable occurrence"). It has no lifecycle: a correction or retest is a new TestRun "through owning domain"
(WRK §17), and no route updates or deletes one. External tool status is input only (STA-REQ-008): the run never sets a
control or obligation state; ControlEvaluation derives it.

Rules applied before anything is written:
  * result is one of PASS / FAIL / ERROR / INCONCLUSIVE / SKIPPED "only" (GLS §06; STA §01);
  * SKIPPED is "Not a pass. Allowed only with reason" (STA §04): a non-blank ``detail`` is required;
  * the evaluated period is a non-empty half-open window (TIM §02);
  * the test definition, its policy and its control implementation belong to the caller's organisation;
  * evidence belongs to the same organisation AND the same legal entity as the control being tested: proof is not
    reused across entities unless a governed shared-evidence reference and policy permit it (TEN §14), which does not
    exist yet. The same holds for the optional ``source_run_id``, whose IPE result evaluation reads as population proof
    (422 ``SOURCE_RUN_SCOPE_MISMATCH`` for another entity's run);
  * an optional ``Idempotency-Key`` (API-001 §08): the same key and request return the original run; the same key with
    a different request is 409 ``idempotency_conflict``; concurrent duplicates serialise on the key.
"""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import (ControlImplementation, ControlTestPolicy, EvidenceArtifact, IdempotencyRecord, SourceRun,
                      TestDefinition, TestRun)
from ..platform import audit, emit, ensure_utc, next_record_seq, sha256_hex
from .locks import xact_lock

RESULTS = ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED")
OPERATION = "POST /test-runs"
MAX_IDEMPOTENCY_KEY = 200  # platform_idempotency_record.idempotency_key is varchar(200); API-001 §08 allows 255


def _refuse(status: int, error: str, message: str, **refs):
    raise HTTPException(status, {"error": error, "message": message, **refs})


def request_hash(body: dict) -> str:
    """Normalised request hash (API-001 §08 "Server stores normalized request hash"): canonical JSON of the validated
    body, instants in UTC, evidence references as a set."""
    norm = {**body, "evidence_ids": sorted(body.get("evidence_ids") or [])}
    for k in ("occurred_at", "window_from", "window_to"):
        norm[k] = ensure_utc(norm[k]).isoformat()
    return sha256_hex(json.dumps(norm, sort_keys=True, separators=(",", ":")))


def _scope(db, tenant_id: str, td_id: str):
    """The test definition and the control implementation it tests, both in this organisation (404 otherwise)."""
    td = db.get(TestDefinition, td_id)
    if td is None or td.tenant_id != tenant_id:
        _refuse(404, "TEST_DEFINITION_NOT_FOUND", "test definition not found")
    pol = db.get(ControlTestPolicy, td.policy_version_id)
    ci = db.get(ControlImplementation, pol.control_implementation_id) if pol else None
    if pol is None or ci is None or ci.tenant_id != tenant_id:
        _refuse(409, "TEST_DEFINITION_ORPHANED", "the test definition's policy or control implementation is missing")
    return td, ci


def _validate(db, tenant_id: str, body: dict):
    if body["result"] == "SKIPPED" and not (body.get("detail") or "").strip():
        _refuse(422, "SKIPPED_REASON_REQUIRED", "A SKIPPED result needs a reason in detail (ZA-STA-001 §04).")
    if ensure_utc(body["window_to"]) <= ensure_utc(body["window_from"]):
        _refuse(422, "TEST_RUN_WINDOW_EMPTY", "window_to must be later than window_from")
    ids = body.get("evidence_ids") or []
    if len(set(ids)) != len(ids):
        _refuse(422, "DUPLICATE_EVIDENCE", "an evidence reference is listed more than once")
    td, ci = _scope(db, tenant_id, body["test_definition_id"])
    for e in ids:
        art = db.get(EvidenceArtifact, e)
        if art is None or art.tenant_id != tenant_id:
            _refuse(404, "EVIDENCE_NOT_FOUND", "evidence not found", evidence_id=e)
        if art.entity_id != ci.entity_id:
            _refuse(422, "EVIDENCE_SCOPE_MISMATCH", "Evidence of another legal entity cannot prove this control: "
                    "cross-entity proof needs a governed shared-evidence reference (ZA-TEN-001 §14).", evidence_id=e)
    if body.get("source_run_id"):
        # Control evaluation reads this run's IPE result as the population proof for the test (assurance.
        # _evidence_admissible), so the run is proof like evidence: it must belong to this organisation (404 otherwise,
        # never disclosing another organisation's run) and to the legal entity of the control being tested. TEN-REQ-008
        # keeps raw entity evidence with its entity; TEN §14 allows cross-entity proof only through a SharedEvidenceReference
        # that policy explicitly permits, which does not exist yet, so no cross-entity run is accepted.
        run = db.get(SourceRun, body["source_run_id"])
        if run is None or run.tenant_id != tenant_id:
            _refuse(404, "SOURCE_RUN_NOT_FOUND", "source run not found")
        if run.entity_id != ci.entity_id:
            _refuse(422, "SOURCE_RUN_SCOPE_MISMATCH", "A source run of another legal entity cannot prove this control: "
                    "cross-entity proof needs a governed shared-evidence reference (ZA-TEN-001 §14).",
                    source_run_id=run.source_run_id)
    return td, ci


def record_test_run(db, *, tenant_id: str, actor, body: dict, idempotency_key: str | None) -> tuple[TestRun, bool]:
    """Validate and record one TestRun with its audit record and outbox event, in the caller's transaction.
    Returns (run, replayed). The caller commits."""
    h = request_hash(body)
    if idempotency_key is not None:
        if not 1 <= len(idempotency_key) <= MAX_IDEMPOTENCY_KEY:
            _refuse(422, "IDEMPOTENCY_KEY_INVALID", f"Idempotency-Key must be 1-{MAX_IDEMPOTENCY_KEY} characters")
        xact_lock(db, "idempotency", tenant_id, OPERATION, idempotency_key)  # concurrent duplicates wait here
        prior = db.execute(select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id, IdempotencyRecord.operation_code == OPERATION,
            IdempotencyRecord.idempotency_key == idempotency_key)).scalars().first()
        if prior is not None:
            if prior.request_hash != h:
                _refuse(409, "idempotency_conflict", "This Idempotency-Key was used with a different request.")
            run = db.get(TestRun, prior.result_ref)
            if run is not None and run.tenant_id == tenant_id:
                return run, True
    td, ci = _validate(db, tenant_id, body)
    seq = next_record_seq(db)
    run = TestRun(tenant_id=tenant_id, record_seq=seq, **body)
    db.add(run)
    db.flush()
    if idempotency_key is not None:
        db.add(IdempotencyRecord(tenant_id=tenant_id, operation_code=OPERATION, idempotency_key=idempotency_key,
                                 request_hash=h, result_ref=run.test_run_id))
        try:
            db.flush()
        except IntegrityError:  # only reachable without the advisory lock (SQLite): a racing twin committed first
            db.rollback()
            _refuse(409, "idempotency_conflict", "A request with this Idempotency-Key is already being processed.")
    audit(db, action="test_run.record", object_type="TestRun", object_id=run.test_run_id, actor=actor,
          tenant_id=tenant_id, new=run.result, record_seq=seq,
          reason=(f"test {td.test_definition_id} of control implementation {ci.control_implementation_id} from "
                  f"{run.source_system}; window [{run.window_from}, {run.window_to})"
                  + (f"; external status {run.external_status}" if run.external_status else "")))
    emit(db, event_type="test.run_recorded", aggregate_type="TestRun", aggregate_id=run.test_run_id, tenant_id=tenant_id,
         record_seq=seq, payload={"result": run.result, "external_status": run.external_status, "test": td.name})
    return run, False
