# Sidebar ↔ backend integration matrix

Date: 2026-11-04. Branch `naveen-changes`, working tree on top of `73106fe`. Schema head: migration 0013 (no migration
added by this work).

Scope: the two operational dashboards and every item in their sidebars.

- **Organisation Dashboard**: customer-organisation users. The dark "Organization workspace" sidebar is `ORG_NAV` in
  `frontend/components/nav.ts`, rendered by `OrgSidebar` in `frontend/components/Shell.tsx`. Each of its 16 items opens
  one `NAV` screen, and its `more` screens appear beneath it.
- **Super Admin Dashboard**: the Zoiko platform principal (`SUPER_ADMIN`, no tenant). The `NAV` items with
  `for: ALL` are rendered by `SidebarBody` (`navFor("platform", …)`).

Not in scope: the `/design-system/*` pages are Design Governance pattern libraries on sample data (their own headers
say so), not dashboards, so they have no sidebar entries here. The content-operations home (Zoiko content staff) is a
third persona of the same Zoiko-staff chrome. Its screens appear below only where the organisation sidebar shares them
(Change Radar).

Governing documents: ZA-IAM-001 (§05, §08, §10–§14, §20–§25 WF-IAM-01..08), ZA-TEN-001 (§24 WF-TEN-01), ZA-API-001
(§04, §11, §18, API-REQ-008/012/014/023/033/042), ZA-PRI-001, ZA-STA-001. Every ZA document is still "Proposed for
lock". The read policy is the provisional table pending decision G-08 (`backend/tests/read_authorization_matrix.json`).

## How visibility, authorization and this matrix stay in step

- `frontend/lib/screen-access.json` lists, for every sidebar screen, the GET endpoints it loads when it opens (`reads`,
  maintained by hand). It also lists the permissions that let a person open it (`perms`). `perms` is **generated** from
  the backend route guards: it is the intersection of what every listed read accepts.
- The sidebar, "Jump to" search and post-sign-in redirect (`navFor`, `orgNav`, `jumpTargets`, `canOpen`) show a
  screen only when the person holds one of its `perms` organisation- or platform-wide. The persona filter (`for`) still
  applies on top.
- The same file's `shell` block covers the reads every page makes: the header record watermark, alert counts and the
  sidebar's entity scope. Its `panels` block covers in-page panels (Team & Access directory, role registry, a member's
  sessions; the Overview figures). The frontend makes these reads only for people the server will serve.
- `backend/tests/test_sidebar_integration.py` fails when:
  - a `perms` list drifts from the guards;
  - a sidebar screen is missing from the file;
  - any frontend `api`/`useApi`/`download`/`fetch` call names a route or method the backend does not have;
  - for one principal per role, a shown screen is refused or a hidden screen is served.

  Regenerate the file with `python tests/test_sidebar_integration.py` (from `backend/`).

The backend still authorises every request on its own. Sidebar visibility is a convenience, never a control.

Status legend: **Implemented** (backend serves everything the screen does, tested), **Partial** (screen works; a
spec-defined part is missing), **Missing** (spec-defined, not built, not blocked), **Blocked** (needs a decision or a
capability that does not exist).

Permission shorthand: `read` (organisation workforce read), `audit_read` (ZA-IAM-001 §14 external-auditor allowlist),
`content_*` (the four Zoiko content-operations permissions). Tenant scope "caller's tenant" means the tenant comes from
the authenticated principal (`Ctx.tenant`, API-REQ-008), enforced in the query and again by PostgreSQL RLS (FORCE RLS,
migrations 0002/0007). Another tenant's identifiers answer 404.

## 1. Organisation Dashboard

