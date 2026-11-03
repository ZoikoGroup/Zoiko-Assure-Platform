# Database migrations

> **Scope:** how the Zoiko Assure PostgreSQL schema is created, changed, verified and recovered. Stage 1 (M-01) of [backend-architecture-roadmap.md](backend-architecture-roadmap.md).
> **Requirements:** ZA-DAT-001 DAT-REQ-023 (versioned migrations), DAT-REQ-039/040 (reviewed, reproducible schema changes), DAT-REQ-004 (least-privilege roles), QA-REQ-035 (fresh install and upgrade tested).
> **Not covered:** this is the migration foundation, plus database-enforced immutability of completed results (Stage 2c). It does not make the backend production-ready; see [What is not enforced yet](#what-is-not-enforced-yet).

## Contents

1. [How the schema is managed](#how-the-schema-is-managed)
2. [Database states](#database-states)
3. [Commands](#commands)
4. [Procedures](#procedures): local development, new database, deployment
5. [Rollback and forward recovery](#rollback-and-forward-recovery)
6. [Writing a new migration](#writing-a-new-migration)
7. [Troubleshooting](#troubleshooting)
8. [What is not enforced yet](#what-is-not-enforced-yet)

---

## How the schema is managed

### Before Stage 1 (for reference)

Every API start-up on PostgreSQL ran `pg_bootstrap.bootstrap()` with the owner role (`DATABASE_ADMIN_URL`), holding advisory lock `724501`:

1. Created or updated the runtime role `assure_app` and checked that it was least-privilege.
2. Ran `Base.metadata.create_all`, which creates missing tables but never alters an existing one.
3. Ran `backend/db/postgres_rls.sql` through a raw cursor: the `tenant_isolation` policies on 41 tables, the `assure_current_tenant()` and `assure_system()` functions, the runtime-role grants, and the append-only triggers on `evidence_ledger_event` and `platform_audit_event`.

The consequences:
- There was no schema version.
- A model change never reached an existing database.
- Every start ran DDL.
- The deploy pipeline stopped before any schema change ("BLOCKED — VERSIONED MIGRATION SYSTEM REQUIRED").

### Now

| Part | Where | Role |
|---|---|---|
| Migration tool | `backend/app/migrate.py` (`python -m app.migrate`) | Owner/migration role (`DATABASE_ADMIN_URL`) |
| Revisions | `backend/migrations/versions/` | Applied in order. Each has a revision ID and a `down_revision` |
| Revision 0001 | `0001_baseline_schema.py` | The 63 tables, exactly as `create_all` built them. Irreversible |
| Revision 0002 | `0002_rls_grants_and_append_only_triggers.py` | The former `db/postgres_rls.sql`, byte for byte, plus one `REVOKE` on `alembic_version` |
| Revision 0003 | `0003_immutable_completed_results.py` | Completed results are immutable (audit I-02). The runtime role loses `UPDATE`/`DELETE` on 11 tables, keeping column-level `UPDATE` only for source-run completion and IPE anomaly disposition. Row triggers also stop the owner role's `UPDATE`/`DELETE`; the owner can still disable a trigger with DDL (audit finding A10). Reversible |
| Revision 0004 | `0004_immutable_published_catalogue.py` | Published catalogue artefacts are immutable (DAT-REQ-012): rule versions, content releases, set versions and released fact definitions. The runtime role keeps `SELECT`/`INSERT` only, and the same row trigger stops `UPDATE`/`DELETE` by every role (the owner can still disable it with DDL; A10). Publication writes each row once. Reversible |
| Revision 0005 | `0005_platform_rows_platform_scope_only.py` | No-tenant (platform) rows of `platform_audit_event` and `platform_outbox_event` are visible and insertable only while a transaction carries no tenant scope, and no ordinary scope can update or delete them; tenants see and write only their own rows (Task 2 outbox remediation). `assure_system()` and the missing `FORCE` are unchanged (Task 1). Reversible |
| Revision 0006 | `0006_auth_session_token_hash.py` | `auth_session.token_hash` (unique): sessions are found by the SHA-256 of an opaque random token, which is never stored. Sessions created before the upgrade can no longer be used (sign in again). Reversible |
| Revision 0010 | `0010_governance_projection_guards.py` | The six Stage 3c state projections (exceptions, compensations, issues, legal holds, evidence conflicts, occurrences): the runtime role loses table-wide `UPDATE`/`DELETE` and keeps `UPDATE` on each table's lifecycle columns only; `immutable_columns` (all other columns, and `DELETE`) and `lifecycle_final` (terminal states frozen) triggers apply to every role. Privileges and triggers only, no data change. Reversible |
| Revision 0009 | `0009_scoped_role_registry.py` | Scoped role registry (Stage 4b): `iam_role_assignment` (explicit TENANT / LEGAL_ENTITY / PLATFORM scope, validity window; only the closure columns can change) and append-only `iam_role_assignment_event`, both FORCE RLS. Backfills every `org_actor.roles` entry as an assignment valid from the migration (no invented history); `org_actor.roles` becomes a cache. Reversible (downgrade drops entity-scoped assignments; the cache keeps organisation-wide roles) |
| Baseline fingerprint | `backend/migrations/baseline_schema.json` | Normalised description of the schema at 0002. Used only to validate databases created before Stage 1 |
| Version record | table `alembic_version` | One row: the current revision. The runtime role has no privilege on it |
| API start-up | `backend/app/main.py` | **Read-only.** Checks that both roles connect, the schema is at this build's head revision, and the runtime role is least-privilege. Otherwise it refuses to start |

Every `upgrade` runs as **one transaction** holding the same advisory lock (`724501`). That transaction covers provisioning the runtime role, validating the database, any stamp, and every pending revision. PostgreSQL DDL is transactional, so a failure anywhere rolls the whole run back.

The baseline is not a reconstruction. On an empty PostgreSQL 17 database, revisions 0001 and 0002 produce the same schema as the old start-up code:
- `pg_dump --schema-only`: 382 normalised statements, identical (`alembic_version` excluded).
- Catalogue fingerprint: identical, digest `663cea6c…`.

`tests/test_migrations.py` re-checks this in CI.

SQLite exists only for isolated test runs (`ZA_ALLOW_SQLITE=1`). There, start-up still calls `create_all`, and migrations do not apply.

---

## Database states

`python -m app.migrate status` classifies the database without changing it.

| State | Meaning | `status` exit | `upgrade` does |
|---|---|---|---|
| `EMPTY` | No tables | 1 | Builds 0001 → head |
| `CURRENT` | At this build's head revision | 0 | Nothing (no-op) |
| `BEHIND` | At an earlier known revision | 1 | Applies the pending revisions in order |
| `UNVERSIONED_BASELINE` | Created by the pre-Stage-1 start-up, and every table, column, constraint, index, policy, trigger, function and runtime-role grant matches `baseline_schema.json` | 1 | Records 0002 (no object is re-created), then continues to head |
| `UNVERSIONED_MISMATCH` | Tables without a version, **not** matching the baseline (drift, a partial schema, another application) | 2 | **Refuses**, lists the differences, changes nothing |
| `UNKNOWN_REVISION` | The recorded revision is not in this build's history (a newer build ran, or a branch) | 2 | **Refuses**, changes nothing |
| `INCONSISTENT` | A version table with no revision, next to existing tables | 2 | **Refuses**, changes nothing |

**A version is never recorded without validation.** The only automatic stamp is `UNVERSIONED_BASELINE`, and it runs only after the full comparison has passed, inside the same transaction as the upgrade. The raw `alembic upgrade`, `alembic downgrade` and `alembic stamp` commands are refused by `migrations/env.py`.

---

## Commands

Run from `backend/` with the virtualenv's Python. Every command reads `DATABASE_ADMIN_URL` and `DATABASE_URL` from the environment or `backend/.env`. Output names `user@host:port/database` and never a password.

| Command | Changes the database? | Purpose |
|---|---|---|
| `python -m app.migrate status` | No | Report the state. Exit 0 current, 1 work due, 2 refused |
| `python -m app.migrate upgrade` | Yes | Validate, then migrate to head (or `--to REV`), in one transaction |
| `python -m app.migrate check` | No | At head; no drift between `app/models.py` and the database (an autogenerate comparison); row-level security and a policy on every table carrying `tenant_id` (except `org_actor`, by design) |
| `python -m app.migrate downgrade REV --confirm` | Yes | **Disposable databases and rehearsals only.** 0002 → 0001 turns tenant isolation off. 0001 cannot be downgraded |
| `python -m app.migrate fingerprint` | No | Print the normalised schema description (maintainers) |

The plain `alembic` command remains available for authoring and read-only commands: `revision --autogenerate`, `check`, `history`, `heads`, `current`.

---

## Procedures

### Local development: the first time after Stage 1

Your existing development database (`assure` on `127.0.0.1:5433`) was created by the old start-up. Until it is migrated, the API refuses to start with `Database schema is not at the revision this build requires`.

1. **Stop the backend.**
2. **Back up the database and the evidence store together.** Payloads in `backend/var/` can't be decrypted without their keys, and they belong to this database.

   ```powershell
   $stamp = Get-Date -Format yyyyMMdd-HHmmss
   New-Item -ItemType Directory -Force "$env:USERPROFILE\zoiko-assure-backups" | Out-Null
   # prompts for the owner (assure) password
   & "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" -h 127.0.0.1 -p 5433 -U assure -d assure --format=custom `
       --enable-row-security --file "$env:USERPROFILE\zoiko-assure-backups\assure-$stamp.dump"
   & "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe" --list "$env:USERPROFILE\zoiko-assure-backups\assure-$stamp.dump" | Select-Object -First 5
   Copy-Item -Recurse backend\var "$env:USERPROFILE\zoiko-assure-backups\var-$stamp"
   ```

   `pg_restore --list` must print the archive header. If it doesn't, the backup is unusable: stop here.

   From revision 0007, every tenant table has `FORCE ROW LEVEL SECURITY`. Without `--enable-row-security`, `pg_dump` stops with "query would be affected by row-level security policy". With it, the owner login reads every row through its explicit `migrator_all` policy (`USING (true)`). A dump taken without the flag is incomplete: delete it.
3. **Check, migrate, verify:**

   ```powershell
   Set-Location backend
   .\.venv\Scripts\python.exe -m app.migrate status    # expect UNVERSIONED_BASELINE ("matching the 0002 baseline exactly"), exit 1
   .\.venv\Scripts\python.exe -m app.migrate upgrade
   .\.venv\Scripts\python.exe -m app.migrate check     # expect "check passed"
   ```

   If `status` reports `UNVERSIONED_MISMATCH`, read [Troubleshooting](#troubleshooting). Never stamp the database by hand.
4. **Start the backend as usual.** The start-up log names the schema revision.

After a pull that adds a revision, `status` reports `BEHIND`. Back up, then run `upgrade` and `check` again.

### A new, empty database

Create the owner role and the database as in the README (step 2), then run `upgrade` and `check`. `upgrade` also creates the runtime role `assure_app` with the password in `DATABASE_URL`. Roles belong to the whole PostgreSQL server, so use the same `assure_app` password everywhere on one server.

### Deployment

`.github/workflows/deploy-environment.yml` runs, in order:

1. **Validate configuration.**
2. **Verify the release bundle** (checksums and commit).
3. **Build the frontend.**
4. **Prepare the migration tool** from the release bundle's own `backend-src.tar.gz` and dependency lock, never a fresh dependency resolution.
5. **Back up the database before migrating.** Target-specific; fails closed until a hosting target exists (G-10). It must produce a restorable backup and record its identifier.
6. **Apply approved database migrations.** Only this step receives the migration credential (`ZA_MIGRATION_DATABASE_URL` as `DATABASE_ADMIN_URL`). It also receives `ZA_DATABASE_URL`, so that it can provision and verify the runtime role. It runs:
   - `status`: exit 2 stops the deployment, with nothing changed.
   - `upgrade`: one transaction; a failure stops the deployment.
   - `check`: read-only verification.
7. **Deploy the backend**, then verify it. The API refuses to start unless the schema is at the release's head revision.

There is no reset, `DROP`, test seed or Customer Zero seed anywhere in this path.

---

## Rollback and forward recovery

| Situation | What happens / what to do |
|---|---|
| A migration fails during `upgrade` | The transaction rolls back automatically: the database stays at its previous revision, with no partial objects or rows. The deployment stops before the backend step, and the previous release keeps serving. Fix the revision in a new release. |
| A migration succeeded but is wrong, and data written since matters | **Forward recovery:** write a new, reviewed revision that corrects it, and deploy it through the normal path. This is the default for staging and production. |
| A migration succeeded and must be undone completely | Restore the pre-migration backup into a **new** database, verify it (`status`, `check`, start the previous release against it), then point the environment's URLs at it. Anything written after the backup is lost, so that is a business decision. Do not restore over the live database in place. |
| The application code must go back one release after a migration | The previous build refuses to start on a schema it doesn't know (`UNKNOWN_REVISION`). Use forward recovery, or restore the backup taken before that migration. A tested `downgrade` is for rehearsals and disposable databases; using it on a real database is an exception that needs its own reviewed decision. |
| **Reverting Stage 1 itself** | Stage 1 changed no table, column, policy or grant; the only addition is the `alembic_version` table. Revert the code, then, with a backup, run as the owner role: `DROP TABLE alembic_version;`. The old start-up code would otherwise grant `assure_app` DML on that table on its next start. The database is then exactly as before Stage 1. |

**Restoring a backup into a new database** (example for the development server):

```powershell
# as the owner role; prompts for its password
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -h 127.0.0.1 -p 5433 -U assure assure_restored
& "C:\Program Files\PostgreSQL\17\bin\pg_restore.exe" -h 127.0.0.1 -p 5433 -U assure -d assure_restored --no-owner <backup>.dump
```

Then set both URLs to `assure_restored`, restore the matching `backend/var` copy, and run `status` and `check`.

---

## Writing a new migration

1. Change `app/models.py`.
2. Against a **development** database at head, generate a draft:

   ```
   cd backend
   .venv\Scripts\alembic.exe revision --autogenerate --rev-id 0003 -m "short description"
   ```

   Revision IDs are four-digit, sequential, and never reused.
3. **Review it by hand.** Autogenerate drafts; it does not decide. Check for:
   - Renames: autogenerate drops and re-adds, losing data. Use `op.alter_column` or a copy.
   - Server defaults, enum and type changes.
   - Data backfills, which must never fabricate history (DAT-REQ-024).
4. **New tables:**
   - **Grant the runtime role explicitly.** 0002's `GRANT … ON ALL TABLES` covered only the tables that existed then.
   - **New tenant tables need RLS:** `ENABLE ROW LEVEL SECURITY` plus a `tenant_isolation` policy. `check` fails without them.
   - **Decide whether rows are mutable**, and record it in `tests/test_immutability.py`.
     - A completed result (an evaluation, snapshot, attestation, package or integrity artefact) gets `SELECT, INSERT` only, plus the `append_only` trigger, in the same migration.
     - A row that a code path still completes in place gets column-level `UPDATE` and the `immutable_columns` trigger listing those columns.
     - The generated test fails for any table whose grants match no classification.
5. **Write and test `downgrade()`.** If a step can't be reversed without losing data, make `downgrade()` raise with an explanation, as 0001 does.
6. Test on a disposable database: `upgrade`, then `downgrade <previous> --confirm`, then `upgrade` and `check`. CI does the same for the newest revision, plus the no-drift and RLS-coverage gates.
7. Never edit a revision that has been applied anywhere outside your machine. Correct it with a new revision. Never regenerate `baseline_schema.json`: it describes 0002 permanently. Its `fingerprint --write` refuses any revision other than 0002.

**Completed results are never corrected in place** (DAT-REQ-011, STA-REQ-019, IPE-REQ-025, EVD-REQ-026). A correction is a new evaluation, snapshot or package. Only the owner role can get past the triggers, and only by `ALTER TABLE … DISABLE TRIGGER` inside a reviewed migration's transaction, where it is visible and attributable.

**Destructive changes** (dropping a column or table, narrowing a type) go in two releases. First stop writing and reading the old thing; then remove it in a later revision, after a backup.

---

## Troubleshooting

**`UNVERSIONED_MISMATCH` on a database created by the old start-up.** Each difference is listed as `missing from database` or `not in baseline`:
- If the drift is accidental (a manual change), restore from a backup or revert the manual change, then retry.
- If the extra objects are intentional, they need a reviewed decision and a corrective migration.

Either way, do not stamp by hand: that would hide the drift from every later check.

**`UNKNOWN_REVISION`.** The database has been migrated by a newer build, or by a branch. Deploy that build, or restore the matching backup.

**`INCONSISTENT`.** Someone created or emptied `alembic_version` by hand. Investigate before doing anything else.

**`Cannot connect to PostgreSQL with DATABASE_ADMIN_URL`.** Check the service, the owner role's password and the database name. The message never contains the password.

**The API says `Runtime role 'assure_app' does not exist`.** The database has not been migrated yet. Run `upgrade`, which provisions the role.

**`Refusing to start: runtime role 'assure_app' is not least-privilege (…)`** (`RuntimeRoleError`, from the API or the migration tool). The runtime role has gained a privilege such as `CREATEROLE` or `BYPASSRLS`, or owns objects. Remove it as a superuser, then retry. The tool never grants it.

---

## What is not enforced yet

These are tracked in [backend-architecture-audit.md](backend-architecture-audit.md) and the roadmap ([§6 gap matrix](backend-architecture-roadmap.md#6-stage-2-gap-matrix)):

- **Isolation (Stage 2a/2b, blocked on the system-job design decision):**
  - RLS is `ENABLE`d, not `FORCE`d.
  - The `assure_system()` bypass is a session variable the runtime role can set (I-01, Critical).
- **Done in Stage 2c (revision 0003):** completed results are immutable to the runtime role and, through triggers, to the owner role (I-02).
- **Done (revision 0004):** the published catalogue (rule versions, content releases, set versions, released fact definitions) is immutable the same way (DAT-REQ-012). Catalogue rows whose lifecycle still changes in place (source records, provisions, requirement versions) are not covered yet (I-04).
- **Still in place:** revision and governance rows are overwritten in place (I-04). They stay writable until Stage 3 replaces those writes with events.
- **The API still receives the owner credential.** It uses it for the read-only start-up checks, the one-time account bootstrap and the demo seed. Serving with the runtime role only is still open (`docs/ci-cd.md`, deployment blockers).
- **Deployment:** no hosting target is chosen (G-10), and the pre-migration backup step is target-specific.
- **Governance:** G-01 (Python or Go runtime) is unresolved.
  - It does not block Stage 1: migrations are SQL schema history, and this tool can stay an operations tool whichever runtime is chosen.
  - A Go runtime would need to read the same `alembic_version` record, or adopt an equivalent check, before it could take over start-up.
