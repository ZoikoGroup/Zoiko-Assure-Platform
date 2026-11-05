# Zoiko Assure — Development Database Auto-Migration

> **Date:** 3 November 2026. **Branch:** `naveen-changes` (working tree; nothing staged, committed, pushed or pulled).
> **Canonical database:** `assure@127.0.0.1:5433/assure` was not migrated, changed or connected to for migration. `status` before and after: revision 0010, 0011 pending (§8).
> **Governance:** ZA-* specifications are "Proposed for lock". The ZA specifications don't address development auto-migration; this is a development-tooling decision that keeps their production controls intact.

## 1. Existing migration architecture inspected

| Part | Finding |
|---|---|
| `app/migrate.py` | The one runner. `upgrade()` runs in **one transaction holding advisory lock 724501**: it provisions the runtime role, classifies the database (`inspect`), refuses unsafe states (`UNVERSIONED_MISMATCH`, `UNKNOWN_REVISION`, `INCONSISTENT`), stamps only a fingerprint-verified baseline, applies revisions and provisions the context key. `check()` covers head, model drift, RLS coverage, FORCE, ownership and the context key. Raw `alembic upgrade/downgrade/stamp` are refused by `migrations/env.py` |
| Integrity checks | There are no per-file checksums. Integrity rests on the baseline fingerprint (`baseline_schema.json`), revision ancestry and the post-migration `check`. All are reused unchanged |
| `app/main.py` lifespan | Read-only: `check_database()`, then `require_current()` (raises `SchemaVersionError` if pending), runtime-role and context-key checks |
| `app/db.py` | `.env` loading **skips `DATABASE_ADMIN_URL` unless `ZA_ADMIN_TOOL=1`**, so the API process holds no migration credential |
| Environment guard | `ZA_ENVIRONMENT` (development/test) already gates the dev identity header (`security.dev_header_violation`) |
| Docker Compose / start-up scripts | None in the repository |
| CI | `backend-postgres` runs migrate status, upgrade, repeat, check, then the gated full suite |

**Spec requires.** ZA-DAT-001 §13: `assure_migrator` has "DDL/migration privileges in controlled deployment; **no normal application use**". DAT-REQ-004: runtime roles have no DDL ownership. DAT-REQ-023/039: reviewed, versioned migrations. ZA-DAT-001 §29: "direct manual production DDL is a release blocker".

**Consequence for the design:** migrating *inside* the API process would make the API hold the migration credential. So the API starts a separate operator process instead.

## 2. Files changed

| File | Change |
|---|---|
| `backend/app/devmigrate.py` (new) | The gates, `run()` (reuses `migrate.inspect/upgrade/status/check`), the CLI `python -m app.devmigrate`, the start-up hook `startup_auto_migrate()` and redaction |
| `backend/app/main.py` | Lifespan calls `startup_auto_migrate()` before `require_current()`; a refusal reason is appended to a `SchemaVersionError` |
| `backend/tests/test_dev_auto_migration.py` (new, 37 tests) | §6 |
| `.github/workflows/ci.yml` | `--require test_dev_auto_migration=37` |
| `backend/.env.example` | Commented `ZA_AUTO_MIGRATE`, `ZA_AUTO_MIGRATE_DATABASES`, `ZA_PROTECTED_DATABASES` (placeholders only) |
| `docs/database-migrations.md` | New section "Automatic migration of a disposable development database"; contents entry |
| `docs/development-auto-migration-report.md` (new) | This report |

No migration file was modified. No existing test was changed for this work.

## 3. Automatic migration design

**Code does.**
1. **Flag off** (`ZA_AUTO_MIGRATE` unset or false): `startup_auto_migrate` returns immediately. Start-up behaves as before (verified end to end, §7), and pending revisions still produce the existing `SchemaVersionError` message.
2. **Flag on, `ZA_ENVIRONMENT` not exactly `development`:** start-up stops with `Refusing to start: ZA_AUTO_MIGRATE is set but …`. No child process is started.
3. **Flag on, development:** the API runs `python -m app.devmigrate`. The child is an operator tool (`ZA_ADMIN_TOOL=1`), so it alone loads `DATABASE_ADMIN_URL` from `backend/.env`. It:
   - applies the static gates (§4) and exits 3 (refused, nothing changed) if any fails, before any connection;
   - over a read-only connection, checks `current_database()` and the owner-set approval comment, and exits 3 on a mismatch;
   - inspects the database with the same revision locations as the upgrade; if current, it exits 0 ("nothing to apply");
   - otherwise runs `migrate.upgrade()`, then requires `status` = CURRENT and `check` = no problems;
   - runs the same idempotent account bootstrap as `python -m app.migrate upgrade` (public schema only), and exits 0;
   - on any error (the migration rolled back, or the runner refused the database state), exits 1.
