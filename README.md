# Zoiko Assure

Regulatory obligation-and-proof infrastructure for multi-entity, multi-jurisdiction enterprises, built from the
ZA-* engineering specifications (ARCH, DOM, PRD, GLS, RUL, APP, TIM, STA, EVD, IPE, TRC).

New to the project? Start with **[ARCHITECTURE.md](ARCHITECTURE.md)**. It covers the system design, code map, request flow, security model, invariants and glossary.

Assure reports **evidential states against named requirement versions and periods**. It never shows an unqualified
"compliant" state; the controlled dictionary (ZA-GLS-001) has no such value.

| Layer    | Stack |
|----------|-------|
| Frontend | Next.js 15 (App Router), React 19, Tailwind CSS v4. Brand tokens come from the logo: navy `#154A6C`, orange `#D9822B`, slate `#5F6B70` |
| Backend  | Python 3.12+ (tested on 3.14), FastAPI, SQLAlchemy 2, `cryptography` (AES-256-GCM envelope encryption, HKDF, HMAC-SHA256, Ed25519) |
| Storage  | PostgreSQL 17 with row-level security (the only runtime database); SQLite only for isolated test runs |

## Run locally (Windows, PowerShell)

Assure runs on **PostgreSQL 17** only. The API refuses to start without `DATABASE_URL` and never falls back to SQLite.
Local ports: UI **5173**, API **8000**, PostgreSQL **5433** on `127.0.0.1`, database `assure`. Commands below start in the
repository root.

**1. Check that PostgreSQL is running** (here a Windows service; see *Alternative: PostgreSQL in Docker* below):

```powershell
Get-Service postgresql-x64-17                                                     # Status: Running
& "C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h 127.0.0.1 -p 5433        # accepting connections
# if it is stopped (administrator PowerShell):  Start-Service postgresql-x64-17
```

**2. Database and roles** (once per machine). The database `assure` is owned by the role `assure`
(`LOGIN NOSUPERUSER CREATEROLE NOCREATEDB`). The API creates and checks the non-owner runtime role `assure_app` itself.
On a new machine, create the owner once as the PostgreSQL superuser (psql prompts for its password; never save it in a file):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -p 5433 -U postgres
#   CREATE ROLE assure LOGIN NOSUPERUSER CREATEROLE NOCREATEDB PASSWORD '<owner-password>';
#   CREATE DATABASE assure OWNER assure;
# verify as the owner (prompts for the owner password):
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h 127.0.0.1 -p 5433 -U assure -d assure -c "select current_user, current_database(), version();"
```

**3. Configure and install** (once):

```powershell
Set-Location backend
Copy-Item .env.example .env          # then set the assure_app and assure passwords in backend\.env (git-ignored)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location ..\frontend
npm ci                               # installs exactly package-lock.json; frontend\.env.example shows NEXT_PUBLIC_API_URL
Set-Location ..
```

`DATABASE_URL` is the runtime role (`assure_app`, row-level security applies) and `DATABASE_ADMIN_URL` the owner role,
both on `127.0.0.1:5433/assure`. Real environment variables override `backend\.env`. The frontend only talks to the API
(`NEXT_PUBLIC_API_URL=http://localhost:8000/api`), never to the database.

**4. Schema.** There is no separate migration step yet: every API start-up applies the idempotent schema and
row-level-security bootstrap below with the owner role. A normal start seeds **nothing**.

**5. Start** (two terminals):

```powershell
# Terminal 1: backend (http://127.0.0.1:8000, API docs at /docs)
Set-Location backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: frontend (http://localhost:5173)
Set-Location frontend
npm run dev
```

A good backend start logs `Database: PostgreSQL 17.x at 127.0.0.1:5433/assure (runtime role assure_app, owner role assure)`
and then `Application startup complete.` If PostgreSQL is unreachable or misconfigured it stops with
`Cannot connect to PostgreSQL with DATABASE_URL (...)` instead of starting. Open the app at exactly
**http://localhost:5173** (`ZA_CORS` allows that origin only). Stop each server with **Ctrl+C**.

**6. Check which database the running backend uses.** Besides the start-up line, its open connections show it:

```powershell
Get-NetTCPConnection -RemotePort 5433 -State Established |
  Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)").CommandLine -match 'uvicorn|spawn_main' } |
  Select-Object OwningProcess, RemoteAddress, RemotePort
```

**Demo data.** Only in demo mode (`ZA_DEMO_MODE=1`) does the first start on an *empty* database load the synthetic
**Customer Zero** fixture (Zoiko Group: a UK and an EU entity, three AI assets, the DORA / AI Act / UK GDPR catalogue, signed
rule releases, connectors, IPE source runs, Q3-2026 evidence, exceptions and snapshots). Demo sign-in accounts are created
only if you also set a local `ZA_DEMO_PASSWORD`; none is built in.

### Importing the old SQLite demo database (one time)

Earlier builds could run on a SQLite file, `backend\var\assure.db`. `tools\migrate_sqlite_to_postgres.py` copies it into
the configured PostgreSQL database. Back up first, then dry-run, then apply:

```powershell
Set-Location backend
New-Item -ItemType Directory -Force var\backups | Out-Null      # backend\var is git-ignored
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" -h 127.0.0.1 -p 5433 -U assure -d assure --format=custom --file "var\backups\assure-$(Get-Date -Format yyyyMMdd-HHmmss).dump"
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py            # dry run: migrates and verifies, then rolls back
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py --apply    # commits; prints before/after counts per table
```

The import is insert-only and repeatable: rows already in PostgreSQL are never updated (a second run inserts nothing),
everything runs in one transaction, timestamps stay UTC, JSON and password hashes are copied unchanged, the record sequence
is only ever raised, and every tenant's evidence ledger is re-verified afterwards. If an SQLite account's email already
belongs to a different PostgreSQL account, it stops, except for the bootstrap Super Admin: that one keeps the PostgreSQL
account and skips the SQLite duplicate. The SQLite file is opened read-only. Restore a backup with `pg_restore` (as the owner,
into an empty database).

### Alternative: PostgreSQL in Docker

On a machine without a local PostgreSQL service, run it in a container on the same port, then continue from step 3:

```powershell
docker run -d --name zoiko-assure-pg --restart unless-stopped -e POSTGRES_USER=assure -e POSTGRES_PASSWORD=<owner-password> -e POSTGRES_DB=assure -p 127.0.0.1:5433:5432 -v zoiko_assure_pgdata:/var/lib/postgresql/data postgres:17-alpine
```

The container's `assure` user is a superuser; the API's least-privilege checks apply to `assure_app` either way.

### Schema bootstrap and row-level security

On API start-up, `app/pg_bootstrap.py` uses the **owner** role to:
1. Create the non-owner runtime role `assure_app` (`NOBYPASSRLS`).
2. Create the tables and apply `db/postgres_rls.sql`: row-level security on 41 tenant tables, and an append-only evidence ledger and audit log.
3. In demo mode only (`ZA_DEMO_MODE=1`), seed the Customer Zero demo fixture if the database is empty.

The API then serves every request as `assure_app`, with the caller's tenant stamped on each transaction. So **the database itself** blocks cross-tenant reads and writes, not just the application code. Three documented cell-wide platform jobs run in an explicit `system_scope`: rule-release impact re-evaluation, shadow impact (aggregate output only), and ledger checkpoints.

### Tests

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q        # throwaway SQLite (tests opt in with ZA_ALLOW_SQLITE); never touches your databases
```

**On PostgreSQL** (all 58 tests, including the database-level row-level-security proofs). The suite seeds the Customer Zero
fixture and expects an **empty** test database, as CI provides. It never reads `DATABASE_URL`; it uses
`ZA_TEST_DATABASE_URL` / `ZA_TEST_DATABASE_ADMIN_URL`, which you can keep in `backend\.env` and load into the session:

```powershell
# once, or whenever the test database holds data from an earlier run (as the PostgreSQL superuser; deletes test data only):
#   DROP DATABASE IF EXISTS assure_test;  CREATE DATABASE assure_test OWNER assure;
Set-Location backend
Get-Content .env | Where-Object { $_ -match '^ZA_TEST_DATABASE_(ADMIN_)?URL=' } | ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "Env:$k" $v }
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:ZA_TEST_DATABASE_URL, Env:ZA_TEST_DATABASE_ADMIN_URL
```

Roles belong to the whole PostgreSQL server, not one database. Use the same `assure_app` password in the test URLs as in
`DATABASE_URL`, otherwise the test bootstrap changes the runtime role's password and the dev API can no longer connect.

### When a production database is provisioned

Nothing here is production configuration. For production:
- Provision PostgreSQL 17 with the same two roles: an owner for schema changes (`NOSUPERUSER CREATEROLE`) and a non-owner
  runtime role (`NOBYPASSRLS`). The API refuses to start if the runtime role is over-privileged.
- Supply `DATABASE_URL` and `DATABASE_ADMIN_URL` from the deployment's secret store (`ZA_DATABASE_URL` and
  `ZA_MIGRATION_DATABASE_URL` in `.github/workflows/deploy-environment.yml`), never from a committed file, with TLS
  (`?sslmode=verify-full`) and credentials separate from local ones.
- Replace schema-at-start-up with versioned migrations (the deploy workflow stays blocked until then), and set up backups
  and point-in-time recovery.
- Keep `ZA_DEMO_MODE=0`, set `ZA_CORS` to the real origin, and never run the SQLite import tool against it.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(required)* from the environment or `backend/.env` | API runtime role (non-owner, RLS applies). Without it the API refuses to start |
| `DATABASE_ADMIN_URL` | = `DATABASE_URL` | Owner role for schema, RLS policies and seed |
| `ZA_DB_CONNECT_TIMEOUT` | `10` | Seconds to wait for a PostgreSQL connection before failing |
| `ZA_ALLOW_SQLITE` | `0` | Test runs only (`tests/conftest.py` sets it). Without it a SQLite `DATABASE_URL` is refused |
| `ZA_DATA_DIR` | `backend/var` | Encrypted evidence objects, WORM checkpoints, packages, dev keys, local backups |
| `ZA_CORS` | `http://localhost:5173` | Allowed browser origins (exact origins only) |
| `ZA_TSA_URL` | *(unset)* | RFC 3161 TSA. If unset, checkpoints are honestly reported as `TSA_PENDING` |

