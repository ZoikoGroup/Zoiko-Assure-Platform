# Task 2 — Authentication, Session Management and Tenant Security: Implementation Report

| Field | Value |
|---|---|
| Date | 31 October 2026 |
| Branch / HEAD | `naveen-changes` / `af1d508` (all Task 2 work is uncommitted, alongside earlier uncommitted work) |
| Scope | Task 2 prompt §1–§16; audit `docs/backend-current-state-and-wireframe-gap-audit.md` §10 and §15 (Task 2) |
| Verdict | **NOT COMPLETE** (see §22). Everything that could proceed without Task 1 and without the open G-05/G-08 decisions is implemented and verified on PostgreSQL 17. The remaining Definition-of-Done items are blocked by named prerequisites. |
| Production readiness | Not claimed. GitHub Actions has not run this work. |

---

## 1. Executive Summary

**What changed (all verified on a disposable PostgreSQL 17.11 cluster and on SQLite):**

- **Outbox defect fixed at both layers.**
  - `GET /api/outbox` now returns only the caller's organisation.
  - Migration `0005` replaces the platform-log RLS policies on `platform_outbox_event` and `platform_audit_event`. A tenant scope can no longer see, insert, update or delete rows that have no tenant, which includes the `security.content_signature_failed` events of a failed publication.
- **Session tokens.**
  - Opaque 32-byte random tokens replace the HMAC tokens that were signed with a key held in a local file.
  - Only their SHA-256 is stored (`auth_session.token_hash`, migration `0006`).
- **Revocation is enforced at commit time.**
  - A request's writes commit only if the session that authorised them is still live.
  - The session row is share-locked (`FOR SHARE`) at commit, so a concurrent logout, password change or suspension either waits for that commit or makes it fail.
- **Sign-in hardening.**
  - A locked account now gets the same generic 401 as an unknown account, after the same work (previously a 423 revealed that the account exists).
  - The failure counter is serialised per credential.
  - Every failure reason is audited (reason class only, never the password).
- **Security audit.**
  - New `authorization.denied` records for every refusal by the single decision point.
  - Failed password changes are recorded, and the number of revoked sessions is recorded.
  - Super Admin registry reads are recorded.
  - Super Admin reads now go through the same audited guard as its mutations.
- **Connection pool.** On check-in, the pool now clears session-level settings and temporary tables (DAT §12 "Connection pooling"). The PostgreSQL 17 probe showed both survived on a pooled connection before.
- **Tests.**
  - 38 new tests: 28 PostgreSQL-only database proofs, plus 10 API/session tests that run on both databases.
  - Two existing tests were adapted without weakening them (§17).

**What is not done, and why:**

- The DAT §13 role model, non-login owner roles, `FORCE` RLS, removal of the owner credential from the API, and the I-01 tenant-context bypass are **Task 1 prerequisites**. None is implemented; there is no Task 1 report and no Task 1 migration.
- I-01 is confirmed on PostgreSQL 17. Any holder of the runtime credential, including SQL injected into a single `SELECT`, can choose any tenant or switch on `app.system` (§9).
- The browser session (BFF cookie instead of `localStorage`; IAM-REQ-018) is blocked on **G-05** and on frontend work, which is out of scope.
- Per-read authorisation is blocked on **G-08**.
- Account and membership lifecycle needs an IAM data model that does not exist yet (IAM §22).

**Test status:**

| Suite | Result |
|---|---|
| SQLite | 129 passed, 62 skipped (PostgreSQL-only), 1 failed |
| PostgreSQL 17, full suite | 191 passed, 1 failed |
| PostgreSQL 17, RLS gate | PASS |
| PostgreSQL 17, migration CLI and `check` | PASS |
| PostgreSQL 17, 0005/0006 downgrade and re-upgrade round trip | PASS |

- The one failure in both suites is the Task 0 test `test_fact_assertion_preserves_non_integrity_evaluation_errors`.
- I showed that this test is **flaky** and unrelated to Task 2. Its pass/fail varies between identical runs, before and after Task 2, on both databases (§17).
- It is not hidden, skipped or weakened. Because of it, the PostgreSQL full-suite gate still fails.

---

## 2. Architecture & Spec Baseline

**Specifications (all "Proposed for … lock"; no approval is inferred, per ADR-REQ-006):**

| Spec | Sections and requirements used |
|---|---|
| ZA-DAT-001 | §12 RLS and tenant context; §13 roles; §16 outbox; DAT-REQ-003/004/005/014/015/027/028 |
| ZA-IAM-001 | §03 fixation and generic errors; §05 sessions and revocation; §19 account safety; §21 security events; §22 `iam.session`; IAM-REQ-001/009/010/014/015/018/019/024/027/037/041/042/043 |
| ZA-SEC-001 | §08 isolation; §09 browser/API security; SEC-REQ-008/009/020/023/031/042/045/047 |
| ZA-TEN-001 | TEN-REQ-021/022 |
| ZA-EVT-001 | §19: SECURITY_CRITICAL events are not customer-visible by default (as recorded in the audit) |

**Architecture gate:**

- ARCH-ADR-003 (register line C-01) says Go is the control-plane runtime.
- The ADR register (ZA-ADR-001) is "Proposed for architecture-governance lock", and its entries read "ACCEPTED* on approval of this register".
- It is therefore **not approved**. The backend stays Python and no second language was introduced.
- The same applies to C-08 (BFF cookie session; IAM-ADR-03/SEC-ADR-07) and DAT-ADR-02 (transaction-local context plus hardened helpers).

**Document conflicts (not resolved silently):**

- The Task 2 prompt names `assure_owner`, `assure_migrator` and `assure_job_owner`.
- DAT §13 names `assure_migrator`, `assure_app_rw`, `assure_app_ro`, `assure_outbox_publisher`, `assure_catalog_loader`, `assure_analytics_ro`, `assure_breakglass_admin`, and non-login table owners.
- As instructed, DAT §13 governs and none of the brief's names was introduced. Neither role list exists in the database (§10).

## 3. Task 1 Dependency Verification

| Prerequisite (Task 1, audit §15) | State found | Effect on Task 2 |
|---|---|---|
| Task 1 report | Not present in `docs/` | Nothing to build on |
| DAT §13 roles (`assure_migrator`, `assure_app_rw`/`_ro`, `assure_outbox_publisher`, …) | Missing. Only `assure` (owner) and `assure_app` (runtime) exist | §6 role work **blocked** |
| Non-login table owners | Missing. `assure` is a **LOGIN** role with CREATEROLE that owns the database and all 208 relations in `public` (tables, indexes, sequences; probe, §18) | `FORCE` RLS and owner hardening **blocked** |
| `FORCE ROW LEVEL SECURITY` | On 0 of 41 RLS tables | **Blocked**: with `FORCE`, the policies (all `TO assure_app`) would deny the owner, which still seeds and bootstraps at start-up |
| System-job design (I-01: `assure_system()` / `app.system`) | Open; Data Architect + Security decision pending (audit §16) | DB-bound tenant context **blocked** |
| Owner credential removed from the API | Not done (`main.py` start-up checks, demo seed, account bootstrap) | **Blocked** (Task 1 step 4) |
| IAM data model (`iam.tenant_membership_revision`, `role_assignment_revision`, …; IAM §22) | Absent. Membership is `org_actor.tenant_id`; roles are a JSON list | Account and membership lifecycle **blocked** |
| Migration `0005` planned by Task 1 | Unused before this task | Task 2 uses `0005`/`0006`, so Task 1 starts at `0007` |

