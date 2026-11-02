# Zoiko Assure — FA-26 Backend Handoff: Outstanding Screens

> **Status:** handoff for backend design. Nothing in this document is implemented by writing it, and **no API contract below is confirmed**.
> **Audience:** backend engineers and the product/governance owners who must settle the open decisions listed per item.
> **Baseline:** branch `vijay-changes`, HEAD `269c836` plus uncommitted frontend work (T1–T16, FA-07…FA-27). Backend source was read, not changed, on 29–30 Oct 2026.
> **Origin:** final frontend audit finding **FA-26 (P2, "Missing screens")** and the FA-26 frontend implementation report.
> **Authority:** the controlled ZA-* specifications (Volumes 1–5). Where a spec table had to be rebuilt from extracted PDF text, the citation is marked *(order-mapped)*.

**How to read this document**

- **Existing** = verified in the backend source, with file references (paths are relative to `backend/app/`). Route paths are shown as served, under the `/api` prefix.
- **Spec-named** = an endpoint, event or field the controlled specs name, but the backend does not implement. The specs name it; they do not fix its full shape.
- **Proposed** = a requirement for backend design written by the frontend team. Names and shapes are illustrative, **not** agreed contracts. Replace them with whatever the backend design settles on.
- **Open decision** = needs a product, legal, privacy or governance owner. The frontend will not pick a side in code.

---

## 1. Background

FA-26 listed screens that were missing or only partially present in the frontend. The frontend has since implemented the parts it could build from data the existing APIs already return, with no new requests:

| Delivered (frontend only) | Where | Data used |
|---|---|---|
| Structured shadow-impact view (WF-APP-03) | Rule draft › Shadow impact | Stored `shadow_report` |
| Structured semantic-diff view (WF-RUL-03) | Rule draft › Semantic diff | `GET /api/rules/diff` |
| Proof-gap list (WF-STA-05 / WF-STA-01) | Obligation dossier › Period assurance | Snapshot / preview `segments`, `excluded_segments` |

The six items below were **not** implemented, because each needs backend functionality that does not exist or is ungoverned today. A seventh FA-26 item, the decision-table editor (WF-RUL-02), is a product-design question rather than a backend gap and is out of scope here.

| # | Item | Blocking gap (summary) |
|---|---|---|
| 1 | Create issue / request remediation | No create endpoint; transitions unguarded; no issue events |
| 2 | Conflict resolution | Open/resolve ungoverned: no permission, audit, event or ledger record |
| 3 | Package disclosure preview | No preview/dry-run; no redaction; synchronous signing only |
| 4 | Timeline explorer | No cross-object history API; raw sequence numbers instead of watermark tokens |
| 5 | Correction impact | No impact index, report, notice or snapshot-supersession signal |
| 6 | Source dossier | Requirement versions carry no lineage, supersession or record period |

---

## 2. Cross-cutting requirements

These apply to every item below. They come from the controlled specs and from audit findings FA-03, FA-05, FA-10 and FA-11.

| Requirement | Source |
|---|---|
| Every mutation checks a named permission server-side (`ctx.require`). UI button gating is never the boundary. | IAM §06, SEC-REQ-022; audit FA-03 |
| Material approvals and governed decisions require a HUMAN principal and enforce separation of duties at action time. | IAM §10, INV-01, WRK-INV-05 |
| Mutations accept `Idempotency-Key`; revisioned targets require `If-Match`/ETag (stale revision → 412). | API §04, §08, §09; audit FA-05/FA-10 |
| Every material transition records actor, prior/new state, record time, correlation ID and reason. | NFR-010 |
| Domain changes emit outbox events from the EVT catalogue in the same transaction. | ZA-EVT-001, DAT-REQ-014 |
| Errors use RFC 9457 problem+json with a stable `code` and `request_id`; list endpoints use opaque cursors that carry the record watermark. | API §11–§12, DAT-REQ-021; audit FA-10/FA-25 |
| Tenant, entity and cell are resolved server-side; client-supplied scope is never authority. | TEN-INV-02, IAM-INV-04 |
| AAL3 step-up for actions the IAM §18 matrix lists (for example material approvals, legal hold/erasure, high-sensitivity exports). | IAM §18; audit FA-05 |
| Long-running work returns a durable Operation resource (`202` + `Location`), not a held HTTP request. | API §13, WRK-REQ-036 |

---

