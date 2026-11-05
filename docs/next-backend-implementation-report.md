# Zoiko Assure — Next Backend Implementation: Agent Authority Grant History (Stage 3c)

> **Date:** 3 November 2026. **Branch:** `naveen-changes` at `f5220e9` (pushed). All changes below are in the working tree only: nothing was staged, committed, pushed or pulled.
> **Database:** the canonical `assure` database was **not** changed. It is at 0010; the code head is now 0011, so the local API refuses to start until `assure` is upgraded (§6).
> **Governance:** every ZA-* specification is "Proposed for lock" and no ADR in ZA-ADR-001 is approved. Nothing here is policy approval. The event-table convention used (from 0008) still awaits the ZA-TIM/STA/DAT owners' agreement (roadmap Stage 3 prerequisite).
> **Specifications** were read from the five controlled volumes (raw text extraction). Each requirement ID below was checked against that text, not taken from code comments.

## 1. Selected task and reason

**Task:** Roadmap Stage 3c, the **agent authority grant** part: append-only grant history, an audited and outboxed revocation, a database-guarded grant projection, and immutable decision receipts (migration 0011).

**Reason.** Stage 3c is the earliest roadmap stage with unfinished, unblocked scope, and the brief forbids skipping unresolved Stage 3c security work. Its remaining items are authority grants, connector certification and catalogue lifecycle. Authority grants came first because the live database showed a concrete security defect (checked read-only on `assure` at 0010):

