# Connector Certification History — Implementation Report (Stage 3c)

> **Date:** 3 November 2026. **Branch:** `naveen-changes`, working tree only. Nothing was staged, committed, pushed or pulled.
> **Design:** [connector-certification-history-design.md](connector-certification-history-design.md) (complete, for review).
> **Implementation:** one slice, migration 0012. It is **partial against the brief**: the lifecycle parts that need open decisions were deliberately not built (§10).
> **Governance:** ZA-* specifications are "Proposed for lock". Nothing here is policy or ADR approval.

## 1. Specification references

All are checked against the specification text, with details in design §0:
- **ZA-GLS-001:** §07 connector dimensions; GLS-REQ-017.
- **ZA-PRD-001:** FR-CON-001/002.
- **ZA-ARCH-001 v3.0:** §10.
- **ZA-IPE-001:** "Certification state transitions" table; IPE-REQ-028/029; IPE-ADR-05 (proposed).
- **ZA-SEC-001:** WF-SEC-03.
- **ZA-DAT-001:** §10, §12, §13; DAT-REQ-014/024/027/037/039.
- **ZA-EVT-001:** catalogue entry `connector.certification_changed`; §20 STATE_CHANGED profile; EVT-REQ-001/007/008/025.
- **ZA-IAM-001:** §08 role templates; IAM-INV-05.
- **ZA-SEC-001:** SEC-REQ-029; SEC-INV-03.
- **ZA-API-001:** API-REQ-014/017.

## 2. Existing implementation findings (before 0012)

| Spec requires | Code did | Classification |
|---|---|---|
| One global registry, read by every organisation (GLS §07, DAT §10) | `integration_connector_definition`, global, read by `/api/connectors`, the dashboard and IPE | IMPLEMENTED |
| No CERTIFIED until the matrix passes (FR-CON-002) | 409 `CERTIFICATION_MATRIX_INCOMPLETE` | IMPLEMENTED |
| GLS certification codes (GLS §07) | `codes.assert_code("connector_certification", …)` | IMPLEMENTED |
| Separate dimensions (GLS-REQ-017) | Separate columns; but the audit and the outbox event combined both as `CERT\|HEALTH`, and a health-only change emitted `connector.certification_changed` | PARTIAL |
| Platform-only writes; no tenant writes (IAM-INV-05) | Application check (`certify_connectors`, HUMAN, platform). **The database allowed any runtime context to UPDATE or DELETE the registry** (no RLS, no trigger, table-wide privileges; verified on the live 0010 database) | PARTIAL: application layer only |
| History of governed changes (DAT-REQ-037, SEC-REQ-029; roadmap 3c) | None | MISSING |
| STATE_CHANGED payload (EVT §20) | `{prior: "A\|B", new: "C\|D", reason: <free text>}` | SPEC VIOLATION (profile fields missing; free text) |
| Atomic state, audit and outbox (DAT-REQ-014) | Yes, in one transaction | IMPLEMENTED |
| Lost-update protection (API-REQ-017) | None: read-modify-write without a lock | MISSING |
| Transition rules, terminal states, expiry | Any GLS code to any GLS code | No spec defines them (design D1) |

## 3. Design decisions and their authority

| Decision | Authority |
|---|---|
| The registry stays global; no tenant RLS | GLS §07, DAT §10 (rows belong to no organisation) |
| Database writes only by a verified platform principal (no tenant, an authenticated actor, not system scope) | Existing 0005/0007 platform-row predicates (`assure_platform_reader()`, `assure_system()`); IAM-INV-05; SEC-INV-03. **Flagged for Data Architect confirmation (D4)** |
| Runtime UPDATE only on `certification` and `runtime_health`; no DELETE; `immutable_columns` trigger | The only runtime writer changes exactly those columns (as 0010 did for its tables); DAT-REQ-027, DAT-REQ-037 |
| Separate `from_*` / `to_*` columns per dimension | GLS-REQ-017 |
| Outbox only when certification changes; STATE_CHANGED payload; no free text | EVT catalogue, EVT §20, EVT-REQ-007/008/025 |
| No-op on unchanged target; row lock | DAT-REQ-014; idempotent retry; API-REQ-017 (partly) |
| No backfill | DAT-REQ-024 |
| **No** transition table, terminal-state trigger, renew, reinstate or expiry; **no** history endpoint; **no** new role; **no** precondition API | Not specified (D1, D3, D7) or G-08 (read policy): kept as current behaviour |

## 4. Files changed

