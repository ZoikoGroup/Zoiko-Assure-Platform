# Zoiko Assure — Pre-Push Remediation Report

> **Date:** 2 November 2026. **Branch:** `naveen-changes`, HEAD `80e95db`, stash `stash@{0}` (`d9560bb`) preserved.
> **Scope:** remediation of the pre-push audit verdict "NOT SAFE TO PUSH" (same day).
> **Nothing was staged, committed or pushed.** The canonical `assure` database was not modified.
> **Governance:** every ZA-* specification is still "Proposed for lock". Implementation evidence below is not policy approval.

## Verdict

| Snapshot | Verdict |
|---|---|
| **The current index** (what a commit would contain today) | **NOT SAFE TO PUSH.** The index is unchanged. It still omits 43 required untracked files (including this report) and the newer version of 24 files, and on its own it fails: 32 passed, 59 errors (§10). Only staging can fix this, and staging needs your separate explicit authorization. |
| **The reconciled snapshot** (the exact file set in §1, as it is in the working tree) | **SAFE TO PUSH WITH DOCUMENTED LIMITATIONS.** Consistent and tested: SQLite 174 passed / 95 skipped (all PostgreSQL-only); PostgreSQL 17 on a fresh database 269 passed / 0 failed / 0 skipped; CI gate PASS; frontend type-check and build pass; secret scan clean; no unresolved critical security defect. The limitations in §13 are open policy decisions and known non-critical gaps, documented as such. |

A push should proceed only after the index is brought exactly to the reconciled snapshot, with nothing more or less, and after a GitHub Actions run of the CI gate. The local backend also needs `assure` upgraded to 0010 before it can start again (§12). That is a local-environment step, not a push blocker, and it needs your approval.

---

## 1. Original audit finding and reconciliation inventory

**Finding.** The staged set imported untracked modules: staged `routers/integration.py` imports `engines.connectors`, and staged `main.py` imports `app.migrate`. It also deleted `backend/db/postgres_rls.sql` without adding `backend/migrations/`, and CI required 7 untracked test modules. The staged-only snapshot failed: 32 passed, 59 errors. Newer edits to 21 staged files, plus 3 tracked-but-unstaged files, were outside the index.

**Reconciliation inventory, file by file.** Counts are as of the end of this remediation. Both versions of every file are preserved: the staged versions in the index (verified byte-identical to the audit export, 125 entries) and the newer versions in the working tree.