## 3. Create issue / request remediation

### Current frontend limitation
- *Exceptions & Issues › Issues* lists issues and moves them through their states. FA-07 and FA-21 added confirmations and cancellation-reason validation.
- Users cannot create an issue or request remediation from a dossier, control or evidence record. PRD WF-03 ("request remediation") and WF-05 ("create issue") require both.
- The frontend does not fabricate or infer issues.

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| `Issue` model | `issue_id, tenant_id, title, issue_type, severity, priority, state, source_ref_type, source_ref_id, owner_actor_id, due_at, dedupe_key, created_at` | `models.py` |
| `GET /api/issues` | Tenant-scoped list, newest first. No filters, no pagination. | `routers/assurance.py` |
| `POST /api/issues/{issue_id}/transition` | Flow: `OPEN→TRIAGED/CANCELLED`, `TRIAGED→IN_REMEDIATION/CANCELLED`, `IN_REMEDIATION→PENDING_VERIFICATION`, `PENDING_VERIFICATION→CLOSED/IN_REMEDIATION`. `CANCELLED` requires a non-empty reason. Writes an `audit()` entry. **No permission check**; `manage_issues` exists in the role map but is never checked. | `routers/assurance.py` |
| System-created issues only | `_issue_hooks` on snapshot freeze (`OBLIGATION_FAILING`, `PROOF_GAP`, `ASSURANCE_REVIEW`, deduplicated by type + obligation; emits `assurance.proof_gap_detected` for NOT_PROVEN), `DETERMINATION_REVIEW`, `INTEGRITY_INCIDENT` | `engines/assurance.py`, `engines/applicability.py`, `engines/evidence.py` |
| Events | `issue.created` / `issue.closed` are **not** emitted. | — |
| Owner / due date | Columns exist but no endpoint sets them. | — |

### Missing backend functionality
- An endpoint to create an issue (or remediation request) against an exact target.
- Owner assignment, due date and escalation (FR-WRK-002: "Every task has accountable owner, due date, status and escalation path").
- A permission check on transitions and creation.
- The `issue.created` / `issue.closed` events (ZA-EVT-001 §17).
- Links from an issue to remediation tasks and retest results (WRK §17 stages: Trigger → Deduplicate → Assign → Investigate → Remediate → Retest → Verify → Close).

### Spec basis
- ZA-STA-001 §17: issue triggers by control/obligation state. "Create/link issue unless an existing open issue already covers exact control/scope/root cause"; a NOT_PROVEN gap is "not automatically same severity as failure".
- ZA-WRK-001 §17: "Workflow cannot mark it green"; "closure does not itself set assurance state".
- STA-REQ-023/024: issue closure must not directly change assurance state.

### Suggested API / data requirements (Proposed)
- **Create:** e.g. `POST /issues`, or the `/v1` equivalent once API naming is settled.
  - Body: target reference `{type, id, revision}` (control implementation, obligation, evidence, snapshot), `issue_type` from a controlled list, title, description and reason. Severity and priority are derived by policy, or requested and validated.
  - Returns the created issue.
  - Deduplication: if an open issue already covers the same target and root cause, return `409` with the existing `issue_id`, per STA §17.
- **Assign:** e.g. `POST /issues/{id}/assignment` with `{owner, due_at}`. Due dates follow policy, not arbitrary input (WRK §06 "Versioned deadline policy").
- **Request remediation:** either an issue of a remediation type, or a WRK human task linked to the issue. This depends on the task inbox (audit FA-11).
- **Events:** `issue.created` and `issue.closed` per EVT §17, with no assurance mutation implied.

### Security, authorization and audit
- Server-side permission on create, assign and transition. `manage_issues` is the obvious candidate but needs confirmation.
- Read-only roles (Internal Audit, External Auditor) must not be able to create or transition issues.
- Validate that the target belongs to the caller's tenant (`ctx.scoped`).
- Audit every create, assign and transition (prior/new state, reason); require `Idempotency-Key` on create.
- Never change assurance or control state from issue actions.

### Dependencies and open decisions
- **Open:** the `issue_type` vocabulary. ZA-DAT-001 lists issue "type, severity, priority, workflow state" with no enum.
- **Open:** the severity and priority derivation policy, and whether users may override it.
- **Open:** which targets allow manual issues, and whether "request remediation" is its own object or an issue type.
- **Open:** owner-resolution rules (control owner vs obligation owner) and escalation policy.
- **Dependency:** WRK human-task inbox (audit FA-11) for remediation tasks.

