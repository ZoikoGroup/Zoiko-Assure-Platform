# Zoiko Assure: backend current state and wireframe gap audit

> **Type:** read-only audit and plan. No source, schema, migration, configuration or database was changed to produce it.
> **Date:** 30 October 2026. **Branch:** `naveen-changes`, HEAD `af1d508` (26 October 2026), with a large uncommitted
> working tree (section 2).
> **Evidence rule:** a status is only "verified" where an automated test exercising it passed in this audit's own run
> (section 13). Code that exists but has no passing test is "implemented but not verified".

Labels used throughout:

| Label | Meaning in this report |
|---|---|
| **Implemented and verified** | Code exists and a test exercising the behaviour passed in this audit's run (application level unless stated) |
| **Implemented but not verified** | Code exists; no test in this run exercises it |
| **Partially implemented** | Some of the specified behaviour exists; named parts are missing |
| **Present but not functionally connected** | Backend exists but no screen or caller uses it, or the reverse |
| **Missing** | No implementation |
| **Blocked by dependency** | Cannot proceed without a named decision or other task |
| **Not specified in available source material** | The requested item has no source in the documents available to this audit |

---

## 1. Executive summary

**Counting method.** Section 7 lists 55 backend capabilities, one row per screen or module and its required backend
behaviour. They are taken from the PRD wireframes WF-01 to WF-08, the PRD §12 reporting views, the specification
wireframes (WF-DOM, -APP, -RUL, -STA, -TIM, -EVD, -IPE, -IAM, -TEN, -PRI) and the two dashboards named in the brief.
Each row has exactly one status. Rows marked "Not specified" are excluded from the denominator. The count is
unweighted: a large capability and a small one count the same, so the percentage is a coverage indicator, not an
effort estimate.

| Status | Rows | Share of the 53 specified rows |
|---|---|---|
| Implemented and verified | 14 | 26% |
| Implemented but not verified | 13 | 25% |
| Partially implemented | 17 | 32% |
| Present but not functionally connected | 1 | 2% |
| Missing | 7 | 13% |
| Blocked by dependency | 1 | 2% |
| Not specified in available source material | 2 | (excluded) |

**51% of the specified capabilities are implemented (27 of 53), and 26% are implemented and verified (14 of 53).**
A further 32% are partly implemented.

| Question | Answer |
|---|---|
| Fully operational core workflows | At application level, in a single-process development setup with seeded data: sign-in and sessions; rule authoring through signed publication with integrity checks; applicability evaluation with bitemporal facts, fact requests and attestation; control evaluation; evidence capture, integrity, audit packages and the offline verifier; the Super Admin organisation registry. None is production-ready (section 18). |
| Partially implemented workflows | Regulatory ingestion and extraction (a regex splitter, no ingestion); re-evaluation after a fact changes (one failing test); occurrences and governance history (state overwritten in place, I-04); assurance snapshots (reproduction unstable, I-03); checkpoints (on demand, no timestamping, local "WORM"); IPE (data posted to the API, no real connector); privileged access, retention, platform audit history. |
| Screens without backend support | Organisation Users & Roles (placeholder); Identity Integrations (blocked, G-05); the PRD §12 "Regulatory change impact" view; worker and queue consoles (WF-EVT-03, WF-WRK-07). One backend feature has no screen: evidence conflicts. |
| Missing APIs | Review assignment (WF-01); revoking external package access (WF-06); a regulatory-change-impact query (PRD §12); organisation user and role management (WF-IAM-01); identity-integration configuration; retention policy (WF-PRI-02); privileged access, JIT and break-glass (WF-IAM-03/04); creating obligation, fact, set and control definitions (seed-only today); idempotency keys (API-REQ-004/005); cursor pagination (API-REQ-012); a platform-wide audit query; outbox and worker monitoring. |
| Database and security gaps | The runtime role can switch on the RLS bypass (I-01, Critical). No table forces RLS (P-02). The owner role can disable the immutability triggers (A10). The API process holds the owner credential. The DAT §13 role model is not implemented. Any tenant can read and write rows with no tenant in the outbox and audit tables. Record time is missing on several tables (P-03). Governance state is updated in place (I-04). There is no SSO, SCIM or MFA (P-12). The browser token is kept in `localStorage` (I-06). Keys are local files (I-05, P-06). |
| What prevents end-to-end use | In development, the lifecycle runs end to end on seeded data. Real use is prevented by: no source ingestion or real connectors; no background workers; the gaps above; no deployment target (G-10); and one failing test in uncommitted work changed today. |
| What must come first | Task 0: stabilise today's uncommitted Task 3 changes, which include a failing test, and re-establish the PostgreSQL 17 baseline. Then the security decisions behind Tasks 1 and 2 (I-01, the role model, A10). |
| Remaining tasks | 12 (Task 0 to Task 11, section 15). |
| Independent work | After Task 0, these can proceed in parallel: Task 3 (regulatory lifecycle), Task 9 (Super Admin APIs), the API-hygiene parts of Task 10, and the non-production parts of Task 6. |
| Ready for frontend integration? | **Yes, for the screens that exist.** Every frontend API call maps to an existing backend route (section 7), with the noted placeholders. |
| Ready for staging? | **No.** There is no deployment target (G-10), and the security blockers are open. |
| Ready for production? | **No.** See section 18. |

---

## 2. Repository and working-tree baseline

Read-only commands: `git status --porcelain`, `git diff --stat HEAD`, `git log`, and `find -newermt` for modification
times.

| Item | Value |
|---|---|
| Branch / HEAD | `naveen-changes` / `af1d508` "Fix PostgreSQL CI bootstrap and environment file hygiene" (26 Oct 2026) |
| Staged | nothing |
| Modified (tracked) | 61 files, of which 28 under `backend/` (771 insertions, 630 deletions vs HEAD) |
| Deleted | `backend/db/postgres_rls.sql`, replaced by migration `0002` |
| Untracked | 14: `backend/alembic.ini`, `backend/app/migrate.py`, `backend/migrations/` (0001–0004 plus the baseline fingerprint), `backend/app/engines/connectors.py`, five test modules (`test_authorization`, `test_immutability`, `test_migrations`, `test_rule_integrity`, `test_snapshot_determinism`) and five docs (architecture audit, roadmap, two decision briefs, the migrations runbook) |

**The whole migration system, the immutability and rule-integrity work, and their tests exist only in the working
tree.** None of it is committed, and GitHub Actions has never run on it.

**Changes made today, 30 Oct 2026, 14:22–14:29, outside this audit:**

| File | What changed (from reading the code) | Assessment |
|---|---|---|
| `backend/app/routers/applicability.py` | `assert_fact_ep` (line 77) now runs `reevaluate_for_fact` inside `integrity_fail_closed` (line 93) | Consistent with Task 3. Correct shape. |
| `backend/app/routers/rules.py` | `publish` refuses a missing SetVersion with `409 SET_VERSION_MISSING` (lines 245–258), and verifies the new RuleVersion before impact re-evaluation (lines 308–310), so a tampered set is refused even when no subject was ever evaluated | Closes the publication-time set gap noted on 29 Oct |
| `backend/tests/test_rule_integrity.py` | Five tests added (lines 326–456): fact assertion fails closed; non-integrity errors are kept; publication refuses a tampered or missing set | **One fails** (section 13) |
| `.pytest_cache/` at the repository root | A test run from the root | Harmless; not tracked |

**Incomplete current change (Task 0):**
`test_fact_assertion_preserves_non_integrity_evaluation_errors` (`tests/test_rule_integrity.py:375`) fails. The test
moves the bound rule version's `valid_to` into the past and expects the fact-triggered re-evaluation to report an
error. The likely cause, from reading `engines/applicability.py:43 resolve_rule`, is this: once the newest version is
no longer effective, evaluation silently binds the previous version and succeeds, so no error appears. Not confirmed by
debugging; this audit is read-only. Either the test assumption or the code is wrong. If the code, a change to a
non-selected version's effective period changes which version binds, without any integrity check. That is a residual of
Task 3.4 ("no fallback").

The CI gate `--require test_rule_integrity=13` (`.github/workflows/ci.yml:337`) still passes on count, since 18 tests
exist. The failing test would fail the job.

---

## 3. Authoritative documents and wireframe inventory

### 3.1 Documents found

| Source | Location | Status |
|---|---|---|
| 22 controlled ZA-* specifications, v1.0 (ARCH-001 v3.0, DOM, PRD, GLS, ADR, APP, RUL, TIM, STA, TRC, DAT, TEN, IAM, SEC, EVD, IPE, PRI, API, EVT, WRK, QA, DOC) | `C:/Users/navee/OneDrive/Desktop/Zoiko Assure docs/` (5 PDF volumes plus source DOCX) | All marked "Proposed for lock". ZA-ADR-001 is unapproved, so no `ACCEPTED*` baseline ADR is in force. Controlled specifications take precedence over the Engineering Brief. |
| Engineering Brief | same folder | Subordinate to the specifications |
| `docs/backend-architecture-audit.md`, `docs/backend-architecture-roadmap.md`, two decision briefs, `docs/database-migrations.md`, `docs/ci-cd.md`, `ARCHITECTURE.md` | repository | Engineering records, not approvals. Their findings are reused here only where re-checked against the code. |

**Requested sources that do not exist in the available material:**
- a "Version 2.2 closure review";
- "Phase 1" and "Phase 2" Zoiko Assure architecture decisions;
- the roles `assure_owner` and `assure_job_owner`;
- a "migration 0005";
- any separate Zoiko Assure wireframe file.

`PHASE-2 CHANGES.txt` on the Desktop concerns the ZoikoMeds Patient Portal. The wireframe DOCX files found also belong
to ZoikoMeds. Section 11 therefore checks the named items against the code and the controlled specifications, and
marks the baseline itself as unavailable.

### 3.2 Wireframe inventory

The approved wireframes are sections inside the controlled specifications.

| Wireframe set | IDs | Backend relevance |
|---|---|---|
| PRD §13 "Engineering Interaction Wireframes" (core product screens, each with a minimum engineering contract of required fields and actions) | WF-01 Regulatory Change Inbox, WF-02 Applicability Review, WF-03 Obligation Dossier, WF-04 Evidence Record, WF-05 Assurance Period View, WF-06 Audit Package Builder, WF-07 AI/Agent Asset Dossier, WF-08 Administration/Trust | Primary |
| PRD §12 reporting views | Executive exposure, Regulatory change impact, Obligation dossier, Evidence freshness, Audit readiness, AI/agent obligation view, Historical reconstruction | Primary (dashboard content) |
| DOM §14 object views | WF-DOM-01 to WF-DOM-07 (source/provision, subject/entity, applicability trace, obligation, evidence, historical reconstruction, AI asset) | Primary |
| Engine wireframes | WF-APP-01/02/03, WF-RUL-01 to 04, WF-STA-01 to 05, WF-TIM-01 to 04, WF-EVD-01 to 06, WF-IPE-01 to 06 | Primary |
| Tenancy, identity, privacy | WF-TEN-01 to 06, WF-IAM-01 to 08, WF-PRI-01 to 08 | Administration |
| Operations consoles | WF-EVT-01 to 08, WF-WRK-01 to 08, WF-SEC-01 to 08, WF-API-01 to 07, WF-DAT-01 to 06 | Platform operations; mostly depend on workers, which don't exist |
| Governance tooling | WF-ADR, WF-DOC, WF-QA | Out of scope for product backend |

**Dashboards.** The brief defines two primary dashboards, Organisation and Super Admin. The specifications use none of
the names "Organisation Dashboard", "Super Admin Dashboard", "Needs Attention" or "Assurance Position". These are
treated as product direction and mapped to the nearest specified views:
- **Organisation Dashboard:** PRD §12 Executive exposure, Evidence freshness and Audit readiness, plus the WF-01 to
  WF-07 drill-downs.
- **Super Admin Dashboard:** WF-08 and WF-TEN.

Any behaviour that exists only under those names is marked "Not specified in available source material". One source
conflicts with the product direction: WF-IAM-01 says "No 'make super admin' button. Privileged capabilities route
through scoped grant/JIT workflow". The code creates the Super Admin from environment variables at start-up
(`backend/app/accounts.py:52 bootstrap_accounts`) and has no API that grants it, which is compatible. But the
JIT/scoped-grant workflow the specification requires does not exist (P-17).

The requirement inventory (screen or module to backend responsibility) is the table in section 7.

---

## 4. Current backend architecture

