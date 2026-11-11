# Zoiko Assure

Zoiko Assure is a governed compliance and assurance platform for multi-entity, multi-jurisdiction organisations. It
works through a chain of steps:
1. Encode regulations as versioned, reviewed rules.
2. Determine which obligations apply to which legal entities and assets.
3. Map those obligations to controls, tests and evidence.
4. Record every determination, attestation and evidence event, so that a reviewer can trace and re-check each conclusion.

> Zoiko Assure answers one question with evidence: for a named requirement version and a given period, what is proven,
> what is not, and why. It reports evidential states (`PROVEN`, `NOT_PROVEN`, `FAILING`, `REQUIRES_REVIEW`), never an
> unqualified "compliant". It supports audit and review; it does not replace auditors or guarantee compliance.

**Status:** a development build, **not production-ready**.
- The design comes from the ZA-* engineering specifications. Their status is "Proposed for lock", which is not approval.
- Open security findings and the remaining work are listed in the [architecture audit](docs/backend-architecture-audit.md) and the [roadmap](docs/backend-architecture-roadmap.md).

New to the code? Start with **[ARCHITECTURE.md](ARCHITECTURE.md)**: system design, code map, request flow, security
model and glossary.

## Project Structure

```
Zoiko-Assure-Platform/
├── backend/                 # FastAPI API (Python)
│   ├── app/                 # main.py, auth, security, models, migrate command
│   │   ├── routers/         # HTTP endpoints, all mounted under /api
│   │   └── engines/         # domain logic: applicability, rules, assurance, evidence, IPE, keys
│   ├── migrations/          # versioned schema migrations (0001–0004) and the recorded baseline
│   ├── tests/               # pytest suites (SQLite and PostgreSQL)
│   ├── tools/               # one-off import of the old SQLite demo database
│   ├── verifier/            # offline audit-package verifier
│   ├── requirements.txt
│   └── .env.example         # backend configuration template
├── frontend/                # Next.js web application
│   ├── app/                 # routes (App Router)
│   ├── components/          # application shell, navigation, shared UI
│   ├── lib/                 # API client, roles, formatting
│   └── .env.example         # frontend configuration template
├── docs/                    # migrations runbook, CI/CD, architecture audit, roadmap, decision briefs
├── .github/                 # CI and deployment workflows, CI helper scripts
├── ARCHITECTURE.md
└── README.md
```

`backend/var/` is created at run time. It holds the encrypted evidence store, packages, checkpoints and development
keys, and is git-ignored.

## Core Components

| Component | Purpose | State |
|---|---|---|
| Regulatory content and rules | Sources, provisions and requirement versions. A rule draft must pass validation, golden vectors, peer and legal review, and shadow impact before a signed content release | Implemented. Releases are signed with the shared cell key, not a dedicated content key, and evaluation does not yet check signatures (I-05, A9) |
| Applicability | Deterministic evaluation of released rules against bitemporal facts, giving `APPLIES`, `DOES_NOT_APPLY` or `UNCERTAIN` with a trace. Also fact requests, and human attestation or challenge of determinations | Implemented |
| Controls and testing | Control implementations, test policies, test runs, and control evaluation with reason codes | Implemented |
| Assurance roll-up | Satisfaction expressions over controls, period segmentation, and immutable assurance snapshots with a dependency digest and a reproduction check | Implemented. Reproduction is not yet stable against later events (I-03) |
| Evidence and integrity | Encrypted evidence store, per-tenant hash-chained ledger, Merkle checkpoints, legal holds, erasure, signed audit packages and an offline verifier | Implemented with local stand-ins: file-based keys, and no RFC 3161 timestamping (P-05, P-06) |
| IPE (completeness and accuracy) | Source runs, population manifests, IPE evaluations, anomalies and dispositions | Implemented |
| Governance | Exceptions, compensating controls, issues, risk acceptances and attestations, kept separate from assurance state | Implemented. Some status changes are still made in place rather than appended (I-04) |
| AI and agent assets | AI asset register, versions, authority grants and policy decisions | Implemented |
| Organisations and tenancy | Customer organisations (tenants), legal entities and subjects. A platform Super Admin keeps the organisation registry, with no access to customer evidence | Implemented for a single cell (`eu-west-cell-a`) |
| Identity and authorisation | Password sign-in, server-side sessions, and an authorisation guard on every state-changing route | Partial: no SSO, SCIM or MFA, and the browser token is kept in `localStorage` (P-12, I-06) |
| Database enforcement | PostgreSQL 17 with row-level security on 41 tenant tables, an append-only ledger and audit log, immutable completed results and published catalogue, and versioned migrations | Implemented, with open findings: the runtime role can switch on the RLS bypass (I-01), RLS is not forced (P-02), and the owner role can disable the immutability triggers (A10) |
| Audit history and events | Append-only audit log of state transitions and a transactional outbox | Partial: outbox events are written but not delivered to a broker (P-04) |

