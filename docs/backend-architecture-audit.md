# Zoiko Assure — Backend Architecture Audit against ZA-ARCH-001 v3.0

> **Status:** audit only. Writing this report changed no application code, schema, test or configuration.
> **Date:** 27 October 2026.
> **Baseline:** `HEAD af1d508` plus the uncommitted working tree described in [§3](#3-working-tree-at-audit-time).
> **Authority:** the controlled ZA-* specifications. This report is non-normative. Where it differs from a ZA-* document, the ZA-* document wins.
> **Companion:** the staged plan derived from these findings is [backend-architecture-roadmap.md](backend-architecture-roadmap.md).
> **Implementation status (added after the audit):** Stage 1 (M-01) and Stage 2 work package A1 (I-02) are implemented in the working tree, not yet committed; see [§12](#12-implementation-status). The findings describe the audit baseline. Their references to `backend/db/postgres_rls.sql` point at that baseline: the file now lives, unchanged, in migration `0002`.

## Contents

1. [Verdict](#1-verdict)
2. [Reference documents, versions and conflicts](#2-reference-documents-versions-and-conflicts)
3. [Working tree at audit time](#3-working-tree-at-audit-time)
4. [Backend structure map](#4-backend-structure-map)
5. [Critical path trace](#5-critical-path-trace)
6. [Review of the P0 fixes](#6-review-of-the-p0-fixes)
7. [Schema, migrations, roles and RLS](#7-schema-migrations-roles-and-rls)
8. [Findings](#8-findings)
9. [Requirement → code → test mapping](#9-requirement--code--test-mapping)
10. [Unresolved governance decisions](#10-unresolved-governance-decisions)
11. [Method, commands and evidence](#11-method-commands-and-evidence)
12. [Implementation status](#12-implementation-status)

---

## 1. Verdict

**The backend does not yet meet ZA-ARCH-001 v3.0 for production.**

The domain engines are largely sound:
- deterministic applicability
- bitemporal facts and determinations
- versioned roll-up with reproduce support
- an evidence ledger with Merkle proofs
- IPE bound to its policy
- human-only accountable actions

The architecture around them is not. Each of the following blocks a production release on its own:

| # | Blocking gap | Dimension | Finding |
|---|---|---|---|
| 1 | Any runtime-role SQL can switch off tenant isolation by setting `app.system = on` | Tenant isolation | [I-01](#i-01) |
| 2 | Immutable results (evaluations, snapshots, IPE evaluations, control evaluations) are writable and deletable by the runtime role | Data integrity | [I-02](#i-02) |
| 3 | Reproducing a snapshot reads today's evidence lifecycle, today's conflicts and the wall clock, and hashes rows in physical order | Historical correctness | [I-03](#i-03) |
| 4 | Governance records named `*_revision` are overwritten in place | Historical correctness | [I-04](#i-04) |
| 5 | No versioned migration system; the schema is created at API start-up; deploy is blocked | Production | [M-01](#m-01) |
| 6 | One signing key signs checkpoints, packages, content releases and AI policy bundles | Data integrity / security | [I-05](#i-05) |
| 7 | Reads have no authorization beyond "signed in to this tenant" | Security | [P-01](#p-01) |
| 8 | Browser sessions are bearer tokens in `localStorage` | Security | [I-06](#i-06) |

**Findings by category.** These cover the 72 audit items in §8, not all 913 controlled IDs; see [§9](#9-requirement--code--test-mapping) for scope.

| Category | Count |
|---|---|
| Confirmed implemented | 18 |
| Partially implemented | 17 |
| Missing | 16 |
| Incorrect or conflicting | 11 |
| Unverified (evidence unavailable) | 10 |

**Test evidence.** Passing tests do not establish compliance (QA-REQ-001); they establish only what they test. On this working tree the backend suite passes:
- **SQLite:** 100 passed, 9 skipped.
- **PostgreSQL 17, disposable cluster with the CI gates:** 109 passed, 0 failed, RLS gate 5/5, bootstrap gate 4/4.

---

## 2. Reference documents, versions and conflicts

### 2.1 Documents read

The text was read from the five volume PDFs, and every requirement table was read row by row from the source DOCX files (see [§11](#11-method-commands-and-evidence)). Results:
- **913** unique controlled IDs were found.
- **902** have a defining row in their owning document.
- The other 11 are ARCH-001's bare `ADR-001…011`, which ZA-ADR-001 re-keys as `ARCH-ADR-001…011`.

| Document | Version | Stated status | Owner |
|---|---|---|---|
| ZA-ARCH-001 Back-End Architecture | 3.0 (6 Oct 2026) | Proposed for architecture lock — approval is not inferred | CTO / Architecture Governance |
| ZA-PRD-001 Master Product Requirements & Release Scope | 1.0 | Proposed for lock | CPO / Product Lead |
| ZA-DOM-001 Canonical Domain Model & Regulatory Ontology | 1.0 | Proposed for engineering lock | CTO / Domain Architecture Lead |
| ZA-GLS-001 Controlled Terminology & Status Dictionary | 1.0 | Proposed for semantic lock | CPO / CTO |
| ZA-ADR-001 Architecture Decision Record Register | 1.0 | Proposed for architecture-governance lock | CTO / Architecture Governance |
| ZA-DAT-001 Logical & Physical Data Model | 1.0 | Proposed for G1 database-contract lock | Data Architect |
| ZA-TEN-001 Tenancy, Legal Entity, Cell & Federation | 1.0 | Proposed for G1 tenancy and residency lock | Platform Architecture |
| ZA-IAM-001 Identity, Authorisation & Privileged Access | 1.0 | Proposed for G1 identity-and-access lock | Security Architecture / IAM Lead |
| ZA-SEC-001 Security Architecture & Threat Model | 1.0 | Proposed for G1 security-architecture lock | Global Head of Cybersecurity |
| ZA-APP-001 Applicability Engine | 1.0 | Proposed for G1 applicability-engine lock | Backend Applicability Lead |
| ZA-RUL-001 Rule Language & Decision Tables | 1.0 | Proposed for G1 rule-language lock | Regulatory Content Engineering Lead |
| ZA-TIM-001 Bitemporal Data & Historical Reconstruction | 1.0 | Proposed for G1 temporal-model lock | Data Architect |
| ZA-STA-001 Assurance State Machines & Roll-Up | 1.0 | Proposed for G1 assurance-semantics lock | Head of Assurance |
| ZA-EVD-001 Evidence Integrity & Chain of Custody | 1.0 | Proposed for G1 evidence-trust lock | Evidence Platform Lead |
| ZA-IPE-001 Evidence Completeness & Accuracy | 1.0 | Proposed for G1 IPE-contract lock | Evidence / Integrations Lead |
| ZA-PRI-001 Privacy, Retention, Deletion & Legal Hold | 1.0 | Proposed for G1 privacy-architecture lock | Global Privacy Lead |
| ZA-API-001 External & Internal API Contracts | 1.0 | Proposed for G1 API-contract lock | API / Platform Engineering Lead |
| ZA-EVT-001 Domain Event Catalogue & Delivery | 1.0 | Proposed for G1 event-contract lock | Platform / Integration Architecture |
| ZA-WRK-001 Durable Workflow / ZOL | 1.0 | Proposed for G1 workflow-semantics lock | Backend Platform / Workflow Lead |
| ZA-QA-001 Master Verification & Test Strategy | 1.0 | Proposed for master verification lock | Global QA Lead |
| ZA-TRC-001 Requirements Traceability Matrix | 1.0 | Proposed for traceability lock | Product Engineering / QA |
| ZA-DOC-001 Master Engineering Document & Evidence Register | 1.0 | Proposed for master engineering-control lock | CTO / Engineering Governance / QA |

**No document is approved.**
- ZA-DOC-001 §02: "approval is not inferred from the fact that the file exists".
- ZA-ADR-001: its 127 migrated decisions become `ACCEPTED*` only when ZA-ADR-001 itself is approved (ADR-REQ-039).
- So every ADR baseline cited below is a **proposed** baseline.

**Byte identity is unverified.**
- ZA-DOC-001 §03 fingerprints 21 upstream DOCX artifacts.
- **None of the 21 local DOCX files matches its registered SHA-256.** Six also have different file names (for example, `Zoiko_Assure_Backend_Architecture.docx` against the registered `…_Backend_Architecture_v3.0.docx`).
- The ZA-ARCH-001 PDF supplied with this task matches the copy in Volume 01: all distinctive passages checked are present.
- Requirement statements in this report therefore come from the local copies. Their identity with the registered baseline is listed as [U-02](#u-02).

### 2.2 Registered conflicts (ZA-DOC-001 §07) and the code

| Conflict | Canonical resolution | Code today |
|---|---|---|
| DOC-CON-001: `UNDETERMINED` vs `UNCERTAIN` (ARCH, PRD, DOM, TRC) | `UNCERTAIN` per ZA-GLS-001 / ZA-APP-001; no production API or schema may emit `UNDETERMINED` | **Conforms.** `UNDETERMINED` appears nowhere in `backend/app` or the frontend. |
| DOC-CON-002: `PROVEN_WITH_EXCEPTION` and compensation/exception conflation (ARCH §8, PRD, DOM, TRC) | Only `PROVEN / NOT_PROVEN / FAILING / REQUIRES_REVIEW`; exceptions never turn a failure green | **Conforms.** No `PROVEN_WITH_EXCEPTION`. Exceptions are reported beside state (`test_exception_never_changes_state_and_manual_green_impossible`). |
| DOC-CON-003: DOM "valid_period is UTC" vs TIM temporal profiles | ZA-TIM-001 controls: `LEGAL_DATE_RANGE` uses calendar dates, `OPERATIONAL_INSTANT_RANGE` uses UTC instants | **Partial.** Fact revisions declare `OPERATIONAL_INSTANT_RANGE`. Legal-effective dates such as rule validity are `date` columns, but the two profiles are not physically tagged everywhere (DAT-REQ-008). |

The source documents remain unrevised. ARCH 3.1, PRD 1.1, DOM 1.1 and TRC 1.1 are required before the G1 baseline (ZA-DOC-001 §07). The code follows the canonical resolutions; the documents still need revision.

### 2.3 Divergences found during this audit (not in the DOC-001 register)

None of these is resolved here. Each is recorded for the document owners.

| ID | Documents | Divergence | Effect on code |
|---|---|---|---|
| D-01 | ZA-ARCH-001 §9.2, §1.1 vs ZA-EVD-001 EVD-REQ-003/007 | ARCH requires segmented tenant ledgers with deterministic rollover, each segment anchored. EVD requires "one logically ordered evidence ledger" per tenant and checkpoints of "changed tenant chain heads". The two can be reconciled (sequential segments of one logical chain), but neither document defines the segment model. | The code has one unsegmented chain per tenant: it conforms to EVD and partially to ARCH. Segmentation is not scheduled until the owners settle it. |
| D-02 | ZA-ARCH-001 §8.1 vs ZA-STA-001 / GLS-001 | ARCH lists control states `NO_EVIDENCE, STALE_EVIDENCE, CONFLICT, EXCEPTED, NOT_APPLICABLE` and period states including `UNDETERMINED`, `NOT_APPLICABLE`. STA uses a different control vocabulary and four period states. | The code follows STA (`engines/assurance.py:28-30`). This is part of the DOC-CON-002 family, but the control-state list is not named in the register. |
| D-03 | ZA-ARCH-001 §6.3 vs GLS-REQ-003, APP-REQ-008/009, DOM-INV-04 | ARCH: "APPLIES creates a proposed obligation instance". The detailed specs separate the evaluation outcome from the determination workflow, and adoption from APPLIES. | The code creates a PROPOSED determination; the obligation instance appears on attested adoption. This matches the detailed specs; the ARCH wording should be aligned in ARCH 3.1. |
| D-04 | ZA-ARCH-001 §17 + ARCH-ADR-003 + ZA-ADR-001 C-01 vs repository | Go is the proposed authoritative Phase-1 runtime; Python is restricted to non-authoritative AI/content workers. | **The whole authoritative backend is Python 3.12 / FastAPI.** This is an implementation deviation from a proposed T0 ADR, not a document conflict. ADR-REQ-022 requires stopping for ADR review once the ADR is accepted. See [G-01](#10-unresolved-governance-decisions). |

---

## 3. Working tree at audit time

Nothing was staged, there are no stashes, and HEAD is `af1d508`. All changes below are local and uncommitted, and **were preserved**. The audit created only this file and the roadmap.

| Group | Files | Origin |
|---|---|---|
| P0 security and integrity: authorization guard, IPE binding, connector registry | `backend/app/security.py`, `main.py`, `engines/{assurance,ipe}.py`, new `engines/connectors.py`, routers `ai, applicability, assurance, catalog, core, evidence, integration, platform, rules`, new `tests/test_authorization.py`, `frontend/lib/roles.ts`, `frontend/app/connectors/page.tsx`, `frontend/app/ipe/[id]/page.tsx` | Earlier P0 remediation task |
| Header simplification | `frontend/components/Shell.tsx` (header part) | Earlier UI task |
| Sidebar loading states | `frontend/components/{Shell,ui,RequirementViews}.tsx`, `frontend/lib/api.ts`, 22 page files | Earlier performance tasks |
| Backend query batching and `/dashboard/attention` | `backend/app/engines/{evidence,applicability}.py`, `routers/{dashboard,evidence,applicability,assurance}.py`, `tests/test_api.py` | Earlier performance task |

Several files hold changes from more than one group. Section 6 reviews the P0 group independently.

---

## 4. Backend structure map

| Layer | Location | Facts (verified) |
|---|---|---|
| Entry point | [`backend/app/main.py`](../backend/app/main.py) | FastAPI app. The start-up lifespan checks the database, runs bootstrap, optionally seeds demo data and bootstraps accounts. It refuses to start if any mutating route is unguarded (`main.py:54`). |
| Configuration and roles | [`backend/app/db.py`](../backend/app/db.py) | PostgreSQL only at runtime. SQLite is allowed only with `ZA_ALLOW_SQLITE=1` for isolated tests. Two engines: runtime `DATABASE_URL` (`assure_app`) and owner `DATABASE_ADMIN_URL`. Every transaction is stamped with `app.tenant_id` and `app.system` (`db.py:128-139`). |
| Schema bootstrap | [`backend/app/pg_bootstrap.py`](../backend/app/pg_bootstrap.py), [`backend/db/postgres_rls.sql`](../backend/db/postgres_rls.sql) | At start-up, with the owner role: provisions and verifies the least-privilege runtime role, runs `Base.metadata.create_all` (`pg_bootstrap.py:89`), then the RLS SQL. |
| Authentication | [`backend/app/auth.py`](../backend/app/auth.py) | Local passwords (scrypt), HMAC-signed bearer token backed by a revocable `auth_session` row, 8 h expiry (`auth.py:34, 102-131`). |
| Authorization | [`backend/app/security.py`](../backend/app/security.py) | Static role → permission map (`:27`), `Ctx.check` (`:95`), the `authorize()` route guard (`:148`), `PUBLIC_MUTATIONS` (`:168`), the start-up check `unguarded_mutations` (`:180`), the RLS bypass `system_scope` (`:194`). |
| Routers (11 + auth) | `backend/app/routers/*.py` | **128 routes:** 62 GET, 63 POST, 2 PUT, 1 PATCH. 62 are permission-guarded, 56 need sign-in only (all reads), 10 need no sign-in. |
| Domain engines | `backend/app/engines/` | `rules` (548 lines), `assurance` (588), `evidence` (446), `applicability` (429), `ipe` (351), `facts` (222), `keys` (101), `connectors` (50). Engines never call `commit()`; routers own the transaction. |
| Platform primitives | [`backend/app/platform.py`](../backend/app/platform.py) | Canonical JSON v1, the cell-local `record_seq` (`:57`), transactional outbox insert `emit` (`:76`), `audit` (`:89`). |
| Data model | [`backend/app/models.py`](../backend/app/models.py) | **63 tables.** 42 carry `tenant_id`, 41 of them under RLS. `org_actor` is deliberately exempt for sign-in. |
| Background work | — | **None.** No worker process, scheduler, outbox relay or workflow runtime. Checkpoints, impact re-evaluation and verification run only when a request asks. |
| Offline verifier | [`backend/verifier/verify_package.py`](../backend/verifier/verify_package.py) | Standalone. Supports `--trusted-key` pinning. Hardened against hostile archives (21 tests). |
| Tests | `backend/tests/` | **109 tests**, 9 of them PostgreSQL-only. |
| CI/CD | `.github/workflows/{ci,deploy,deploy-environment}.yml` | CI has frontend, backend-unit, backend-postgres (RLS and bootstrap gates) and security hygiene jobs. Deploy fails closed: "BLOCKED — VERSIONED MIGRATION SYSTEM REQUIRED" (`deploy-environment.yml:120-131`), and no hosting target exists. |

---

## 5. Critical path trace

Trace: authentication → authorization → domain operation → transaction → outbox → evaluation → snapshot → package. **✔** = matches the controlling requirement; **◐** = partial; **✘** = gap.

| Step | Where | Controlling requirements | Observed |
|---|---|---|---|
| 1. Authenticate | `auth.py:109` `resolve_token`; `security.py:125` `get_ctx` | IAM-REQ-001/005/018, SEC-REQ-020 | ◐ The HMAC token is verified, then the session row is checked for revocation and expiry. Local passwords only. The browser keeps the token in `localStorage` (`frontend/lib/api.ts:18-24`). The dev header `X-Actor-Id` is honoured whenever `ZA_DEV_IDENTITY_HEADER=1`, with no environment guard (`security.py:122`). |
| 2. Scope tenant | `security.py:75` `Ctx.tenant`; `db.py:142` `set_tenant_scope` | DAT-REQ-002/005, IAM-INV-03 | ✔ Tenant staff are scoped. Staff with no tenant get 403 (INV-14). The scope is transaction-local (`set_config(…, true)`). |
| 3. Authorize | `security.py:95` `Ctx.check`, `:148` `authorize` | IAM-REQ-007, IAM-INV-03, SEC-REQ-022 | ◐ Mutations are deny-by-default with route and service checks. **Reads are not authorized beyond step 2.** |
| 4. Domain operation | `engines/*` | Module ownership (ARCH §4, §22.3) | ✔ Core records (evaluations, determinations, snapshots, evidence, IPE) are created only by their engines. ◐ Routers write governance rows (exceptions, compensations, holds, issues, grants) directly and mutate them in place ([I-04](#i-04)). |
| 5. Transaction | routers call `ctx.db.commit()`; engines never do | DAT-REQ-014, TIM-REQ-020 | ✔ One commit per request. Evidence is acknowledged only after manifest, ledger and outbox commit (EVD-REQ-001). |
| 6. Outbox | `platform.py:76` `emit` | EVT-REQ-001/002/003/005/010/012, ARCH §16.2 | ◐ Written in the same transaction (46 call sites). ✘ Never delivered: `published_at` is never set and there is no relay, queue, inbox or dead-letter queue. The envelope lacks valid time, aggregate version and causation ID. |
| 7. Evaluate applicability | `engines/applicability.py:59` | APP-REQ-016, TIM-REQ-012, DOM-INV-04 | ✔ The rule version and AST hash, effective time, record cutoff, evaluator build and frozen input bundle are stored. Identical inputs give the same record (`:113`). The outbox event is in the same transaction. |
| 8. Assurance snapshot | `engines/assurance.py:355` `compute_snapshot`; `routers/assurance.py:121` reproduce | STA-REQ-019/021, TIM-REQ-006/013 | ◐ Stores roll-up version, expression, cutoff, segments, dependency manifest, digest and hash. ✘ Inputs are read as of *now*: evidence lifecycle (`:67`), conflicts (`:75`), the clock (`:432`). The dependency digest depends on row order (`:112`, `:161`). See [I-03](#i-03). |
| 9. Evidence package | `engines/evidence.py:348` `build_package` | EVD-REQ-026/027/029, SEC-REQ-026 | ◐ Manifests, ledger proof, Merkle inclusion proof, checkpoint signature, snapshots and IPE proof. ✘ Signed with the shared cell key (`:418`). The RFC 3161 token is absent: the checkpoint reports `TSA_PENDING` (`:319`). |

---

## 6. Review of the P0 fixes

The three fixes were re-read independently. Their tests pass (`tests/test_authorization.py`, 16 test functions), but each verdict below rests on reading the code.

**Authorization guard (deny-by-default for mutations).**
- *Correct:*
  - Every POST, PUT, PATCH and DELETE route carries `authorize(...)` or is one of 4 justified `PUBLIC_MUTATIONS`.
  - The app refuses to start otherwise (`main.py:54`).
  - Service functions that must not be bypassed also call `ctx.check` (IPE re-evaluation, connector registry).
  - Agents are refused outright until grant binding exists (`security.py:98-104`). That is fail-closed and conforms to INV-01.
- *Residual:*
  1. The start-up check proves a guard **exists**, not that it names the right permission. Correctness rests on `test_route_matrix_refuses_everything_not_explicitly_allowed`.
  2. **The 56 reads have no permission check.** For example, `EXTERNAL_AUDITOR` and `SERVICE` can read every tenant record. IAM-REQ-007 / IAM-INV-03 make *every* authorisation decision deny-by-default; IAM-REQ-040 scopes external engagements.
  3. Roles are a JSON list on `org_actor` mapped through a static dictionary. There are no scoped or time-bound role assignments (ARCH §22.1 `role_assignment`, `delegated_authority`).
- **Verdict:** partially implemented. The fix is sound for what it covers.

**IPE binding.**
- *Correct:*
  - `ipe.bound_evaluation` (`engines/ipe.py:29`) selects the earliest evaluation under the run's own policy version.
  - Every decision path reads it: the assurance roll-up (`engines/assurance.py:51`), the evidence views and the integration views.
  - Re-evaluation under another policy returns 409 `IPE_POLICY_NOT_BOUND` (`ipe.py:326-330`) and needs a human with `reevaluate_ipe`.
  - The only newest-first query is a history listing (`routers/integration.py:112`).
- *Residual:* immutability is enforced in application code only. The runtime role can `UPDATE`/`DELETE` `evidence_ipe_evaluation` and update `evidence_source_run.ipe_policy_version_id` (IPE-REQ-025, IPE-INV-11; see [I-02](#i-02)).
- **Verdict:** correct at application level; database enforcement missing.

**Connector certification registry.**
- *Correct:*
  - There is a single write path (`engines/connectors.py:24`) requiring a human platform principal with `certify_connectors`.
  - The certification-matrix gate applies for `CERTIFIED`.
  - A reason is required, and the change is audited and emitted.
- *Residual:*
  1. The registry row is overwritten in place; history exists only in audit and outbox rows.
  2. No transition rules: any state can change to any other except that `CERTIFIED` needs the matrix. ARCH §10 defines PLANNED → BETA → CERTIFIED → SUSPENDED → RETIRED.
  3. The runtime role can update the global table directly.
  4. Privileged platform actions have no MFA, step-up or JIT (IAM-REQ-006/020).
- **Verdict:** partially implemented. Access control is correct; versioning and state rules are missing.

---

## 7. Schema, migrations, roles and RLS

**Verified facts:**
- **Migrations.** None exist. `alembic` is listed in `backend/requirements.txt`, but there is no `alembic.ini`, environment or versions directory. The schema comes from `Base.metadata.create_all` plus `db/postgres_rls.sql`, both applied **at every API start-up** with the owner role (`main.py:27`, `pg_bootstrap.py:82-91`). `create_all` never alters existing tables, so a model change does not reach an existing database. The deploy pipeline deliberately stops before any schema change.
- **Runtime role.** Start-up verifies `assure_app` holds no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS, owns no database or relation, and belongs to no role. It fails closed otherwise (`pg_bootstrap.py:33-54`). This conforms to DAT-REQ-004 for role attributes and SEC-REQ-023.
- **Table grants.** `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES` to the runtime role (`postgres_rls.sql:285`), including immutable result tables (DAT-REQ-011).
- **RLS coverage.** Every tenant table except `org_actor` has `ENABLE ROW LEVEL SECURITY` and a `tenant_isolation` policy (41/41). **None uses `FORCE`** (DAT-REQ-003); the file says so deliberately (`postgres_rls.sql:9`).
- **The RLS bypass is self-service.** Policies read `… OR assure_system()`, and `assure_system()` reads the session variable `app.system` (`postgres_rls.sql:14-15, 22`). The runtime role can set this variable itself with `set_config`, so the bypass is not a privilege boundary.
- **Append-only triggers.** Only `evidence_ledger_event` and `platform_audit_event` (`postgres_rls.sql:273-277`).

**Temporal columns** (from a model scan; see §11):

| Coverage | Tables |
|---|---|
| Full valid time plus record time (`valid_from/to`, `record_from_seq/record_to_seq`) | `applicability_determination_revision`, `applicability_fact_assertion_revision`, `assurance_obligation_instance_revision` |
| `record_seq` only, or valid time plus `record_seq` without record-time closure | test runs, control implementations, policies, exceptions, compensations, occurrences, binding, source runs, attestations, catalogue requirement and rule versions |
| No record-time column | `evidence_ledger_event` (has `recorded_at`, no `record_seq`), `evidence_ipe_evaluation`, `evidence_legal_hold_revision`, `governance_risk_acceptance_revision`, `integration_connector_installation_revision`, `assurance_snapshot` (`created_at` plus `record_cutoff_seq`) |

---

## 8. Findings

Severity ranks the worst effect in any of five dimensions:
- **S**: security
- **D**: data integrity
- **H**: historical correctness
- **T**: tenant isolation
- **P**: production risk

### 8.1 Incorrect or conflicting

| ID | Finding | Dimension, severity | Spec | Evidence |
|---|---|---|---|---|
| <a id="i-01"></a>I-01 | RLS bypass is a session variable the runtime role can set; any SQL path running as `assure_app` can read and write every tenant's rows | T, S — **Critical** | DAT-REQ-004, SEC-REQ-008, INV-04 | `postgres_rls.sql:14-15, 22…`; `db.py:128`; `security.py:194` |
| <a id="i-02"></a>I-02 | Immutable results are mutable by the runtime role: `UPDATE`/`DELETE` on evaluations, control evaluations, snapshots, IPE evaluations, packages and attestations. **Stage 2 A1 implemented, not yet committed; see [§12](#12-implementation-status)** | D, H — **High** | DAT-REQ-011, STA-REQ-019, IPE-REQ-025, IPE-INV-11, EVD-REQ-026 | `postgres_rls.sql:285`; triggers only at `:273-277` |
| <a id="i-03"></a>I-03 | Snapshot reproduction is not stable against later events: (a) evidence lifecycle read without cutoff; (b) conflicts read by current status; (c) wall clock in occurrence logic; (d) dependency digest hashes run IDs in unspecified SQL order. **(d) partly fixed by Stage 3a; see [§12](#12-implementation-status)** | H — **High** | TIM-REQ-006, STA-REQ-019/021, QA-REQ-013 | `engines/assurance.py:67`, `:75-76`, `:432`, `:112-115`, `:161` |
| <a id="i-04"></a>I-04 | `*_revision` and governance rows overwritten in place: exception state, compensation status, issue state, legal-hold release, evidence-conflict resolution, authority-grant revocation, catalogue lifecycle, connector certification | H, D — **High** | DAT-REQ-009, TIM-REQ-007, WRK-REQ-013, API-REQ-007 | `routers/assurance.py:362, 424, 469`; `routers/evidence.py:181, 235`; `routers/ai.py:134`; `routers/catalog.py:75-172`; `engines/connectors.py:40` |
| <a id="i-05"></a>I-05 | One Ed25519 key signs checkpoints, evidence packages, content releases and AI policy bundles. The key is unversioned (its ID derives from the public key) and stored as a plaintext PEM file | D, S — **High** | EVD-REQ-012, SEC-REQ-026/049, ADR C-07 | `engines/keys.py:6, 71-91`; callers `engines/evidence.py:317, 418`, `routers/rules.py:278`, `routers/ai.py:207` |
| <a id="i-06"></a>I-06 | Browser session is a bearer token in `localStorage` | S — **High** | IAM-REQ-018, SEC-REQ-020, ADR C-08 (IAM-ADR-03 / SEC-ADR-07) | `frontend/lib/api.ts:18-24`; `auth.py:206` |
| I-07 | The authoritative runtime is Python; the proposed ARCH-ADR-003 / C-01 baseline is Go | P — **High** (governance) | ARCH §17, ARCH-ADR-003, ADR-REQ-022 | whole `backend/` |
| I-08 | The dev identity header can be enabled in any environment; no start-up guard | S — Medium | IAM-REQ-001, SEC-REQ-024 | `security.py:122, 131` |
| I-09 | Request schemas accept unknown fields: 7 of 43 request models set `extra="forbid"` | S — Medium | API-REQ-014, SEC-REQ-022 | e.g. `routers/applicability.py:67` (forbid) vs most models (default ignore) |
| I-10 | `GET /api/watermark` is unauthenticated and exposes the cell record-sequence counter (an activity-volume signal) | S — Low | SEC-REQ-001 | `routers/core.py:48` |
| I-11 | The connector registry has no transition state machine, and its certification states differ from ARCH §10 | D — Low | ARCH §10, INV-11 | `engines/connectors.py:24-50` |

### 8.2 Partially implemented

| ID | Finding | Severity | Spec | Evidence |
|---|---|---|---|---|
| <a id="p-01"></a>P-01 | Authorization: mutations deny-by-default; 56 reads authorized only by sign-in and tenant scope; static roles, no scoped role assignments, no engagement scoping | **High** | IAM-REQ-007, IAM-INV-03, IAM-REQ-040, SEC-REQ-022 | §6; route scan in §11 |
| P-02 | Tenant isolation: RLS on 41/41 tenant tables but no `FORCE`. The cross-tenant isolation tests are specific cases, not the coverage QA-REQ-019 requires | High | DAT-REQ-003, DAT-REQ-028, QA-REQ-019, INV-04 | `tests/test_rls.py` (5), `tests/test_authorization.py::test_other_organisations_cannot_touch_or_see_these_records` |
| P-03 | Bitemporal coverage: full valid and record time on 3 table families only | High | INV-03, TIM-REQ-001, DAT-REQ-008 | §7 table |
| P-04 | Transactional outbox: atomic write ✔; delivery, inbox, DLQ, envelope fields ✘ | Medium | EVT-REQ-001/003/005/010/012, ADR C-09 | `platform.py:76`; `models.py:44-60`; no reader except `routers/core.py:55`, `routers/dashboard.py:155` |
| P-05 | Checkpointing on demand only; RFC 3161 not integrated (honestly `TSA_PENDING`); WORM is a read-only local file; no hourly verification | High | EVD-REQ-007/008/009/025/037 | `engines/evidence.py:300-331` |
| P-06 | Key hierarchy: per-evidence DEK and tenant KEK and EFK by HKDF from a local master file; no KMS/HSM or rotation | High (pre-production) | EVD-REQ-011/012, SEC-REQ-007/044 | `engines/keys.py:29-67` |
| P-07 | Idempotency: evidence capture key and evaluation key only; `platform_idempotency_record` is never used | Medium | INV-10, API-REQ-004/005, QA-REQ-028 | `models.py:64`; usage scan in §11 |
| P-08 | API contract: OpenAPI generated at runtime, not version-controlled; no `/v1` major version in URIs | Medium | API-REQ-001/002/031 | routers mounted at `/api` (`main.py:51`) |
| P-09 | Offline verifier: signature, digests, ledger and Merkle checked; key pinning optional; no RFC 3161 token verification | Medium | EVD-REQ-029 | `verify_package.py:197-200` |
| P-10 | Connector registry (P0): correct access control; no versioned history | Medium | INV-11, DAT-REQ-009 | §6 |
| P-11 | INV-05: manifest and ledger event committed before acknowledgement ✔; ledger segmentation per ARCH ✘ (see D-01) | Medium | INV-05, EVD-REQ-001/003/005 | `engines/evidence.py:44-121` |
| P-12 | Authentication: scrypt, lockout, server-side revocation ✔; no OIDC, SAML, SCIM or MFA | High (production) | IAM-REQ-005/006/016 | `auth.py` |
| P-13 | DOC-CON-003: temporal profiles not physically tagged on every valid range | Medium | DAT-REQ-008, TIM-REQ-003 | `models.py` (fact revisions annotated; others not) |
| P-14 | Snapshot reproducibility support exists (`/snapshots/{id}/reproduce`), but the result is not reliable (I-03) | High | STA-REQ-020/021 | `routers/assurance.py:120-131` |
| P-15 | Content release requires `VERIFIED` and a human release role ✔; no dedicated content-signing key (I-05) | Medium | INV-09, RUL-INV-01, SEC-REQ-026 | `routers/rules.py:236-309` |
| P-16 | Evidence erasure destroys the active-store wrapped DEK ✔; backup and immutable-copy limitations not represented | Medium | EVD-REQ-022, PRI-REQ-031 | `engines/evidence.py:230-231` |
| P-17 | Content-staff isolation ✔; Zoiko support JIT access and break-glass not implemented | High (production) | INV-14, IAM-REQ-022/024 | `security.py:75-78` |

### 8.3 Missing

| ID | Missing capability | Severity | Spec |
|---|---|---|---|
| <a id="m-01"></a>M-01 | Versioned, reviewed migrations (expand/contract) with CI upgrade/downgrade/no-drift gates; start-up DDL removed. **Stage 1 implemented, not yet committed; see [§12](#12-implementation-status)** | **High** (blocks deploy and every schema fix) | DAT-REQ-023/039/040, QA-REQ-035/043 |
| M-02 | Durable workflow runtime; workers for checkpoints, verification, impact jobs and connectors | High | WRK-REQ-001/002/003, ADR C-10, ARCH §16.1 |
| M-03 | Outbox relay, managed queue, consumer inbox, dead-letter queue, replay | Medium | EVT-REQ-003/005/018/043, ADR C-09 |
| M-04 | Idempotency enforcement for external mutations | Medium-High | INV-10, API-REQ-004/005, WRK-INV-02 |
| M-05 | Optimistic concurrency (ETag / If-Match) on revisioned resources | Medium | API-REQ-006 |
| M-06 | Transfer policy and transfer ledger | Medium (before a second cell) | INV-07, TEN-REQ-011 |
| M-07 | OIDC/SAML federation, SCIM, phishing-resistant MFA, JIT, break-glass | High (production) | IAM-REQ-005/006/016/020/022/024 |
| M-08 | Machine-readable ADR register and traceability matrix in the repository; CI checks that requirements map to tests | Medium | ADR-REQ-024/025, TRC-REQ-002..004, QA-REQ-001/002 |
| M-09 | Outbox crash/failure-injection tests and consumer duplicate/out-of-order tests | Medium | QA-REQ-029/030 |
| M-10 | Backup/restore semantic-reconstruction tests | Medium | DAT-REQ-030, QA-REQ-036, TIM-REQ-029, EVD-REQ-039 |
| M-11 | Cross-language conformance vectors for canonical JSON and ledger hashing | Medium | EVD-REQ-004, ADR C-02 |
| M-12 | Model gateway with routing, provenance and injection controls. INV-02/12 are vacuously met because no model is called | Low now (P3) | ARCH §13, INV-02, INV-12 |
| M-13 | Graph/search projection with watermark (Phase 1 permits relational edges) | Low | INV-15, ARCH §11 |
| M-14 | Privacy schema: processing records, retention policies, deletion executions | Medium (before customer data) | ZA-PRI-001 |
| M-15 | Cell routing and entity-cell assignment history | Medium (before a second cell) | TEN-REQ-004/005/006 |
| M-16 | Database observability: outbox age, locks, constraint signals | Low | DAT-REQ-031 |

### 8.4 Confirmed implemented

Each item has code evidence and at least one test; where a test is missing, that is stated.

| ID | Requirement | Code | Tests |
|---|---|---|---|
| C-01 | INV-01 / APP-REQ-009: humans only as accountable authorizers; agents refused | `security.py:90-104`; `engines/applicability.py:249` | `test_inv01_agent_cannot_attest_even_with_permission`, `test_agents_need_a_bound_grant_not_just_an_identity`, `test_human_only_actions_refuse_service_principals` |
| C-02 | INV-14: content and platform staff have no standing tenant access | `security.py:75-78` | `test_cross_tenant_and_content_staff_isolation`, `test_super_admin_manages_the_registry_but_never_customer_data` |
| C-03 | INV-06: requirement version → non-null source record with fingerprint | `models.py:189-224` | none dedicated (structural: NOT NULL foreign key) |
| C-04 | INV-08: one control implementation per tenant, definition, subject and variant | `models.py:544` | none dedicated (constraint) |
| C-05 | INV-09 / RUL-INV-01: only `VERIFIED` drafts are published, by a human `content_release` role | `routers/rules.py:236-240`; `routers/catalog.py:158` | `test_source_mapping_required` (partial) |
| C-06 | INV-11: customer-visible connector status comes only from the registry; one guarded write path | `engines/connectors.py` | `test_only_zoiko_certification_staff_can_change_the_global_registry` and 2 more |
| C-07 | INV-13: one versioned roll-up; no snapshot update endpoint; exceptions never change state | `engines/assurance.py:355`; `codes.ROLLUP_ENGINE_VERSION` | `test_exception_never_changes_state_and_manual_green_impossible` |
| C-08 | APP-REQ-016 / TIM-REQ-012: evaluations pin rule version, inputs, cutoff and evaluator build; idempotent | `engines/applicability.py:59-131` | `test_canonical_hash_stable_and_semantic`, `test_bitemporal_correction_preserves_as_known_then` |
| C-09 | TIM-REQ-007 / DAT-REQ-009 for facts, determinations and obligations: corrections close record slices | `engines/facts.py:116`; `engines/applicability.py:167-329` | `test_bitemporal_correction_preserves_as_known_then` |
| C-10 | RUL: three-valued logic, UNKNOWN never coerced, typed rules, single-match invariant | `engines/rules.py` | `tests/test_engines.py` (8 rule tests) |
| C-11 | EVD-REQ-001/005: acknowledgement after manifest, ledger and head update in one transaction | `engines/evidence.py:44-121` | `test_evidence_integrity_package_and_offline_verifier` |
| C-12 | EVD-REQ-002 (ledger part): ledger append-only even for its tenant | `postgres_rls.sql:273` | `test_ledger_is_append_only_even_for_its_tenant` |
| C-13 | EVD-REQ-010/011: per-evidence DEK, tenant-keyed HMAC fingerprints | `engines/keys.py:41-67` | `test_ledger_tamper_detection_and_merkle_proof` (ledger part) |
| C-14 | DAT-REQ-014 / EVT-REQ-001 (write side): state and outbox row in one transaction | `platform.py:76`; engines plus router commit | none (QA-REQ-029 crash test missing, M-09) |
| C-15 | IPE-REQ-024 / IPE binding: vendor PASS stays an input; the bound result is used | `engines/assurance.py:42-53`; `engines/ipe.py:29` | `test_weaker_policy_cannot_replace_the_bound_result`, `test_assurance_ignores_newer_or_weaker_ipe_results` |
| C-16 | DAT-REQ-004 / SEC-REQ-023 role attributes: runtime role least-privilege, fail-closed | `pg_bootstrap.py:33-79` | `tests/test_pg_bootstrap.py` (4), `test_runtime_role_is_not_owner_and_cannot_bypass_rls` |
| C-17 | DOC-CON-001/002 canonical codes only | `app/codes.py` | `test_seeded_assurance_and_no_generic_compliance_state` |
| C-18 | DAT-REQ-016: connector credentials are vault references, not secrets | `models.py` (`credential_ref`) | none dedicated |

### 8.5 Unverified (evidence unavailable)

| ID | Item | Why unverified |
|---|---|---|
| U-01 | Approval of any ZA-* document or ADR | All are "Proposed for lock"; no signed approval record is in the repository or the document folder |
| <a id="u-02"></a>U-02 | Identity of the local specifications with the ZA-DOC-001 baseline | 0 of 21 local DOCX hashes match ZA-DOC-001 §03 |
| U-03 | ARCH §22 start condition: named owners and target decisions for ADR-001…004 and ADR-008…010 | No decision records in the repository |
| U-04 | Production infrastructure: KMS/HSM, WORM, TSA, multi-AZ PostgreSQL, cells | No deployment target exists |
| U-05 | ARCH §18 SLOs (availability, RPO/RTO, p99 latency) | Only local synthetic benchmarks exist (see the earlier performance work); no production-like environment |
| U-06 | Behaviour on the real development database | Not accessed (no credentials; policy) |
| U-07 | Hash and serialization conformance with other languages | No vectors exist to run (M-11) |
| U-08 | Content warranty and attestation language (ARCH §19) | Legal artefact, outside the repository |
| U-09 | Customer-zero / GA gate evidence (ARCH-ADR-011) | Operational evidence, outside the repository |
| U-10 | Backup and restore semantics | No environment or test (M-10) |

---

## 9. Requirement → code → test mapping

**Scope.**
- **Mapped here:** the ZA-ARCH-001 v3.0 normative core (§3 invariants, §4 authority rule, §22.2 transaction boundaries, §22.3 engineering-ready definition), each with its controlling detailed requirements.
- **Not mapped here:** the remaining controlled IDs one by one. This report makes no compliance claim about them.
- **Full ID index:** the 913-ID index produced for this audit is kept outside the repository; see §11. Producing the full matrix is roadmap Stage 9.

**Status key:** ✔ implemented and tested · ◐ partial · ✘ missing · ✗ incorrect.

### 9.1 §3 Architectural invariants

| Invariant | Controlling detail | Code | Tests | Status |
|---|---|---|---|---|
| INV-01 no AI actor as accountable authorizer | APP-REQ-009, IAM-REQ-030, DOM-INV-04 | `security.py:90-104`; `engines/applicability.py:249` | C-01 tests | ✔ |
| INV-02 no model call on an authorization or enforcement path | ARCH §13 | No model gateway exists | none (no dependency test) | ✔ vacuously; dependency test missing |
| INV-03 every assurance object bitemporal | TIM-REQ-001/007, DAT-REQ-008/009 | 3 table families complete; others partial (§7) | `test_bitemporal_correction_preserves_as_known_then` | ◐ (P-03, I-04) |
| INV-04 isolation below application logic | DAT-REQ-002/003/004, SEC-REQ-008, QA-REQ-019 | `postgres_rls.sql` | `tests/test_rls.py`, `test_other_organisations_cannot_touch_or_see_these_records` | ✗ (I-01) / ◐ (P-02) |
| INV-05 acknowledged manifest committed to a ledger segment | EVD-REQ-001/003/005 | `engines/evidence.py:44-121` | C-11, C-12 | ◐ (segment model, D-01) |
| INV-06 provision → source reference and fingerprint | RUL-REQ (source mapping) | `models.py:189-224` | `test_source_mapping_required` (rules) | ✔ (structural) |
| INV-07 evidence leaves its home cell only under a recorded transfer policy | TEN-REQ-011 | — | — | ✘ (M-06) |
| INV-08 framework mappings are views | — | `models.py:544` | — | ✔ (constraint) |
| INV-09 AI-extracted content cannot reach RELEASED without VERIFIED | RUL-INV-01 | `routers/rules.py:239`, `routers/catalog.py:158` | partial | ✔ (code) / test gap |
| INV-10 external mutations idempotent | API-REQ-004/005, WRK-INV-02 | two paths only | `IDEMPOTENCY_CONFLICT` path untested | ◐ (P-07, M-04) |
| INV-11 connector status only from the registry | FR-CON-001/002 | `engines/connectors.py` | 3 tests | ✔ |
| INV-12 hidden reasoning never stored | ARCH §13 | no model calls | — | ✔ vacuously |
| INV-13 one versioned roll-up; no manual override | STA-REQ-019/020/021 | `engines/assurance.py:355-549` | C-07 test | ✔ for override; reproducibility ✗ (I-03) |
| INV-14 content ops have no standing access | IAM-REQ-024 | `security.py:75-78` | C-02 tests | ✔ (support JIT missing, P-17) |
| INV-15 projection can be deleted and rebuilt | DAT-REQ-019/020, SEC-INV-11 | no projection exists; `/watermark` only | — | ✘ (M-13, low) |

### 9.2 §4 Authority rule and §22.2 transaction boundaries

| Item | Controlling detail | Code | Status |
|---|---|---|---|
| Decisions read authoritative PostgreSQL only; no stale-projection fallback | DAT-REQ-019/020 | All decision engines query PostgreSQL; no projection exists | ✔ |
| Content release publish: release + signatures + activated rule versions + outbox, atomic | RUL-INV-01 | `routers/rules.py:236-309` (one commit) | ◐ atomic; shared signing key (I-05) |
| Applicability evaluation: evaluation + trace + proposed obligation + outbox, atomic | APP-REQ-016, TIM-REQ-020 | `engines/applicability.py:59-131` | ✔ (proposed *determination*, see D-03) |
| Evidence acknowledgement: manifest + binding + ledger sequence + outbox before acknowledgement | EVD-REQ-001/005 | `engines/evidence.py:67-121`; router commits, then responds | ✔ |
| Customer attestation: attestation + adoption transition + audit, atomic | APP-REQ-009, DAT-REQ-036 | `engines/applicability.py:249` (router commit) | ✔ |
| Exception approval: state + coverage binding + recomputation request, atomic | STA, WRK-REQ-013 | `routers/assurance.py:353-365` updates state in place; no recomputation request | ◐ (I-04) |
| External mutation: idempotency record and mutation in one transaction | API-REQ-004/005 | idempotency table unused | ✘ (M-04) |

### 9.3 §22.3 Definition of engineering-ready

| Criterion | Status | Notes |
|---|---|---|
| Stable ID, revision ID, bitemporal fields, scope and accountable actor on every core object | ◐ | See §7 and P-03 / I-04 |
| Every state transition has one owning module, permitted actors and an explicit event | ◐ | Engines own core records; routers own governance transitions in place; most transitions emit events |
| Every rule or evaluator has golden vectors and a reproducibility test | ◐ | Rule golden vectors exist (`engines/rules.py`, workbench); snapshot reproduction is unreliable (I-03) |
| Every cross-cell or external data movement has a policy object and audit record | ✘ | M-06 |
| Every connector defines IPE capabilities before CERTIFIED | ✔ | Certification matrix gate (`engines/connectors.py:35-38`) |
| Every derived projection publishes a watermark and can be rebuilt | ◐ | `/watermark` exists; no projections yet |
| Every customer-visible status can be reproduced from stored revisions without model inference | ✗ | I-03 |

---

## 10. Unresolved governance decisions

None of these is decided by this report. Each blocks or shapes a roadmap stage.

| ID | Decision needed | Owner (per specs) | Blocks |
|---|---|---|---|
| G-01 | Runtime language: accept ARCH-ADR-003 (Go) and plan a port, or raise a successor ADR keeping Python for the authoritative control plane | CTO (T0) | Long-term architecture; Stage 11 |
| G-02 | Ledger segmentation model reconciling ARCH §9.2 with EVD-REQ-003/007 (D-01) | CTO + Security; Evidence Platform Lead | Stage 7 segmentation |
| G-03 | Approval of ZA-ADR-001 and the G1 baseline; closure of DOC-CON-001…003 and recording of D-02/D-03 in ARCH 3.1 | CTO / Architecture Governance; document owners | All "accepted" claims |
| G-04 | Confirm or replace the local specification copies (U-02) | Engineering Governance / QA | Traceability evidence |
| G-05 | Browser session architecture (C-08: BFF with cookie session) and identity provider for OIDC | Security Architecture / IAM Lead | Stage 6 |
| G-06 | KMS/HSM provider and key-purpose catalogue (checkpoint, package, content release, policy bundle, session) | CTO + Security | Stage 7 |
| G-07 | New reason code for snapshot inputs whose history predates the migration (proposed `HISTORY_UNAVAILABLE`), and its GLS-001 entry | CPO / CTO (semantic authority); Head of Assurance | Stage 3 backfill |
| G-08 | Read-authorization policy: permission per read operation, external-auditor engagement scoping | IAM Lead | Stage 4 |
| G-09 | Workflow runtime selection (C-10: managed Temporal) and hosting | Backend Platform / Workflow Lead | Stage 8 |
| G-10 | First production cell and deployment target (ARCH-ADR-001) | CEO / CPO / CTO | Deployment; Stage 1 rehearsal environment |

---

## 11. Method, commands and evidence

**Verified versus assumed.**
- Every finding marked I, P, M or C cites code that was read during this audit. Where a statement depends on runtime behaviour, it was run: the test suites, the route scan and the model scan.
- Specification statements were read from the source DOCX tables, not paraphrased from memory.
- **Assumptions are labelled as such:**
  - that the local DOCX content equals the registered baseline (U-02);
  - that reconciling D-01 is possible.

**Commands run.** All read-only against the repository; temporary outputs were written outside it.

| Purpose | Command (abridged) | Result |
|---|---|---|
| Working tree | `git status --short`; `git diff --cached --name-only`; `git stash list`; `git log --oneline -1` | 45 modified, 2 untracked, 0 staged, 0 stashes, HEAD `af1d508` |
| Specification text | `pdftotext -layout Zoiko_Assure_Volume_01.pdf` (volumes 02–05 extracted earlier) | 5 text files |
| Specification index | Python, standard library only (`zipfile` + `xml.etree`): every DOCX table row whose first cell is a controlled ID | 913 unique IDs; 902 canonical definitions |
| Document fingerprints | `sha256sum *.pdf *.docx`; compared with ZA-DOC-001 §03 | PDFs: V01 `32457eab…`, V02 `d05a6160…`, V03 `f6d610c6…`, V04 `f8aa66ee…`, V05 `bf56b6b2…`. DOCX: 0/21 match the register |
| Model scan | Import `app.models` against a throwaway SQLite path; compare tables with `postgres_rls.sql` | 63 tables, 42 tenant, 41 RLS, 0 FORCE, 2 append-only |
| Route scan | Import `app.main.ROUTERS`; classify each route by `authorize` / `get_ctx` dependency | 128 routes: 62 guarded, 56 sign-in only, 10 public |
| Code inspection | `grep`/`sed` over `backend/app`, `backend/db`, `.github/workflows`, `frontend/lib/api.ts` | as cited |
| Tests (SQLite) | `python -B -m pytest tests -q -p no:cacheprovider` | 100 passed, 9 skipped |
| Tests (PostgreSQL 17) | Disposable cluster reproducing the CI `backend-postgres` job (random passwords, never printed; deleted afterwards) | 109 passed, 0 failed; RLS gate 5/5; bootstrap gate 4/4 |

**Not run:**
- No migrations, seeds or schema changes against any persistent database.
- No access to the development database.
- No frontend build: the scope is the backend. The frontend was type-checked and built in the preceding task.

**Scope limits.**
- The source specifications total about 612 pages and 913 controlled IDs.
- This audit maps ARCH-001's normative core in full, and the detailed requirements cited above. It does not assert compliance status for the other detailed IDs.
- The per-ID matrix is roadmap Stage 9, which also makes it maintainable in CI.

---

## 12. Implementation status

This section records work done after the audit. The findings above are unchanged and describe the audit baseline.

### Stage 1: migration foundation (M-01)

**Status:**
- Implemented in the working tree on 27 October 2026, and verified on disposable PostgreSQL 17 clusters.
- **Not yet committed, and not yet run in GitHub Actions.**
- M-01 is resolved for versioned migrations and start-up DDL.
- Deployment stays blocked by the deployment target (G-10), which now also covers the pre-migration backup step.
- Migrations alone do not make the backend production-ready.

| Requirement | Evidence |
|---|---|
| Versioned, ordered migrations with dependency tracking (DAT-REQ-023/039) | Revisions in `backend/migrations/versions/`. `0001`: the 63 tables, equal to `create_all`, irreversible. `0002`: the former `db/postgres_rls.sql` byte for byte, plus one `REVOKE`. `alembic history` shows `<base> -> 0001 -> 0002 (head)`. Entry point: `python -m app.migrate` ([database-migrations.md](database-migrations.md)) |
| Baseline equal to the schema the old start-up produced | On empty PostgreSQL 17, the normalised `pg_dump --schema-only` had 382 statements, identical to the old start-up's output (`alembic_version` excluded). The catalogue fingerprint was identical (digest `663cea6c…`). CI re-checks this: `test_old_startup_schema_is_exactly_the_migration_baseline`, `test_fresh_install_upgrades_in_order_through_the_baseline_to_head` |
| Safe adoption of existing databases; no stamp without validation | `status` compares the catalogue with `migrations/baseline_schema.json` without changing anything. The only automatic stamp follows an exact match, inside the upgrade transaction; anything else is refused. Rehearsal through the deploy step: a database built by the HEAD start-up code with the Customer Zero fixture (63 tables, 310 rows) was adopted, with identical row counts afterwards; a drifted database was refused with its differences listed and was left unchanged. Development database `assure`: a read-only `status` reports an exact baseline match. **It has not been migrated.** |
| Separation from API start-up | `main.py` start-up is read-only: both roles connect, the schema is at head, and the runtime role is least-privilege. Tests: `test_api_startup_runs_no_ddl` (a psycopg-level spy covering both engines and raw cursors) and `test_api_refuses_to_start_on_a_database_not_at_head` |
| Failure handling | One transaction holding advisory lock `724501`. `test_failed_migration_rolls_back_completely`: a failing revision leaves no table, row or version change, whether run from empty or from head |
| CI gates (QA-REQ-035) | `backend-postgres` runs the migration tool on a fresh database (`status`, `upgrade` ×2, `status`, `check`). The gate requires `test_migrations=16`. The tests include no drift from the models (autogenerate-empty), RLS coverage of every table carrying `tenant_id`, and downgrade and re-upgrade of the newest revision |
| Deploy integration | `deploy-environment.yml` runs `status`, `upgrade` and `check` from the release bundle, with the migration credential given to that step only. A target-specific, fail-closed backup step precedes it |

| Run | Result |
|---|---|
| SQLite (`pytest tests -q -p no:cacheprovider`) | 101 passed, 24 skipped (all PostgreSQL-only) |
| PostgreSQL 17, local reproduction of the CI `backend-postgres` job on a fresh cluster | Migration step passed; RLS gate 5/5; full suite 125 passed, 0 skipped. Gate: `test_rls` 5/5, `test_pg_bootstrap` 4/4, `test_migrations` 16/16 |

**Unchanged by design** (later stages):
- I-01, I-02, P-02
- the RLS policies (still `ENABLE`, not `FORCE`), immutable-table privileges, historical reconstruction, authorization, session storage and signing keys

The one addition, made for migration correctness only: `REVOKE ALL ON alembic_version FROM assure_app` in revision 0002. It makes fresh and adopted databases identical, and keeps the runtime role from reading or altering the migration state.

**Still open:**
- **Deployment target and backup step (G-10).**
- **The API still holds the owner credential at start-up.** It needs it for the read-only checks, the one-time account bootstrap and the demo seed ([ci-cd.md](ci-cd.md), deployment blockers). This depends on the Stage 2 system-job design.
- **A first GitHub Actions run** of the updated workflows.
- **The week plan.** In `backend/backend-build-plan.md`, the line 68 checkbox ("Migration framework") and line 40 (which names `db/postgres_rls.sql`) are left to the plan owner.

**Governance:** none of G-01…G-10 is decided by this work.
- G-01 does not block Stage 1: migrations are SQL schema history, and the tool can remain an operations tool under either runtime. The recorded dependency is that a Go runtime would have to honour the same `alembic_version` start-up gate, or replace it through a reviewed decision.
- G-10 still blocks deployment.

### Stage 2, work package A1: immutable completed results (I-02)

**Status:**
- Implemented in the working tree on 28 October 2026, and verified on disposable PostgreSQL 17 clusters.
- **Not yet committed, and not yet run in GitHub Actions.**
- The development database was upgraded to `0003` on 28 October 2026 at 13:00, after a verified backup and a rehearsal (see below).
- I-01 (Critical) and P-02 are **not** addressed. They are blocked on the system-job design decision ([roadmap §6](backend-architecture-roadmap.md#6-stage-2-gap-matrix)).

**Write-path inventory** (every `UPDATE`, `DELETE`, ORM attribute assignment and post-flush change in `backend/app` and `backend/tools`):
- These are inserted once with final values and never updated or deleted: `applicability_evaluation`, `assurance_control_evaluation`, `assurance_snapshot`, `evidence_ipe_evaluation`, `evidence_package`, `governance_attestation`, `evidence_ledger_checkpoint`.
- Two legitimate in-place writes remain:
  - source-run completion (`engines/ipe.py:273-275`: `lifecycle`, `completed_at`, `evidence_ids`)
  - IPE anomaly disposition (`routers/integration.py:169`)
- Every other in-place write targets I-04 tables, which are out of this package.

| Requirement | Enforcement (migration `0003`) | Evidence |
|---|---|---|
| DAT-REQ-011, STA-REQ-019, APP-REQ-012, API-REQ-019, DAT-REQ-036; DAT §13 "completed evaluation/snapshot tables deny ordinary UPDATE/DELETE" | Runtime role: no `UPDATE`/`DELETE` on applicability evaluations, control evaluations, snapshots and attestations. A row trigger also stops the owner role's `UPDATE`/`DELETE` (not its DDL; A10) | `test_runtime_role_cannot_update_or_delete_completed_results`, `test_completed_results_cannot_be_rewritten_even_by_the_owner_role` |
| IPE-REQ-025, IPE-INV-11 | The same for IPE evaluations. On source runs, only the completion columns stay writable, so version bindings and provenance are fixed | as above, plus `test_guarded_rows_change_only_in_their_completion_columns` |
| IPE-REQ-034 (partial) | On anomalies, only the disposition columns are writable; the original check result is fixed. The disposition is still an in-place write, not the appended event the spec requires (I-04) | `test_guarded_rows_change_only_in_their_completion_columns` |
| EVD-REQ-026, EVD-REQ-009, EVD-REQ-002, SEC-REQ-029 | Packages and checkpoints immutable. Ledger and audit events gain the privilege layer behind their 0002 triggers | the generated policy test |
| DAT §13 "no runtime role may TRUNCATE" | Asserted for every table (never granted) | `test_runtime_role_privileges_and_triggers_match_the_reviewed_policy` |
| Reversibility (DAT-REQ-023/039) | The downgrade restores the 0002 grants exactly and removes the triggers and function | `test_downgrade_to_the_baseline_restores_its_privileges_exactly`; rehearsed on a restored copy of the development database |

| Run | Result |
|---|---|
| SQLite suite and `backend-unit` gate | 101 passed, 29 skipped (all PostgreSQL-only); gate passed |
| Full suite on a fresh disposable PostgreSQL 17 database | 130 passed, 0 skipped |
| Mutation check of the generated policy test (rolled back) | All three injected regressions reported: `UPDATE` re-granted on a snapshot table, a guard trigger dropped, `TRUNCATE` granted on an unrelated table |
| Rehearsal on the development data | A verified backup was restored into a disposable database (63 tables, 605 rows). Then: `upgrade` 0002 → 0003; per-table row counts identical; `check` passed; no policy mismatches; API start-up with no DDL (`/api/health` and `/api/watermark` 200); `downgrade 0002` matches the 0002 baseline exactly; re-upgrade passed |

**Test change:**
- `test_rls.py::test_ledger_is_append_only_even_for_its_tenant` now expects the privilege refusal, which comes before the trigger.
- `test_authorization.py::test_assurance_ignores_newer_or_weaker_ipe_results` removes its planted rows by disabling the trigger explicitly inside its cleanup transaction.

**Development database:**
- Backup: `backend/var/backups/assure-before-0003-20261028-104121.dump`, checked with `pg_restore --list` and by a full restore.
- Before the upgrade, the backup was restored into a disposable database and matched the live database exactly: revision, schema digest, 605 rows, per-table counts, record watermark 237, and a content digest of every row. The upgrade was then rehearsed on that copy.
- Upgraded with `python -m app.migrate upgrade` (0002 → 0003; exit 0). Afterwards, `status` reports head with nothing pending and `check` passed.
- Data unchanged: rows, per-table counts, content digest and watermark are the same as before. The evidence-store manifest is unchanged.
- Read-only inspection: all 11 guard triggers are enabled, and the runtime role has no `UPDATE`, `DELETE` or `TRUNCATE` except the 5 permitted completion columns. The policy check finds no mismatches.
- The backend restarted on it: `/api/health` returns 200, and the schema digest is unchanged across start-up (no DDL).

### Stage 3a: deterministic snapshot inputs (I-03(d)), partial

**Status:**
- Partially implemented in the working tree on 28 October 2026. **Not committed; not run in GitHub Actions.**
- No migration and no engine version change.
- **Stage 3a is not complete:** the version bump and the test-definition order are blocked (see below).

**Snapshot lifecycle traced:**
1. **Entry points:**
   - `POST /api/obligations/{lineage}/snapshots` persists.
   - `POST …/preview` computes and rolls back.
   - `GET /api/snapshots/{id}/reproduce` recomputes at the pinned cutoff and compares the dependency digest and state.
2. **`engines/assurance.compute_snapshot`** collects the inputs known at the record cutoff:
   - obligation revisions and expression versions
   - control implementations, with their policies, test definitions, runs and evidence, which set the segment boundaries
   - compensations, exceptions and occurrences

   It then segments the period at sorted unique boundaries.
3. **For each segment:** it evaluates the expression, with leaves from `evaluate_control`. A persisted evaluation deduplicates by its dependency digest.
4. **Persistence:** adjacent segments coalesce. The engine builds the dependency manifest (sorted ID sets) and its digest, then persists the `AssuranceSnapshot` (immutable since migration 0003), outbox events and issue hooks in one transaction.

**Root causes of nondeterminism found:**

| Where | Effect | Requirement |
|---|---|---|
| Test runs per test definition read in physical order | The control-evaluation digest, and through it the snapshot digest, reproduction and deduplication | STA-REQ-020/021, I-03(d) |
| Test definitions read in physical order (no record order exists) | The same digest for multi-test policies; the primary-test fallback | STA-REQ-021 (remaining) |
| Obligation revisions, control implementations, compensations, exceptions and occurrences in physical order. Exceptions and occurrences are rewritten in place (I-04), so their physical order drifts | Which revision or implementation is chosen; the order of exception treatments and occurrence traces inside the hashed segments | STA-REQ-020, TIM-REQ-015 |
| Duplicate control evaluations (concurrent twins) resolved in physical order | Which evaluation a leaf references | ZA-STA-001 uniqueness |
| No tenant predicate on runs, test definitions, policies, expression versions, compensations, occurrences and evidence | Another tenant's rows became inputs wherever RLS does not apply (owner role, system scope) | SEC-REQ-008, DAT-REQ-028 |
| Wall clock in occurrence logic; evidence lifecycle and conflicts read without the cutoff (I-03(c)(a)(b)) | Reproduction depends on when it runs and on later events | TIM-REQ-006/015: **Stage 3d, not changed** |

**Requirements to code:**

| Requirement (ZA v1.0) | Required | Before | Change | Proof |
|---|---|---|---|---|
| DAT-REQ-007, TIM-REQ-005 | `record_seq` gives deterministic knowledge ordering | Unordered reads | `ORDER BY record_seq, id` on every multi-row snapshot input | test 1 |
| STA-REQ-020 | Deterministic reasons and explanation trace | Trace item order followed physical order | as above | test 1 (segments identical after a physical rewrite) |
| STA-REQ-021, TIM-REQ-015 | Exact dependencies pinned at a record cutoff | The digest changed with row order | Runs in record order | tests 1 and 2 |
| ZA-STA-001 uniqueness rule | An identical digest *may* deduplicate to one authoritative evaluation | An arbitrary twin was reused | The earliest twin (`created_at`, id) | test 3 |
| SEC-REQ-008, DAT-REQ-028 | Isolation enforced independently of RLS | A foreign run was pinned as an input (owner role) | Explicit tenant predicates | test 2 |
| STA-REQ-032 | Engine changes need an ADR, golden-vector regression and snapshot versioning | n/a | No engine output change, so no version bump. Shown by an old-versus-new comparison (below) | comparison |
| STA-REQ-021 (test definitions) | Deterministic dependency digest | Stored (physical) order | **Not changed, blocked:** no persisted order. Any change alters digests (STA-REQ-032; TIM-ADR-04), or needs a schema decision to store authoring order | none |

Tests 1 to 4 are in `backend/tests/test_snapshot_determinism.py`.

**Results:**

| Run | Result |
|---|---|
| New tests against the pre-change engine (saved copy, loaded by a scratch pytest plugin) | 3 failed, 1 passed. Each failure is its defect: the digest after a physical rewrite, the foreign run pinned, the twin chosen by row order |
| New tests against the changed engine | 4 passed |
| Old-versus-new engine on the same database state: a copy of the development data, and the suite's data | 20 of 20 snapshot computations byte-identical. 113 of 113 stored control-evaluation digests reproduced by both engines. 8 of 8 stored snapshots reproduced (digest and state) by both engines |
| Full suite on a fresh disposable PostgreSQL 17 database | 134 passed, 0 skipped. Gate requires `test_snapshot_determinism=4` |
| SQLite suite and `backend-unit` gate | 102 passed, 32 skipped (PostgreSQL-only); gate passed |
| Local reproduction of the CI `backend-postgres` job | Migration step to 0003 and check passed; RLS gate 5/5; full suite 134 passed, 0 skipped; gate requirements read from ci.yml all met (test_rls 5/5, test_pg_bootstrap 4/4, test_migrations 16/16, test_immutability 5/5, test_snapshot_determinism 4/4) |

Stage 2 A1 protections are unchanged: on the development database, the generated policy check finds no mismatches.

**Not in 3a, or blocked:**
- The evaluator and roll-up version bump (STA-REQ-032 ADR).
- The test-definition order.
- I-03(a)(b)(c): Stage 3d, which needs 3b and 3c.
- Full snapshot-hash comparison in `reproduce`: Stage 3e.
- A uniqueness constraint on control-evaluation digests: the spec says deduplication *may* happen, and concurrent twins remain possible but now resolve deterministically.
- Idempotent snapshot requests: Stage 5 (API-REQ-004/005).

### Findings recorded after the audit

| ID | Finding | Severity | Requirements | Status |
|---|---|---|---|---|
| A8 | Published catalogue artefacts (rule versions, content releases, set versions, released fact definitions) were writable and deletable by the runtime role, with no trigger. Publication rewrote the new rule version in place | **High** (D) | DAT-REQ-012, RUL-REQ-009/010/014/027; ZA-DAT-001 immutability verification row | Fixed in the working tree by migration `0004` and a publication change (below). Not committed |
| A9 | Binding evaluation does not verify the rule version's AST hash or its release signature | **High** (D, S) | APP-REQ-006; ZA-APP-001 "signature/release ID checked before input freeze" | Partly addressed. A9a (hash and manifest binding) is implemented, [below](#task-3-rule-integrity-before-binding-evaluation-a9a). A9b (signature) is blocked on the trust anchor ([signature-trust brief](backend-decision-brief-content-signature-trust.md)) |
| A10 | The immutability triggers stop the owner role's DML but not its DDL: the owner can disable them | **High** (D, S) | ZA-DAT-001 §13; DAT-REQ-004; SEC-INV-08 | Open; needs the role-model decision (review below) |

### Stage 2c extension: published catalogue immutability (A8)

**Status:**
- Implemented in the working tree on 28 October 2026, and verified on disposable PostgreSQL 17 clusters.
- **Not committed; not run in GitHub Actions.**
- The development database was upgraded to `0004` on 28 October 2026, after a verified backup and a rehearsal (below).
- Like every schema change touching immutable tables, it needs the security/data/QA review DAT-REQ-039 requires.

**Write-path inventory:**
- The four tables are only ever inserted, by publication (`routers/rules.py`) and the seed.
- The one in-place write was publication pointing the new `RuleVersion` at its release. Both now insert final values, using IDs generated up front.
- Development data: 4 PUBLISHED rule versions, 4 releases, 3 set versions, 10 RELEASED fact definitions; none `pending`.

| Requirement | Before | Change | Proof |
|---|---|---|---|
| DAT-REQ-012; ZA-DAT-001 "runtime role cannot update/delete … PUBLISHED catalogue artifacts" | Runtime role `UPDATE`/`DELETE`; no trigger | `0004`: revoke `UPDATE`/`DELETE`; `append_only` trigger | Generated policy test; runtime refused; owner stopped |
| RUL-REQ-009/010 (a PUBLISHED RuleVersion is immutable; semantic change means a new version) | Rewritable in place | Immutable; publication inserts once | End-to-end publication test (version + 1, row cannot be rewritten) |
| RUL-REQ-014 (immutable SetVersions pinned by hash) | Rewritable | Immutable | Policy and owner tests |
| RUL-REQ-027 (the signed manifest binds the exact RuleVersion and hash) | Release rewritable; rule version pointed at it after insert | Both written once; the manifest binds the inserted version | End-to-end publication test |

**Results:**

| Run | Result |
|---|---|
| `test_immutability.py` | 7 passed, including a failed publication that leaves no rows (negative control: it fails against a mutant that commits early) |
| The publication test against the pre-change publication code (scratch copy loaded by a pytest plugin) | Failed: `permission denied for table catalog_rule_version`, the old in-place update |
| SQLite suite and `backend-unit` gate | 102 passed, 34 skipped (PostgreSQL-only); gate passed |
| Upgrade from `0003` on a copy of the development data | `0003` → `0004`; `check` passed; content digest identical before and after; catalogue privileges and triggers as intended; API start-up without DDL. Downgrade to `0003` restored the previous grants (content unchanged); re-upgrade passed |
| Local reproduction of the CI `backend-postgres` job | Migration step to 0004 and check passed; RLS gate 5/5; full suite 136 passed, 0 skipped; gate requirements read from ci.yml all met (test_rls 5/5, test_pg_bootstrap 4/4, test_migrations 16/16, test_immutability 7/7, test_snapshot_determinism 4/4) |

**Stage 3a governance:** the blocked remainder is documented in the [decision brief](backend-decision-brief-snapshot-versioning.md). No approved ADR exists: ZA-ADR-001 itself is unapproved, so none of its ACCEPTED* baseline entries (including TIM-ADR-04) is in force.

### Development database upgraded to `0004` (28 October 2026)

1. **Target confirmed:** `assure@127.0.0.1:5433/assure` from `backend/.env`. Before: `at revision 0003; pending 0004`.
2. **Backend stopped:** the reload worker was down (health did not respond) and the database had no other sessions.
3. **Backup:** `backend/var/backups/assure-before-0004-20261028-154908.dump`, custom format, 449 archive entries.
   - Evidence store copied to `backend/var/backups/var-before-0004-20261028-154908` (17 files, digest identical to the source).
   - Restored without error into a new disposable database. Identical to the live database on revision, schema fingerprint, 63 tables, 607 rows, watermark 238 and content digest `e19166f830e37c9b`.
4. **Rehearsal on the restored copy:**
   - Upgrade, `check` passed, no policy mismatches, content unchanged.
   - Downgrade to `0003` and re-upgrade, content unchanged.
   - One setup error was found and fixed first: the disposable cluster's runtime role lacked the owner's ADMIN option. It was set to mirror the development server.
5. **Upgrade:** `python -m app.migrate upgrade` gave `0003` → `0004`; `check` passed; `status` shows the head revision.
   - Content digest is still `e19166f830e37c9b`, the watermark 238.
   - No policy mismatches and no disabled triggers.
   - The runtime role holds only `SELECT`/`INSERT` on the four catalogue tables.
6. **Backend restarted:** by a file-change event on the user's `uvicorn --reload` supervisor.
   - `/api/health` 200; `/docs` (Swagger UI) 200; `/openapi.json` 200 with 112 paths.
   - The schema fingerprint is unchanged by start-up (`55b44a62285391fb`), so start-up ran no DDL.

### Backend security, data-integrity and QA review (28 October 2026)

**Scope and standing:**
- Prepared as evidence for the review that DAT-REQ-039 requires of schema changes to immutable tables (migration `0004`).
- **The sign-off belongs to the named Security, Data and QA reviewers. It has not happened.**
- Every probe ran on a disposable PostgreSQL 17 copy of the development data, inside a transaction that was rolled back.

| ID | Spec requires | Code and database do | Evidence | Severity | Remediation | Decision needed |
|---|---|---|---|---|---|---|
| A8 | DAT-REQ-012; ZA-DAT-001 §29: "Runtime role cannot update/delete … PUBLISHED catalogue artifacts" | `0004` revokes runtime `UPDATE`/`DELETE` and adds `append_only` triggers. Publication and seed write each row once | `test_immutability.py` 7/7. Development database: no policy mismatches, no disabled triggers. Probe: owner `UPDATE` refused ("catalog_rule_version is append-only") | Closed in the working tree; residual risk A10 | None beyond A10 | DAT-REQ-039 sign-off |
| A10 (new) | ZA-DAT-001 §13: non-login table owners; migrator only in controlled deployment; runtime roles not owners. DAT-REQ-004; SEC-INV-08 | The owner role `assure` (CREATEROLE, owns 64 tables) can disable a trigger with DDL | Probe: owner ran `DISABLE TRIGGER append_only`, then rewrote 4 published rule versions (rolled back). The runtime role was refused ("must be owner") | High (integrity; requires the owner credential, which the API holds) | Non-login owner role; separate migrator; remove the owner credential from the API | Role model: Data Architect + Security, with A2 |
| I-01 | DAT-REQ-005; ZA-DAT-001 §12 (narrow SECURITY DEFINER only); INV-04; DOM-INV-11; SEC-INV-02; SEC-REQ-008 | Every RLS policy admits `assure_system()`, which reads `current_setting('app.system')`. Any session can set it | Probe as `assure_app` with a foreign tenant context: 0 / 0 / 0 rows (test runs / evidence / legal entities). After `set_config('app.system','on')` by the same session: 9 / 10 / 2 | Critical | Stage 2a: per-job system path | System-job design (Data Architect + Security); blocked |
| P-02 | DAT-REQ-003; TEN-REQ-022; ZA-DAT-001 §12 | RLS enabled on 41 tables, forced on 0. The owner is not subject to RLS | Probe: owner with no tenant context reads all 9 test runs | High | Stage 2b: `FORCE ROW LEVEL SECURITY` once owner-dependent paths (seed, bootstrap, system jobs) move off the owner | Same decision as I-01 |
| Owner credential | DAT-REQ-004; ZA-DAT-001 §13; SEC-REQ-004/023; SEC-INV-07; IAM-REQ-041 | `db.py` builds `admin_engine` from `DATABASE_ADMIN_URL` for the process lifetime. `main.py` start-up uses it for the runtime-role check, the demo seed and account bootstrap. No request handler uses it | `app/main.py:32-42`; `app/db.py:56,89`; grep of `AdminSession`/`admin_engine` | High (production) | Run seed and bootstrap as operator commands with the migration credential; the API receives `DATABASE_URL` only | Sequenced with A2 and A10 |
| I-04 | DAT-REQ-009/037; TIM-REQ-007; STA-REQ-027; DOM-INV-15 | Governance and occurrence rows are updated in place | `routers/assurance.py:163-164` (occurrence fulfilment), `:362` (exception state), `:424`/`:435` (compensation status); `routers/evidence.py:235` (conflict resolution) | High | Stage 3c: append-only status events | Event-table convention (B2) |
| QA-1 | DAT-REQ-030; TIM-REQ-029; SEC-REQ-036; ZA-DAT-001 §22 ("restore must prove meaning") | The runbook checks a backup with `pg_restore --list` only. Semantic verification (restore into a new database, compare revision, schema, rows, content digest, watermark) exists only in scratch tooling used for this upgrade | [database-migrations.md](database-migrations.md) §Procedures; this upgrade's verification results below | Medium | A repository command that restores a backup into a new database and compares it, plus snapshot reproduction after restore | None for development; production target G-10 |
| QA-2 | DAT-REQ-028; QA-REQ-019; ZA-SEC-001 §8 (seeded canary tenants, ID substitution on every path) | `test_rls.py` has 5 targeted cases. No generated test over every tenant table, and no test for the I-01 bypass (it currently works) | `tests/test_rls.py`; 8 cross-tenant references across the suite | High (with P-02) | A generated read/write/update/delete isolation test over every tenant table, like the generated immutability policy test | None to write; it can only pass after I-01 and P-02 |
| QA-3 | DAT-REQ-014; RUL-REQ-020; ZA-DAT-001 §29 (outbox atomicity "under crash injection") | Injected-failure coverage now exists for publication only | New test: a signing failure after the RuleVersion flush leaves no rule version, release or audit record, and a retry succeeds. Negative control: against a mutant that commits after the insert, the test fails (`assert 1 == 0`) | Medium | The same pattern for snapshot creation, evidence capture and adoption | None |

**No finding:** migration consistency and rollback.
- `0004` upgraded, downgraded to `0003` and re-upgraded on a copy of the development data. Content was unchanged throughout, and `check` and the policy check passed.
- `test_migrations` passed 16/16.
- The CI gates read their counts from `ci.yml`.

### Task 3: rule integrity before binding evaluation (A9a)

**Status:** implemented in the working tree on 29 October 2026, and verified on SQLite and on a disposable PostgreSQL 17
cluster. **Not committed, and not run in GitHub Actions.** A9b (signature verification) is not implemented: it is
blocked on the content-release trust decision ([signature-trust brief](backend-decision-brief-content-signature-trust.md)).

| Task | Requirement | Evidence |
|---|---|---|
| 3.1 AST hash | APP-REQ-006; ZA-APP-001 §13; ZA-RUL-001 §12; RUL-REQ-014/016 | `engines/applicability.verify_rule_integrity` runs in `evaluate()` before any fact is resolved. It re-hashes every pinned SetVersion from its members, then the AST under the RuleVersion's declared schema and operator-set versions (`rules.ast_hash` takes them as keyword arguments; the defaults are unchanged). Tests: altered AST, declared schema version, SetVersion members |
| 3.2 Manifest hash | RUL-REQ-027 | The release manifest is re-hashed (canonical JSON, SHA-256) and compared with `manifest_hash`. Test: altered manifest |
| 3.3 Release coverage | RUL-REQ-027; ZA-APP-001 §13 | The release the RuleVersion names must exist, be PUBLISHED and carry a signature (presence only). Its manifest must list exactly this RuleVersion: the same AST hash, every field it declares, and the same SetVersion pins. Tests: consistent rewrite of the rule row, release of another rule version, missing release, moved effective period |
| 3.4 Refusal | ZA-RUL-001 §18; APP-REQ-019; SEC-REQ-048; ZA-API-001 error rule | `RuleIntegrityError` is not an `HTTPException`, so no caller can absorb it. Every route that evaluates a binding RuleVersion runs inside `integrity_fail_closed`: single and batch evaluation, replay, historical comparison and publication impact re-evaluation. The result is HTTP 500 `{"error": "RUL_SIGNATURE_INVALID"}`, the whole operation rolled back, and no fallback. Tests: no fallback to the previous version; every path fails closed; publication fails closed |
| 3.5 Security signal | SEC-REQ-048; ZA-SEC-001 §23; ZA-EVT-001 §19 and its durability rule; DAT-REQ-014 | After the rollback, a new transaction on the same session and runtime role commits an audit record (`content.verify`; reason: the check and the failing artefact) and `security.content_signature_failed` (release ID, rule version ID, failing artefact ID, cell, verification result). Every refusal test asserts both rows, and that nothing else changed |
| 3.6 Tests | ZA-QA-001 rule-release gates and canonicalization; no test doubles for integrity | `tests/test_rule_integrity.py`, 13 tests; CI gate `--require test_rule_integrity=13`. Tampering happens below the application: on PostgreSQL the owner disables the trigger inside its own transaction (A10) |
| 3.7 A9b | APP-REQ-006 ("signature-valid"); C-07; RUL-ADR-06 | Not implemented, by design. The signature is checked for presence only |
| 3.8 Stage 3a | STA-REQ-032; TIM-ADR-04 | No engine version, snapshot format or input-bundle change: the check adds nothing to the bundle or its hash. `test_snapshot_determinism` passed 4/4 |

| Run | Result |
|---|---|
| SQLite, `tests/test_rule_integrity.py` | 13 passed |
| Negative controls (SQLite; a pytest plugin patches at run time, source unchanged) | Verification removed: 11 of 13 fail (the 2 untampered regression tests pass). Signal recording removed: the same 11 fail |
| SQLite, full suite (`pytest tests -q -p no:cacheprovider`) | 115 passed, 34 skipped (all PostgreSQL-only) |
| PostgreSQL 17, local reproduction of the CI `backend-postgres` job on a fresh cluster | Migration step passed; RLS gate 5/5; full suite 149 passed, 0 skipped. Gate: `test_rls` 5/5, `test_pg_bootstrap` 4/4, `test_migrations` 16/16, `test_immutability` 7/7, `test_snapshot_determinism` 4/4, `test_rule_integrity` 13/13 |
| Development database (read-only) | All 4 published rule versions pass `verify_rule_integrity` |

**Decisions and limits:**
- **Signal persistence, for confirmation.** The design follows ZA-EVT-001's durability rule, with the audit record as the committed security state. The EVT and SEC owners should confirm it.
- **Durable, not delivered.** The outbox is not relayed (P-04) and there is no `SECURITY_CRITICAL` route or delivery class, so the event reaches no SIEM yet.
- **Self-consistency only.** A consistent rewrite of the rule row, its manifest and the manifest hash passes every check. Only signature verification against a trusted key (A9b) detects it.
- **HTTP status.** ZA-API-001 names no status for integrity failures. 500 matches the existing mapping of content errors in `evaluate()`.
- **Publication pins stored set hashes.** Publication still pins each SetVersion's stored hash without re-hashing its members. A tampered SetVersion is caught at the next binding evaluation, including the publication's own impact re-evaluation when earlier evaluations exist, but not at publication itself.
- **Read routes can write.** The replay (evaluation detail) and historical-comparison GET routes commit the security signal when they refuse.
