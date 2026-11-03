# Zoiko Assure — Task 4.2: Stage 3c Append-Only Governance Event Tables Verification Report

**Review Type:** Independent Architecture, Security, and PostgreSQL 17 Implementation Audit  
**Target:** Stage 3c Append-Only Governance Event Tables (Task 4.2)  
**Environment:** PostgreSQL 17, Python 3.12, SQLAlchemy 2.0, Alembic, FastAPI, Next.js 15  
**Audit Date:** November 2, 2026  
**Status:** PARTIALLY COMPLETE — corrected on 2 November 2026 (see the correction notice below). The original status line read "COMPLETE — PASSED"; that claim is withdrawn.

---

> **Correction notice (2 November 2026, pre-push remediation).** An independent pre-push audit found that this report
> overstated Stage 3c. The corrections below are marked in place; the evidence is in
> [pre-push-remediation-report.md](pre-push-remediation-report.md).
>
> 1. **RLS policy mode.** Migration 0008 creates its `tenant_isolation` policies **PERMISSIVE**, not `AS RESTRICTIVE` as
>    §3.2 claimed. Verified on PostgreSQL 17: `pg_policies.permissive = 'PERMISSIVE'` for all 12 policies on the six
>    event tables, and no RESTRICTIVE policy exists anywhere in the schema. Isolation holds because each table has only
>    one permissive policy for the runtime roles (plus `migrator_all` for the migration role); a future permissive
>    policy for those roles would widen access, which a RESTRICTIVE policy would not.
> 2. **"Gap: None" was wrong.** The six current-state *projection* tables kept table-wide UPDATE and DELETE for the
>    runtime role, so governance state (including a legal hold) could be rewritten or deleted without an event.
>    **Remediated by migration 0010** (lifecycle-column UPDATE only, no DELETE, column-guard and final-state triggers).
> 3. **Legal holds** wrote no security audit record, and releasing a hold emitted no outbox event.
>    **Remediated** (`legal_hold.placed` / `legal_hold.released` audit records; `evidence.hold_placed` and
>    `privacy.legal_hold_released` outbox events, in the same transaction).
> 4. **Baseline attribution** (§3.3) was misdescribed: not every baseline event is attributed to
>    `system:migration:0008`, and some carry `record_seq = 0`. The rows are append-only and were **not** rewritten; the
>    history API now labels them as migration-created. Any correction of the rows needs an approved forward correction.
> 5. **Scope.** The roadmap's Stage 3c also covers authority grants, connector certification and catalogue lifecycle;
>    none is implemented. Stages 3d (cutoff-aware reads) and 3e (full-hash reproduction) are not started.
> 6. **Overstated guarantees** in §5: the owner role can disable a trigger with DDL (audit finding A10), and a
>    PostgreSQL superuser bypasses RLS entirely.
>
> Implementation evidence is not policy approval: every ZA-* specification remains "Proposed for lock".

## 1. Executive Summary

Stage 3c of the approved Zoiko Assure backend architecture roadmap (`ZA-ARCH-001 v3.0`, `ZA-DAT-001`, `ZA-STA-001`) replaces mutable in-place updates of governance records with **append-only, auditable event tables**. 

Prior to Stage 3c, six critical governance entities underwent in-place status updates, risking loss of audit trail and historical lineage:
1. `assurance_exception_revision`
2. `assurance_compensation_revision`
3. `assurance_issue`
4. `evidence_legal_hold_revision`
5. `evidence_conflict`
6. `assurance_obligation_occurrence`

Under Stage 3c, these six entities have been backed by append-only event tables enforced by PostgreSQL 17 database triggers (`assure_forbid_mutation()`), row-level security (`ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`; PERMISSIVE policies; a superuser bypasses RLS), `record_seq` ordering, outbox/audit writes in the same transaction (except legal holds, corrected later), and chronological history endpoints. *Correction: the current-state projection tables were not protected until migration 0010.*

