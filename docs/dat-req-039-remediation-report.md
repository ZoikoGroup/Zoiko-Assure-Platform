# DAT-REQ-039 Remediation — Migrations 0011–0013

> **Date:** 3 November 2026. **Branch:** `naveen-changes`, working tree only. Nothing was staged, committed, pushed or pulled.
> **Findings:** [dat-req-039-review-report.md](dat-req-039-review-report.md).
> **Fixed here:** R039-01…04, plus the R039-05 ratchet. R039-06…13 stay open for their owners.
> **DAT-REQ-039 is not complete.** It requires the security, data and QA owners' review, and this is evidence for that review.
> **`assure`:** not connected to or modified, not even read-only.

## 1. Why the corrections are inside 0011–0013 and not in a new 0014

[database-migrations.md](database-migrations.md), "Writing a new migration", step 7: never edit a revision "that has been applied anywhere outside your machine". Revisions 0011–0013:
- are uncommitted;
- have never been pushed (the pushed head `f5220e9` ends at 0010);
- have never run in GitHub Actions;
- are not applied to `assure` (at 0010, as of the last read on 3 November 2026);
- have only ever been applied to disposable scratch databases that were deleted afterwards.

Correcting them in place is the reviewed correction. A 0014 would ship known-defective revisions and then repair them. Each edited revision states the correction, with its finding ID, in its docstring.

## 2. Fixes

### R039-01 — Decision versus concurrent revocation (High)

- **Spec requires:** IAM-REQ-036, "current role/grant/revocation state MUST still be evaluated at commit time"; IAM-REQ-015, revocation invalidates new authorisation immediately.
- **Code did:** `app/routers/ai.py` `_decide` read the matching ACTIVE grants with a plain SELECT. A revocation could start and commit while the decision was still open, and the decision then committed a PERMIT citing the revoked grant.
- **Reproduction:** `test_r039_01_a_decision_and_a_concurrent_revocation_are_serialised`.
  1. Open a decision transaction and evaluate (PERMIT).
  2. Start a revocation in another thread, before the decision commits.
  3. **Unchanged code:** the revocation committed while the PERMIT was uncommitted (`assert t.is_alive()` failed).
- **Fix:** read the grants `FOR SHARE`, held until the decision commits (`.with_for_update(read=True)`, `app/routers/ai.py:172`).
  - A revocation's `FOR UPDATE` waits for the decision to commit.
  - A decision that meets an in-progress revocation waits, re-reads the row and no longer finds it ACTIVE, so it returns DENY. This follows from PostgreSQL's READ COMMITTED re-check of locked rows; **this ordering is not separately tested**, only the revocation-waits ordering is.
  - The runtime role's `UPDATE (status)` privilege allows the share lock. RLS applies unchanged.
- **Regression:** the same test now passes. The PERMIT commits first, the revocation follows, and the next decision is DENY.

### R039-02 — Authority-level vocabulary (Medium)

- **Spec requires:** ZA-DOM-001 §10.2, "A0–A4 is vocabulary for the authority level on a grant"; IAM-REQ-030.
- **Code did:** the API refused other values, but the database accepted any string. With one `A9` row present, **every** `POST /api/ai/decisions` for that agent and action returned `422 {"detail":"'A9' is not in list"}` (reproduction probe on the unchanged code).
- **Fix:** migration 0011 adds `CHECK ck_ai_authority_grant_level (authority_level IN ('A0','A1','A2','A3','A4'))`, mirrored in `app/models.py`. A4 stays refused by the API (ARCH: "excluded from initial production scope"). The database holds the vocabulary, not the release scope. An upgrade over a database holding another value fails and rolls back; `assure`'s one grant is `A2`.
- **Regression:** `test_r039_02_…`: A0–A4 are accepted; `A5`, `A9`, `a2`, the empty string and `ROOT` are refused by the CHECK, as the runtime role in a tenant context.

### R039-03 — No runtime creation of registry or contract rows (Medium)

