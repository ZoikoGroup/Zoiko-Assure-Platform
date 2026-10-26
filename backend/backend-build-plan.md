# Zoiko Assure — Backend Build Plan

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL 17.11 with row-level security
**Scope:** the authoritative engine and API for the obligation-to-proof chain:
- catalogue and rules
- facts and applicability
- adoption
- obligations and controls
- evidence vault and IPE
- period assurance and history
- verifiable audit packages
- the identity, tenancy, events and workflows Release-1 needs

**Runs on:** `http://localhost:8000`. PostgreSQL 17 on `127.0.0.1:5433` (the only runtime database): `assure` for development, `assure_test` for tests only.
**Database roles:** `assure` is the **non-superuser** owner (CREATEROLE). `assure_app` is the least-privilege runtime role. The owner is never made a superuser.

> Status: **substantially built.** The first implementation was audited, and its core engines are correct and kept. The PostgreSQL bootstrap is now repeatable (P0-8A, in the working tree, pending review). This plan fixes the trust boundaries and completes the chain. It does not rewrite engines.
> Frontend counterpart: [`../frontend/frontend-build-plan.md`](../frontend/frontend-build-plan.md).

---

## Existing implementation summary

```
KEEP — correct engines, only small edge fixes scheduled
  app/codes.py                    Controlled dictionary            (rename RELEASED → PUBLISHED)
  app/engines/rules.py            Typed rule language + evaluator
  app/engines/facts.py            Bitemporal facts                 (server-derived provenance)
  app/engines/applicability.py    Applicability core               (evaluation key, impact jobs)
  app/engines/assurance.py        Roll-up maths                    (read records as of cutoff)
  app/engines/evidence.py         Evidence ledger + Merkle proofs  (package selection)
  verifier/                       Offline package verifier
  app/engines/ipe.py              IPE derivation                   (trusted ingress)
  app/routers/rules.py            Rule publication chain
  app/routers/ai.py               Deterministic AI governance      (grant revisions)
  app/pg_bootstrap.py             Repeatable bootstrap             DONE (P0-8A)

MODIFY — authorization, provenance, scope, revisions
  app/routers/{assurance,evidence,integration,applicability,core}.py
  db/postgres_rls.sql             FORCE RLS, no self-bypass, protected immutable tables

REPLACE — architecture is not compatible with the specs
  app/auth.py browser token       → cookie session, then OIDC
  direct multipart evidence upload → upload intent + quarantine + finalize
```

---

## Week 1 — Security, PostgreSQL & platform foundation

**Goal:** make the existing backend safe and stable before adding functionality.

- [x] Repeatable PostgreSQL bootstrap with the non-superuser owner. It fails closed if the runtime role is unsafe (P0-8A).
- [ ] Add a permission check, an audit record and an event to every unchecked governed write:
  - evidence conflicts and bindings
  - exceptions, compensations and issues
  - occurrences
  - control evaluation
  - IPE re-evaluation
  - anomaly dispositions
  - agent decision (PDP) calls
- [ ] Server-derived fact provenance: remove `provenance_class` from requests.
- [ ] Server-resolved scope: validate every `entity_id` and stop accepting a client-chosen `home_cell_id`.
- [ ] Connector certification can be changed only by a Zoiko role, with append-only history.
- [ ] Containment: manual or external test results plus manual uploads cannot make a control `OPERATING` until they are reviewed.
- [ ] Disable client-posted IPE source runs as proof. Allow them in dev only, capped at `IPE_INCONCLUSIVE`.
- [ ] Demo mode off by default. Seeding runs only as an explicit dev command. No endpoint returns a password.
- [ ] Migration framework: a baseline migration and no `create_all` at start-up.
- [ ] RLS migration:
  - `FORCE` RLS on every tenant table
  - replace the self-settable `app.system` bypass
  - protect `org_actor` and the `auth_*` tables
  - make the catalogue read-only for the runtime role
  - revoke UPDATE/DELETE on immutable result tables and add append-only triggers
- [ ] Cookie session (HttpOnly, SameSite, short-lived with refresh, CSRF) plus a current-user/permissions endpoint.
- [ ] Environment hygiene:
  - CORS limited to exact origins
  - the dev identity header is refused outside dev/test
  - rotate the local `assure_app` password
- [ ] Tests:
  - an authorization matrix over every write route
  - PostgreSQL RLS and bootstrap tests that **fail instead of skipping**, using a disposable test database per session

**Exit criteria:**
- 0 write routes without authorization.
- FORCE RLS on 100% of tenant tables.
- No self-bypass remains.
- 0 runtime UPDATE/DELETE grants on immutable tables.
- PostgreSQL tests run with 0 skips.
- A fresh start seeds nothing.