| Layer | Implementation | Evidence |
|---|---|---|
| Runtime | One FastAPI process (Python 3.12, SQLAlchemy 2, psycopg 3), started by `uvicorn app.main:app`. A modular monolith: no service split, no worker process. | `backend/app/main.py` |
| Start-up (`lifespan`) | Read-only checks: `check_database` (both roles reach the same database), `require_current` (schema at head), `verify_runtime_role` (least privilege). Then the demo seed when `ZA_DEMO_MODE=1`, then `bootstrap_accounts`. It refuses to start if a state-changing route has no guard. | `main.py:19`, `db.py:95`, `migrate.py:295`, `pg_bootstrap.py:96`, `accounts.py:52`, `main.py:62` |
| HTTP surface | 128 routes in 11 routers under `/api`. 66 change state: 62 behind `authorize(...)` and 4 documented public routes. 56 reads behind `get_ctx`. 6 reads need no sign-in. | `security.py:148 authorize`, `security.py:168 PUBLIC_MUTATIONS` |
| Domain engines | `engines/rules.py` (rule language, canonical hashing, pure evaluator), `applicability.py`, `facts.py` (bitemporal), `assurance.py` (control evaluation, satisfaction expressions, snapshots), `evidence.py` (vault, ledger, Merkle checkpoints, packages), `ipe.py`, `keys.py`, `connectors.py` | `backend/app/engines/` |
| Persistence | PostgreSQL 17 only at runtime; SQLite only for isolated tests. 63 tables; 42 have `tenant_id`, 41 of them under RLS. | `models.py`, `migrations/versions/0001`–`0004` |
| Database roles | `assure`: owner, `NOSUPERUSER CREATEROLE`, owns every table, used by migrations and API start-up. `assure_app`: runtime, used for every request. No other role. | `db.py:56`, `pg_bootstrap.py` |
| Tenant context | Per transaction: `set_config('app.tenant_id', …, true)` and `app.system`, stamped by an `after_begin` listener from `session.info` | `db.py:132-157` |
| Files outside the database | `backend/var/` (or `ZA_DATA_DIR`): encrypted evidence objects, `worm/` (read-only files), packages, keys (master key, signing key, session HMAC key) | `engines/evidence.py:27-30`, `engines/keys.py`, `auth.py:46` |
| Events | Transactional outbox rows written in the same transaction as the state change. **Nothing reads or relays them.** | `platform.py:76 emit` |
| Background processing | **None.** No worker, scheduler, queue, retry or lease. Impact re-evaluation runs synchronously inside the request. | grep of `backend/app` (section 12) |
| External integrations | **None.** No outbound HTTP client or external SDK is imported anywhere in `backend/app`. | section 12 |
| Frontend contract | Next.js calls `${NEXT_PUBLIC_API_URL}` with a bearer token from `localStorage`. All 86 distinct paths it calls exist in the backend. | `frontend/lib/api.ts:6,44`; section 7 |

---

## 5. Backend execution flow explained

Every request follows the same skeleton, and the workflows below only add their own steps:

1. The frontend calls `api(path)`. It sends `Authorization: Bearer <token>` read from `localStorage` (`frontend/lib/api.ts:27,44`).
2. FastAPI opens one SQLAlchemy session for the request (`db.py:158 get_db`), bound to the runtime role `assure_app`.
3. The route's dependency resolves the caller:
   - `get_ctx` (`security.py:125`) for reads;
   - `authorize(permission, human=, platform=)` (`security.py:148`) for writes. It runs `get_ctx` and then checks the permission **before the body is validated**.
4. `get_ctx` does four things:
   - verifies the token (`auth.py:109 resolve_token`: HMAC-SHA256 with a key file, then the server-side `auth_session` row must not be revoked or expired);
   - loads the actor;
   - calls `set_tenant_scope` (`db.py:146`);
   - refuses a suspended organisation (`auth.py:84 tenant_active`) or a forced password change (403).
5. Every transaction on that session starts with `set_config('app.tenant_id', <tenant>, true)` and `app.system` (`db.py:135 _stamp`, an `after_begin` listener). PostgreSQL RLS then limits rows to the caller's tenant.
6. The handler runs engine functions synchronously, then calls `ctx.db.commit()`. State, audit rows and outbox rows commit in **one** transaction.
7. The response is JSON from `to_dict` (`platform.py:98`). Errors are FastAPI `HTTPException`s, which give `{"detail": …}` with either a string (107 sites) or `{"error": CODE, …}` (15 sites).
8. **Nothing happens after the response.** No worker picks up the outbox, and dashboards re-read the tables on their next request.

**Failure behaviour common to all flows:**
- A raised exception rolls the whole request transaction back, because the session closes without committing.
- Authorisation failures return 401 or 403 before any write.
- Validation errors return 422.
- Domain conflicts return 409.
- Integrity failures return 500 `RUL_SIGNATURE_INVALID` after the security signal is committed (flows B and C).

### A. Authentication and organisation access

| Step | Code |
|---|---|
| User action | Sign-in page posts email and password |
| Endpoint | `POST /api/auth/login` (`auth.py:175 login`), public by design |
| Checks | Only `HUMAN` and `EXTERNAL_AUDITOR` actors may sign in (INV-01). scrypt verification (`auth.py:60`). Five failures lock the account for 15 minutes (`MAX_FAILURES`, `LOCK_MINUTES`). A suspended organisation is refused (`auth.py:84`). |
| Writes | `auth_session` row; `auth_credential.failed_attempts` / `locked_until` |
| Response | Token `<body>.<HMAC>` (`auth.py:102 issue_token`) plus the actor view. The frontend stores the token in `localStorage` (I-06). |
| Validation on later requests | `get_ctx` → `resolve_token` checks the HMAC, then the session row (revoked? expired after `ZA_SESSION_HOURS`, default 8? same actor?) |
| Membership and tenant | `org_actor.tenant_id` **is** the membership; one actor belongs to one tenant. `NULL` means Zoiko staff (content or platform). The tenant comes only from the actor, never from the client (API-REQ-008). |
| Roles and permissions | `org_actor.roles` (a JSON list) is mapped through a static role→permission dictionary (`security.py:28-56`). There are no scoped, time-bound or delegated role assignments. |
| Super Admin vs organisation | Super Admin is an actor with no tenant and role `SUPER_ADMIN`, created only by `bootstrap_accounts` from `ZA_BOOTSTRAP_*` settings. The platform router's guard is `platform.py:39 _platform_admin`. The frontend picks the dashboard from the persona (`frontend/app/page.tsx:33`). |
| Expiry and revocation | `POST /api/auth/logout` (`auth.py:211`) revokes the session row. `POST /api/auth/password` (`auth.py:241`) revokes all other sessions. |
| Failures | 401 (missing, invalid or expired token); 423 (locked); 403 (suspended, permission, password change required). The dev header `X-Actor-Id` works only when `ZA_DEV_IDENTITY_HEADER=1`, with no environment guard (I-08). |
| Verified by | `test_api.py`: `login_logout_and_token_access`, `agents_cannot_sign_in_and_lockout`, `change_password_revokes_other_sessions`. `test_authorization.py` (22 tests). |

### B. Regulatory source management

| Step | Code |
|---|---|
| User action | Regulatory Inbox (`frontend/app/regulatory/page.tsx`): list sources, create one, run extraction |
| Endpoints | `GET/POST /api/catalog/sources` (`routers/catalog.py:35,61`); `POST /api/catalog/sources/{id}/extract` (`:94`); `PUT /api/catalog/provisions/{id}` (`:142`); `POST /api/catalog/requirements/{id}/verify` (`:157`); `GET /api/catalog/requirements/{id}` (`:179`) |
| Authorisation | `content_author` / legal-review permissions, platform (Zoiko) staff only |
| Processing | **Ingestion:** the source's text arrives in the request body. Nothing fetches, fingerprints or monitors official feeds. **Extraction (`:94 extract`):** a regex splits the text into sentences, keeps those matching a `MODAL` keyword pattern, and creates a `RequirementVersion` plus up to 20 `Provision`s with lifecycle `AI_EXTRACTED`. Despite the label, no model is involved. **Verification (`:157`):** a human legal reviewer moves the requirement to `VERIFIED` (INV-09: AI cannot). |
| Tables | `catalog_source_record`, `catalog_requirement_version`, `catalog_provision`; outbox `regulatory.extraction_proposed` |
| Requirement → rule | Rule drafts (`routers/rules.py:56-235`) reference an existing `catalog_obligation_definition`. **No API creates obligation, fact, set or control definitions.** They come only from `seed.py`. |
| Rule publication | `POST /api/rules/drafts/{id}/publish` (`rules.py:235`). Gates: VERIFIED draft; release manager distinct from author, peer and legal reviewer; validation; golden vectors; shadow impact; existence of every referenced set. It then writes the `RuleVersion` and signed `ContentRelease` once, with final values; emits `content_release.published` and audit `content.publish`; verifies the new version (Task 3); and re-evaluates impacted subjects **synchronously** under `system_scope`. |
| Failure | Any gate returns 409 and nothing is written. An integrity failure rolls back everything, commits the security signal, and returns 500. |
| Verified by | Publication and integrity tests in `test_rule_integrity.py` and `test_immutability.py` (PostgreSQL-only, not run today); engine tests in `test_engines.py`. Source, extraction and verification endpoints: guard tests only. |

### C. Facts and applicability