---

## 4. Conflict resolution

### Current frontend limitation
- T13 shows evidence conflicts **read-only**: status, members, description and disposition, on both evidence pages.
- No "open conflict" or "resolve conflict" actions are offered. The backend endpoints exist but are ungoverned, so a UI would expose an unaudited action that changes assurance outcomes.

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| `EvidenceConflict` model | `conflict_id, tenant_id, member_evidence_ids, description, status, disposition, created_at`. No `resolved_by`/`resolved_at`. | `models.py` |
| `POST /api/evidence-conflicts` | Validates that members belong to the tenant, then creates the conflict. **No permission check, no audit entry, no event, no ledger event.** | `routers/evidence.py` |
| `POST /api/evidence-conflicts/{id}/resolve` | Sets `RESOLVED` and `disposition = reason`. **No permission check, no audit, no event, no ledger event**; can be repeated, which overwrites the disposition; empty-check only on the reason. | `routers/evidence.py` |
| `GET /api/evidence-conflicts` | Full tenant list. No filter by evidence ID, no pagination. | `routers/evidence.py` |
| Assurance effect | Members of an OPEN conflict are evaluated as `CONFLICTING` (test status `CONFLICT`, reason `SOURCE_CONFLICT`), normally leading to `REQUIRES_REVIEW`. | `engines/assurance.py` |
| Ledger event types in use | `MANIFEST_CREATED, VERIFIED, SUPERSEDED, INVALIDATED, HOLD_PLACED, HOLD_RELEASED, ERASE_REQUESTED, PAYLOAD_ERASED, EXPORT_INCLUDED`. None is conflict-specific. | `engines/evidence.py` |

### Spec basis
- **ZA-EVD-001 §13** *(order-mapped)*:
  - **Conflict detected:** "Create EvidenceConflict object referencing evidence IDs; append optional conflict-linked events, not intrinsic 'CONFLICTING' mutation to both artifacts."
  - **Conflict resolved:** "Append resolution/disposition event; may create new evidence or select authoritative source according to policy." Effect: "New evaluation/snapshot; old conflict history preserved."
- **EVD data model:** EvidenceConflict is a "Governed workflow; members stay immutable."
- **EVD §29:** display the conflicting evidence set and source authority rather than marking one artifact red.

### Missing backend functionality
- Permission enforcement on open and resolve.
- Resolver identity, resolution time and authority/policy reference.
- A resolution or disposition event appended to each member's ledger chain, plus an outbox event.
- A guarded transition (resolve only from OPEN), so the disposition can't be overwritten.
- A structured disposition: which source is authoritative, and whether members are superseded or invalidated, or new evidence is captured.
- A per-evidence query (T13 currently fetches the whole list and matches in the browser).

### Suggested API / data requirements (Proposed)
- **Model:** add `opened_by`, `resolved_by`, `resolved_at`, `disposition_code` and an optional `authoritative_evidence_id`. Keep `disposition` as the human-readable reason (required; reject blank and whitespace-only).
- **Resolve:** accepts `{disposition_code, reason, authoritative_evidence_id?}` with `If-Match` and `Idempotency-Key`. Returns `409` unless the conflict is OPEN.
- **Follow-up actions:** supersession or invalidation of members happen through the existing supersede/invalidate actions, each with its own ledger event, never by mutating member bytes.
- **Ledger:** a conflict-linked ledger event on open and a resolution/disposition event on resolve, for each member. The EVD event-type list allows "controlled successor" types; the names need a decision.
- **Events:** outbox events for conflict opened and resolved. These aren't in the EVT catalogue yet and need adding.
- **Query:** `GET /evidence-conflicts?evidence_id=…`, or conflict membership embedded in the evidence detail response.

### Security, authorization and audit
- Separate permissions for opening (an evidence or compliance role) and resolving (a reviewer authority). Resolution should require a HUMAN principal.
- Consider SoD so the opener is not the sole resolver where policy marks the conflict material (IAM §10 pattern).
- Audit open and resolve with prior/new state and reason.
- Tenant scoping for all members, which already exists for open.

