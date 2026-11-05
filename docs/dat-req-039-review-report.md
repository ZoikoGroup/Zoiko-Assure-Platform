# DAT-REQ-039 Review — Migrations 0011, 0012, 0013

> **Date:** 3 November 2026. **Branch:** `naveen-changes`, working tree only. Nothing was staged, committed, pushed or pulled.
> **What this is:** an engineering security and data-integrity review, with fixes and tests. **It is not the DAT-REQ-039 review itself.** DAT-REQ-039 says schema changes affecting RLS, controlled codes, authoritative refs or immutable tables "require security/data/QA review", and that review is by the security, data and QA owners. **DAT-REQ-039 is not complete** until they review this evidence.
> **Database safety:** the canonical `assure` database was not connected to or modified, not even read-only. All PostgreSQL work used fresh databases in a throwaway PostgreSQL 17.11 cluster in the session scratch folder.
> **Fixes:** see [dat-req-039-remediation-report.md](dat-req-039-remediation-report.md).

## 1. Scope and method

- **Migrations:**
  - `0011_authority_grant_events.py`: AI authority grant history, guarded grant, append-only receipts.
  - `0012_connector_certification_history.py`: global registry history and platform-only writes.
  - `0013_contract_version_certification.py`: contract versions as certification subjects.
- **Code:**
  - `app/engines/governance.py`: `issue_authority_grant`, `revoke_authority_grant`.
  - `app/routers/ai.py`: grants, `_decide`, policy bundle.
  - `app/engines/connectors.py`: `set_certification`, `set_contract_certification`, `_prior_event`.
  - `app/routers/integration.py`.
  - The 0003, 0007 and 0010 guard and context functions these reuse.
- **Specifications:**
  - ZA-DAT-001: §12, §13; DAT-REQ-003/004/014/024/026/027/037/039.
  - ZA-IAM-001: IAM-INV-07/09; IAM-REQ-015/030/033/036.
  - ZA-DOM-001 §10.2; ZA-GLS-001 §07.
  - ZA-EVT-001: catalogue; EVT-REQ-008/025.
  - ZA-API-001: API-REQ-014/033.
  - ZA-IPE-001: IPE-REQ-002/028/029.
  - ZA-SEC-001: SEC-REQ-029, SEC-INV-03.
- **Method:** read each migration and every code path that writes its tables. For each suspected defect, write a test, run it on PostgreSQL 17 against the **unchanged** code to reproduce it, fix it, then re-run on a fresh database.

## 2. Findings

Severity is the reviewer's assessment for owner review.

