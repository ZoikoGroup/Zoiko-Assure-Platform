# Zoiko Assure — Backend Architecture Implementation Roadmap

> **Status:** plan. Nothing here is implemented by writing it; every stage starts with its own reviewed change set.
> **Progress:**
> - Stage 1 is implemented in the working tree (27 October 2026), not yet committed.
> - Stage 2c for completed results (I-02, migration 0003) is implemented in the working tree (28 October 2026), not yet committed.
> - Stage 2a and 2b are blocked on the system-job design decision.
> - Stage 3a is partially implemented in the working tree (28 October 2026), not yet committed: snapshot inputs are read in record order and within the evaluated tenant. The evaluator version bump and the test-definition order are blocked (STA-REQ-032, TIM-ADR-04); the decisions required are set out in the [decision brief](backend-decision-brief-snapshot-versioning.md).
> - Published catalogue immutability (DAT-REQ-012, migration 0004) is implemented in the working tree (28 October 2026), not yet committed. The development database was upgraded to `0004` on 28 October 2026, after a verified backup.
>
> See the status rows below and [§6](#6-stage-2-gap-matrix). No other stage has started.
> **Date:** 27 October 2026.
> **Derived from:** [backend-architecture-audit.md](backend-architecture-audit.md). Finding IDs (I-, P-, M-, U-, G-) refer to that report.
> **Authority:** ZA-ARCH-001 v3.0 and the controlled ZA-* specifications, all still "Proposed for lock". A stage that depends on a governance decision (G-xx) must not start that part until the decision is recorded.
> **Relationship to the week plan:** [`backend/backend-build-plan.md`](../backend/backend-build-plan.md) is the current execution plan. This roadmap orders the architecture work by dependency. Where the two differ, reconcile them through the plan owner; this document does not edit the week plan.

## Contents

1. [Principles](#1-principles)
2. [Dependency order](#2-dependency-order)
3. [Stages](#3-stages)
4. [Finding → stage traceability](#4-finding--stage-traceability)
5. [Recommended sequence and parallel work](#5-recommended-sequence-and-parallel-work)
6. [Stage 2 gap matrix](#6-stage-2-gap-matrix)

---

## 1. Principles

- **Smallest correct change per stage.** No broad rewrite. Engines judged correct in the audit (rules, facts, applicability core, roll-up maths, ledger and Merkle, IPE derivation) are kept.
- **Migrations first.** Every schema, grant or policy change after Stage 1 is a reviewed, versioned migration with a tested downgrade. Nothing changes the schema at API start-up once Stage 1 lands (DAT-REQ-023/039).
- **Additive before destructive.** New tables and columns land first, with backfills that never fabricate history (DAT-REQ-024). Old columns stay as caches until reads have moved.
- **Prove, then enforce.** Guards land in report-only mode with tests, then switch to enforce. Each enforcement step is its own reversible release.
- **Versioned semantics.** Any change to how an evaluator hashes or computes results bumps its version, and the old version stays available to reproduce old results (STA-REQ-019/021).
- **Tests are the acceptance evidence.** Each requirement a stage claims needs a test that can falsify it (QA-REQ-001) and runs in CI on PostgreSQL. Skipped tests never count (QA-REQ-005).

---

## 2. Dependency order

```
Stage 0  Governance decisions (G-01…G-10) ── no code; runs alongside everything
Stage 9  Traceability and release gates   ── starts with Stage 1 and runs continuously

Stage 1  Migration foundation  ─┬─► Stage 2  Database isolation and immutability
                                │        │
                                │        ▼
                                ├─► Stage 3  Historical correctness (snapshot reproducibility)
                                │
                                ├─► Stage 4  Authorization completeness ─► Stage 5  Idempotency and API contract
                                │                                    └──► Stage 6  Identity and sessions
                                ├─► Stage 7  Evidence trust (keys, checkpoints, TSA)
                                └─► Stage 8  Events, workers and durable workflows
Stage 10 Residency and privacy foundations ── before a second cell or real customer data
Stage 11 Runtime-language execution        ── conditional on G-01
```

**Ordering notes:**
- **Stage 2 is split:** the RLS changes come first; revoking `UPDATE` on revision tables waits until Stage 3 has replaced the in-place updates.
- **Stage 7 cadence work:** checkpoints every 15 minutes and hourly verification need a scheduled worker. Stage 8 provides one; a minimal single-purpose worker is acceptable earlier.

---

## 3. Stages

### Stage 0 — Governance decisions (no code)

| Field | Content |
|---|---|
| Scope | Record G-01…G-10. Close DOC-CON-001…003 through ARCH 3.1, PRD 1.1, DOM 1.1, TRC 1.1. Record D-01 (ledger segments), D-02 (control-state vocabulary) and D-03 (proposed obligation wording) in ARCH 3.1. Approve ZA-ADR-001 so its baseline becomes `ACCEPTED*`. Confirm or replace the local specification copies (U-02). |
| Affected | Specification documents and ADR records, not code. Once Stage 9 lands, the machine-readable register at `governance/adr/` (path proposed in ZA-ADR-001 §repository layout). |
| Prerequisites | None. |
| Migration impact | None. |
| Risks | **G-01 (Go vs Python) is the largest open decision.** Postponing it is acceptable only if recorded as a deferred ADR with a trigger and a safe interim (ADR-REQ-012). |
| Tests | None. |
| Acceptance | Each G-xx has an ADR or a document revision with owner, status and date. The DOC-001 register lists the approved versions and hashes. |
| Rollback | Not applicable; superseded through ADR history (ADR-REQ-003/008). |

### Stage 1 — Migration foundation (M-01)

| Field | Content |
|---|---|
| Scope | 1. Configure Alembic (already a dependency) on the owner role. Write a hand-reviewed baseline equal to today's schema, and move `db/postgres_rls.sql` into versioned migrations. 2. Stop `create_all` and the RLS SQL at start-up on PostgreSQL; start-up only verifies the runtime role and the schema version. SQLite test runs may keep `create_all`. 3. Before stamping the development database: take a backup, then diff the live schema against the baseline, because `create_all` never altered existing tables, so drift is possible. 4. CI on disposable PostgreSQL: `upgrade head` from empty; downgrade-and-upgrade of the newest revision; a no-drift check (autogenerate empty); a generated check that every table carrying `tenant_id` has an RLS policy. 5. Make the deploy migration step run only reviewed migrations, with the migration credential mapped to that step alone. |
| Affected | `backend/alembic.ini`, `backend/migrations/**` (new), `backend/app/pg_bootstrap.py`, `backend/app/main.py`, `backend/db/postgres_rls.sql` (moved), `.github/workflows/ci.yml`, `.github/workflows/deploy-environment.yml`, `backend/tests/test_pg_bootstrap.py`, `backend/tests/test_migrations.py` (new), `docs/ci-cd.md` |
| Prerequisites | Agreement that Alembic is the tool (already named in the deploy workflow comment, DAT-REQ-023/039). A backup procedure for the development database. |
| Migration impact | Baseline plus `alembic_version` only; **no schema change**. Any drift found in step 3 becomes a separate, reviewed corrective migration. |
| Risks | Hidden drift between models and the live database. Tests that assume start-up DDL. The owner role needs CREATE on the schema; it must stay non-superuser. |
| Tests | QA-REQ-035 (fresh install, upgrade, representative data). The no-drift check. RLS-coverage generation. Start-up performs no DDL (assert by catching DDL on the runtime path). |
| Acceptance | On empty PostgreSQL 17, `alembic upgrade head` produces a `pg_dump --schema-only` equal (normalised) to today's `create_all` + RLS output. The API starts without DDL. CI gates are green. The deploy migration step is no longer the blocker (a hosting target is still required, G-10). |
| Rollback | Revert the code commit; the database keeps working, since the baseline changed nothing. `DROP TABLE alembic_version` undoes the stamp. |
| **Status** | **Implemented in the working tree, 27 October 2026. Not yet committed or run in GitHub Actions.**<br>• Scope items 1, 2, 4 and 5 are done.<br>• Item 3 is prepared but not performed: a read-only `status` of the development database reports an exact match with the baseline. The owner takes a backup, then runs `python -m app.migrate upgrade` ([database-migrations.md](database-migrations.md)).<br>• The prerequisite "agreement that Alembic is the tool" was taken from the existing dependency and the deploy-workflow note. Confirm it with the Data Architect. |
| **Acceptance** | **Met:** on empty PostgreSQL 17 the migrations produce a normalised `pg_dump --schema-only` equal to the old `create_all` + RLS output (382 statements). **Met:** the API starts without DDL. **Met:** the deploy migration step is no longer the blocker; G-10 still is. **Pending:** CI gates are green only in a local reproduction of the `backend-postgres` job (full suite 125 passed; `test_migrations` 16/16); a GitHub Actions run is still needed. Evidence: [audit §12](backend-architecture-audit.md#12-implementation-status). |
| **Deviations** | 1. The entry point is `python -m app.migrate`. It validates, holds the migration lock and runs in one transaction; raw `alembic upgrade`, `downgrade` and `stamp` are refused. "`alembic upgrade head`" in the acceptance row is met through this tool.<br>2. Adoption of an unversioned database is automated, but only on an exact catalogue match with `migrations/baseline_schema.json`; anything else is refused unchanged.<br>3. Revision 0002 adds `REVOKE ALL ON alembic_version FROM assure_app`, needed for migration correctness.<br>4. The deploy workflow gains a pre-migration backup step that fails closed until a target exists.<br>5. The runtime role is provisioned by the migration tool (formerly by start-up). |
| **Remaining** | The API still holds the owner credential at start-up, for read-only checks, account bootstrap and demo seed. Serving with the runtime role only depends on the Stage 2 system-job design. Reconcile the week plan through its owner. |

### Stage 2 — Database-enforced isolation and immutability (I-01, I-02, P-02)

| Field | Content |
|---|---|
| Scope | **2a. Remove the self-service bypass.** Delete `OR assure_system()` from every policy. Implement the four documented cross-tenant jobs (checkpoint over tenant heads, registry read, shadow-impact aggregates, impact re-evaluation) as `SECURITY DEFINER` functions with a fixed `search_path`, owned by a dedicated role, each returning only the columns and aggregates the job needs (DAT-REQ-027). The design choice is the Data Architect's. **2b. FORCE RLS** on every tenant table (DAT-REQ-003). Migrations and seeding run as a migration role allowed to bypass RLS, used only in the migration step; the runtime role never gets BYPASSRLS (DAT-REQ-004). **2c. Grants.** Revoke `UPDATE, DELETE` from the runtime role on immutable result tables: evaluations, control evaluations, snapshots, IPE evaluations, packages, attestations, checkpoints. Add append-only triggers to them. Grant column-level `UPDATE` only where a legitimate in-place write remains (for example `record_to_seq` closure, the ledger head, erasure fields). Revision tables follow after Stage 3. |
| Affected | New migrations; `backend/app/db.py` (`app.system` stamping removed); `backend/app/security.py` (`system_scope` replaced by calls to the definer functions); `routers/evidence.py:270`, `routers/platform.py:143`, `routers/rules.py:222, 297`; `backend/tests/test_rls.py` and a new generated privilege test |
| Prerequisites | Stage 1. A decision on the system-job design (Data Architect, Security). |
| Migration impact | Policy, grant and trigger changes; no data change. |
| Risks | A legitimate write path still doing `UPDATE` breaks. Mitigate with the write-path inventory in the audit (I-04), a report-only rehearsal (log denied statements in CI), and staging 2c after Stage 3 for revision tables. Definer functions widen privilege if over-broad; review each one. |
| Tests | As the runtime role: `SELECT set_config('app.system','on',true)` must not expose another tenant's rows. `UPDATE`/`DELETE` on each immutable table must fail. FORCE is present on every tenant table (generated). Cross-tenant read/write tests per table, including history tables (QA-REQ-019, DAT-REQ-028). |
| Acceptance | All of the above pass on PostgreSQL in CI. The full suite stays green. |
| Rollback | A migration downgrade restores the previous policies and grants. Release 2a, 2b and 2c separately. |
| **Status** | **2c, completed results: implemented in the working tree on 28 October 2026 (migration `0003`). Not yet committed, and not run in GitHub Actions.**<br>• 2a and 2b: not started, blocked on the system-job design decision ([§6](#6-stage-2-gap-matrix), A2–A4).<br>• Revision tables in 2c still follow Stage 3. |
| **Deviation** | §5 placed 2a/2b before 2c. 2c went first for the completed-result tables: it is fully specified and needs no design decision, while 2a/2b wait for one. It does not touch revision tables.<br>Also included:<br>• the privilege layer on the two tables that were already trigger-protected (ledger events, audit events)<br>• column guards on source-run completion and IPE anomaly disposition (IPE-INV-11, IPE-REQ-034), which the audit's I-02 evidence names |
| **Evidence** | [audit §12](backend-architecture-audit.md#12-implementation-status), Stage 2 section |

### Stage 3 — Historical correctness and snapshot reproducibility (I-03, I-04, P-03, P-14)

| Field | Content |
|---|---|
| Scope | **3a. Deterministic inputs.** Add stable `ORDER BY` to every query whose results feed a digest (test runs, test definitions, artifacts in `evaluate_control`). Bump `CONTROL_EVALUATOR_VERSION` and `ROLLUP_ENGINE_VERSION`, and keep the previous algorithms callable so pre-existing results reproduce under their stored version. **3b. Record time where missing.** Add a nullable `record_seq` to `evidence_ledger_event` and `evidence_ipe_evaluation`; backfill only where derivable from same-transaction outbox or audit records. **3c. Append-only status events** for exceptions, compensations, issues, legal holds, evidence conflicts, authority grants, occurrence fulfilment, connector certification and catalogue lifecycle. Each gets `tenant_id` where relevant, RLS, an append-only trigger, `record_seq`, actor and reason. Each transition writes an event in the same transaction; the existing column becomes a cache. **3d. Cutoff-aware reads** in `compute_snapshot` and `_evidence_admissible`: evidence lifecycle and conflict status as of the cutoff (`engines/assurance.py:67, 75`). Occurrence logic uses the snapshot's pinned evaluation time, not the clock (`:432`). **3e.** Reproduction compares the full snapshot hash. Where pre-migration history is missing, the dependency reports `REQUIRES_REVIEW` with the reason code chosen in G-07, never an invented history. |
| Affected | `backend/app/engines/assurance.py`, `engines/evidence.py`, `engines/ipe.py`, `engines/connectors.py`, `app/codes.py` (versions, reason code), routers `assurance.py`, `evidence.py`, `ai.py`, `catalog.py`; new migrations and backfill scripts; `backend/tests/` (new reproduction suite) |
| Prerequisites | Stage 1; Stage 2a/2b. G-07 approved. ZA-TIM-001, ZA-STA-001 and ZA-DAT-001 owners agree the event-table convention. |
| Migration impact | Additive tables and nullable columns, plus idempotent, resumable backfills (DAT-REQ-024). After this stage, Stage 2c revokes `UPDATE` on the status columns. |
| Risks | **Digest changes:** persisted `ControlEvaluation` rows deduplicate by digest, so a version bump yields new rows rather than matches, which is acceptable if documented. **Backfill gaps:** surfaced, never filled in. Event-table write amplification is small at current volumes. |
| Tests | For each kind of later change (conflict raised or resolved, compensation decided, exception decided, occurrence fulfilled, evidence invalidated or erased, IPE re-evaluated): snapshot → change → reproduce gives an identical hash. A row-order test: rewrite the table (`CLUSTER`/`VACUUM FULL` on a disposable cluster), then reproduce identically. Reconstruction after restore (TIM-REQ-029, QA-REQ-036). Segmentation at every dependency boundary (QA-REQ-013). |
| Acceptance | The reproduction suite passes on PostgreSQL. No governance status column is written outside its event writer: grep gate plus database grants after 2c. |
| Rollback | Reads switch back to the cache columns behind a flag. Migrations downgrade (dropping event tables loses only post-migration events; acceptable before production). The old evaluator versions remain. |
| **Status (3a)** | **Partially implemented in the working tree on 28 October 2026. Not committed; GitHub Actions not run. No migration and no engine version change.**<br>• **Done:** every multi-row snapshot input is read in record order (`record_seq`, then id; DAT-REQ-007, TIM-REQ-005) and within the evaluated tenant. Duplicate control evaluations resolve to the earliest. An old-versus-new engine comparison on the development data and the suite's data gave byte-identical output, so no stored result changes.<br>• **Blocked:** the evaluator and roll-up version bump in this row's scope (STA-REQ-032 requires an ADR, golden-vector regression and explicit snapshot versioning). Also the order of test definitions, which have no record order: changing it alters digests for multi-test policies (STA-REQ-032; TIM-ADR-04, the snapshot canonical manifest format).<br>• **Not 3a:** I-03(a)(b)(c) are 3d; the full-hash comparison is 3e.<br>• **Decisions required:** the [decision brief](backend-decision-brief-snapshot-versioning.md) (D1 to D4: test-definition order, versioning and reproduction policy, manifest format, evaluation instant) and their approvers. |
| **Correction** | Earlier versions of this roadmap called 3a fully specified. It is not. ZA-STA-001 STA-REQ-032 makes any roll-up or evaluator version change an ADR decision, and ZA-TIM-001 lists the snapshot canonical manifest format as an open ADR (TIM-ADR-04). |

### Stage 4 — Authorization completeness (P-01, I-08, I-09, I-10)

| Field | Content |
|---|---|
| Scope | **4a.** An operation registry: every route, reads included, declares an operation ID and a required permission (API-REQ-031, IAM-REQ-007). Extend the start-up check from mutations to all methods; keep an explicit public list. **4b.** Scoped, time-bound role assignments (tenant or entity scope, validity) replacing the JSON `roles` list. **4c.** Engagement-scoped external auditors (IAM-REQ-040) and narrowed `SERVICE` reads. **4d.** Refuse to start if `ZA_DEV_IDENTITY_HEADER=1` outside an explicit dev/test environment. **4e.** One strict request base model (`extra="forbid"`, server-owned fields excluded) for all 43 request models (API-REQ-014). **4f.** Move `/api/watermark` behind sign-in or reduce what it returns. |
| Affected | `backend/app/security.py`, `backend/app/main.py`, every `routers/*.py` (dependency declarations), `backend/app/models.py` plus a migration (role assignments, engagements), `backend/tests/test_authorization.py`, frontend clients if they send extra fields |
| Prerequisites | Stage 1 (new tables). G-08 (read policy). |
| Migration impact | Additive tables; data migration from `org_actor.roles`, kept as a read fallback for one release. |
| Risks | Over-restricting reads breaks pages; unknown-field rejection breaks clients that send extras. Run 4a in report-only mode first (log denials), then enforce. |
| Tests | Generated route × role matrix for every method. Engagement-scoping tests. Dev-header guard test. Unknown-field rejection per request model. |
| Acceptance | Start-up refuses any route without a declared operation and permission. The matrix test is green on PostgreSQL. |
| Rollback | Switch enforcement back to report-only; the migration downgrade keeps the JSON roles path. |
| **Status** | **4a, 4b and 4f partially implemented in the working tree on 2 November 2026 (migration `0009`). Not committed; GitHub Actions not run.** Evidence and gaps: [Stage 4 report](stage-4-read-authorization-and-scoped-role-registry-report.md).<br>• **Done:** operation registry over every method with an explicit public list; a declared requirement on every one of the 67 non-public GET routes (66 permission-guarded, plus `GET /me` as self-service); scoped, time-bound role assignments with history; TENANT_SECURITY_ADMIN role management; entity-scoped read resolution; the external-auditor allowlist; commit-time authority revalidation; `/api/watermark` behind sign-in.<br>• **Deviation:** enforced directly, not report-only first (decision of 2 November 2026).<br>• **Pre-push remediation (2 November 2026):** 4d dev-header start-up guard, 4e strict request models and the generated per-role read matrix are implemented ([report](pre-push-remediation-report.md)).<br>• **Not done:** 4c engagement objects and SERVICE narrowing (both need G-08). G-08 is still open: the read classes are an interim interpretation for the IAM Lead to confirm. |

### Stage 5 — Idempotency, concurrency and API contract (M-04, M-05, P-07, P-08)

| Field | Content |
|---|---|
| Scope | **5a.** An `Idempotency-Key` dependency for external mutations, writing `platform_idempotency_record` (key, request hash, stored result) in the same transaction as the mutation. Replay returns the stored result. Same key with a different request is refused (API-REQ-004/005). **5b.** ETag / `If-Match` on revisioned resources (API-REQ-006). **5c.** Export OpenAPI 3.1 and commit it, with a CI diff gate (API-REQ-001). **5d.** A `/v1` major version in URIs, keeping `/api` as an alias through the dual-support window (API-REQ-002; ARCH §16.3: six months). **5e.** A problem-details error format. |
| Affected | `backend/app/platform.py` or a new `app/idempotency.py`, routers (dependency), `backend/app/main.py`, migration (idempotency columns: response hash, status, expiry), `frontend/lib/api.ts` (send keys; version prefix), new `backend/openapi/` |
| Prerequisites | Stage 1; Stage 4a (operation IDs). |
| Migration impact | Additive columns on `platform_idempotency_record`. |
| Risks | Key scope and expiry policy; stored-response size; frontend changes. |
| Tests | QA-REQ-028: exact replay, concurrent replay, same key with a different request. Lost-update tests for `If-Match`. Contract diff in CI. |
| Acceptance | Every external mutation requires a key unless the contract documents an exemption (API-REQ-004). Replay tests pass. The OpenAPI diff gate is active. |
| Rollback | Run in accept-but-not-require mode; keep the `/api` alias. |

### Stage 6 — Identity and sessions (I-06, P-12, P-17, M-07)

| Field | Content |
|---|---|
| Scope | **6a.** A BFF or server-managed session: Secure, HttpOnly, SameSite cookie, CSRF protection, no bearer token in browser storage (IAM-REQ-018, SEC-REQ-020, ADR C-08). **6b.** OIDC Authorization Code + PKCE with the chosen identity provider (IAM-REQ-005); local passwords kept for the dev profile only. **6c.** Phishing-resistant MFA and step-up for privileged actions such as platform registry, certification and break-glass (IAM-REQ-006, API-REQ-019). **6d.** Zoiko support JIT grants and governed break-glass (IAM-REQ-022/024). **6e.** SCIM deprovisioning within the revocation SLO (IAM-REQ-015/016). |
| Affected | `backend/app/auth.py`, `backend/app/security.py`, `backend/app/models.py` plus a migration (session binding, grants), `frontend/lib/api.ts`, `frontend/lib/actor.tsx`, `frontend/app/login/page.tsx`, Next.js route handlers if BFF |
| Prerequisites | G-05 (session architecture, identity provider). Stage 4 (operation registry for step-up mapping). |
| Migration impact | Additive session and grant tables. |
| Risks | Cookie domain and CORS configuration; identity-provider outage handling; frontend rework. |
| Tests | No token in `localStorage`/`sessionStorage` after sign-in (browser test). CSRF rejection. Revocation within SLO. Step-up demanded and enforced at commit (IAM-REQ-036). |
| Acceptance | Sign-in works end to end through OIDC in a test identity provider. All privileged operations require step-up. The browser stores no bearer token. |
| Rollback | A feature flag keeps local sign-in in development. Cookie sessions and the old token can coexist for one release. |

### Stage 7 — Evidence trust hardening (I-05, P-05, P-06, P-09, P-11, P-16, M-11)

| Field | Content |
|---|---|
| Scope | **7a.** Key-purpose catalogue and separate, versioned signing keys for ledger checkpoints, evidence packages, content releases and AI policy bundles (EVD-REQ-012, SEC-REQ-026, ADR C-07). The current key becomes "retired, verify-only" under its existing key ID. **7b.** A KMS/HSM adapter (development file backend plus a production provider, G-06) and rotation runbooks (SEC-REQ-044). **7c.** A public trust registry of active and retired verification keys; the verifier requires a pinned key or the registry. **7d.** Scheduled checkpoints every 15 minutes (EVD-REQ-007), an RFC 3161 TSA client with token storage, verifier support for TSA tokens (EVD-REQ-008/029), and a WORM storage adapter (EVD-REQ-009). **7e.** Hourly incremental verification that opens an incident on mismatch (EVD-REQ-025). **7f.** Conformance vectors for canonical JSON and ledger hashing (EVD-REQ-004, ADR C-02). **7g.** Ledger segmentation, only after G-02. **7h.** Represent backup and immutable-copy limits for erasure (EVD-REQ-022). |
| Affected | `backend/app/engines/keys.py`, `engines/evidence.py`, `routers/rules.py`, `routers/ai.py`, `routers/evidence.py`, `backend/verifier/verify_package.py`, migrations (key-version columns, trust registry, TSA token storage), new worker entry point (or Stage 8 workers), `backend/tests/test_package_verifier.py` |
| Prerequisites | G-06 (KMS provider and key catalogue), G-02 (segmentation). A scheduler: a minimal worker or Stage 8. |
| Migration impact | Additive columns and tables. Existing signatures stay verifiable through the retired key. |
| Risks | Verification of already-issued packages (they embed the old key; keep it verify-only). TSA availability (report lag honestly, EVD-REQ-037). Key-handling mistakes during migration. |
| Tests | QA-REQ-016: tamper, reorder, deletion, wrong key, wrong proof, TSA, offline package. A key-separation test: a content-release key cannot validate a package. Checkpoint-lag metric test. |
| Acceptance | Each purpose signs with its own key ID. Checkpoint lag stays at or under 15 minutes in a soak test. The verifier validates TSA tokens and refuses an unpinned key unless explicitly allowed. |
| Rollback | A feature flag returns to the single signer while keeping the new keys verify-only; the TSA client can be disabled with `TSA_PENDING` honestly recorded. |

### Stage 8 — Events, workers and durable workflows (P-04, M-02, M-03, M-09)

| Field | Content |
|---|---|
| Scope | **8a.** An outbox relay worker publishing to a managed queue at least once, setting `published_at`, and exposing outbox-age metrics (EVT-REQ-003, DAT-REQ-031). **8b.** Envelope completion: CloudEvents fields plus valid time, aggregate version, causation ID and schema version (EVT-REQ-009/010/012/017). **8c.** A consumer inbox for idempotency, a dead-letter queue, and isolated replay (EVT-REQ-005/018/043). **8d.** A durable workflow runtime (managed Temporal per C-10 / G-09) for long-running flows: exception expiry and renewal, impact re-evaluation, connector schedules, checkpoint and verification schedules (WRK-REQ-001/002/003). Domain state stays in PostgreSQL. |
| Affected | `backend/app/platform.py`, new `backend/app/workers/**`, migrations (aggregate version counters, inbox), deployment manifests, `backend/tests/` (relay and consumer tests) |
| Prerequisites | Stage 1. G-09 (runtime and hosting). A queue product decision. |
| Migration impact | Additive columns (aggregate version) and inbox tables. |
| Risks | Operating new stateful systems (ADR-REQ-035). Duplicate delivery is expected and must be handled. |
| Tests | QA-REQ-029: crash and failure injection between commit and publish. QA-REQ-030: duplicates, out-of-order delivery, aggregate gaps. WRK golden workflow tests (WRK-REQ-050). |
| Acceptance | Every outbox row is published at least once; consumers are idempotent; outbox age is observable; workflows survive worker restarts. |
| Rollback | Stopping the relay loses nothing (rows remain). Workflows are versioned, and disabling a workflow type is reversible. |

### Stage 9 — Traceability and release gates (M-08), continuous from Stage 1

| Field | Content |
|---|---|
| Scope | **9a.** A machine-readable ADR register (ADR-REQ-024/025) in the repository. **9b.** A traceability matrix generated from the controlled-ID index (913 IDs; built for this audit from the source DOCX tables) plus test markers carrying requirement IDs (TRC-REQ-002…004, QA-REQ-002). **9c.** CI gates: each release-scoped MUST requirement has at least one falsifying test (QA-REQ-001); no skipped mandatory test (QA-REQ-005); test evidence records build, environment and fixture (QA-REQ-004); an unresolved required ADR blocks release (ADR-REQ-011). |
| Affected | New `governance/` folder (register, matrix), `backend/tests/` (markers), `.github/workflows/ci.yml`, `.github/scripts/` (gate scripts) |
| Prerequisites | G-03 for "accepted" statuses. The specification identity confirmed (U-02, G-04). |
| Migration impact | None. |
| Risks | Gate noise early on; run in report-only mode until coverage is meaningful. |
| Tests | Gate self-tests with known-good and known-bad fixtures (ZA-ADR-001 test families). |
| Acceptance | The matrix is regenerated in CI. The release gate lists uncovered release-scoped requirements and blocks when configured. |
| Rollback | Switch gates to report-only. |

### Stage 10 — Residency and privacy foundations (M-06, M-14, M-15), before a second cell or real customer data

| Field | Content |
|---|---|
| Scope | Transfer policy and transfer execution records (INV-07, TEN-REQ-011). Bitemporal entity-cell assignment and server-side routing (TEN-REQ-004/005/006). The privacy schema: processing records, retention policies, deletion executions with residual-copy state (ZA-PRI-001, PRI-REQ-026/031). |
| Affected | `backend/app/models.py` plus migrations, new engines for transfer and privacy, routers for entity provisioning |
| Prerequisites | Stages 1–3. G-10 (first cell). Privacy owner sign-off. |
| Migration impact | Additive tables. |
| Risks | Scope creep: deliver only what the first contracts need. |
| Tests | QA-REQ-020 (single writer during migration); deletion idempotency; legal-hold blocking (QA-REQ-026). |
| Acceptance | No cross-cell movement without a policy record; deletion leaves auditable residual state. |
| Rollback | Additive; downgrade migrations. |

### Stage 11 — Runtime-language execution (conditional on G-01)

| Field | Content |
|---|---|
| Scope | Only if ARCH-ADR-003 (Go) is accepted without a successor ADR. Port the authoritative evaluators and ledger services module by module behind the same API and database contract, proving equivalence with cross-language conformance vectors (Stage 7f) and the reproduction suite (Stage 3). If a successor ADR keeps Python, this stage is replaced by recording that decision and its consequences (ADR-REQ-020). |
| Affected | Depends on the decision. |
| Prerequisites | Stages 1–3, 7f and 9 (the conformance and reproduction evidence make a port verifiable). |
| Migration impact | None if the database contract is unchanged. |
| Risks | A large effort; divergence between the two implementations during transition. |
| Tests | Golden-vector and reproduction equivalence between implementations. |
| Acceptance | Identical outputs on all vectors and snapshots; one implementation authoritative at a time. |
| Rollback | Route back to the Python implementation per module. |

---

## 4. Finding → stage traceability

| Finding | Stage | Finding | Stage |
|---|---|---|---|
| I-01 RLS self-bypass | 2a | P-07 idempotency partial | 5a |
| I-02 immutable results mutable | 2c | P-08 API contract | 5c–5d |
| I-03 snapshot reproducibility | 3a, 3d, 3e | P-09 verifier trust | 7c–7d |
| I-04 in-place revision updates | 3c, then 2c | P-10 registry history | 3c |
| I-05 shared signing key | 7a–7c | P-11 ledger segments | 7g (after G-02) |
| I-06 bearer token in localStorage | 6a | P-12 authentication | 6b–6e |
| I-07 runtime language | 0 (G-01), 11 | P-13 temporal profiles | 3 (with the DOM 1.1 revision) |
| I-08 dev header guard | 4d | P-14 reproduce endpoint | 3e |
| I-09 unknown fields accepted | 4e | P-15 content signing key | 7a |
| I-10 public watermark | 4f | P-16 erasure limits | 7h |
| I-11 registry transitions | 3c | P-17 support JIT | 6d |
| P-01 read authorization | 4a–4c | M-01 migrations | 1 |
| P-02 FORCE RLS / isolation tests | 2b | M-02, M-03, M-09 events and workflows | 8 |
| P-03 bitemporal coverage | 3b–3c | M-04, M-05 idempotency, concurrency | 5 |
| P-04 outbox delivery | 8a–8c | M-06, M-14, M-15 residency, privacy | 10 |
| P-05 checkpoint cadence, TSA, WORM | 7d–7e | M-07 identity federation | 6 |
| P-06 key hierarchy | 7b | M-08 traceability | 9 |
| | | M-10 backup/restore semantics | 3 (tests), 7 (EVD-REQ-039) |
| | | M-11 conformance vectors | 7f |
| | | M-12, M-13, M-16 model gateway, projection, DB observability | later; outside this roadmap's Release-1 path |

---

## 5. Recommended sequence and parallel work

1. **Now:** Stage 0 decisions G-01, G-05, G-06, G-07, G-08 and G-09 in parallel. Start Stage 1, and Stage 9a/9b in report-only mode.
2. **After Stage 1:** Stage 2a/2b (isolation) first; it carries the highest severity. Begin Stage 3 at the same time (3a needs no schema change and can land first).
3. **Then:** Stage 3 completes, and Stage 2c (grants) follows. Stage 4 can run in parallel with Stage 3, since it touches different code.
4. **Then:** Stage 5 after Stage 4a. Stage 7a–7c (key separation) can run alongside Stage 5.
5. **Then:** Stage 6 once G-05 is decided. Stage 8 once G-09 is decided; Stage 7d–7e uses its workers.
6. **Before a second cell or real customer data:** Stage 10.
7. **Stage 11** only as G-01 dictates.

**Release-1 critical path:** Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 6 → Stage 7. Stages 5, 8 and 9 are required before general availability; they do not block the controlled customer-zero work that ARCH-ADR-011 describes.

---

## 6. Stage 2 gap matrix

Built on 28 October 2026 from the code, the audit and the controlled specifications (ZA-DAT-001, ZA-TIM-001, ZA-STA-001, ZA-IPE-001, ZA-EVD-001, ZA-IAM-001, ZA-SEC-001; all v1.0, "Proposed for lock"). **Status** says what was done, or exactly what blocks it. A requirement counts as verified only when its tests have run and passed.

### Work package A: database integrity and enforcement

| ID | Requirements | Code today | Gap | Proposed change; migration impact | Risk | Tests and acceptance | Status or dependency |
|---|---|---|---|---|---|---|---|
| A1 = I-02 | DAT-REQ-011, STA-REQ-019, APP-REQ-012, IPE-REQ-025, IPE-INV-11, IPE-REQ-034, EVD-REQ-002/009/026, DAT-REQ-035/036, API-REQ-019, SEC-REQ-029; DAT §13 | Engines insert completed results once and never update or delete them (write-path inventory, audit §12). But 0002 granted the runtime role `UPDATE`/`DELETE` on every table, and only the ledger and audit log had triggers | Immutability was a code convention: the runtime and owner roles could rewrite or delete completed results and a source run's version bindings | Migration 0003: revoke `UPDATE`/`DELETE` on 11 tables; column-level `UPDATE` kept for source-run completion and IPE anomaly disposition; row triggers that also stop the owner's `UPDATE`/`DELETE` (not its DDL; audit A10). No data change | A missed write path fails. Mitigated by the inventory and the full PostgreSQL suite; the downgrade restores 0002 exactly | `tests/test_immutability.py` (5): a generated privilege and trigger check over every table, runtime role refused, owner stopped, guarded columns, exact downgrade | **Implemented and verified** on disposable PostgreSQL 17. Not committed; GitHub Actions not run. Development database upgraded to `0003` on 28 October 2026, with a verified backup |
| A2 = I-01 | DAT-REQ-004, DAT-REQ-027, SEC-REQ-008/009, INV-04. DAT §12: tenant SQL needs transaction-local scope "except through explicitly audited privileged roles"; definer functions narrowly scoped, fixed `search_path`, non-login owner | All 41 policies end in `OR assure_system()`, which reads `app.system`, a setting the runtime role can change itself. Four `system_scope` jobs: ledger checkpoint (`routers/evidence.py:270`), organisation registry (`routers/platform.py:143`), shadow impact (`routers/rules.py:222`), impact re-evaluation (`routers/rules.py:297`) | Any SQL running as `assure_app` can read and write every tenant (**Critical**) | Remove `OR assure_system()`. Each job becomes a narrowly scoped `SECURITY DEFINER` function, or per-tenant work under transaction-local scope. Migration: policies, functions, one non-login owner role | Job behaviour changes; a definer function that is too broad widens privilege | As the runtime role, `set_config('app.system','on')` exposes nothing; each job's output unchanged; a privilege review of every function | **Blocked: the system-job design.** Which function per job, which owner role, and whether impact re-evaluation runs per tenant are for the Data Architect and Security to decide (Stage 2 prerequisite). ZA-DAT-001 fixes the pattern, not the per-job design |
| A3 = P-02 | DAT-REQ-003, DAT-REQ-028, QA-REQ-019 | `ENABLE`, not `FORCE`, on 41 tables. The owner role bypasses RLS, and API start-up seeds and bootstraps accounts as the owner | Owner-role SQL is not tenant-checked; isolation tests cover examples, not every path | `FORCE` on every tenant table; a seed and migration path the DAT §13 role model permits; isolation tests per table, including history tables | Seeding, account bootstrap and owner-side tests stop working until moved | A generated `FORCE` check; cross-tenant read and write per table | **Blocked:** needs A2's policy shape and the DAT §13 role model (non-login table owners; only a superuser can create a `BYPASSRLS` role) |
| A4 | DAT-REQ-004, SEC-REQ-004; DAT §13 (migration role: "no normal application use") | API start-up reads as the owner; the account bootstrap and demo seed write as the owner (`main.py`) | The running backend holds a DDL-capable credential | Move the bootstrap and seed to a one-off privileged job, and run the start-up checks through a minimal read path | Bootstrap semantics change | Start-up with only `DATABASE_URL` | **Blocked:** the same privileged-job decision as A2 |
| A5 | DAT §13: "no runtime role may TRUNCATE" | Never granted | None | None | None | Now asserted for every table by the generated test | **Verified** |
| A6 | DAT-REQ-014, EVT-REQ-001, TIM-REQ-020 | `platform.emit` adds the outbox row to the caller's transaction | Delivery side missing (P-04, M-03) | Stage 8 | None here | M-09 failure-injection tests | Atomic write **implemented**; delivery waits for Stage 8 (G-09) |
| A7 | INV-10, API-REQ-004/005/006, QA-REQ-028 | Idempotency keys for evidence capture and evaluations only; `platform_idempotency_record` unused; no `If-Match` | P-07, M-04, M-05 | Stage 5 | None here | Retry and concurrency tests | Not started; depends on Stage 4a operation IDs |
| A8 (found after the audit) | DAT-REQ-012; RUL-REQ-009/010/014/027; ZA-DAT-001: "Runtime role cannot update/delete … PUBLISHED catalogue artifacts" | Rule versions, content releases, set versions and fact definitions are inserted once, but the runtime role held `UPDATE`/`DELETE` and there were no triggers. Publication rewrote the new rule version in place to point at its release | Binding rules and signed release manifests could be rewritten in the database, and evaluation does not re-verify them (A9) | Migration `0004`: revoke and triggers. Publication and seed insert final values | The old publication code fails against the guard (shown); changed with it | `tests/test_immutability.py` (7), including an end-to-end publication and a failed publication that leaves no rows | **Implemented and verified** on disposable PostgreSQL 17. Development database upgraded to `0004` on 28 October 2026 (verified backup and rehearsal). Not committed; GitHub Actions not run. DAT-REQ-039 review sign-off pending |
| A9 (found after the audit) | APP-REQ-006; ZA-APP-001: "signature/release ID checked before input freeze" | Binding evaluation selects a rule version by `status = PUBLISHED` only; neither the AST hash nor the release signature is checked | Tampered or mismatched rule content would be evaluated without error | Verify the release signature and AST hash before input freeze, and fail as an integrity error, not a domain outcome | The verification key's trust and purpose separation (I-05, C-07, G-06) | Tamper and mismatch tests | **A9a implemented** in the working tree (29 October 2026, Task 3); not committed, GitHub Actions not run. Before input freeze, evaluation re-hashes the pinned SetVersions from their members and the AST under its declared versions, re-hashes the release manifest, and checks that the PUBLISHED, signed release lists exactly this RuleVersion. A failure refuses the whole operation with `RUL_SIGNATURE_INVALID` (no fallback, never UNCERTAIN) and commits `security.content_signature_failed` after the rollback. Evidence: [audit §12](backend-architecture-audit.md#task-3-rule-integrity-before-binding-evaluation-a9a). **A9b** (signature verification) stays **blocked**: no trust anchor is decided (G-06, RUL-ADR-06, C-07; ZA-KEY-001 does not exist). Until then, a consistent rewrite of a rule row, its manifest and the manifest hash is undetected. See the [signature-trust brief](backend-decision-brief-content-signature-trust.md) |
| A10 (found in the 28 October review) | ZA-DAT-001 §13: table owners are non-login roles, the migrator is not used by the application, and runtime roles are not owners; DAT-REQ-004; SEC-INV-08 | The `assure` role owns all 64 tables, applies migrations and is used by the API at start-up. As owner it can `ALTER TABLE … DISABLE TRIGGER`, so the immutability triggers of 0002 to 0004 stop its DML but not its DDL | Anyone holding the owner credential can rewrite completed results and the published catalogue | Non-login table-owner role; separate migrator; API without the owner credential | The role model, decided together with A2 (system jobs) | A probe showing the owner cannot disable the trigger | Open. Reproduced on a disposable copy (audit §12). **Needs the Data Architect + Security role decision** |

### Work package B: bitemporal history and reconstruction

| ID | Requirements | Code today | Gap | Proposed change | Status or dependency |
|---|---|---|---|---|---|
| B1 = I-03, P-14 | TIM-REQ-006/015, STA-REQ-019/020/021, QA-REQ-013 | `engines/assurance.py:67, 75-76, 112-115, 161, 432` | Reproduction depends on later events, the wall clock and SQL row order | Stage 3a: record-order reads and tenant predicates (output-preserving, so no version change). A version bump, with the old algorithm kept callable, only after an ADR (STA-REQ-032). Stage 3d: cutoff-aware reads | **3a partially implemented** (28 Oct 2026; [audit §12](backend-architecture-audit.md#12-implementation-status)). Remainder **blocked**: version bump and test-definition order (STA-REQ-032 ADR; TIM-ADR-04). 3d needs 3b and 3c |
| B2 = I-04 | DAT-REQ-009, TIM-REQ-007, WRK-REQ-013, IPE-REQ-034 | Exceptions, compensations, issues, legal holds, conflicts, authority grants, catalogue, connector certification and IPE anomaly dispositions are overwritten in place | Prior states are lost | Stage 3c append-only status events; then Stage 2c revokes `UPDATE` on those tables | **Blocked:** the event-table convention the ZA-TIM, ZA-STA and ZA-DAT owners must agree (Stage 3 prerequisite) |
| B3 = P-03 | TIM-REQ-001/004/005, DAT-REQ-007/024 | Full valid and record time on 3 table families only | Record time missing on ledger events, IPE evaluations, holds and others | Stage 3b: nullable `record_seq` plus a derivable-only backfill | **Blocked** where history is not derivable: G-07 reason code |
| B4 | DAT-REQ-010, TIM-REQ-021 | No exclusion constraints. Fact revisions may overlap by design (0002 comment) | Non-overlap is not enforced in the database where the spec requires one current value | Exclusion constraints per object class | Not started; needs the per-object rules from ZA-TIM-001 / ZA-DAT-001 |
| B5 = P-13 | DAT-REQ-008, TIM-REQ-003 | Fact revisions annotated with their profile; other valid ranges are not | The temporal profile is not physical | Tag every valid range with its profile | **Blocked:** DOC-CON-003 (G-03) |

### Work package C: security, sessions and signing keys

| ID | Requirements | Code today | Status or dependency |
|---|---|---|---|
| C1 = I-05 | EVD-REQ-012, SEC-REQ-026 | One Ed25519 key for every purpose, a plaintext PEM file | **Blocked:** G-06 (KMS/HSM, key-purpose catalogue). No key hierarchy invented here |
| C2 = I-06 | IAM-REQ-018, SEC-REQ-020/021 | Bearer token in browser `localStorage` | **Blocked:** G-05 (session architecture). The fix is also frontend work, outside this backend scope |
| C3 = P-12, M-07, P-17 | IAM-REQ-005/006/016/022/024 | Local passwords with scrypt, lockout and server-side revocation | **Blocked:** G-05 (identity provider) |
| C4 = I-08 | IAM-REQ-001, SEC-REQ-024 | The dev identity header is honoured whenever `ZA_DEV_IDENTITY_HEADER=1` | Not started (Stage 4); specified and small |

**Order of the remaining Stage 2 work, by dependency:**
1. The system-job decision, which unblocks A2.
2. A2.
3. A3 and A4, which need A2.
4. Stage 3 events (B2).
5. The Stage 2c revokes on revision tables.

B1/3a is partially done; its remainder needs the STA-REQ-032 ADR ([decision brief](backend-decision-brief-snapshot-versioning.md)). A8 is done. A9a is implemented (Task 3, not committed); A9b and A10 need decisions ([signature-trust brief](backend-decision-brief-content-signature-trust.md); role model with A2).