**Proceeded safely** (needs none of the above):
- Outbox/audit policy rewrite (no new role, no ownership change).
- API outbox fix.
- Token hashing.
- Commit-time revalidation.
- Sign-in hardening.
- Denial audit.
- Pool reset.
- Tests.

No schema was invented for blocked work.

## 4. Initial Working-Tree State

**Before any Task 2 edit:**
- Branch `naveen-changes`, HEAD `af1d508`.
- About 70 modified tracked files across backend, CI, docs and frontend (earlier uncommitted work).
- Untracked: `alembic.ini`, `app/migrate.py`, `app/engines/connectors.py`, `migrations/`, five test modules and six docs.
- Migrations 0001–0004 present.

**Preserved untouched:**
- Task 3 files. Their diffstat is unchanged (207 insertions / 67 deletions), and SHA-256 prefixes after Task 2 are:

  | File | SHA-256 prefix |
  |---|---|
  | `app/routers/applicability.py` | `c0a37967b8e83d10` |
  | `app/routers/rules.py` | `3616c426033990d8` |
  | `app/engines/applicability.py` | `cc82d5dc81e53d8b` |
  | `tests/test_rule_integrity.py` | `06a962452095257f` |

- Frontend.
- Every other unrelated file.

**Baselines:**
- SQLite: 154 collected; 119 passed, 1 failed, 34 skipped (audit run, 30 Oct 17:31).
- PostgreSQL: last full run 29 Oct (149 passed).

No `git` write command was run or is suggested.

## 5. Existing Auth Architecture (before Task 2)

| Element | Implementation |
|---|---|
| Sign-in | `POST /api/auth/login`: scrypt (n=2^14, r=8, p=1, 16-byte salt), constant-time compare. Only HUMAN and EXTERNAL_AUDITOR can sign in. Lockout after 5 failures for 15 minutes, answered with **423** (revealed that the account exists). |
| Token | `base64(json{sid, sub, exp}).base64(HMAC-SHA256)`, keyed by `var/keys/session.key` (a plaintext file the API created on first use) |
| Session | `auth_session` row (8 h, `revoked_at`), checked at the **start** of each request |
| Context | `get_ctx` → `resolve_token` → actor → `set_tenant_scope(actor.tenant_id)` → organisation must be ACTIVE → `must_change` gate. `X-Actor-Id` is honoured only when `ZA_DEV_IDENTITY_HEADER=1`. |
| Authorisation | Static `ROLE_PERMISSIONS`; one decision point `Ctx.check`; `authorize()` route guard; start-up refuses any unguarded mutation |
| Tenant scope | `db.py` `after_begin` stamps `set_config('app.tenant_id', …, true)` and `app.system` on every transaction; 41 `tenant_isolation` policies `TO assure_app`; `system_scope` sets `app.system=on` |
| Super Admin | Tenant-less `SUPER_ADMIN` holding only `manage_organisations`. Reads used a separate check (`_platform_admin`). |
| Audit | Append-only `platform_audit_event`: `auth.login`, `auth.login_failed` (known accounts only), `auth.logout`, `auth.password_changed`, `organisation.*`. **No** `authorization.denied`. |

## 6. Authentication Changes

| File / function | Spec | Before | After | Security reason | Test evidence |
|---|---|---|---|---|---|
| `app/auth.py` `login` (lockout) | IAM §03; IAM-REQ-043 | Locked account → 423 "Try again after HH:MM", with no scrypt work | Same generic 401 as an unknown account; dummy scrypt to equalise timing; reason `LOCKED` audited | A 423 after 5 guesses revealed that the account exists and that it is locked | `test_api.py::test_agents_cannot_sign_in_and_lockout` (correct password refused while locked; response identical to an unknown account; audit shows `BAD_PASSWORD`/`LOCKED` then `LOCKED`) |
| `login` (counter) | IAM §03 lockout; DAT-REQ-005 (serialised mutation) | Read-modify-write of `failed_attempts` without a lock | `SELECT … FOR UPDATE` on `auth_credential` before the check | Parallel guesses could all read the same count and outrun the lockout | Exercised on every sign-in in both suites; PostgreSQL locks are effective (§18) |
| `login` (failure audit) | IAM §21 `identity.authentication_failed`; IAM-REQ-043 | Audited only wrong passwords on existing interactive accounts | `auth.login_failed` for `UNKNOWN_ACCOUNT` (pseudonymous `email-sha256:` reference, email not stored), `NOT_INTERACTIVE`, `NO_CREDENTIAL`, `LOCKED`, `BAD_PASSWORD` (+ `new_state=LOCKED` when it triggers the lock) | Credential stuffing against unknown accounts was invisible | `test_auth_session_security.py::test_failed_sign_ins_get_one_generic_answer_and_a_reason_in_the_audit_only` (one answer for all cases; the passwords tried never appear in any `auth.*` row) |
| `change_password` | IAM-REQ-015; IAM §19 | Wrong current password not audited; credential not locked; revoked count unrecorded | `FOR UPDATE` on the credential; `auth.password_change_failed` (`BAD_CURRENT_PASSWORD`); `auth.password_changed` records "N other session(s) revoked" | Investigating account takeover needs the failed attempts and their effect | `test_password_change_failures_and_revocations_are_audited` |

**Not changed (no invented policy):**
- scrypt cost and password rules.
- MFA/OIDC/SAML/SCIM (blocked on G-05).
- Account states such as disabled or unverified (no per-account status exists; IAM §22 model needed).
- Password reset (no reset flow is specified for local accounts beyond admin-issued temporary passwords, which already exist).

## 7. Session Lifecycle Changes

| File / function | Spec | Before | After | Security reason | Test evidence |
|---|---|---|---|---|---|
| `app/auth.py` `issue_token` / `resolve_token`; `models.AuthSession.token_hash`; migration `0006` | IAM §05, §22; Task 2 §3 "store only token hashes" | HMAC token rebuildable from DB data (`sid`, `actor_id`, `expires_at`) plus a plaintext key file | 32 random bytes (`token_urlsafe`); only `sha256(token)` stored (unique index); lookup by hash; token length capped | A database plus host-file leak no longer yields live tokens; removes the session key file (a G-06 weakness) | `test_only_a_hash_of_the_session_token_is_stored`, `test_forged_unknown_old_format_and_revoked_tokens_are_refused` |
| `login` | IAM §03 fixation | New session per sign-in (kept) | Unchanged, now explicit | — | `test_every_sign_in_gets_a_new_session_and_token` |
| `app/security.py` `_revalidate_session` (`before_commit` on `SessionLocal`); `get_ctx` records `auth_session_id` | IAM-REQ-014, IAM-REQ-015 | Session checked only when the request started | Before every commit of a token-authenticated request, the session row is re-read `FOR SHARE`; revoked or expired → 401 and nothing commits | A logout, password change or suspension that lands mid-request could not stop that request's writes | SQLite and PostgreSQL: `test_writes_authorised_by_a_session_revoked_before_commit_are_refused`. PostgreSQL: `test_a_revocation_waits_for_an_in_flight_commit_and_the_next_commit_is_refused` (the concurrent revoke hits `lock_timeout` until the in-flight commit finishes; the next commit gets 401; only the first write survives) |
| `app/db.py` `_reset_on_checkin` (pool `reset` event) | DAT §12 "Connection pooling"; DAT-REQ-005 | Pool only rolled back | Also `RESET ALL` and `DISCARD TEMP` | The probe showed a session-level `set_config` and a TEMP table shadowing `platform_audit_event` survived on the pooled connection | `test_nothing_set_on_a_connection_survives_its_return_to_the_pool` |

