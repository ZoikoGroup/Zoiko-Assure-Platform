# Zoiko Assure — Local Database Upgrade to Revision 0010

> **Date:** 3 November 2026. Times are local (UTC+05:30).
> **Database:** `assure@127.0.0.1:5433/assure`, the local development database. The migration role is `assure`; the runtime role is `assure_app`.
> **Code:** branch `naveen-changes` at `f5220e9`, head revision 0010.
> **Scope:** back up the local database, then upgrade it from 0009 to 0010 so the backend starts again.
> **Not done:** no Git operation, no application code change, no reset, drop, truncate or seed, and no manual SQL from the migration file.
> **Governance:** every ZA-* specification is still "Proposed for lock". This report is implementation evidence, not policy approval.
> **Specifications read** (Zoiko Assure Volumes 02–05, extracted text; ZA-STA-001 for STA-REQ-027):
> - ZA-DAT-001 v1.0: §12 RLS, §13 roles, §20 migrations, §22 backup/restore, §29 verification, §30 DAT-REQ-001..040
> - ZA-SEC-001: SEC-REQ-008/022/023/024/029/036
> - ZA-EVD-001: EVD-REQ-018/020/022/039
> - ZA-PRI-001: PRI-REQ-021/022/032
> - ZA-QA-001: QA-REQ-026/036
> - ZA-API-001: API-REQ-014
>
> Every "Spec requires" line below quotes or paraphrases that text, not code comments. **This revision (same day) corrects the first version, which took its requirement IDs from code comments: one was wrong, and the backup was called "verified" beyond what the specifications accept (§2, §9).**

## Outcome

| Item | Result |
|---|---|
| Initial revision | **0009**, with only 0010 pending |
| Backup | `backend/var/backups/assure-before-0010-20261103-105109.dump`, plus the evidence-store copy `var-before-0010-20261103-105109/`. **Physically verified:** the archive is readable, and a restore of it is content-identical to the live database in every row. **Not yet spec-complete:** semantic reconstruction (DAT-REQ-030, QA-REQ-036) and the integrity checks of SEC-REQ-036 and EVD-REQ-039 were not run, and the backup is not encrypted (DAT §22). See §2 |
| Migration applied | **0010 only** (`0010_governance_projection_guards`), in one transaction, 10:53:32–10:53:36 |
| Final revision | **0010**: `status` CURRENT, `check` passed, schema fingerprint identical to a fresh 0001→0010 build |
| Privileges and triggers | Match 0010 on all six governance tables |
| RLS | Unchanged: 52 RLS tables, all FORCE; 116 policies |
| Data | Unchanged by the migration: 73 tables, 664 rows, the same per-table content digests |
| Backend | Running; `GET /api/health` returns **200**; authentication and scope checks pass 17 of 17 |

---

## 1. Initial database revision

**Verification.**
- `python -m app.migrate status` returned `assure@127.0.0.1:5433/assure: at revision 0009; pending 0010 (head 0010)`, exit 1 (`BEHIND`).
- Re-run immediately before the upgrade, it gave the same result, and no other session was connected to `assure`.
- The backend was not serving: the `uvicorn --reload` supervisor was running, but its worker had stopped on `SchemaVersionError`.

**Code does.** `python -m app.migrate upgrade` does more than apply revisions (`backend/app/migrate.py`):

| Step | Effect |
|---|---|
| `ensure_runtime_role` | Re-states `assure_app`'s `LOGIN` and password from `DATABASE_URL`, and its `assure_app_rw` membership |
| `provision_context_key` | Rewrites the database copy of the context key only if the key file changed |
| `provision_data` | Creates any bootstrap account from `backend/.env` that does not exist yet, and seeds demo data only when `ZA_DEMO_MODE=1` |

**Verification (read-only, before the upgrade).** Each step was confirmed to be a no-op on this database:

| Check | Result | Consequence |
|---|---|---|
| `assure_app` authenticates with the `DATABASE_URL` password | Yes | The role is re-stated with the password it already has |
| Context key in the database = key file | Yes (`456e1c5f20890680`) | No rewrite |
| Both bootstrap accounts and their tenant exist | Yes | No account is created (the tool then reported "already exists, left unchanged" for both) |
| `ZA_DEMO_MODE` | `0` | No seeding |
| Role attributes | `assure`: LOGIN, CREATEROLE, not superuser, NOBYPASSRLS. `assure_app`: LOGIN only, NOBYPASSRLS, owns no relation | Least privilege, as the specs require: DAT-REQ-004 (no BYPASSRLS, SUPERUSER, DDL ownership or standing cross-tenant privilege), DAT-REQ-027 (least privilege and fixed safe `search_path` for every role), SEC-REQ-023 (no database superuser or BYPASSRLS for runtime identities) and DAT §13 (no CREATEDB, CREATEROLE or schema ownership) |

