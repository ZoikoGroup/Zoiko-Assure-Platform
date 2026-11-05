# Connector Certification History — Design

> **Date:** 3 November 2026. **Status:** design for review. Every ZA-* specification is "Proposed for lock", and no ADR in ZA-ADR-001 is approved.
> **Sources:** specification text extracted from the five controlled volumes. Requirement IDs were checked against that text, not taken from code comments.
> **Decision rule:** where the specifications are silent or conflict, this design **keeps the current behaviour** and names the decision owner. It does not decide.

## 0. Specification findings

| Source | What it says |
|---|---|
| ZA-GLS-001 §07, "Connector dimensions — do not collapse" | Four dimensions. **Certification: `UNASSESSED · CERTIFIED · SUSPENDED · REVOKED`** (middle dots: a code *set*, not a sequence). Release availability `PLANNED · PRIVATE_BETA · GENERAL_AVAILABILITY · DEPRECATED · RETIRED`; delivery model; runtime health `HEALTHY · DEGRADED · FAILED · DISABLED`. "Public product surfaces must derive connector claims from the certification registry" |
| GLS-REQ-017 | Connector availability, certification and runtime health MUST remain separate dimensions |
| ZA-PRD-001 FR-CON-001 / 002 | One state `AVAILABLE, BETA, CERTIFIED, DEGRADED, SUSPENDED or RETIRED`, "sourced from one certification registry". Not CERTIFIED until auth scopes, field mapping, IPE behaviour, retries and tenancy isolation pass |
| ZA-ARCH-001 v3.0 §10 | "Connector certification has explicit states: PLANNED BETA CERTIFIED SUSPENDED RETIRED"; availability sourced only from the registry |
| ZA-IPE-001, "Certification state transitions" | A trigger table: source API change → "moves to BETA/SUSPENDED until recertified"; production defect → "Suspend affected certification/version"; new scope → "Certification updated" |
| IPE-REQ-028; IPE-ADR-05 (proposed) | Certification MUST be versioned by connector build × source API/report × Extraction/Mapping contract compatibility |
| IPE-REQ-029 | A material source change MUST trigger recertification or suspension of affected contract versions |
| ZA-SEC-001 WF-SEC-03 | Review decision "CERTIFIED / BETA / BLOCKED/REQUIRES_REVIEW according to connector governance; approval/evidence refs" |
| ZA-DAT-001 §10 | `integration.connector_definition`: release availability, certification, supported objects, version compatibility. "Customer surfaces derive claims from certification registry" |
| ZA-EVT-001 catalogue | **`connector.certification_changed`** (integration governance; WEBHOOK_ELIGIBLE/admin). No event for runtime-health or availability changes |
| ZA-EVT-001 §20, STATE_CHANGED profile | Mandatory payload: resource_id; prior_state; new_state; exact prior/new revision refs; effective scope; reason codes |
| EVT-REQ-001/008/024/025; DAT-REQ-014 | Outbox in the same transaction; registered event types only; minimised payloads with no unnecessary free text |
| ZA-IAM-001 §08 | The minimum role-template set contains **no** Zoiko-platform connector-certification role (INTEGRATION_ADMIN configures *installations*) |
| IAM-INV-05; IAM-REQ-024 | No standing cross-tenant access; Zoiko support uses JIT grants |
| DAT-REQ-027; ZA-DAT-001 §12–13 | Least privilege; `assure_migrator` "no normal application use"; table owners are non-login |
| DAT-REQ-037; SEC-REQ-029; DAT-REQ-024 | No hard delete of governed history; immutability protections; no fabricated history |
| API-REQ-017 | Optimistic concurrency or preconditions where a lost update matters |

**Document conflicts.** None of these is resolved here; ZA-DOC-001 §01 sends each to its subject-matter authority.
- **C-1, certification vocabulary:** GLS-001 (four codes, separate dimension) vs PRD FR-CON-001 and ARCH §10 (one collapsed state including BETA/RETIRED) vs IPE-001 and SEC-001 (which put `BETA` / `BLOCKED` / `REQUIRES_REVIEW` in the certification decision). GLS-001 is the controlled terminology authority, and the code already uses its codes. The conflict is not in the DOC-001 §07 register.
- **C-2, unit of certification:** IPE-REQ-028 / IPE-ADR-05 certify a build × source × contract envelope; the registry holds one certification per connector definition.