---

## Week 2 — Sources, requirements & applicability

**Goal:** the first half of the chain: source → requirement → facts → deterministic applicability → human adoption. AI may propose content but never decides applicability.

- [ ] Sources & versions API: source fingerprint and supersession chain. Provision edits are stored as revisions.
- [ ] Requirement verification:
  - the author cannot verify their own work
  - content stays `AI_PROPOSED` until verified
  - a release requires the source fingerprint
  - rename `RELEASED` → `PUBLISHED`
- [ ] Rule lifecycle: keep it; restrict draft reads to content roles.
- [ ] Fact APIs: server-derived provenance, history with both valid time and recorded time, corrections stored as revisions.
- [ ] Missing-fact workflow: de-duplicated requests; resolving one triggers a deterministic re-evaluation.
- [ ] Applicability API:
  - the evaluation key includes the record cutoff
  - a trace endpoint
  - infrastructure errors are never returned as `UNCERTAIN`
- [ ] Challenges stored as governed records. Evaluations are never rewritten.
- [ ] Adoption / attestation:
  - backed by a delegated authority grant
  - human only, with separation of duties
  - tied to the exact evaluation revision
  - a step-up hook
  - `UNCERTAIN` cannot be adopted
- [ ] Change impact and correction notices. Publish-time re-evaluation moves to per-tenant background jobs.
- [ ] Entity isolation:
  - entity-scoped roles and entity-level RLS policies
  - authorization for applicability and adoption
  - shadow impact returns totals only
  - Zoiko content staff cannot read tenant data
- [ ] Tests: applicability golden vectors, bitemporal correction tests, attestation negative tests, J-01 and J-02.

**Governance:** legal-date ranges for facts and rules wait on the DOC-CON-003 decision.

**Exit criteria:**
- J-01 and J-02 work end to end.
- 100% of golden vectors pass.
- Adopted determinations replay identically.
- 0 adoptions without a grant-backed human attestation.

---

## Week 3 — Obligations, controls, evidence & IPE

**Goal:** connect adopted obligations to **admissible** proof. An uploaded file plus "PASS" never equals `PROVEN`.

- [ ] Obligation revisions. Every occurrence is kept: missed, late and fulfilled.
- [ ] Governed revision model (close-and-insert, no overlapping revisions) for exceptions, compensations, legal holds, evidence conflicts, authority grants and anomaly dispositions.
- [ ] The assurance engine reads those records as they were at the record cutoff. Week 4 history depends on this.
- [ ] Control mappings as views over canonical controls. Satisfaction expressions are versioned.
- [ ] Test-run origin plus independent review. Admissibility follows the control's automation requirement.
- [ ] External GRC results are mapped as test inputs with provenance. They never set state.
- [ ] Evidence upload: intent → quarantine → scan → finalize. Evidence is inadmissible until finalized.
- [ ] Trusted source runs:
  - a connector worker with its own identity
  - pinned contract versions
  - the actual scope recorded
  - the only path that can create system-direct evidence
  - connector certification versioned
- [ ] Evidence bindings and conflicts as a governed workflow. Exceptions and compensations follow separation of duties and never change state.
- [ ] Obligation dossier read-model API covering the full chain.
- [ ] Tests:
  - admissibility vectors
  - IPE vectors (zero rows, pagination, count-only, schema drift)
  - tamper tests
  - J-03

**Governance:** `OCCURRENCES` semantics and the evidence provenance vocabulary need decisions before any new codes are added.

**Exit criteria:**
- J-03 passes.
- 0 paths where unreviewed manual evidence reaches `OPERATING`.
- 0 in-place updates on governed records, enforced by the database.
- Completeness and accuracy are stored separately.

---

## Week 4 — Assurance, temporal history & packages

**Goal:** reproducible period-based assurance (`PROVEN` / `NOT_PROVEN` / `FAILING` / `REQUIRES_REVIEW`) and packages that can be verified offline.

- [ ] Snapshots:
  - pin exact revisions and the record cutoff
  - no "latest" lookups
  - an exception never turns a failure into `PROVEN`
- [ ] Read APIs take a valid period, record cutoff and perspective, and echo the watermark. There is no single `as_of` parameter.
- [ ] Historical reconstruction and comparison APIs, reusing the existing compare and reproduce logic.
- [ ] Current assurance projection that references a snapshot ID and watermark. It is the dashboard's source.
- [ ] Read models: obligation assurance, evidence freshness, exposure, audit readiness.
- [ ] Packages:
  - exact snapshot by entity + period + perspective
  - entity validation
  - built as a long-running operation
  - unselected ledger events reduced to hashes