## 2. Backup and verification

**Spec requires.**

| Source | Requirement |
|---|---|
| ZA-DAT-001 §22, "Backup, restore and data-integrity contract" | PITR to the architectural RPO. **Encrypted backups** with strict regional/cell access. Restore testing in an isolated environment that validates **migrations, RLS, temporal reconstruction, outbox and evidence refs**. Logical-integrity checks (FK/constraint, temporal overlap, catalogue hash, snapshot dependency, evidence reconciliation). Postgres and object-storage reconciliation. *"Restore must prove meaning, not just bytes"*: representative historical applicability evaluations and assurance snapshots must reproduce their original inputs and results, and evidence manifest references must reconcile |
| DAT-REQ-030 | Backup/restore tests MUST validate semantic reconstruction, not only physical restore success |
| ZA-DAT-001 §29 | Backup/restore: restore, then replay historical evaluation/snapshot verification and evidence reconciliation |
| QA-REQ-036 | Backup/restore exercises MUST prove semantic reconstruction of selected applicability, assurance and evidence references, not only database availability |
| SEC-REQ-036 | Backup/restore exercises MUST verify security policy, tenant isolation, content signatures and evidence/assurance integrity after recovery |
| EVD-REQ-039 | Restored environments MUST verify ledger heads, checkpoints, representative HMAC/object references and erasure tombstones before customer evidence access is enabled |
| PRI-REQ-032 | Backup restores MUST remain quarantined until current erasure directives, active holds, restrictions, tenant routing and security controls are replayed/reconciled and verified |

The repository procedure (`docs/database-migrations.md`, "Local development") adds:
- Stop the backend.
- Back up the database **and** the `backend/var` evidence store together, because payloads can't be decrypted without their keys.
- Use `pg_dump --format=custom --enable-row-security` as the owner role. From 0007, a dump taken without `--enable-row-security` is incomplete.
- `pg_restore --list` must print the archive header, or the backup is unusable.

**Code does.** There is no backup command in `app.migrate`; the backup is the documented manual procedure. The password reached `pg_dump` only through its own `PGPASSWORD` environment and was never printed or written.

**Verification.**

| Check | Result |
|---|---|
| Command | `pg_dump -h 127.0.0.1 -p 5433 -U assure -d assure --format=custom --enable-row-security`, exit **0**, no stderr |
| File | `backend/var/backups/assure-before-0010-20261103-105109.dump`: **358,326 bytes**, written 2026-11-03 10:51:10 |
| SHA-256 | `bca70ea67bb49f164856027ca2fa600f2ee96e832550c50766d69638e897cf80` |
| `pg_restore --list` | Exit 0. Header: `dbname: assure`, created 2026-11-03 10:51:09, custom format, gzip, 633 TOC entries, PostgreSQL 17.11 |
| Archive contents | 73 tables, 73 table-data sections, 116 policies, 23 triggers, 52 row-security entries. All equal to the live database |
| Evidence store | `backend/var/backups/var-before-0010-20261103-105109/` (`keys`, `objects`, `packages`, `worm`: 15 files), the same layout as the earlier `var-before-*` copies |
| **Restore test** | Restored into a throwaway PostgreSQL 17 cluster in the session scratch folder (roles mirrored from the dev server), with `--single-transaction --exit-on-error`: exit 0, no errors |
| **Content comparison** | Restored copy compared with the live database: **identical** in every row of all 73 tables (per-table row count and an order-independent digest of every row), 664 rows, data digest `2433d962e0c61903`. Also identical: revision, RLS flags, 116 policies, 23 triggers, 750 table and 1,385 column privileges, 11 functions, roles and 12 memberships |
| Existing backups | All preserved; nothing was overwritten (the script refuses an existing target) |

The backend served no requests between the backup and the upgrade, and no other session was connected. The "stop the backend" precondition was therefore met without stopping your supervisor process.