## 1. Ownership and scope

**Spec requires.** One global certification registry (GLS, DAT §10, PRD FR-CON-001), read by every organisation.

**Code does.**
- `integration_connector_definition` has no `tenant_id`, no RLS and no row trigger.
- The only runtime writer is `engines/connectors.set_certification`, guarded by `Requirement(("certify_connectors",), human=True, platform=True)`:
  - "platform" means the actor has no tenant;
  - `certify_connectors` is held only by the code's role `CONNECTOR_CERTIFICATION_MANAGER`;
  - organisation roles, including PLATFORM_ADMIN (installations only), SERVICE and AGENT principals, are refused (403).
- The demo seed inserts the definitions.

**Design.**
- The registry stays **global**, so tenant RLS does not apply.
- **Write authority:** unchanged (Zoiko platform human with `certify_connectors`). **Gap:** IAM §08 has no such role template, so the role is a provisional interpretation (decision D3).
- **Database layer:** new, reusing the 0007 verified-context predicates.
  - The runtime role may change a registry row, or add history, only inside a verified **platform principal** context: a signed context with no tenant, an authenticated actor (`assure_platform_reader()`), and not system scope.
  - A tenant request context, or no context, can read but not write.
  - The database cannot tell *which* platform role the actor holds; the application check does that. These are two independent layers (SEC-INV-03).

## 2. Lifecycle and state transitions

**Code does.**
- `certification` takes the GLS codes (`codes.assert_code`).
- `CERTIFIED` requires every certification-matrix row to be `PASS` (FR-CON-002), else 409.
- Any other change between GLS codes is allowed, including `REVOKED → CERTIFIED`, with a mandatory reason.
- `runtime_health` (a separate GLS dimension) can change in the same call.

**Gap.**
- No controlled document defines permitted certification transitions, terminal states, renewal, reinstatement or expiry.
- GLS lists codes, not transitions. IPE gives triggers only. "Renew", "reinstate" and an expiry state appear nowhere for connector certification, and **there is no `EXPIRED` certification code** in GLS-001.

**Design.**
- Keep the current rules exactly: GLS codes only; `CERTIFIED` only with a passing matrix; a reason is required.
- **No transition table and no terminal-state trigger is added.** Treating `REVOKED` as final, or adding renew, reinstate or expiry, would decide decision D1.
- **Expiry is out of scope:** no state or code exists. When one is decided, expiry is a scheduled transition (Stage 8), kept separate from manual revocation and recorded with its own actor (`system:…`).

## 3. Append-only history

**Design.** A new global table, `integration_connector_certification_event`. One row per *effective* registry change:

| Column | Content |
|---|---|
| `event_id` | Identifier |
| `connector_id` | The connector |
| `build_version` | The connector build at the time: the effective scope today. Forward-compatible with IPE-REQ-028 |
| `from_certification`, `to_certification` | Separate columns, because GLS-REQ-017 keeps the dimensions apart |
| `from_runtime_health`, `to_runtime_health` | Separate columns |
| `actor_id` | Who made the change |
| `reason` | Required, as today |
| `prior_event_id` | Revision chain: the previous event for this connector, or null |
| `record_seq` | Cell record sequence, shared with the audit and outbox rows |
| `created_at` | Timestamp |

- A request that changes nothing writes nothing (§6).
- **No backfill.** Existing rows' past changes were never recorded (they have no record time), so the history of a pre-migration connector starts at its first post-migration change, with `prior_event_id = NULL` and `from_*` the state found then (DAT-REQ-024).

## 4. Audit and outbox

All in one transaction with one `record_seq`: the registry update, the history row, the audit record and (when certification changed) the outbox event.

| Record | Content |
|---|---|
| Audit | Unchanged contract: `connector.certification_changed`, platform record (`tenant_id` NULL), actor, prior/new `CERT\|HEALTH`, reason |
| Outbox | `connector.certification_changed` **only when `certification` changes**. Payload per the STATE_CHANGED profile: `resource_id`, `prior_state` / `new_state` (certification and runtime health as separate fields), `revision_ref` / `prior_revision_ref` (event IDs), `effective_scope` (`build_version`), `reason_codes: []`. **No free-text reason** (EVT-REQ-025) |
| Health-only change | History and audit, **no outbox event**: ZA-EVT-001 catalogues none, so the certification event is not reused for another fact (EVT-REQ-007/008) |