**Evaluation (Task 2 §3):**

| Concern | Finding |
|---|---|
| Fixation | No client-supplied session ID; a new random token at every sign-in |
| Replay | A bearer token is replayable until expiry or revocation (no sender binding). Residual: BFF/cookie or sender-constrained tokens need G-05. |
| Concurrent sessions | Unlimited. No spec limit exists, so none was invented. |
| Revocation consistency | Linearised against commits (above). Reads in flight at revocation still complete; the IAM §05 SLO (≤5 min) is met immediately for new requests. |
| Refresh rotation / logout–refresh races | Not applicable: there are no refresh tokens. IAM-REQ-019 applies "where token form permits". Concurrent logouts of one session are safe (plain row updates, no lock upgrade). |
| Password change | Revokes every other session; the current one stays (existing behaviour, test unchanged) |
| Admin revocation | Organisation suspension revokes all its sessions (existing). There is no per-user admin revocation API: user-management APIs and the IAM data model are not built (§20). |
| Expiry | Absolute 8 h (`ZA_SESSION_HOURS`). No idle timeout is specified, so none was invented. The IAM §05 5–15 min applies to access tokens (G-05 design). |
| Storage in the browser | Still `localStorage` (IAM-REQ-018 / SEC-REQ-020 **open**; G-05 + frontend) |
| Upgrade effect | Sessions created before `0006` have no hash, so everyone signs in again |

## 8. Membership & Authorization

**Model in the code:**
- A principal is one `org_actor` row.
- **Membership** is `org_actor.tenant_id`: exactly one organisation, server-side, never taken from the client (IAM-REQ-009).
- **Roles** are a JSON list mapped by the static `ROLE_PERMISSIONS`.
- Organisation status is checked on every request (`tenant_active`).
- There is no generic admin bypass:
  - no role holds all permissions;
  - `SUPER_ADMIN` holds only `manage_organisations`, and it is tenant-less;
  - `PLATFORM_ADMIN` is an organisation role;
  - tenant data needs `ctx.tenant`, which refuses principals without a tenant (INV-14, IAM-REQ-024).

**Role matrix:**

| Role | Tenant | Permissions |
|---|---|---|
| CCO | own organisation | approve_exception, assurance_review, attest, challenge, manage_facts, manage_issues, package, read, request_evaluation, request_exception |
| LEGAL_REVIEWER | own organisation | attest, challenge, legal_disposition, read, request_evaluation |
| REGULATORY_ANALYST | own organisation | challenge, manage_facts, read, request_evaluation |
| CONTROL_OWNER | own organisation | manage_controls, manage_issues, read, request_exception, submit_evidence |
| INTERNAL_AUDIT | own organisation | package, read, reevaluate_ipe, verify |
| AI_GOVERNANCE_LEAD | own organisation | grant_authority, manage_ai, manage_facts, read, request_evaluation, request_policy_decision |
| PLATFORM_ADMIN | own organisation | checkpoint, manage_connectors, manage_tenant, read, reevaluate_ipe |
| TENANT_OWNER | own organisation | manage_tenant, read |
| RISK_APPROVER | own organisation | approve_compensation, approve_exception, assurance_review, read |
| EXTERNAL_AUDITOR | own organisation | read, verify |
| SERVICE | own organisation | checkpoint, manage_facts, read, request_evaluation, request_policy_decision, submit_evidence |
| CONTENT_ANALYST / ENCODING_ENGINEER / CONTENT_LEGAL_REVIEWER / CONTENT_RELEASE_MANAGER | none (Zoiko content staff) | content_author / +content_peer_review / content_legal_review / content_release |
| SUPER_ADMIN | none (Zoiko platform) | manage_organisations |
| CONNECTOR_CERTIFICATION_MANAGER | none (Zoiko platform) | certify_connectors |

AGENT principals are refused by every guard until grants are bound to agent identities (`Ctx.check`). HUMAN-only actions refuse SERVICE principals.

**Changes:**

| File / function | Spec | Before | After | Reason | Test |
|---|---|---|---|---|---|
| `app/security.py` `Ctx.deny` (used by `check`, `require`, `require_human`, `tenant`) | IAM §21 `authorization.denied`; IAM-REQ-037/042; SEC-REQ-031 | A refusal was a bare 403 with no record | The refused transaction is rolled back, then `authorization.denied` is committed under the actor's tenant (none for platform staff). It records the endpoint template (`GET /api/evidence/{evidence_id}`), the reason code (`NO_TENANT_SCOPE`, `PLATFORM_FUNCTION`, `MISSING_PERMISSION`, `HUMAN_REQUIRED`, `AGENT_WITHOUT_GRANT`) and the message. If recording fails, the 403 stands. | Covers unauthorised tenant attempts by Zoiko staff and platform-function attempts by organisation users; the organisation's own trail shows its people's refusals | `test_refusals_are_recorded_as_authorization_denied`; `test_authorization.py` route matrix asserts **exactly one** denial row per refused request |
| `app/routers/platform.py` reads | IAM-REQ-037; SEC-REQ-045 | `GET /platform/organisations[/{id}]` used a separate `_platform_admin` check | Same `SUPER_ADMIN_ONLY` guard as the mutations, so refusals are audited | One decision point | `test_platform.py` (unchanged, passes) |

**Not done:**
- Per-read policy (G-08; any member reads all organisation data, P-01).
- Scoped and time-bound role assignments (IAM-REQ-010).
- Engagement-scoped external auditors (IAM-REQ-027).
- Membership states.
- User and role management APIs.

## 9. Tenant-Context Security Design

**Trusted sequence as implemented:**

1. Authenticate: the token hash resolves to a live `auth_session`.
2. Resolve the actor; membership = `org_actor.tenant_id` (server-side).
3. Organisation must be ACTIVE; temporary password must have been changed.
4. `set_tenant_scope` puts the tenant in `Session.info`.
5. The `after_begin` hook stamps `app.tenant_id` / `app.system` with `set_config(…, true)` on **every** transaction.
6. RLS `tenant_isolation` filters reads and checks writes.
7. Before commit, the session is re-validated (`FOR SHARE`).
8. At transaction end the settings revert.
9. At pool check-in, `RESET ALL` and `DISCARD TEMP` run.