**Against the specification**, control by control:

| Control | Status | Basis |
|---|---|---|
| Restore into an isolated environment | Done | Throwaway cluster in the session scratch folder, deleted afterwards |
| Migrations validated on the restore | Done | Rehearsed 0009→0010, `check` passed (§3) |
| RLS and security policy on the restore | Done (configuration) | RLS flags, 116 policies and all grants identical to live; tenant isolation was not exercised by requests on the copy |
| Byte and row identity | Done | Every row of all 73 tables identical |
| FK and constraint integrity | Implicit only | Every constraint was rebuilt and validated by the restore, which raised no error. No separate logical-integrity run |
| **Temporal reconstruction, and replay of historical evaluations and snapshots** (DAT §22, §29, DAT-REQ-030, QA-REQ-036) | **Not done** | Rows are identical, so the inputs are identical, but no result was reproduced. The code offers `GET /snapshots/{id}/reproduce`; **no endpoint replays a historical applicability evaluation**, so that half cannot be demonstrated yet |
| **Content signatures, ledger heads, checkpoints, HMAC/object references** (SEC-REQ-036, EVD-REQ-039) | **Not done** | `POST /ledger/verify`, `POST /evidence/{id}/verify`, `GET /releases/{id}/verify` and `POST /packages/{id}/verify` exist but were not run on the copy. The evidence-store copy was not reconciled against the restored manifests |
| Outbox validation | Not done beyond row identity | — |
| Erasure tombstones, restore quarantine (PRI-REQ-032) | Not applicable to this run | The copy never served customers and was deleted. Required if this backup is ever restored for use |
| **Backup encryption** (DAT §22) | **Not met** | The custom-format dump is compressed, not encrypted. The evidence-store copy holds the key files (`master.key`, `session.key`, `db_context.key`, the cell signing key) unencrypted, next to the payloads they protect |
| PITR | Not applicable locally | Local development server; a production-cell control |

**Conclusion.** The backup is a sound physical, content-identical copy, and it was safe to rely on for this local upgrade, because the migration changed no data and a restore was not needed. By ZA-DAT-001 §22, DAT-REQ-030 and QA-REQ-036, the restore test is **not complete**: "restore must prove meaning, not just bytes".

## 3. Migration applied

**Rehearsal first** (on the restored copy, in the throwaway cluster):
- **Commands:** `status` returned exit 1 (0009, pending 0010); `upgrade` returned exit 0; `status` then returned exit 0 (0010); `check` passed.
- **Diff against the live before-state:**
  - Data is identical.
  - RLS, policies, roles and memberships are identical.
  - Changes: `alembic_version` 0009 → 0010; +1 function (`assure_freeze_final`); +12 triggers; −12 table privileges; −48 column privileges.

  These are exactly the objects 0010 declares and nothing else.

**Live upgrade.** `python -m app.migrate upgrade`:
- **Output:**
  ```
  assure@127.0.0.1:5433/assure: before: at revision 0009; pending 0010 (head 0010)
  assure@127.0.0.1:5433/assure: after:  at the head revision 0010
  assure@127.0.0.1:5433/assure: account bootstrap: SUPER_ADMIN …: already exists, left unchanged
  assure@127.0.0.1:5433/assure: account bootstrap: TENANT_OWNER …: already exists, left unchanged
  ```
- **Result:** exit 0, from 10:53:32 to 10:53:36.

**Verification.**
- **Only 0010 ran:** the before-state shows one pending revision, and `alembic_version` moved from 0009 to 0010.
- **The live result is identical to the rehearsed result** on every compared aspect: data, RLS, policies, triggers and their enabled state, table and column privileges, functions, roles and memberships.
- **No data was deleted or truncated:** all 73 tables keep the same row counts and content digests (664 rows, `2433d962e0c61903`).

## 4. Final database revision

| Command | Result |
|---|---|
| `python -m app.migrate status` | `at the head revision 0010`, exit 0 |
| `python -m app.migrate check` | `check passed`, exit 0: head revision, no drift from `app/models.py`, RLS and a policy on every tenant table, FORCE everywhere, no object owned by a login role, context key provisioned |
| `python -m app.migrate fingerprint` | Revision 0010, digest `4de4eeb77f457476…`. Counts: 72 tables (`alembic_version` excluded), 852 columns, 101 constraints, 179 indexes, 116 policies, 35 triggers, 12 functions |
| Fingerprint consistency | A fresh, empty PostgreSQL 17 database (CI bootstrap script, throwaway cluster) built 0001→0010 has the **identical** fingerprint (same digest and contents). The development schema therefore equals the schema the migrations define |