The IDs in the State column are findings in the [architecture audit](docs/backend-architecture-audit.md).

## Frontend

Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS v4 and lucide-react icons. It runs at
**http://localhost:5173**.

The home page `/` shows one of two dashboards, depending on who signs in:
- **Organisation Dashboard**, for users of a customer organisation.
- **Super Admin dashboard**, for Zoiko platform administration. It covers the organisation registry only, with no customer evidence or assurance data.

Zoiko content staff land on their regulatory-content workspace.

The navigation groups are listed below. Each person sees only the screens their role uses.

| Group | Screens |
|---|---|
| Regulatory Content | Regulatory Inbox, Sources & Provisions, Rule Authoring, Rule Test Workbench |
| Applicability | Entities & Facts, Applicability Review, Fact Requests, Applicability Trace |
| Assurance | Obligation Dossiers, Controls & Tests, Assurance Periods, Exceptions & Issues |
| Evidence | Evidence Records, IPE, Source Runs |
| Audit & History | Audit Package Builder, Historical Reconstruction, Ledger |
| AI & Agents | AI/Agent Asset Dossiers |
| Integrations | Connectors |
| Administration & Trust | Users & Roles, Identity Integrations, Cells & Residency, Content Releases, Retention & Legal Holds, Privileged Access Log, Controlled Dictionary |

Some administration screens are not functional yet:
- **Users & Roles:** self-service provisioning and role assignment are not available yet.
- **Identity Integrations, Retention & Legal Holds, Privileged Access Log:** not enabled yet.

The frontend talks only to the API, never to the database. Its address comes from `NEXT_PUBLIC_API_URL`, default
`http://localhost:8000/api`. Screens a role cannot use are hidden for convenience only: the backend enforces every
permission and tenant boundary.

## Backend

Python 3.12, FastAPI, SQLAlchemy 2, psycopg 3 and Alembic. `cryptography` provides AES-256-GCM envelope encryption,
HKDF, HMAC-SHA256 and Ed25519 signatures. A single process serves every route under `/api` at
**http://127.0.0.1:8000**.

| Module | Responsibility |
|---|---|
| `app/main.py` | Application set-up, read-only start-up checks, CORS and routers. Refuses to start if a state-changing route has no authorisation guard |
| `app/auth.py`, `app/security.py`, `app/accounts.py` | Sign-in and sessions (`/api/auth`), authorisation guards and tenant scoping, one-time bootstrap accounts |
| `app/routers/catalog.py`, `app/routers/rules.py` | Regulatory sources, requirement versions, rule drafts, review and signed release |
| `app/routers/applicability.py` | Facts, evaluations, determinations, fact requests, historical comparison |
| `app/routers/assurance.py` | Obligations, controls, test runs, snapshots, exceptions, compensations, issues |
| `app/routers/evidence.py` | Evidence store, ledger and checkpoints, conflicts, legal holds, audit packages, public trust key |
| `app/routers/integration.py` | Connectors, IPE source runs and anomalies |
| `app/routers/ai.py` | AI assets, authority grants, policy decisions |
| `app/routers/core.py`, `app/routers/dashboard.py`, `app/routers/platform.py` | Health, controlled dictionary, organisation and subjects, dashboards, Super Admin organisation registry |
| `app/engines/` | Domain logic: applicability, rules, facts, assurance, evidence, IPE, connectors, keys |
| `app/models.py`, `app/db.py`, `app/migrate.py` | Data model, database connections and roles, migration command |
| `verifier/verify_package.py` | Offline audit-package verifier |