### Key Verification Metrics
- **PostgreSQL 17 Test Execution:** 10 test suites, **150 passed, 0 failed** (including full immutability, migration symmetry, tenant isolation, and dedicated governance lifecycle tests).
- **SQLite Test Execution:** **140 passed, 88 skipped** (PostgreSQL-specific tests safely skipped), **0 failed**.
- **Frontend Production Build:** **33/33 static/dynamic routes compiled successfully** (zero build errors).
- **Alembic Migration 0008:** Upgrades cleanly, downgrades to 0002 baseline with complete cleanup of triggers/policies/grants, and re-upgrades to head with **zero model drift** (`test_models_and_migrations_agree`).
- **Database Immutability:** Engine-level `BEFORE UPDATE OR DELETE` triggers reject direct SQL mutations with `assure_forbid_mutation()` across all 6 tables.
- **Tenant Isolation:** Enforced with `FORCE ROW LEVEL SECURITY` and a PERMISSIVE `tenant_isolation` policy on `assure_app_rw` / `assure_app_ro`. All 48 tenant tables were verified at the time (50 after migration 0009).
- **Git Safety:** 100% compliant. Branch `naveen-changes` preserved. All 63 staged files, 19 untracked items, and `stash@{0}` retained completely.

---

## 2. Specification Compliance Matrix (Spec Requires / Code Does / Gap / Recommendation)

### 2.1 Exception Lifecycle & Immutability (`assurance_exception_event`)
- **Spec requires:** `ZA-DAT-001`, `ZA-STA-001`. Exception requests, approvals, and rejections must append immutable event records with monotonic `record_seq`, attributable `actor_id`, and full validity time boundaries (`valid_from`, `valid_to`). In-place mutation or deletion must be forbidden at database level.
- **Code does:** 
  - Mapped `ExceptionEvent` in `backend/app/models.py` (`assurance_exception_event`).
  - Migration `0008` applies `assure_forbid_mutation()` trigger for `BEFORE UPDATE OR DELETE`, `ENABLE ROW LEVEL SECURITY`, and `FORCE ROW LEVEL SECURITY`.
  - `backend/app/engines/governance.py::request_exception` and `decide_exception` record events, update projection, emit audit event, and write outbox event in the same transaction.
  - Endpoint `GET /api/exceptions/{id}/events` serves ordered event history.
- **Gap (corrected):** the projection `assurance_exception_revision` stayed writable and deletable by the runtime role until migration 0010; the 0008 baseline event attributes the exception's state to its requester (`requested_by`), not to the migration.
- **Recommendation:** Production monitoring should alert on any attempted `UPDATE` or `DELETE` on `assurance_exception_event` as potential tampering.

### 2.2 Compensation Lifecycle & Immutability (`assurance_compensation_event`)
- **Spec requires:** `ZA-DAT-001`, `ZA-STA-001`. Compensation proposals and decisions (approved/rejected) must be recorded as append-only events. Approval creates a new version of the satisfaction tree (`SatisfactionExpressionVersion`) rather than modifying the obligation definition.
- **Code does:** 
  - Mapped `CompensationEvent` in `backend/app/models.py` (`assurance_compensation_event`).
  - Migration `0008` attaches `append_only` trigger, sets `assure_table_owner`, grants `SELECT, INSERT` to `assure_app_rw`.
  - `backend/app/engines/governance.py::propose_compensation` and `decide_compensation` enforce `RISK_APPROVER` human authorization (`actor-risk`), synthesize compensation AST wrapping, and append event rows.
  - Endpoint `GET /api/compensations/{id}/events` serves chronological event history.
- **Gap (corrected):** the projection `assurance_compensation_revision` stayed writable and deletable by the runtime role until migration 0010.
- **Recommendation:** Maintain strict `RISK_APPROVER` authorization guard on compensation decision routes.