- The runtime role held table-wide `UPDATE` and `DELETE` on `ai_authority_grant`, with no row trigger. A revoked agent grant could be set back to `ACTIVE`, have its level, scope or expiry widened, or be deleted, with no record.
- `POST /ai/grants/{id}/revoke` overwrote `status` and wrote **no audit record and no outbox event**.
- Issuing emitted `agent.grant_issued`, a name not in the ZA-EVT-001 catalogue.
- `ai_policy_decision_receipt` (the PDP's evidence receipt) was also fully mutable.

The grant table is tenant-scoped, so it fits the existing RLS pattern. Connector certification is a global registry with no tenant and needs a different design.

## 2. Specification references

| ID / section | Requirement (specification text, abridged) |
|---|---|
| IAM-INV-09 | Authorisation policies and role/grant changes are versioned and auditable; policy evaluation never depends on AI/model output |
| IAM-REQ-030 | Agent authority MUST be explicit grants per action class + scope + expiry + authority level |
| IAM-REQ-015; IAM-INV-07 | Revoking a grant MUST invalidate new authorisation immediately |
| ZA-IAM-001 §05 | Reactivation: prior privileged grants are not automatically restored |
| ZA-IAM-001 §16 | Agent decision: resolve AgentAuthorityGrant → PDP (grant version) → evidence receipt with agent principal + grant ID; "instant revoke" |
| ZA-IAM-001 §21 | `authority_grant.issued / revoked`: grant, action/authority, scope, expiry, grantor/approver |
| ZA-EVT-001 catalogue | `authority_grant.issued`, `authority_grant.revoked`, `authority_grant.expired` (IAM; WEBHOOK_ELIGIBLE/admin) |
| EVT-REQ-001; DAT-REQ-014 | Domain events through the transactional outbox, in the same PostgreSQL transaction as the state change |
| EVT-REQ-008 | Every event type has one owning context and a registered schema |
| EVT-REQ-025 | Payloads minimised; no unnecessary customer free text |
| DAT-REQ-037 | Ordinary workflows MUST NOT hard-delete governed history |
| SEC-REQ-029 | Completed records keep their immutability protections; ordinary security/admin roles MUST NOT rewrite them |
| DAT-REQ-003; DAT-REQ-027; ZA-DAT-001 §12–13 | FORCE RLS; least privilege; fixed `search_path`; no runtime TRUNCATE |
| DAT-REQ-024 | Backfills MUST NOT fabricate historical Assure record time |
| DAT-REQ-028; SEC-REQ-008 | Cross-tenant tests over read/write paths; isolation enforced independently per layer |
| IAM-REQ-033; INV-01 | No workload credential (or agent) performs HUMAN_ONLY actions |
| DAT-REQ-039 | Schema changes affecting immutable tables or RLS need security/data/QA review |

## 3. Dependency assessment (workstreams A–E)

| Workstream | Status | Basis | Eligible now? |
|---|---|---|---|
| **A. Stage 3c closure** | PARTIAL | Six event tables (0008) and projection guards (0010) exist. Authority grants, connector certification and catalogue lifecycle have no event history. Baseline attribution needs a decision (Data Architect). Release-by-`hold_id` is an API change needing agreement | **Yes**: authority grants (this slice) |
| **B. Stage 3d** cutoff-aware reads | MISSING / BLOCKED | Roadmap Stage 3 prerequisite **G-07** (reason code for pre-migration history) is open. Changing `compute_snapshot` output for existing data needs an ADR (STA-REQ-032) | No |
| **C. Stage 3e** full-hash reproduction | BLOCKED | **TIM-ADR-04** (snapshot canonical manifest format) is an open ADR in ZA-TIM-001; STA-REQ-032 | No |
| **D. Stage 8a** outbox relay | MISSING / BLOCKED | No relay code: `platform_outbox_event.published_at` is never set and there is no `app/workers/`. Prerequisites **G-09** (workflow runtime and hosting) and a queue product decision are open | No |
| **E. Stage 4 closure** | PARTIAL / BLOCKED | 4c (engagements, SERVICE narrowing) needs **G-08** (IAM Lead). The PRD/IAM role-template conflict is unresolved | No |

## 4. Previous implementation state

| Requirement | Before (0010) | Classification |
|---|---|---|
| IAM-INV-09 grant changes versioned/auditable | No grant history; issue wrote outbox only; revoke wrote nothing | MISSING |
| IAM-REQ-015 immediate revocation | The PDP read `status`, so revocation stopped the next decision, but the row could be set back to ACTIVE | PARTIAL |
| IAM §21 audit content | No audit record for issue or revoke | MISSING |
| ZA-EVT-001 event names | `agent.grant_issued` (unregistered); none for revoke | SPEC VIOLATION |
| DAT-REQ-037 / SEC-REQ-029 on grants and receipts | Runtime UPDATE/DELETE, no triggers | MISSING |
| IAM-REQ-030 grant tuple | Action class, scope, expiry and level per grant; A4 refused | IMPLEMENTED (unchanged) |
| IAM-REQ-033 humans only | `authorize("grant_authority", human=True)`; agents refused (INV-01) | IMPLEMENTED (now tested for this route) |

## 5. Exact files changed

| File | Change |
|---|---|
| `backend/migrations/versions/0011_authority_grant_events.py` (new) | Event table and its hardening; guards on the grant table and receipts; downgrade (§6) |
| `backend/app/models.py` | New `AuthorityGrantEvent`; comment on `AuthorityGrant.status` |
| `backend/app/engines/governance.py` | New section 7: `issue_authority_grant`, `revoke_authority_grant` and helpers |
| `backend/app/routers/ai.py` | `POST /ai/grants` and `POST /ai/grants/{id}/revoke` delegate to the engine; optional strict `RevokeIn {reason}` |
| `backend/tests/test_authority_grant_events.py` (new, 14 tests) | §9 |
| `backend/tests/test_immutability.py` | Reviewed classification: `ai_authority_grant` GUARDED {status} with a completion probe; `ai_authority_grant_event` APPEND_ONLY; `ai_policy_decision_receipt` IMMUTABLE |
| `backend/tests/test_migrations.py` | `ai_authority_grant_event` registered as introduced by 0011 |
| `backend/tests/test_tenant_security.py` | Reviewed tenant-table count 50 → 51 |
| `.github/workflows/ci.yml` | `--require test_authority_grant_events=14` |
| `docs/database-migrations.md` | Revision 0011 row |
| `docs/backend-architecture-roadmap.md` | Stage 3c authority-grant status row |
| `docs/next-backend-implementation-report.md` (new) | This report |

## 6. Database migration 0011

**Spec requires:** DAT-REQ-037, SEC-REQ-029, IAM-INV-09, IAM §05, DAT-REQ-003, DAT-REQ-027, DAT-REQ-024, DAT-REQ-039.

**Code does.**
1. **`ai_authority_grant_event`** (new, tenant-scoped). Columns: grant, tenant, agent subject, event type (`ISSUED` / `REVOKED`), from/to status, action class, authority level, scope, expiry, actor, reason and `record_seq`. Owner `assure_table_owner`; ENABLE + FORCE RLS; the 0007 `tenant_isolation` predicate and `migrator_all`; runtime `SELECT, INSERT` only; `append_only` trigger.
2. **`ai_authority_grant`:** `REVOKE UPDATE, DELETE` from `assure_app_rw`, then `GRANT UPDATE (status)`. `immutable_columns` (0003 function: every other column immutable, DELETE refused, for every role) and `lifecycle_final` (0010 function: `REVOKED` is final).
3. **`ai_policy_decision_receipt`:** `REVOKE UPDATE, DELETE`, plus `append_only`. No code path updates a receipt.
4. **No backfill.** Existing grants have no `record_seq`, so an ISSUED event built from them would fabricate record time (DAT-REQ-024). Their history starts with the first post-0011 event. This also avoids adding to the open 0008 baseline-attribution decision.
5. **Downgrade** restores 0010 exactly. It drops the event table, losing post-migration history; acceptable only before production.

**Verification.** A fresh build reaches head (CI migration step) and `check` passes. `test_migrations` (16) runs the newest-revision downgrade/upgrade with a fingerprint comparison, fresh install and adoption, and now includes 0011. `test_immutability` (7) generates the privilege and trigger check over every table and covers downgrade to the baseline.

**Gap.**
- DAT-REQ-039 requires a security/data/QA review of 0011 before it is applied outside development. None is recorded.
- The owner role can still disable triggers with DDL (A10).

**Recommendation.** Review 0011. To run the API locally again, take a verified backup of `assure` and run `python -m app.migrate upgrade` (needs your approval; not done).

## 7. API and domain behaviour

**Code does.**

| Operation | Behaviour |
|---|---|
| `POST /ai/grants` (HUMAN, `grant_authority`) | Writes, with one `record_seq` in one transaction: the grant (ACTIVE), an `ISSUED` event, an audit record `authority_grant.issued` (actor = grantor; reason carries action class, authority, scope and expiry, per IAM §21) and an outbox event `authority_grant.issued`. The outbox payload is `{grant_id, agent_subject_id, authority_level, expires_at}`: action class and scope are free text in this version, so they stay out (EVT-REQ-025). Response unchanged |
| `POST /ai/grants/{id}/revoke` (HUMAN, `grant_authority`) | Optional body `{reason}`, strict (unknown fields → 422). It locks the row (`SELECT … FOR UPDATE`), sets REVOKED, and writes a `REVOKED` event, an audit record `authority_grant.revoked` (prior/new, IAM §21 content plus reason) and an outbox event `authority_grant.revoked` (identifiers only, no reason text). The next `POST /ai/decisions` returns DENY |
| Repeat revoke | **Idempotent:** 200 with the revoked grant; no second event, audit or outbox row. A retry after an unknown outcome is safe |
| Another organisation's grant | 404, exactly like a missing grant; nothing written |
| SERVICE or AGENT caller | 403; nothing written |

**Behaviour changes for clients.**
- A revoke body with unknown fields is now 422. The existing frontend sends no body and is unaffected.
- The outbox event name changed from `agent.grant_issued` to the catalogue name `authority_grant.issued`. No consumer exists yet (there is no relay), and existing outbox rows are not rewritten.

**Gap.**
- No `authority_grant.expired` event: it needs a scheduler (Stage 8). The PDP already denies expired grants.
- No read endpoint for grant history, because adding one means a new entry in the provisional read matrix (G-08). History is visible today through the audit log.
- The demo seed inserts its one grant directly, with no event (documented exception in the grep-gate test).
- No grant version or ETag (Stage 5b).

## 8. Security and tenant-isolation impact

| Check | Result |
|---|---|
| Runtime role on `ai_authority_grant` | SELECT, INSERT, UPDATE(status) only; no DELETE, no TRUNCATE |
| Revoked grant | Cannot be reactivated by any role (`lifecycle_final`); scope, level and expiry can't be changed by any role |
| Event and receipt tables | Append-only for every role, owner included |
| FORCE RLS on all three tables | Verified |
| Cross-tenant (API) | Revoke and issue across organisations return 404; the database fingerprint is unchanged |
| Cross-tenant (database, as the runtime role) | Another tenant's context sees 0 grants and 0 events, updates 0 rows, and an insert into another tenant is refused by WITH CHECK; no context sees nothing |
| Concurrency | Two simultaneous revocations produce exactly one REVOKED event |
| Atomicity | An injected outbox failure leaves no projection change, event, audit or outbox row (both issue and revoke) |

No privilege was widened. The runtime role lost UPDATE and DELETE on two tables, and lost UPDATE on every grant column except `status`.

## 9. Tests executed and results

All runs used an exported copy of exactly the files a commit would contain, taken from the working tree. PostgreSQL ran in a throwaway PostgreSQL 17.11 cluster set up with CI's own bootstrap script; the cluster was deleted afterwards. The canonical `assure` database was only read (`status`: still 0010).

| Run | Result |
|---|---|
| `backend-unit` (SQLite, full suite + gate `--allow-skip-reason PostgreSQL-only`) | **183 passed, 100 skipped** (all `PostgreSQL-only`), 0 failed; **gate PASS** |
| `backend-postgres` migration step (empty database: `status` exit 1, upgrade, upgrade again, `status`, `check`) | Built to **0011**; the repeated upgrade was a no-op; **check passed** |
| RLS gate (`test_rls` 5, no skips) | **PASS** |
| Full suite on PostgreSQL 17 + gate (`--no-skips`, every `--require` in `ci.yml`) | **283 passed, 0 failed, 0 skipped; gate PASS**. Includes `test_authority_grant_events` 14/14, `test_immutability` 7, `test_migrations` 16, `test_tenant_security` 49, `test_authorization` 22, `test_governance_projections` 8 |
| `repo_hygiene.py`, `git diff --check` | PASS |

`test_authority_grant_events.py` (14):
- **Both databases (9):**
  - issue writes projection, event, audit and outbox with one `record_seq`;
  - revocation is recorded atomically and the next decision is DENY;
  - repeat revocation is idempotent;
  - the revoke body is strict;
  - another organisation gets 404 and the database fingerprint is unchanged;
  - SERVICE and AGENT callers get 403 and the fingerprint is unchanged;
  - an injected outbox failure leaves nothing, for issue and for revoke (2);
  - grep gate (event writer only; no unregistered event name).
- **PostgreSQL only (5):**
  - privileges, triggers and FORCE RLS;
  - a revoked grant can't be reactivated, widened or deleted (runtime role and owner role);
  - an active grant changes only through `status`;
  - tenant isolation below the API (another tenant, no context, WITH CHECK);
  - two concurrent revocations produce exactly one event.

**Defects found and fixed during the work.**
- **Test-order coupling.** My first version reused `tenant-authz-b`, which made `tests/test_authorization.py` fail (5 tests): its fixture creates its principals only when that tenant doesn't exist yet. The module now uses its own `tenant-grant-b`; `test_authorization.py` is unchanged.
- **Reviewed inventory.** `test_tenant_security`'s reviewed tenant-table count had to include the new table (50 → 51). That is a deliberate review step, not a loosened check.

**Not run.** Frontend checks (no frontend file changed); a GitHub Actions run (needs a push, which you control); migration 0011 on a restored copy of `assure` (the table holds 1 grant, and 0011 changes no data).

## 10. Traceability

**Spec requires.** TRC-REQ / QA-REQ-001: every changed behaviour maps to requirement → implementation → test → release evidence. The brief asks for a CI gate that fails closed on unresolved traceability edges.

**Code does.** The repository has **no traceability registry** (`governance/` does not exist; roadmap Stage 9 not started). The convention is requirement IDs in test and migration docstrings, plus `--require` counts in the CI gate.

| Requirement | Implementation | Verification |
|---|---|---|
| IAM-INV-09, IAM §21, EVT catalogue, DAT-REQ-014 | `governance.issue_authority_grant` / `revoke_authority_grant`; `routers/ai.py` | `test_issuing_a_grant_records…`, `test_revocation_is_recorded_atomically…`, `test_a_failed_write_leaves…[issue,revoke]` |
| IAM-REQ-015, IAM §05 | Revoke; `lifecycle_final` | `test_revocation_is_recorded…` (DENY), `test_a_revoked_grant_cannot_be_reactivated…` |
| Idempotent retry | `revoke_authority_grant` | `test_revocation_is_idempotent…`, `test_concurrent_revocations…` |
| DAT-REQ-037, SEC-REQ-029, DAT-REQ-027 | 0011 guards | `test_runtime_privileges_triggers_and_force_rls`, `test_immutability` (generated) |
| DAT-REQ-003, DAT-REQ-028, SEC-REQ-008 | 0011 RLS | `test_grant_history_is_tenant_isolated…`, `test_another_organisation…`, `test_tenant_security` inventory |
| API-REQ-014 | `RevokeIn` | `test_revoke_body_is_strict` |
| IAM-REQ-033, INV-01 | `authorize(..., human=True)`, agent gate | `test_workloads_and_agents_cannot_issue_or_revoke` |
| EVT-REQ-008, Stage 3 grep gate | Event writer only | `test_grant_status_is_written_only_by_the_event_writer` |

Release evidence: the CI gate `--require test_authority_grant_events=14`, failing on any skip in the PostgreSQL job.

**Gap.** There is no machine-readable registry and no gate on unresolved edges, so stale evidence isn't invalidated automatically. **Recommendation:** Stage 9 (9b/9c), as its own workstream.

## 11. Remaining gaps and blocked decisions

| # | Item | Owner / blocker |
|---|---|---|
| 1 | Security/data/QA review of 0011 (DAT-REQ-039) | Security, Data Architect, QA |
| 2 | Event-table convention agreement (Stage 3 prerequisite), which covers 0008 and this table | ZA-TIM/STA/DAT owners |
| 3 | `authority_grant.expired` emission | Stage 8 scheduler; G-09 |
| 4 | Grant-history read endpoint and its read class | G-08 (IAM Lead) |
| 5 | Agent identities aren't bound to agent subjects, so the agent action gate refuses all AGENT actors (`security.py`) | IAM design (IAM-REQ-031) |
| 6 | Authority vocabulary: ZA-IAM-001 §16 lists A0–A3, ZA-DOM/GLS list A0–A4 with A4 excluded from initial scope. The code accepts A0–A3 and refuses A4, which is consistent with both. **Document difference**, not resolved here | GLS / IAM owners |
| 7 | `action_class` is free text, though IAM §16 describes a typed action (e.g. `PREPARE_FILING_DRAFT`) | IAM / GLS: controlled action-class list |
| 8 | Connector certification and catalogue lifecycle event history (rest of 3c) | Backend; design needed for global (no-tenant) rows |
| 9 | Owner can disable triggers (A10) | Data Architect + Security |
| 10 | The local API is down until `assure` is upgraded to 0011 | You (backup first) |

## 12. Next recommended task

**Stage 3c: connector certification history.** `engines/connectors.py` overwrites `certification` and `runtime_health` in place on a global registry row (`integration_connector_definition`, no tenant). It already writes an audit record and the catalogue event `connector.certification_changed`, but has no append-only history, and the runtime role can rewrite the row. Its isolation design differs (platform-only writer, every tenant reads), so it needs a short design note before code. After that comes catalogue lifecycle events. Stages 3d/3e, 4c and 8a stay blocked on G-07/TIM-ADR-04, G-08 and G-09.