**Persistence:** PostgreSQL 17 is the only runtime database. The API refuses to start without `DATABASE_URL`, and
SQLite is allowed only for isolated test runs. Two roles are used:
- `assure_app`: the runtime role. It is not the owner and is subject to row-level security. Every request runs as this role, with the caller's tenant set on its transaction.
- `assure`: the owner role. It applies migrations and runs the start-up checks, account bootstrap and demo seed.

**Files outside the database:** evidence payloads, packages, checkpoints and development keys live in `backend/var/`.
Payloads can't be decrypted without the matching keys, so back up `backend/var/` together with the database.

### Offline package verification

Audit packages are signed zip files. They can be verified without Zoiko Assure, a login or any tenant secret:

```bash
python backend/verifier/verify_package.py package.zip --trusted-key trust.pem   # add --json for machine-readable output
```

Get `trust.pem` from `GET /api/trust/public-key`. The same verifier runs behind **Audit Package Builder → Verify upload**.

## Database and Migrations

The schema is managed by versioned migrations in `backend/migrations/versions/`:

| Revision | Change |
|---|---|
| `0001` | Baseline: the 63 tables |
| `0002` | Row-level security on 41 tenant tables, runtime-role grants, an append-only evidence ledger and audit log |
| `0003` | Completed results become immutable: evaluations, snapshots, IPE evaluations, packages, attestations, checkpoints |
| `0004` | The published catalogue becomes immutable: rule versions, content releases, set versions, released fact definitions |

Run migrations only through the project's command, from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m app.migrate status    # read-only: current revision and pending migrations
.\.venv\Scripts\python.exe -m app.migrate upgrade   # owner role, one transaction; also provisions the runtime role assure_app
.\.venv\Scripts\python.exe -m app.migrate check     # read-only: head revision, no schema drift, RLS coverage
```

**Rules:**
- **Start-up is read-only.** The API checks that both roles reach the same database, that the schema is at this build's head revision, and that the runtime role is least-privilege. Otherwise it refuses to start. It never creates or changes the schema.
- **Back up before you upgrade** a database that holds data, following the [migrations runbook](docs/database-migrations.md).
- **`downgrade` is for disposable databases and rehearsals only.** Never run it against your development database.
- **Never stamp a revision by hand.** Running raw `alembic upgrade`, `stamp` or `downgrade` is refused.
- **Databases from before versioned migrations:** `upgrade` validates the existing schema against the recorded baseline and adopts it in place. If the schema doesn't match, it refuses and changes nothing.

The runbook covers database states, backups, recovery, troubleshooting and how to write a migration.

### Importing the old SQLite demo database (one time)

Earlier builds could run on `backend\var\assure.db`. `tools\migrate_sqlite_to_postgres.py` copies it into a PostgreSQL
database that is already migrated. It never creates tables. The import:
- inserts only, so a repeat run adds nothing;
- runs in one transaction;
- re-verifies every tenant's evidence ledger;
- opens the SQLite file read-only.

Its docstring lists every guarantee. Back up first, then dry-run, then apply, from `backend/`:

```powershell
New-Item -ItemType Directory -Force var\backups | Out-Null
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" -h 127.0.0.1 -p 5433 -U assure -d assure --format=custom --file "var\backups\assure-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py            # dry run: migrates and verifies, then rolls back
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py --apply    # commits; prints before/after counts per table
```

## Local Development

The commands are for Windows PowerShell and start in the repository root.

### Prerequisites

| Tool | Version |
|---|---|
| PostgreSQL | 17, listening on `127.0.0.1:5433` (local service, or Docker as shown below) |
| Python | 3.12 (the version CI uses) |
| Node.js and npm | Node.js 22 (the version CI uses) |

| Port | Service |
|---|---|
| `5173` | Frontend |
| `8000` | Backend API |
| `5433` | PostgreSQL, database `assure` |

### 1. Check that PostgreSQL is running

```powershell
Get-Service postgresql-x64-17                                                     # Status: Running
& "C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h 127.0.0.1 -p 5433        # accepting connections
# if it is stopped (administrator PowerShell):  Start-Service postgresql-x64-17
```

### 2. Create the database and owner role (once per machine)

The database `assure` is owned by the role `assure` (`LOGIN NOSUPERUSER CREATEROLE NOCREATEDB`). The runtime role
`assure_app` is created later by `app.migrate upgrade`. Create the owner as the PostgreSQL superuser. psql prompts for
its password; never save it in a file.

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -p 5433 -U postgres
#   CREATE ROLE assure LOGIN NOSUPERUSER CREATEROLE NOCREATEDB PASSWORD '<owner-password>';
#   CREATE DATABASE assure OWNER assure;
# verify as the owner (prompts for the owner password):
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -p 5433 -U assure -d assure -c "select current_user, current_database(), version();"
```