| Step | Code |
|---|---|
| User action | Entity profile → assert a fact (`components/AssertModal.tsx`); Applicability Review → evaluate, attest, challenge |
| Fact assertion | `POST /api/subjects/{id}/facts` (`routers/applicability.py:77`, permission `manage_facts`). Refuses: unknown fact definition (404); wrong subject type (422); manual attestation not allowed (422); system-sourced facts (403, certified-connector path only). Then `engines/facts.py:86 assert_fact` writes bitemporal `applicability_fact_assertion_revision` rows; a correction closes the earlier record slice. |
| Re-evaluation on fact change | `engines/applicability.py:456 reevaluate_for_fact`, **synchronous, in the same transaction**, inside `integrity_fail_closed`. For each subject (and the entity's other subjects) and each obligation whose rule needs the fact and which was evaluated before, it calls `evaluate`. Other errors are collected per item; open fact requests are resolved. **Defect:** it picks the rule version to inspect with an unordered `.first()` (`:467`), so which version's required facts are checked is arbitrary. |
| Evaluation | `POST /api/applicability/evaluations` (`:128`) and `/evaluate-all` (`:142`) → `engines/applicability.py:164 evaluate`. Steps: `resolve_rule` (`:43`: newest PUBLISHED, effective, within the record cutoff); `verify_rule_integrity` (`:109`) **before** any fact is read; facts resolved with the record cutoff (`facts.py:175`); input bundle hashed; pure evaluator (`rules.py:502`); an existing evaluation returned for identical inputs (evaluation key); otherwise it persists `applicability_evaluation` plus outbox `applicability.evaluated`, and `_derive` (`:284`) writes proposed determinations, fact requests and review flags. |
| Determinism | Stored `input_bundle_hash` and `evaluator_build`. The replay on the evaluation detail page (`routers/applicability.py:190 _replay`) re-runs the frozen inputs. `GET /api/history/compare` (`:283`) evaluates two record cutoffs. |
| Decisions | `POST /api/applicability/determinations/{lineage}/attest` (`:227` → `engines/applicability.py:355 attest`). Rules: human only (INV-01); `UNCERTAIN` cannot be adopted; refused if a newer evaluation exists; writes a determination revision and activates the obligation instance (`:428`). Challenge (`:243`) creates a record and never rewrites the evaluation. |
| Response and dashboard | The evaluation view (outcome, trace, missing facts). The dashboard counts `UNCERTAIN` evaluations and open fact requests. |
| Failures | 409 `RULE_NOT_EFFECTIVE`, `COVERAGE_NOT_ENCODED`, `SCHEMA_INCOMPATIBLE`; 500 `RUL_SIGNATURE_INVALID` (whole operation refused, no fallback, signal committed); 500 for a content invariant breach. Never `UNCERTAIN` for an error (APP-REQ-019). |
| Verified by | `test_engines.py` (three-valued logic, typing, decision tables); `test_api.py` (bitemporal correction, uncertain cannot be adopted, agent cannot attest); `test_rule_integrity.py` (17 of 18 pass). |

### D. Obligations and controls

| Step | Code |
|---|---|
| Requirement → obligation | An obligation **instance** appears only when a determination is attested as adopted (`engines/applicability.py:428 _activate_obligation`). A `DOES_NOT_APPLY` revision closes it (`:414`). Definitions are seed-only (flow B). |
| Screens | Obligations, Obligation dossier, Controls, Assurance periods |
| Endpoints | `GET /api/obligations`, `/obligations/{lineage}` (`routers/assurance.py:31,49`); snapshots `POST /obligations/{lineage}/snapshots` (`:95`), preview (`:106`), `GET /snapshots/{id}`, `/reproduce` (`:115,120`); occurrences (`:140`), fulfil (`:155`); controls `GET` (`:172,195`), `POST /controls` (`:255`), `/controls/{id}/evaluate` (`:221`); `POST /test-runs` (`:296`) |
| Business rules | **Control evaluation:** `engines/assurance.py:99 evaluate_control` checks admissible evidence (`:58`), IPE status (`:45`) and test runs, and returns a state with reason codes. **Satisfaction expressions:** `:268 eval_expression`, four-valued ALL/ANY/K-of-N. **Snapshot:** `:369 compute_snapshot` splits the period into segments, evaluates each and stores a dependency digest. **Test runs** are inputs with provenance, never direct control state (STA-REQ-008). |
| Ownership and progress | Owners are stored on implementations. A progress or completion percentage is **not specified**, and STA forbids a blended score; the dashboard states "counts only — never a blended compliance score" (`routers/dashboard.py:113`). |
| Status transitions | Occurrence fulfilment, exception and compensation decisions and issue transitions **overwrite status in place** (I-04): `routers/assurance.py:155,353,415,460` |
| Failures | 409 for invalid transitions; the four-eyes rule (requester ≠ approver) returns 403 or 409; manual "green" is impossible |
| Verified by | `test_engines.py` (expression semantics, degraded never satisfies, compensation only when valid); `test_api.py` (exception never changes state); `test_snapshot_determinism.py` (1 SQLite, 3 PostgreSQL-only) |

### E. Evidence and IPE

| Step | Code |
|---|---|
| Upload | `POST /api/evidence/upload` (`routers/evidence.py:58`) → `engines/evidence.py:67 capture_evidence`. Steps: encrypt the payload (AES-256-GCM under a per-evidence key, wrapped by a tenant key derived by HKDF from a **local master key file**, `engines/keys.py:30-64`); hash the ciphertext; write `backend/var/objects/<key>.bin`; write `evidence_artifact` and its manifest; append a hash-chained `evidence_ledger_event` (`:44`); emit to the outbox. Idempotent on the capture key. |
| Association | `POST /api/evidence/{id}/bindings` (`:203`), revisioned |
| Lifecycle | `supersede`, `invalidate`, `hold`, `release-hold`, `erase` (`:135-189`); erasure destroys the wrapped data key (P-16); a hold blocks erasure |
| Integrity | `POST /api/evidence/{id}/verify` (`:119` → `engines/evidence.py:181`); `GET /api/ledger`, `POST /api/ledger/verify` (`:249,261` → `verify_chain` `:241`) |
| Checkpoint | `POST /api/ledger/checkpoint` (`:268` → `:300 create_checkpoint`), **on demand only**: a Merkle root and proofs (`:281`), Ed25519 signature with the single cell key (I-05), a file in `backend/var/worm/` set read-only (0o444), and status `TSA_PENDING` (no RFC 3161 request, P-05) |
| IPE | `POST /api/ipe/source-runs` (`routers/integration.py:70` → `engines/ipe.py:109 execute_source_run`). The run's data arrives **in the request body**; no connector pulls it. It writes the population manifest, mapping and accuracy sub-results. Re-evaluation: `POST /ipe/source-runs/{id}/evaluate-ipe` (`:141`, human only). Anomaly dispositions: `:163`. |
| Conflicts | `POST/GET /api/evidence-conflicts`, `/resolve` (`:217-243`). **No screen calls them.** |
| Failures | 403 for erasing held evidence; 409 for lifecycle conflicts; integrity mismatches open an integrity incident (`engines/evidence.py:264`) |
| Verified by | `test_api.py` (`evidence_integrity_package_and_offline_verifier`, `hold_blocks_erasure`); `test_engines.py` (ledger tamper detection and Merkle proof); `test_authorization.py` (IPE authorisation) |

### F. Assurance and audit packages

| Step | Code |
|---|---|
| Organisation dashboard | `GET /api/dashboard` (`routers/dashboard.py:27`). Returns assurance counts and totals from each obligation's newest snapshot (`:92`), broken down by entity and severity; evidence conditions; open issues and fact requests; `UNCERTAIN` evaluations; review items; ledger head and checkpoint/verify sequence; connector certification; release; runtime health; and the denominator rule. Counts only, recomputed on every call. |
| Needs Attention | `GET /api/dashboard/attention` (`:83`): open issues plus open fact requests, counted as `/dashboard` counts them. Shown as the header bell (`frontend/components/Shell.tsx`). |
| Assurance Position | The per-entity `assurance_counts` in `/dashboard`. No period selector: it always uses the newest snapshot per obligation. |
| Audit packages | `POST /api/packages` (`routers/evidence.py:285` → `engines/evidence.py:348 build_package`) builds a signed zip with the manifest, included evidence, checkpoint manifest and proofs. Then `GET /packages`, `/download` (`:320,326`), `POST /packages/{id}/verify` (`:332`), public `POST /packages/verify-upload` (`:346`, size-limited) and `GET /trust/public-key` (`:365`). Offline: `backend/verifier/verify_package.py`. |
| Failures | Package verification returns explicit per-step results, never a 500 for bad archives (26 tests) |
| Verified by | `test_package_verifier.py` (26); `test_api.py` (package and offline verifier; header attention counts equal dashboard counts) |

### G. Super Admin operations

| Step | Code |
|---|---|
| Screens | Super Admin dashboard (`components/PlatformAdmin.tsx`) and organisation detail (`app/organisations/[id]/page.tsx`) |
| Endpoints | `GET /api/platform/organisations` (`routers/platform.py:139`), `/{tenant_id}` (`:154`); `POST` create (`:160`); `PATCH` (`:195`); `POST /{id}/status` (`:207`, suspend or reactivate); `POST /{id}/admins` (`:227`, appoints a `TENANT_OWNER` with a one-time password that must be changed) |
| Authorisation boundary | The platform permission plus no tenant. Registry reads use `system_scope` (`security.py:194`), limited to organisation and entity metadata. No route returns customer evidence, facts or assurance to a Super Admin. |
| Audit | Every change is written as an audit event **in the organisation's own trail**. There is no platform-wide audit history query for the Super Admin. |
| Verified by | `test_platform.py` (6): one-time bootstrap; fails closed; registry without customer data; customer roles refused; no API grants Super Admin; organisation lifecycle and audit |

### H. Background processing

| Concern | Actual behaviour |
|---|---|
| Outbox creation | `platform.py:76 emit` inserts in the same transaction (DAT-REQ-014 ✔) |
| Dispatch | **None.** No publisher, relay, broker or consumer. Rows are only readable through `GET /api/outbox` (`routers/core.py:53`). |
| Publication impact | Synchronous inside `POST /rules/drafts/{id}/publish`, one savepoint per subject, under `system_scope`. It runs in the request time and transaction. |
| Tenant-specific jobs | None. Cell-wide work runs in request handlers under `system_scope`. |
| Retries, leases, dead letters | None |
| Idempotency | Evaluation key and evidence capture key only. `platform_idempotency_record` exists but nothing writes to it (P-07). |
| Crash recovery | Transaction atomicity only. A crash mid-request rolls back. Nothing resumes. |
| Checkpointing | Manual (`POST /api/ledger/checkpoint`); no schedule |

---

## 6. Workflow-by-workflow implementation audit

| Workflow | Spec requires | Code does | Gap | Status |
|---|---|---|---|---|
| A. Authentication and sessions | IAM: OIDC/SAML federation, SCIM, phishing-resistant MFA, JIT/break-glass; BFF cookie sessions (C-08); per-read authorisation (G-08) | Local passwords (scrypt), lockout, server-side revocable sessions, static role map, bearer token in `localStorage` | Federation, MFA, SCIM, JIT and break-glass (P-12, P-17); token storage (I-06); per-read policy (P-01); dev-header guard (I-08) | Partially implemented |
| B. Regulatory source management | Ingestion under recorded rights and fingerprint (PRD §11); governed AI extraction through a model gateway; human verification; versioned requirements; release governance | Manual source text; regex "extraction"; human verification; versioned requirement and provision rows | Ingestion and feeds; a real extractor and model gateway (M-12); diff view; reviewer assignment; definition authoring APIs | Partially implemented |
| C. Facts and applicability | APP-REQ-001…020; bitemporal facts (TIM); re-evaluation on change (APP §10); integrity before input freeze (APP-REQ-006) | Implemented, including integrity (A9a), with synchronous re-evaluation | One failing test; unordered rule selection in `reevaluate_for_fact`; no durable impact scheduling; signature verification (A9b, blocked) | Partially implemented (core evaluation verified) |
| D. Obligations and controls | STA state machines, append-only revisions (DAT-REQ-009), period snapshots reproducible (STA-REQ-019/021) | Implemented engines; some statuses overwritten in place | I-04 append-only history; I-03 reproducibility | Partially implemented |
| E. Evidence and IPE | EVD vault, ledger, checkpoints with RFC 3161 and WORM; certified connectors; IPE | Vault, ledger, Merkle and signature, IPE engine | TSA (P-05), real WORM, KMS (P-06), real connector capture, conflict screen | Partially implemented |
| F. Assurance and packages | Period views; executive exposure for a selected period; signed packages; offline verification | Snapshots, dashboard counts, packages, verifier | Period selection on the dashboard; revoking package access; snapshot reproduction stability (I-03) | Partially implemented |
| G. Super Admin | WF-08 / WF-TEN; IAM scoped grants and JIT | Organisation registry CRUD and lifecycle with audit | Platform audit history view; JIT and scoped privileged grants | Partially implemented (registry verified) |
| H. Background processing | EVT outbox relay, at-least-once delivery, inbox and dead letters (EVT-REQ-003…006); WRK durable workflows (C-10) | Outbox write only | Everything after the write (M-02, M-03) | Missing |

---

## 7. Screen-by-screen wireframe-to-backend mapping

Abbreviations: **IV** implemented and verified; **INV** implemented but not verified; **PI** partially implemented;
**PNC** present but not functionally connected; **M** missing; **BD** blocked by dependency; **NS** not specified.
"Frontend" names the page that calls the API, from a scan of every `api()`, `useApi()` and `download()` call.

| # | Wireframe screen / module | Expected user action | Required backend API | Existing implementation | Dependency | Status | Remaining work |
|---|---|---|---|---|---|---|---|
| 1 | Org dashboard: overview and organisation context | See organisation, entities, as-of time | `GET /api/dashboard`, `/org/tenant` | `dashboard.py:27`; frontend `app/page.tsx` | snapshots, evaluations, evidence | IV | None for the current scope |
| 2 | Org dashboard: Needs Attention | See open work | `GET /api/dashboard/attention` | `dashboard.py:83`; `Shell.tsx` | issues, fact requests | IV | The name is not in the specifications. Evidence freshness (row 4) is not included. |
| 3 | Org dashboard: Assurance Position | Assurance per entity | `GET /api/dashboard` (`assurance_counts`) | Newest snapshot per obligation (`dashboard.py:92`) | `assurance_snapshot` | INV | No test asserts the per-entity values. The name is not in the specifications. |
| 4 | PRD §12 Executive exposure and Evidence freshness | Obligations not proven for a **selected period**; stale or low-provenance evidence | Period-parameterised summary | Dashboard counts use the newest snapshot only; evidence conditions counted | snapshots, evidence | PI | Period parameter; freshness and staleness rules per PRD §12 |
| 5 | PRD §12 Regulatory change impact | Which released provisions changed, who changed determination, pending action | Change-impact query | Publication returns only an impact count | releases, evaluations | M | Read model and endpoint |
| 6 | PRD §12 Audit readiness | Can the period be packaged and verified now? | Readiness check | Packages can be built and verified; no readiness query | packages, checkpoints | PI | Readiness endpoint |
| 7 | WF-DOM-06 / WF-TIM-02 Historical reconstruction | Compare two record cutoffs | `GET /api/history/compare` | `applicability.py:283`; `app/history/page.tsx` | facts, evaluations | IV | Applicability only. Snapshot reproduction (I-03) is in row 32. |
| 8 | WF-01 / WF-DOM-01 Regulatory sources | List and create sources | `GET/POST /api/catalog/sources` | `catalog.py:35,61`; `app/regulatory/page.tsx` | `catalog_source_record` | INV | Ingestion from official sources, fingerprint and rights (PRD §11) |
| 9 | WF-01 Extraction | Propose provisions | `POST /catalog/sources/{id}/extract` | Regex splitter labelled `AI_EXTRACTED` (`catalog.py:94`) | — | PI | Governed extractor through a model gateway (M-12), or an explicit decision to keep it manual |
| 10 | WF-01 Requirement verification | Human legal verification | `POST /catalog/requirements/{id}/verify`, `PUT /catalog/provisions/{id}` | `catalog.py:142,157`; `RequirementViews.tsx` | — | INV | Tests |
| 11 | WF-01 Regulatory Change Inbox contract | See impacted entity count, missing-fact count, reviewer, severity; view diff; assign review | Inbox query and assign action | List exists; field coverage not verified; **no assign-review endpoint** | — | PI | Assign-review API; inbox fields per WF-01 |
| 12 | WF-RUL-01/02/04 Rule builder, decision table, workbench | Author, validate, test, sandbox | `/api/rules/drafts*` | `rules.py:56-145`; `app/rules/*`, `RuleTesting.tsx` | `catalog_rule_draft` | IV | — |
| 13 | WF-RUL-03, WF-APP-03 Semantic diff and shadow impact | View diff, shadow run | `GET /rules/diff`, `POST /drafts/{id}/shadow` | `rules.py:213,358` | evaluations | INV | Tests |
| 14 | WF-08 / RUL §15 Publication and signed release | Review, release | `POST /drafts/{id}/{step}`, `/publish` | `rules.py:164-315`, integrity checks | `catalog_*`, keys | IV | Signature verification against a trusted key (A9b, blocked); content-signing key (I-05) |
| 15 | WF-02 / WF-APP-01 / WF-DOM-03 Applicability review and trace | Evaluate, see the trace | `POST /applicability/evaluations`, `GET …/{id}` | `applicability.py:128,176`; `app/applicability/*` | facts, rules | IV | — |
| 16 | WF-APP-02 / WF-DOM-02 / WF-TIM-01 Fact profile and timeline | Assert and correct facts; view history | `GET/POST /subjects/{id}/facts`, `/history` | `applicability.py:28,55,77`; `AssertModal.tsx` | `applicability_fact_assertion_revision` | IV | — |
| 17 | APP §10 Re-evaluation on fact change | Dependent evaluations refresh | Within the fact assertion | `engines/applicability.py:456`, synchronous | evaluations | PI | Fix the failing test or the code; deterministic rule choice; durable scheduling (Task 8) |
| 18 | WF-02 Missing facts / fact requests | Supply missing facts | `GET /fact-requests` | `applicability.py:269`; `app/fact-requests/page.tsx` | `applicability_fact_request` | IV | — |
| 19 | WF-02 Attest, adopt, challenge | Attest when authorised; challenge | `POST …/attest`, `…/challenge` | `applicability.py:227,243`; `AttestModal.tsx` | determinations, obligations | INV | Challenge tests; request-legal-review action not found |
| 20 | WF-03 / WF-DOM-04 / WF-STA-01 Obligation dossier | Open controls and evidence, request remediation, generate snapshot | `GET /obligations*`, `POST …/snapshots` | `assurance.py:31,49,95` | obligations, controls, evidence | INV | Tests; WF-03 "request remediation" action not found |
| 21 | Obligation occurrences | Record and fulfil occurrences | `POST /obligations/{l}/occurrences`, `/occurrences/{id}/fulfil` | `assurance.py:140,155` | `assurance_obligation_occurrence` | PI | Append-only history (I-04); wall clock in occurrence logic (I-03c) |
| 22 | WF-STA-02 Controls and control evaluation | Create, evaluate | `GET/POST /controls`, `/controls/{id}/evaluate` | `assurance.py:172-255`; `app/controls/*` | implementations, test runs | IV | — |
| 23 | Test-run ingestion | Record results | `POST /test-runs` | `assurance.py:296` | `assurance_test_run` | INV | Tests; external GRC ingestion is Task 11 |
| 24 | Control ownership and progress calculations | — | — | Owners stored; no progress percentage (STA forbids a blended score) | — | NS | None unless specified |
| 25 | WF-STA-04 Exceptions, compensations, issues, risk acceptances | Request, decide, transition | `/exceptions*`, `/compensations*`, `/issues*`, `/risk-acceptances` | `assurance.py:324-491`; `app/governance/page.tsx` | governance tables | PI | Append-only status events (I-04, blocked on the event-table convention) |
| 26 | WF-04 / WF-DOM-05 / WF-EVD-01 Evidence record | Upload, view, verify integrity, download | `POST /evidence/upload`, `GET /evidence*`, `POST …/verify` | `evidence.py:50-119`; `app/evidence/*` | vault, ledger | IV | KMS-backed keys (P-06, production) |
| 27 | WF-04 / WF-EVD-04 Evidence lifecycle | Supersede, invalidate, hold, release, erase, bind | `POST /evidence/{id}/{action}`, `/bindings` | `evidence.py:135-203` | lifecycle rows | INV | Tests beyond hold and erase |
| 28 | Evidence conflicts | Raise and resolve conflicts | `/evidence-conflicts*` | `evidence.py:217-243` | `evidence_conflict` | PNC | No screen calls it; resolution overwrites status (I-04) |
| 29 | WF-EVD-03 Ledger and checkpoint monitor | View the ledger; verify; checkpoint | `GET /ledger`, `POST /ledger/verify`, `/ledger/checkpoint` | `evidence.py:249-268`; `app/ledger/page.tsx` | ledger, WORM dir | PI | Scheduled checkpoints; RFC 3161 (P-05); real WORM storage; purpose-specific key (I-05) |
| 30 | WF-EVD-02 / WF-IPE-01 Connector capture and source runs | Run a capture | `POST /ipe/source-runs` | `integration.py:70`, data from the request | — | PI | Real connector execution (Task 11); certification lifecycle (I-11) |
| 31 | WF-IPE-02…06 IPE evaluation and anomalies | Reconcile, evaluate, dispose | `/ipe/*` | `integration.py:58-163`; `app/ipe/*` | IPE tables | IV | Disposition history in place (interim guard) |
| 32 | WF-05 / WF-STA-05 / WF-TIM-04 Assurance period view | Coverage, gaps, explain, create issue, export | `POST …/snapshots`, `…/preview`, `GET /snapshots/{id}/reproduce` | `assurance.py:95-120`; `app/obligations/[lineage]` | snapshots | PI | Reproduction stability (I-03; decisions D1–D4); export |
| 33 | WF-STA-03 Roll-up explanation | Explain a result | Inside the snapshot or preview | `engines/assurance.py:576 _explain` | — | INV | Tests |
| 34 | WF-06 / WF-EVD-05 Audit package builder | Validate, generate, revoke external access | `/packages*` | `evidence.py:285-332`; `app/packages/page.tsx` | packages | PI | **Revoke external access: missing**; redaction controls not verified |
| 35 | WF-EVD-06 Offline verifier | Verify a package offline | `POST /packages/verify-upload`; CLI | `evidence.py:346`; `verifier/` | — | IV | Key pinning optional (P-09); TSA |
| 36 | WF-07 / WF-DOM-07 AI/agent asset dossier and PDP | Register assets, versions, grants; policy decisions | `/ai/*` | `ai.py:26-195`; `app/ai/page.tsx` | AI tables | INV | Dossier completeness tests (PDP itself verified); `POST /ai/decisions` and `/ai/acs/evaluate` have no screen (machine-to-machine) |
| 37 | WF-TEN-01/02 Entities and subjects | Create and list entities and subjects | `GET/POST /org/subjects`, `POST /org/entities` | `core.py:74-117`; `app/entities/*`, `app/admin/cells` | org tables | INV | Tests |
| 38 | Organisation settings | — | — | No organisation-level settings screen in the sources. Tenant profile edits exist only for the Super Admin (`PATCH /platform/organisations`). | — | NS | Specify first |
| 39 | WF-IAM-01 / WF-08 Users & Roles | Invite, assign scoped role, revoke | User and role management | `GET /api/actors` (list only); screen is a placeholder | `org_actor` | M | APIs for invite, role assignment and revocation (IAM) |
| 40 | WF-08 / WF-IAM Identity integrations | Configure OIDC/SAML | IdP configuration | None; screen says "not yet available" | G-05 | BD | Decision G-05, then implementation |
| 41 | WF-08 / WF-IAM-07 / WF-SEC-07 Privileged access log | Review privileged actions | Privileged-access and JIT model | `GET /api/audit` (tenant audit list); screen placeholder | audit | PI | JIT, break-glass and support access (P-17) |
| 42 | WF-08 / WF-PRI-02/05 Retention and legal holds | Retention policy; holds | Retention policy APIs | Holds exist; retention policy missing; screen placeholder | — | PI | Privacy schema (M-14) |
| 43 | WF-08 Content releases | View and verify releases | `GET /catalog/releases`, `/{id}/verify` | `catalog.py:227,242`; `app/admin/releases` | releases | INV | Signature trust (A9b) |
| 44 | WF-08 / WF-TEN-02 Cells and residency | View residency; add entity | `GET /org/tenant`, `POST /org/entities` | Single cell `eu-west-cell-a` | `org_cell` | PI | Multi-cell routing and residency moves (M-15) |
| 45 | GLS Controlled dictionary | View codes | `GET /api/dictionary` | `core.py:22`; `app/admin/dictionary` | `codes.py` | INV | — |
| 46 | Super Admin: platform overview | Platform-level status | Platform summary | Organisation list only (`PlatformAdmin.tsx`) | registry | PI | Platform metrics are not specified beyond WF-08/WF-TEN; specify before building |
| 47 | Super Admin: organisation directory and detail | List, open | `GET /platform/organisations*` | `platform.py:139,154` | registry | IV | — |
| 48 | Super Admin: organisation lifecycle | Create, edit, suspend or reactivate, appoint admin | `POST/PATCH /platform/organisations*` | `platform.py:160-227` | registry, accounts | IV | — |
| 49 | Super Admin: administrative audit history | Review platform actions | Platform audit query | Writes go to each organisation's trail; no cross-organisation query | audit | PI | Platform audit endpoint within the Super Admin boundary |
| 50 | WF-EVT-03 / WF-WRK-07 Outbox, worker and queue consoles | Monitor delivery and workers | Monitoring APIs | `GET /api/outbox` only (and it leaks no-tenant events, section 10) | workers | M | Depends on Task 8 |
| 51 | EVT/WRK background processing | — | Relay, workers, retries, dead letters | None | G-09 | M | Task 8 |
| 52 | PRD §11 external integrations | Connect GRC, ServiceNow, identity, cloud, feeds | Connector framework | Certification registry only; no outbound integration | — | M | Task 11 |
| 53 | API-REQ-004/005 Idempotency keys | Safe retries | Idempotency-Key handling | Table exists, unused | — | M | Task 10 |
| 54 | API-REQ-012 Cursor pagination, filtering, sorting | Page large lists | Cursor parameters | `limit` on 3 endpoints only | — | M | Task 10 |
| 55 | Consistent API error format | Predictable errors | Single error schema | Mixed string and dict `detail` | — | PI | Task 10 (single error contract; API-001 profile) |

**Frontend-to-backend connectivity.**
- **Every frontend call has a backend route.** 41 frontend files call 86 distinct API paths, and every one, including
  dynamic action paths and direct `fetch` calls, matches a route and method.
- **Backend routes with no frontend caller:**
  - `/api/evidence-conflicts*` (row 28);
  - `/api/catalog/coverage` and `/api/catalog/sets`;
  - `/api/me`;
  - `GET /api/rules/versions/{id}`;
  - `POST /api/ai/decisions` and `/api/ai/acs/evaluate` (machine-to-machine by design);
  - `GET /api/health` (operations).

---

## 8. API completeness report

| Area | Spec requires | Code does | Gap |
|---|---|---|---|
| Contract | API-REQ-001: version-controlled OpenAPI 3.1 before frontend completion. API-REQ-002: major version in the URI. | OpenAPI is generated at run time (112 paths) and not committed. Routes are under `/api`, with no `/v1`. | Commit the contract and add a CI diff gate; add `/v1` with `/api` as an alias (P-08) |
| Idempotency | API-REQ-004/005: every externally reachable mutation needs an idempotency key; a reused key with a different body is refused | Only evaluations (evaluation key) and evidence capture (capture key). `platform_idempotency_record` is unused. | Idempotency-Key dependency on all 66 mutations (P-07) |
| Concurrency | API-REQ-006: optimistic preconditions on mutable revisioned resources | None (no ETag or `If-Match`) | ETag / `If-Match` (M-05) |
| Immutable resources | API-REQ-007: no update endpoints on completed evaluations, snapshots or attestations | None exist, and the database refuses updates (0003) | — (met) |
| Tenant from context | API-REQ-008/009 | Tenant from the authenticated actor; cell resolved server-side | — (met, application level) |
| Fine-grained authorisation | API-REQ-010: IAM policy beyond scopes, including resource relationship | Mutations: permission check. Reads: any signed-in member of the tenant (P-01). | Per-read permission policy (G-08) |
| Historical APIs | API-REQ-011: valid time and record time kept separate | `history/compare` takes `valid_at` plus `left_seq`/`right_seq`; facts carry both | Not verified across all list endpoints |
| Pagination | API-REQ-012: cursor pagination with a stable order | `limit` only on `/outbox`, `/audit`, `/ledger` (`core.py:54,60`, `evidence.py:250`); every other list is unbounded | Cursor pagination, filters and sorting |
| Request validation | Unknown fields rejected | 7 of 43 request models set `extra="forbid"` (I-09) | Forbid unknown fields everywhere |
| Error format | A single error contract | 107 `HTTPException` sites pass a string and 15 pass `{"error": CODE, …}`; no request or correlation ID in errors | Single machine-readable error body with a stable code and correlation ID. The exact format API-001 requires was not re-extracted for this audit; take it from API-001 when implementing. |
| Status codes | API-001 status table (409 domain conflict, 422 semantic precondition, 500 unexpected fault, 503 dependency unavailable) | Broadly followed. Integrity failures return 500, matching existing content-error handling. | Not an approved mapping for integrity errors (open) |
| Unauthenticated reads | — | `GET /api/auth/session`, `/auth/demo-accounts` (demo only), `/health`, `/dictionary`, `/watermark`, `/trust/public-key` | `/watermark` exposes the cell's record-sequence counter (I-10) |
| Missing endpoints (from section 7) | WF-01, WF-06, WF-IAM-01, PRD §12 | — | Review assignment; package revocation; user and role management; regulatory change impact; audit readiness; definition authoring (obligation, fact, set, control); platform audit history; retention policy; privileged access and JIT |

---

## 9. Database and migration audit

| Item | Evidence | Status |
|---|---|---|
| Migration tool | `python -m app.migrate status/upgrade/check/downgrade/fingerprint` (`app/migrate.py:358`). Raw `alembic upgrade/stamp/downgrade` refused (`migrations/env.py:25`). Advisory lock. Adopts databases created before migrations by baseline fingerprint. Downgrade needs `--confirm`. | Implemented. Verified on PostgreSQL 17 on 29 Oct (16/16 `test_migrations`), not re-run today. |
| Revisions | `0001` 63 tables; `0002` RLS, grants, append-only ledger and audit; `0003` 11 completed-result tables immutable; `0004` published catalogue immutable. **No `0005`.** | Head is `0004` |
| Start-up guard | `require_current` refuses to start unless at head; start-up runs no DDL | Implemented (PostgreSQL test on 29 Oct) |
| Tenant tables | 42 tables carry `tenant_id` (3 nullable: `org_actor`, `platform_audit_event`, `platform_outbox_event`). 21 do not (catalogue, credentials, sessions, record sequence, cells, checkpoints, connector definitions). | Inventory complete (section 11) |
| RLS | `ENABLE` plus a `tenant_isolation` policy on 41 of 42 tenant tables. `org_actor` is exempt by design so sign-in can find the identity (`0002…py:43`). **`FORCE` on none** (DAT-REQ-003 requires it). | Partially implemented (P-02) |
| RLS bypass | Every policy admits `assure_system()`, which reads `app.system`, a setting any session can change with `set_config` | **Critical (I-01)**, open; blocked on the system-job design decision |
| No-tenant rows | `platform_audit_event` and `platform_outbox_event` policies admit `tenant_id IS NULL` in `USING` and `WITH CHECK` (`0002…py:284-292`) | Every tenant can read, and insert, cell-wide events and audit rows |
| Immutability | Triggers stop UPDATE and DELETE by every role on 15 tables, plus the append-only ledger and audit. The runtime role also lacks those privileges. The owner can disable the triggers with DDL. | Implemented; A10 open |
| Ownership | Owner role `assure` (`NOSUPERUSER CREATEROLE`) owns all 64 tables and is a login role used by the API process | DAT §13 requires non-login owner roles, and runtime roles must not be owners. Not met. |
| Role model | `assure` and `assure_app` only | DAT §13 requires `assure_migrator`, `assure_app_rw`, `assure_app_ro`, `assure_outbox_publisher`, `assure_catalog_loader`, `assure_analytics_ro`, `assure_breakglass_admin` and non-login table owners. **Missing.** |
| Bitemporal coverage | Valid and record time on three table families only; record time missing on ledger events, IPE evaluations, holds and others (P-03) | Partially implemented |
| History | Governance and occurrence statuses updated in place (I-04, DAT-REQ-009) | Partially implemented; blocked on the event-table convention |
| Constraints and indexes | 12 tables have no foreign key, unique constraint or secondary index beyond the primary key (e.g. `org_tenant`, `catalog_content_release`, `catalog_controlled_set_version`, `evidence_ledger_checkpoint`, `platform_record_sequence`). Several are reference or singleton tables where this is acceptable. Not reviewed table by table for DAT-required uniques. | Not verified against DAT's physical model |
| Idempotency table | `platform_idempotency_record` exists and is never written | Present but not functionally connected |
| Backup and restore | The runbook checks `pg_restore --list` only; semantic restore checks exist only as scratch tooling (QA-1) | Partially implemented |

---

## 10. Authentication, authorisation and tenant security

| Control | Spec requires | Code does | Gap | Severity |
|---|---|---|---|---|
| Tenant context | DAT §12: transaction-scoped context; a narrow `SECURITY DEFINER` path only for system jobs | `set_config(…, true)` per transaction (equivalent to `SET LOCAL`), stamped from server-side session state | Correct scoping. **Limitation:** the runtime role can call `set_config` itself, so a compromised or buggy code path can choose any tenant, or switch on `app.system` (I-01). Context isn't bound to anything the database can check. | Critical |
| Session validation | IAM-REQ-018, SEC-REQ-020, C-08: BFF with cookie session | HMAC bearer token plus a server-side session row; revocable; 8-hour expiry; token in `localStorage` | XSS can steal the token (I-06). The HMAC key is a local file (`auth.py:46`). | High |
| Authentication strength | P-12 / IAM: OIDC/SAML, SCIM, phishing-resistant MFA | Local passwords only | Federation, MFA, SCIM (G-05) | High (production) |
| Authorisation, writes | Deny by default | Every mutation guarded; start-up refuses an unguarded route (`main.py:62`); 22 tests | — | Met |
| Authorisation, reads | G-08 per-read policy; external-auditor scoping | Any signed-in member reads all tenant data (P-01) | Read policy | High |
| Privileged access | IAM WF-IAM-03/04/08, P-17: JIT, break-glass, support sessions | None; Super Admin comes from environment bootstrap | JIT and break-glass | High (production) |
| Super Admin boundary | WF-TEN / WF-08 | Registry metadata only via `system_scope`; tested | Platform audit history view | Medium |
| No-tenant event visibility | EVT §19: `SECURITY_CRITICAL` events not customer-visible by default | `GET /api/outbox` (`core.py:53`) returns the caller's events **plus every no-tenant event**, and RLS allows it. Since 29 Oct that includes `security.content_signature_failed` from publication. | Restrict no-tenant rows to platform roles; registry-based visibility | Medium |
| Dev identity header | Dev only | `ZA_DEV_IDENTITY_HEADER=1` honoured in any environment (I-08) | Start-up guard | Medium |
| Owner credential in the API | DAT-REQ-004; SEC-INV-07 | The API holds `DATABASE_ADMIN_URL` for its lifetime (`db.py:56,89`) for start-up checks, bootstrap and demo seed | Remove from the runtime (Task 1) | High |
| Secrets and config | — | `.env` is git-ignored; CI uses generated passwords; master key, signing key and session key are plaintext files under `backend/var/keys` | KMS/HSM (G-06, P-06) | High (production) |
| Audit events | IAM-REQ-023 append-only audit | Append-only `platform_audit_event` (trigger) | Authorisation **denials** are not audited (`security.authorization_denied` is never emitted) | Medium |

---

## 11. Phase 1 and Phase 2 architecture status

The "Version 2.2 closure review" and any Phase 1 or Phase 2 decision records were **not found** (section 3.1). Each
item the brief names is checked below against the code and the controlled specifications. No document is treated as
proof of implementation.

### Phase 1 (rule integrity, publication, rollback)

| Item | Code evidence | Application-level test (SQLite, today) | PostgreSQL 17 | Status |
|---|---|---|---|---|
| Rule integrity before evaluation (AST hash under the declared versions) | `engines/applicability.py:72-118` | `test_rule_integrity`: altered AST, schema version, consistent rewrite, etc. pass | Passed 29 Oct (13/13); today's 5 new tests not run | Implemented and verified (application). PostgreSQL not verified for the current tree. |
| SetVersion hash validation | At evaluation: re-hashed from members (`:76-83`). At publication: missing set refused (`routers/rules.py:245-258`) and new version verified before impact (`:308-310`). | Pass (altered members; publication with zero subjects; missing set) | Not run on the current tree | Implemented and verified (application) |
| Publication integrity | Gates plus once-only writes plus verification | Pass | 29 Oct pass for the earlier version | As above |
| Evaluation rollback | `integrity_fail_closed` (`:139`): rollback, signal, 500; no fallback | Pass on 5 entry points plus fact assertion | Not run today | Implemented and verified (application). **Residual:** a version made non-effective by tampering makes evaluation fall back to the previous version (failing test, section 2). |
| Audit and outbox behaviour | After rollback, audit `content.verify` plus outbox `security.content_signature_failed` committed in a new transaction on the same session | Pass | Not run today | **Implemented, awaiting approval** (EVT/SEC owners). Durable but never delivered (no relay). |
| Signature verification (A9b) | Signature presence only | — | — | Blocked (G-06, RUL-ADR-06, K1–K6) |
| PostgreSQL enforcement | 0003/0004 triggers; runtime privileges | PostgreSQL-only tests skipped on SQLite | 29 Oct: `test_immutability` 7/7 | Verified on 29 Oct; A10 open |

### Phase 2 (tenant security, roles, workers)

| Item | Expected (brief / DAT) | Code evidence | Status |
|---|---|---|---|
| Tenant-context security | Context the application cannot spoof | `db.py:132-157` stamps per transaction; the runtime role can set it itself | **Partially implemented**; I-01 open (Critical) |
| `SET LOCAL` limitations | Documented and mitigated | Transaction-local `set_config` is used, so pooled connections can't leak context. Nothing prevents in-process misuse. | Partially implemented |
| Session validation and tenant authorisation | Validated session to actor to tenant | `get_ctx`; tenant only from the actor | Implemented and verified (application) |
| RLS policy coverage | Every tenant table | 41/42 (`org_actor` exempt by design) | Implemented (verified on PostgreSQL 29 Oct, `test_rls` 5/5) |
| `FORCE ROW LEVEL SECURITY` | All tenant tables | 0 | **Missing** (P-02); blocked on the system-job decision |
| Tenant-table inventory | Exact list | 42 tables (section 9); 3 with nullable `tenant_id` | Complete |
| Role separation `assure_owner`, `assure_migrator`, `assure_app`, `assure_job_owner` | Brief (source unavailable). DAT §13 lists a different set. | Only `assure` and `assure_app` exist | **Missing**. Discrepancy: the brief's role names do not match DAT §13; decide which governs. |
| Role memberships and privileges | Least privilege | `assure` is `CREATEROLE` and member of `assure_app` (`WITH ADMIN OPTION, INHERIT FALSE, SET FALSE` on the development server); runtime least privilege checked at start-up | Partially implemented |
| `DATABASE_ADMIN_URL` removed from the API runtime | Required | Still read (`db.py:56`) and used at start-up (`main.py:28-43`) | **Missing** |
| Two-phase outbox impact scheduling | Required by the brief | Impact re-evaluation is synchronous in the publish request | **Missing** |
| Publication worker state machine | Required by the brief | No worker | **Missing** |
| Checkpoint cryptographic integrity | Merkle plus signature plus timestamp | Merkle plus Ed25519 (shared key); `TSA_PENDING`; manual | Partially implemented (P-05, I-05) |
| WORM storage boundary | Object-lock storage | Local file `chmod 0o444` (`engines/evidence.py`) | **Missing** (production); development stand-in only |
| Migration `0005` completeness | Required by the brief | Does not exist | **Missing** |

---

## 12. Background jobs and integration status

### Background processing

| Capability | Status | Evidence |
|---|---|---|
| Outbox write in the same transaction | Implemented and verified | `platform.py:76`; publication atomicity test |
| Outbox relay, broker, consumer inbox, dead letters, replay | Missing (M-03, P-04) | No code. A grep of `backend/app` for `BackgroundTasks`, `threading`, `asyncio`, `create_task`, schedulers, `lease`, `retry`, `dead letter`, `relay` and `dispatch` finds nothing relevant. |
| Durable workflow runtime and workers (checkpoints, verification, impact jobs, connectors) | Missing (M-02); blocked on G-09 (C-10 managed Temporal) | — |
| Publication impact processing | Present, but synchronous and in-request | `routers/rules.py:~300-315` |
| Tenant-specific job execution | Missing | — |
| Retries, leases, crash recovery | Missing | — |

### External integrations (PRD §11)

| Integration class (PRD §11) | Status |
|---|---|
| Existing GRC/compliance tools (certified control results and evidence metadata) | Missing. Test runs and source runs are posted to the API by hand; no connector pulls them. |
| Cloud posture / IAM / DevOps evidence | Missing |
| ServiceNow / Archer issues (bidirectional, "when demanded") | Missing |
| Regulatory feeds / official sources (rights, fingerprint) | Missing. Sources are entered manually. |
| AI governance registries | Missing |
| Identity provider (OIDC/SAML/SCIM) | Missing; blocked on G-05 |
| RFC 3161 timestamp authority | Missing (`ZA_TSA_URL` only records an intended URL; P-05) |
| KMS/HSM | Missing; blocked on G-06 |
| Model gateway for extraction | Missing (M-12) |
| Connector certification registry (internal) | Implemented and verified (`engines/connectors.py`, 3 tests); no transition state machine (I-11) |

No module in `backend/app` imports an HTTP client (`httpx`, `requests`, `urllib`) or a cloud, mail or LLM SDK.

---

## 13. Test and verification results

| Run | Command | Environment | Result | PostgreSQL 17? | Proves |
|---|---|---|---|---|---|
| This audit, 30 Oct | `.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider -ra --junitxml=<scratchpad>\audit-sqlite.xml` (from `backend/`) | Throwaway SQLite and temporary data folder (`tests/conftest.py`); no shared database touched | **154 collected: 119 passed, 1 failed, 34 skipped** | No | Application behaviour only |
| Failure | `tests/test_rule_integrity.py::test_fact_assertion_preserves_non_integrity_evaluation_errors` | — | `assert any("error" in reeval …)` is false: the response is 200 but no re-evaluation reports an error | — | Likely silent fallback to the previous rule version (section 2) |
| Skips | `test_immutability` 7, `test_migrations` 15, `test_rls` 5, `test_pg_bootstrap` 4, `test_snapshot_determinism` 3 | — | "PostgreSQL-only" | — | Nothing on SQLite |
| Last PostgreSQL run, 29 Oct (earlier session) | `ci_pg_repro.py` on a disposable PostgreSQL 17.11 cluster | Disposable cluster, deleted afterwards | 149 passed, 0 skipped; every CI gate passed | Yes | Database enforcement **for the tree as of 29 Oct**. Today's 5 new tests and router changes are **not covered**. |
| GitHub Actions | — | — | Never run on the uncommitted work | — | Nothing |

**Not re-run in this audit:** the PostgreSQL suite. It applies migrations, even to a disposable cluster, which this
audit's rules prohibit. **Database-level guarantees for the current tree are therefore not verified.**

### Existing coverage by module (today's run)

| Module | Tests | What it covers |
|---|---|---|
| `test_api.py` | 15 | Seeded lifecycle, INV-01, uncertain adoption, cross-tenant and content-staff isolation, bitemporal correction, exceptions, evidence, package and offline verifier, holds, PDP, sign-in, lockout, password change, provenance and cell, attention counts |
| `test_authorization.py` | 22 | Guard on every mutation; route matrix; agents need grants; human-only actions; cross-organisation access; IPE and connector authorisation |
| `test_engines.py` | 27 | Three-valued logic, typing, decision tables, canonical hash, expressions, IPE tables, ledger tamper and Merkle |
| `test_package_verifier.py` | 26 | Archive limits and adversarial packages |
| `test_platform.py` | 6 | Super Admin registry, lifecycle and boundary |
| `test_rule_integrity.py` | 18 | Task 3 tamper, refusal, publication and fact-assertion paths (1 failing) |
| `test_database_config.py` | 4 | Database configuration refusals |
| `test_migrations`, `test_immutability`, `test_rls`, `test_pg_bootstrap`, `test_snapshot_determinism` | 35 | PostgreSQL enforcement (skipped on SQLite) |

### Missing coverage, mapped to tasks

| Area | Missing test | Task |
|---|---|---|
| Full lifecycle | One end-to-end test from source → requirement verified → rule published → fact → evaluation → attestation → obligation → control and evidence → snapshot → package → offline verify, on PostgreSQL | 0 / 10 |
| Tenant isolation | A generated test over every tenant table and path, with canary tenants (QA-2); a test proving the I-01 bypass is closed | 1, 2 |
| Role separation | Every DAT §13 role's privileges against a reviewed matrix | 1 |
| Sessions | Cookie/BFF session, MFA, revocation propagation | 2 |
| Workers | Crash and duplicate delivery, out-of-order, lease expiry, retries, dead letters (M-09) | 8 |
| Checkpoints | Scheduled checkpoint, TSA token validation, key rotation | 6, 7 |
| Migrations | `0005` upgrade and downgrade, plus restore semantics (QA-1, M-10) | 1 |
| API | Idempotency replay and conflict, pagination stability, error contract | 10 |
| Untested endpoints | Catalog source, extract and verify; diff and shadow; challenge; occurrences; evidence lifecycle actions; conflicts; IPE disposition history; entities and subjects | 3–7 |

---

## 14. Missing backend functionality

Grouped by domain. "Row" refers to section 7.

| Domain | Missing or incomplete | Rows | Task |
|---|---|---|---|
| Baseline | One failing test (possible silent version fallback); unordered rule choice in `reevaluate_for_fact`; PostgreSQL unverified for the current tree; CI count gate stale (13 vs 18) | 17 | 0 |
| Database | DAT §13 role model; non-login owners; `FORCE` RLS; I-01 bypass; A10; owner credential in the API; tighter no-tenant outbox and audit policies; migration `0005`; record time (P-03); semantic restore test | — | 1 |
| Identity and access | Cookie/BFF sessions; OIDC/SAML/SCIM/MFA; scoped role assignments; organisation user and role management; per-read authorisation; JIT, break-glass and support access; denial audit events; dev-header guard; `/watermark` authentication | 39, 40, 41 | 2 |
| Regulatory content | Source ingestion (rights, fingerprint, versions); governed extractor or explicit manual policy; inbox fields and review assignment; definition authoring APIs; regulatory-change-impact query | 5, 8, 9, 11 | 3 |
| Applicability | Durable fact-change impact scheduling; request-legal-review action; temporal profile tagging (P-13) | 17, 19 | 4 |
| Obligations and controls | Append-only status history (I-04); remediation action (WF-03); occurrence clock (I-03c) | 20, 21, 25 | 5 |
| Evidence and IPE | KMS keys and purpose separation; RFC 3161; real WORM; scheduled checkpoints; conflict screen and history; retention policies; disposition history | 28, 29, 42 | 6 |
| Assurance and packages | Snapshot reproduction stability (I-03); period-selected executive exposure; audit-readiness query; package access revocation; snapshot export | 4, 6, 32, 34 | 7 |
| Background processing | Outbox relay, workers, retries, leases, dead letters, two-phase impact scheduling, publication worker state machine, security-event delivery, operations consoles | 50, 51 | 8 |
| Platform administration | Platform audit history; platform overview (needs specifying); organisation settings (needs specifying); multi-cell residency | 38, 44, 46, 49 | 9 |
| API quality | Committed OpenAPI and `/v1`; idempotency keys; ETag; cursor pagination, filters and sorting; unified error contract; unknown-field rejection; dashboard period parameter and freshness; operational metrics | 4, 53, 54, 55 | 10 |
| Integrations | Connector framework; first connector set (PRD §11.1); regulatory feeds; certification state machine | 30, 52 | 11 |

---

## 15. Dependency-ordered implementation tasks

The twelve tasks below are self-contained assignments.
- **Prerequisite for everything:** Task 0.
- **Can run in parallel after Task 0:** Tasks 3, 4, 9 and 10 (API hygiene).
- **Blocked by architecture approval:** parts of Tasks 1, 2, 5, 6, 7 and 8 (section 16).
- **Deferrable without breaking the core wireframe workflows in development:** Task 11.

### Task 0: Baseline and existing-change stabilisation

| Field | Content |
|---|---|
| Purpose | A trustworthy, green baseline before new work |
| Screens | All, indirectly |
| Specs | QA-REQ-008 (critical gate tests are not quarantined); APP-REQ-006; RUL-REQ-027; SEC-REQ-048 |
| Current | Today's uncommitted Task 3 changes (section 2); 154 SQLite tests with 1 failure; PostgreSQL last verified 29 Oct; nothing ever run in GitHub Actions |
| Missing | (a) A decision on `test_fact_assertion_preserves_non_integrity_evaluation_errors`. If a published version is made non-effective below the application, evaluation binds the previous version without any integrity check of the altered row. (b) Deterministic rule selection in `reevaluate_for_fact` (`engines/applicability.py:467`, `.first()` without `ORDER BY`). (c) A PostgreSQL 17 run of the current tree. (d) The CI gate at 18. (e) A recorded approval status for the 3.5 signal design. |
| Files | `backend/app/engines/applicability.py` (`resolve_rule` `:43`, `reevaluate_for_fact` `:456`), `backend/tests/test_rule_integrity.py`, `.github/workflows/ci.yml:337`, `docs/backend-architecture-audit.md` |
| APIs | None new |
| Database | None |
| Depends on | Nothing |
| Security | Close the fallback residual. Options: verify every candidate version `resolve_rule` considers, or check that the selected version is the newest published one whose release declares the effective period. Choose one with the APP owner. |
| Steps | 1. Reproduce the failure. 2. Decide whether this is legitimate fallback or tampering (published rows are immutable, so a changed `valid_to` means tampering). 3. Fix the code or the test, not the assertion alone. 4. Order the rule selection. 5. SQLite suite. 6. `ci_pg_repro.py` on a disposable cluster. 7. Update the gate. 8. Update the docs. 9. Hand over to the owner for review and commit. |
| Acceptance | SQLite: 0 failures. PostgreSQL 17: 0 failures and 0 skips. Gates match. A written decision on fallback. |
| Tests | Tampering a non-selected version's effective period is refused; re-evaluation picks the same version as evaluation |
| Definition of Done | Green suites on both databases; docs updated; nothing quarantined |
| Priority / complexity | **Critical / Small** |

### Task 1: Database architecture, role model and migration foundation

| Field | Content |
|---|---|
| Purpose | Isolation and immutability enforced by the database, not only the application |
| Screens | Every tenant screen, indirectly |
| Specs | DAT-REQ-003/004/005; ZA-DAT-001 §12 (narrow `SECURITY DEFINER` for system jobs), §13 (roles); SEC-INV-07/08; INV-04; DAT-REQ-009/024/030 |
| Current | Two roles. The owner is a login role holding all objects and is used by the API at start-up. RLS without `FORCE`. `assure_system()` can be switched on by any session. Owner can run `DISABLE TRIGGER`. No-tenant rows readable and writable by every tenant. |
| Missing | The DAT §13 roles (`assure_migrator`, `assure_app_rw`, `assure_app_ro`, `assure_outbox_publisher`, `assure_catalog_loader`, `assure_analytics_ro`, `assure_breakglass_admin`) and non-login table owners, **or** the brief's `assure_owner`/`assure_migrator`/`assure_app`/`assure_job_owner`, once the governing source is decided. Ownership transfer. `FORCE` RLS. System jobs through definer functions, with `assure_system()` removed from policies. The owner credential removed from the API (start-up checks under a read-only verifier role or as a deploy step). Tighter policies for no-tenant outbox and audit rows. Record time on ledger events and IPE evaluations (P-03, derivable backfill only). Semantic restore test (QA-1). |
| Files | `backend/migrations/versions/0005_*.py` (new), `app/pg_bootstrap.py`, `app/db.py`, `app/main.py`, `app/migrate.py`, `app/security.py` (`system_scope`), `.github/scripts/ci_postgres_bootstrap.sql`, `.github/workflows/deploy-environment.yml`, `tests/test_rls.py`, `test_immutability.py`, `test_pg_bootstrap.py` |
| APIs | None |
| Database | Migration `0005`: roles, ownership, `FORCE`, definer functions, policy rewrite, grants. Reversible on disposable databases. |
| Depends on | Task 0. **Decisions:** system-job design (Data Architect + Security); role model (A10, together with A2); which role list governs. |
| Security | Highest. The I-01 probe must fail afterwards. |
| Steps | 1. Get the decisions. 2. Generate a privilege matrix from the reviewed design. 3. Write `0005`. 4. Change the bootstrap and start-up to need no owner credential. 5. Move system jobs to definer functions. 6. Generate RLS tests per table. 7. Rehearse on a copy of development data. 8. Update the runbook. |
| Acceptance | `set_config('app.system','on')` gives the runtime role no cross-tenant rows. `FORCE` on every tenant table. The owner is a non-login role and can't log in. The API starts without `DATABASE_ADMIN_URL`. All privilege matrices match. |
| Tests | Generated per-table RLS read and write tests with canary tenants (QA-2); I-01 negative test; ownership and privilege matrix; `0005` upgrade, downgrade and re-upgrade; restore semantics |
| Definition of Done | PostgreSQL 17 suite green with 0 skips; development database upgraded only after a verified backup and the owner's approval |
| Priority / complexity | **Critical / Large**; partly blocked |

### Task 2: Authentication, session and tenant security

| Field | Content |
|---|---|
| Purpose | Production-grade identity, sessions and authorisation |
| Screens | Sign-in; Users & Roles (row 39); Identity Integrations (40); Privileged Access Log (41); every read screen |
| Specs | IAM-REQ-018/023; SEC-REQ-020; ADR C-08 (BFF cookie session); WF-IAM-01…08; API-REQ-010; P-01, P-12, P-17, I-06, I-08, I-10 |
| Current | Local passwords, HMAC bearer token in `localStorage`, static role map, read access for any tenant member |
| Missing | BFF/cookie session; OIDC/SAML, SCIM, MFA; scoped, time-bound role assignments; organisation user and role management APIs (invite, assign, revoke); per-read authorisation policy; JIT, break-glass and support access with audit; `security.authorization_denied` events; dev-header production guard; authenticated `/watermark`; outbox read restricted to the caller's own visibility class |
| Files | `app/auth.py`, `app/security.py`, `app/accounts.py`, `app/routers/core.py`, new `app/routers/iam.py`, `app/models.py` (role assignment, grants) plus a migration, `frontend/lib/api.ts` |
| APIs | `/api/iam/users` (invite, list, revoke), `/api/iam/role-assignments`, `/api/iam/privileged-access` (request, approve, end), IdP configuration |
| Database | Role-assignment and privileged-grant tables (append-only) |
| Depends on | Task 0; G-05 (session architecture and IdP); G-08 (read policy); Task 1 for database-level grants |
| Security | Core |
| Steps | 1. Decide G-05 and G-08. 2. BFF session. 3. Role-assignment model. 4. User management APIs. 5. Read policy enforcement. 6. JIT and break-glass. 7. Denial events. 8. Environment guards. |
| Acceptance | No token readable by browser JavaScript; MFA enforced per policy; every read checked; privileged actions time-bound and audited |
| Tests | Session fixation and CSRF; revocation; MFA step-up; read-policy matrix; JIT expiry; no-tenant outbox visibility |
| Definition of Done | IAM test suite green on PostgreSQL; WF-IAM screens backed |
| Priority / complexity | **High (Critical for production) / Large**; partly blocked |

### Task 3: Regulatory source and requirement lifecycle

| Field | Content |
|---|---|
| Purpose | Real regulatory input through to governed, released content |
| Screens | Regulatory Inbox (WF-01), Sources & Provisions (WF-DOM-01), Rule Authoring, Content Releases |
| Specs | PRD WF-01 (fields and actions; "Prohibited: publishing unverified extraction"); PRD §11 (rights, fingerprint); PRD §12 Regulatory change impact; INV-09; RUL §§10–17; M-12 |
| Current | Manual source text; regex "extraction" labelled `AI_EXTRACTED`; human verification; rule drafts through signed publication; definitions seed-only |
| Missing | Source ingestion with rights, fingerprint and versioning; a governed extractor via a model gateway, **or** a recorded decision that extraction stays manual (and the label changed to match); WF-01 inbox fields (impacted entity count, missing-fact count, reviewer, severity); review assignment; source version diff; authoring and release APIs for obligation, fact, set and control definitions; regulatory-change-impact query |
| Files | `app/routers/catalog.py`, `app/routers/rules.py`, `app/models.py` plus a migration, `app/engines/rules.py` |
| APIs | `POST /catalog/sources/{id}/versions`, `POST /catalog/inbox/{id}/assign`, `GET /catalog/sources/{id}/diff`, `POST /catalog/obligation-definitions` (and the fact, set and control equivalents, each draft → review → release), `GET /reports/regulatory-change-impact` |
| Database | Source version and fingerprint columns; review assignments; definition drafts |
| Depends on | Task 0; model-gateway decision (M-12) for AI extraction; Task 8 for asynchronous ingestion |
| Security | Content staff isolation stays; extraction output never binding (INV-09) |
| Steps | 1. Decide on the extraction policy. 2. Source versioning and fingerprint. 3. Inbox fields and assignment. 4. Definition authoring through the release process. 5. Change-impact read model. |
| Acceptance | WF-01 required fields and actions served; no unverified extraction publishable; definitions created without seed code |
| Tests | Ingestion idempotency; fingerprint change creates a version; assignment and permission; definition release gates; change-impact correctness |
| Definition of Done | WF-01 backed end to end; tests green |
| Priority / complexity | **High / Large** |

### Task 4: Facts and applicability engine completion

| Field | Content |
|---|---|
| Purpose | Complete WF-02 and APP §10 behaviour |
| Screens | Applicability Review, Entities & Facts, Fact Requests, Applicability Trace, History |
| Specs | APP-REQ-001…020; APP §10 (impact scheduling); TIM-REQ-001/004/005/007; DOC-CON-003 / P-13 |
| Current | Implemented and mostly verified, including integrity checks; re-evaluation runs synchronously |
| Missing | Durable, asynchronous impact scheduling for fact and rule changes (with Task 8); the WF-02 "request legal review" action; tests for challenge; temporal-profile tagging (P-13) |
| Files | `app/engines/applicability.py`, `app/engines/facts.py`, `app/routers/applicability.py` |
| APIs | `POST /applicability/determinations/{lineage}/legal-review` (per WF-02) |
| Database | Temporal profile columns (after the DOC-CON-003 resolution) |
| Depends on | Task 0; Task 8 for scheduling; DOC-CON-003 |
| Security | Integrity checks stay ahead of input freeze |
| Steps | 1. Legal-review action. 2. Challenge tests. 3. Move impact work to jobs once Task 8 exists. 4. Temporal profiles. |
| Acceptance | WF-02 actions served; impact jobs idempotent and observable |
| Tests | Impact on fact change (sync now, async later); legal review; challenge history |
| Definition of Done | Tests green on PostgreSQL |
| Priority / complexity | **High / Medium** |

### Task 5: Obligation and control lifecycle

| Field | Content |
|---|---|
| Purpose | Full, append-only obligation and control history |
| Screens | Obligation Dossiers (WF-03), Controls & Tests, Exceptions & Issues, Assurance Periods |
| Specs | STA-REQ-001…032; DAT-REQ-009; TIM-REQ-007; WRK-REQ-013; WF-03; WF-STA-01…05 |
| Current | Engines implemented; statuses overwritten in place (I-04); occurrence logic reads the wall clock (I-03c) |
| Missing | Append-only status events (exceptions, compensations, issues, occurrences, conflicts, holds, grants, certification, catalogue lifecycle); WF-03 "request remediation"; occurrence evaluation at a pinned time; tests for occurrences and governance transitions |
| Files | `app/routers/assurance.py:140-491`, `app/engines/assurance.py`, `app/models.py` plus a migration |
| APIs | `POST /obligations/{lineage}/remediation-requests` |
| Database | Event tables with `tenant_id`, RLS, append-only trigger, `record_seq`, actor and reason; then revoke `UPDATE` on the status columns |
| Depends on | Task 0; Task 1; **decision: event-table convention (TIM, STA and DAT owners)** |
| Security | No status column writable outside its event writer |
| Steps | 1. Agree the convention. 2. Event tables. 3. Transition writers. 4. Cache columns read-only. 5. Revoke `UPDATE`. |
| Acceptance | A grep gate plus database grants show no direct status writes; history fully reconstructable |
| Tests | Every transition writes exactly one event; illegal transitions refused; history at any record time |
| Definition of Done | PostgreSQL green; I-04 closed |
| Priority / complexity | **High / Large**; blocked on the convention |

### Task 6: Evidence and IPE hardening

| Field | Content |
|---|---|
| Purpose | Evidence integrity fit for audit and production |
| Screens | Evidence Records, Ledger, IPE, Source Runs, Retention & Legal Holds |
| Specs | EVD-REQ-*, IPE-REQ-*, WF-04, WF-EVD-01…06, WF-IPE-01…06, WF-PRI-02/05; P-05, P-06, P-11, P-16, I-05; C-07 (purpose-separated keys) |
| Current | Encrypted vault, chained ledger, manual Merkle checkpoints with a shared key, local "WORM", IPE engine, holds and erasure |
| Missing | KMS/HSM-backed key hierarchy and purpose-separated keys; RFC 3161 timestamping; real WORM (object-lock) storage; scheduled checkpoints and verification (Task 8); evidence-conflict screen hookup and conflict history; retention policy model (M-14); IPE disposition history |
| Files | `app/engines/keys.py`, `app/engines/evidence.py`, `app/engines/ipe.py`, `app/routers/evidence.py`, `app/routers/integration.py`; frontend conflicts screen |
| APIs | `GET/POST /retention-policies`; conflict list in the UI (backend exists) |
| Database | Key references; retention tables; disposition events |
| Depends on | Task 0; G-06 (KMS and key purposes); G-02 (ledger segmentation, D-01); TSA provider; Task 8 for scheduling |
| Security | Key custody, purpose separation, erasure across backups (P-16) |
| Steps | 1. Decide on keys. 2. Key provider abstraction. 3. TSA client. 4. Object-lock storage adapter. 5. Scheduled checkpoints. 6. Retention model. 7. Conflict screen. |
| Acceptance | Checkpoints carry a valid TSA token and a purpose-specific signature; WORM enforced by storage; keys never on local disk in production |
| Tests | Key rotation preserving historical verification; TSA token validation; WORM write-once; erasure with a hold; IPE disposition history |
| Definition of Done | Evidence suite green; offline verifier pins keys by default |
| Priority / complexity | **High / Large**; partly blocked |

### Task 7: Assurance and audit package engine

| Field | Content |
|---|---|
| Purpose | Reproducible period assurance and complete package workflow |
| Screens | Assurance Periods (WF-05), Audit Package Builder (WF-06), Org dashboard Executive exposure and Audit readiness |
| Specs | STA-REQ-019/020/021/032; TIM-REQ-006/015; QA-REQ-013; PRD §12; WF-05; WF-06; decision brief D1–D4 |
| Current | Snapshots with dependency digests; ordering fixed (Stage 3a); packages and verifier implemented and verified |
| Missing | Reproduction stable against later events (I-03 a, b, c), plus the evaluator version policy; period-selected executive exposure; audit-readiness query; package external-access revocation; snapshot export |
| Files | `app/engines/assurance.py:369`, `app/routers/assurance.py:95-120`, `app/routers/dashboard.py`, `app/routers/evidence.py:285-346`, `app/engines/evidence.py:348` |
| APIs | `GET /dashboard?period=…`, `GET /reports/audit-readiness`, `POST /packages/{id}/revoke-access`, `GET /snapshots/{id}/export` |
| Database | Package access grants; possibly a snapshot manifest version (after TIM-ADR-04) |
| Depends on | Tasks 5 and 6; **decisions:** STA-REQ-032 ADR, TIM-ADR-04, G-07 |
| Security | Package access scoping (external auditors, G-08) |
| Steps | 1. Get decisions D1–D4. 2. Cutoff-aware reads. 3. Version bump keeping old versions callable. 4. Period parameter. 5. Readiness and revocation. |
| Acceptance | Snapshot → later change → reproduce gives an identical hash, for every change kind |
| Tests | Reproduction suite; physical row-order rewrite; package revoke; readiness |
| Definition of Done | Reproduction suite green on PostgreSQL |
| Priority / complexity | **High / Large**; blocked on decisions |

### Task 8: Background processing and publication workers

| Field | Content |
|---|---|
| Purpose | Durable asynchronous work: delivery, impact, checkpoints |
| Screens | WF-EVT-03 Outbox monitor, WF-WRK-07 Queue/Worker health; indirectly publication and facts |
| Specs | EVT-REQ-001…019; WRK; ADR C-09 (outbox), C-10 (managed Temporal); M-02, M-03, M-09 |
| Current | Outbox write only; impact synchronous in the request; no worker |
| Missing | Outbox relay (`assure_outbox_publisher`), broker, consumer inbox, dead letters, replay; worker runtime with leases, retries and idempotency; two-phase impact scheduling (commit intent, then process per tenant); publication worker state machine; scheduled checkpoints and verification; `SECURITY_CRITICAL` routing for `security.*` events; monitoring APIs |
| Files | New `app/workers/`, `app/routers/ops.py`, `app/routers/rules.py:~300` (replace the synchronous impact), `app/engines/applicability.py:456` |
| APIs | `GET /ops/outbox`, `GET /ops/workers`, `POST /ops/replay` (platform-scoped) |
| Database | Job, lease and attempt tables; inbox table; publisher role grants (Task 1) |
| Depends on | Task 1 (roles); **G-09 (workflow runtime)**; the 3.5 signal approval |
| Security | Tenant context per job; publisher limited to delivery metadata (DAT §13) |
| Steps | 1. Decide G-09. 2. Job model and lease. 3. Relay. 4. Impact jobs. 5. Publication state machine. 6. Checkpoint scheduler. 7. Consoles. |
| Acceptance | Crash at any point: no lost or duplicated effect; failed jobs reach the dead-letter queue and are visible |
| Tests | Failure injection, duplicate and out-of-order delivery, lease expiry, retry limits (M-09) |
| Definition of Done | Worker suite green on PostgreSQL; impact no longer in the request path |
| Priority / complexity | **High / Large**; blocked on G-09 |

### Task 9: Organisation and Super Admin APIs

| Field | Content |
|---|---|
| Purpose | Complete the Super Admin dashboard within its boundary |
| Screens | Super Admin dashboard, organisation detail, Cells & Residency |
| Specs | WF-08; WF-TEN-01…06; WF-IAM-01 ("no make super admin button") |
| Current | Registry list, detail, create, edit, suspend, appoint admin; tested |
| Missing | Platform administrative audit history (within the boundary); platform overview contents (**not specified**: specify first); organisation settings (**not specified**); multi-cell residency moves (M-15, before a second cell) |
| Files | `app/routers/platform.py`, `app/routers/core.py` |
| APIs | `GET /platform/audit` |
| Database | None, or an index on audit by actor |
| Depends on | Task 0; Task 2 for JIT-based privileged actions |
| Security | No customer data reachable; every action audited |
| Steps | 1. Platform audit query. 2. Specify the overview and settings with the product owner. 3. Implement. |
| Acceptance | Super Admin sees platform actions across organisations and no customer records |
| Tests | Boundary tests extended to the audit query |
| Definition of Done | Tests green |
| Priority / complexity | **Medium / Medium** |

### Task 10: API contract, dashboard data and operational completeness

| Field | Content |
|---|---|
| Purpose | A stable, safe, paginated API contract and dashboard data per PRD §12 |
| Screens | Org dashboard (Overview, Needs Attention, Assurance Position) and every list screen |
| Specs | API-REQ-001/002/004/005/006/012; PRD §12 (Executive exposure, Evidence freshness); I-09; M-16 |
| Current | Runtime OpenAPI; `/api` prefix; mixed error bodies; `limit` on 3 lists; no idempotency or ETag |
| Missing | Committed OpenAPI plus a CI diff gate and `/v1`; Idempotency-Key on every mutation; ETag/`If-Match`; cursor pagination, filters and sorting; unified error contract with a stable code and correlation ID (per API-001); `extra="forbid"` on every request model; a dashboard period parameter and evidence-freshness metrics; structured logs and metrics |
| Files | Every `app/routers/*.py`; `app/main.py` (middleware); `app/platform.py`; frontend `lib/api.ts` |
| APIs | Changes across the board; `/v1` alias |
| Database | `platform_idempotency_record` put to use |
| Depends on | Task 0. Independent of the blocked decisions. |
| Security | Idempotency prevents duplicate mutations; unknown fields rejected |
| Steps | 1. Error contract. 2. Request-model hardening. 3. Idempotency middleware. 4. Pagination. 5. OpenAPI commit and diff gate. 6. Dashboard period and freshness. |
| Acceptance | Replay of the same key returns the same result; a different body is refused; lists page stably; one error schema |
| Tests | Idempotency replay and conflict; pagination stability under inserts; schema-diff gate; error contract |
| Definition of Done | Contract committed; CI gate active |
| Priority / complexity | **Medium / Medium** |

### Task 11: External integrations (connectors)

| Field | Content |
|---|---|
| Purpose | Evidence and facts from real systems (PRD §11) |
| Screens | Connectors, Source Runs, IPE, Evidence |
| Specs | PRD §11 and §11.1 (first connector set: one incumbent GRC/evidence platform, a ServiceNow-class issue source where demanded, an identity source …); FR-CON-001/002; WF-EVD-02; WF-IPE-05; WF-SEC-03 |
| Current | Certification registry only; data posted to the API by hand |
| Missing | Connector framework (workload identity, secret references, egress policy, certification lifecycle I-11); first connectors; regulatory feed ingestion (with Task 3) |
| Files | New `app/connectors/`; `app/engines/ipe.py`; `app/engines/connectors.py` |
| APIs | Installation, run and certification lifecycle endpoints |
| Database | Installation revisions (exist), run state, secret references |
| Depends on | Tasks 2, 6 and 8; G-06 |
| Security | Untrusted input, egress control, secret custody (WF-SEC-03) |
| Steps | 1. Framework. 2. One GRC connector end to end with IPE. 3. Further connectors on demand. |
| Acceptance | A certified connector run produces an IPE-verified source run with provenance |
| Tests | Contract tests per connector; failure and retry; IPE reconciliation |
| Definition of Done | First connector certified in a non-production environment |
| Priority / complexity | **Medium / Large**; deferrable |

### Test matrix by task

| Task | Unit | Integration (PostgreSQL 17) | Security and negative | End-to-end lifecycle |
|---|---|---|---|---|
| 0 | Rule selection order | Full suite, 0 skips | Version fallback refused | Existing seeded lifecycle |
| 1 | — | Migration `0005` up and down; privilege matrix | I-01 probe; owner DDL; canary tenants | Lifecycle under the new roles |
| 2 | Token and session | IdP and MFA flows | CSRF, fixation, read policy, JIT expiry | Sign-in through dashboard |
| 3 | Fingerprint, extraction proposal | Definition release | Unverified extraction not publishable | Source → released rule |
| 4 | Impact selection | Async impact | Integrity before input freeze | Fact → evaluation → attestation |
| 5 | Transition rules | Event writers | Direct status writes refused | Obligation → control → exception |
| 6 | Key derivation, TSA parsing | Checkpoint plus WORM | Key misuse, erasure with hold | Evidence → checkpoint → verify |
| 7 | Roll-up | Reproduction suite | Package scope | Snapshot → package → offline verify |
| 8 | State machine | Relay, inbox, dead letters | Duplicate and crash injection | Publish → impact job → dashboard |
| 9 | — | Audit query | Boundary | Organisation lifecycle |
| 10 | Error contract | Pagination, idempotency | Unknown fields | All lists |
| 11 | Mapping | Connector contract | Egress, secrets | Connector → IPE → assurance |

---

## 16. Critical blockers and architectural decisions

None of these can be decided by engineering or by AI (ADR-REQ-006). All specifications are "Proposed for lock".

| Decision | Blocks | Owners (from the specifications and earlier audit) | Evidence needed |
|---|---|---|---|
| **G-01 runtime language.** ARCH-ADR-003 / C-01 propose Go; the repository is Python (D-04, I-07). | Potentially every task | CTO (T0) | Accept Go and plan a port, or raise a successor ADR keeping Python |
| G-03 approval of ZA-ADR-001 / G1 baseline; DOC-CON-001…003 | Every "accepted" claim | CTO / Architecture Governance | Signed approvals |
| System-job design (I-01, P-02) | Task 1 | Data Architect + Security | Definer-function design; probe plan |
| Role model (A10; brief's role names vs DAT §13) | Task 1 | Data Architect + Security | Approved role and privilege matrix |
| G-05 session architecture and IdP | Task 2 | Security Architecture / IAM Lead | BFF and IdP selection |
| G-08 read-authorisation policy | Task 2 | IAM Lead | Permission per read operation |
| Event-table convention (I-04) | Task 5 | TIM, STA and DAT owners | Convention document |
| G-06 KMS/HSM and key purposes; signature trust K1–K6 (A9b) | Tasks 6 and 7; A9b | CTO + Security; RUL-ADR-06 (CTO + Head of Assurance + GC) | Key catalogue, trust anchor, rotation |
| STA-REQ-032 ADR, TIM-ADR-04, G-07 (D1–D4) | Task 7 | STA/TIM owners; CPO/CTO | Decision record |
| 3.5 security-signal persistence | Task 0 (status), Task 8 (delivery) | EVT and SEC owners | Approve or replace the post-rollback outbox design |
| G-09 workflow runtime | Task 8 | Backend Platform / Workflow Lead | Runtime selection |
| G-10 deployment target | Staging and production; backup step | CEO / CPO / CTO | Target, region, backup and PITR |
| Model gateway (M-12) / extraction policy | Task 3 (AI extraction) | CTO + Security | Gateway design, or a manual-only decision |
| Platform overview and organisation settings | Task 9 | Product owner | Specification (not specified in available material) |

---

## 17. Recommended implementation sequence

1. **Now:** Task 0. In parallel, request decisions G-01 (first, since it can change everything), the system-job design, the role model, G-05, G-08, G-09, G-06 and the 3.5 approval.
2. **Independent, after Task 0:** Task 10 (API contract and hygiene), Task 3 (regulatory lifecycle, manual extraction unless M-12 is decided), Task 4 (actions and tests), Task 9 (platform audit).
3. **After the database decisions:** Task 1, then Task 2 (needs G-05 and G-08).
4. **After Task 1 and G-09:** Task 8, then move Task 4's impact scheduling and Task 6's checkpoints onto workers.
5. **After the convention and G-06:** Tasks 5 and 6.
6. **After D1–D4 and Tasks 5 and 6:** Task 7.
7. **Last, driven by design partners:** Task 11.

---

## 18. Final backend readiness assessment

| Target | Ready? | Reason |
|---|---|---|
| Frontend integration (existing screens, development) | **Yes, with caveats** | Every frontend call maps to an existing route and method. Placeholders remain for Users & Roles, Identity, Retention and the Privileged Access Log. One backend feature has no screen (evidence conflicts). One test fails in today's uncommitted work. |
| Staging | **No** | No deployment target or backup step (G-10). PostgreSQL behaviour of the current tree is unverified, and GitHub Actions has never run on the uncommitted work. Critical database finding I-01 is open. |
| Production | **No** | Everything above, plus: no `FORCE` RLS; owner credential in the API; A10; no SSO, MFA or SCIM; token in `localStorage`; local keys with no KMS; no TSA or WORM; no workers or outbox delivery; no real integrations; append-only history incomplete (I-04); snapshot reproduction unstable (I-03); governance decisions open (G-01 to G-10; no approved ADR baseline). |

**Bottom line.** The backend is a capable, well-guarded development implementation of the obligation-to-proof chain.
Its deterministic engines, evidence integrity, packaging and Super Admin boundary are real and largely tested at
application level. It is **not** yet a production system. The gating work is database-enforced isolation (Task 1),
identity (Task 2) and asynchronous processing (Task 8), and each of those waits on named architecture decisions. Today's
uncommitted work must be stabilised first (Task 0).