| File | Change |
|---|---|
| `backend/migrations/versions/0012_connector_certification_history.py` (new) | History table; RLS, policies, grants and triggers on the registry and history; downgrade |
| `backend/app/models.py` | `ConnectorCertificationEvent` |
| `backend/app/engines/connectors.py` | Row lock, no-op, history row, audit (contract unchanged), outbox only on certification change with the STATE_CHANGED payload; docstring |
| `backend/tests/test_connector_certification_history.py` (new, 21) | §9 |
| `backend/tests/test_immutability.py` | Reviewed classification: registry GUARDED {certification, runtime_health}; history APPEND_ONLY; global probe context for the runtime-role probe |
| `backend/tests/test_tenant_security.py` | Reviewed RLS inventory: the two global tables and their exact policy sets |
| `backend/tests/test_migrations.py` | History table registered as introduced by 0012 |
| `.github/workflows/ci.yml` | `--require test_connector_certification_history=21` |
| `docs/connector-certification-history-design.md` (new) | Design |
| `docs/connector-certification-history-implementation-report.md` (new) | This report |
| `docs/database-migrations.md` | Revision 0012 row |
| `docs/backend-architecture-roadmap.md` | Stage 3c connector-certification status row |

**Unchanged:**
- The route `POST /api/connectors/{id}/certification`: same body, response and status codes.
- The router, the frontend, the seed, and migrations 0001–0011.

## 5. Migration 0012

| Part | Detail |
|---|---|
| New table | `integration_connector_certification_event`: `event_id`, `connector_id`, `build_version`, `from/to_certification`, `from/to_runtime_health`, `actor_id`, `reason` (required), `prior_event_id`, `record_seq`, `created_at`. Owner `assure_table_owner`; ENABLE + FORCE RLS; policies `registry_history_read` (SELECT) and `registry_history_insert` (INSERT), both a verified platform principal; `migrator_all`; runtime SELECT/INSERT; `append_only` trigger |
| Registry | ENABLE + FORCE RLS. `registry_read` (SELECT, `true`); `registry_platform_insert` and `registry_platform_update` (platform principal only); `migrator_all`. `REVOKE UPDATE, DELETE`, then `GRANT UPDATE (certification, runtime_health)`; `immutable_columns` trigger |
| Data | No data change and no backfill |
| Downgrade | Restores 0011 exactly. Verified by `test_migrations` (newest revision downgrade, upgrade, fingerprint) and `test_immutability` (downgrade to baseline, column ACLs) |

## 6. Authorization model

**Application layer (unchanged).** `Requirement(("certify_connectors",), human=True, platform=True)`, checked in the service function.

**Database layer (new).** Writes, and history reads, need a signed context with no tenant, an authenticated actor and no `system` flag. Tenant contexts, anonymous contexts and system jobs can read the registry but cannot change it, forge history or read history.

| Caller | Result | Evidence |
|---|---|---|
| Organisation roles (Organisation Admin, CCO), workloads, agents, an external auditor | 403, nothing written | Tested |
| SUPER_ADMIN (no `certify_connectors`) | 403, nothing written | Tested |
| SERVICE holding the certification role | 403, nothing written | Tested |
| HUMAN `CONNECTOR_CERTIFICATION_MANAGER` | Allowed | Tested |

## 7. Database security controls

Verified as the runtime role, below the API:
- **Registry, platform principal:** can update the two dimensions only. Changing `build_version`, `release_availability`, the matrix or `key` fails with permission denied, and so does DELETE. There is no TRUNCATE.
- **Registry, any other context** (tenant, none, system with or without an actor): UPDATE affects 0 rows. Inserting forged history fails on row-level security, and history is not visible.
- **Owner role:** the triggers refuse rewriting a protected registry column, deleting a registry row, and updating or deleting any history row.
- **FORCE RLS** is on both tables.

The owner can still disable triggers with DDL (A10, open).

## 8. Event catalogue alignment

| Change | Outbox |
|---|---|
| Certification changes | `connector.certification_changed` (catalogued). Payload `{resource_id, prior_state{certification, runtime_health}, new_state{…}, revision_ref, prior_revision_ref, effective_scope{build_version}, reason_codes: []}`. Free-text reason removed |
| Runtime health only | None: no catalogued event (D5). History and audit still record it |
| No change | Nothing |

No consumer exists yet (there is no relay, Stage 8), so the payload change breaks nothing. Existing outbox rows are not rewritten.

## 9. Test results

All runs used an exported copy of exactly the files a commit would contain. Afterwards the tested copy was confirmed identical to the working tree (`app`, `tests`, `migrations`, `ci.yml`). PostgreSQL ran in a throwaway PostgreSQL 17.11 cluster set up with CI's own bootstrap SQL, and was deleted afterwards.