4. **Back in the API:**
   - exit 0: proceed;
   - exit 3: log the refusal and proceed to the normal `require_current`, which stops start-up with the refusal appended if revisions are pending;
   - any other exit: raise, so the API does not start.
5. **The API verifies independently** with its unchanged read-only checks: head revision, least-privilege runtime role and context key.

**Behaviour with `uvicorn --reload`:** every reload starts a new worker and so runs the check again. A pulled revision is applied on the next reload.

## 4. Database protection mechanisms

| # | Gate | Refused example |
|---|---|---|
| 1 | `ZA_ENVIRONMENT` exactly `development` | `production`, `prod`, `staging`, `test`, `dev`, empty, unset |
| 2 | `ZA_AUTO_MIGRATE` in {1, true, yes} | unset, `0`, `false`, `maybe` |
| 3 | Admin and runtime URLs: both PostgreSQL, same host:port/database | Different databases; SQLite |
| 4 | Loopback host only | `db.dev.internal` (shared or remote) |
| 5 | Exact `host:port/database` in `ZA_AUTO_MIGRATE_DATABASES` | Unlisted database |
| 6 | Not protected: the name **`assure` on every host**, plus `ZA_PROTECTED_DATABASES` | `127.0.0.1:5433/assure` even if allowlisted |
| 7 | Server identity: `current_database()` equals the allowlisted name | URL and server disagree |
| 8 | Owner-set marker: `COMMENT ON DATABASE … IS 'zoiko-assure:auto-migrate-approved'` | No marker. The environment label alone never approves a database |

Gates 1–6 run before any connection. No reset, drop, downgrade, stamp or skip exists in this path. A failed revision is never recorded: the runner's single transaction rolls back.

**Secrets:** output names `user@host:port/database` only. URL passwords and the known password values are redacted from every child message, and the API redacts again before logging or raising.

## 5. Concurrency handling

**Code does.** `migrate.upgrade` takes `pg_advisory_xact_lock(724501)` and re-inspects the database after acquiring it. Processes that start together queue on the lock; the first applies the revisions, and the others see CURRENT and apply nothing. The lock is transaction-scoped, so it is released on commit or rollback, including when the process dies.

**Verification:**
- In-process: 3 concurrent `run()` calls on one empty schema gave exactly one `MIGRATED` and two `CURRENT`, with no errors.
- End to end: two real API processes started together; one applied, one found nothing to apply, and both served HTTP 200.

## 6. Tests executed and results

| Run | Result |
|---|---|
| `test_dev_auto_migration.py` on SQLite | 30 passed, 7 skipped (`PostgreSQL-only`) |
| Full SQLite suite + `backend-unit` gate | **213 passed, 107 skipped** (all `PostgreSQL-only`), 0 failed; **gate PASS** |
| Full suite on fresh PostgreSQL 17.11 + gate (`--no-skips`, every `--require`) | **320 passed, 0 failed, 0 skipped; gate PASS**; `test_dev_auto_migration` **37/37** |
| CI migration step (empty database: status, upgrade, upgrade again, status, check) and RLS gate | PASS (head 0011) |
| `repo_hygiene.py`, `git diff --check` | PASS |

The 37 tests, against the brief's cases:

| Brief case | Test(s) |
|---|---|
| Fresh database, all pending | `test_a_fresh_database_is_migrated_to_head_and_verified`; real child `test_the_real_child_process_migrates…` |
| No pending / repeated start-up does not rerun | `test_pending_revisions_are_applied_and_a_repeat_changes_nothing` (fingerprint and single `alembic_version` row unchanged); real child second run "nothing to apply" |
| Existing database with pending revisions | Same test, from the previous revision to head |
| Disabled | `test_auto_migration_is_opt_in` (6 values; the runner is never called) |
| Production / staging / unknown refusal | `test_only_the_development_environment_may_auto_migrate` (8 values); `test_the_api_refuses_to_start_when_the_flag_is_set_outside_development` (3); real child with `production` exits 3 |
| Protected database, including `assure` | `test_protected_unlisted_and_shared…` (`assure` on 5433 and on another port, the extra protected list by name and by key); `test_the_canonical_assure_database_is_refused_without_a_connection` (real child, fake password, exit 3, static refusal) |
| Not allowlisted / shared / unidentifiable | Same parametrised test; `test_unidentifiable_targets_are_refused`; `test_without_the_marker_or_with_another_identity_nothing_is_touched` (schema stays EMPTY) |
| Migration failure prevents start-up | `test_a_failed_migration_rolls_back…` (an injected failing revision; schema and fingerprint unchanged at head); `test_a_failure_in_the_real_child_stops_startup` (a real child on a refused state, so the hook raises) |
| Concurrent start-up | `test_concurrent_startups_apply_the_revisions_once` |
| No secrets in logs | `test_startup_reports_a_refusal…` (caplog and exception); real child output checked against both real passwords; `test_redaction` |
| The API never loads the credential | `test_the_api_process_does_not_load_the_migration_credential` (structural) |

## 7. PostgreSQL 17 verification

**Automated:** the full CI-equivalent run in §6, on a throwaway PostgreSQL 17.11 cluster in the session scratch folder, set up with CI's own bootstrap SQL (owner `assure`, NOSUPERUSER CREATEROLE).

**End to end** (real `uvicorn` processes, real lifespan, ports 8011/8012). Target: an owner-approved disposable database `assure_dev_scratch` in that cluster, configured by its own `.env` in a scratch copy of `backend/`, so the API process loaded no migration credential and the child did.

| Scenario | Observed |
|---|---|
| Two processes start together on an empty database | One logged `AUTO-MIGRATE …assure_dev_scratch: applied; before: empty database …; after: at the head revision 0011; check passed`; the other logged `nothing to apply`. Both logged `schema revision 0011` as the runtime role only; health 200 on both |
| Restart | `nothing to apply`; health 200 |
| `ZA_ENVIRONMENT=production` with the flag set | Exit 3: `Refusing to start: ZA_AUTO_MIGRATE is set but ZA_ENVIRONMENT is 'production'…`; no health |
| Flag `false` | Started as before; no `AUTO-MIGRATE` line |
| Passwords in any log | None |

The cluster and the scratch copy, including its `.env`, were deleted afterwards.

## 8. Canonical `assure` database

- `python -m app.migrate status` (read-only) before the work and after it: `at revision 0010; pending 0011 (head 0011)`.
- No test or run used its URL with real credentials. The one test naming `127.0.0.1:5433/assure` uses a fake password and is refused before any connection.
- The real `backend/.env` was not modified and has no `ZA_AUTO_MIGRATE`, so your local start-up behaves exactly as before. It still stops at the schema check until `assure` is upgraded to 0011 manually, after a backup.

## 9. Remaining limitations

| Item | Gap | Recommendation |
|---|---|---|
| Spec coverage | No ZA requirement governs development auto-migration; the controlling constraints applied are DAT-REQ-004 and ZA-DAT-001 §13 (credential separation) | Record this tooling decision with the Data Architect |
| Integrity of revision files | No checksums exist to preserve. A revision edited after being applied to a disposable database is not detected until `check` or drift shows it | Optional future: record revision file hashes in a sidecar table (a reviewed migration) |
| Marker trust | Anyone who owns a database can mark it, so the marker proves the owner's intent, not disposability; `assure` is protected by name regardless | Keep disposable databases under distinct names; add shared ones to `ZA_PROTECTED_DATABASES` |
| Start-up time | Each worker start adds one child process (about 1–2 s; more when revisions apply and `check` runs) | Acceptable for development; flag off by default |
| Windows `--reload` | Not exercised end to end with `--reload`; the per-worker behaviour follows from the lifespan running in each worker | Try it once with a disposable database |
| Credential in a real environment variable | If `DATABASE_ADMIN_URL` is a real environment variable rather than in `.env`, the API process sees it too (unchanged existing behaviour) | Keep the migration credential in `backend/.env` for development |