- **Spec requires:** DAT-REQ-027 and ZA-DAT-001 §13 (least privilege); FR-CON-002 (no CERTIFIED without the matrix); IPE-REQ-028.
- **Code did:** 0012 and 0013 granted the runtime role INSERT on `integration_connector_definition` and both contract tables, with `registry_platform_insert` / `contract_platform_insert` policies. No runtime code inserts these rows (only the seed, as the migration role). In a platform-principal context, direct SQL could therefore create a CERTIFIED connector or contract version with no matrix check and no history.
- **Reproduction:** `test_r039_03_…`. On the unchanged code, the platform-context INSERT into the registry succeeded (`ok:1`).
- **Fix:** 0012 and 0013 run `REVOKE INSERT, UPDATE, DELETE` (then grant `UPDATE` on the certification columns, as before). The two insert policies are no longer created. The downgrades re-grant INSERT, so 0011 / 0012 are restored exactly.
- **Regression:** `test_r039_03_…`: platform and tenant contexts both fail with privilege denied on all three tables.
  - `test_immutability`: the generated classification now requires these three tables to have SELECT **without** INSERT (`RUNTIME_NO_INSERT`).
  - `test_tenant_security`: the policy inventory now lists them without insert policies.

### R039-04 — Request field lengths (Low)

- **Spec requires:** API-REQ-033, "API input parsers MUST enforce body size, nesting depth, field length and media-type limits".
- **Code did:** `GrantIn` and `DecisionReq` fields were unbounded. On PostgreSQL an oversized `action_class` returned **500 Internal Server Error** (reproduction probe); on SQLite it was stored.
- **Fix:** `Field(max_length=…)` equal to the existing column sizes (`agent_subject_id` 36, `action_class` 120, `authority_level` 8, `scope` 200, `reversibility` 64, `fail_mode` 16; decision fields 36 / 120 / 200). No new limit is invented.
- **Regression:** `test_r039_04_…` (6 fields): 422, and the database fingerprint is unchanged.

### R039-05 — Event catalogue guard (ratchet)

- **Spec requires:** EVT-REQ-008.
- **Code does:** the 0011–0013 paths emit only catalogued names. 17 other emitted names appear nowhere in ZA-EVT-001 (outside scope).
- **Added:** `test_r039_05_reviewed_paths_emit_only_catalogued_events`, and `test_r039_05_no_new_uncatalogued_event_name_appears`. Every emitted name must be on the generated catalogued list or the known-violation list, so a new unregistered name fails CI. **The 17 are not renamed:** mapping them is the EVT owner's decision.

## 3. Other changes

| File | Change |
|---|---|
| `backend/tests/test_dat_req_039_review.py` (new, 13 tests) | R039-01…05; API refusal of agent and workload principals on every reviewed write; the cross-migration privilege-boundary matrix (5 contexts × 15 statements, plus owner probes) |
| `backend/tests/test_immutability.py` | `RUNTIME_NO_INSERT`; the global-table runtime probe uses an existing row (no runtime INSERT); probe value `authority_level = 'A1'` for the new CHECK |
| `backend/tests/test_tenant_security.py` | Policy inventory without the two insert policies |
| `backend/tests/test_connector_certification_history.py` | The registry has no runtime INSERT; history keeps it |
| `backend/tests/test_connector_version_certification.py` | A tenant contract insert is now refused by privilege (it was refused by RLS) |
| `backend/tests/test_migrations.py` | `ck_ai_authority_grant_level` registered as a post-baseline constraint (0011) |
| `.github/workflows/ci.yml` | `--require test_dat_req_039_review=13` |

No correct enforcement was relaxed. Each changed existing assertion now describes a *stricter* boundary (no INSERT) or supplies a value the new CHECK requires.

## 4. Validation