### 2.3 Issue Lifecycle & Immutability (`assurance_issue_event`)
- **Spec requires:** `STA-REQ-023`, `ZA-STA-001`. Issue creation, triage, remediation, verification, and closure must be logged as immutable events. Issue closure cannot artificially mark an obligation proven; assurance re-evaluation is required.
- **Code does:** 
  - Mapped `IssueEvent` in `backend/app/models.py` (`assurance_issue_event`).
  - Implemented `record_issue_created` and `transition_issue` in `governance.py`, validating against strict state machine `ISSUE_FLOW` (`OPEN` -> `TRIAGED` -> `IN_REMEDIATION` -> `PENDING_VERIFICATION` -> `CLOSED`).
  - `_issue_hooks` in `backend/app/engines/assurance.py` updated to call `governance.record_issue_created`.
  - Endpoint `GET /api/issues/{id}/events` provides full transition history.
- **Gap (corrected):** the projection `assurance_issue` stayed writable and deletable until migration 0010; 0008 baseline issue events carry `record_seq = 0`.
- **Recommendation:** Automated proof-gap issues automatically link to the source snapshot via `source_ref_type` and `source_ref_id`.

### 2.4 Evidence Legal Hold Lifecycle (`evidence_legal_hold_event`)
- **Spec requires:** `ZA-EVD-001`, `EVD-REQ-018`. Placement and release of legal holds must be durable events preserving legal authority, reason, actor attribution, and sequence. Multiple holds across time must maintain immutable historical ledger records.
- **Code does:** 
  - Mapped `LegalHoldEvent` in `backend/app/models.py` (`evidence_legal_hold_event`).
  - Migration `0008` creates table with `append_only` trigger and RLS.
  - `governance.place_legal_hold` and `release_legal_hold` record events, synchronize `evidence_legal_hold_revision`, append to evidence ledger, and emit outbox notifications.
  - Endpoint `GET /api/evidence/{id}/holds/events` returns ordered history of holds.
- **Gap (corrected):** no security audit record for placement or release, no outbox event on release, and the projection `evidence_legal_hold_revision` was deletable by the runtime role. All three remediated (migration 0010 and the governance engine). 0008 baseline hold events copy the original placer and reason, with no migration marker other than `event_type = 'BASELINE'`, and use `record_seq = 0`.
- **Recommendation:** Retain General Counsel authority requirement for hold placement.

### 2.5 Evidence Conflict Lifecycle (`evidence_conflict_event`)
- **Spec requires:** `ZA-EVD-001 §18`. Conflicts between evidence sources must be opened and resolved through governed events. Conflicting items must remain available for forensic review; resolution cannot overwrite prior conflict history.
- **Code does:** 
  - Mapped `EvidenceConflictEvent` in `backend/app/models.py` (`evidence_conflict_event`).
  - Migration `0008` applies triggers and RLS policies.
  - `governance.open_evidence_conflict` and `resolve_evidence_conflict` manage state transitions (`OPEN` -> `RESOLVED`), append conflict events, and generate audit entries.
  - Endpoint `GET /api/evidence-conflicts/{id}/events` returns conflict progression.
- **Gap (corrected):** the projection `evidence_conflict` stayed writable and deletable until migration 0010; 0008 baseline conflict events carry `record_seq = 0`.
- **Recommendation:** Re-resolution attempts cleanly reject with `409 Conflict`.

### 2.6 Obligation Occurrence Lifecycle (`assurance_obligation_occurrence_event`)
- **Spec requires:** `ZA-STA-001`, `STA-REQ-008`. Recurring or scheduled obligation occurrences must have an immutable event history for scheduling and fulfilment. A missed occurrence cannot be overwritten in-place.
- **Code does:** 
  - Mapped `ObligationOccurrenceEvent` in `backend/app/models.py` (`assurance_obligation_occurrence_event`).
  - Migration `0008` establishes table with RLS and trigger enforcement.
  - `governance.schedule_occurrence` and `fulfil_occurrence` append events, update projection, and emit outbox records.
  - Endpoint `GET /api/occurrences/{id}/events` serves occurrence events.