## 5. Privilege and trigger verification

**Spec requires** (the specification text, checked; not the IDs as 0010's docstring cites them):

| ID | Requirement | Relation to 0010 |
|---|---|---|
| DAT-REQ-037 | Ordinary workflows MUST NOT hard-delete governed assurance history | Direct: `DELETE` is refused on all six tables |
| DAT-REQ-009 | Bitemporal corrections MUST preserve closed historical record slices and create new revisions rather than overwriting history | Supporting: non-lifecycle columns can't be overwritten |
| SEC-REQ-029 | Completed evidence, applicability, control and assurance records MUST retain immutability protections defined by EVD/DAT, and ordinary security/admin roles MUST NOT rewrite them | Direct: `immutable_columns` and `lifecycle_final` apply to every role |
| STA-REQ-027 | Occurrence-based obligations MUST preserve each occurrence and MUST NOT overwrite missed/failed occurrences with later success | **Only `assurance_obligation_occurrence`.** 0010 freezes a fulfilled occurrence; it does not stop a missed occurrence (`fulfilled_at` null) from later being fulfilled. So STA-REQ-027 is only partly enforced at this layer |
| EVD-REQ-020; PRI-REQ-021/022 | Legal hold MUST block governed payload destruction for its active scope and MUST itself be a versioned governed event/object. Legal holds MUST be versioned, authorised, auditable and scope-minimised | `evidence_legal_hold_revision`: a released hold is final and its record can't be deleted or rewritten |
| DAT §13 | Runtime role: no BYPASSRLS, SUPERUSER, CREATEDB, CREATEROLE or schema ownership; no TRUNCATE of authoritative tenant tables; table owners are non-login | Verified below |
| DAT §12; DAT-REQ-027 | Functions: fixed `search_path`, non-login owner, least privilege | Verified for `assure_freeze_final` |

**Citation error.** 0010's docstring, `tests/test_governance_projections.py`, `tests/test_immutability.py` and the earlier reports cite **EVD-REQ-018 for legal holds**. EVD-REQ-018 actually says: "Evidence supersession or correction MUST create a new EvidenceArtifact and a supersession/invalidation event; payload bytes behind an existing evidence ID MUST NOT be replaced." The legal-hold requirements are EVD-REQ-020 and PRI-REQ-021/022 (§9).

The repository roadmap's Stage 3 acceptance line ("no governance status column is written outside its event writer: grep gate plus database grants") is a repository document, not a ZA-* requirement.

**Code does.** On each of the six tables, 0010:
- revokes table-wide `UPDATE` and `DELETE` from `assure_app_rw`, then grants `UPDATE` on the lifecycle columns only;
- adds `immutable_columns`: every other column is immutable and `DELETE` is refused, for every role;
- adds `lifecycle_final`: terminal states are frozen.

**Verification.** Read-only, connected as the runtime role `assure_app`, using effective privileges (`has_table_privilege` / `has_column_privilege`, inheritance included):

| Table | SELECT / INSERT | Table UPDATE / DELETE / TRUNCATE | Updatable columns | Triggers (enabled) |
|---|---|---|---|---|
| `assurance_exception_revision` | yes / yes | no / no / no | `approved_by`, `state` (2 of 11) | `immutable_columns`, `lifecycle_final` |
| `assurance_compensation_revision` | yes / yes | no / no / no | `approved_by`, `status` (2 of 11) | both |
| `assurance_issue` | yes / yes | no / no / no | `owner_actor_id`, `state` (2 of 13) | both |
| `evidence_legal_hold_revision` | yes / yes | no / no / no | `released_at`, `status` (2 of 9) | both |
| `evidence_conflict` | yes / yes | no / no / no | `disposition`, `status` (2 of 7) | both |
| `assurance_obligation_occurrence` | yes / yes | no / no / no | `evidence_ids`, `fulfilled_at` (2 of 9) | both |

Further checks:
- **Before 0010** the same tables had table-wide UPDATE and DELETE and no triggers. That is the gap 0010 closes.
- **`assure_freeze_final()`** is owned by `assure_table_owner`, is not `SECURITY DEFINER`, and has `search_path=pg_catalog` pinned.
- **Runtime role:** not a superuser, not BYPASSRLS, no CREATEROLE or CREATEDB, owns no relation. Its attributes and memberships are identical before and after.
- **DAT §13, across the whole schema:** the runtime role can TRUNCATE **no** table (50 tables carry `tenant_id`), and the `public` schema is owned by the non-login `assure_table_owner`.

These checks are catalogue-level. The triggers' behaviour (refusing a protected column, a delete or reopening a final state) is not exercised on live data here. It is proven by `tests/test_governance_projections.py` (8) and `tests/test_immutability.py` (7) on a fresh PostgreSQL 17 database, which both passed today as part of the full suite (269 passed, 0 skipped).

## 6. RLS verification

**Spec requires.**
- **DAT-REQ-003:** every tenant-scoped table MUST enable and FORCE RLS unless an approved ADR documents a non-tenant table.
- **ZA-DAT-001 §12:** RLS is defence in depth below application authorisation; tenant-scoped SQL must be impossible without transaction-local scope.
- **SEC-REQ-008:** tenant and entity isolation MUST be enforced independently across API authorisation, PostgreSQL RLS and the other layers.

**Code does.** 0010 doesn't touch RLS, FORCE or the `tenant_isolation` policies.

**Verification.**
- **RLS flags:** identical before and after for all 73 tables. 52 tables have RLS, every one with FORCE, and 0 RLS tables lack FORCE.
- **Policies:** the 116 policies are identical before and after (name, permissive/restrictive, roles, command, `USING` and `WITH CHECK`).
- **`check`:** RLS coverage and the security gaps both pass.
- **Runtime role without a tenant context:** `assure_app` sees **0 rows** in each of the six tables, while the owner sees the four existing rows.

## 7. Backend start-up

**What was done.** Your `uvicorn --reload` supervisor was still running (`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`, pid 22864 → 4116), waiting for a file change after its worker stopped on the schema check.

Starting a second server with the same command would have competed for port 8000. Instead, `touch -c backend/app/main.py` (mtime only; content hash unchanged, `cde8f04e100fcd42`) made the supervisor start a new worker (pid 23736). No code was changed to get past the schema check.

**Verification.**
- The worker started and serves on 8000.
- No schema-version error: `/api/health` answers, and authenticated routes work. The API checks the revision at start-up and refuses to start otherwise.
- The supervisor's log is in your terminal and was not captured here.

## 8. Health endpoint and behaviour checks

`GET /api/health` returned **200**: `{"status":"ok","cell_id":"eu-west-cell-a","dictionary_version":"ZA-GLS-001:1.0"}`.

Authentication and scope, against the running API (`api_checks.py`, final run 17 of 17 PASS):

| Check | Expected | Result |
|---|---|---|
| `GET /auth/session`, `/watermark` and `/org/tenant` without a token | 401 | 401 |
| `GET /auth/session` with an invalid token | 401 | 401 |
| `GET /org/tenant` with only an `X-Actor-Id` header (dev header off) | 401 | 401 |
| Sign-in as the bootstrap Organisation Admin | 200 | 200, role `TENANT_OWNER`, session tenant = bootstrap tenant |
| `GET /org/tenant` signed in | Own tenant only | 200; tenant and both legal entities belong to the caller's tenant |
| `GET /watermark` signed in | 200 | 200 |
| `GET /organisations` and `/organisations/{other}` (Super Admin only) as tenant admin | Refused | 404 (masked) |
| Sign-in body with an unknown field | 422 (API-REQ-014) | 422 |
| Sign-out, then reuse the token | 200, then 401 | 200, then 401 (revoked immediately) |

An earlier run of the same script reported one FAIL. My script read `tenant_id` at the top level of the `/org/tenant` response instead of `tenant.tenant_id`; the API was not at fault. The corrected script was re-run in full.

**Data written by this verification:** two sign-ins and two sign-outs (one per run) added 2 `auth_session` rows (121 → 123) and 4 audit records (170 → 174: `auth.login` and `auth.logout`). This is ordinary application use; nothing else was written.

## 9. Remaining errors and limitations

| # | Gap | Recommendation |
|---|---|---|
| 1 | **Docs:** the revision table in `docs/database-migrations.md` ("How the schema is managed → Now") has no rows for 0007 and 0008, and lists 0010 before 0009 | Add the 0007 and 0008 rows and restore the order (documentation only) |
| 2 | **Tool behaviour:** every `upgrade` re-sets `assure_app`'s password to the value in `DATABASE_URL`. Here that was the existing password (verified), but an edited `.env` would silently change the server's password | Document this in `docs/database-migrations.md` "Commands", or have the tool skip the `ALTER ROLE` when the role already authenticates |
| 3 | **Coverage:** with one tenant in the development database, a live cross-tenant HTTP probe wasn't possible without creating data | Covered by the database-level checks above and by `tests/test_tenant_security.py` (49) on fresh PostgreSQL 17 |
| 4 | **Coverage:** trigger behaviour was checked from the catalogue, not exercised on live rows | Covered by the tests (§5) and by the rehearsal |
| 5 | **Owner role (audit A10, open):** the owner can still disable a trigger with DDL | Data Architect + Security decision (non-login owner, separate migrator) |
| 6 | **Backup location:** the backup and the evidence-store copy (which includes key files) sit on the same disk as the database, under git-ignored `backend/var/backups/` | Keep an off-machine copy if this data matters; never commit `backend/var/` |
| 7 | **Start-up log:** the start-up line naming the schema revision is in your terminal and was not captured | None needed: start-up is evidenced by the health and authenticated checks |
| 8 | **Restore verification incomplete** (DAT §22, DAT-REQ-030, QA-REQ-036, SEC-REQ-036, EVD-REQ-039): no semantic replay, ledger/checkpoint check or evidence reconciliation on the restored copy (§2) | Run a restore exercise on an isolated copy with `ZA_DATA_DIR` pointing at the evidence-store copy: `snapshots/{id}/reproduce` for every snapshot, `ledger/verify`, `evidence/{id}/verify` for every item, and release/package verification. Record the results |
| 9 | **No applicability replay:** no endpoint or tool reproduces a historical applicability evaluation, which DAT §22 requires of a restore | Backend work; ties to Stage 3e (blocked on TIM-ADR-04) |
| 10 | **Backups unencrypted** (DAT §22). Key material sits beside the payloads it protects, which also defeats any local crypto-erasure claim (EVD-REQ-022). The repository is under `OneDrive\Desktop`, so `backend/var/backups/` is probably synced to OneDrive | Encrypt backup sets at rest (for example a password-protected archive, or keep the keys separately). Exclude `backend/var/` from OneDrive sync, or keep backups outside the synced folder |
| 11 | **Review (DAT-REQ-039, SEC-REQ-024):** a schema change affecting immutable tables needs security/data/QA review, and security-sensitive configuration changes MUST be reviewed. No security/data/QA review of 0010 is recorded | Record a security/data/QA review of 0010 before staging or production. Not needed for this local upgrade |
| 12 | **Wrong citations in committed files:** EVD-REQ-018 is cited for legal holds in `migrations/versions/0010_…py`, `tests/test_governance_projections.py`, `tests/test_immutability.py`, `docs/task-4-2-…-report.md` and `docs/pre-push-remediation-report.md` (correct: EVD-REQ-020, PRI-REQ-021/022). SEC-REQ-024 ("security-sensitive policy/configuration changes MUST be versioned, reviewed…") is cited for the dev-header guard in `app/security.py`, `tests/test_request_hardening.py` and three docs; no ZA requirement names a development header, and the closest are IAM-REQ-025 (hidden impersonation prohibited), IAM-REQ-001 and SEC-INV-08 | Correct the docs, tests and code comments in a follow-up change. **Do not edit the applied revision 0010** (`docs/database-migrations.md`, "Writing a new migration" step 7); note the correction elsewhere |
| 13 | **STA-REQ-027 only partly enforced:** 0010 freezes fulfilled occurrences but doesn't stop a missed occurrence from being fulfilled later | Decide with the STA owner whether a missed occurrence must become final at its due time; that is a new reviewed revision, not an edit to 0010 |

**Not changed by this task:**
- **Git:** HEAD `f5220e9`, already pushed. No Git command that changes state was run.
- **Files:** this report is the only new file. `backend/app/main.py` got an mtime-only touch.
- **Scratch work:** scripts and temporary PostgreSQL clusters stayed in the session scratch folder. Both throwaway clusters were stopped and deleted.
- **Credentials:** no password, connection string or secret appears in this report or in any log produced.
