# Zoiko Assure — Stage 4: Read Authorization and Scoped Role Registry

> **Status:** partially implemented in the working tree on 2 November 2026 (migration `0009`). **Not committed, not run in GitHub Actions, not deployed.** The development database `assure` was not migrated during the stage. It was upgraded 0007 → 0009 afterwards, at the requester's request, after a verified backup and a rehearsal ([addendum](#addendum-development-database-upgraded-to-0009-2-november-2026)).
> **Roadmap:** [Stage 4 — Authorization completeness](backend-architecture-roadmap.md#stage-4--authorization-completeness-p-01-i-08-i-09-i-10), findings P-01 and I-10 of the [architecture audit](backend-architecture-audit.md).
> **Governance:** every ZA-* specification is still "Proposed for lock". Governance decision G-08 (read-authorization policy, owner: IAM Lead) is **still open**. The read classes below are an interim interpretation chosen on 2 November 2026, and the IAM Lead must confirm or replace them.
> **Evidence labels:** *Spec requires* / *Code did* / *Code does* / *Gap* / *Recommendation*.

## Contents

1. [Verdict](#1-verdict)
2. [Controlled requirements in scope](#2-controlled-requirements-in-scope)
3. [Decisions and deviations](#3-decisions-and-deviations)
4. [Findings by requirement](#4-findings-by-requirement)
5. [Role and scope registry design](#5-role-and-scope-registry-design)
6. [Enforcement coverage by endpoint](#6-enforcement-coverage-by-endpoint)
7. [Database security verification](#7-database-security-verification)
8. [Test results](#8-test-results)
9. [Migration verification](#9-migration-verification)
10. [Git and canonical database preservation](#10-git-and-canonical-database-preservation)
11. [Remaining gaps and approval dependencies](#11-remaining-gaps-and-approval-dependencies)
12. [Files changed](#12-files-changed)

---

## 1. Verdict

**Stage 4 is not complete.** These parts are implemented and verified on PostgreSQL 17 and SQLite:

| Roadmap item | State |
|---|---|
| **4a** Operation registry: every route of every method has a declared requirement or is explicitly public; start-up refuses otherwise | Implemented |
| **4b** Scoped, time-bound role assignments replacing the JSON `roles` list | Implemented: TENANT, LEGAL_ENTITY and PLATFORM scopes. The JSON list remains as a cache |
| **4c** Engagement-scoped external auditors and narrowed `SERVICE` reads | **Partial.** The auditor is limited to an allowlist of resource classes, but there is no engagement object, expiry or resource selection. `SERVICE` is unchanged |
| **4d** Refuse to start with `ZA_DEV_IDENTITY_HEADER=1` outside dev/test | Implemented in the pre-push remediation (2 November 2026): allowed only when `ZA_ENVIRONMENT` is `development` or `test` |
| **4e** Strict request models (`extra="forbid"`) for all request models | Implemented in the pre-push remediation: all 45 request-body models refuse unknown fields; the frontend sends none |
| **4f** `/api/watermark` behind sign-in | Implemented |

The acceptance row of the roadmap reads: "Start-up refuses any route without a declared operation and permission. The matrix test is green on PostgreSQL."

- **The first clause is met.**
- **The matrix test is green on PostgreSQL.** The pre-push remediation added the generated per-role read matrix (`tests/test_read_authorization_matrix.py`, reviewed table `tests/read_authorization_matrix.json`, marked PROVISIONAL pending G-08).

## 2. Controlled requirements in scope

Requirement text is quoted from the DOCX sources of the controlled documents: ZA-IAM-001 v1.0, ZA-API-001 v1.0, ZA-SEC-001 v1.0, ZA-QA-001 and ZA-DAT-001.

| ID / section | Requirement (abridged) | Where addressed |
|---|---|---|
| IAM-INV-03 | Every binding authorisation decision is deny-by-default and resolves tenant, entity/cell, action, resource and policy version | §4.1, §4.7 |
| IAM-INV-05 | No application runtime or support principal has standing cross-tenant access | §4.9, §7 |
| IAM-INV-09 | Authorisation policies and role/grant changes are versioned and auditable | §4.11 |
| IAM-REQ-007 | Every authorisation decision MUST be deny-by-default and evaluate action, resource, tenant/entity scope, principal and current policy/grant context | §4.1 |
| IAM-REQ-009 | Client-supplied tenant/entity/cell claims MUST NOT be trusted as authoritative resource scope without server-side resolution | §4.7 |
| IAM-REQ-010 | RBAC role assignment MUST include an explicit scope and MUST NOT imply universal tenant/entity resource access unless the policy expressly defines that scope | §4.3, §4.7 |
| IAM-REQ-014 / -036 | High-risk mutations MUST revalidate current authority immediately before commit; step-up MUST NOT freeze authority | §4.6 |
| IAM-REQ-015 | Disabling a principal or revoking a grant MUST invalidate new authorisation immediately | §4.6 |
| IAM-REQ-021 | A principal MUST NOT be the sole approver of its own privileged elevation | §4.4 |
| IAM-REQ-024 | Zoiko support MUST have no standing tenant access | §4.9 |
| IAM-REQ-027 | External auditor/regulator access MUST be engagement-scoped, time-boxed, resource-allowlisted and isolated from ordinary workforce membership | §4.8 (partial) |
| IAM-REQ-037 | Every material privileged action MUST record principal, effective grant, policy version, scope, correlation and time | §4.11 |
| IAM-REQ-040 | External engagement closure/expiry MUST revoke sessions and resource access automatically | **Gap** (§11) |
| IAM-REQ-041 | Runtime roles MUST preserve ZA-DAT-001 RLS guarantees and MUST NOT use BYPASSRLS | §7 |
| IAM-REQ-042 | Infrastructure errors MUST NOT become ALLOW | §4.1 (refusal recording unchanged) |
| IAM §02, §22 | RoleAssignment = principal + role template + scope + valid time + approval/source; `iam.role_assignment_revision` "Bitemporal; revocation creates successor/closure" | §5 |
| IAM §08 | Role templates; TENANT_SECURITY_ADMIN does "role/grant management"; TENANT_OWNER "may delegate admin"; "Admin without scope is prohibited" | §4.4 |
| IAM §14 | External auditor resource allowlist: "Audit package(s), selected evidence manifests/payload views, control/obligation dossiers, reports. Default deny outside engagement." | §4.8 |
| IAM §21 | `role.assigned` / `role.revoked` (principal, role, scope, valid time, actor, approval/source); `authorization.denied` | §4.11 |
| IAM §23 | `POST /role-assignments` (authorisation + SoD); `GET /principals/{id}/effective-access` (security/admin-only, no secret credentials) | §5 |
| API-REQ-008 | Tenant identity derives from authenticated context; callers cannot choose a tenant | §4.3 |
| API-REQ-014 | Mutation request schemas reject unknown fields; server-owned fields not mass-assignable | §4.5 (new models only) |
| API-REQ-031 | Every API operation MUST have a stable operationId used by documentation, metrics, IAM mapping and tests | §4.1 |
| ZA-API-001 error model | 403 when the actor is not permitted on a known resource; "404 `not_found` may be returned instead of revealing existence" | §4.7 |
| SEC-REQ-008 / -009 | Isolation enforced independently at API and RLS; any cross-tenant read is a release-blocking failure | §4.10, §7 |
| SEC-REQ-022 | Object-level authorisation and explicit field allowlisting on every mutation | §4.5 |
| QA-REQ-019 / -021, QA §09 | Cross-tenant read/write tests on every repository/API/history/export path; positive/negative RBAC/relationship cases; cross-entity access denied unless the grant covers the exact entity | §8 |
| DAT-REQ-003 / -024 / -039 | FORCE RLS; backfills never fabricate history; reviewed schema changes | §7, §9 |

ZA-EVT-001 §18 has **no** role-assignment event type, so no outbox event is emitted for role changes. They are recorded in the security audit (IAM §21) and the registry's own history table.

## 3. Decisions and deviations

**Decisions taken by the requester on 2 November 2026,** at the start of this stage:

1. **Role administration.** Add the IAM §08 template TENANT_SECURITY_ADMIN with the permission `manage_role_assignments`. TENANT_OWNER ("may delegate admin") may appoint only TENANT_SECURITY_ADMIN.
2. **Read policy (pending G-08).** Guard every read. Tenant reads need `read`. EXTERNAL_AUDITOR is narrowed to the IAM §14 allowlist classes, with no engagement table yet.
3. **Scope.** TENANT and LEGAL_ENTITY scopes, time-bound. An entity-scoped role counts only on resources resolved server-side to that entity, never on tenant-wide lists. Existing JSON roles migrate as TENANT scope.

**Deviations, for review:**

| # | Deviation | Reason |
|---|---|---|
| D1 | A second new permission, `delegate_admin` (TENANT_OWNER only), in addition to `manage_role_assignments` | Gating the TENANT_SECURITY_ADMIN appointment on the existing `manage_tenant` would also have let PLATFORM_ADMIN reach the role-management routes, because it holds `manage_tenant`. `delegate_admin` maps IAM §08's "may delegate admin" one-to-one. |
| D2 | A new permission, `audit_read`, for the external auditor, replacing its workforce-wide `read` | It implements decision 2. It names the IAM §14 allowlist read and is accepted only by routes in that allowlist. |
| D3 | Enforced directly, not run report-only first as the roadmap's risk row suggests | It follows the requester's choice. The read population of every existing tenant role is unchanged (they all hold `read`), so only the external auditor, platform staff, agents and the new TENANT_SECURITY_ADMIN see new refusals. |
| D4 | TENANT_SECURITY_ADMIN can be assigned at TENANT scope only (IAM §08 says "Tenant / selected entities") | Role management needs the grantor's authority organisation-wide in this stage, because the scope to check is in the request body. Entity-scoped security administration is left for later (fail closed). |
| D5 | The role-assignment API refuses back-dated `valid_from` (more than 5 minutes in the past) | Not stated in the specs. A back-dated start would record authority that was never held, which conflicts with DAT-REQ-024's "never fabricate history". Flagged for the IAM Lead. |
| D6 | A comment in `security.py` cited IAM-REQ-030 for the Super Admin's lack of tenant access; it now cites IAM-REQ-024 | IAM-REQ-030 is about agent authority grants. IAM-REQ-024 is "no standing tenant access". |

## 4. Findings by requirement

### 4.1 Deny-by-default for every operation (IAM-REQ-007, IAM-INV-03, API-REQ-031; roadmap 4a)

- **Spec requires:**
  - Every authorisation decision is deny-by-default and evaluates action, resource, scope, principal and current grants (IAM-REQ-007).
  - Every operation has a stable `operationId` (API-REQ-031).
- **Code did:**
  - Only POST/PUT/PATCH/DELETE routes were checked at start-up (`unguarded_mutations`).
  - 60 GET routes were authorised by sign-in plus tenant scope only. Audit P-01 counted 56 at audit time.
  - `operationId`s were FastAPI's generated names.
- **Code does:**
  - `security.operations()` builds the operation registry: operationId `<module>.<function>`, method, path, requirement, entity resolution and public reason.
  - `assign_operation_ids` sets every route's operationId before mounting and refuses duplicates.
  - `main.py` refuses to start while `unguarded_operations()` (any method) is non-empty.
  - Public routes are an explicit, reasoned list: 4 mutations (unchanged) and 5 reads: `health`, `dictionary`, `trust/public-key`, `auth/session`, `auth/demo-accounts`.
  - `GET /me` uses the new `authenticated()` guard: self-service, recorded as such in the registry.
  - In total there are 140 operations with 140 unique ids, and they match the OpenAPI document.
- **Gap:**
  - There is no published OpenAPI artefact or contract diff (Stage 5c).
  - The registry is in code. IAM-REQ-013 ("policies versioned, tested, release-controlled") is met only to the extent of the `AUTHZ_POLICY_VERSION` constant and the tests.
- **Recommendation:** when Stage 5c commits the OpenAPI bundle, generate the route × permission table in §6 from `operations()` and gate changes in CI.

### 4.2 Read permissions distinct from mutation permissions

- **Spec requires:**
  - Separation between read and write authority; reads are authorised too (IAM-REQ-007).
  - EXTERNAL_AUDITOR "no tenant admin/business mutations" (IAM §08).
- **Code did:** `read` was a single permission held by every tenant role, the external auditor and `SERVICE`. It was checked on one GET (evidence download) and on four read-class POSTs.
- **Code does:** three read classes, all separate from every mutation permission:
  - **`read`:** the organisation's workforce read. It is held by the same roles as before, so their reads are unchanged.
  - **`audit_read`:** the IAM §14 allowlist (§4.8), held only by EXTERNAL_AUDITOR.
  - **Content and platform read permissions:** the global catalogue and rule-version reads also accept the content-operations permissions; the people directory and the connector registry also accept the platform permissions.
  - **TENANT_SECURITY_ADMIN:** holds `manage_role_assignments` only and **no read**. It administers access, not the regulatory record (test `test_read_permissions_are_distinct_from_mutation_permissions`).
- **Gap:** which workforce roles should read the audit log, the outbox and the people directory is not specified. Every role holding `read` still can, as before. That question belongs to G-08.
- **Recommendation:** the IAM Lead decides G-08 at operation level, using §6 as the starting inventory.

### 4.3 Scoped role registry (IAM §02, §22, IAM-REQ-010; roadmap 4b)

- **Spec requires:**
  - Each RoleAssignment carries principal, role template, scope, valid time and approval/source.
  - It is bitemporal, and revocation creates a successor or closure.
  - The scope is explicit, with no universal access implied.
- **Code did:** `org_actor.roles` was a JSON list mapped through a static dictionary, with no scope, validity or history.
- **Code does:**
  - **Storage:** `iam_role_assignment` (current row) and append-only `iam_role_assignment_event` (history), described in §5.
  - **Authority source:** every request reads the principal's assignments in force (`iam.active_assignments`) within its own tenant. An assignment recorded under another tenant never confers authority, a check that also runs below RLS (`test_role_administration_never_crosses_the_tenant_boundary`).
  - **The JSON column:** it becomes a cache, kept equal to the current TENANT/PLATFORM-wide roles in the same transaction as every change.
  - **Views:** `/me` and `/auth/session` report roles, permissions and `role_assignments` from the registry.
  - **Creation paths:** the bootstrap, the Super Admin's appointment of an Organisation Admin and the demo seed all write recorded assignments (sources `BOOTSTRAP`, `PLATFORM_APPOINTMENT`, `SEED`).
- **Gap:**
  - Record time is a closure on the same row plus the event history, not a full bitemporal revision table (`record_from_seq`/`record_to_seq`).
  - Role templates are not versioned rows (`iam.role_template_version`). The catalogue is the code constant plus `AUTHZ_POLICY_VERSION`.
  - There is no `approval_ref` or dual approval for assignments (IAM §23 "optional approval").
- **Recommendation:**
  - The Data Architect confirms whether closure plus an event table satisfies "bitemporal" for IAM rows, as it did for the Stage 3c governance tables.
  - If not, a later migration adds `record_to_seq` and successor rows.

### 4.4 Role management authority and segregation of duties (IAM §08, §10, IAM-REQ-021)

- **Spec requires:**
  - TENANT_SECURITY_ADMIN does role/grant management and "cannot approve own privileged elevation".
  - TENANT_OWNER "may delegate admin".
  - A principal is never the sole approver of its own elevation.
- **Code did:** no tenant role-management API existed. Only the Super Admin could grant a role, TENANT_OWNER, at organisation creation.
- **Code does:**
  - `POST /role-assignments` and `POST /role-assignments/{id}/revoke` are human-only routes.
  - Per-role rules live in `iam.ASSIGNABILITY`:
    - **Business roles** (CCO, LEGAL_REVIEWER, REGULATORY_ANALYST, CONTROL_OWNER, INTERNAL_AUDIT, AI_GOVERNANCE_LEAD, PLATFORM_ADMIN, RISK_APPROVER): HUMAN principals at TENANT or LEGAL_ENTITY scope, by `manage_role_assignments`.
    - **TENANT_SECURITY_ADMIN:** HUMAN principals at TENANT scope, by `delegate_admin`.
    - **EXTERNAL_AUDITOR, SERVICE:** revocable by `manage_role_assignments` (IAM §24 emergency revocation), **never assignable** through this API.
    - **TENANT_OWNER and every platform role:** neither assignable nor revocable here.
  - The grantor's authority must be held organisation-wide.
  - Self-assignment is refused (`SELF_ASSIGNMENT`). A TENANT_SECURITY_ADMIN cannot appoint another one, and a TENANT_OWNER cannot assign business roles.
- **Gap:**
  - Two-person collusion remains possible: an Organisation Admin appoints a security admin, who then assigns the Organisation Admin a business role. That is SoD-compliant (two distinct principals), but IAM §10 "independent approval above configured materiality" and dual approval are not implemented.
  - There are no access-review campaigns (IAM §20, IAM-REQ-038/039).
- **Recommendation:** decide whether high-impact roles (CCO, LEGAL_REVIEWER, RISK_APPROVER) need dual approval, and add an approval slot if so.

### 4.5 Escalation prevention and mass assignment (SEC-REQ-022, API-REQ-014, IAM-REQ-002)

`test_escalation_and_self_assignment_are_refused_and_change_nothing` runs 29 refused attempts. Each one leaves every table unchanged apart from its own `authorization.denied` record, and each 403 records the expected reason code:

| Kind of attempt | Result |
|---|---|
| Self-assignment, by a TENANT_SECURITY_ADMIN or a TENANT_OWNER | 403 `SELF_ASSIGNMENT` |
| A security admin appointing a security admin; an Organisation Admin assigning CCO | 403 `NOT_DELEGABLE_BY_ACTOR` |
| Assigning TENANT_OWNER, SUPER_ADMIN, CONTENT_RELEASE_MANAGER, CONNECTOR_CERTIFICATION_MANAGER, EXTERNAL_AUDITOR or SERVICE | 403 `ROLE_NOT_ASSIGNABLE` |
| CCO, PLATFORM_ADMIN, INTERNAL_AUDIT and Super Admin callers | 403 `MISSING_PERMISSION` |
| External auditor and service callers | 403 `HUMAN_REQUIRED` |
| Agent callers | 403 `AGENT_WITHOUT_GRANT` |
| Unknown role; a human role for a SERVICE, AGENT or EXTERNAL_AUDITOR principal (IAM-REQ-002) | 422 |
| PLATFORM scope, or the server-owned fields `tenant_id` / `granted_by` in the body (API-REQ-014) | 422 |
| Back-dated or inverted validity; another tenant's scope ID | 422 |
| An entity of another organisation, or no entity named for LEGAL_ENTITY scope | 404 |

Revocation needs the same authority as assignment: a CCO, a PLATFORM_ADMIN or the security admin itself cannot revoke the security admin's role, and nobody in the tenant can revoke TENANT_OWNER.

- **Gap:** API-REQ-014 is met only for the two new request models. The other request models are roadmap 4e.

### 4.6 Revocation and commit-time revalidation (IAM-REQ-014, -015, -036)

- **Spec requires:**
  - Revocation invalidates new authorisation immediately.
  - High-risk mutations re-check authority immediately before commit.
- **Code did:** roles came from the actor row on each request, but there was no revocation path. Commit-time revalidation covered the session only.
- **Code does:**
  - **New decisions:** authority is read from the registry on every request, so a revocation applies to the next decision (`test_role_assignment_lifecycle…`: 200 → revoke → 403).
  - **In-flight mutations:**
    - For state-changing routes of organisation principals, the guard records the assignment IDs that authorised the request.
    - A new `before_commit` listener (`_revalidate_authority`) re-reads those rows `FOR SHARE` and refuses the commit (403) unless one is still ACTIVE and in its validity window.
    - Revocation takes `FOR UPDATE` on the same row, so the two serialise.
  - **Evidence:** `test_a_mutation_does_not_commit_after_its_authorising_role_is_revoked` (PostgreSQL). With the listener removed by a negative control, it fails.
- **Gap:**
  - Zoiko platform principals' mutations are not revalidated at commit, because platform roles have no revocation path in any API.
  - Read requests are not revalidated at commit.
  - Sessions are not revoked on role revocation. That is not needed for correctness, since authority is re-read per request.

### 4.7 Resource scope, entity scoping and existence masking (IAM-REQ-009/010, ZA-API-001 error model, QA §09)

- **Spec requires:**
  - An entity-scoped grant confers access only to its exact entity; scope is resolved server-side.
  - QA §09: "cross-entity access within tenant: deny unless grant covers exact entity/scope", and "returns denied/not found without leaking existence".
- **Code did:** nothing was entity-scoped.
- **Code does:**
  - **Resolution:** routes declare `entity=` resolvers (`security.EntityOf` / `entity_of`). The resolver loads the resource inside the caller's tenant and returns its legal entity, from the row or, for governance records, the obligation's entity.
  - **Organisation-wide permission:** used directly; the resource is not resolved twice.
  - **Entity-scoped permission only:** the resource is resolved. It is allowed only when the entity matches. Otherwise the answer is **404 with the same body as a missing record**, and `authorization.denied` is recorded as `ENTITY_OUT_OF_SCOPE`.
  - **Tenant-wide lists:** an entity-scoped role never satisfies them (403).
  - **State-changing routes:** none has an entity resolver yet, so entity-scoped roles cannot mutate (fail closed). Three read-class POSTs (preview, evidence verify, package verify) do have one.
  - **Evidence:** `test_entity_scoped_roles_reach_their_entity_only`.
- **Gap:**
  - **Records without one entity** (issues, evidence conflicts, exceptions on controls, AI assets): entity-scoped principals cannot reach them.
  - **Lists:** no list is filtered to the principal's entities.
  - **Other scope types:** business units, engagements, resource collections and object sets (IAM §08) are not implemented.
- **Recommendation:** filter lists by entity for entity-scoped principals once G-08 confirms the read classes. Add entity resolvers to mutation routes one by one, each with tests.

### 4.8 External auditor (IAM §08, §14, IAM-REQ-027, -040; roadmap 4c)

- **Spec requires:**
  - EXTERNAL_AUDITOR may "read selected evidence/controls/packages; time-boxed; no tenant admin/business mutations".
  - Resource allowlist: "audit package(s), selected evidence manifests/payload views, control/obligation dossiers, reports. Default deny outside engagement."
  - Engagement-scoped and time-boxed, isolated from workforce membership; automatic revocation at expiry.
- **Code did:** the auditor held `read` and `verify` on every tenant read, including the audit log, outbox, people directory, facts, AI assets and rule drafts.
- **Code does:**
  - **Permissions:** EXTERNAL_AUDITOR holds `audit_read` and `verify`.
  - **Allowlist mapping (interpretation pending G-08):**
    - packages, evidence and its holds/ledger/IPE context
    - control and obligation dossiers: obligations, controls, snapshots, exceptions, compensations, issues, occurrences, risk acceptances, applicability determinations, evaluations and history comparison
    - reports: dashboard and attention
    - the published catalogue and published rule versions
  - **Denied:** the audit log, outbox, people directory, organisation profile and subjects, facts and fact requests, rule drafts and diffs, AI assets and policy bundles, connectors and the role registry.
  - **No workforce roles:** the role API refuses to give a workforce role to an EXTERNAL_AUDITOR principal or EXTERNAL_AUDITOR to anyone (IAM §14 "external identity isolation").
  - **Evidence:** `test_external_auditor_reads_only_the_engagement_allowlist`, `test_route_matrix…[actor-ext-auditor]`.
- **Gap (IAM-REQ-027, -040):**
  - There is no engagement object (`iam.external_engagement`), no per-engagement resource selection ("selected"), no expiry or automatic revocation, no sponsor and no watermarking of exports.
  - The allowlist is by resource class, not by selected records.
- **Recommendation:** model `iam.external_engagement` (tenant/entities, resources, sponsor, start/expiry) after G-08 and IAM-ADR-07, then replace `audit_read` with engagement-scoped resolution.

### 4.9 Super Admin and platform boundaries (IAM-REQ-024, IAM-INV-05, INV-14, WF-IAM-01)

- **Code did:** the Super Admin was refused tenant data by `ctx.tenant` inside the handlers, but could read the catalogue, rules and people directory through routes without a guard.
- **Code does:**
  - **Tenant data:** the Super Admin holds `manage_organisations` only, so every tenant read is refused at the guard (403, recorded with no tenant), as are catalogue and rule reads. The organisation registry routes are unchanged.
  - **Platform roles:** they exist only as PLATFORM-scope assignments (tenant NULL). The runtime role can read them only as a verified platform principal and can never write them (§7). No API grants or revokes them.
  - **Organisation Admin appointment:** it records a `PLATFORM_APPOINTMENT` assignment in the target tenant, granted by the Super Admin and audited under that tenant. The Super Admin gains no tenant role (`test_super_admin_has_no_tenant_authority_and_appointment_is_recorded`).
- **Changed expectation:** `test_refusals_are_recorded_as_authorization_denied` now expects `MISSING_PERMISSION` instead of `NO_TENANT_SCOPE` for a Super Admin reading `/api/evidence`. The guard now refuses before the handler runs; the refusal and its record are otherwise identical.

### 4.10 Governance event history endpoints (Stage 3c)

- **Code did:** the six `…/events` routes were authorised by sign-in plus tenant scope. Another tenant got 404 through `ctx.scoped`.
- **Code does:**
  - All six require `read` or `audit_read`. Exceptions, compensations, occurrences and legal holds also resolve their legal entity.
  - `test_governance_history_endpoints_enforce_read_authorization` checks each endpoint:

    | Caller | Result |
    |---|---|
    | CCO, INTERNAL_AUDIT, EXTERNAL_AUDITOR | 200 |
    | Another tenant's CCO | 404 |
    | Super Admin, content analyst, agent, TENANT_SECURITY_ADMIN | 403 |
    | No session | 401 |

    No refusal writes anything.
- **Not changed:** the Stage 3c write paths, events, triggers and migration 0008 (scope boundary).

### 4.11 Audit evidence (IAM §21, IAM-INV-09, IAM-REQ-037, -043)

- **Every assignment or revocation writes:**
  - an `iam_role_assignment_event` row: `ASSIGNED`/`REVOKED`, actor, reason, `authorized_by`, `policy_version`, record sequence
  - a `platform_audit_event` row, `role.assigned` / `role.revoked`, under the affected tenant, whose reason carries the role, principal, scope, validity, the grantor's authorising assignment IDs, the policy version and the stated reason
- **Credentials:** none are recorded.
- **Refusals:** they write `authorization.denied` with a reason code, as before. The new codes are `ENTITY_OUT_OF_SCOPE`, `SELF_ASSIGNMENT`, `NOT_DELEGABLE_BY_ACTOR`, `ROLE_NOT_ASSIGNABLE` and `ROLE_NOT_REVOCABLE`.
- **Gap:**
  - There is no correlation ID (IAM-REQ-037 "correlation") because the API has none yet.
  - ZA-EVT-001 has no role event type, so nothing reaches the outbox.

### 4.12 Watermark (audit I-10, SEC-REQ-001; roadmap 4f)

- **Code did:** `GET /api/watermark` was public and returned the cell record sequence, an activity-volume signal.
- **Code does:** it requires sign-in and `read`, `audit_read` or a content permission. The frontend history page already sends the session and tolerates an error.
- **Changed tests:** three tests read the watermark without signing in and now send a principal: `test_bitemporal_correction_preserves_as_known_then` (two calls) and `test_snapshot_determinism`.

## 5. Role and scope registry design

### 5.1 Tables (migration 0009)

**`iam_role_assignment`**, one row per assignment:

| Column | Notes |
|---|---|
| `assignment_id` | Primary key |
| `tenant_id` | NULL means a Zoiko platform principal |
| `principal_id` | |
| `role_code` | |
| `scope_type` | `TENANT`, `LEGAL_ENTITY` or `PLATFORM` |
| `scope_id` | The tenant, the entity, or NULL |
| `valid_from`, `valid_to` | The validity window |
| `status` | `ACTIVE` or `REVOKED` |
| `source` | `MIGRATION`, `BOOTSTRAP`, `SEED`, `PLATFORM_APPOINTMENT` or `ADMIN` |
| `granted_by`, `reason`, `record_seq`, `recorded_at` | |
| `revoked_at`, `revoked_by`, `revoke_reason`, `revoked_record_seq` | The closure |

CHECK constraints:

- `ck_iam_role_assignment_scope`: PLATFORM has no tenant and no scope ID; TENANT's scope ID equals its tenant; LEGAL_ENTITY names an entity.
- `ck_iam_role_assignment_status`
- `ck_iam_role_assignment_period`: `valid_to ≥ valid_from`.

**`iam_role_assignment_event`**, append-only history: `event_id`, `tenant_id`, `assignment_id`, `principal_id`, `event_type` (`ASSIGNED`/`REVOKED`), role, scope, validity, `source`, `actor_id`, `reason`, `authorized_by` (JSON list of the actor's authorising assignment IDs), `policy_version`, `record_seq`, `recorded_at`.

### 5.2 Policy decision (`security.Ctx.check`)

1. **Agents:** refused (unchanged).
2. **Platform-only routes** refuse organisation principals. **Human-only routes** refuse non-HUMAN principals.
3. **Self-service** routes (`GET /me`) allow the caller.
4. **Organisation- or platform-wide authority:** the permissions of TENANT and PLATFORM assignments in force. If one matches the route's permissions, the request is allowed, and the assignment IDs are kept as `Ctx.authority`.
5. **Entity-scoped authority:** otherwise, if the route has an entity resolver and the principal holds a matching LEGAL_ENTITY assignment, the resource is resolved inside the tenant:
   - a missing resource gets 404
   - a matching entity is allowed
   - a different entity gets 404, recorded as `ENTITY_OUT_OF_SCOPE`
6. **Otherwise:** 403 `MISSING_PERMISSION`, recorded.
7. **Commit:** for state-changing routes of organisation principals, the authorising assignments are re-read before commit (§4.6).

### 5.3 Role catalogue after Stage 4

| Role | Permissions | Assignable via `/role-assignments` |
|---|---|---|
| CCO | read, attest, approve_exception, request_evaluation, manage_facts, challenge, package, manage_issues, request_exception, assurance_review | Yes: `manage_role_assignments`; TENANT or LEGAL_ENTITY; HUMAN |
| LEGAL_REVIEWER | read, attest, challenge, request_evaluation, legal_disposition | As above |
| REGULATORY_ANALYST | read, manage_facts, request_evaluation, challenge | As above |
| CONTROL_OWNER | read, submit_evidence, manage_controls, manage_issues, request_exception | As above |
| INTERNAL_AUDIT | read, package, verify, reevaluate_ipe | As above |
| AI_GOVERNANCE_LEAD | read, manage_ai, manage_facts, request_evaluation, grant_authority, request_policy_decision | As above |
| PLATFORM_ADMIN | read, manage_connectors, manage_tenant, checkpoint, reevaluate_ipe | As above |
| RISK_APPROVER | read, approve_exception, approve_compensation, assurance_review | As above |
| **TENANT_SECURITY_ADMIN** (new; IAM §08) | **manage_role_assignments** | Yes: `delegate_admin`; TENANT; HUMAN |
| TENANT_OWNER | read, manage_tenant, **delegate_admin** (new) | No: Super Admin appointment |
| EXTERNAL_AUDITOR | **audit_read** (new; replaces read), verify | No (revocable only) |
| SERVICE | read, submit_evidence, request_evaluation, manage_facts, checkpoint, request_policy_decision | No (revocable only) |
| SUPER_ADMIN, CONTENT_*, ENCODING_ENGINEER, CONNECTOR_CERTIFICATION_MANAGER | Unchanged | No (platform; no API) |

**Document conflict, not resolved here.** The tenant role codes in the code are ZA-PRD-001 §4 personas: CCO, Legal Reviewer, Regulatory Analyst, Control Owner, Internal Audit, AI Governance Lead and Platform Administrator. ZA-IAM-001 §08 defines a different template set: COMPLIANCE_MANAGER, LEGAL_ATTESTOR, ASSURANCE_REVIEWER, EVIDENCE_MANAGER, ENTITY_ADMIN, INTEGRATION_ADMIN, AUDIT_MANAGER, EXECUTIVE_READ and REGULATOR_REVIEWER. Only TENANT_OWNER, TENANT_SECURITY_ADMIN and EXTERNAL_AUDITOR exist in both. This stage adds one IAM §08 template and does not rename the rest. Reconciling the two vocabularies is for the IAM Lead and the PRD owner (ZA-DOC-001 §01: by subject-matter authority).

## 6. Enforcement coverage by endpoint

140 operations: 72 GET and 68 state-changing.

- **GET:** 5 public, 1 self-service and 66 permission-guarded. 17 GETs resolve an entity.
- **State-changing:** 4 public and 64 permission-guarded, including 2 new role-registry routes. 3 read-class POSTs resolve an entity.

The GET table is generated from `security.operations()`. "+entity" marks a route that resolves its legal entity for entity-scoped roles.

| GET route | operationId | Requirement |
|---|---|---|
| `/api/health`, `/api/dictionary`, `/api/trust/public-key`, `/api/auth/session`, `/api/auth/demo-accounts` | `core.health`, `core.dictionary`, `evidence.public_key`, `auth.session`, `auth.demo_accounts` | Public (reasons in `security.PUBLIC_READS`) |
| `/api/me` | `core.me` | Self-service |
| `/api/audit`, `/api/outbox`, `/api/org/tenant`, `/api/org/subjects`, `/api/fact-requests`, `/api/ai/assets` | `core.audit_log`, `core.outbox`, `core.tenant`, `core.subjects`, `applicability.fact_requests`, `ai.assets` | read |
| `/api/subjects/{subject_id}/facts`, `…/facts/history`, `/api/ai/policy-bundle/{agent_subject_id}` | `applicability.fact_profile`, `applicability.fact_history`, `ai.bundle` | read +entity |
| `/api/evidence`, `/api/evidence-conflicts`, `/api/evidence-conflicts/{conflict_id}/events`, `/api/ledger`, `/api/packages` | `evidence.list_evidence`, `evidence.conflicts`, `evidence.conflict_events`, `evidence.ledger`, `evidence.packages` | read \| audit_read |
| `/api/evidence/{evidence_id}`, `…/download`, `…/holds/events`, `/api/packages/{package_id}/download` | `evidence.get_evidence`, `evidence.download`, `evidence.hold_events`, `evidence.download_package` | read \| audit_read +entity |
| `/api/obligations`, `/api/controls`, `/api/exceptions`, `/api/compensations`, `/api/issues`, `/api/issues/{issue_id}/events`, `/api/risk-acceptances` | `assurance.obligations`, `assurance.controls`, `assurance.list_exceptions`, `assurance.list_compensations`, `assurance.issues`, `assurance.issue_events`, `assurance.risks` | read \| audit_read |
| `/api/obligations/{lineage}`, `/api/controls/{ci_id}`, `/api/snapshots/{snapshot_id}`, `…/reproduce`, `/api/exceptions/{exception_id}/events`, `/api/compensations/{comp_id}/events`, `/api/occurrences/{occurrence_id}/events` | `assurance.dossier`, `assurance.control_detail`, `assurance.get_snapshot`, `assurance.reproduce`, `assurance.exception_events`, `assurance.compensation_events`, `assurance.occurrence_events` | read \| audit_read +entity |
| `/api/applicability/determinations`, `/api/applicability/evaluations` | `applicability.determinations`, `applicability.list_evaluations` | read \| audit_read |
| `/api/applicability/evaluations/{evaluation_id}`, `/api/history/compare` | `applicability.get_evaluation`, `applicability.compare` | read \| audit_read +entity |
| `/api/ipe/policies`, `/api/ipe/source-runs`, `/api/ipe/anomalies`, `/api/dashboard`, `/api/dashboard/attention` | `integration.policies`, `integration.runs`, `integration.anomalies`, `dashboard.dashboard`, `dashboard.attention` | read \| audit_read |
| `/api/ipe/source-runs/{run_id}` | `integration.run_detail` | read \| audit_read +entity |
| `/api/catalog/*` (10 routes), `/api/rules/versions`, `/api/rules/versions/{rv_id}`, `/api/watermark` | `catalog.*`, `rules.versions`, `rules.version`, `core.watermark` | read \| audit_read \| content_author \| content_peer_review \| content_legal_review \| content_release |
| `/api/rules/drafts`, `/api/rules/drafts/{draft_id}`, `/api/rules/diff` | `rules.list_drafts`, `rules.get_draft`, `rules.diff` | read \| content_* |
| `/api/actors` | `core.actors` | read \| content_* \| manage_organisations \| certify_connectors |
| `/api/connectors` | `integration.connectors` | read \| certify_connectors |
| `/api/platform/organisations`, `/api/platform/organisations/{tenant_id}` | `platform.list_organisations`, `platform.get_organisation` | manage_organisations, human, platform (unchanged) |
| `/api/roles`, `/api/role-assignments`, `/api/role-assignments/{assignment_id}/events`, `/api/principals/{principal_id}/effective-access` | `iam.role_catalogue`, `iam.list_role_assignments`, `iam.role_assignment_events`, `iam.effective_access` | manage_role_assignments \| delegate_admin |

**Changed state-changing guards:**

- **Read-class POSTs:** `POST /obligations/{lineage}/preview` now needs read | audit_read and resolves its entity. The evidence, ledger and package `…/verify` POSTs now need read | verify; evidence and package verify resolve their entity.
- **New routes:** `POST /role-assignments` and `POST /role-assignments/{assignment_id}/revoke` need manage_role_assignments | delegate_admin, human.
- **Everything else:** unchanged. `test_route_matrix_refuses_everything_not_explicitly_allowed` still proves it.

**Cross-tenant coverage:** `test_cross_tenant_reads_fail_on_every_identifier_route` substitutes tenant A identifiers into every GET route with a tenant-record path parameter (20 or more routes). It calls each as tenant B's CCO and expects 403 or 404 for all of them. It covers the history routes and `/history/compare`.

## 7. Database security verification

| Control | Verified state | Evidence |
|---|---|---|
| FORCE RLS on the new tables | Both tables `relrowsecurity = relforcerowsecurity = true` | `test_every_tenant_and_identity_table_has_forced_rls_and_reviewed_policies` (tenant tables now 50) |
| Policies | `tenant_isolation` (ALL, `assure_app_rw`/`assure_app_ro`, the 0007 verified-context predicate); `migrator_all` (`assure_migrator`); `platform_rows_select` (SELECT only, tenant_id NULL and verified platform principal). No platform INSERT/UPDATE/DELETE policy | Same test (exact policy set per table) |
| Least privilege | `assure_app_rw`: SELECT and INSERT on both tables; UPDATE only on the six closure columns of `iam_role_assignment`; no DELETE, TRUNCATE, REFERENCES or TRIGGER. `assure_app_ro`: SELECT | `test_runtime_role_privileges_and_triggers_match_the_reviewed_policy` (classification: event table APPEND_ONLY, assignment table GUARDED) |
| Row guards | `immutable_columns` trigger (`assure_forbid_mutation_except` with the closure columns) on assignments; `append_only` (`assure_forbid_mutation`) on events. Both also stop the owner role's UPDATE/DELETE | `test_completed_results_cannot_be_rewritten_even_by_the_owner_role`, `test_guarded_rows_change_only_in_their_completion_columns`. An attempt during development to back-date `valid_from` was refused by the trigger |
| Ownership | `assure_table_owner` (non-login) | `test_dat13_roles_memberships_and_ownership` |
| Signed tenant context | Unchanged (0007). The registry is read under the request's verified context; nothing new can set a tenant | `test_tenant_security.py` (49 tests) |
| Runtime role | No BYPASSRLS, no superuser, no extra membership (IAM-REQ-041, SEC-REQ-023) | `test_runtime_role_holds_no_membership_ownership_or_extra_table_privilege` |
| Registry RLS from the runtime role | No context sees zero rows. A tenant sees only its rows. Platform rows are visible only to a verified platform principal. Inserting a platform row or another tenant's row fails RLS. UPDATE of `role_code`, DELETE and any event UPDATE/DELETE are refused | `test_role_registry_rows_are_tenant_isolated_and_closure_only_in_postgresql` |

**Pre-existing observation, outside Stage 4, not changed.** The Stage 3c report ([task-4-2](task-4-2-append-only-governance-events-report.md) §3.2) says the 0008 `tenant_isolation` policies are `AS RESTRICTIVE`. Migration 0008 actually creates them PERMISSIVE, like every 0007 policy. Isolation still holds, because each such table has no other permissive policy for the runtime roles. The report's text is inaccurate, not the database.

**Unchanged and still open** (from the Task 2 and Stage 2 records):

- the policies' `OR assure_system()` branch, now signed: the system-job design (I-01 remainder)
- owner-held DDL that can disable triggers (A10)

## 8. Test results

All runs are local; none ran in GitHub Actions. The PostgreSQL runs used PostgreSQL 17.11.

| Run | Before Stage 4 | After Stage 4 |
|---|---|---|
| SQLite, full suite (`pytest tests -q`) | 140 passed, 88 skipped, 0 failed | **153 passed, 91 skipped, 0 failed** |
| PostgreSQL 17, full suite, **fresh** `assure_test` database in a throwaway cluster (scratchpad, port 55450; mirrors CI's fresh database) | 228 passed, 0 skipped, 0 failed | **244 passed, 0 skipped, 0 failed** |
| CI full-suite gate (`.github/scripts/junit_gate.py --no-skips` with every `--require`, including the new `test_role_registry=16`) on that report | — | **PASS** (rls 5, pg_bootstrap 8, migrations 16, immutability 7, snapshot_determinism 4, rule_integrity 19, tenant_security 49, auth_session_security 10, role_registry 16) |
| PostgreSQL 17, full suite, the designated persistent `assure_test` on 127.0.0.1:5433 (migrated 0008 → 0009 by the run) | 200 passed, 3 failed, 25 errors | 214 passed, 5 failed, 25 errors |

**Why the persistent `assure_test` fails both before and after.** The failures are environmental, and they occurred before any Stage 4 change. After the change, the same 3 failures and 25 errors recur. The 2 additional failures are new Stage 4 tests (`test_entity_scoped_roles_reach_their_entity_only` and `test_external_auditor_reads_only_the_engagement_allowlist`): both download the first evidence payload, which on this database is one of the missing files, and fail with the same `FileNotFoundError` in `evd.read_payload`. Both pass on every fresh database. The causes:

- **Missing payloads:** the database holds evidence rows whose payload files lived in an earlier session's temporary data directory, now deleted. This is `FileNotFoundError` in every package-verifier setup.
- **Stale bootstrap credentials:** `test_bootstrap_is_one_time…` gets "Email or password is incorrect".
- **Accumulated facts:** `test_bitemporal_correction…` sees APPLIES vs DOES_NOT_APPLY.

`.github/scripts/ci_postgres_bootstrap.sql` states the rule: "every pytest session needs a database of its own … a database reused by a second session points at files that no longer exist". As instructed, the database was not dropped or recreated, so the authoritative PostgreSQL results are the fresh-database runs above.

**Focused Stage 4 tests** (`tests/test_role_registry.py`, 16 tests):

- **SQLite:** 13 passed, 3 skipped. The PostgreSQL-only tests are the RLS test, the 0009 backfill test and commit-time revalidation, which needs a second concurrent writer.
- **PostgreSQL:** 16 of 16 passed.

**Negative controls.** Each control weakens one protection at run time through a scratchpad pytest plugin; the source is unchanged. The SQLite runs exclude the 3 PostgreSQL-only tests.

| Weakened protection | Tests that fail |
|---|---|
| Entity scope treated as organisation-wide | `test_entity_scoped_roles_reach_their_entity_only` (1 of 13) |
| Every grantor treated as authorised | lifecycle, escalation (2 of 13) |
| Validity windows and revocation ignored | lifecycle, validity (2 of 13) |
| External auditor given workforce `read` | auditor allowlist (1 of 13) |
| Every GET allowed for any signed-in principal | 8 of 13. The cross-tenant sweep still passes: tenant isolation (`ctx.scoped` and RLS) is an independent layer, which is the intended defence in depth |
| Commit-time revalidation removed (PostgreSQL) | `test_a_mutation_does_not_commit_after_its_authorising_role_is_revoked` (1 of 16) |

**Existing tests changed** (expectations or fixtures only; every change is in §12):

- Fixtures create principals through `conftest.add_principal`, which records registry assignments as the application now does.
- `test_authorization` allows TENANT_OWNER to reach the two role-registry mutation routes, which is decision 1.
- `test_auth_session_security` expects `MISSING_PERMISSION` instead of `NO_TENANT_SCOPE` (§4.9).
- Three watermark reads now sign in (§4.12).
- The reviewed classifications gain the two new tables: `test_immutability`, `test_migrations` and `test_tenant_security` (tenant tables 48 → 50, with the registry tables' policy set).
- The immutability probe may set nullable override columns, which the new CHECK constraint requires.

**Skipped tests.** On PostgreSQL: none. On SQLite: 91, all PostgreSQL-only: 90 read "PostgreSQL-only", and 1 (an existing Stage 3c test) reads "PostgreSQL trigger enforcement only". QA-REQ-005 counts none of them as evidence.

**Known flaky test** (Task 0 record): `test_fact_assertion_preserves_non_integrity_evaluation_errors`. It passed in every run above.

## 9. Migration verification

| Check | Result |
|---|---|
| Upgrade from empty to head (0009), in order, through the baseline | `test_fresh_install_upgrades_in_order_through_the_baseline_to_head`: pass |
| Repeat upgrade changes nothing | `test_repeat_upgrade_changes_nothing`: pass |
| Newest revision (now 0009) downgrades and re-upgrades to an identical catalogue fingerprint | `test_newest_revision_downgrades_and_upgrades_again`: pass |
| Downgrade to the baseline restores its privileges exactly | `test_downgrade_to_the_baseline_restores_its_privileges_exactly`: pass |
| Models and migrations agree (autogenerate empty) | `test_models_and_migrations_agree`: pass |
| Every tenant table has RLS | `test_every_tenant_table_has_row_level_security`: pass |
| Backfill (DAT-REQ-024) | `test_migration_0009_backfills_roles_without_inventing_history`: pass (detail below) |
| API start-up runs no DDL and refuses a database not at head | pass |

**Backfill detail.** In a throwaway schema migrated to 0008 and then to 0009:

- Each JSON role became one ACTIVE assignment at TENANT or PLATFORM scope, source `MIGRATION`, `valid_from` ≥ the migration start (never earlier).
- The record sequence advanced by exactly the number of assignments (101–103), with one `ASSIGNED` event each by `system:migration:0009`.
- Empty and non-array `roles` produced nothing.
- Downgrading to 0008 removed both tables and left `org_actor.roles` unchanged.

**Development database `assure`: not migrated.** It is at revision **0007**: Stage 3c's 0008 has not been applied to it either. Before the API at this build can start against it, the owner must:

1. take and verify a backup
2. run `python -m app.migrate upgrade`, which applies 0008 and 0009

The procedure is in [database-migrations.md](database-migrations.md).

**Downgrade consequence.** Downgrading 0009 drops entity-scoped assignments; the old code cannot express them, so this fails closed. TENANT/PLATFORM roles survive in the `org_actor.roles` cache. Assignment and revocation history is lost on downgrade. That is acceptable before production, as the roadmap's rollback row says.

## 10. Git and canonical database preservation

| Item | State at the end of this stage |
|---|---|
| Branch | `naveen-changes` (unchanged) |
| Staged changes | The same 63 staged paths as at the start. Nothing was staged or unstaged |
| Stash | `stash@{0}` "On naveen-changes: WIP before updating main" (d9560bb) is present. At the start of the session I listed it once with the read-only `git stash list`. Afterwards it was checked only with `git rev-parse refs/stash` / `git reflog show refs/stash`. No stash was created, applied or dropped |
| Git commands run | `git status`, `git diff --name-only` / `--cached --name-only`, `git branch --show-current`, `git rev-parse`, `git reflog show refs/stash`, and the one `git stash list`. **None of** add, commit, push, pull, stash push/pop/apply/drop, reset, restore or clean |
| New work | Working tree only: modified tracked files and new untracked files (§12). `backend/app/engines/assurance.py` and `backend/app/platform.py` had unstaged changes before this session and were not touched |
| Canonical `assure` database | Read-only revision check only (`SELECT … FROM alembic_version`). Still at **0007**. No migration, seed, write or backup was run against it |
| `assure_test` (127.0.0.1:5433) | Used as the designated disposable test database. The test harness migrated it from 0008 to **0009** and wrote test data, as every earlier run did. It was not dropped or recreated |
| Throwaway cluster | `initdb` in the session scratchpad on port 55450, with fresh `assure_test`, `assure_test_b1`…`_b4` databases. It is separate from every existing server and is stopped at the end of the session |

## 11. Remaining gaps and approval dependencies

| Item | Why open | Owner / decision |
|---|---|---|
| G-08 read policy: confirm or replace the read classes (§4.2) and the auditor allowlist mapping (§4.8) | Interim interpretation only | IAM Lead |
| Engagement-scoped external access (IAM-REQ-027 "selected", time-boxed; IAM-REQ-040 expiry revokes access; watermarking) | No `iam.external_engagement` model; IAM-ADR-07 open | IAM Lead, General Counsel (IAM §00 reviewer for auditor flows) |
| Role vocabulary: PRD §4 personas vs IAM §08 templates (§5.3) | Document conflict | IAM Lead and PRD owner (ZA-DOC-001 §01) |
| `SERVICE` read narrowing (4c) and workload identity (IAM §15) | Not specified per operation | IAM Lead; Stage 6 |
| 4d dev-header start-up guard (I-08) | Not in this stage's requested scope; needs a defined environment marker | Backend / Security |
| 4e strict request models (I-09, API-REQ-014) for the existing request models | Not in this stage's requested scope; may break clients that send extra fields | API Lead |
| Read-side route × role matrix (roadmap test row) | Coverage is by class and sweep, not a generated per-role read matrix | QA Lead |
| Entity filtering of lists; entity resolvers on mutations; business-unit, engagement and object-set scopes | Design follows G-08 | IAM Lead, Data Architect |
| Bitemporal form of IAM rows (closure + events vs `record_to_seq` revisions) | §4.3 | Data Architect |
| Dual approval for material roles; access-review campaigns (IAM §10, §20, IAM-REQ-038/039) | Not built | IAM Lead, Security |
| Back-dated `valid_from` refusal (D5); the `delegate_admin` permission (D1); TENANT-only security admin (D4) | Interpretations made here | IAM Lead (confirm) |
| Signed `assure_system()` bypass in policies; owner DDL can disable triggers (A10) | Pre-existing; system-job design | Data Architect, Security |
| Policy release control (IAM-REQ-013): signed, versioned policy bundles, cell-local PDP (IAM-ADR-01) | Registry and rules are in code | CTO / Security |
| Development database at 0007 | Needs 0008 and 0009 after a verified backup | Database owner (the requester) |
| GitHub Actions run of the new gate | Local reproduction only | Requester |

## 12. Files changed

**New:**

- `backend/app/iam.py`: role registry, assignment rules, effective access, provisioning
- `backend/app/routers/iam.py`: `/roles`, `/role-assignments`, `/role-assignments/{id}/events`, `/role-assignments/{id}/revoke`, `/principals/{id}/effective-access`
- `backend/migrations/versions/0009_scoped_role_registry.py`
- `backend/tests/test_role_registry.py` (16 tests)
- this report

**Modified, backend:**

- `app/security.py`: registry-based decisions, entity resolution, the commit-time authority check, the operation registry, public reads, new role and permissions
- `app/main.py`: operationIds, all-method start-up check, the IAM router
- `app/models.py`: `RoleAssignment`, `RoleAssignmentEvent`; `roles` documented as a cache
- `app/auth.py`: actor view from the registry; principal set at sign-in
- `app/accounts.py`, `app/routers/platform.py`, `app/seed.py`: recorded assignments on every creation path; Organisation Admins listed from the registry
- `app/routers/{core,catalog,rules,applicability,assurance,evidence,integration,ai,dashboard}.py`: read guards and entity resolvers. The evidence download's in-handler `require("read")` is replaced by the route guard
- `tests/conftest.py`: `add_principal`
- `tests/test_authorization.py`, `test_auth_session_security.py`, `test_governance_events.py`, `test_tenant_security.py`, `test_immutability.py`, `test_migrations.py`, `test_api.py`, `test_snapshot_determinism.py`: see §8

**Modified, other:**

- `.github/workflows/ci.yml`: `--require test_role_registry=16`
- `frontend/lib/roles.ts`: display labels for TENANT_SECURITY_ADMIN, `audit_read`, `manage_role_assignments`, `delegate_admin`
- `docs/backend-architecture-roadmap.md`: Stage 4 status row
- `docs/database-migrations.md`: revision 0009 row. The table still has no rows for 0007 and 0008; that predates this stage

**Not changed** (scope boundary):

- the Stage 3c governance engine, its events and migration 0008
- the assurance, applicability, rules, evidence and IPE engines
- idempotency (Stage 5)
- the canonical database

---

## Addendum: development database upgraded to 0009 (2 November 2026)

**Trigger.** The local backend (`uvicorn --reload` on 127.0.0.1:8000) stopped answering. Its worker refused to start with `SchemaVersionError: … at revision 0007; pending 0008, 0009 (head 0009)`. That is the intended start-up guard: the code needs revision 0009 and `assure` was at 0007. The requester asked for back-up, rehearsal and upgrade.

| Step | Result |
|---|---|
| Backup | `backend/var/backups/assure-before-0009-20261102-171939.dump` (custom format), plus the evidence store `backend/var/backups/var-before-0009-20261102-171939/` (objects, packages, worm, keys). The first `pg_dump` attempt failed: since 0007, FORCE RLS needs `--enable-row-security`. The incomplete file was deleted, the dump redone, and the documented command in [database-migrations.md](database-migrations.md) corrected |
| Backup verified | Restored into a new database on a throwaway PostgreSQL 17 cluster. Identical to live in every one of 65 tables (row count and content digest), revision 0007, watermark 246, 624 rows, overall digest `dba729dde8e74a01` |
| Rehearsal | `python -m app.migrate upgrade` on a second restored copy: 0007 → 0009, `check` passed, account bootstrap left both existing accounts unchanged. 59 existing tables were byte-identical; `alembic_version` and the record sequence changed (246 → 263). New tables: six Stage 3c event tables (0008 baseline rows) and the registry tables with 17 assignments for 17 `org_actor.roles` entries; 0 principals whose cache differs. The API started on the copy and gave the expected answers for every existing principal |
| Upgrade of `assure` | Same command: 0007 → 0009, `check` passed. Compared with the backup, only `alembic_version` and `platform_record_sequence` changed in the 65 existing tables (63 unchanged), and the 8 new tables hold the same row counts as in the rehearsal |
| Server | Worker reloaded: `GET /api/health` 200, `GET /api/me` and `GET /api/watermark` without a session 401 (expected) |