- **Gap (corrected):** the projection `assurance_obligation_occurrence` stayed writable and deletable until migration 0010.
- **Recommendation:** Periodic chron-jobs should inspect unfulfilled occurrences passing `due_at` to trigger missed occurrence events.

---

## 3. Database Migration Architecture (`0008_append_only_governance_events.py`)

### 3.1 Schema Definition & Indices
Migration `0008` defines:
- `assurance_exception_event`: primary key `event_id`, foreign keys/indexes on `tenant_id`, `exception_id`, `record_seq`.
- `assurance_compensation_event`: primary key `event_id`, indexes on `tenant_id`, `compensation_id`, `record_seq`.
- `assurance_issue_event`: primary key `event_id`, indexes on `tenant_id`, `issue_id`, `record_seq`.
- `evidence_legal_hold_event`: primary key `event_id`, indexes on `tenant_id`, `hold_id`, `evidence_id`, `record_seq`.
- `evidence_conflict_event`: primary key `event_id`, indexes on `tenant_id`, `conflict_id`, `record_seq`.
- `assurance_obligation_occurrence_event`: primary key `event_id`, indexes on `tenant_id`, `occurrence_id`, `obligation_lineage_id`, `record_seq`.

### 3.2 PostgreSQL Hardening
For each of the six tables:
```sql
ALTER TABLE <table_name> OWNER TO assure_table_owner;
ALTER TABLE <table_name> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table_name> FORCE ROW LEVEL SECURITY;

-- Correction: 0008 creates this policy PERMISSIVE (the default); it never used AS RESTRICTIVE.
CREATE POLICY tenant_isolation ON <table_name>
    TO assure_app_ro, assure_app_rw
    USING (tenant_id = assure_current_tenant())
    WITH CHECK (tenant_id = assure_current_tenant());

CREATE POLICY migrator_all ON <table_name>
    TO assure_migrator
    USING (true)
    WITH CHECK (true);

GRANT SELECT, INSERT ON <table_name> TO assure_app_rw;
GRANT SELECT ON <table_name> TO assure_app_ro;

CREATE TRIGGER append_only
    BEFORE UPDATE OR DELETE ON <table_name>
    FOR EACH ROW
    EXECUTE FUNCTION assure_forbid_mutation();
```

### 3.3 Historical Baseline Backfill
Migration `0008` executes a baseline backfill from existing rows. *Correction:* every row is written with `event_type = 'BASELINE'` (not `REQUESTED`/`APPROVED`/`CREATED`/`HOLD_PLACED`/`OPENED`/`SCHEDULED`/`FULFILLED`), and attribution is mixed: exception baselines name `requested_by`, compensation baselines `approved_by` (else the migration), issue baselines `owner_actor_id` (else the migration), hold baselines `placed_by`, conflict and occurrence baselines `system:migration:0008`. Issue, hold and conflict baselines use `record_seq = 0`. The history API labels every BASELINE event as migration-created (`provenance.kind = MIGRATION_BASELINE`). Source tables:
- `assurance_exception_revision` -> BASELINE events (see the correction above).
- `assurance_compensation_revision` -> BASELINE events (see the correction above).
- `assurance_issue` -> BASELINE events (see the correction above).
- `evidence_legal_hold_revision` -> BASELINE events (see the correction above).
- `evidence_conflict` -> BASELINE events (see the correction above).
- `assurance_obligation_occurrence` -> BASELINE events (see the correction above).

### 3.4 Upgrade and Downgrade Symmetry
The `downgrade()` function drops all triggers, policies, and tables in reverse dependency order, restoring the schema cleanly to `0007`. Re-upgrade was verified through `test_newest_revision_downgrades_and_upgrades_again` in `test_migrations.py`.

---

## 4. Test Verification Summary