A client-supplied organisation ID is never used for scope. The Super Admin's `tenant_id` path parameter selects a registry target only behind the platform guard.

**Threat evaluation (PostgreSQL 17.11 probe, §18):**

| Threat | Result | Protected? |
|---|---|---|
| `SET LOCAL app.tenant_id = '<other tenant>'` by the runtime role | Shows the other tenant's rows | **No** (I-01). By design the runtime role is trusted to set the context. |
| `set_config('app.system','on',true)` | Every tenant's rows plus platform rows | **No** (I-01) |
| SQL injection | A `set_config` inside a sub-select of **one** `SELECT` already widened that statement (`('on', 2)`). The application builds no SQL from request input (only bound ORM parameters; checked by search). | Only by the absence of injection points |
| Stolen runtime credential | Full cross-tenant DML. It also has full DML on non-RLS `auth_session`, `auth_credential`, `org_actor` and `org_cell`, so it could mint a session by inserting a `token_hash` it chose, or edit roles. | **No** |
| Compromised API process | Holds the runtime **and** owner credentials (`DATABASE_ADMIN_URL`) | **No protection claimed** |
| Connection pooling | Context is transaction-local (proven on the same backend PID after commit and after rollback); session-level settings and TEMP tables are now cleared at check-in | Yes, within the trusted API |
| Nested transactions | `ROLLBACK TO SAVEPOINT` restores the outer context (tested) | Yes |
| Worker sessions | No workers exist. `system_scope` jobs run inside request transactions. Future workers (Task 8) must use `SessionLocal` (same stamping and reset). | n/a |
| `SET ROLE` / `SET SESSION AUTHORIZATION` / granting itself the owner role | Refused (42501); the runtime role is a member of nothing | Yes |
| `SET row_security = off` | Refused ("query would be affected by row-level security") | Yes |

**Trust boundary:** the database enforces isolation only against application code that keeps to the stamped context. It does **not** enforce it against anyone who can run SQL as `assure_app`.

**Options to close I-01, for the decision owners (not implemented):**
- (a) A context bound to the authenticated session and verified inside the policy by a narrow `SECURITY DEFINER` function owned by a non-login role: an HMAC over tenant, actor, `txid_current()` and `pg_backend_pid()` with a secret readable only by that owner. `pgcrypto` is available on the cluster.
- (b) A separate database role for system jobs instead of the `app.system` setting.

Both need the Task 1 owner/role separation and the Data Architect + Security decision. Even then, a fully compromised API process remains a residual risk.

## 10. PG Role & Privilege Changes

**No role was created, altered or re-granted:** DAT §13 is Task 1, and role provisioning belongs to the deployment.

**Observed (probe), against DAT §13 / DAT-REQ-004 / SEC-REQ-023:**

| Check | `assure_app` (runtime) | `assure` (owner) |
|---|---|---|
| LOGIN / SUPERUSER / BYPASSRLS / CREATEROLE / CREATEDB | yes / no / no / no / no | yes / no / no / **yes** / no |
| Memberships | none | holds ADMIN OPTION on `assure_app` (INHERIT and SET false; PostgreSQL 16+ automatic grant to a CREATEROLE creator) |
| Owns | nothing (tables, functions, schemas, types: 0) | the database and all 208 relations in `public` (DAT §13 wants non-login owners) |
| Table privileges | SELECT/INSERT/UPDATE/DELETE as granted by 0002–0004; no TRUNCATE, REFERENCES or TRIGGER on any table (tested) | owner |
| `CREATE` on `public` | no | yes |
| `TEMP` on database | **yes** (PUBLIC default; contents now dropped at check-in) | yes |

**Runtime escalation probes (14, all refused with SQLSTATE 42501):**
- `SET ROLE` to the owner;
- `SET SESSION AUTHORIZATION`;
- `GRANT owner TO self`;
- `ALTER ROLE self BYPASSRLS`;
- `CREATE ROLE`;
- `DISABLE ROW LEVEL SECURITY` and `NO FORCE`;
- `DROP POLICY`;
- replacing `assure_current_tenant()`;
- `CREATE TABLE` and `CREATE SCHEMA`;
- `TRUNCATE`;
- `DISABLE TRIGGER append_only`;
- `ALTER TABLE … OWNER`.

Neither context function (`assure_current_tenant()`, `assure_system()`) is `SECURITY DEFINER`. They run with caller rights, and policies bind them by OID.

## 11. RLS Policy Inventory & Changes

| Item | State |
|---|---|
| Tables with `tenant_id` | 42 (nullable on `org_actor`, `platform_audit_event`, `platform_outbox_event`) |
| RLS enabled | 41. `org_actor` is exempt because sign-in resolves an identity before a tenant is known. That is broader than IAM §22's "minimum cross-tenant identity-discovery mapping" (residual). |
| FORCE | **0 of 41** (DAT-REQ-003 / TEN-REQ-022 not met; Task 1) |
| Owner of every table | `assure` (login) |
| Policies | All `TO assure_app`. 39 tables: one `tenant_isolation` (ALL commands, `USING` = `WITH CHECK` = `tenant_id = assure_current_tenant() OR assure_system()`). `org_tenant`: `USING` only (`WITH CHECK` defaults to it). |
| Changed by 0005 | `platform_audit_event`, `platform_outbox_event` (see below) |
| Entity-level access (DAT §12 `can_access_entity`) | Absent (G-08 / Task 1) |
| Cross-tenant references | Foreign-key checks are not filtered by RLS, and keys are single-column IDs. Isolation of client-supplied references depends on `ctx.scoped` (tested in `test_client_supplied_identifiers_must_belong_to_the_caller`). Composite `(tenant_id, id)` keys are Task 1 schema work. |
| Non-RLS tables the runtime role can write | `auth_session`, `auth_credential`, `org_cell`, `platform_record_sequence` (full DML), plus the global catalogue |

**After 0005, on each of the two platform-log tables:**

| Policy | Command | Predicate |
|---|---|---|
| `tenant_isolation` | ALL | `tenant_id = assure_current_tenant() OR assure_system()` |
| `platform_rows_select` | SELECT | `tenant_id IS NULL AND assure_current_tenant() IS NULL` |
| `platform_rows_insert` | INSERT | `tenant_id IS NULL AND assure_current_tenant() IS NULL` (WITH CHECK) |

No policy lets an ordinary scope UPDATE or DELETE a no-tenant row.

**Tests:** `test_every_tenant_table_has_rls_and_no_tenant_policy_admits_platform_rows` fails if:
- a new `tenant_id` table lacks RLS;
- a policy names another role;
- any `tenant_isolation` policy admits `IS NULL`.

## 12. Outbox Security Remediation

**Defect (audit §10):**
- `GET /api/outbox` returned the caller's events plus every no-tenant event, including `security.content_signature_failed` from failed publications.
- RLS admitted `tenant_id IS NULL` in `USING` and `WITH CHECK`, so a tenant session could also insert, update or delete platform rows.