- [ ] Canonical JSON v1 with test vectors. Separate signing keys for the ledger, content releases and packages. The verifier trusts each key only for its purpose.
- [ ] Package integrity:
  - immutable after signing
  - auditor access engagement-scoped and time-limited
  - checkpoints every 15 minutes, plus hourly verification
- [ ] Tests:
  - assurance golden vectors
  - reproducibility
  - package pass, tamper, wrong key and mismatch cases
  - J-05

**Governance:** RFC 3161 timestamps wait on the TSA provider decision. Status stays `TSA_PENDING` until then.

**Exit criteria:**
- A sample package verifies offline.
- 100% of sampled snapshots reproduce.
- Every dashboard number traces to a snapshot ID and watermark.

---

## Week 5 — API, events, workflows & release verification

**Goal:** the complete Release-1 vertical slice, defensible with release evidence.

- [ ] `/v1` alignment and an OpenAPI 3.1 contract. Problem+json errors. Unknown fields rejected.
- [ ] `Idempotency-Key` on every write. `If-Match` / ETag on mutable resources.
- [ ] Events: catalogue names and a CloudEvents envelope. An outbox relay and consumer inbox on local transport.
- [ ] Workflows: human tasks (attestation, exception approval) stored in PostgreSQL. Durable operations for packages and impact jobs.
- [ ] Identity:
  - OIDC (Auth Code + PKCE), keyed by issuer + subject
  - workload credentials for connectors and agents
  - AI grants stored as revisions, with human-only revoke
- [ ] Tenancy: a time-versioned entity-to-cell assignment, resolved on the server.
- [ ] Privacy & audit:
  - a legal hold blocks erasure
  - erasure keeps the ledger
  - no secrets or raw evidence in events or logs
  - every privileged action fully audited
- [ ] Security regression:
  - cross-tenant and cross-entity negative tests on every path, export and job
  - a key-rotation drill
  - a dependency audit
- [ ] Release verification:
  - full PostgreSQL suite with 0 skips
  - performance budgets
  - traceability and release-readiness report

**Governance:** the managed queue, Temporal and the cloud cell are production decisions (ADR OPEN items). Local equivalents are used until they are made.

**Exit criteria:**
- J-01…J-06 pass on PostgreSQL.
- An external auditor verifies a sample package offline.
- 0 cross-tenant reads or writes.
- 0 skipped tests.
- 100% of golden vectors pass.

This is the Release-1 vertical slice, not yet controlled production.

---

## Cross-cutting standards

| Concern | Standard |
|---------|----------|
| Terminology | Applicability: `APPLIES` / `DOES_NOT_APPLY` / `UNCERTAIN`. Assurance: `PROVEN` / `NOT_PROVEN` / `FAILING` / `REQUIRES_REVIEW`. Never a generic `COMPLIANT` state. |
| AI | Proposes only. No model calls on authorization, applicability or assurance paths. |
| History | Never overwrite. Corrections are new revisions. Snapshots are immutable. |
| Writes | Permission check + audit record + catalogue event, in the same transaction. |
| Tenancy | PostgreSQL RLS (FORCE) + entity scope. The server resolves tenant, entity and cell. |
| Roles | `assure` is the non-superuser owner; `assure_app` is least privilege; never superuser. |
| Evidence | Admissible only with provenance, integrity, completeness, accuracy, freshness and validity. |
| Tests | The PostgreSQL suite never skips. Golden vectors are never edited to match code. |
| Secrets | Nothing committed. The `postgres` superuser never appears in app config. |
| Spec authority | Controlled ZA-* specs win. Conflicts are raised, never resolved silently in code. |

## Suggested delivery order

```
Week 1 security/foundation → Week 2 applicability → Week 3 evidence/IPE → Week 4 assurance/packages → Week 5 API/events/release
Week 3's revision model must land before Week 4's history work.
The frontend builds each week's pages against that week's APIs.
```

**Decisions needed (do not resolve in code):**

| Decision | Needed by |
|---|---|
| Python vs Go authoritative runtime (ADR-001 C-01) | Before Week 2 |
| Legal-date temporal profile (DOC-CON-003) | Week 2 |
| Evidence provenance vocabulary; `OCCURRENCES` semantics | Week 3 |
| RFC 3161 timestamp provider | Week 4 |
| Managed queue, Temporal, first cloud cell | Week 5 (production only) |

Sizing: about 2 backend engineers plus part-time QA/security.