### Dependencies and open decisions
- **Open:** the conflict status vocabulary. `OPEN`/`RESOLVED` are not in the GLS dictionary.
- **Open:** disposition codes, and whether resolution needs approval or dual control.
- **Open:** whether conflicts can be opened by the system (connector reconciliation) as well as by people.
- **Open:** ledger and outbox event names.
- **Dependency:** the frontend resolve flow (reason dialog, confirmation) is ready to build once the governed endpoint exists.

---

## 5. Package disclosure preview

### Current frontend limitation
- The Audit Package Builder collects entity, period, obligations and "Include decrypted evidence payloads" (default on). It then builds and signs in one step.
- There is no way to see, before signing, which files, payloads, redactions or omissions the package will contain (WF-EVD-05 "Disclosure preview").
- T15 also showed that each obligation contributes its **most recently frozen** snapshot, whatever the package period. The builder now labels this, but it can't preview it.

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| `POST /api/packages` | Requires `package`. Synchronous build and sign. For each obligation takes the most recently frozen snapshot regardless of period. Includes decrypted payloads when `include_payloads`. Appends `EXPORT_INCLUDED` to each evidence item's ledger chain. Emits `evidence.package_signed`. | `routers/evidence.py`, `engines/evidence.py` |
| Package contents | `evidence/`, `manifests/`, `ledger-proofs/`, `merkle-proofs/`, `timestamp/`, `assurance/snapshots.json`. The verification report notes `PAYLOAD_NOT_INCLUDED` / `PAYLOAD_ERASED`; its `redactions` list is always empty. | `engines/evidence.py` |
| `GET /api/packages` | Tenant list. | `routers/evidence.py` |
| `GET /api/packages/{id}/download` | **Tenant scope only: no permission check, no audit entry** (audit FA-02). | `routers/evidence.py` |
| `POST /api/packages/{id}/verify`, `POST /api/packages/verify-upload` | Verification; upload verification is unauthenticated by design (offline verifier parity). | `routers/evidence.py` |

### Missing backend functionality
- A preview or dry-run that resolves scope and members **without** signing, writing ledger events or exposing payload bytes.
- Per-member disclosure details: files, redactions, metadata-only entries, privileged omissions, recipient access (WF-EVD-05).
- A verification gate (HMAC, object, ledger, checkpoint, TSA, IPE statuses) and the blocking rules, shown before signing (WF-EVD-05).
- Redaction artifacts. Per EVD §19: "Original EvidenceArtifact remains unchanged. Create a derived/redaction artifact"; reports label redactions clearly.
- Asynchronous build through Operations, and a separate finalize step (API §13, WRK §19).
- Short-lived, authorized downloads ("never public permanent URL", WRK §19), with download audit.
- Recipient/engagement scoping (FR-AUD-005).

### Spec basis
- WF-EVD-05 regions: Scope, Evidence selection, Verification gate, Disclosure preview, Package manifest, Sign/finalize.
- WRK §19 stages: Scope freeze → Dependency resolution → Privacy/redaction → Integrity proof → Human review → Generate → Deliver → Receipt/expiry.
- Spec-named in ZA-API-001: `POST /v1/audit-packages` returning `202` with an Operation, and `POST /v1/audit-packages/{id}/finalize`.
- IAM §18: "Export high-sensitivity audit package" requires AAL3, export permission and watermark/log.

### Suggested API / data requirements (Proposed)
- **Preview:** e.g. `POST /packages/preview`, or a draft state of the spec's `/v1/audit-packages` before `finalize`. Same inputs as build. The response would contain:
  - resolved scope (entity, period, record cutoff), and the snapshot chosen for each obligation with its own period, perspective and cutoff;
  - each evidence member with `evidence_id`, title, data classification and inclusion mode (`PAYLOAD`, `REDACTED`, `METADATA_ONLY`, `OMITTED` with reason);
  - gate results with blocking flags;
  - totals.
- **Preview constraints:**
  - No signing, no ledger writes, no payload bytes in the response. It should be idempotent.
  - The preview must be bound to the exact inputs, for example by returning a scope digest that `finalize` must echo, so the signed package matches what was previewed.

### Security, authorization and audit
- Preview requires the same permission as building, plus data-classification checks on each member. It must never return payload content.
- Decide whether preview requests are audited. Build, finalize and download must be.
- Download needs a permission check and an audit entry (FA-02).