| ID | Severity | Finding | Spec | Location | Status |
|---|---|---|---|---|---|
| **R039-01** | **High** | A policy decision and a concurrent revocation of its grant could interleave so that a **PERMIT committed after the revocation had committed**. `_decide` read the grant without a lock | IAM-REQ-036 ("current role/grant/revocation state MUST still be evaluated at commit time"); IAM-REQ-015 | `app/routers/ai.py` `_decide` | **Fixed**: reproduced, then passing |
| **R039-02** | **Medium** | `ai_authority_grant.authority_level` accepted any string at the database. One out-of-vocabulary row (e.g. `A9`, written by any runtime path in the tenant) made **every decision for that agent and action fail** with `422 {"detail":"'A9' is not in list"}`: a decision denial of service that also leaks internals | ZA-DOM-001 §10.2 ("A0–A4 is vocabulary for the authority level"); IAM-REQ-030 | Migration 0011 | **Fixed**: reproduced, then passing |
| **R039-03** | **Medium** | The runtime role held INSERT on the registry and both contract-version tables, and RLS allowed it in a platform-principal context. No runtime code creates these rows (only the seed, which runs as the migration role). So direct SQL in a platform context could **create a CERTIFIED connector or contract version with no matrix check and no history** | DAT-REQ-027 (least privilege); ZA-DAT-001 §13; FR-CON-002; IPE-REQ-028 | Migrations 0012, 0013 | **Fixed**: reproduced, then passing |
| **R039-04** | **Low** | Grant and decision request fields had no length limits. Oversized values reached the database: PostgreSQL returned **500** (truncation error), while SQLite stored them silently | API-REQ-033 ("API input parsers MUST enforce … field length … limits") | `app/routers/ai.py` `GrantIn`, `DecisionReq` | **Fixed**: reproduced, then passing |
| R039-05 | Low (for 0011–0013) | The 0011–0013 paths emit only catalogued names (`authority_grant.issued`, `authority_grant.revoked`, `connector.certification_changed`). **Codebase-wide, 17 emitted names appear nowhere in ZA-EVT-001** (e.g. `evidence.conflict_opened`, `exception.requested`, `ipe.evaluated`, `subject.registered`); all are outside 0011–0013 | EVT-REQ-008 | Other modules (list in the regression test) | **Ratchet added** (no new uncatalogued name can appear). **Mapping is the EVT owner's** |
| R039-06 | Medium | **Event coupling is not enforced by the database.** A context allowed to change a governed status column directly in SQL can do so without writing the matching history row: tenant runtime for grant `status`, a platform principal for registry or contract `certification`. Only the application guarantees history | IAM-INV-09; DAT-REQ-037 (by intent) | 0010–0013 design (same class as 0010's open item) | **Open**: needs a design decision (deferred constraint triggers, or SECURITY DEFINER writers). Owner: Data Architect + Security |
| R039-07 | Low | **History-chain forgery by a platform principal.** In a platform context, direct SQL can insert a certification-history row with any `prior_event_id`, creating a fork, a second root or a dangling link. The application serialises writers with row locks | DAT-REQ-037; IPE "traceable" | 0012 history | **Open**: UNIQUE(`prior_event_id`) plus a per-subject root index would stop forks but not forged rows. Decide together with R039-06 |
| R039-08 | Low | **No referential integrity for history subjects.** `integration_connector_certification_event.connector_id` and `subject_id` have no foreign key | DAT-REQ-026 | 0012/0013 history | **Open**: a foreign key on `connector_id` is possible; `subject_id` is polymorphic. Data Architect |
| R039-09 | Low | `fail_mode` on a grant is free text and is passed to the enforcement runtime in the signed bundle. No controlled code list exists | API-REQ-038 (customer-configured fail-open/closed); no GLS codes | `GrantIn.fail_mode` | **Open**: needs GLS codes; no vocabulary invented |
| R039-10 | Low | Grant `status` has no database CHECK; only `ACTIVE` and `REVOKED` are used. No controlled code list exists | GLS (absent) | 0011 | **Open**: IAM/GLS owner |
| R039-11 | Informational | **Signed policy bundles** stay valid for 15 minutes after issue, so a bundle fetched before a revocation can still permit offline until it expires | IAM-REQ-015 ("within the defined revocation SLO"); ARCH §12.2 (short-validity bundles) | `app/routers/ai.py` `bundle` | **Open**: the revocation SLO is not numerically defined. IAM Lead |
| R039-12 | Informational | The tenant predicate on `ai_authority_grant_event` (as on every tenant table since 0007) includes `OR assure_system()`, so a signed system-scope context reads and writes every tenant's rows. Shown in the boundary matrix (`grant.update_status`: system job `ok:1`) | DAT-REQ-003; I-01 remainder | 0011 (convention from 0007) | **Open**: Stage 2a system-job design (Data Architect) |
| R039-13 | Informational | **The database sees an AI agent as a tenant principal.** Agent refusal (INV-01, `AGENT_WITHOUT_GRANT`) is application-only | IAM-REQ-002; SEC-INV-03 | `app/security.py` | Documented; agent identities are not yet bound to grants (IAM-REQ-031) |

**Verified correct** (no finding), each with test evidence (§5):
- **0011:**
  - tenant isolation of grants and events (RLS, FORCE, WITH CHECK);
  - human-only issue and revoke (SERVICE and AGENT get 403);
  - REVOKED is final for every role;
  - append-only events and receipts;
  - idempotent, row-locked revocation;
  - atomic writes (an injected failure leaves nothing).
- **0012:**
  - only a verified platform principal (not system scope) can change certification, and tenant contexts cannot;
  - certification and runtime health are kept separate (no outbox event for health);
  - history is append-only for every role;
  - linear chains under concurrency;
  - atomicity.
- **0013:**
  - `(connector, version)` uniqueness;
  - foreign keys to the connector and from source runs;
  - immutable contract fields;
  - GLS-only status;
  - per-subject history;
  - endpoint authorization;
  - matrix gate;
  - atomicity;
  - concurrency.

## 3. Database privilege boundary

What the database enforces for the application runtime role `assure_app` (via `assure_app_rw`), by context. This is from `test_r039_cross_migration_privilege_boundary`, after the fixes:

| Statement | Tenant user / AI agent | Platform user | System job | No context |
|---|---|---|---|---|
| Grant: update `status` | allowed (own tenant) | 0 rows | **allowed (any tenant: R039-12)** | 0 rows |
| Grant: update other columns, delete | privilege denied | denied | denied | denied |
| Grant events, decision receipts: update, delete | denied | denied | denied | denied |
| Registry: update `certification` / `runtime_health` | 0 rows | allowed | 0 rows | 0 rows |
| Registry: update other columns, delete, **insert** | denied | denied | denied | denied |
| Certification history: update, delete | denied | denied | denied | denied |
| Contract version: update `status` | 0 rows | allowed | 0 rows | 0 rows |
| Contract version: update other columns, delete, **insert** | denied | denied | denied | denied |

**Table owner** (`assure_table_owner`, reached through the migration login): the row triggers refuse a real change to protected columns and deletes. Tested here on nine representative statements across all three migrations, and for every protected table by the generated `test_immutability` owner probes.

**What the database cannot guarantee**:
- A **PostgreSQL superuser**, or any role with BYPASSRLS, ignores RLS and can alter or drop anything.
- The **table owner / migration role** can `ALTER TABLE … DISABLE TRIGGER`, drop policies or re-grant privileges with DDL (audit finding A10, open). `migrator_all` gives it every row.
- Within an **allowed** context, the database does not check *which role* the principal holds: a platform principal without `certify_connectors` passes the RLS predicate; only the application checks the role. It also does not tie a status change to its history row (R039-06).

These limits are inherent to running as the owner or superuser, or are open design decisions. No test claims otherwise.

## 4. Unresolved decisions (unchanged by this review)

- **D1:** certification vocabulary and lifecycle, terminal states, renew/reinstate/expiry.
- **D2-e…i:** build identity and hash/range, envelope composition (IPE-ADR-05, unapproved), registry ↔ version relationship, effect on runs, per-version evidence.
- **D3:** certification role template. **D4:** confirm the global-table model. **D5:** health/availability events. **D6:** reason codes. **D7:** stale-request refusal.
- **G-08:** history read authorization. No endpoint was added.
- **New from this review:** R039-06…13 (owners in §2).
- **The DAT-REQ-039 security/data/QA review** by its owners, of 0011–0013 including the corrections below.

## 5. Test evidence

From [dat-req-039-remediation-report.md](dat-req-039-remediation-report.md) §4, where the counts are recorded.