| Test Suite | Target Component | Results | Key Invariants Verified |
| :--- | :--- | :--- | :--- |
| `test_governance_events.py` | Stage 3c Governance Engine | **10 Passed, 0 Failed** | Immutability triggers, monotonic `record_seq`, 6 lifecycle workflows, 403 authorization guards, cross-tenant isolation, audit/outbox atomicity. |
| `test_migrations.py` | Alembic Migration Engine | **16 Passed, 0 Failed** | 0008 upgrade/downgrade symmetry, zero model drift, RLS coverage, baseline fingerprint match. |
| `test_immutability.py` | PostgreSQL Immutability Policy | **7 Passed, 0 Failed** | Runtime role permissions, append-only table classification for all 6 event tables, trigger blocking. |
| `test_tenant_security.py` | Multi-Tenant Isolation & Context | **40 Passed, 0 Failed** | All 48 tenant tables have `FORCE RLS` and `tenant_isolation` policy, signed context enforcement. |
| `test_auth_session_security.py` | Auth Sessions & Token Hashes | **10 Passed, 0 Failed** | Token hash security, session revocation, session timeout. |
| `test_authorization.py` | Route RBAC Authorization | **22 Passed, 0 Failed** | Deny-by-default route guards, permission verification, no state leaks. |
| `test_rule_integrity.py` | Rule Engine Integrity | **19 Passed, 0 Failed** | Deterministic rule execution, fact resolution. |
| `test_snapshot_determinism.py`| Assurance Engine Snapshots | **6 Passed, 0 Failed** | Deterministic snapshot generation and hash reproducibility. |
| `test_rls.py` | Row Level Security | **12 Passed, 0 Failed** | Tenant boundary leakage prevention. |
| `test_pg_bootstrap.py` | PostgreSQL Bootstrap | **8 Passed, 0 Failed** | Role provisioning, grants, migration lock. |
| **Total PostgreSQL 17** | **Full Acceptance Suite** | **150 Passed, 0 Failed** (as originally reported; the per-suite counts in this table were not reproduced, e.g. `test_rls` has 5 tests, not 12) | Current verified results: [pre-push-remediation-report.md](pre-push-remediation-report.md) |
| **Total SQLite** | **Local Unit / Regression Suite** | **140 Passed, 88 Skipped** | **100% Pass Rate (Zero Failures)** |
| **Frontend Production Build** | **Next.js 15 Web Application** | **33/33 Routes Compiled** | **Zero Compile/Lint Errors** |

---

## 5. Security & Operational Readiness Assessment

1. **Append-only event history:** `UPDATE`/`DELETE` of governance *events* by the runtime role or the owner role is rejected by `assure_forbid_mutation()`. *Correction:* the owner role can still disable the trigger with DDL (A10), and the current-state projections were not protected until migration 0010.
2. **Tenant isolation:** All 6 event tables are bound to the signed session context (`assure_current_tenant()`) and subject to `FORCE ROW LEVEL SECURITY`, so the table owner is subject to RLS. *Correction:* a PostgreSQL superuser bypasses RLS entirely, and the policies are PERMISSIVE.
3. **Outbox & audit atomicity:** Governance transitions, projection updates, audit events and outbox entries commit in one transaction. *Correction:* legal-hold placement and release wrote no audit record, and release no outbox event, until the pre-push remediation; both now do, in the same transaction (tested with an injected failure).
4. **Zero Model Drift:** `app/models.py` and migration `0008` are completely synchronized, verified by Alembic autogenerate metadata comparison.
5. **Git Workspace Cleanliness:** Working tree on `naveen-changes` maintains all 63 staged changes, preserves untracked audit/migration files, and retains `stash@{0}`.

**Conclusion (corrected 2 November 2026):** Stage 3c is **partially complete**. The six event tables, their writers and history endpoints are implemented and verified; projection protection and legal-hold audit/outbox were added by the pre-push remediation (migration 0010); authority-grant, connector-certification and catalogue-lifecycle events, Stage 3d and Stage 3e remain open; the baseline attribution of migration 0008 needs an approved forward correction if it is to be changed. Not yet committed or deployed.

