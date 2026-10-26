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
| `backend-postgres` | PostgreSQL 17 → RLS tests → full backend suite (details below) | the database is unavailable; any RLS test skipped or failed; any failure, error or skip in the full suite |
| `security` | repository hygiene; `npm audit` as a report only | committed secrets or private runtime files (npm advisories never fail it) |
| `ci-success` | aggregates the four jobs above | any of them not `success` |

`next lint` is deliberately not run. The project has no ESLint configuration, and the command would prompt interactively. Add a lint step once a configuration is committed.

CI references **no** repository or environment secrets. The frontend job sees only public `NEXT_PUBLIC_*` values.

## How the PostgreSQL tests work

1. A `postgres:17` container starts inside the job, listening on the runner's loopback only. It is removed when the job ends.
   - It is started by a step rather than as a `services:` container, because service containers can't receive a password generated during the run.
2. Three passwords are generated per run with `openssl rand` and masked in the log.
   - **Superuser:** reaches only the container. It is never exported to the backend or the tests.
   - **Owner `assure`:** created by `ci_postgres_bootstrap.sql` as NOSUPERUSER with CREATEROLE (least privilege, as the backend's start-up check requires). The password reaches psql through an environment variable, never a command line.
   - **Runtime `assure_app`:** created by the backend's own bootstrap. The RLS policies name this role, so the name is fixed.
3. Two fresh databases are created, `assure_test_rls` and `assure_test`. Each pytest session needs its own database: the suite stores evidence payloads in a per-session temporary directory, so a reused database points at files that no longer exist. Separate databases avoid any reset, and there is no `DROP` anywhere in CI.
4. `ZA_TEST_DATABASE_URL` and `ZA_TEST_DATABASE_ADMIN_URL`, the variables `tests/conftest.py` reads, are written to the job environment. A preflight step refuses to continue unless they are PostgreSQL URLs pointing at server version 17. Otherwise `conftest.py` would silently fall back to SQLite.
5. **RLS gate:** `tests/test_rls.py` runs on `assure_test_rls`. pytest exits 0 when tests skip, so `junit_gate.py` requires **5 RLS tests executed and passed, none skipped**. If they skip, the job is red.
6. **Full suite:** runs on `assure_test`. Its gate requires zero failures, errors and skips, the 5 RLS tests and the 3 `test_pg_bootstrap.py` tests.
7. Reports are scrubbed of the generated passwords before upload.

`ZA_DEMO_MODE=0` is set for the job. `tests/conftest.py` opts its own pytest process into the synthetic Customer Zero fixture, which is seeded only into these throwaway databases.

To change the number of RLS tests, update `--require test_rls=5` in `ci.yml` in the same PR. The gate fails if the count drops below that number.

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
5. Apply approved migrations. Currently **BLOCKED — VERSIONED MIGRATION SYSTEM REQUIRED**.
6. Deploy the backend. Currently **DEPLOYMENT TARGET REQUIRED**.
7. Verify the backend.
8. Deploy the frontend. Currently **DEPLOYMENT TARGET REQUIRED**.
9. Verify the frontend, and the frontend → backend path.
10. Record the result: the job summary, the `deployment-record` artifact, and the environment's deployment history.

**Production gating.** Production runs only after staging has deployed and verified, and only after a reviewer approves the `production` environment.

**Concurrency.** Deployments run one at a time and are never cancelled midway.

**Failure behaviour.** Any failed step stops the job, so later steps never run. A release is never exposed after a failed migration or health check.

**Requirements for the target implementation:**

- Deploy each new revision outside live traffic (an inactive slot or new revision).
- Switch traffic only after its verification step passes.
- Keep the previous revision available as the rollback target.
- Rollback means redeploying the previous `release-<sha>` while it is still retained, or the previous revision.
- Rollback must never involve a database reset or seed.

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
| `ZA_DATABASE_URL` | Runtime role (`assure_app`, NOSUPERUSER, NOBYPASSRLS). Given to the running backend as `DATABASE_URL`. |
| `ZA_MIGRATION_DATABASE_URL` | Owner/migration role. Given to the migration step only. |
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

1. **DEPLOYMENT TARGET REQUIRED.** The repository defines no hosting target: no Dockerfile, infrastructure code or platform config. Choose one, then:
   - implement the two deploy steps in `deploy-environment.yml`
   - provide persistent, backed-up storage (and key management) for `ZA_DATA_DIR`
   - set the variables and secrets above
2. **BLOCKED — VERSIONED MIGRATION SYSTEM REQUIRED.** The schema comes from `create_all` plus `db/postgres_rls.sql`. A versioned, reviewed migration framework (planned: Alembic, DAT-REQ-023/039) must exist before any staging or production database is touched.
3. **Backend start-up performs schema DDL.** On every start, `app/main.py` runs `pg_bootstrap.bootstrap()`, which creates tables, applies RLS and provisions the runtime role with the owner credential. On PostgreSQL it refuses to start without that owner URL. Deploying it unchanged would alter staging and production schemas outside the migration step. Before the first deployment, a backend change must let the API serve with the runtime role only, while migrations run as their own step.

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

**Only one test fails, and only on a reused local database**
- Use a fresh database per run. A reused database points at evidence files from an earlier session.

**Reproduce locally** against a disposable PostgreSQL 17 database, never a shared one:
```
# create role assure (NOSUPERUSER CREATEROLE) and database assure_test owned by it, then:
cd backend
export ZA_TEST_DATABASE_ADMIN_URL="postgresql+psycopg://assure:<owner-password>@127.0.0.1:<port>/assure_test"
export ZA_TEST_DATABASE_URL="postgresql+psycopg://assure_app:<app-password>@127.0.0.1:<port>/assure_test"
python -m pytest tests --junitxml=report.xml
python ../.github/scripts/junit_gate.py --report report.xml --no-skips --require test_rls=5
```

### security

**Hygiene failure**
- Remove the file from the repository, and rotate the credential if it was real.
- Deleting it in a later commit doesn't remove it from history.

### Deploy

**DEPLOYMENT TARGET REQUIRED / BLOCKED — VERSIONED MIGRATION SYSTEM REQUIRED**
- Expected until the blockers above are resolved.

**Configuration errors**
- Each missing or invalid setting is listed by name.

**Verification failures**
- The step summary shows each check with its HTTP status.
- A failure after a deploy step means traffic must stay on, or return to, the previous revision.