| Run | Result |
|---|---|
| New module on SQLite | 15 passed, 6 skipped (`PostgreSQL-only`) |
| Full SQLite suite + `backend-unit` gate | **228 passed, 113 skipped** (all `PostgreSQL-only`), 0 failed; **gate PASS** |
| CI migration step on an empty database (status, upgrade, repeat, status, check) | Built to **0012**; **check passed** |
| RLS gate | 5/5, **PASS** |
| Full suite on PostgreSQL 17 + gate (`--no-skips`, every `--require`) | **341 passed, 0 failed, 0 skipped; gate PASS**. `test_connector_certification_history` **21/21**; also `test_immutability` 7, `test_migrations` 16, `test_tenant_security` 49, `test_authorization` 22 (its certification tests unchanged and passing) |
| `repo_hygiene.py`, `git diff --check` | PASS |

The 21 tests, against the brief's scenarios:
- **Both databases (15):**
  - Change recorded together (history, audit with the unchanged contract, outbox with the exact STATE_CHANGED payload).
  - Suspension → revocation → recertification as one linked history.
  - A health-only change produces no certification event.
  - Duplicates and retries record nothing.
  - Invalid requests change nothing: matrix FAIL and empty matrix (409); the codes `EXPIRED` and `BETA` (422); a blank reason, an unknown health code or an unknown field (422); an unknown connector (404).
  - Seven unauthorised callers get 403 and the database fingerprint is unchanged.
  - Mid-transition failure in emit or audit leaves nothing (2).
  - No fabricated history: chains are linear and no row has a system actor.
- **PostgreSQL only (6):**
  - Privileges, triggers and FORCE RLS.
  - Tenant, anonymous and system contexts cannot write the registry, forge history or read history.
  - A platform principal changes only the two dimensions and can never delete.
  - History rows can't be rewritten by any role, owner included.
  - Concurrent changes serialise into a linear history.
  - The migration backfills nothing (0011 → 0012 with an existing row).

**Not covered.** Renewal, reinstatement and expiry have no definition in any specification (D1). The "renewal" case is tested only as "re-submitting CERTIFIED records nothing". There is no stale-request refusal (D7) and no history read endpoint (G-08).

**Defect found and fixed during the run.** In my own test, a probe identifier (41 characters) exceeded the 36-character column, so PostgreSQL rejected the insert before the RLS policy was evaluated. Fixed, and the whole suite was re-run on fresh databases.

## 10. Remaining gaps and dependencies

| # | Item | Owner | Effect |
|---|---|---|---|
| D1 | Certification transition table, terminal states (is `REVOKED` final?), renew, reinstate, expiry; resolve vocabulary conflict C-1 (GLS vs PRD/ARCH/IPE/SEC) | GLS and IPE owners, connector governance (ZA-DOC-001 §01) | Not implemented; current permissive rules kept. **The brief's renewal, reinstatement and expiry scenarios are not implemented, because no specification defines them** |
| D2 | Unit of certification (IPE-REQ-028, IPE-ADR-05) | IPE Lead | History records `build_version` only |
| D3 | Platform certification role (missing from IAM §08) | IAM Lead | Provisional role kept |
| D4 | Confirm the global-table database model | Data Architect | Implemented on the 0005/0007 precedent |
| D5 | Events for runtime-health and availability changes | EVT owner | None emitted |
| D6 | Certification reason-code list | GLS owner | `reason_codes: []` |
| D7 | Stale-write precondition (API-REQ-017; Stage 5b ETag / `If-Match`) | API Lead | Row lock only: no silent loss, but no stale-request refusal |
| G-08 | Read policy for certification history | IAM Lead | No endpoint; history visible via the platform audit log |
| — | DAT-REQ-039 security/data/QA review of 0012 | Security, Data Architect, QA | Required before use outside development |
| — | Catalogue lifecycle events (rest of Stage 3c) | Backend | Not started |

## 11. Canonical `assure`

Not migrated or modified. `python -m app.migrate status` (read-only, after the work): `assure@127.0.0.1:5433/assure: at revision 0010; pending 0011, 0012 (head 0012)`. No test or run used its credentials. The local API keeps refusing to start until it is upgraded manually, after a backup.

## 12. Readiness

- **Design:** complete, for review.
- **Implementation:** complete for the decision-free slice, and ready for code review together with the security/data/QA review DAT-REQ-039 requires. **Not complete against the brief's full lifecycle** (D1, D7).