### 3. Configure and install

```powershell
Set-Location backend
Copy-Item .env.example .env          # then set the assure_app and assure passwords in backend\.env (git-ignored)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location ..\frontend
npm ci                               # installs exactly package-lock.json
Set-Location ..
```

In `backend\.env`:
- `DATABASE_URL` is the runtime role (`assure_app`).
- `DATABASE_ADMIN_URL` is the owner role (`assure`).

Both point at `127.0.0.1:5433/assure`. Real environment variables override `backend\.env`.

The frontend needs no configuration for local use. To change the API address, copy `frontend\.env.example` to
`frontend\.env.local`.

### 4. Apply the migrations

Do this once, and again after a pull that adds a migration. If the database holds data, back it up first
([runbook](docs/database-migrations.md)).

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m app.migrate status
.\.venv\Scripts\python.exe -m app.migrate upgrade
.\.venv\Scripts\python.exe -m app.migrate check     # expect "check passed"
Set-Location ..
```

### 5. Start the backend and frontend (two terminals)

```powershell
# Terminal 1: backend, http://127.0.0.1:8000
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: frontend, http://localhost:5173
Set-Location frontend
npm run dev
```

A good backend start logs a line like
`Database: PostgreSQL 17.x at 127.0.0.1:5433/assure (runtime role assure_app, owner role assure); schema revision 0004`,
then `Application startup complete.` If the database is unreachable, misconfigured or not at the head revision, the
backend stops with an error instead of starting.

Open the app at exactly **http://localhost:5173**: `ZA_CORS` allows only that origin. Stop each server with **Ctrl+C**.

### 6. Sign in

Open http://localhost:5173 and you are sent to `/login`. A normal start seeds nothing, so accounts come from one of two
places.

**Bootstrap accounts.** These are created at start-up from `ZA_BOOTSTRAP_*` settings in `backend\.env`; the template
documents each one.
- A Super Admin, who manages customer organisations and has no access to customer data.
- An Organisation Admin, for an organisation that already exists.

**Demo mode.** With `ZA_DEMO_MODE=1` and a local `ZA_DEMO_PASSWORD`, the first start on an **empty** database does two
things:
- It loads the synthetic **Customer Zero** fixture: Zoiko Group, with a UK and an EU entity, AI assets, the DORA / AI Act / UK GDPR catalogue, signed rule releases, evidence, exceptions and snapshots.
- It creates a demo account for every seeded person, as `<actor-id>@zoiko.example` with the password you chose.

If the frontend also has `NEXT_PUBLIC_ZA_DEMO_MODE=1`, the sign-in page lists the account emails. It never shows a
password.

| Demo account | Try |
|---|---|
| `actor-cco` (Chief Compliance Officer) | Attest determinations, read everything for the tenant |
| `actor-risk` (risk approver) | Approve exceptions and compensations (requester ≠ approver) |
| `actor-owner` (control owner) | Upload evidence, record test results, request exceptions |
| `actor-aigov` (AI governance) | Register AI assets, issue authority grants |
| `actor-content-analyst` → `actor-encoder` → `actor-content-legal` → `actor-release` | Rule authoring, review and signed release; no tenant data access |
| `actor-audit` (internal audit), `actor-ext-auditor` (external auditor) | Read, build and verify packages |

How sign-in behaves:
- Passwords are scrypt-hashed.
- Five failed attempts lock an account for 15 minutes. A failed sign-in doesn't reveal whether the account exists.
- **Sign out** revokes the session on the server.
- **Changing a password** (`POST /api/auth/password`) needs the current password and signs out your other sessions. The account menu doesn't offer it.
- An account issued a temporary password (a new organisation administrator, for example) must set a new password before any page opens.
- Agents and service identities cannot sign in interactively.

### PostgreSQL in Docker (alternative)

The repository has no Docker Compose file or application images. On a machine without a local PostgreSQL service,
run PostgreSQL alone in a container on the same port, then continue from step 3:

```powershell
docker run -d --name zoiko-assure-pg --restart unless-stopped -e POSTGRES_USER=assure -e POSTGRES_PASSWORD=<owner-password> -e POSTGRES_DB=assure -p 127.0.0.1:5433:5432 -v zoiko_assure_pgdata:/var/lib/postgresql/data postgres:17-alpine
```

The container's `assure` user is a superuser. The API's least-privilege checks apply to `assure_app` either way.

### Configuration

The full list is in `backend/.env.example` and `frontend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(required)* | Runtime role (`assure_app`). Without it the API refuses to start |
| `DATABASE_ADMIN_URL` | = `DATABASE_URL` | Owner role: migrations, start-up checks, account bootstrap, demo seed |
| `ZA_DB_CONNECT_TIMEOUT` | `10` | Seconds to wait for a PostgreSQL connection |
| `ZA_DATA_DIR` | `backend/var` | Evidence store, packages, checkpoints, development keys |
| `ZA_CORS` | `http://localhost:5173` | Allowed browser origins (exact origins only) |
| `ZA_SESSION_HOURS` | `8` | Session lifetime |
| `ZA_DEMO_MODE` | `0` | `1` seeds the Customer Zero fixture into an empty database |
| `ZA_DEMO_PASSWORD` | *(none)* | Local demo password. Never commit it |
| `ZA_BOOTSTRAP_*` | *(none)* | One-time Super Admin and Organisation Admin accounts |
| `ZA_TSA_URL` | *(unset)* | Records the intended RFC 3161 timestamp service. Timestamping is not implemented yet: checkpoints stay `TSA_PENDING` |
| `ZA_DEV_IDENTITY_HEADER` | `0` | `1` re-enables the `X-Actor-Id` impersonation header used by tests. Never on a shared server |
| `ZA_ALLOW_SQLITE` | `0` | Test runs only (`tests/conftest.py` sets it) |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api` | Frontend: API base address |

## API Documentation and Health Checks

With the backend running:

| URL | Content |
|---|---|
| http://127.0.0.1:8000/docs | Interactive API documentation (Swagger UI) |
| http://127.0.0.1:8000/redoc | API reference (ReDoc) |
| http://127.0.0.1:8000/openapi.json | OpenAPI schema |
| http://127.0.0.1:8000/api/health | Liveness: `status`, cell ID and dictionary version. No sign-in; it does not query the database |

There is no separate readiness endpoint. The API does not start unless its database checks pass. Most other endpoints
need a signed-in session.

## Testing

### Backend tests (SQLite)

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q
```