| # | Sidebar label (section) | Frontend route(s) | Backend endpoints (method) | Module / service | Database entities | Required permission (sidebar `perms`; actions) | Tenant scope | Status | Test coverage | Remaining blockers |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Overview (Workspace) | `/` | GET `/dashboard`, GET `/controls`, GET `/ipe/anomalies`, GET `/org/tenant` | `routers/dashboard.py`, `routers/assurance.py`, `routers/integration.py`, `routers/core.py`; `engines/evidence.py` | obligation instance revisions, occurrences, snapshots, applicability evaluations/determinations, evidence artifacts + ledger head, issues, fact requests, outbox | `read`\|`audit_read` (`/org/tenant`: `read`). A role administrator without either sees a notice pointing to Team & Access, not failing figures | caller's tenant | **Partial**: "Obligations due / late" is now a recount of recorded occurrences (`obligation_occurrences`, added). Controls, evidence-gap and approvals tiles were already real. "Changes needing review", "Agent actions held", the Priorities table and the Coverage panel stay marked SYNTHETIC | `test_overview_obligation_occurrence_figures_are_recorded_counts`, `test_each_visible_screen_loads…`, `test_api.py` dashboard tests | No spec-defined record or measure for the four synthetic panels (ZA-STA-001 §22 forbids an invented score). Needs a product/Head of Assurance definition |
| 2 | My Work & Approvals (Workspace) | `/fact-requests` | GET `/fact-requests`; POST `/subjects/{id}/facts` (assert from a request) | `routers/applicability.py`, `engines/facts.py` | `applicability_fact_request`, fact assertion revisions | `read`; assert: `manage_facts` | caller's tenant, entity-resolved | **Implemented** | `test_api.py`, `test_engines.py`, sidebar visibility test | — |
| 3 | Change Radar (Regulation) + Sources & Provisions, Rule Authoring, Rule Test Workbench | `/regulatory`, `/sources`, `/rules`, `/rules/[id]`, `/rules/workbench` | GET `/catalog/sources`, `/catalog/instruments`, `/catalog/obligation-definitions`, `/catalog/fact-definitions`, `/catalog/controls`, `/catalog/requirements/{id}`, `/rules/drafts`, `/rules/drafts/{id}`, `/rules/versions`, `/rules/diff`. Mutations (POST/PUT sources, extract, provisions, verify, drafts, test, sandbox, review, publish) are Zoiko content staff only | `routers/catalog.py`, `routers/rules.py`, `engines/rules.py` | global catalogue: source records, instruments, requirement versions, provisions, rule drafts/versions, content releases | `read`\|`audit_read`\|`content_*` (Rule screens: `read`\|`content_*`); mutations: `content_*` + platform principal | global regulatory content (no tenant data) | **Implemented** (read-only for organisation users by design: Zoiko authors content) | `test_authorization.py` (content routes refused to organisation users), `test_rule_integrity.py`, visibility test | — |
| 4 | Obligations (Regulation) | `/obligations`, `/obligations/[lineage]` | GET `/obligations`, `/obligations/{lineage}`, `/snapshots/{id}`, `/snapshots/{id}/reproduce`, `/occurrences/{id}/events`; POST `/obligations/{lineage}/preview`, `/obligations/{lineage}/snapshots`, `/obligations/{lineage}/occurrences`, `/occurrences/{id}/fulfil` | `routers/assurance.py`, `engines/assurance.py` | obligation instance revisions, assurance snapshots, occurrences + occurrence events | `read`\|`audit_read`; snapshot: `request_evaluation`\|`package`; occurrences: `manage_controls`\|`manage_facts` | caller's tenant, entity-resolved | **Implemented** | `test_snapshot_determinism.py`, `test_governance_events.py`, `test_api.py` | Collection pagination (API-REQ-012) not applied (see §4) |
| 5 | Controls & Testing (Assurance) | `/controls`, `/controls/[id]` | GET `/controls`, `/controls/{id}`, `/catalog/controls`, `/org/subjects`; POST `/controls`, `/controls/{id}/evaluate`, `/test-runs` | `routers/assurance.py`, `engines/assurance.py` | control implementation revisions, test policies/definitions, test runs, control evaluations | `read`\|`audit_read`; implement: `manage_controls`; evaluate: `manage_controls`\|`request_evaluation`\|`package`; test run: `submit_evidence` | caller's tenant, entity-resolved | **Implemented** | `test_api.py`, `test_engines.py`, `test_authorization.py` | — |
| 6 | Evidence & IPE (Assurance) + IPE, Source Runs | `/evidence`, `/evidence/[id]`, `/ipe`, `/ipe/runs`, `/ipe/[id]` | GET `/evidence`, `/evidence/{id}`, `/evidence/{id}/download`, `/evidence/{id}/holds/events`, `/evidence-conflicts`(+`/{id}/events`), `/ipe/anomalies`, `/ipe/policies`, `/ipe/source-runs`(+`/{id}`); POST `/evidence/upload`, `/evidence/{id}/verify`, `…/supersede`, `…/invalidate`, `…/hold`, `…/release-hold`, `…/erase`, `…/bindings`, `/evidence-conflicts`(+`/resolve`), `/ipe/anomalies/{id}/dispositions`, `/ipe/source-runs/{id}/evaluate-ipe` | `routers/evidence.py`, `routers/integration.py`, `engines/evidence.py`, `engines/ipe.py` | evidence artifacts, ledger events, legal hold revisions, bindings, conflicts, source runs, IPE evaluations/anomalies | `read`\|`audit_read`; verify: `read`\|`verify`; submit: `submit_evidence`; hold/erase: `attest` + HUMAN; review: `assurance_review` + HUMAN; IPE re-run: `reevaluate_ipe` + HUMAN | caller's tenant, entity-resolved | **Implemented** | `test_immutability.py`, `test_package_verifier.py`, `test_api.py`, `test_rls.py` | `/evidence` is unpaginated (changing it to the §04 envelope breaks the evidence, retention and obligation screens and existing tests; deferred) |
| 7 | Findings & Actions (Assurance) | `/governance` | GET `/exceptions`, `/compensations`, `/issues`, `/risk-acceptances` (+ `/{id}/events`); POST `/exceptions`, `/exceptions/{id}/decide`, `/compensations`, `/compensations/{id}/decide`, `/issues/{id}/transition`, `/risk-acceptances` | `routers/assurance.py`, `engines/governance.py` | exception/compensation/risk-acceptance revisions + events, issues + issue events | `read`\|`audit_read`; request: `request_exception`; decide: `approve_exception`/`approve_compensation` + HUMAN; issues: `manage_issues` | caller's tenant, entity-resolved | **Implemented** | `test_governance_events.py`, `test_governance_projections.py`, `test_authorization.py` | — |
| 8 | Assurance Reports (Assurance) + Audit Package Builder, Historical Reconstruction | `/assurance`, `/packages`, `/history` | GET `/obligations`, `/org/tenant`, `/packages`, `/packages/{id}/download`, `/trust/public-key` (public), `/history/compare`, `/org/subjects`, `/catalog/obligation-definitions`, `/watermark`, `/rules/versions/{id}`; POST `/packages`, `/packages/{id}/verify`, `/packages/verify-upload` (public verifier) | `routers/assurance.py`, `routers/evidence.py`, `routers/applicability.py`, `verifier/` | assurance snapshots, audit packages, ledger checkpoints | `read` (packages/obligations also `audit_read`); build: `package` | caller's tenant | **Implemented** | `test_package_verifier.py`, `test_snapshot_determinism.py`, `test_api.py` | Package build runs inline, not as an Operation (API-REQ-021) |
| 9 | Entities & Scope (Governance) | `/entities`, `/entities/[subjectId]` | GET `/org/tenant`, `/org/subjects`, `/subjects/{id}/facts`(+`/history`), `/applicability/evaluations?subject_id=`; POST `/org/subjects`, `/subjects/{id}/facts`, `/applicability/evaluate-all` | `routers/core.py`, `routers/applicability.py`, `engines/facts.py`, `engines/applicability.py` | `org_legal_entity`, `org_subject_registry`, fact assertion revisions, evaluations | `read`; register subject / assert: `manage_facts`; evaluate: `request_evaluation` | caller's tenant, entity-resolved | **Implemented**. This work: `POST /org/subjects` field lengths (API-REQ-033) | `test_entity_registration_is_validated_and_audited` (subjects part), `test_api.py`, `test_rule_integrity.py` | — |
| 10 | Decisions & Attestations (Governance) + Applicability Trace | `/applicability`, `/applicability/trace`, `/applicability/[id]` | GET `/applicability/determinations`, `/applicability/evaluations`, `/applicability/evaluations/{id}`; POST `/applicability/determinations/{lineage}/attest`, `…/challenge` | `routers/applicability.py`, `engines/applicability.py` | determination revisions, attestations, challenges, evaluations | `read`\|`audit_read`; attest: `attest` + HUMAN; challenge: `challenge` | caller's tenant, entity-resolved | **Implemented** | `test_rule_integrity.py`, `test_engines.py`, `test_authorization.py` | AAL3 step-up for material adoption (IAM §08 LEGAL_ATTESTOR) does not exist |
| 11 | Agent Governance (Governance) | `/ai` | GET `/ai/assets`, `/ai/policy-bundle/{agent}`, `/applicability/evaluations?subject_id=`; POST `/ai/assets`, `/ai/assets/{id}/versions`, `/ai/grants`, `/ai/grants/{id}/revoke` (`/ai/decisions` and `/ai/acs/evaluate` are agent-runtime APIs) | `routers/ai.py`, `engines/governance.py` | AI asset subjects/versions, authority grants + grant events, decision receipts | `read`; register: `manage_ai`; grant/revoke: `grant_authority` + HUMAN | caller's tenant, entity-resolved | **Implemented** | `test_authority_grant_events.py`, `test_authorization.py` | Agent identities are not bound to grants (FR-AI-004), so agents are refused everywhere |
| 12 | Team & Access (Admin) | `/admin/users` | GET `/sessions`; POST `/sessions/{id}/revoke`; GET `/actors?actor_type=&role=&q=`; GET `/roles`, `/role-assignments?principal_id=&include_closed=`, `/principals/{id}/effective-access`, `/principals/{id}/sessions`; POST `/role-assignments`, `/role-assignments/{id}/revoke` | `routers/iam.py`, `app/iam.py`, `routers/core.py`, `app/auth.py` (sessions) | `org_actor`, `iam_role_assignment`, `iam_role_assignment_event`, `auth_session`, `platform_audit_event` | own access and sessions: any signed-in principal. Directory: `read`\|`content_*`\|`manage_organisations`\|`certify_connectors`\|`manage_role_assignments`\|`delegate_admin`. Role registry: `manage_role_assignments`\|`delegate_admin` (+ HUMAN to change; per-role rules in `app/iam.py`). A member's sessions: `manage_role_assignments` | caller's tenant. Session rows are resolved to their principal's tenant in code, because the `auth` capability sees every session | **Implemented**. This work: the screen was not wired to the role registry and said role assignment was "not yet available"; it now lists, assigns, revokes and explains effective access. Added directory filters, security-admin directory access, session listing/revocation, `/roles` entity scopes | `test_identity_directory_…`, `test_role_registry_endpoints_the_team_screen_uses`, `test_sessions_are_revoked_…`, `test_role_registry.py` (16), `test_read_authorization_matrix.py` | Adding people (WF-IAM-01 "invite/link identity") is **Blocked**: it needs enterprise sign-in or SCIM, and IAM-REQ-003 keys identity by issuer + subject, not email. Access reviews (WF-IAM-06, IAM-REQ-038/039) and external engagements (WF-IAM-05, IAM-REQ-027) are **Missing** (new IAM records, migration needed) |
| 13 | Integrations (Admin) + Identity Integrations | `/connectors`, `/admin/identity` | GET `/connectors`, `/outbox`; POST `/connectors/{id}/certification`, `…/contracts/{kind}/{version}/certification` (Zoiko certification managers only). `/admin/identity` calls nothing | `routers/integration.py`, `engines/connectors.py`, `routers/core.py` | connector definitions, contract versions, certification history (0012/0013), outbox | `read`\|`certify_connectors`; certification: `certify_connectors` + HUMAN + platform | connector registry is global; outbox is the caller's tenant only | **Partial**: registry and outbox are read-only and implemented. Configuring this organisation's connector installations (`manage_connectors`, IAM §08 INTEGRATION_ADMIN) has no backend | `test_connector_certification_history.py` (21), `test_connector_version_certification.py` (23), visibility test | Connector installations are **Blocked**: open decisions D2-e..i (`docs/connector-version-certification-design.md`), secret custody (TEN-REQ-023) and migration. Identity Integrations is **Blocked**: OIDC (IAM-REQ-005) and SCIM (§04) need an IdP decision and client registration |
| 14 | Audit Trail (Admin) + Privileged Access Log | `/ledger`, `/admin/access-log` | GET `/ledger`; POST `/ledger/verify`, `/ledger/checkpoint`; GET **`/audit-events`** (new, cursor-paginated; replaces the screen's `/audit?limit=200`) | `routers/evidence.py`, `engines/evidence.py`, `routers/core.py`, `app/pagination.py` (new) | ledger events, checkpoints, `platform_audit_event` | ledger: `read`\|`audit_read`; verify: `read`\|`verify`; checkpoint: `checkpoint`\|`package`; access log: `read` | caller's tenant; platform rows (no tenant) are never included | Ledger **Implemented**. Access log **Partial**: the audit trail now pages through everything with filters; privileged-access sessions do not exist | `test_audit_trail_pages_through_…`, `test_audit_trail_cursors_are_bound_…`, `test_audit_trail_filters_…`, `test_auth_session_security.py` | JIT elevation, break-glass and Zoiko support access (IAM-REQ-020/022/024, WF-IAM-03/04/08) are **Blocked**: they need AAL3 phishing-resistant step-up (IAM-REQ-006) and an approval policy |
| 15 | Org Settings (Admin) + Retention & Legal Holds, Content Releases | `/admin/cells`, `/admin/retention`, `/admin/releases` | GET `/org/tenant`; POST `/org/entities`; GET `/evidence` (retention view filters it in the browser); hold/release/erase via the evidence record; GET `/catalog/releases`, `/catalog/releases/{id}/verify` | `routers/core.py`, `routers/evidence.py`, `routers/catalog.py`, `engines/keys.py` | `org_tenant`, `org_legal_entity`, `org_cell`, `org_subject_registry`, legal holds, content releases | `read` (retention: `read`\|`audit_read`; releases: `read`\|`audit_read`\|`content_*`); register entity: `manage_tenant` | caller's tenant; cell assigned server-side (TEN-REQ-005, API-REQ-009) | Cells **Implemented**. This work: entity registration now validates (ISO alpha-2 country, column-size lengths, whitespace) and is audited as `entity.registered`. Releases **Implemented**. Retention **Partial** | `test_entity_registration_is_validated_and_audited`, `test_api.py`, `test_platform.py` | Retention policies are **Blocked**: RetentionPolicyVersion (PRI-REQ-018–020) needs schedules from Privacy/Legal and a migration. WF-TEN-01 residency badges, routing epoch and audit rail, and WF-TEN-02 assignment history, are not modelled (EntityCellAssignment, TEN-REQ-004) |

## 2. Super Admin Dashboard

| # | Sidebar label | Frontend route(s) | Backend endpoints (method) | Module / service | Database entities | Required permission | Tenant scope | Status | Test coverage | Remaining blockers |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Dashboard (Overview) | `/` → `SuperAdminDashboard`; `/organisations/[id]` | GET **`/platform/organisations?status=&q=`** (filters new); POST `/platform/organisations`; GET/PATCH `/platform/organisations/{id}`; POST `/platform/organisations/{id}/status`, `/platform/organisations/{id}/admins` | `routers/platform.py`, `app/iam.py` (`provision_roles`), `app/auth.py` | `org_tenant`, `org_legal_entity`, `org_subject_registry`, `org_cell`, `org_actor`, `auth_credential`, `auth_session` (revoked on suspension), `iam_role_assignment`(+event), `platform_audit_event`, outbox | `manage_organisations` + HUMAN + platform principal on every route. Organisation roles are refused (`PLATFORM_FUNCTION`, recorded) | global registry **metadata only**: the list is the one documented `system_scope` read, recorded as `platform.organisation_registry_read`. Detail and writes run under the target organisation's scope and are audited in its trail | **Implemented**. This work: allowlisted `status` and name `q` filters; the search text is kept out of the audit record (ZA-API-001 §11); the table gains a search box and status filter while headline counts stay over the whole registry | `test_super_admin_endpoints_are_platform_only`, `test_super_admin_registry_filters`, `test_platform.py`, `test_authorization.py` (registry mutations) | Organisation offboarding (TEN-REQ-037), isolation-tier change (WF-TEN-06) and migration (WF-TEN-05) are not built. Support access into an organisation is **Blocked** (IAM-REQ-024, see row 14) |
| 2 | Users & Roles (Administration & Trust) | `/admin/users` | GET `/actors` (Zoiko staff: tenant NULL), GET `/sessions`, POST `/sessions/{id}/revoke` (own sessions) | `routers/core.py`, `routers/iam.py` | `org_actor`, `iam_role_assignment`, `auth_session`, `platform_audit_event` | directory: `manage_organisations` (in the `/actors` guard); sessions: self | platform staff only (RLS `platform_actors`) | **Implemented**. Platform roles are never granted through an API (WF-IAM-01: no "make super admin" button), so there are no role controls | `test_identity_directory_…` (platform part), `test_sessions_…` (Super Admin refused other sessions), read matrix | — |
| 3 | Identity Integrations (Administration & Trust) | `/admin/identity` | none (static explanation) | — | — | any signed-in principal | — | **Blocked** | — | OIDC (IAM-REQ-005) / SCIM (§04) need an IdP decision; Zoiko staff privileged sign-in also needs AAL3 (IAM-REQ-006) |

Shared shell (both dashboards), from `frontend/lib/screen-access.json` `shell`:

| Read | Purpose | Permission | Change in this work |
|---|---|---|---|
| GET `/watermark` every 10 s | header record watermark | `read`\|`audit_read`\|`content_*` | **Was polled for everyone.** For the Super Admin (and a security administrator) every poll was refused and recorded as `authorization.denied`: about 8,640 audit rows per open tab per day. Now polled only when it will be served |
| GET `/dashboard/attention` | header alert counts | `read`\|`audit_read` | was called for every organisation user, including role administrators; now gated |
| GET `/org/tenant` | organisation sidebar entity scope | `read` | was called for every organisation user, including external auditors; now gated, and the footer says the scope is not readable for that role |

## 3. What changed

Backend (no migration; `backend/migrations/versions/0001–0013` untouched):

- `app/pagination.py` (new): ZA-API-001 §04 collection envelope and an opaque, HMAC-signed cursor. The cursor binds
  the query fingerprint (filters, sort, page size), the authorized scope, the stable sort position, the watermark
  pinned on the first page, and an expiry (1 h). Altered, foreign-scope, other-query or expired cursors return 400
  `invalid_cursor` / `cursor_expired`. The signing key is a purpose-bound derivative of the cell's context key; isolated
  SQLite runs use a per-process key.
- `routers/core.py`:
  - GET `/audit-events` (new): newest first, stable `(record_seq, id)` order, pinned watermark, allowlisted typed
    filters `action`, `category`, `actor_id`, `object_type`, `object_id`, and a half-open `recorded_from`/`recorded_to`
    window; `limit` 1–200.
  - GET `/actors`: accepts `manage_role_assignments`\|`delegate_admin` (WF-IAM-01 is the security administrator's
    directory) and adds typed filters `actor_type`, `role` (read from the role registry, any scope) and `q`. LIKE
    wildcards are escaped. With no filters the response is unchanged.
  - GET `/audit` and GET `/outbox`: `limit` must be ≥ 1. A negative LIMIT was a 500 on PostgreSQL. No upper bound,
    because an existing consumer uses `limit=10000`.
  - POST `/org/entities`: lengths = column sizes, ISO 3166-1 alpha-2 country (as the Super Admin path already required),
    whitespace stripped, country upper-cased, and an `entity.registered` audit record with the authorising assignments.
  - POST `/org/subjects`: lengths = column sizes.
- `routers/iam.py`:
  - GET `/sessions` (own) and GET `/principals/{id}/sessions` (security administrator).
  - POST `/sessions/{id}/revoke` (IAM §23: "self or authorized security admin; audit event"). Permission is checked
    before existence, so anyone else gets a recorded 403. Another organisation's session answers 404. A reason is
    required on the administrator path. The current session must sign out instead (409). The action is audited as
    `auth.session_revoked`. The revocation takes effect on the session's next request, and its in-flight writes are
    refused at commit by the existing `_revalidate_session` hook.
  - GET `/roles` gains `tenant_id`, `entities` (ID, name and country only, for scoping assignments) and
    `assigned_by_permission`. Additive.
- `routers/platform.py`: GET `/platform/organisations` gains `status` and `q` filters. The audit reason names the
  filters, never the search text.
- `routers/dashboard.py`: `/dashboard` gains `obligation_occurrences` (`unfulfilled`, `overdue`, `rule`). Additive.

Frontend:

- `lib/screen-access.json` (new, generated `perms`); `components/nav.ts` (permission-aware `navFor`/`orgNav`/`canOpen`/
  `jumpTargets`; a section whose own screen is not permitted opens its first permitted sub-screen); `components/Shell.tsx`
  (gated shell reads); `app/login/page.tsx` (redirect respects permissions).
- `app/admin/users/page.tsx` (Team & Access) rewritten:
  - your sessions, with an "End session" action;
  - a directory with search, principal-type and role filters;
  - a principal drawer for role administrators: effective access now, assignments with scope and validity, assign (scope
    picker from `/roles`), revoke with reason, and the member's active sessions with "End session".
- `app/admin/access-log/page.tsx`: `/audit-events` with a category filter and "Load more". Spec citations corrected
  (it cited IAM-REQ-024 for break-glass and IAM-REQ-030 for support access; these are IAM-REQ-022 and IAM-REQ-024).
- `components/PlatformAdmin.tsx`: registry search and status filter.
- `app/page.tsx`: the "Obligations due / late" tile is real. Reads are gated by permission, and a role administrator
  gets an explanatory home instead of failing figures.
- `app/admin/cells/page.tsx`: the form mirrors the server's validation.
- `app/admin/retention/page.tsx`: citation corrected (retention policies are PRI-REQ-018–020, not PRI-REQ-006).
- `lib/roles.ts`: exports `ROLE_CODES`.

Read-policy delta (`tests/read_authorization_matrix.json`, regenerated and reviewed). It is additions only:

- `core.audit_events`: readable by the same roles as `core.audit_log`.
- `iam.my_sessions`: every principal.
- TENANT_SECURITY_ADMIN gains `core.actors` and `iam.principal_sessions`.

This is a change to the provisional G-08 policy and needs the IAM Lead's review.

## 4. Test results (2026-11-04)

| Run | Database | Result |
|---|---|---|
| Full backend suite | isolated SQLite | 290 passed, 124 skipped (PostgreSQL-only), 0 failed |
| `tests/test_rls.py` (CI fail-fast step) | fresh PostgreSQL 17.11 `assure_test_rls`, throwaway cluster (port 55450, CI bootstrap SQL, non-superuser owner) | 5 passed |
| Full backend suite | fresh PostgreSQL 17.11 `assure_test`, same cluster | **414 passed, 0 skipped, 0 failed**; `.github/scripts/junit_gate.py` with every CI `--require` plus `test_sidebar_integration=37`: **PASS** |
| `test_sidebar_integration.py` + `test_read_authorization_matrix.py` after the last test edit | fresh PostgreSQL 17.11 `assure_test_sb` | 39 passed |
| `tsc --noEmit` | — | exit 0 |
| `next build` (isolated copy; the dev server's `.next` untouched) | — | compiled, types valid, 38/38 static pages |

The canonical `assure` database was never connected to, and no migration was run against it.

## 5. Remaining blockers and open decisions

| Item | Spec | What is needed | Owner (per spec) |
|---|---|---|---|
| Synthetic Overview panels (changes needing review, agent actions held, priorities, coverage) | ZA-STA-001 §22 | A defined record/measure for each; no invented score | Head of Assurance / CPO |
| Adding people, enterprise sign-in, SCIM | IAM-REQ-003/005/016, §03–§04 | IdP choice, client registration, SCIM endpoint design | IAM Lead, Security Architect |
| JIT elevation, break-glass, Zoiko support access | IAM-REQ-006/020/022/024, WF-IAM-03/04/08 | AAL3 step-up capability; approval policy; `iam.privileged_access_grant` (migration) | IAM Lead, Security Architect |
| Access reviews, external engagements | IAM-REQ-027/038/039/040, WF-IAM-05/06 | `iam.access_review_campaign/item`, `iam.external_engagement` (migration) | IAM Lead |
| Connector installations | IAM §08 INTEGRATION_ADMIN, TEN-REQ-023, D2-e..i | installation/scope model, secret custody, migration | Integrations Lead, Head of Assurance |
| Retention policies | PRI-REQ-018–020 | released RetentionPolicyVersion schedules; migration | Privacy Lead, Legal |
| Entity residency history | TEN-REQ-004, WF-TEN-01/02 | EntityCellAssignment (bitemporal) | Data Architect |
| Security administrator's view of security events | IAM §21, WF-IAM-01 drawer | today TSA has no audit read (deliberate, tested); decide whether it gets a security-event subset | IAM Lead (G-08) |
| Cursor pagination on the other collections; problem+json errors; Idempotency-Key | API-REQ-012/017/004 | contract changes with several consumers each; only `/audit-events` follows §04/§11 today | API / Platform Engineering Lead |
| Cursor signing key custody | ZA-SEC-001 (no ZA-KEY-001) | confirm the derived-key approach or name a dedicated key | Security Architect |
