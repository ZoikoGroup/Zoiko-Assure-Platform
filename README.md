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
| Storage  | PostgreSQL 17 (Docker) with row-level security; SQLite fallback for zero-setup runs |

## Run locally

**Backend** (API on http://localhost:8100, interactive docs at `/docs`):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows  (Linux/macOS: .venv/bin/pip)
.venv/Scripts/python -m uvicorn app.main:app --port 8100 --reload
```

On first start the database is created and seeded with **Customer Zero** (Zoiko Group: a UK and an EU entity, three AI
assets, the DORA / AI Act / UK GDPR catalogue, signed rule releases, connectors, IPE source runs, Q3-2026 evidence,
exceptions and snapshots).

**Frontend** (http://localhost:3100):

```bash
cd frontend
npm install
npm run dev            # or: npm run build && npm run start
```

`frontend/.env.local` sets `NEXT_PUBLIC_API_URL=http://localhost:8100/api`.

Ports 3100 and 8100 are used so Assure doesn't clash with other local apps on 3000 and 8000.

**Tests:** `cd backend && .venv/Scripts/python -m pytest -q` (39 engine, API and auth tests).

### Database (PostgreSQL in Docker)

Assure runs on **PostgreSQL 17** in its own Docker container. If `backend/.env` is missing it falls back to SQLite.

```bash
# one-time: create the container (port 5433, so it doesn't clash with other local Postgres on 5432)
docker run -d --name zoiko-assure-pg --restart unless-stopped   -e POSTGRES_USER=assure -e POSTGRES_PASSWORD=<owner-password> -e POSTGRES_DB=assure   -p 127.0.0.1:5433:5432 -v zoiko_assure_pgdata:/var/lib/postgresql/data postgres:17-alpine

cp backend/.env.example backend/.env    # then put the passwords in
```

On API start-up, `app/pg_bootstrap.py` uses the **owner** role to:
1. Create the non-owner runtime role `assure_app` (`NOBYPASSRLS`).
2. Create the tables and apply `db/postgres_rls.sql`: row-level security on 41 tenant tables, and an append-only evidence ledger and audit log.
3. Seed the Customer Zero demo data if the database is empty.

The API then serves every request as `assure_app`, with the caller's tenant stamped on each transaction. So **the database itself** blocks cross-tenant reads and writes, not just the application code. Three documented cell-wide platform jobs run in an explicit `system_scope`: rule-release impact re-evaluation, shadow impact (aggregate output only), and ledger checkpoints.

Useful commands:

```bash
docker start zoiko-assure-pg                                   # after a reboot (restart policy normally handles it)
docker exec -it zoiko-assure-pg psql -U assure -d assure       # SQL shell
docker exec zoiko-assure-pg pg_dump -U assure assure > backup.sql
# reset demo data: stop the API, then
docker exec zoiko-assure-pg psql -U assure -d postgres -c "DROP DATABASE assure WITH (FORCE)" -c "CREATE DATABASE assure OWNER assure"
```

**Tests on PostgreSQL** (use a *disposable* database. The suite never reads `backend/.env`):

```bash
docker exec zoiko-assure-pg psql -U assure -d assure -c "CREATE DATABASE assure_test OWNER assure"
ZA_TEST_DATABASE_URL=postgresql+psycopg://assure_app:<app-pw>@127.0.0.1:5433/assure_test ZA_TEST_DATABASE_ADMIN_URL=postgresql+psycopg://assure:<owner-pw>@127.0.0.1:5433/assure_test .venv/Scripts/python -m pytest -q      # 44 passed, incl. 5 database-level RLS proofs
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | from `backend/.env`, else SQLite | API runtime role (non-owner, RLS applies) |
| `DATABASE_ADMIN_URL` | = `DATABASE_URL` | Owner role for schema, RLS policies and seed |
| `ZA_DATA_DIR` | `backend/var` | DB, encrypted objects, WORM checkpoints, packages, dev keys |
| `ZA_CORS` | `http://localhost:3100,http://127.0.0.1:3100` | Allowed browser origins |
| `ZA_TSA_URL` | *(unset)* | RFC 3161 TSA. If unset, checkpoints are honestly reported as `TSA_PENDING` |

Encrypted evidence payloads, packages and dev keys live in `backend/var/`. Keep them together with the database: payloads can't be decrypted without the matching keys.

## Sign-in

Open http://localhost:3100. You're sent to **/login**. Every seeded person has a demo account
(`<actor-id>@zoiko.example`, e.g. `actor-cco@zoiko.example`) with the shared demo password **`Assure@2026!`**.
In demo mode the sign-in page lists them so you can click to fill.

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
| `ZA_DEMO_MODE` | `1` | Creates demo credentials and lists demo accounts on the sign-in page. **Set to `0` outside demos** |
| `ZA_DEMO_PASSWORD` | `Assure@2026!` | Shared demo password |
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