These use a throwaway SQLite database and never touch your PostgreSQL databases. Tests that need PostgreSQL are
skipped.

### Tests on PostgreSQL

The full suite includes the row-level security, migration, immutability and snapshot proofs. It needs:
- an **empty** PostgreSQL test database, which it migrates and seeds itself;
- `ZA_TEST_DATABASE_URL` and `ZA_TEST_DATABASE_ADMIN_URL` set to that database.

It never reads `DATABASE_URL`. You can keep both variables in `backend\.env` and load them into the session:

```powershell
# once, or whenever the test database holds data from an earlier run (as the PostgreSQL superuser; deletes test data only):
#   DROP DATABASE IF EXISTS assure_test;  CREATE DATABASE assure_test OWNER assure;
Set-Location backend
Get-Content .env | Where-Object { $_ -match '^ZA_TEST_DATABASE_(ADMIN_)?URL=' } | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "Env:$k" $v }
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:ZA_TEST_DATABASE_URL, Env:ZA_TEST_DATABASE_ADMIN_URL
```

Roles belong to the whole PostgreSQL server, not one database. Use the same `assure_app` password in the test URLs as
in `DATABASE_URL`. Otherwise the test run's migration step changes the runtime role's password, and the development
API can no longer connect.

### Frontend checks

The frontend has no test suite. CI type-checks and builds it, from `frontend/`:

```powershell
npx tsc --noEmit     # type-check
npm run build        # production build (next build)
```

`npm run lint` is defined, but there is no ESLint configuration yet, so CI does not run it.

### Continuous integration

`.github/workflows/ci.yml` defines five jobs:
- `frontend`: the frontend checks above.
- `backend-unit`: the SQLite backend suite.
- `backend-postgres`: the full backend suite on a disposable PostgreSQL 17. Its test gate requires minimum counts of RLS, migration, immutability and snapshot-determinism tests.
- `security`: a repository hygiene scan, and an npm advisory report that does not block.
- `ci-success`: the single required check. It passes only when every other job succeeds.

See [docs/ci-cd.md](docs/ci-cd.md).

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, code map, request flow, security model, invariants, glossary |
| [docs/database-migrations.md](docs/database-migrations.md) | Migration runbook: states, backups, recovery, writing a migration |
| [docs/ci-cd.md](docs/ci-cd.md) | CI jobs and gates, deployment workflow, environment secrets |
| [docs/backend-architecture-audit.md](docs/backend-architecture-audit.md) | Backend audit against the specifications, findings, implementation status, security review |
| [docs/backend-architecture-roadmap.md](docs/backend-architecture-roadmap.md) | Staged remediation plan and its status |
| [docs/backend-decision-brief-snapshot-versioning.md](docs/backend-decision-brief-snapshot-versioning.md) | Decisions needed on snapshot manifest format and engine versioning |
| [docs/backend-decision-brief-content-signature-trust.md](docs/backend-decision-brief-content-signature-trust.md) | Decisions needed on content-release signature trust |
| [docs/frontend-backend-build-plan.md](docs/frontend-backend-build-plan.md), [backend/backend-build-plan.md](backend/backend-build-plan.md), [frontend/frontend-build-plan.md](frontend/frontend-build-plan.md) | Build plans |

The ZA-* engineering specifications (ARCH, DOM, PRD, GLS, DAT, TEN, IAM, SEC, APP, RUL, TIM, STA, EVD, IPE, TRC, QA
and others) are kept outside this repository.
- They are the authority for required behaviour. Where they conflict with this README or ARCHITECTURE.md, the specifications win.
- All of them are "Proposed for lock". None is approved yet, and neither is the ADR register (ZA-ADR-001).

## Current Limitations

Development stand-ins that must be replaced before production:
- **Keys:** a local file-based key store (`backend/var/keys/`) stands in for a cloud KMS/HSM. One signing key signs checkpoints, packages and content releases.
- **Identity:** local password sign-in stands in for OIDC/SAML federation and SCIM provisioning. The session token is kept in browser `localStorage`.
- **Runtime:** one process plays one cell (`eu-west-cell-a`). Outbox events are not relayed to a broker.
- **Timestamping:** RFC 3161 timestamping of checkpoints is not implemented.

**Production:** nothing in this README is production configuration, and no hosting target exists yet (G-10). When a
production database is provisioned:
- Use PostgreSQL 17 with the same two roles: an owner for schema changes (`NOSUPERUSER CREATEROLE`) and a non-owner runtime role (`NOBYPASSRLS`). The API refuses to start if the runtime role is over-privileged.
- Supply both database URLs from the deployment's secret store, as `ZA_DATABASE_URL` and `ZA_MIGRATION_DATABASE_URL`. Never put them in a committed file. Use TLS (`?sslmode=verify-full`) and credentials separate from local ones.
- Schema changes run in the deploy workflow's migration step, after a pre-migration backup. The backup step is not implemented yet.
- Keep `ZA_DEMO_MODE=0` and set `ZA_CORS` to the real origin. Never run the SQLite import tool against a production database.

See [docs/ci-cd.md](docs/ci-cd.md).