| Layer | Change | Evidence |
|---|---|---|
| API (`app/routers/core.py` `outbox`) | `WHERE tenant_id = ctx.tenant` only. Platform staff get 403, which is now audited. | `test_outbox_returns_only_the_callers_own_events`: a platform security event and another organisation's event are absent; the caller's own event is present (SQLite and PostgreSQL) |
| Database (migration `0005`) | Split policies (§11) on outbox **and** audit tables | PostgreSQL: in a tenant scope there are 0 no-tenant rows; INSERT of a no-tenant row is refused; UPDATE/DELETE of no-tenant rows affect 0 rows; the platform scope sees 0 tenant rows and cannot write them; the platform scope cannot rewrite platform rows (6 tests) |
| Writers | All no-tenant writers checked (§13 and below) | Full PostgreSQL suite green except Task 0 |

**Compatibility fix found by the PostgreSQL run:**
- `engines/evidence.py` `build_package` (POST `/api/packages`) took a ledger checkpoint inside the **tenant** scope, where RLS shows only that tenant's head. It still tagged the checkpoint event as a no-tenant platform event, which only worked because of the defect.
- `create_checkpoint` now takes the scope's `tenant_id`, so a package's checkpoint event is recorded under that tenant.
- Cell-wide checkpoints (`/ledger/checkpoint` under `system_scope`, and the seed) are unchanged.
- Packages are otherwise out of scope; this is a one-parameter change.

**Downgrade:** restores the 0002 policies exactly (verified on PostgreSQL 17).

## 13. Security Audit Logging

All records use the existing append-only `platform_audit_event` with the controlled `audit()` helper. Existing action codes were **kept**: renaming persisted codes to the IAM §21 names needs a dictionary decision (DAT-REQ-025).

| Event (IAM §21 name) | Action code | Status |
|---|---|---|
| identity.authenticated | `auth.login` (session created) | existing |
| identity.authentication_failed | `auth.login_failed` + reason class | **extended** (unknown, non-interactive, no credential, locked, bad password, lock triggered) |
| session revoked | `auth.logout`; `auth.password_changed` ("N other session(s) revoked"); `organisation.status_changed` ("N session(s) revoked") | extended |
| password change failed | `auth.password_change_failed` | **new** |
| membership / role changes | `organisation.admin_appointed`, `account.bootstrap_created` | existing; no other API changes membership or roles |
| account status | n/a: there are no per-account states | blocked (IAM data model) |
| authorization.denied (includes unauthorised tenant and platform attempts) | `authorization.denied` | **new** |
| platform access | `platform.organisation_registry_read` (the one cross-tenant `system_scope` read; platform record); `platform.organisation_viewed` (recorded under the organisation, so it sees the access) | **new** |

**No secrets:**
- No password, token or token hash is written.
- Unknown sign-in identifiers are stored only as a truncated SHA-256.
- Tests assert that the passwords tried are absent from all `auth.*` rows.

**Not covered:** JIT, break-glass and support access (none exist), and per-request rate limiting.

## 14. API Authorization Coverage

**128 routes in 11 routers:**

| Guard type | Count | Notes |
|---|---|---|
| `authorize(...)` | 64 | 62 before; the two Super Admin reads were added |
| `get_ctx` reads (any signed-in member) | 54 | |
| Public mutations | 4 | `/auth/login`, `/auth/logout`, `/auth/password`, `/packages/verify-upload` (unchanged) |
| Public reads | 6 | `/auth/session` (validates its token), `/auth/demo-accounts` (404 unless demo mode), `/health`, `/dictionary`, `/trust/public-key`, `/watermark` |

`/watermark` stays public because the deploy health check (`.github/scripts/deploy_healthcheck.py`) depends on it.

**Fixed in Task 2:** the outbox read, the two platform reads, and denial auditing on every guard.

**Not changed (not Task 2 defects, or blocked):**
- Read-level permissions (G-08).
- `POST /ledger/checkpoint`: tenant roles holding `checkpoint` or `package` start the cell-wide `system_scope` job. Its response and `/ledger` expose the per-window `leaf_count`, a minor cross-tenant activity signal (evidence owner).

**Reading the matrix:**
- "Tenant derivation" is a static scan of each handler. "-" means the handler is global (catalogue) or delegates to an engine function that receives `ctx`.
- Cross-tenant refusal of every mutating route is proven dynamically in `test_authorization.py`.

<details><summary>Full per-route matrix (generated from the live routers)</summary>

