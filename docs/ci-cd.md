# CI/CD

How Zoiko Assure is built, tested and (once a target exists) deployed with GitHub Actions.

```
Pull request ──► CI ──► merge to main ──► CI on main ──► Deploy: build-release
                                                          ──► staging: deploy + verify
                                                          ──► production approval
                                                          ──► production: deploy + verify
```

**Current state:** CI is complete. Deploy fails closed because there is no deployment target yet, and no versioned migration system ([blockers](#current-deployment-blockers)). It never deploys, migrates, resets or seeds anything.

## Workflows

| File | Name | Trigger | Purpose |
|---|---|---|---|
| `.github/workflows/ci.yml` | **CI** | every pull request; pushes to `main`, `develop`, `release/**`; manual | Build, test and security checks |
| `.github/workflows/deploy.yml` | **Deploy** | a CI run on `main` completes | Release a CI-verified commit to staging, then production |
| `.github/workflows/deploy-environment.yml` | Deploy environment | called by Deploy | The deployment steps for one environment |

Helper scripts live in `.github/scripts/` and use only the Python standard library:

- `junit_gate.py`: test-report gate.
- `repo_hygiene.py`: secret and private-file check.
- `ci_postgres_bootstrap.sql`: disposable test databases.
- `deploy_healthcheck.py`: post-deploy verification.

Older CI runs on the same PR or branch are cancelled when a new commit arrives. On `main`, every merged commit keeps a complete CI run. No path filters are used, so the required checks always report.

## CI jobs

| Job (check name) | Does | Fails on |
|---|---|---|
| `frontend` | Node 22: `npm ci` → `npx tsc --noEmit` → `npm run build` with `NEXT_PUBLIC_API_URL=http://localhost:8000/api` | install, type or build errors |
| `backend-unit` | Python 3.12 in a fresh venv: `pip install -r backend/requirements.txt` → `pip check` → `pytest tests` on the throwaway SQLite database that `conftest.py` creates | install or dependency conflicts; any failure or error; any skip not marked `PostgreSQL-only` |
| `backend-postgres` | PostgreSQL 17 → migration tool on a fresh database → RLS tests → full backend suite (details below) | the database is unavailable; any migration command fails; any RLS test skipped or failed; any failure, error or skip in the full suite |
| `security` | repository hygiene; `npm audit` as a report only | committed secrets or private runtime files (npm advisories never fail it) |
| `ci-success` | aggregates the four jobs above | any of them not `success` |

`next lint` is deliberately not run. The project has no ESLint configuration, and the command would prompt interactively. Add a lint step once a configuration is committed.

CI references **no** repository or environment secrets. The frontend job sees only public `NEXT_PUBLIC_*` values.

## How the PostgreSQL tests work

1. A `postgres:17` container starts inside the job, listening on the runner's loopback only. It is removed when the job ends.
   - It is started by a step rather than as a `services:` container, because service containers can't receive a password generated during the run.
2. Three passwords are generated per run with `openssl rand` and masked in the log.
   - **Superuser:** reaches only the container. It is never exported to the backend or the tests.
   - **Owner `assure`:** created by `ci_postgres_bootstrap.sql` as NOSUPERUSER with CREATEROLE (least privilege, as the backend's checks require). The password reaches psql through an environment variable, never a command line.
   - **Runtime `assure_app`:** created by `python -m app.migrate upgrade`, as in a deployment. The RLS policies name this role, so the name is fixed.
3. Two fresh databases are created, `assure_test_rls` and `assure_test`. Each pytest session needs its own database: the suite stores evidence payloads in a per-session temporary directory, so a reused database points at files that no longer exist. Separate databases avoid any reset, and there is no `DROP` anywhere in CI.
4. `ZA_TEST_DATABASE_URL` and `ZA_TEST_DATABASE_ADMIN_URL`, the variables `tests/conftest.py` reads, are written to the job environment. A preflight step refuses to continue unless they are PostgreSQL URLs pointing at server version 17. Otherwise `conftest.py` would silently fall back to SQLite.
5. **Migrations on a fresh database:** the migration tool runs on the empty `assure_test_rls` exactly as the deploy step does ([database-migrations.md](database-migrations.md)). `status` must report an empty database (exit 1), then `upgrade`, `upgrade` again (must change nothing), `status` and `check` (head revision, no drift between the models and the database, RLS and a policy on every table carrying `tenant_id`). Any non-zero exit fails the job.
6. **RLS gate:** `tests/test_rls.py` runs on `assure_test_rls`, which the previous step migrated. pytest exits 0 when tests skip, so `junit_gate.py` requires **5 RLS tests executed and passed, none skipped**. If they skip, the job is red.
7. **Full suite:** runs on `assure_test`; `conftest.py` first migrates that empty database through the same `upgrade` function. Its gate requires zero failures, errors and skips, the 5 RLS tests, at least 3 `test_pg_bootstrap.py` tests (runtime-role provisioning) and the 16 `test_migrations.py` tests:
   - fresh install in order, and a repeat run that changes nothing
   - adoption of a database created before migrations existed
   - refusal of drifted, partial, unknown-revision and inconsistent databases
   - full rollback of a failing revision; downgrade and re-upgrade of the newest revision
   - an API start-up with no DDL, and refusal to start on a database that is not at head
   - the RLS-coverage and no-drift checks

   It also requires the 7 `test_immutability.py` tests (audit I-02 and the published catalogue; migrations 0003 and 0004):
   - a generated check that the runtime role's privileges and the guard triggers on every table match the reviewed classification, including no `TRUNCATE`
   - the runtime role is refused `UPDATE`/`DELETE` on completed results
   - the owner role is stopped by the triggers
   - source runs and IPE anomalies change only in their completion columns
   - a rule published through every gate writes its catalogue rows once, and they cannot be rewritten
   - a publication that fails after its rule version is written leaves no catalogue rows, and a retry succeeds
   - an exact downgrade to 0002

   And the 4 `test_snapshot_determinism.py` tests (roadmap Stage 3a):
   - snapshot and control-evaluation output unchanged after a physical table rewrite
   - the record cutoff, pinned dependencies and the tenant boundary
   - concurrent duplicate evaluations resolving to the earliest
   - repeated requests reproducing exactly

   And the 13 `test_rule_integrity.py` tests (Task 3, audit A9a):
   - every published rule version verifies, and the AST hash binds the declared schema and operator-set versions
   - a tampered AST, schema version, SetVersion, manifest, release reference or effective period is refused with `RUL_SIGNATURE_INVALID`. No evaluation rows survive, and a `security.content_signature_failed` event is committed
   - no fallback to an older rule version; batch evaluation, replay, historical comparison and publication impact re-evaluation all fail closed
   And the 28 `test_tenant_security.py` tests (Task 2, PostgreSQL only):
   - tenant scopes cannot read, write, update or delete no-tenant platform outbox/audit rows, and the platform scope sees no tenant rows (migration 0005); RLS coverage and policy inventory
   - the tenant context does not survive commit, rollback, a rolled-back savepoint or the connection's return to the pool; `row_security = off` is refused
   - the runtime role cannot switch role, take ownership, change RLS, policies, context functions or triggers, create objects or truncate (14 probes), and holds no membership or extra privilege
   - a revocation waits for an in-flight commit, and the next commit under the revoked session is refused

   And the 10 `test_auth_session_security.py` tests (Task 2, also run on SQLite): token-hash-only storage, refused forged/revoked tokens, generic and audited sign-in failures, password-change audit, commit-time revalidation, outbox visibility, `authorization.denied` and Super Admin read records.
8. Reports are scrubbed of the generated passwords before upload.

`ZA_DEMO_MODE=0` is set for the job. `tests/conftest.py` opts its own pytest process into the synthetic Customer Zero fixture, which is seeded only into these throwaway databases.

To change the number of RLS, migration, immutability, snapshot-determinism or rule-integrity tests, update `--require test_rls=5`, `--require test_migrations=16`, `--require test_immutability=7`, `--require test_snapshot_determinism=4`, `--require test_rule_integrity=13`, `--require test_tenant_security=28` or `--require test_auth_session_security=10` in `ci.yml` in the same PR. The gate fails if the count drops below that number.

## Security checks

`repo_hygiene.py` checks every file Git ships. It prints only paths, line numbers and rule names, never the matched content. It fails on:

- committed `.env` files other than `*.example`, `*.sample` and `*.template`
- private key blocks, key and keystore files (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_rsa`…), credential files and `secrets/` directories
- high-confidence token formats: AWS, GitHub, Slack, Stripe, Google and Anthropic keys
- database URLs with a non-placeholder password
- SQLite databases (detected by extension or file header) and anything under `backend/var/`
- committed virtualenvs, `node_modules` or `.next`

Run it locally before committing with `python .github/scripts/repo_hygiene.py`.

**Every tracked env file fails the check, with no exceptions.** `frontend/.env.local` was committed before the `.gitignore` rule existed. Remove it from the index (`git rm --cached frontend/.env.local`, which keeps your local copy) and rely on `frontend/.env.example`. The `.env.*` rule then ignores the local file.

**npm advisories (report only, not blocking):**

| Package | Severity | Direct dependency? | Fix |
|---|---|---|---|
| `postcss` | high | no | Next.js 16 (semver-major) |
| `next` | moderate | yes | Next.js 16 (semver-major) |

Making `npm audit` blocking now would turn every run red until the Next.js 16 upgrade is decided. Revisit that once the upgrade is decided.

**Recommended GitHub-native features** (repository settings → Code security; may need GitHub Advanced Security on private repositories):

- Dependabot alerts and security updates for npm, pip and GitHub Actions.
  - The action pins in the workflows are commit SHAs with a version comment, which Dependabot can update.
- Secret scanning with push protection.
- Code scanning (CodeQL default setup) for Python and JavaScript/TypeScript.
- Dependency review on pull requests.

## Artifacts

| Artifact | From | Retention | Contents |
|---|---|---|---|
| `frontend-build-metadata` | CI | 14 days | build log, `BUILD_ID`, route and chunk manifests, `build-metadata.json` |
| `backend-unit-reports`, `backend-postgres-reports` | CI | 14 days | JUnit XML plus gate summaries |
| `security-advisories` | CI | 14 days | `npm-audit.json` |
| `backend-requirements-lock` | CI | 30 days | `pip freeze` of the set the PostgreSQL suite passed with |
| `release-<sha>` | Deploy | 30 days | `backend-src.tar.gz` (tracked files only, via `git archive`), the lock, `SHA256SUMS`, `release.json` |
| `deployment-record-<env>-<sha>` | Deploy | 90 days | step outcomes and verification results |

Never uploaded: `.env` files, keys, databases, `backend/var/`, credentials, local state, and `.next/prerender-manifest.json` (it holds Next.js preview-mode signing keys).

## Deployment flow and gating

**When Deploy runs.** Deploy starts only when **CI succeeded** for a **push to `main`** of **this repository**. It never runs for pull requests (including a fork branch named `main`), manual CI runs or other branches.

**What gets released.** It releases exactly the commit CI verified (`head_sha`), not whatever `main` points to later.

**Order of steps in each environment** (`deploy-environment.yml`):

1. CI passes; `build-release` packages the immutable backend release.
2. Validate configuration. Fails closed, with **DEPLOYMENT TARGET REQUIRED** when no target is set.
3. Verify the release bundle checksums and commit.
4. Build the frontend for this environment. `NEXT_PUBLIC_API_URL` is compiled into the bundle, so each environment builds its own frontend from the same commit and lockfile.
5. Prepare the migration tool from the release bundle (`backend-src.tar.gz` and the dependency lock, in a fresh virtualenv).
6. Back up the database. Target-specific; currently **DEPLOYMENT TARGET REQUIRED**. Migrations never run without it.
7. Apply approved migrations. Only this step receives `ZA_MIGRATION_DATABASE_URL`. It runs:
   - `python -m app.migrate status`: exit 2 (a refused state) stops the deployment, with nothing changed
   - `upgrade`: one transaction under the migration lock
   - `check`: read-only verification
8. Deploy the backend. Currently **DEPLOYMENT TARGET REQUIRED**. The API refuses to start unless the schema is at the release's head revision, and never changes it.
9. Verify the backend.
10. Deploy the frontend. Currently **DEPLOYMENT TARGET REQUIRED**.
11. Verify the frontend, and the frontend → backend path.
12. Record the result: the job summary, the `deployment-record` artifact, and the environment's deployment history.

**Production gating.** Production runs only after staging has deployed and verified, and only after a reviewer approves the `production` environment.

**Concurrency.** Deployments run one at a time and are never cancelled midway.

**Failure behaviour.** Any failed step stops the job, so later steps never run. A release is never exposed after a failed backup, migration or health check. A failed migration rolls back completely, so the previous release keeps running against an unchanged database.

**Requirements for the target implementation:**

- Deploy each new revision outside live traffic (an inactive slot or new revision).
- Switch traffic only after its verification step passes.
- Keep the previous revision available as the rollback target.
- Rollback means redeploying the previous `release-<sha>` while it is still retained, or the previous revision.
- Rollback must never involve a database reset or seed.
- After a migration, the previous release refuses to start on the newer schema. Recover forward, or restore the pre-migration backup ([database-migrations.md](database-migrations.md#rollback-and-forward-recovery)).

### Post-deploy verification (`deploy_healthcheck.py`)

The check uses only endpoints that already exist and sends no credentials:

| Check | Request | Expected |
|---|---|---|
| Backend health | `GET /api/health` | 200, `status: ok`; retried while the rollout settles |
| API reachable | `GET /api/dictionary` | 200 |
| Database connectivity | `GET /api/watermark` | reads the record sequence |
| Auth boundary | `GET /api/me` with no token, with a bad token, and with only `X-Actor-Id` | 401 each time; the last must say "Sign in required", proving the dev identity header is off |
| Demo mode off | `GET /api/auth/demo-accounts` | 404 |
| Frontend reachable | `GET /login` | 200 HTML |
| Frontend API config | the `/login` client bundle | contains the expected `NEXT_PUBLIC_API_URL` |
| Frontend → backend | CORS preflight from the frontend origin | allowed |

To run it locally:

```
python .github/scripts/deploy_healthcheck.py --api-base http://localhost:8000/api --frontend-url http://localhost:5173
```

## GitHub settings to configure

These are repository settings. Nothing in the workflows can configure them.

### Environments

Create both under Settings → Environments.

**`staging`**
- Deployment branches: `main` only.

**`production`**
- Required reviewers: at least one person who is not the author.
- Prevent self-review.
- Deployment branches: `main` only.
- Optionally, a wait timer.

### Branch protection (or a ruleset) on `main`

- Require a pull request before merging, with at least one approval, and dismiss stale approvals.
- Require these status checks to pass: `frontend`, `backend-unit`, `backend-postgres`, `security` and `ci-success`.
  - Checks appear in the picker after the first CI run.
- Require branches to be up to date before merging.
- Block force pushes and deletions, and apply the rules to administrators.
- Production can't deploy when checks fail: Deploy only runs after a successful CI run on `main`, and production requires staging to succeed first.

## Required secrets and variables

These are names only. Set values per environment, never in the repository, in workflow YAML or in frontend variables.

**CI:** none.

**Environment secrets** (`staging` and `production`):

| Name | Purpose |
|---|---|
| `ZA_DATABASE_URL` | Runtime role (`assure_app`, NOSUPERUSER, NOBYPASSRLS). Given to the running backend as `DATABASE_URL`, and to the migration step, which provisions the role with this password. |
| `ZA_MIGRATION_DATABASE_URL` | Owner/migration role. Given to the migration step only, as `DATABASE_ADMIN_URL`. |
| Deployment-target credentials | Named when the target is chosen. Prefer OIDC federation over long-lived keys. |

**Environment variables** (non-secret):

| Name | Purpose |
|---|---|
| `ZA_DEPLOY_TARGET` | The chosen target. Deployment fails closed while it is empty. |
| `ZA_FRONTEND_URL` | https origin of the frontend |
| `NEXT_PUBLIC_API_URL` | https API base ending in `/api`, compiled into that environment's frontend |
| `ZA_CORS` | allowed browser origins; must include `ZA_FRONTEND_URL` |
| `ZA_CELL_ID` | cell identifier |
| `ZA_DEMO_MODE` | must be `0` |

Optional backend runtime settings: `ZA_TSA_URL`, `ZA_SESSION_HOURS`, and `ZA_DATA_DIR` on persistent storage (it holds encrypted evidence objects and keys).

**Never set in staging or production:** `ZA_DEV_IDENTITY_HEADER`, `ZA_DEMO_PASSWORD`, or `ZA_DEMO_MODE=1`. The post-deploy checks fail if the dev identity header or demo mode is active.

## Current deployment blockers

1. **DEPLOYMENT TARGET REQUIRED.** The repository defines no hosting target: no Dockerfile, infrastructure code or platform config (G-10). Choose one, then:
   - implement the two deploy steps and the pre-migration backup step in `deploy-environment.yml`
   - provide persistent, backed-up storage (and key management) for `ZA_DATA_DIR`
   - set the variables and secrets above
2. **Resolved by Stage 1 (M-01): versioned migrations.** The schema is now versioned Alembic revisions, applied by `python -m app.migrate` in the deploy's own migration step ([database-migrations.md](database-migrations.md)). The step is implemented, and runs once the backup step before it exists.
3. **Partly resolved: the API and the owner credential.**
   - **Resolved:** API start-up no longer performs DDL. It only checks, read-only, that the schema is at its head revision and that the runtime role is least-privilege.
   - **Still open:** start-up still needs `DATABASE_ADMIN_URL` for those checks, the one-time account bootstrap and the demo seed, so the running backend still holds the owner credential.
   - **Next:** before the first production deployment, a backend change should let the API serve with the runtime role only. This depends on the system-job design in Stage 2 of [backend-architecture-roadmap.md](backend-architecture-roadmap.md).

## Troubleshooting

### frontend

**`npm ci` fails**
- `package-lock.json` is out of sync with `package.json`. Run `npm install` in `frontend/` and commit the lockfile.

**tsc or build errors**
- Reproduce with `cd frontend && npm ci && npx tsc --noEmit && npm run build`.
- A clean checkout has no `next-env.d.ts` or `.next/`. Delete both locally to reproduce exactly.

### backend-unit

**`pip check` fails**
- A requirement conflict. Pin or adjust `backend/requirements.txt`.

**Gate reports an "unexpected reason" skip**
- Only `PostgreSQL-only` skips are allowed outside PostgreSQL.

### backend-postgres

**"did not become healthy"**
- The container log tail is printed in the step.

**"not a PostgreSQL URL"**
- The provisioning step failed or was skipped. Look at it first.

**RLS gate shows skipped tests**
- The tests ran without PostgreSQL. This is the failure the gate exists to catch.

**The migration step fails with "expected an empty database"**
- The RLS database was not fresh. CI creates it in the provisioning step; locally, use a new database.

**Only one test fails, and only on a reused local database**
- Use a fresh database per run. A reused database points at evidence files from an earlier session.

**Reproduce locally** against a disposable PostgreSQL 17 database, never a shared one:
```
# create role assure (NOSUPERUSER CREATEROLE) and database assure_test owned by it, then:
cd backend
export ZA_TEST_DATABASE_ADMIN_URL="postgresql+psycopg://assure:<owner-password>@127.0.0.1:<port>/assure_test"
export ZA_TEST_DATABASE_URL="postgresql+psycopg://assure_app:<app-password>@127.0.0.1:<port>/assure_test"
python -m pytest tests --junitxml=report.xml
python ../.github/scripts/junit_gate.py --report report.xml --no-skips --require test_rls=5 --require test_migrations=16 --require test_immutability=7 --require test_snapshot_determinism=4 --require test_rule_integrity=13
```

### security

**Hygiene failure**
- Remove the file from the repository, and rotate the credential if it was real.
- Deleting it in a later commit doesn't remove it from history.

### Deploy

**DEPLOYMENT TARGET REQUIRED**
- Expected until a hosting target and its backup step are implemented.

**Migrations refused** (the `status` step exited 2)
- The database has drifted from the recorded baseline, has an unknown revision, or has an inconsistent version table. Nothing was changed.
- See [database-migrations.md](database-migrations.md#troubleshooting).

**Configuration errors**
- Each missing or invalid setting is listed by name.

**Verification failures**
- The step summary shows each check with its HTTP status.
- A failure after a deploy step means traffic must stay on, or return to, the previous revision.
