# Zoiko Assure: architecture guide

> **Non-normative.** This is an onboarding guide, not a specification. The controlled ZA-* documents are authoritative;
> where this guide differs from them, the ZA-* documents win. Current work is tracked in
> [frontend/frontend-build-plan.md](frontend/frontend-build-plan.md) and [backend/backend-build-plan.md](backend/backend-build-plan.md).

This guide is for engineers joining the project. Read it before your first change. It explains what the
system does, how the code is organised, how a request travels through it, and which rules you must not break.
Setup commands are in [README.md](README.md). The detailed behaviour is specified in the ZA-* engineering
documents (see [§15](#15-spec-documents)).

---

## 1. What Assure is (and isn't)

Zoiko Assure is **regulatory obligation and proof infrastructure** for groups with many legal entities in many
jurisdictions. For each entity it answers three questions, always for a named requirement version and a named period:

1. **Does this law apply to us?** Applicability: deterministic rules over versioned facts, then a human adopts the result.
2. **What exactly must we do?** Obligations, defined by satisfaction expressions over shared controls.
3. **Can we prove we did it?** Assurance: evidence, tests and source-data integrity (IPE), rolled up per period.

It **never** says "compliant". The controlled dictionary (ZA-GLS-001) has no such state. The outputs are
evidential states such as `PROVEN` or `NOT_PROVEN` for a requirement version and a period, and every one of them
can be traced back to its rule, facts, evidence and record cutoff.

---

## 2. System context

```
                     ┌──────────────────────────────── Zoiko Assure ────────────────────────────────┐
  Regulators /       │                                                                              │
  official sources ──┼─► Regulatory content ─► Rule authoring ─► Signed content release              │
  (EU OJ, FCA, …)    │   (ingest, extract,     (typed AST, golden   (Ed25519, immutable)            │
                     │    human verification)   vectors, review)          │                         │
                     │                                                     ▼                         │
  Customer users ────┼─► Next.js UI ──HTTPS──► FastAPI ──► Applicability ─► Obligations ─► Assurance │
  (CCO, GC, control  │   (port 5173)           (port 8000)  (facts + rules)  (adopted)     (periods)  │
  owners, auditors)  │                              │                                        ▲       │
                     │                              ▼                                        │       │
  Customer systems ──┼─► Connectors / source runs ─► IPE checks ─► Evidence vault + ledger ──┘       │
  (Entra ID, Vanta,  │                                              (encrypted, hash-chained)        │
   AWS, ServiceNow)  │                                                     │                         │
                     │   AI/agent runtime ◄── deterministic policy decision point (PDP)             │
                     └─────────────────────────────────────────────────────┼─────────────────────────┘
                                                                           ▼
                                                      Signed audit package (.zip) ──► external auditor
                                                      verifiable OFFLINE with verifier/verify_package.py
```

---

## 3. Technology stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS v4, lucide-react icons | All pages are client components that call the API. Charts are hand-written SVG |
| Backend | Python 3.12+ (dev uses 3.14), FastAPI, SQLAlchemy 2.0, Pydantic 2 | A **modular monolith**: one deployable, with strict internal module boundaries |
| Database | PostgreSQL 17 on `127.0.0.1:5433` (local Windows service or Docker), with **row-level security** | The only runtime database: no SQLite fallback. Tests use a throwaway SQLite only when they opt in (`ZA_ALLOW_SQLITE`) |
| Crypto | `cryptography`: AES-256-GCM envelope encryption, HKDF, HMAC-SHA256, Ed25519; stdlib scrypt | Dev keys live in `backend/var/keys/` (stand-in for a cloud KMS/HSM) |
| Tests | pytest + FastAPI TestClient | 149 tests on PostgreSQL (incl. RLS, runtime-role, migration, immutability, snapshot-determinism and rule-integrity proofs); on SQLite 115 run and the 34 PostgreSQL-only tests skip |

Ports: UI **5173**, API **8000**, Postgres **5433**.

---

## 4. Repository map

```
zoiko-Assure/
├── README.md                    how to run, accounts, configuration
├── ARCHITECTURE.md              this file
├── backend/
│   ├── .env                     DB connection strings (git-ignored; template: .env.example)
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py              FastAPI app, start-up (read-only schema/role checks; demo seed only in demo mode), routers, CORS
│   │   ├── db.py                engines (runtime + admin), sessions, .env loading, RLS scope stamping
│   │   ├── migrate.py           `python -m app.migrate`: versioned migrations, state detection, baseline adoption, checks
│   │   ├── pg_bootstrap.py      runtime role: provisioned by the migration tool, verified at start-up
│   │   ├── models.py            ALL tables (63), grouped by bounded context (see §7)
│   │   ├── codes.py             controlled dictionary ZA-GLS-001: states, reason codes, engine versions
│   │   ├── platform.py          record sequence/watermark, canonical JSON + hashing, outbox emit, audit
│   │   ├── security.py          roles → permissions, Ctx (request context), get_ctx, system_scope
│   │   ├── auth.py              sign-in / sign-out / sessions / password change / demo accounts
│   │   ├── seed.py              "Customer Zero" demo fixture (demo mode only, empty database)
│   │   ├── engines/             PURE DOMAIN LOGIC (no HTTP)
│   │   │   ├── rules.py         rule AST validation, decision-table compilation, 3-valued evaluator, golden vectors
│   │   │   ├── facts.py         bitemporal fact store: assert / correct / resolve at (valid time, record seq)
│   │   │   ├── applicability.py evaluation coordinator, determinations, attestation, re-evaluation, shadow impact
│   │   │   ├── assurance.py     control evaluation, satisfaction expressions, period segmentation, snapshots
│   │   │   ├── evidence.py      capture/encrypt, hash-chained ledger, Merkle checkpoints, holds, erasure, packages
│   │   │   ├── ipe.py           source runs, population manifests, completeness/accuracy checks
│   │   │   └── keys.py          dev KMS: tenant key derivation, encrypt/decrypt, Ed25519 signing
│   │   └── routers/             THIN HTTP LAYER: validate input, check permission, call engine, commit
│   │       ├── core.py          /health /dictionary /me /actors /watermark /outbox /audit /org/*
│   │       ├── catalog.py       /catalog/* regulatory sources, requirements, definitions, releases
│   │       ├── rules.py         /rules/* drafts → validate → test → review → shadow → publish, diff
│   │       ├── applicability.py /subjects/*/facts, /applicability/*, /fact-requests, /history/compare
│   │       ├── assurance.py     /obligations, /snapshots, /controls, /test-runs, /exceptions, /issues …
│   │       ├── evidence.py      /evidence, /ledger, /packages, /trust/public-key
│   │       ├── integration.py   /connectors, /ipe/*
│   │       ├── ai.py            /ai/assets, /ai/grants, /ai/decisions (PDP), /ai/acs/evaluate
│   │       └── dashboard.py     /dashboard (aggregates + chart series)
│   ├── alembic.ini              Alembic configuration (use `python -m app.migrate`, see docs/database-migrations.md)
│   ├── migrations/              env.py, versions/0001 baseline tables, versions/0002 RLS + grants + append-only triggers,
│   │                            versions/0003 immutable completed results (privileges + guard triggers),
│   │                            versions/0004 immutable published catalogue,
│   │                            baseline_schema.json (validates databases created before migrations existed)
│   ├── verifier/verify_package.py  standalone offline audit-package verifier (no Assure needed)
│   ├── tests/                   test_engines.py, test_api.py, test_rls.py, test_migrations.py, test_immutability.py,
│   │                            test_snapshot_determinism.py, test_rule_integrity.py (+ conftest.py)
│   └── var/                     runtime data: encrypted evidence objects, packages, WORM checkpoints, keys
└── frontend/
    ├── app/                     one folder per route (App Router); login/ is the only page without the shell
    ├── components/              Shell (sidebar/topbar), ui (design primitives), charts, Trace, modals
    ├── lib/                     api.ts (fetch + auth), actor.tsx (auth context), format.ts, motion.ts
    └── public/                  logo.png (full logo), logo-mark.jpg (app icon for the collapsed sidebar)
```

**Golden rule of the backend:** routers stay thin; domain rules live in `engines/`. If you're writing an `if`
about regulatory or assurance semantics inside a router, move it into an engine.

---

## 5. Life of a request

Example: the CCO opens an obligation dossier.

```
Browser (Next.js page)                FastAPI                                   PostgreSQL
──────────────────────               ───────                                   ──────────
useApi("/obligations/{id}")
  └─ api(): fetch with
     Authorization: Bearer <token> ─► get_ctx()  (security.py)
                                       1. auth.resolve_token(): SHA-256 of the
                                          token, expiry, revocation ────────►  auth_session lookup by token_hash
                                       2. set_tenant_scope(tenant) ─────────►  set_config('app.tenant_id', …)
                                       3. return Ctx(db, actor)                 (stamped on EVERY transaction
                                     router: assurance.dossier()                 by db.py after_begin hook)
                                       ctx.require("…") permission check
                                       ctx.scoped(obj)  → 404 if other tenant
                                       engines.assurance.* (pure logic) ────►  queries; RLS filters rows
                                       state changes? → platform.emit()        to app.tenant_id only
                                                        platform.audit()
                                       db.commit() ───────────────────────►  one atomic transaction
  ◄──────────────────────── JSON
page renders with ui.tsx
primitives (Badge, Card, …)
```

Key points:
- **`Ctx`** (`security.py`) is the request context. Use `ctx.tenant`, `ctx.require(perm)`, `ctx.require_human(what)`
  and `ctx.scoped(obj)`, and never read a tenant ID from the request body.
- **Every state change** in a handler emits a domain event (`platform.emit`, the transactional outbox) and, for
  governance actions, an audit row (`platform.audit`), **in the same transaction**.
- **Any 401** in the UI fires `za-unauthorized`, which clears the session and redirects to `/login`.

---

## 6. Security model

### 6.1 Authentication (`app/auth.py`)
- Email + password. Hashing is **scrypt** with a per-user salt and constant-time comparison.
- On success, a new `AuthSession` row is created and an opaque random token (32 bytes) is returned once, valid 8 hours.
  Only its SHA-256 is stored (`auth_session.token_hash`, migration 0006); no key on disk can re-create it. Logout,
  password change and organisation suspension set `revoked_at`, and a request's writes are refused at commit if its
  session was revoked meanwhile (`security._revalidate_session`).
- **Lockout:** 5 failures locks the account for 15 minutes. Unknown, locked, non-interactive and wrong-password sign-ins
  all get the same answer; the reason goes to the audit trail only (`auth.login_failed`). Refused authorisation
  decisions are recorded as `authorization.denied`.
- Only `HUMAN` and `EXTERNAL_AUDITOR` actors can sign in. **Agents and services can never log in.**
- Demo mode is **off by default**. `ZA_DEMO_MODE=1` seeds the Customer Zero fixture into an empty database and, only if a
  local `ZA_DEMO_PASSWORD` is set, gives seeded people that password. No password is built in or shown on the login page.
- `ZA_DEV_IDENTITY_HEADER=1` re-enables the `X-Actor-Id` impersonation header. It's **off by default** and used only by tests.

### 6.2 Authorisation (`app/security.py`)
`ROLE_PERMISSIONS` maps roles to permissions (e.g. `CCO → attest, approve_exception, package …`). Handlers check
permissions, not role names. Separation of duties is enforced in handlers. For example:
- A rule draft's author ≠ peer reviewer ≠ legal reviewer ≠ release manager.
- An exception's requester can't approve it.

### 6.3 Tenant isolation: two layers
1. **Application layer:** every tenant query goes through `ctx.tenant` / `ctx.scoped()`.
2. **Database layer (PostgreSQL RLS):**
   - The API connects as **`assure_app`**, which is non-owner and `NOBYPASSRLS`.
   - `db.py` stamps every transaction with `app.tenant_id`.
   - Migration 0002 puts a `tenant_isolation` policy on 41 tables, so even a buggy query can't read or write another tenant's rows.
   - The **owner role** (`DATABASE_ADMIN_URL`) applies migrations (`python -m app.migrate`: schema, policies, runtime role). The API uses it only at start-up, read-only for the schema-version and role checks, and for the one-time account bootstrap and the demo seed. It never changes the schema.
   - `tests/test_rls.py` proves this directly at the database level.
3. **`system_scope`:** the *only* way to act across tenants, and it is limited to three documented platform jobs:
   rule-release impact re-evaluation, shadow impact (aggregate output only) and cell ledger checkpoints.
   Don't add more without a design review.

Content-operations staff (`tenant_id = NULL`) have **no** access to customer data (INV-14).

---

## 7. Data model (63 tables, by bounded context)

Table names are prefixed by context. All IDs are UUIDv7 strings.

| Prefix | Tables | Holds |
|---|---|---|
| `platform_` | 4 | record sequence (watermark), transactional outbox, idempotency keys, audit log (append-only) |
| `auth_` | 2 | credentials (scrypt hashes, lockout), sessions |
| `org_` | 5 | cells, tenants, legal entities, subject registry (entities, business units, products, AI assets…), actors |
| `catalog_` | 12 | **global regulatory content**: sources, instruments, requirement versions, provisions, obligation/fact definitions, controlled sets, rule drafts, rule versions, control library, framework mappings, content releases |
| `applicability_` | 6 | fact assertion revisions (bitemporal), fact conflicts, evaluations, determination revisions, challenges, fact requests |
| `governance_` | 2 | adoption attestations, risk acceptances |
| `assurance_` | 12 | obligation instances, satisfaction expressions, occurrences, control implementations, test policies/definitions/runs, control evaluations, snapshots, exceptions, compensations, issues |
| `evidence_` | 13 | artifacts, ledger events (append-only), tenant ledger heads, checkpoints, bindings, conflicts, legal holds, packages, source runs, population manifests, IPE evaluations/anomalies, IPE policies |
| `integration_` | 4 | connector definitions (certification registry), installations, extraction & mapping contracts |
| `ai_` | 3 | AI asset versions (AgBOM), authority grants, policy decision receipts |

`catalog_*` is **global and tenant-free**: Zoiko's legal content, shared by every customer. Everything that
describes a customer carries `tenant_id` and is RLS-protected.

### Patterns you'll see everywhere
- **Revisions, not updates.** Most governed records are append-only revisions (`record_from_seq` / `record_to_seq`,
  where `record_to_seq IS NULL` means current). History is never overwritten. A correction closes the old revision and adds a new one.
- **Two time axes (ZA-TIM-001).** *Valid time* (`valid_from/valid_to`) is when something was true in the world.
  *Record time* (`record_seq`) is when Assure learned it. `platform.next_record_seq()` gives a cell-wide monotonic
  sequence, and every reconstruction is pinned to a record cutoff.
- **Canonical hashing.** `platform.canonical_json()` + SHA-256 gives stable digests for rule ASTs, input bundles,
  snapshots, manifests and packages.

---

## 8. The core pipeline (follow one obligation end to end)

```
 ① REGULATORY CONTENT            ② RULE                     ③ APPLICABILITY                 ④ OBLIGATION
 catalog.ingest_source ──►  rules draft (AST or     ──►  evaluate(subject, rule,    ──►  human ATTESTS the
 fingerprint + rights       decision table)              facts @ valid time,            PROPOSED determination
 extract (AI proposal,      validate · golden vectors    record cutoff)                 (INV-01: humans only;
 non-binding, INV-09)       peer → legal review          → APPLIES / DOES_NOT_APPLY     UNCERTAIN can't be adopted)
 human legal VERIFY         shadow impact (aggregate)      / UNCERTAIN + full trace     → ObligationInstance ADOPTED
                            signed ContentRelease        UNCERTAIN → FactRequests
                                                                                              │
 ⑦ AUDIT PACKAGE             ⑥ PERIOD ASSURANCE           ⑤ CONTROLS & EVIDENCE              ▼
 signed zip: snapshots,  ◄── snapshot(obligation,    ◄── satisfaction expression over shared controls
 evaluations, evidence,      period, cutoff):            (ALL / ANY / K_OF_N / COMPENSATION / OCCURRENCES)
 IPE statements, ledger      split into segments,        control state from tests + evidence:
 proofs, manifest            each PROVEN / NOT_PROVEN /    evidence admissible? (stale/invalid/erased/conflict)
 → verify offline            FAILING / REQUIRES_REVIEW     source data IPE-verified? (completeness + accuracy)
                             exceptions shown ALONGSIDE
```

### Engine cheat-sheet

| Engine | Core idea | Results |
|---|---|---|
| `rules.py` | Typed AST, `OperatorSetVersion 1.0`. **Three-valued logic** (TRUE/FALSE/UNKNOWN). Short-circuited branches are `NOT_EVALUATED`, never FALSE. Decision tables: `ANY_MATCH` / `SINGLE_MATCH_VALIDATED` only (`FIRST_MATCH` is prohibited). Golden vectors must cover POSITIVE, NEGATIVE and UNKNOWN | trace tree with source mappings |
| `facts.py` | Bitemporal fact store. `resolve_fact()` answers "what was the value at valid time T, as known at record seq S", applying provenance (P1 system … P5 AI-inferred) and expiry | value or UNKNOWN + reason |
| `applicability.py` | Freezes an **input bundle**, dedupes by `evaluation_key`, creates PROPOSED determinations, raises FactRequests, re-evaluates when a fact changes, and replays deterministically | `APPLIES`, `DOES_NOT_APPLY`, `UNCERTAIN` |
| `assurance.py` | Control precedence **FAILING > REQUIRES_REVIEW > NOT_EVALUATED > DEGRADED > OPERATING**. Period precedence **FAILING > REQUIRES_REVIEW > NOT_PROVEN > PROVEN**. Periods split into segments wherever inputs change; equal neighbours coalesce. Exceptions are a separate treatment and never turn a gap green. Inputs are read in record order (`record_seq`, then id) and within the evaluated tenant, so output does not depend on physical row order or on RLS (roadmap Stage 3a; test definitions excepted, see the roadmap's gap matrix) | snapshot with segments, trace, dependency digest |
| `evidence.py` | Payloads are AES-256-GCM encrypted with a per-object key wrapped by a tenant key. Per-tenant **hash chain** (`ZOIKO-ASSURE-EVIDENCE-LEDGER-v1`). Merkle checkpoints are signed (RFC 3161 timestamping shows `TSA_PENDING` unless configured). Erasure = destroy the data key (crypto-shred); legal holds block it | lifecycle + condition per artifact |
| `ipe.py` | For each source run: scope/auth, consistency boundary, enumeration, source-total reconciliation, duplicates, rejects, zero-rows, schema drift, mapping, transform re-performance, source checksum | `IPE_VERIFIED`, `IPE_FAILED`, `IPE_INCONCLUSIVE`, `IPE_NOT_REQUIRED` |
| `keys.py` | Dev KMS: master key → HKDF per-tenant keys. Ed25519 signer for releases, checkpoints and packages | — |

---

## 9. Frontend architecture

```
app/layout.tsx
 └─ ActorProvider (lib/actor.tsx)   auth state: me, login(), logout(); redirects to /login when signed out
     └─ Shell (components/Shell.tsx) collapsible sidebar (NAV list), topbar (breadcrumb, jump-to search,
         │                           watermark, notifications, user menu, change password)
         └─ page (app/<route>/page.tsx)
              ├─ useApi("/path")     lib/api.ts: fetch + Bearer token; reloads when the user changes
              ├─ api(path, {method, json})   for mutations, wrapped in useAction() for busy/error state
              └─ ui.tsx primitives   Badge (state → tone), Card, Table, KV, Modal, Tabs, Hash, Json…
```

- **Adding a page:** create `app/<route>/page.tsx` (`"use client"`), then add one entry to `NAV` in `Shell.tsx`.
  A page file can only export its default component. Put shared components in `components/`.
- **States must never rely on colour alone.** `Badge` always shows an icon and the code text. `toneOf()` maps
  dictionary codes to tones, so add new codes there.
- **Brand theme:** navy `#154A6C`, orange `#D9822B`, slate `#5F6B70`, defined as Tailwind tokens in `app/globals.css`
  (`navy-*`, `brand-*`). Component classes (`card`, `btn-primary`, `input`, `label`, `th`, `td`) are `@utility`
  definitions in the same file.
- **Motion:** the `za-*` animation classes in `globals.css`, plus `useCountUp` / `delay()` in `lib/motion.ts`.
  All of it is disabled under `prefers-reduced-motion`.
- **Traces:** `components/Trace.tsx` renders rule traces (`RuleTrace`), satisfaction-expression traces (`ExprTrace`)
  and the period `SegmentRibbon`.

---

## 10. Invariants: never break these

| ID | Rule | Enforced in |
|---|---|---|
| **INV-01** | Only a named **human** can be the accountable authoriser (adopt, approve, grant, release, hold, erase). Agents and services never can, even if they hold the role | `ctx.require_human()`, `auth.INTERACTIVE_TYPES` |
| **INV-02** | No model or LLM call sits on a decision path. Evaluation, roll-up and the PDP are deterministic | engines, `routers/ai.py` |
| **INV-08** | One control implementation per control and scope. Frameworks are *mappings*, not copies | unique constraint + `create_control` |
| **INV-09** | AI-extracted content is a non-binding proposal until a qualified human verifies it | `catalog.extract` / `verify_requirement` |
| **INV-11** | Customer-visible connector status comes only from the certification registry | `routers/integration.py` |
| **INV-14** | Content-operations staff have no standing access to customer data | `Ctx.tenant`, RLS |
| DOM-INV-03 | An `UNCERTAIN` determination can't be adopted | `applicability.attest` |
| — | No "compliant" state anywhere. Exceptions and compensations never rewrite assurance state. Closing an issue doesn't prove remediation | `codes.py`, `assurance.py` |
| — | Evidence bytes are never replaced: supersede creates a new ID. The ledger, the audit log and completed results (evaluations, snapshots, IPE evaluations, packages, attestations, checkpoints) are append-only: no `UPDATE`/`DELETE` for the runtime role, and DB triggers that refuse those statements from every role (the table owner can still disable a trigger with DDL; audit A10). So is the published catalogue (rule versions, content releases, set versions, released fact definitions; DAT-REQ-012) | `evidence.py`, migrations 0002, 0003 and 0004 |
| — | Published rule versions and content releases are immutable. Changes go through a new draft and a new release | `routers/rules.py` |

---

## 11. Cross-cutting conventions

- **Dictionary first:** every state and reason code must exist in `codes.py` (ZA-GLS-001). Don't invent strings in handlers.
- **Versions on outputs:** evaluations record `evaluator_build`, snapshots `rollup_engine_version`, IPE `engine_version`.
  Bump the constant in `codes.py` whenever you change semantics.
- **Errors:** raise `HTTPException` with a message that tells the user *why* (e.g. "Separation of duties: author cannot peer-approve").
- **Time:** store UTC (`TIMESTAMP WITH TIME ZONE`) and use `platform.ensure_utc()`. Periods are half-open `[from, to)`.
- **Never trust the client** for tenant, actor or timestamps of record. The server assigns them.

---

## 12. Running, testing, debugging

```powershell
& "C:\Program Files\PostgreSQL\17\bin\pg_isready.exe" -h 127.0.0.1 -p 5433       # database (port 5433) must be up first
Set-Location backend;  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
Set-Location frontend; npm run dev                                              # second terminal: http://localhost:5173
```

The backend logs `Database: PostgreSQL ... at 127.0.0.1:5433/assure` at start-up and refuses to start without a reachable
PostgreSQL (see README §"Run locally").

| Task | Command |
|---|---|
| Unit + API tests (throwaway SQLite, never touches your DB) | `cd backend && .venv/Scripts/python -m pytest -q` |
| Same on PostgreSQL + RLS proofs | README §"Tests" (needs an empty `assure_test` DB) |
| Frontend type-check / build | `cd frontend && npx tsc --noEmit && npm run build` |
| SQL shell | `psql -h 127.0.0.1 -p 5433 -U assure -d assure` (prompts for the owner password) |
| Back up the database | README §"Importing the old SQLite demo database" (`pg_dump` command) |
| Import the old SQLite demo DB | `tools/migrate_sqlite_to_postgres.py` (dry run by default, `--apply` to commit) |
| API explorer | http://localhost:8000/docs |
| Event and audit trail | UI → Administration → Outbox events / Audit trail |

---

## 13. Recipe: adding a feature

1. **Spec:** find the requirement in the ZA-* documents and note its ID in a docstring.
2. **Dictionary:** add any new states or reason codes to `codes.py`.
3. **Model and migration:** add tables or columns in `models.py`, then a reviewed migration
   ([docs/database-migrations.md](docs/database-migrations.md)). New tenant tables need a `tenant_id`, and the migration
   must grant the runtime role and add their RLS policy; `python -m app.migrate check` fails without the policy.
4. **Engine:** put the logic in `engines/` as a pure function of a DB session plus inputs, and make it deterministic.
5. **Router:** keep it thin: `Ctx` → permission check → engine → `emit` / `audit` → commit.
6. **Tests:** add an engine test and an API test. If the change touches tenancy, extend `test_rls.py`.
7. **UI:** add a page or component and wire it into `NAV`. Show states with `Badge`, and add traces wherever a decision is made.

---

## 14. Known limitations (dev stand-ins)

| Area | Today | Production target |
|---|---|---|
| Identity | Local password sign-in. Token kept in browser localStorage | OIDC/SAML SSO + SCIM provisioning, httpOnly session cookie |
| Keys | File-based dev KMS in `backend/var/keys` | Cloud KMS/HSM, key rotation |
| Cells | One process = one cell (`eu-west-cell-a`) | Multiple regional cells, data residency |
| Events | Outbox written transactionally, not relayed | Outbox relay to a message broker |
| Timestamps | Checkpoints are `TSA_PENDING` unless `ZA_TSA_URL` is set | Qualified RFC 3161 TSA |
| Schema changes | Versioned Alembic migrations via `python -m app.migrate`; start-up only checks the revision | Same, run by the deploy pipeline after a pre-migration backup on a chosen hosting target |
| Connectors | Source-run payloads are posted to the API | Real connector workers against vendor APIs |

---

## 15. Spec documents

| Doc | Topic | Main code |
|---|---|---|
| ZA-ARCH-001 v3.0 | Overall architecture, cells, AI governance | everything; `routers/ai.py` |
| ZA-PRD-001 | Product requirements, screens | `frontend/app/*` |
| ZA-DOM-001 | Domain model and invariants | `models.py`, §10 |
| ZA-GLS-001 | Controlled dictionary (states, reason codes) | `codes.py` |
| ZA-DAT-001 | Data architecture, tenancy | `db.py`, `migrate.py`, `migrations/` |
| ZA-RUL-001 | Rule language, validation, release | `engines/rules.py`, `routers/rules.py` |
| ZA-APP-001 | Applicability evaluation | `engines/applicability.py`, `engines/facts.py` |
| ZA-TIM-001 | Bitemporal time model, reconstruction | `facts.py`, `/history/compare`, snapshot reproduce |
| ZA-STA-001 | Assurance states and period roll-up | `engines/assurance.py` |
| ZA-EVD-001 | Evidence vault, ledger, packages | `engines/evidence.py`, `verifier/` |
| ZA-IPE-001 | Information Produced by the Entity | `engines/ipe.py`, `routers/integration.py` |
| ZA-TRC-001 | Traceability | traces in evaluations/snapshots, `components/Trace.tsx` |

---

## 16. Glossary

| Term | Meaning |
|---|---|
| **Subject** | Anything an obligation can attach to: legal entity, business unit, regulated activity, product, environment, AI asset |
| **Fact / FactAssertion** | A typed, provenance-tagged statement about a subject, valid over a time range (e.g. `entity.established_in_eu = true`) |
| **Evaluation** | One deterministic run of a rule version over a frozen fact bundle → APPLIES / DOES_NOT_APPLY / UNCERTAIN |
| **Determination** | The governed result of evaluations: PROPOSED → (human attestation) → ADOPTED / REJECTED |
| **Obligation instance** | An adopted duty for a subject from a valid-from date |
| **Satisfaction expression** | A versioned formula over controls that defines "how this obligation is met" |
| **Control implementation** | The customer's way of meeting a library control for a scope, shared across obligations and frameworks |
| **Snapshot** | Frozen period assessment of an obligation, pinned to a record cutoff and reproducible |
| **Segment** | A sub-interval of a period with uniform inputs and one assurance state |
| **IPE** | *Information Produced by the Entity*: proof that a population pulled from a source system is complete and accurate |
| **Record cutoff / watermark** | The record sequence a view is "as known at". The header shows the current one |
| **Exception** | A time-boxed, approved governance treatment shown *alongside* a gap, never instead of it |
| **Compensation** | An approved alternative control branch, added as a new expression version |
| **Content release** | A signed, immutable bundle of rule versions published by Zoiko content operations |
| **System scope** | The narrowly scoped cross-tenant mode for the three documented platform jobs |