A failure anywhere rolls the whole transaction back.

**Gap.**
- No controlled list of certification reason codes exists, so `reason_codes` is empty (decision D6).
- Runtime-health events are not catalogued (decision D5).

## 5. Database security

| Object | Runtime role (`assure_app_rw`) | Row triggers (every role, owner included) | RLS (ENABLE + FORCE) |
|---|---|---|---|
| `integration_connector_definition` | SELECT, INSERT; UPDATE **(certification, runtime_health)** only; no DELETE, no TRUNCATE | `immutable_columns` (0003 function: other columns immutable, DELETE refused) | `registry_read` FOR SELECT USING (true); `registry_platform_insert` and `registry_platform_update` only in a verified platform-principal context; `migrator_all` |
| `integration_connector_certification_event` | SELECT, INSERT only | `append_only` | `registry_history_read` FOR SELECT only for a verified platform principal; `registry_history_insert` same predicate; `migrator_all` |

Further points:
- Owner `assure_table_owner` (non-login) for both tables; `assure_app_ro` gets SELECT.
- The current `release_availability`, `delivery_model`, matrix and other columns have no runtime writer, so they become immutable to the runtime role. A future writer needs a reviewed migration.
- **Why not tenant RLS:** the rows belong to no organisation. The tenant predicate would hide the registry from everyone, and `tenant_id` is absent. The global predicate is "who may write".
- **Owner and privileged paths:** the owner can still disable triggers with DDL (audit A10, open). The migrator bypasses RLS through `migrator_all`, as on every table. No SECURITY DEFINER function is added.

## 6. Concurrency and idempotency

**Design.**
- The service locks the registry row (`SELECT … FOR UPDATE`, reloading the row) before comparing. Two concurrent changes therefore serialise: each records exactly one event against the state it actually found, and `prior_event_id` forms a chain without forks.
- **Retries:** a request whose target equals the current state is a no-op. It returns the row and writes no history, audit or outbox. A retry after an unknown outcome is safe.
- **Stale updates:** with the lock, a later request always wins and is recorded against the true prior state, so nothing is lost silently. Refusing a request built on a stale read needs a precondition (API-REQ-017), and the mechanism (ETag / `If-Match`) is roadmap Stage 5b. **Not added** (decision D7).

## 7. Read authorization

**Code does.** Platform reads use `PLATFORM_READERS` (permissions `manage_organisations`, `certify_connectors`). Tenant connector reads use `READ`. The read classes are G-08's provisional interpretation.

**Design.** **No history endpoint.** Who may read certification history (platform certifiers, every tenant, auditors) is a G-08 read-policy decision. History is visible today through the platform audit log. At the database layer, history is readable only in a verified platform-principal context (§5).

## 8. Decisions

| ID | Decision | Authority | Status | Effect on implementation |
|---|---|---|---|---|
| D1 | Certification transition table; terminal states (is `REVOKED` final?); renew, reinstate, expiry | GLS / IPE owners, Connector governance; resolve C-1 | **Open** | Not implemented; current permissive rules kept |
| D2 | Unit of certification (IPE-REQ-028, IPE-ADR-05) | IPE Lead; ADR approval | **Open** | History records `build_version`; the registry is unchanged |
| D3 | Platform role for certification (IAM §08 has none) | IAM Lead | **Open (provisional role kept)** | No change |
| D4 | Global-table database write model | Data Architect | **Resolved by existing precedent**: the 0005/0007 platform-row policies and verified-context predicates; no new mechanism | Implemented; flagged for confirmation |
| D5 | An event for runtime-health or availability changes | EVT owner | **Open** | Health-only changes emit no outbox event |
| D6 | Certification reason-code list | GLS owner | **Open** | `reason_codes: []` |
| D7 | Precondition mechanism for stale writes | API Lead (Stage 5b) | **Open** | Not implemented; the row lock prevents silent loss |
| D8 | Event-table convention (Stage 3 prerequisite) | TIM / STA / DAT owners | **Open (as for 0008 and 0011)** | Followed |

**Implementation decision (Phase 3).** One slice needs none of the open decisions. It adds history, guards, atomicity, idempotency, a row lock and catalogue-aligned events, and changes no lifecycle rule, role or read policy. It is implemented as migration 0012. Everything listed as Open stays unimplemented.