### Dependencies and open decisions
- **Open:** whether decrypted payloads should be included by default. Currently on in both backend and frontend.
- **Open:** redaction policy, who may redact, and whether redaction needs approval.
- **Open:** the snapshot-selection rule for packages (period-matched vs most recently frozen). Shared with audit FA-08.
- **Open:** the recipient/engagement model (FR-AUD-005, WF-IAM-05).
- **Dependency:** Operations resource (FA-10) for asynchronous builds.

---

## 6. Timeline explorer

### Current frontend limitation
- Partial coverage exists in three places:
  - the subject page (fact profile at a chosen valid time and record sequence, plus fact revision history);
  - *Historical Reconstruction* (`/history`), which compares two record cutoffs for one subject and obligation, for applicability only, using raw sequence numbers;
  - snapshot Reproduce in the dossier.
- There is no WF-TIM-01 timeline: valid-time intervals on one track, record-time revisions and corrections on another, across object classes.

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| `GET /api/subjects/{id}/facts?valid_at&known_seq` | Fact profile for a perspective (`CURRENT` or `CUSTOM_BITEMPORAL`), with cell watermark | `routers/applicability.py` |
| `GET /api/subjects/{id}/facts/history` | Every `FactAssertion` revision: valid range, `record_from_seq`/`record_to_seq`, `recorded_at`, `change_kind` (`CREATE`, `CORRECTION`, `RETROACTIVE_CORRECTION`, `PROSPECTIVE_CHANGE`, `LATE_ARRIVAL`, `WITHDRAWAL`, `SPLIT`), `correction_of_revision_id` | `routers/applicability.py`, `engines/facts.py` |
| `GET /api/history/compare` | One valid time, two record cutoffs. Non-persisted re-evaluation. Differences `LEGAL_VERSION_CHANGED`, `KNOWLEDGE_ADDED`, `KNOWLEDGE_CORRECTED`, `NO_SEMANTIC_CHANGE`. Applicability only. | `routers/applicability.py` |
| `GET /api/snapshots/{id}/reproduce` | Rebuild at the pinned cutoff and compare digests | `routers/assurance.py` |
| `GET /api/watermark` | Current cell record sequence | `routers/core.py` |

### Missing backend functionality
- A history API that spans object classes: facts, determinations, obligation revisions, evidence lifecycle, snapshots.
- Opaque RecordWatermark tokens `{cell_id, recorded_at, record_seq}`, echoed in responses (TIM-ADR-01; API §10). The UI currently shows and accepts raw integers.
- Explicit perspective handling (`CURRENT`, `AS_KNOWN_THEN`, `RECONSTRUCTED_NOW`, `CUSTOM_BITEMPORAL`), failing closed on ambiguous "as of" (TIM §16).
- Assurance-level comparison. WF-TIM-02 difference classes also include `SOURCE_CONFLICT_RESOLVED` and `EVIDENCE_ADDED_OR_ERASED`.
- Cursor pagination that preserves the watermark and valid-time context (DAT-REQ-021).

### Spec-named (not implemented; shape not specified beyond the spec)
- ZA-TIM-001:
  - `GET /subjects/{type}/{id}/history`
  - `GET /subjects/{type}/{id}/state?valid_at=…&known_at=…` ("both parameters required outside CURRENT shortcut")
  - `POST /historical-reconstructions`, `GET /historical-reconstructions/{id}`
  - `POST /historical-comparisons`
- Events: `temporal.revision_recorded`, `temporal.correction_recorded`.

### Suggested API / data requirements (Proposed)
- **Timeline items:** `{object_class, lineage_id, revision_id, valid_range (with profile: LEGAL_DATE_RANGE or OPERATIONAL_INSTANT_RANGE), recorded_at, record_seq, change_kind, actor, reason, supersedes_revision_id, correction_of_revision_id}`.
- **Filters:** by valid range and by record cutoff (watermark token).
- **Response:** echoes the resolved watermark and perspective.

### Security, authorization and audit
- Mask or redact sensitive fact values by classification and role. WF-TIM-01: "Sensitive values masked; lineage/revision metadata still shown where authorized."
- Tenant scoping on every joined object.
- Cross-cell views carry one watermark per cell. TIM: `record_seq` is cell-local and must not imply a global order.

### Dependencies and open decisions
- **Open:** which object classes v1 covers.
- **Open:** legal-date vs UTC-instant display (governance item G-2; DOC-CON-003). The frontend currently converts dates to UTC midnight.
- **Dependency:** watermark tokens and cursor pagination (FA-10).