| Method | Path | Guard | Tenant derivation |
|---|---|---|---|
| GET | `/api/actors` | get_ctx (any signed-in member) | actor's own tenant |
| POST | `/api/ai/acs/evaluate` | authorize(request_policy_decision) | engine |
| GET | `/api/ai/assets` | get_ctx | ctx.tenant |
| POST | `/api/ai/assets` | authorize(manage_ai) | ctx.tenant +scoped |
| POST | `/api/ai/assets/{subject_id}/versions` | authorize(manage_ai) | ctx.tenant +scoped |
| POST | `/api/ai/decisions` | authorize(request_policy_decision) | engine |
| POST | `/api/ai/grants` | authorize(grant_authority, human) | ctx.tenant +scoped |
| POST | `/api/ai/grants/{grant_id}/revoke` | authorize(grant_authority, human) | +scoped |
| GET | `/api/ai/policy-bundle/{agent_subject_id}` | get_ctx | +scoped |
| GET | `/api/applicability/determinations` | get_ctx | ctx.tenant |
| POST | `/api/applicability/determinations/{lineage_id}/attest` | authorize(attest, human) | engine |
| POST | `/api/applicability/determinations/{lineage_id}/challenge` | authorize(challenge) | ctx.tenant |
| POST | `/api/applicability/evaluate-all` | authorize(request_evaluation) | ctx.tenant +scoped |
| GET | `/api/applicability/evaluations` | get_ctx | ctx.tenant |
| POST | `/api/applicability/evaluations` | authorize(request_evaluation) | ctx.tenant +scoped |
| GET | `/api/applicability/evaluations/{evaluation_id}` | get_ctx | ctx.tenant +scoped |
| GET | `/api/audit` | get_ctx | ctx.tenant |
| GET | `/api/auth/demo-accounts` | public read (404 unless demo mode) | - |
| POST | `/api/auth/login` | public mutation | set_tenant_scope |
| POST | `/api/auth/logout` | public mutation | set_tenant_scope |
| POST | `/api/auth/password` | public mutation (token + current password) | set_tenant_scope |
| GET | `/api/auth/session` | public read (validates token) | - |
| GET | `/api/catalog/controls` | get_ctx | global catalogue |
| GET | `/api/catalog/coverage` | get_ctx | global catalogue |
| GET | `/api/catalog/fact-definitions` | get_ctx | global catalogue |
| GET | `/api/catalog/instruments` | get_ctx | global catalogue |
| GET | `/api/catalog/obligation-definitions` | get_ctx | global catalogue |
| PUT | `/api/catalog/provisions/{provision_id}` | authorize(content_author, platform) | global catalogue |
| GET | `/api/catalog/releases` | get_ctx | global catalogue |
| GET | `/api/catalog/releases/{release_id}/verify` | get_ctx | global catalogue |
| GET | `/api/catalog/requirements/{rv_id}` | get_ctx | global catalogue |
| POST | `/api/catalog/requirements/{rv_id}/verify` | authorize(content_legal_review, human, platform) | global catalogue |
| GET | `/api/catalog/sets` | get_ctx | global catalogue |
| GET | `/api/catalog/sources` | get_ctx | global catalogue |
| POST | `/api/catalog/sources` | authorize(content_author, platform) | global catalogue |
| POST | `/api/catalog/sources/{source_id}/extract` | authorize(content_author, platform) | global catalogue |
| GET | `/api/compensations` | get_ctx | ctx.tenant |
| POST | `/api/compensations` | authorize(request_exception) | ctx.tenant |
| POST | `/api/compensations/{comp_id}/decide` | authorize(approve_compensation, human) | ctx.tenant +scoped |
| GET | `/api/connectors` | get_ctx | ctx.tenant |
| POST | `/api/connectors/{connector_id}/certification` | authorize(certify_connectors, human, platform) | global registry |
| GET | `/api/controls` | get_ctx | ctx.tenant |
| POST | `/api/controls` | authorize(manage_controls) | ctx.tenant +scoped |
| GET | `/api/controls/{ci_id}` | get_ctx | +scoped |
| POST | `/api/controls/{ci_id}/evaluate` | authorize(manage_controls\|request_evaluation\|package) | ctx.tenant +scoped |
| GET | `/api/dashboard` | get_ctx | ctx.tenant |
| GET | `/api/dashboard/attention` | get_ctx | ctx.tenant |
| GET | `/api/dictionary` | public read | - |
| GET | `/api/evidence` | get_ctx | ctx.tenant |
| GET | `/api/evidence-conflicts` | get_ctx | ctx.tenant |
| POST | `/api/evidence-conflicts` | authorize(submit_evidence) | ctx.tenant +scoped |
| POST | `/api/evidence-conflicts/{conflict_id}/resolve` | authorize(assurance_review, human) | ctx.tenant +scoped |
| POST | `/api/evidence/upload` | authorize(submit_evidence) | ctx.tenant +scoped |
| GET | `/api/evidence/{evidence_id}` | get_ctx | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/bindings` | authorize(submit_evidence) | ctx.tenant +scoped |
| GET | `/api/evidence/{evidence_id}/download` | get_ctx | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/erase` | authorize(attest, human) | +scoped |
| POST | `/api/evidence/{evidence_id}/hold` | authorize(attest, human) | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/invalidate` | authorize(submit_evidence) | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/release-hold` | authorize(attest, human) | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/supersede` | authorize(submit_evidence) | ctx.tenant +scoped |
| POST | `/api/evidence/{evidence_id}/verify` | authorize(read) | +scoped |
| GET | `/api/exceptions` | get_ctx | ctx.tenant |
| POST | `/api/exceptions` | authorize(request_exception) | ctx.tenant +scoped |
| POST | `/api/exceptions/{exception_id}/decide` | authorize(approve_exception, human) | ctx.tenant +scoped |
| GET | `/api/fact-requests` | get_ctx | ctx.tenant |
| GET | `/api/health` | public read | - |
| GET | `/api/history/compare` | get_ctx | ctx.tenant +scoped |
| GET | `/api/ipe/anomalies` | get_ctx | ctx.tenant |
| POST | `/api/ipe/anomalies/{anomaly_id}/dispositions` | authorize(assurance_review, human) | ctx.tenant +scoped |
| GET | `/api/ipe/policies` | get_ctx | global |
| GET | `/api/ipe/source-runs` | get_ctx | ctx.tenant |
| POST | `/api/ipe/source-runs` | authorize(submit_evidence) | ctx.tenant +scoped |
| GET | `/api/ipe/source-runs/{run_id}` | get_ctx | +scoped |
| POST | `/api/ipe/source-runs/{run_id}/evaluate-ipe` | authorize(reevaluate_ipe, human) | +scoped |
| GET | `/api/issues` | get_ctx | ctx.tenant |
| POST | `/api/issues/{issue_id}/transition` | authorize(manage_issues) | ctx.tenant +scoped |
| GET | `/api/ledger` | get_ctx | ctx.tenant |
| POST | `/api/ledger/checkpoint` | authorize(checkpoint\|package) | system_scope (cell-wide) |
| POST | `/api/ledger/verify` | authorize(read) | ctx.tenant |
| GET | `/api/me` | get_ctx | own actor |
| GET | `/api/obligations` | get_ctx | ctx.tenant |
| GET | `/api/obligations/{lineage}` | get_ctx | ctx.tenant |
| POST | `/api/obligations/{lineage}/occurrences` | authorize(manage_controls\|manage_facts) | ctx.tenant |
| POST | `/api/obligations/{lineage}/preview` | authorize(read) | ctx.tenant |
| POST | `/api/obligations/{lineage}/snapshots` | authorize(request_evaluation\|package) | ctx.tenant |
| POST | `/api/occurrences/{occurrence_id}/fulfil` | authorize(manage_controls\|manage_facts) | ctx.tenant +scoped |
| POST | `/api/org/entities` | authorize(manage_tenant) | ctx.tenant |
| GET | `/api/org/subjects` | get_ctx | ctx.tenant |
| POST | `/api/org/subjects` | authorize(manage_facts) | ctx.tenant +scoped |
| GET | `/api/org/tenant` | get_ctx | ctx.tenant |
| GET | `/api/outbox` | get_ctx | ctx.tenant (**fixed**) |
| GET | `/api/packages` | get_ctx | ctx.tenant |
| POST | `/api/packages` | authorize(package) | ctx.tenant +scoped |
| POST | `/api/packages/verify-upload` | public mutation (offline verification) | - |
| GET | `/api/packages/{package_id}/download` | get_ctx | +scoped |
| POST | `/api/packages/{package_id}/verify` | authorize(read) | +scoped |
| GET | `/api/platform/organisations` | authorize(manage_organisations, human, platform) (**was a separate check**) | system_scope (registry metadata) |
| POST | `/api/platform/organisations` | authorize(manage_organisations, human, platform) | set_tenant_scope (new org) |
| GET | `/api/platform/organisations/{tenant_id}` | authorize(manage_organisations, human, platform) (**was a separate check**) | target org scope |
| PATCH | `/api/platform/organisations/{tenant_id}` | authorize(manage_organisations, human, platform) | target org scope |
| POST | `/api/platform/organisations/{tenant_id}/admins` | authorize(manage_organisations, human, platform) | target org scope |
| POST | `/api/platform/organisations/{tenant_id}/status` | authorize(manage_organisations, human, platform) | target org scope |
| GET | `/api/risk-acceptances` | get_ctx | ctx.tenant |
| POST | `/api/risk-acceptances` | authorize(approve_exception, human) | ctx.tenant |
| GET | `/api/rules/diff` | get_ctx | global catalogue |
| GET | `/api/rules/drafts` | get_ctx | global catalogue (unpublished drafts readable by any member: G-08) |
| POST | `/api/rules/drafts` | authorize(content_author, platform) | global catalogue |
| GET | `/api/rules/drafts/{draft_id}` | get_ctx | global catalogue (G-08) |
| PUT | `/api/rules/drafts/{draft_id}` | authorize(content_author, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/legal-approve` | authorize(content_legal_review, human, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/peer-approve` | authorize(content_peer_review, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/publish` | authorize(content_release, human, platform) | system_scope (impact re-evaluation) |
| POST | `/api/rules/drafts/{draft_id}/sandbox` | authorize(content_author\|content_peer_review\|content_legal_review\|content_release, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/shadow` | authorize(content_author\|content_release\|content_legal_review, platform) | system_scope (aggregate only) |
| POST | `/api/rules/drafts/{draft_id}/submit-review` | authorize(content_author, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/test` | authorize(content_author, platform) | global catalogue |
| POST | `/api/rules/drafts/{draft_id}/validate` | authorize(content_author, platform) | global catalogue |
| GET | `/api/rules/versions` | get_ctx | global catalogue |
| GET | `/api/rules/versions/{rv_id}` | get_ctx | global catalogue |
| GET | `/api/snapshots/{snapshot_id}` | get_ctx | +scoped |
| GET | `/api/snapshots/{snapshot_id}/reproduce` | get_ctx | ctx.tenant +scoped |
| GET | `/api/subjects/{subject_id}/facts` | get_ctx | ctx.tenant +scoped |
| POST | `/api/subjects/{subject_id}/facts` | authorize(manage_facts) | ctx.tenant +scoped |
| GET | `/api/subjects/{subject_id}/facts/history` | get_ctx | ctx.tenant +scoped |
| POST | `/api/test-runs` | authorize(submit_evidence) | ctx.tenant +scoped |
| GET | `/api/trust/public-key` | public read | - |
| GET | `/api/watermark` | public read (health check; cell-wide record sequence) | - |

</details>

## 15. Files Modified

Modified files were already modified before this task, except `tests/test_authorization.py` and `tests/test_migrations.py`, which were already untracked. Edits were added on top of the existing work.

| File | Status | Change |
|---|---|---|
| `backend/app/auth.py` | modified | Token hash; sign-in and password-change hardening and audit; session key file removed |
| `backend/app/security.py` | modified | `Ctx.route`, `Ctx.deny`, `_route_template`, `_revalidate_session`; `get_ctx` records the session |
| `backend/app/db.py` | modified | Pool check-in reset (PostgreSQL) |
| `backend/app/models.py` | modified | `AuthSession.token_hash` |
| `backend/app/routers/core.py` | modified | Outbox filter |
| `backend/app/routers/platform.py` | modified | Reads behind `SUPER_ADMIN_ONLY`; registry read/view audit |
| `backend/app/engines/evidence.py` | modified | `create_checkpoint(tenant_id=…)` (compatibility with 0005, §12) |
| `backend/migrations/versions/0005_platform_rows_platform_scope_only.py` | **new** | Platform-log policies |
| `backend/migrations/versions/0006_auth_session_token_hash.py` | **new** | `token_hash` column and unique index |
| `backend/tests/test_auth_session_security.py` | **new** | 10 tests (SQLite + PostgreSQL) |
| `backend/tests/test_tenant_security.py` | **new** | 28 tests (PostgreSQL) |
| `backend/tests/test_api.py` | modified | Lockout assertion (§17) |
| `backend/tests/test_authorization.py` | modified | Fingerprint accounts for denial records (§17) |
| `backend/tests/test_migrations.py` | modified | Legacy schema excludes the post-baseline column (§17) |
| `.github/workflows/ci.yml` | modified | Gate: `--require test_tenant_security=28`, `--require test_auth_session_security=10` |
| `ARCHITECTURE.md`, `docs/ci-cd.md`, `docs/database-migrations.md` | modified | Session, gate and revision documentation |
| `docs/task-2-authentication-session-and-tenant-security-report.md` | **new** | This report |

## 16. Database Migrations

| Revision | Content | Reversible | Verified |
|---|---|---|---|
| `0005` | Platform-log policy split (§11). No grant, role or ownership change. | Yes (restores 0002 policies) | PostgreSQL 17: upgrade, repeat, `check`, downgrade to 0004, re-upgrade; `test_migrations` 16/16 (throwaway schemas) |
| `0006` | `auth_session.token_hash varchar(64)` + unique index `ix_auth_session_token_hash`. Expand only; no data rewrite. | Yes (drops both) | Same, plus `migrate check` (no model drift) |

- **Not applied to the development database `assure`**, which is still at `0004`. Per standing instructions it needs a verified backup and the owner's explicit request.
- Until then the API's start-up check (`require_current`) refuses to start against it, including a `--reload` dev server that restarts on these file changes.
- After `0006`, every existing session must sign in again.

## 17. Automated Test Results

| Run | Result |
|---|---|
| SQLite, full suite (final tree) | **129 passed, 62 skipped** (PostgreSQL-only, reason "PostgreSQL-only"), **1 failed**: the Task 0 test |
| SQLite, repeated runs with Task 2 code | Before the two new test modules existed: 4 consecutive runs, 120 passed / 0 failed. With them: 3 runs, each 129 passed / 1 failed. Always the same test, so it is flaky. |
| Byte-identical scratch copies | Task 2 tree, 3 runs: failed, failed, passed. Pre-Task-2 tree (my edits reversed, exact-match checked), 4 runs: failed, failed, passed, failed. The baseline is equally flaky. |

**Changed existing tests:**

- **`test_api.py::test_agents_cannot_sign_in_and_lockout`**
  - Before: `423 == status`.
  - Now: the correct password is refused while locked (401), the response is byte-identical to an unknown account's, and the audit shows `BAD_PASSWORD`/`LOCKED` then `LOCKED`.
  - The lockout is still proven, and enumeration resistance is added.
- **`test_authorization.py` `db_fingerprint`**
  - Still hashes every row of every table except the new `authorization.denied` rows.
  - The record sequence is hashed **net of** those rows, so any other write or any other use of the sequence still changes it.
  - The route matrix also asserts exactly one denial row per refused request (stronger than before).
- **`test_migrations.py::_legacy_startup`**
  - The old start-up schema is `create_all` minus columns added after the 0002 baseline. Those columns are listed explicitly (`POST_BASELINE_COLUMNS`), so any unlisted model change still fails the full baseline comparison.

**Task 0 test (`test_fact_assertion_preserves_non_integrity_evaluation_errors`):**
- Untouched.
- When it fails, the tampered rule version still evaluates to `APPLIES`. That is the Task 0 fallback / unordered `.first()` defect (audit §15 Task 0 (a)/(b)), and it is not concealed.
- The CI gate's stale `test_rule_integrity=13` (18 tests exist) was left for Task 0.

## 18. PG17 Verification

**Environment:** every run used a **disposable** PostgreSQL 17.11 cluster.
- Initialised in the session scratch directory on ports 55433–55435.
- Provisioned by the committed `.github/scripts/ci_postgres_bootstrap.sql` as its throwaway superuser.
- Random passwords per run, scrubbed from output; the cluster was deleted afterwards.
- No existing server or database (including `assure`, `assure_test` or any other project's) was touched.

| Run | Result |
|---|---|
| CI reproduction (`ci.yml` steps, gates read from `ci.yml`): migration CLI | status (empty) → upgrade → upgrade (repeat) → status → `check`: **pass**, head `0006` |
| CI reproduction: RLS gate | **PASS** (5/5) |
| CI reproduction: full suite | **191 passed, 1 failed** (Task 0 test) |
| CI reproduction: full-suite gate | **FAIL**, only because of that test. Every required module passed: `test_rls` 5, `test_pg_bootstrap` 4, `test_migrations` 16, `test_immutability` 7, `test_snapshot_determinism` 4, `test_tenant_security` 28/28, `test_auth_session_security` 10/10; `test_rule_integrity` 17/18. |
| Pre-Task-2 copy, same cluster setup | 153 passed, 1 failed (same Task 0 test) |
| Probe script | Role, ownership and RLS inventory (§10–§11); the I-01 demonstrations (§9); 0005/0006 downgrade → policies back to the 0002 form and `token_hash` gone → re-upgrade → `check passed` |

**PostgreSQL 17 tests in `test_tenant_security.py`:**
- Platform-row read, insert, update and delete in both scopes.
- RLS inventory.
- Pool reuse after commit and rollback on the same backend PID.
- Check-in reset.
- Savepoint rollback.
- `row_security = off`.
- 14 escalation probes.
- Membership, ownership and extra-privilege audit.
- Concurrent revocation under `FOR SHARE`.

**How to reproduce in CI:** the existing `backend-postgres` job. Locally: `python ci_pg_repro.py <repo> <workdir>` (scratch tooling, disposable cluster).

## 19. Security Limitations & Residual Risks

| # | Risk | Severity | Owner / unblock |
|---|---|---|---|
| R1 | **I-01**: the runtime role chooses its own tenant context (`set_config`, even in one statement); `app.system` bypasses all policies | Critical | Task 1: system-job design (Data Architect + Security) |
| R2 | No `FORCE` RLS; owner `assure` is a login role owning everything and can disable triggers or RLS (A10) | High | Task 1 role model |
| R3 | API process holds the owner credential (`DATABASE_ADMIN_URL`) | High | Task 1 |
| R4 | Runtime role has unrestricted DML on `auth_session`, `auth_credential` and `org_actor` (no RLS). With the runtime credential one can mint sessions or change roles. | High | Task 1 + IAM data model (separate identity lookup path, IAM §22) |
| R5 | Bearer token in browser `localStorage`; no CSRF/cookie model; tokens replayable until expiry | High | G-05, frontend |
| R6 | Local passwords only; no MFA/OIDC/SAML/SCIM; scrypt n=2^14 | High (production) | G-05 |
| R7 | Any member reads all organisation data (P-01); unpublished rule drafts readable by tenants | High | G-08 |
| R8 | Runtime role keeps PUBLIC `TEMP` on the database. Within one request, injected SQL could shadow tables (cleared at check-in now). | Medium | Task 1 privilege matrix (`REVOKE TEMP`) |
| R9 | `X-Actor-Id` honoured whenever `ZA_DEV_IDENTITY_HEADER=1`, in any environment (I-08); only the deployment checks catch it | Medium | Deployment target (G-10) / start-up guard decision |
| R10 | No rate limiting beyond per-account lockout. Every refusal and failed sign-in writes an audit row (volume, sequence-row contention). | Medium | SEC §09 rate limiting |
| R11 | `/watermark` public; `/ledger` and checkpoint responses expose cell-wide counters (activity inference) | Low | TIM/EVD owners |
| R12 | Refusals inside a SAVEPOINT are not recorded (none exist today; logged as a warning) | Low | — |
| R13 | Timing between unknown and known accounts differs by one indexed lookup | Low | — |

A fully compromised API process defeats tenant isolation; this design does not claim otherwise.

## 20. Deferred Work

| Item | Reason |
|---|---|
| BFF / HttpOnly cookie session, CSRF | G-05; frontend out of scope |
| OIDC/SAML, MFA, SCIM, step-up | G-05 |
| User/membership/role APIs; account disable; per-user admin revocation | IAM §22 data model + G-08; "do not build missing business APIs" |
| JIT, break-glass, support access | IAM §11–§13 design |
| Renaming `auth.*` codes to IAM §21 names | Controlled-vocabulary decision |
| Outbox publisher role and its UPDATE path | DAT §13 `assure_outbox_publisher` (Task 1 / Task 8) |
| Frontend login comment still mentions 423 (`frontend/app/login/page.tsx`); behaviour is fine because it shows the backend's message | Frontend out of scope |

## 21. Remaining Blockers

| Decision | Blocks | Owner |
|---|---|---|
| System-job / tenant-context design (I-01, P-02) | R1, `FORCE` | Data Architect + Security |
| Role model (DAT §13 vs the brief's names; A10) | R2, R3, R4, R8 | Data Architect + Security |
| G-05 session architecture and IdP | R5, R6 | Security Architecture / IAM Lead |
| G-08 read-authorisation policy | R7 | IAM Lead |
| Task 0 fallback decision | The flaky test; the PostgreSQL gate | APP owner |
| ZA-ADR-001 approval (incl. ARCH-ADR-003 Go vs Python) | Every "accepted" claim | CTO / Architecture Governance |
| Dev database upgrade to `0006` | Running the dev API on this tree | Repository owner (backup first) |

## 22. DoD Assessment

| DoD item (Task 2 §16 / §2–§13) | Status |
|---|---|
| Outbox defect fixed at API **and** database, with regression tests | **Done** (§12) |
| Authentication: hashing, generic errors, lockout, audit | **Done** within the local-password model; federation and MFA blocked (G-05) |
| Account lifecycle states | **Blocked** (no IAM model; no states invented) |
| Session create/validate/expire/revoke/logout; hashed tokens; revoked sessions rejected, including mid-request | **Done** (§7) |
| Browser session storage | **Blocked** (G-05, frontend) |
| Authorisation matrix; no generic bypass; no client-supplied organisation | **Done** (§8, §14); per-read policy blocked (G-08) |
| Tenant context: transaction-local, no pooled leakage, cleared at end | **Done** (§9) |
| Tenant context resistant to GUC manipulation / database-bound | **Blocked** (Task 1, I-01) |
| PostgreSQL roles per DAT §13; owner hardening; `FORCE` RLS | **Blocked** (Task 1) |
| Runtime role cannot escalate or disable enforcement | **Verified** (14 probes) |
| Super Admin boundary explicit and audited | **Done** |
| Security audit events, no secrets | **Done** for the events this code can produce (§13) |
| PostgreSQL 17 tests pass | Task 2 tests **pass** (38/38); full gate **fails** only on the pre-existing flaky Task 0 test |
| Task 3 and unrelated work untouched | **Done** (§4) |
| Report complete | **Done** |

**Verdict: NOT COMPLETE.**
- All Task 2 work that could proceed without Task 1, G-05 and G-08 is implemented and verified on PostgreSQL 17.
- The database-bound tenant context, the DAT §13 role model with `FORCE` RLS, and the browser session model remain open behind the prerequisites in §21.
- The PostgreSQL full-suite gate stays red until Task 0 resolves its flaky test.