Encrypted evidence payloads, packages and dev keys live in `backend/var/`. Keep them together with the PostgreSQL database: payloads can't be decrypted without the matching keys, so back up both.

## Sign-in

Open http://localhost:5173. You're sent to **/login**. In demo mode (`ZA_DEMO_MODE=1` plus a local `ZA_DEMO_PASSWORD`), every
seeded person gets a demo account (`<actor-id>@zoiko.example`, e.g. `actor-cco@zoiko.example`) with the password you chose.
If the frontend is also built with `NEXT_PUBLIC_ZA_DEMO_MODE=1`, the sign-in page lists the account emails so you can click
to fill the email; it never shows a password.

- Passwords are scrypt-hashed. Sessions are HMAC-signed tokens backed by a server-side session record, so **Sign out** revokes the token immediately.
- Five failed attempts lock an account for 15 minutes. Error messages don't reveal whether an account exists.
- **Change password** is in the user menu (top right). It signs out your other sessions.
- Agents and service identities can't sign in interactively (INV-01).

| Account | Try |
|---|---|
| Priya Shah, CCO (`actor-cco`) | Attest determinations, read everything for the tenant |
| Rui Costa, risk approver (`actor-risk`) | Approve exceptions and compensations (requester ≠ approver) |
| Tom Becker, control owner (`actor-owner`) | Upload evidence, record test results, request exceptions |
| Lena Novak, AI governance (`actor-aigov`) | Register AI assets, issue authority grants |
| Content staff (`actor-content-analyst` authors → `actor-encoder` peer-reviews → `actor-content-legal` → `actor-release`) | Rule authoring and signed release. No tenant data access |
| Aisha Rahman, internal audit (`actor-audit`) / external auditor (`actor-ext-auditor`) | Read, build and verify packages |

| Variable | Default | Purpose |
|---|---|---|
| `ZA_DEMO_MODE` | `0` | Demo/dev only: seeds the Customer Zero fixture into an empty database and lists demo account emails on the sign-in page |
| `ZA_DEMO_PASSWORD` | *(none)* | Local demo password. Required for demo sign-in; never commit it |
| `ZA_SESSION_HOURS` | `8` | Session lifetime |
| `ZA_DEV_IDENTITY_HEADER` | `0` | `1` re-enables the `X-Actor-Id` impersonation header (the test suite uses it to simulate agents/services). Never enable it on a shared server |

## What's where

| Screen | Spec | Backend |
|---|---|---|
| Executive exposure `/` | PRD §12, STA §22 | `routers/dashboard.py` |
| Regulatory change inbox `/regulatory` | ARCH §7, DOM §4 | `routers/catalog.py` |
| Rule authoring `/rules` | RUL §§10-17 | `routers/rules.py`, `engines/rules.py` |
| Entities & facts `/entities` | APP, TIM | `routers/applicability.py`, `engines/facts.py` |
| Applicability review `/applicability`, fact requests | APP §§7-9 | `engines/applicability.py` |
| Obligation dossiers `/obligations` | STA, WF-STA-01 | `routers/assurance.py`, `engines/assurance.py` |
| Controls & tests, governance | STA §§6-15 | `routers/assurance.py` |
| Evidence vault, ledger, packages | EVD | `routers/evidence.py`, `engines/evidence.py`, `engines/keys.py` |
| Source runs & IPE, connectors | IPE, FR-CON | `routers/integration.py`, `engines/ipe.py` |
| AI / agent assets | ARCH §12 | `routers/ai.py` |
| Historical reconstruction | TIM §12 | `/api/history/compare`, snapshot reproduce |
| Administration & trust | GLS, ARCH §14 | `routers/core.py` |

## Offline package verification

Audit packages are signed zip files. They can be verified without Assure, a login or any tenant secret:

```bash
python backend/verifier/verify_package.py package.zip --trusted-key trust.pem   # add --json for machine-readable output
```

Get `trust.pem` from `GET /api/trust/public-key`. The same verifier runs behind **Audit packages → Verify upload**.

## Development-only shortcuts (replace before production)

- **Keys:** a local file-based KMS (`var/keys/`) stands in for a cloud KMS/HSM.
- **Identity:** local password sign-in stands in for OIDC/SAML federation and SCIM provisioning. Tokens are kept in browser localStorage. A production build should use an httpOnly cookie or the IdP's session.
- **Single process:** one process plays one cell (`eu-west-cell-a`). The outbox is written transactionally but not relayed to a broker.
- **Timestamping:** checkpoints are only RFC 3161-timestamped when `ZA_TSA_URL` is set.