---

## 7. Correction impact

### Current frontend limitation
- There is no WF-TIM-03 correction-impact view.
- Users see only scattered signals:
  - *Content Releases* shows "N of your evaluations" per release;
  - adopted determinations are flagged for review, with a `DETERMINATION_REVIEW` issue;
  - fact corrections list the re-evaluations they triggered.
- No screen answers "which conclusions, snapshots and packages does this correction affect, and what must happen next".

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| Bitemporal fact corrections | Corrections close prior revisions and add new ones (`correction_of_revision_id`, `change_kind`). They emit `fact.changed` with closed/new revision IDs and the valid range, and re-evaluate affected obligations (`reevaluate_for_fact`, returned as `impact_reevaluations`). | `engines/facts.py`, `engines/applicability.py`, `routers/applicability.py` |
| Rule correction release | Publishing a draft with `change_class=CORRECTION` sets `correction_of_release_id`, emits `content_release.corrected`, re-evaluates every subject previously evaluated for that obligation definition (trigger `RULE_CHANGED`), and returns a count. | `routers/rules.py` |
| Release impact count | `GET /api/catalog/releases` adds `tenant_impacted_evaluations` (count only) for tenant users | `routers/catalog.py` |
| Adopted-determination review | When a newer evaluation disagrees, the determination gets a revision with `needs_review` and a `DETERMINATION_REVIEW` issue is created | `engines/applicability.py` |
| Snapshot supersession signal | **None.** No `stale_indicator`, no `SUPERSEDED_FOR_CURRENT_VIEW` lifecycle, no `snapshot.superseded_for_current_view` event. | — |

### Missing backend functionality
- An impact record per correction: affected valid range; counts by object class (evaluations, determinations, obligations, snapshots, audit packages, open workflows overlapping the interval); and authorized detail lists.
- Materiality classification and an action plan: re-evaluate, require customer review, issue a correction notice, regenerate the current package, or no action with a reason (WF-TIM-03).
- Signed correction notices to impacted tenants (FR-WRK-003; NFR-006: within 2 business days).
- `stale_indicator` on snapshots when newer knowledge exists, without mutating the snapshot (TIM snapshot fields).
- The events `temporal.retroactive_change_detected` and `snapshot.superseded_for_current_view`.
- A correction-impact workflow. STA §17: "Correction changes historical state → Open correction-impact workflow and reassess affected current/adopted reports/packages."

### Spec-named (not implemented)
- ZA-API-001: `GET /v1/content-releases/{id}/impact`.
- ZA-TIM-001 events listed above.
- WRK §16 tenant-impact step and the correction workflow ("never mutates the original content release or historical customer determination").

### Suggested API / data requirements (Proposed)
- **Resource:** a `CorrectionImpact` per correction (fact revision or content release) containing:
  - `{correction_ref, change_kind, affected_valid_range, detected_at (record time), counts_by_class, materiality, recommended_actions, status}`;
  - detail lists, available only in authorized tenant scope: snapshot IDs with their period, perspective and cutoff; package IDs; determination lineages; open workflow references.
- **Endpoints:** the spec-named release impact endpoint, plus a correction-impact read for fact corrections (name to be designed).
- **Snapshots:** expose `stale_indicator` (or an equivalent derived flag) on snapshot reads. It must never mutate the snapshot.

### Security, authorization and audit
- Content operations see counts only, never tenant identities or fact values (APP shadow rule; WF-TIM-03 "counts first, entity detail only to authorized customer/support context").
- Tenant detail is tenant-scoped.
- Correction notices are signed. Their delivery and acknowledgement are recorded.

### Dependencies and open decisions
- **Open:** materiality rules, and who signs and approves correction notices.
- **Open:** which snapshots count as "superseded for current view", and how packages already delivered are handled.
- **Dependency:** WRK correction workflow and task inbox (FA-11); history API (§6 above).

---

## 8. Source dossier

### Current frontend limitation
- What exists:
  - the *Regulatory Inbox* lists sources (title, locator, authority, jurisdiction, rights, lifecycle, fingerprint, fetched time);
  - the requirement review drawer shows one requirement version with its provisions and source;
  - *Sources & Provisions* lists instruments (with coverage mode) and obligation definitions.
- There is no WF-DOM-01 source/provision dossier: no stable vs revision identity, no valid and record periods, no supersession chain, and no single view of a source's requirement versions.