All PostgreSQL runs used fresh databases in a throwaway PostgreSQL 17.11 cluster set up with CI's own bootstrap SQL, deleted afterwards. The full runs used an exported copy of exactly the files a commit would contain, confirmed afterwards to be identical to the working tree (`app`, `tests`, `migrations`, `ci.yml`).

| Run | Result |
|---|---|
| **Reproduction:** `test_dat_req_039_review.py` against the **unchanged** code (fresh database) | **8 failed, 5 passed**. Failed: R039-04 ×5 (the `authority_level` case already passed), R039-02, R039-03, R039-01. Passed: the boundary matrix, both catalogue tests, the agent/workload API refusal, and the `authority_level` length case |
| Reproduction probe (unchanged code) | Oversized `action_class` gave 500; an `A9` row made decisions return `422 'A9' is not in list` |
| `test_dat_req_039_review.py` against the **fixed** code (fresh database) | **13 passed** |
| Full SQLite suite + `backend-unit` gate | **253 passed, 124 skipped** (all `PostgreSQL-only`), 0 failed; **gate PASS** |
| CI migration step on an empty database (status, upgrade, repeat, status, check) | Head **0013**; **check passed** (no model drift; RLS/FORCE/ownership) |
| RLS gate | 5/5, PASS |
| **Full suite on PostgreSQL 17 + gate** (`--no-skips`, every `--require`) | **377 passed, 0 failed, 0 skipped; gate PASS** |

**Migration-specific (PostgreSQL 17):**
- `test_authority_grant_events` 14/14 (0011);
- `test_connector_certification_history` 21/21 (0012);
- `test_connector_version_certification` 23/23 (0013);
- `test_migrations` 16/16: fresh install, legacy adoption with the post-baseline constraints including the new CHECK, newest-revision downgrade and upgrade;
- `test_immutability` 7/7: generated privilege/trigger classification, owner probes, downgrade to baseline;
- `test_tenant_security` 49/49 (policy inventory).

**Cross-migration (PostgreSQL 17):**
- `test_r039_cross_migration_privilege_boundary`: tenant user, AI agent, platform user, system job and no context × 15 statements, all as expected (review report §3), plus nine owner-role probes refused by the triggers.
- `test_r039_agent_and_workload_principals_cannot_reach_any_reviewed_write`: four write endpoints × two principals, 403, database unchanged.

**Iteration during the run.** The first full PostgreSQL run failed one existing generic test, `test_immutability::test_guarded_rows_…`: its generic probe filled `authority_level` with a placeholder that the new CHECK correctly refused. The probe now supplies `A1`; the check is unchanged. The suite was re-run on fresh databases, giving the 377/0/0 above.

**CI differences.** The local runs reproduce the `backend-unit` and `backend-postgres` jobs (same bootstrap SQL, steps and gate arguments) on Windows with PostgreSQL 17.11 and Python 3.12.0. GitHub Actions uses Ubuntu with a `postgres:17` container and a fresh pip resolve. **No GitHub Actions run has happened:** that needs a push, which is yours to make.

## 5. Remaining limitations and owner decisions

- **Open findings (review report §2):**
  - R039-06: event coupling at the database. Data Architect + Security.
  - R039-07: history-chain forgery or forks by a platform principal. Data Architect.
  - R039-08: history referential integrity. Data Architect.
  - R039-09 / R039-10: `fail_mode` and grant-status vocabularies. GLS, IAM.
  - R039-11: policy-bundle validity after revocation (revocation SLO). IAM Lead.
  - R039-12: `assure_system()` in tenant predicates. Stage 2a, Data Architect.
  - R039-13: agent identity at the database. IAM-REQ-031.
- **Unchanged, unresolved:** D1, D2-e…i, D3–D7, G-08, IPE-ADR-05.
- **Database boundary:** PostgreSQL superusers, BYPASSRLS roles and the table owner's DDL (A10) are outside what these controls can guarantee (review report §3).
- **DAT-REQ-039 itself:** the security, data and QA owners' review of 0011–0013 as corrected.