| Category | Files | Required action for a consistent commit |
|---|---|---|
| Staged, with newer unstaged edits (21) | `.github/workflows/ci.yml`, `backend/.env.example`, `backend/app/{auth,main,models,security,seed}.py`, `backend/app/engines/assurance.py`, `backend/app/routers/{ai,applicability,assurance,catalog,core,dashboard,evidence,integration,platform,rules}.py`, `backend/tests/{conftest,test_api}.py`, `frontend/lib/roles.ts` | Include the **working-tree** version. Each unstaged diff builds on the staged version: it revises staged lines from later work (Stage 3c, Stage 4, this remediation) and reverts none of them wholesale |
| Tracked, unstaged only (3) | `backend/app/accounts.py`, `backend/app/platform.py`, `frontend/lib/actor.tsx` | Include. `accounts.py` writes registry assignments; `platform.py` predates this session and is used by every engine; `actor.tsx` is the session-check timeout |
| Untracked: backend code (6) | `backend/alembic.ini`, `backend/app/migrate.py`, `backend/app/iam.py`, `backend/app/engines/connectors.py`, `backend/app/engines/governance.py`, `backend/app/routers/iam.py` | Include: imported by staged and working-tree code |
| Untracked: migrations (13) | `backend/migrations/env.py`, `script.py.mako`, `baseline_schema.json`, `versions/0001`–`0010` | Include. Without them there is no schema path, because `db/postgres_rls.sql` is deleted in the index |
| Untracked: tests (13) | `test_auth_session_security`, `test_authorization`, `test_governance_events`, `test_governance_projections`, `test_immutability`, `test_migrations`, `test_read_authorization_matrix` (+ `read_authorization_matrix.json`), `test_request_hardening`, `test_role_registry`, `test_rule_integrity`, `test_snapshot_determinism`, `test_tenant_security` | Include: `ci.yml` requires 13 test modules by name |
| Untracked: documentation (11) | `docs/backend-architecture-audit.md`, `backend-architecture-roadmap.md`, `backend-current-state-and-wireframe-gap-audit.md`, `backend-decision-brief-*.md` (2), `database-migrations.md` (referenced by the app's own error messages), `stage-4-…-report.md`, `task-0-…`, `task-2-…`, `task-4-2-…` reports, this report | Include. `database-migrations.md` is required; the others are the stage evidence |
| Staged only, working tree identical (42) | Frontend pages and components, `backend/app/{db,pg_bootstrap}.py`, engines `{applicability,evidence,ipe,rules}.py`, `README.md`, `ARCHITECTURE.md`, `docs/ci-cd.md`, CI scripts, the staged deletion of `backend/db/postgres_rls.sql` | Already correct in the index |

The reconciled snapshot was built from `git ls-files --cached --others --exclude-standard`, with working-tree content: 167 files. It excludes `backend/.env`, `backend/var/` and every other ignored file, and was tested on its own (§8).

## 2. Root causes

| Finding | Root cause |
|---|---|
| Staged snapshot broken | Work since Task 2 (Stage 3c, Stage 4, the migration tool) was written to the working tree and never staged; the index kept an intermediate state, including a deletion whose replacement (`migrations/`) was untracked |
| Projection tables writable and deletable | Migration 0008 hardened the new *event* tables only; the six existing state tables kept the table-wide `UPDATE, DELETE` that migration 0007 had moved to `assure_app_rw` |
| Legal-hold audit and outbox gaps | `place_legal_hold` and `release_legal_hold` wrote the event and projection but no `platform_audit_event`; the outbox call lived in the place route only |
| Baseline attribution | Migration 0008's backfill copied each record's own actor field and used `record_seq = 0` for three tables |
| Stage 4d/4e open | Not in Stage 4's requested scope; the dev header had no environment check, and 36 of 45 request models used pydantic's default (ignore extras) |
| Stage 3c report inaccurate | The report described intended policy, not the deployed SQL (PERMISSIVE policies) |

## 3. Exact files changed in this remediation

| File | Change |
|---|---|
| `backend/migrations/versions/0010_governance_projection_guards.py` (new) | Projection privileges and triggers (§4) |
| `backend/app/engines/governance.py` | Legal-hold audit records and outbox events, in the same transaction; `event_view()` provenance labelling |
| `backend/app/routers/evidence.py` | Hold routes: outbox moved into the engine; history endpoints use `event_view`; 4 request models strict |
| `backend/app/routers/assurance.py` | History endpoints use `event_view`; 12 request models strict |
| `backend/app/routers/{ai,applicability,catalog,core,integration,rules}.py`, `backend/app/auth.py` | 20 request models strict (`extra="forbid"`) |
| `backend/app/security.py` | `DEV_HEADER_ENVIRONMENTS`, `dev_header_violation()` |
| `backend/app/main.py` | Refuses to start when `dev_header_violation()` returns a reason |
| `backend/.env.example` | Documents `ZA_ENVIRONMENT` (commented placeholder) |
| `backend/tests/conftest.py` | `ZA_ENVIRONMENT=test` |
| `backend/tests/test_governance_projections.py` (new, 8) | Projection privileges, legal-hold tamper/delete/reopen, frozen final states, atomic hold audit/outbox with injected failure, provenance labels |
| `backend/tests/test_request_hardening.py` (new, 15) | Dev-header guard (decision and real start-up subprocess), generated strict-model check, behavioural 422s |
| `backend/tests/test_read_authorization_matrix.py` (new, 2) + `read_authorization_matrix.json` (new) | Generated per-role read matrix (19 principals × 67 operations) and the reviewed, PROVISIONAL policy table |
| `backend/tests/test_immutability.py` | The six projections join the reviewed GUARDED classification, with real completion and tamper probes |
| `backend/tests/test_authorization.py` | Probe-conflict cleanup disables the new guard inside its own transaction |
| `backend/tests/test_rule_integrity.py` | Removed an undeclared `reason` field from two rule-draft bodies (silently ignored before, refused now) |
| `.github/workflows/ci.yml` | `--require test_governance_events=10`, `test_governance_projections=8`, `test_request_hardening=15`, `test_read_authorization_matrix=2` |
| `docs/task-4-2-append-only-governance-events-report.md` | Corrected (§6); the original is kept outside the repository |
| `docs/stage-4-…-report.md`, `docs/backend-architecture-roadmap.md`, `docs/database-migrations.md` | Status rows for 4d, 4e and the matrix; revision 0010 row |
| `docs/pre-push-remediation-report.md` (new) | This report |

## 4. Migration 0010: security changes

Each of the six tables was checked against every write path in the application. `app/engines/governance.py` is the only writer, and no code deletes these rows. The lifecycle columns below are exactly what the event writers change.

| Table | Runtime `UPDATE` kept on | Terminal state frozen by `lifecycle_final` |
|---|---|---|
| `assurance_exception_revision` | `state`, `approved_by` | `REJECTED`, `REVOKED` (an `APPROVED` exception can still be revoked) |
| `assurance_compensation_revision` | `status`, `approved_by` | `REJECTED`, `REVOKED` |
| `assurance_issue` | `state`, `owner_actor_id` | `CLOSED`, `CANCELLED` |
| `evidence_legal_hold_revision` | `status`, `released_at` | `RELEASED` |
| `evidence_conflict` | `status`, `disposition` | `RESOLVED` |
| `assurance_obligation_occurrence` | `fulfilled_at`, `evidence_ids` | any non-null `fulfilled_at` |

On each table, 0010 makes these changes:

1. It runs `REVOKE UPDATE, DELETE … FROM assure_app_rw`, followed by `GRANT UPDATE (<lifecycle columns>)`.
2. It adds an `immutable_columns` trigger (`assure_forbid_mutation_except`, from 0003). Every other column is immutable and `DELETE` is refused, for every role including the owner.
3. It adds a `lifecycle_final` trigger, which calls the new function `assure_freeze_final` (owned by `assure_table_owner`, `search_path` pinned). The trigger is named to fire after the column guard, so tampering is reported as tampering.

0010 changes nothing else: INSERT and SELECT, FORCE RLS and the `tenant_isolation` policies are untouched, and there is no data change. The downgrade restores 0009 exactly, verified by `test_migrations` (newest-revision downgrade/upgrade fingerprint, and downgrade-to-baseline privileges).

**Legal-hold workflow:**
- **Placement** now writes, in one transaction, the projection, the `HOLD_PLACED` event, the `legal_hold.placed` audit record (actor, authority, reason) and the `evidence.hold_placed` outbox event, all with the same `record_seq`.
- **Release** now writes the `HOLD_RELEASED` event, the `legal_hold.released` audit record (`ACTIVE → RELEASED`) and the `privacy.legal_hold_released` outbox event (ZA-EVT-001), in the same way. Its payload carries identifiers only.
- **Ledger:** the route adds its ledger entry and commits once.
- **Atomicity:** an injected failure after the hold was written left every table unchanged.

## 5. Privilege verification, before and after

These are the runtime role's (`assure_app`) privileges as PostgreSQL reports them:
- **Before:** the live `assure` database at 0009, read-only.
- **After:** a restored copy of the same data upgraded to 0010 in a throwaway cluster.

| Table | Before: UPDATE / DELETE / updatable columns / triggers | After |
|---|---|---|
| `assurance_exception_revision` | yes / yes / all 11 / none | no / no / `approved_by, state` / `immutable_columns, lifecycle_final` |
| `assurance_compensation_revision` | yes / yes / all 11 / none | no / no / `approved_by, status` / both |
| `assurance_issue` | yes / yes / all 13 / none | no / no / `owner_actor_id, state` / both |
| `evidence_legal_hold_revision` | yes / yes / all 9 / none | no / no / `released_at, status` / both |
| `evidence_conflict` | yes / yes / all 7 / none | no / no / `disposition, status` / both |
| `assurance_obligation_occurrence` | yes / yes / all 9 / none | no / no / `evidence_ids, fulfilled_at` / both |

FORCE RLS stayed on for all six, before and after.

**Rehearsal on real data.** A read-only dump of `assure` (73 tables, 664 rows, digest `852e5e9a1ad40e87`) was restored into the throwaway cluster and upgraded 0009 → 0010:
- `check` passed, and only `alembic_version` changed.
- The API started on the copy. Your existing users placed and released a legal hold (200, 200) and moved an issue to `TRIAGED` (200).
- Existing baseline history was labelled `MIGRATION_BASELINE`.

**Direct PostgreSQL tests as the actual runtime role** (`test_governance_projections`, `test_immutability`):
- `DELETE` and protected-column updates are refused with "permission denied".
- Lifecycle updates are allowed inside the tenant scope.
- A released hold or a resolved conflict cannot be reopened ("is final once …").
- Another tenant's update affects 0 rows.
- The owner role is refused by the triggers.

## 6. Stage 3c compliance status: partially complete

| Item | Spec requires | Code does now | Gap | Recommendation |
|---|---|---|---|---|
| Append-only event history | DAT-REQ-009/037; STA-REQ-027 | Six event tables: append-only trigger, no runtime `UPDATE`/`DELETE`, FORCE RLS | Owner can disable triggers with DDL (A10) | Non-login owner and separate migrator decision (Data Architect + Security) |
| State projections | Roadmap Stage 3 acceptance: "no governance status column is written outside its event writer … database grants" | **Remediated (0010):** lifecycle columns only, no `DELETE`, final states frozen | A writer could still change a lifecycle column without writing the event (no event-coupling check) | Optional: a deferred constraint trigger requiring a matching event in the same transaction |
| Legal holds | EVD-REQ-018; ZA-EVT-001; IAM §21 | **Remediated:** audit and outbox for place and release, atomic | `release_legal_hold` releases an arbitrary ACTIVE hold when an evidence item has several (no hold ID in the API) | Release by `hold_id` (an API change; needs agreement) |
| Tenant isolation | SEC-REQ-008; DAT-REQ-003 | FORCE RLS; PERMISSIVE `tenant_isolation` | Policies are PERMISSIVE: correct today, but an added permissive policy would widen access | Consider RESTRICTIVE in a reviewed migration |
| Baseline attribution | DAT-REQ-024 (never fabricate history) | Rows unchanged (append-only). Real data: 4 baseline events; 1 exception baseline attributed to its requester for state `APPROVED`; 1 issue baseline with `record_seq = 0`. API labels every BASELINE event `MIGRATION_BASELINE`, `recorded_by: system:migration:0008`; a test proves the application never writes BASELINE | Stored `actor_id`/`record_seq` remain as 0008 wrote them | **Needs approval:** an append-only correction record (e.g. a provenance table) if the stored rows must state the true recorder |
| Remaining 3c scope | Roadmap 3c | — | Authority grants, connector certification and catalogue lifecycle have no event tables | Separate stage work |
| 3d / 3e | Roadmap | — | Cutoff-aware reads; full-hash reproduction | Blocked on G-07 and TIM-ADR-04 decisions |
| Report accuracy | — | `task-4-2` report corrected: RESTRICTIVE claim withdrawn, "Gap: None" replaced, baseline description corrected, §5 guarantees qualified, status changed to PARTIALLY COMPLETE | — | — |

## 7. Stage 4 compliance status: partially complete, not complete

| Item | Status | Basis |
|---|---|---|
| 4a operation registry, every route guarded or explicitly public (9 public, each with a reason) | Implemented | API-REQ-031, IAM-REQ-007 |
| 4b scoped role registry, append-only history, audit, immediate revocation, commit-time revalidation | Implemented | IAM §02/§22, IAM-REQ-010/014/015/036 |
| Tenant and legal-entity scope, 404 masking | Implemented | IAM-REQ-009/010, ZA-API-001 error model |
| Auditor allowlist (`audit_read`), Super Admin boundaries | Implemented, **provisional** | IAM §14 resource classes, pending G-08 |
| **4d dev-header start-up guard** | **Implemented in this remediation.** Allowed only when `ZA_ENVIRONMENT` is `development` or `test`, the marker the deploy workflow already sets; unset or anything else refuses to start (tested in a real start-up subprocess) | IAM-REQ-001, SEC-REQ-024 |
| **4e unknown-field rejection** | **Implemented.** All 45 request-body models refuse unknown fields and none exposes a server-owned field. The frontend's request bodies were checked first: every one is a subset of its model | API-REQ-014, SEC-REQ-022 |
| **Generated per-role read matrix** | **Implemented.** 19 principals × 67 GET operations: each is refused exactly when the declared requirement says so, with every refusal recorded; the role → operations table is reviewed in `read_authorization_matrix.json` (PROVISIONAL) | Roadmap Stage 4 test row; IAM §07 semantic diff |
| `SERVICE` read narrowing | **Not done: needs G-08.** IAM §15 requires "exact service/API actions" and SEC-INV-06 limits a connector to its scoped resources, but no controlled document lists which read operations a workload needs. Proposal for the IAM Lead: `SERVICE` keeps reads its actions need (`/ai/policy-bundle/{id}`, IPE policies and source runs, connectors) and loses the audit log, outbox, people directory, organisation profile, facts and rule drafts | IAM §15, SEC-INV-06 |
| External-auditor engagement records | **Not done: blocked.** IAM §14/§22 define the object, but creating engagements belongs to AUDIT_MANAGER (absent from the code; PRD/IAM conflict), resource selection is G-08, and authentication is IAM-ADR-07 | IAM-REQ-027/040 |
| Provisional interpretations | `delegate_admin`, `audit_read`, TENANT-only security admin, no back-dated `valid_from`: documented, **not approved** | Stage 4 report §3 |

## 8. Test results (all executed)

| Run | Result |
|---|---|
| SQLite, full suite, working tree = reconciled snapshot | **174 passed, 95 skipped** (all PostgreSQL-only), 0 failed |
| PostgreSQL 17.11, full suite, fresh disposable database, working tree | **269 passed, 0 failed, 0 skipped** |
| PostgreSQL 17.11, full suite, fresh disposable database, **reconciled snapshot export** | **269 passed, 0 failed, 0 skipped** |
| CI gate (`junit_gate.py --no-skips`, every `--require` in `ci.yml`) on both PostgreSQL runs | **PASS** |
| Gate detail | rls 5, pg_bootstrap 8, migrations 16, immutability 7, snapshot_determinism 4, rule_integrity 19, tenant_security 49, auth_session_security 10, role_registry 16, governance_events 10, governance_projections 8, request_hardening 15, read_authorization_matrix 2 |
| Also inside the PostgreSQL run | test_authorization 22, test_api 15, test_platform 6, test_engines 27, test_package_verifier 26, test_database_config 4 |
| Migration and model consistency, RLS coverage, upgrade/downgrade/re-upgrade | Pass (`test_migrations`, including the newest revision, now 0010) |
| Real-data rehearsal of 0010 | Pass (§5) |

Iterations during the work, all fixed and rerun:
- Two test helpers assumed no earlier legal holds on the evidence.
- Two rule tests sent an undeclared field.
- The trigger order initially reported "is final" before "append-only except".
- One immutability probe set a timestamp equal to its own insert time, so nothing changed.

None was a product defect except the trigger order, which was corrected.

## 9. Frontend results

| Check | Result |
|---|---|
| `tsc --noEmit` (isolated copy of the working tree) | Exit 0, no diagnostics |
| `next build` (isolated copy; never in the running dev server's `frontend/.next`) | Pass: compiled, 33/33 pages |
| Lint | **Not run: not configured.** The `lint` script exists, but ESLint isn't installed and there is no config |
| Running dev server (5173) | Still serving (200) after the builds |

## 10. Staged-only test

The index was not changed: all 125 entries are byte-identical to the pre-push audit export. Re-run on that snapshot: **32 passed, 59 errors** (`ImportError: cannot import name 'connectors' from 'app.engines'`). The index can't become consistent without staging, which you have not authorized. The reconciled snapshot (§1) is the tested alternative.

## 11. Secret scan

I scanned 191 files: the index blobs, the 42 untracked files that existed before this report was written, and 24 unstaged edits. The scan compared them against the 8 real secret values in `backend/.env` and `backend/var/keys`, plus the PostgreSQL password supplied earlier in chat. Values were compared, never printed. It also checked private-key, cloud-key, token, JWT and URL-password patterns.

**Hits: none.** `backend/.env`, `backend/var/`, the frontend `.env.*.local` files and `.next/` are git-ignored, and the tracked `.env.example` templates hold placeholders only.

Whitespace and conflict markers:
- `git diff --cached --check` and `git diff --check`: 0 issues.
- Conflict markers: none.
- Untracked files: 1 trailing space in `0002_…py`, kept because it is a byte-for-byte copy of the old RLS SQL, and Markdown line breaks in the corrected Stage 3c report.

## 12. Database and Git preservation

**Database:**
- **`assure` was not migrated, reset, seeded or written by this remediation.** It is at revision **0009**, where it was put earlier the same day at your explicit request, after a verified backup.
- Actions on it: read-only revision checks, read-only privilege queries and one read-only `pg_dump --enable-row-security` into the session scratch folder for the rehearsal.
- No role, password or RLS policy was changed.
- **Consequence:** the code head is now 0010, so the local API refuses to start (`SchemaVersionError: … at revision 0009; pending 0010`). That is the intended start-up guard. To run it locally again: take a verified backup, then run `python -m app.migrate upgrade`. This needs your approval; the rehearsal on a copy of your data passed.
- **Runtime role:** the API still connects as `assure_app` (least privilege), and the migration URL is not visible to the API process.
- **Test databases:** all PostgreSQL testing used fresh databases (`assure_test_remed1`–`4`, `assure_rehearsal10`) in a throwaway PostgreSQL 17 cluster in the session scratch folder, now stopped. `assure_test` on 127.0.0.1:5433 was not used, dropped or recreated.

**Git:**
- Branch `naveen-changes`, HEAD `80e95db`, stash `d9560bb`, 63 staged paths: all unchanged. The index is byte-identical (§10).
- **Read-only git commands only:** `status`, `diff`, `ls-files`, `hash-object --no-filters` (no `-w`), `rev-parse`, `grep`, `branch --show-current`.
- **Not run:** add, commit, push, pull, stash, reset, restore, clean, checkout, merge or rebase.
- **Working-tree changes:** edits to existing files and new untracked files only.

## 13. Remaining policy decisions and limitations

| # | Item | Owner / decision |
|---|---|---|
| 1 | **G-08** read-authorization policy: confirm or replace the read classes, the auditor allowlist and the reviewed read matrix; decide `SERVICE` reads | IAM Lead |
| 2 | PRD §4 personas vs IAM §08 role templates (role vocabulary conflict) | IAM Lead + PRD owner (ZA-DOC-001 §01) |
| 3 | External-auditor engagements (IAM-REQ-027/040): object, sponsor/AUDIT_MANAGER, expiry; IAM-ADR-07 | IAM Lead, General Counsel |
| 4 | Provisional Stage 4 interpretations: `delegate_admin`, `audit_read`, TENANT-only security admin, no back-dated assignments | IAM Lead (confirm) |
| 5 | Migration 0008 baseline attribution: approve, or decline, an append-only forward correction | Data Architect |
| 6 | RESTRICTIVE vs PERMISSIVE tenant policies | Data Architect + Security |
| 7 | A10: owner can disable triggers; signed `assure_system()` bypass path (I-01 remainder) | Data Architect + Security |
| 8 | Legal-hold release targets an arbitrary ACTIVE hold when several exist | API Lead |
| 9 | Remaining Stage 3c scope (authority grants, connector certification, catalogue lifecycle), Stage 3d/3e | Backend; G-07, TIM-ADR-04 |
| 10 | Bearer token in browser `localStorage` (IAM-REQ-018, Stage 6) | Security Architecture |
| 11 | Frontend lint not configured | Frontend Lead |
| 12 | GitHub Actions run of the extended CI gate | Requester, after authorizing staging and pushing |
| 13 | Upgrade of the canonical `assure` database to 0010, so the local API starts | Requester (backup first) |

## 14. Addendum, 3 November 2026: `backend-unit` gate

**Finding.** §8 ran the CI gate on the PostgreSQL reports only. Run on the SQLite report as `ci.yml` does (`--allow-skip-reason PostgreSQL-only`), the `backend-unit` gate **failed**: `test_governance_events::test_governance_event_tables_enforce_database_trigger_immutability` skipped with the reason "PostgreSQL trigger enforcement only", and the gate accepts exactly "PostgreSQL-only" (`docs/ci-cd.md`).

**Fix.** The skip reason in `backend/tests/test_governance_events.py` now reads `PostgreSQL-only`, like every other PostgreSQL-only test. The test still executes on PostgreSQL, so `--require test_governance_events=10` still applies.

**Re-verification.** The reconciled snapshot (168 files, working-tree content, including this fix) was re-exported and every `ci.yml` job was re-run on it:

| Job | Result |
|---|---|
| `backend-unit`: SQLite suite + gate | 174 passed, 95 skipped (all `PostgreSQL-only`); **gate PASS** |
| `backend-postgres`: fresh PostgreSQL 17.11 cluster, CI bootstrap SQL, migrations status/upgrade/upgrade/status/check, RLS gate, full suite + gate | Migrations to 0010, check passed; RLS 5/5; **269 passed, 0 failed, 0 skipped; gate PASS** (every `--require` met) |
| `frontend`: `tsc --noEmit`, `next build` (isolated copy) | Pass |
| `security`: `repo_hygiene.py` | PASS (168 paths); real `backend/.env` and key values: no hits |

The index was still not changed. A consistent commit still needs every path in the reconciled snapshot staged (§1).