### Existing backend capabilities
| Capability | Detail | Location |
|---|---|---|
| `RegulatorySource` | `source_id, authority, jurisdiction, source_type, title, locator, fingerprint, rights_class, rights_record, raw_text, publication_date, effective_date, lifecycle, fetched_at, record_seq` | `models.py` |
| `RequirementVersion` | `requirement_version_id, requirement_key, instrument_id, source_id, title, locator, summary, valid_from, valid_to, lifecycle, origin, content_release_id, reviewed_by, record_seq`. **No lineage ID, no supersedes link, no record_to/recorded_at.** | `models.py` |
| `Provision` | `provision_id, requirement_version_id, locator, duty_bearer, modality, action, regulated_object, timing, normalized_meaning, evidence_expectation, semantic_origin, lifecycle` | `models.py` |
| `ContentRelease` | Includes `correction_of_release_id` | `models.py` |
| Reads | `GET /api/catalog/sources`, `GET /api/catalog/instruments` (with requirements and obligation definitions), `GET /api/catalog/requirements/{rv_id}` (with provisions and source; raw text withheld for `REFERENCE_ONLY`) | `routers/catalog.py` |
| Writes | `POST /api/catalog/sources`, `POST /api/catalog/sources/{source_id}/extract`, `PUT /api/catalog/provisions/{provision_id}`, `POST /api/catalog/requirements/{rv_id}/verify` | `routers/catalog.py` |

### Missing backend functionality
- Stable lineage identity separate from revision identity for requirement versions (and for sources, if they are revisioned).
- Record periods (`record_from_seq`/`record_to_seq`, `recorded_at`) alongside valid periods.
- Supersession and correction links between revisions. Without them a chain could only be inferred from matching keys, which the frontend will not do.
- A read that returns a source with its requirement versions, provisions, content releases and revision chain.

### Spec basis
- **WF-DOM-01:** "stable and revision IDs, locator, valid/record periods, semantic origin, content release, supersession chain."
- **ZA-DAT-001 §03** bitemporal columns: `lineage_id, revision_id, recorded_at, record_from_seq, record_to_seq, record_batch_id, change_kind, change_reason, supersedes_revision_id, correction_of_revision_id, valid_date_range/valid_instant_range`.
- **Events:** `requirement.version_published`, `requirement.version_superseded` (EVT §15).
- **Spec-named in ZA-API-001:** `GET /v1/regulatory-instruments`, `GET /v1/requirements/{id}`, `GET /v1/content-releases`.

### Suggested API / data requirements (Proposed)
- Add the DAT §03 bitemporal columns to `RequirementVersion` (and decide for `RegulatorySource`), with a backfill for existing rows; for example, lineage = current ID for first revisions.
- A source dossier read, e.g. `GET /catalog/sources/{source_id}`, returning:
  - the source;
  - its requirement-version lineages, each with its revisions (valid period, record period, lifecycle, origin, content release, supersedes/corrects links);
  - provisions per revision;
  - related content releases.

### Security, authorization and audit
- `rights_class` continues to govern raw-text display (`REFERENCE_ONLY` withholds text; existing behaviour).
- Catalogue content is global. Customers see released content; draft and AI-proposed content follows the authoring permissions (APP authorization matrix: "View rule definition — Customer Compliance: Yes (released)").
- Content writes stay audited.

### Dependencies and open decisions
- **Open:** whether sources are immutable snapshots (DOM: "SourceSnapshot Immutable") or revisioned records.
- **Open:** how supersession across instruments is represented, and the backfill and migration approach.
- **Dependency:** shares the history and watermark design with §6.

---

## 9. Suggested sequencing

1. **Governance fixes first:** permission, audit and ledger enforcement for conflicts and issue transitions (§3, §4). These close existing ungoverned writes (audit FA-03) and are prerequisites for the frontend actions.
2. **Issue creation and remediation** (§3), together with the task inbox (FA-11).
3. **Package preview and finalize** (§5), together with Operations and download controls (FA-02, FA-10).
4. **Bitemporal lineage for catalogue content** (§8), then the **history/timeline API** (§6).
5. **Correction impact** (§7), which builds on §6 and §8.

For each item, the frontend will build the screen once the backend endpoint exists and its contract is agreed. Until then, the frontend continues to show only data the API returns, and documents gaps instead of inferring values.
