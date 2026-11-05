# Obligation definition versioning: architecture and implementation readiness

Date: 2026-11-04. Status: **proposal for approval**. Nothing here is implemented: no migration created, no schema or
code changed, and the `assure` database was not touched.

**Verdict: BLOCKED BY DECISIONS.** The proposed design is specification-consistent and ready to implement once the
decision register (§1) is answered. Five decisions block it outright: B1-a, B1-b, B1-c, B2 and B5. B3, B4 and B6 change
what the first release may allow. Nothing in the design contradicts a controlled requirement.

Inputs:
- [obligation-definition-authoring-implementation-report.md](obligation-definition-authoring-implementation-report.md)
- [regulatory-requirement-traceability-implementation-report.md](regulatory-requirement-traceability-implementation-report.md)
- [regulatory-release-signature-verification-report.md](regulatory-release-signature-verification-report.md)
- ZA-DOM-001, ZA-GLS-001, ZA-RUL-001, ZA-DAT-001, ZA-ARCH-001 §5.2, ZA-STA-001
- the models and migrations 0001–0013

All ZA documents are v1.0 "Proposed for lock"; quotations are from the extracted specification text.

## 0. What the specifications fix for any design

| Rule | Text | Source |
|---|---|---|
| Definitions are versioned content | "ObligationDefinition — Versioned reusable obligation semantics derived from one or more provision versions. Regulatory Content owns." | DOM §6.1 |
| Versioned by release | ObligationTemplate: "…versioned by content release." | ARCH §5.1 |
| Stable vs revision identity | "Stable identity is separate from revision identity… each revision is immutable once closed in record time." | DOM §1.1 |
| Bitemporal revision shape | `stable_id`, `revision_id`, `valid_period`, `record_period`, `content_release_id`, `recorded_by_actor_id`, `correction_of_revision_id`; "Corrections create new record-time versions; they do not erase prior knowledge states." | ARCH §5.2 |
| History | "Bitemporal corrections MUST preserve closed historical record slices and create new revisions rather than overwriting history." | DAT-REQ-009 |
| Immutable once published | "PUBLISHED catalogue artifacts MUST be immutable in activated cell catalogues." | DAT-REQ-012 |
| Exact references | "Material references required for reconstruction MUST point to immutable revision/version IDs, not only mutable logical IDs." | DAT-REQ-026 |
| Backfill honesty | "Backfills MUST be idempotent/resumable and MUST NOT fabricate historical Assure record time." | DAT-REQ-024 |
| Migration pattern | "Migrations MUST follow expand/contract or approved zero-downtime patterns and preserve historical…" | DAT-REQ-023 |
| Links | "ProvisionVersion → ObligationDefinition 1:N…; each link is explicit." "ObligationDefinition → RuleVersion 1:N over time; exactly one released rule version is effective for a valid instant." "Adopted Determination → ObligationInstance 1:0..1 per canonical scoped obligation revision." | DOM §13 |
| Snapshots | "No AssuranceSnapshot references 'latest' objects; it stores exact revision IDs." | DOM-INV-10 |
| Codes | "Controlled semantic codes MUST follow ZA-GLS-001 and MUST NOT redefine existing meanings in place." | DAT-REQ-025 |
| Content lifecycle | DISCOVERED → … → REVIEW_REQUIRED → VERIFIED → PUBLISHED → SUPERSEDED / WITHDRAWN (with allowed moves) | GLS §04 |

No specification names an obligation-definition revision entity, its lifecycle or its approval step. Those are the
decisions below.

## 1. Decision register

Evidence labels: **Spec requires** / **Code does** / **Gap** / **Decision required**.

### B1: definition revisions and updating a released definition (blocking)

| | |
|---|---|
| Spec requires | Versioned definitions (DOM §6.1); revisions immutable once closed (DOM §1.1); new revisions instead of overwrites (DAT-REQ-009); references to immutable revision IDs (DAT-REQ-026); exactly one effective released rule per valid instant (DOM §13) |
| Code does | One mutable row per definition, with links in a JSON list. Links change only until the first PUBLISHED RuleVersion, then 409 `OBLIGATION_DEFINITION_RELEASED`. Obligation instances, evaluations and determinations reference only `obligation_definition_id`. Signed releases bind the exact provision IDs (`requirement_versions[]`) since the traceability work |
| Gap | No revision identity; no way to adopt a newer requirement version into a released definition; obligation instances violate DAT-REQ-026 for the definition reference |

**Decision required** (owners: Regulatory Content owner, who owns definitions per DOM §6.1, with the Data Architect and
the RUL owner):

- **B1-a:** Is a released definition changed by a new *revision* of the same stable definition? This design's
  assumption is yes. Or by a new definition (new key)? A new key breaks the obligation lineage of every adopting
  organisation.
- **B1-b:** Is a revision's valid time a legal-date range (as RequirementVersion and RuleVersion use) or record-only?
  This design assumes a legal-date `valid_from`/`valid_to` plus record sequence. DOC-CON-003 (DOM UTC valid_period vs
  TIM profiles) applies, and the TIM owner must confirm the profile.
- **B1-c:** Must every rule release pin exactly one definition revision? The design assumes yes. And does a revision
  become binding only through a release (ARCH "versioned by content release")?
- **B1-d:** Must existing obligation instances be re-pointed or re-adopted when a newer revision is released, or do
  they keep the revision their adoption used? The design assumes they keep it, and a newer release triggers the existing
  re-evaluation and review path; it never silently re-points. The owner is the Obligation/Assurance domain (DOM §6.1
  "Obligation domain owns").

### B2: authorization of `content_author` (blocking)

| | |
|---|---|
| Spec requires | Definitions are Zoiko-authored (GLS) and owned by Regulatory Content (DOM §6.1). RUL §10 permits the Regulatory Analyst to "Create/edit RuleDraft, source mappings, decision tables…" and the Encoding Engineer to "Edit typed AST/decision-table structures". Neither mentions obligation definitions |
| Code does | `content_author` (CONTENT_ANALYST, ENCODING_ENGINEER) held by a platform principal |
| Gap | The role is an interpretation, not a quoted assignment |

**Decision required** (IAM Lead + Regulatory Content owner):
- Confirm `content_author` for creating and revising definitions, or name another permission.
- State whether the Encoding Engineer (also `content_author` today) may author legal content selection, or only the
  Regulatory Analyst (RUL: "Change legal meaning without analyst/legal review" is prohibited for the engineer).

### B3: independent legal review of a definition revision

| | |
|---|---|
| Spec requires | Legal review exists for requirement content (VERIFIED by "Qualified reviewer", GLS §04) and for rules and source mappings (RUL §10 "Approve/reject interpretation and source mapping"). Nothing for definitions |
| Code does | No definition review. The safeguards are verified provisions at link time, the rule draft's legal approval, and the release gate |
| Gap | Which provisions form one obligation, and the duty-bearer/use-case split (DOM §13), are legal interpretation without a named review step |

**Decision required** (RUL owner + GC): is a definition revision released only after a distinct qualified legal
reviewer approves it, separate from its author, as RUL's separation-of-duties pattern does for rules? The design
reserves the transition either way (§4).

### B4: schedule-code vocabulary

| | |
|---|---|
| Spec requires | DOM §6.1 ObligationScheduleDefinition: "one-time, recurring, event-triggered, continuous or windowed", validated by rule/content tests |
| Code does | `CONTINUOUS`, `RECURRING`, `EVENT_TRIGGERED` (the values the schema already held) and `recurrence_days` |
| Gap | GLS-001 has no controlled schedule codes; one-time and windowed duties cannot be represented; recurrence is a day count only |

**Decision required** (GLS owner + Regulatory Content owner):
- Controlled codes for the five schedule kinds.
- The structure of a recurrence or window rule (DOM: "Encoded as structured deadline/schedule rule, not text only").
- Whether a schedule change is a new definition revision. The design assumes yes.

### B5: editable fields and retirement (blocking)

| | |
|---|---|
| Spec requires | Published content immutable (DAT-REQ-012). Coverage `RETIRED`: "Previously supported coverage no longer maintained" (GLS). Content WITHDRAWN requires a reason (GLS §04) |
| Code does | Creation only; no edit of title, subject type, coverage, schedule, materiality or template; no retirement |
| Gap | No definition lifecycle |

**Decision required** (Regulatory Content owner, with the GLS owner for codes):

| Field | Design classification |
|---|---|
| Key, instrument | Identity, immutable |
| Title | Revisable without legal effect |
| Subject type, schedule, materiality, coverage mode, links, satisfaction template | Semantic: a new revision |

The owner must also say whether retirement is coverage `RETIRED` on a new revision, GLS `WITHDRAWN`, or both, and
whether retiring stops new evaluations, existing obligations, or both.

### B6: link changes after a rule draft's legal approval

| | |
|---|---|
| Spec requires | RUL §19: "Post-approval mutation — Release-ready canonical artifact frozen and hashed; any edit invalidates approvals/tests and creates new draft revision." Each draft revision is auditable (RUL §17) |
| Code does | Changing an unreleased definition's links does not touch its rule drafts. The release gate re-checks links at publish |
| Gap | A legal reviewer may approve source mappings to provisions that are later unlinked |

**Decision required** (RUL owner): does a change to the definition revision a draft targets invalidate that draft's
peer/legal approval? With revisions this becomes simple, because a draft would target one immutable definition revision
(§2.3) and a changed definition is a different revision. The design assumes drafts pin a revision, so the question
reduces to "a draft must be re-targeted to release against a newer revision".

### B7: linkable states and COMPENSATION restriction (confirmation)

| | |
|---|---|
| Spec requires | WITHDRAWN "removed from release eligibility or use"; SUPERSEDED "replaces this version for future valid time"; AI_EXTRACTED non-binding (GLS §04); INV-09; DOM-INV-13; "governed COMPENSATION" nodes (STA-REQ-010); tenant/entity scoped objects not addressable from other scopes (DOM-INV-11) |
| Code does | Links only to REVIEW_REQUIRED / VERIFIED / PUBLISHED content of the definition's instrument; COMPENSATION refused in catalogue templates |
| Gap | Both rules are derived, not quoted |

**Decision required** (Regulatory Content owner; STA owner): confirm both rules. Also: may a definition link provisions
from more than one instrument? The model has one `instrument_id` per definition, and DOM does not restrict it.

## 2. Proposed data model

Tables named in the ZA-DAT-001 catalogue convention, current physical names in brackets. Every new table is global
catalogue content: no `tenant_id`, no RLS. As with the existing `catalog_*` tables, the runtime role gets SELECT and
INSERT, plus the narrow UPDATE noted.

### 2.1 `catalog_obligation_definition` (existing: becomes the stable identity)

Kept as the stable identity: `obligation_definition_id`, `key` (unique), `instrument_id`. The semantic columns
(`provision_ids`, `subject_type`, `coverage_mode`, `schedule_kind`, `recurrence_days`, `material`,
`satisfaction_template`) become a read cache of the **current** revision during expand. Readers move to the revision
during migrate, and the columns are dropped in a later contract step.

### 2.2 `catalog_obligation_definition_revision` (new)

| Column | Type | Rule |
|---|---|---|
| `definition_revision_id` | uuid PK | Immutable revision identity (ARCH §5.2 `revision_id`) |
| `obligation_definition_id` | FK → definition | Stable identity |
| `revision` | int | Unique with the definition; 1, 2, … in record order |
| `title`, `subject_type`, `coverage_mode`, `schedule_kind`, `recurrence_days`, `material` | as today | Semantic content (B4/B5 may change the schedule columns) |
| `satisfaction_template` | json + `satisfaction_template_sha256` | Template and its digest (RUL §12 convention) |
| `valid_from`, `valid_to` | date | Legal-date range (B1-b) |
| `record_seq` | bigint | Record time of creation (TIM-REQ-004/005) |
| `lifecycle` | GLS content code | REVIEW_REQUIRED → VERIFIED → PUBLISHED → SUPERSEDED / WITHDRAWN (§4); the only column the runtime may UPDATE, guarded like 0012/0013 status-only updates |
| `content_release_id` | uuid null | Set once, at publication |
| `supersedes_revision_id` / `correction_of_revision_id` | FK self, null | Lineage (ARCH §5.2) |
| `authored_by`, `reason` | | Provenance |
| `backfill` | bool | True for rows created by the migration (§5), so derived record time is never presented as observed |

Database rules:
- Unique (`obligation_definition_id`, `revision`).
- At most one revision in REVIEW_REQUIRED or VERIFIED per definition: a partial unique index, so parallel drafts cannot
  fork.
- Non-overlap of PUBLISHED revisions' valid ranges per definition (DAT-REQ-010; exclusion constraint, B1-b).
- A trigger forbids every change except a permitted lifecycle move and forbids DELETE, for all roles (0003/0004
  pattern).

### 2.3 `catalog_obligation_definition_link` (new; replaces the JSON list)

| Column | Type | Rule |
|---|---|---|
| `definition_revision_id` | FK → revision | Part of PK |
| `provision_id` | FK → `catalog_provision` | Part of PK: no duplicate link per revision |
| `requirement_version_id` | FK → `catalog_requirement_version` | Must equal the provision's own requirement version (composite FK on (`provision_id`, `requirement_version_id`), which needs a unique constraint on that pair in `catalog_provision`) |
| `record_seq`, `linked_by`, `reason` | | Provenance |

Insert-only. Links belong to a revision, so a released revision's links are immutable through the revision's
immutability. A link change on a released definition is a new revision with a new link set.

### 2.4 Revision references on dependent rows (expand; nullable for existing rows)

| Table | New column | Why |
|---|---|---|
| `catalog_rule_draft` | `definition_revision_id` | A draft targets one immutable revision (B6) |
| `catalog_rule_version` | `definition_revision_id` (nullable; immutable table, so set on insert only) | DOM §13 rule-to-definition lineage; DAT-REQ-026 |
| Release manifest (JSON) | `obligation_definition_revisions[]`: revision ID, revision number, link set, template digest | Signed binding (RUL §02); verified by the A9b verifier |
| `applicability_evaluation` | none: it already names `rule_version_id`, which will name the revision | Avoids touching an immutable, tenant-scoped table |
| `assurance_obligation_instance_revision` | `definition_revision_id` (nullable) | DAT-REQ-026; DOM §13 "per canonical scoped obligation revision" |

`assurance_obligation_instance_revision` is tenant-scoped with FORCE RLS. It is populated on new instance revisions
from the adopted evaluation's rule version, so the adoption *algorithm* is unchanged and only one more column is
recorded. Applying this is a decision for the Obligation domain owner (B1-d).

## 3. API contract changes (all additive)

| Endpoint | Change |
|---|---|
| `GET /catalog/obligation-definitions/{id}` | Adds `current_revision` and `revisions[]` (number, lifecycle, valid range, release). Existing fields keep their meaning and describe the current revision |
| `GET /catalog/obligation-definitions/{id}/revisions/{revision}` | New: one revision with its links, resolved as today |
| `POST /catalog/obligation-definitions` | Unchanged request. Creates the definition **and revision 1** (REVIEW_REQUIRED) |
| `POST /catalog/obligation-definitions/{id}/revisions` | New (B1-a, B5): opens the next revision from the current one with changed fields and/or links. 409 if one is already open |
| `POST …/{id}/links`, `…/links/{provision_id}/remove` | Kept. Act on the **open** revision. On a definition with only released revisions, 409 becomes `OPEN_REVISION_REQUIRED` with a pointer to `/revisions` (no silent change, as today) |
| `POST …/{id}/revisions/{revision}/verify` | New, only if B3 requires review: REVIEW_REQUIRED → VERIFIED by a distinct legal reviewer |
| `POST …/{id}/revisions/{revision}/withdraw` | New, only per B5: → WITHDRAWN with reason |
| `POST /rules/drafts` | Accepts an optional `definition_revision_id`, defaulting to the open or current revision. Publication refuses a revision that is not releasable (B3) |
| `GET /obligations/{lineage}` | `regulatory_provenance` adds `definition_revision`. `provisions` is read from that revision's links, not the current definition |
| Release verification | New check `DEFINITION_REVISION_BINDING` (present, links equal the signed set). Releases without it report UNVERIFIABLE for that check (A9b convention) |

No existing response field is removed or changes meaning; errors stay coded `{error, message, …}` objects.

## 4. Lifecycle

States are existing GLS-001 §04 content codes only; no new vocabulary. Transitions marked † exist only if the named
decision approves them.

```
                     create / open revision (content author, B2)
                                   |
                                   v
                          +-----------------+   withdraw† (B5, reason)   +-----------+
        edit links/fields |  REVIEW_REQUIRED |-------------------------->| WITHDRAWN |
        (same revision) ->+-----------------+                            +-----------+
                                   |                                           ^
                     legal verify† (B3: distinct qualified reviewer)            |
                     -- or, if B3 says no review: released directly --          |
                                   v                                           |
                          +-----------------+  returned for review†            |
                          |    VERIFIED     |----------------> REVIEW_REQUIRED |
                          +-----------------+                                  |
                                   |  rule release pins this revision (B1-c)   |
                                   v                                           |
                          +-----------------+    withdraw† (release authority) |
                          |    PUBLISHED    |----------------------------------+
                          +-----------------+
                                   |  a newer revision of the same definition is PUBLISHED
                                   v
                          +-----------------+
                          |   SUPERSEDED    |  (terminal; history kept)
                          +-----------------+
```

Invariants:
- Only REVIEW_REQUIRED revisions accept edits.
- PUBLISHED, SUPERSEDED and WITHDRAWN revisions are immutable (DAT-REQ-012).
- At most one open revision per definition.
- A release supersedes older PUBLISHED revisions for future valid time only.
- Obligation instances keep the revision their adoption used (B1-d).

## 5. Migration and backfill proposal

Two migrations, both expand/contract (DAT-REQ-023). The number is **0015 or later**: 0014 is reserved for the separate
catalogue-lifecycle-events decision. If both are approved together, they can be sequenced 0014 → 0015.

1. **Expand (one migration):**
   - Create the revision and link tables, the nullable revision columns, the triggers and the grants.
   - Add the unique (`provision_id`, `requirement_version_id`) constraint on `catalog_provision`; it holds already,
     since the provision ID is the PK.
2. **Backfill, in the same migration.** Idempotent and resumable (DAT-REQ-024):
   - For each existing definition, create **revision 1** from today's columns.
   - **Lifecycle:** PUBLISHED if any of its RuleVersions is PUBLISHED; otherwise REVIEW_REQUIRED.
   - **Valid range:** the earliest published RuleVersion's `valid_from`, otherwise the earliest linked requirement
     version's `valid_from`. If neither exists, `valid_from` stays null and the row carries `backfill=true`. This is
     subject to B1-b.
   - **`record_seq`:** the minimum of the definition's RuleVersion and linked requirement-version record sequences,
     which is the earliest *derivable* point the definition existed. If there is none, the migration's own sequence
     value. `backfill=true` either way, so the record time is labelled derived, never fabricated.
   - **Links:** one link row per JSON `provision_ids` entry whose provision exists, with its requirement version taken
     from the provision row.
     - Entries naming a missing provision are not invented. They are recorded in a backfill report table (or in the
       migration log for review) and reported by the existing traceability validation as
       `OBLIGATION_PROVISION_MISSING`.
     - A definition with an empty list (one seeded MONITORING_ONLY definition) gets a revision with no links and a
       validation warning, as today.
   - **Existing RuleVersions** are immutable. They are not updated. Their revision is resolved through the backfilled
     mapping (the definition's revision 1), and release verification reports the legacy case as UNVERIFIABLE for
     `DEFINITION_REVISION_BINDING`, never as a pass.
   - **Existing obligation instances** keep a null `definition_revision_id`. Readers fall back to the definition's
     revision 1 and label it `DERIVED` (same pattern as `DEFINITION_DERIVED` in the traceability report). They are not
     back-filled: their adoption was against the definition as it was, which for all current data is revision 1, but
     stating so as recorded fact would fabricate record history.
3. **Migrate (code release):** writers use revisions and links; readers prefer revisions and fall back to the cache.
4. **Contract (later migration, after a release cycle):** drop `provision_ids` and the semantic cache columns, or keep
   them as a generated view. The decision rests on API consumers.

**Rehearsal and rollback.**
- Rehearse on a restored copy in a scratch PostgreSQL 17 cluster. Prove that row counts, link counts and release
  verification results are identical before and after, apart from the new check.
- Downgrade of the expand migration drops only the new objects. Post-migration revisions would be lost, which is
  acceptable before production and is documented.
- The dev database is upgraded only on the user's instruction, after a verified backup.

## 6. Compatibility and security analysis

| Area | Effect |
|---|---|
| Existing releases | Untouched (immutable). Verification gains one check that reports UNVERIFIABLE for legacy releases. Integrity and signature results are unchanged |
| Evaluations and determinations | Untouched; still resolved by `rule_version_id`; algorithm unchanged |
| Adoption | Same algorithm. One extra column recorded on new obligation instance revisions (B1-d) |
| Obligation dossiers | Provisions read from the instance's revision instead of the current definition. For all existing data the result is identical (revision 1 = today's links) |
| Frontend | Additive fields only; no screen depends on the semantic columns changing |
| Authorization | Unchanged guard (`content_author` + platform; B2). Reads stay catalogue-read. New verify/withdraw transitions need the permissions B3/B5 name |
| Tenant isolation | New tables are global catalogue content, as today. The only tenant-scoped change is a nullable column on an RLS-protected table, written in the tenant's own transaction |
| Immutability | Moves from API-only to database-enforced for definitions (DAT-REQ-012), closing the gap in the authoring report |
| A10 caveat | The table owner can still disable triggers (audit A10). Signed release binding plus A9b verification is the detection layer |

## 7. Test strategy

| Layer | Tests |
|---|---|
| Migration | Upgrade/downgrade on fresh PostgreSQL 17; backfill idempotence (run twice, no duplicates); restored-copy rehearsal with before/after parity; `backfill=true` on every derived row; missing-provision entries reported, not linked |
| Database guards (owner and runtime role) | UPDATE of a released revision refused; DELETE refused; only permitted lifecycle moves; one open revision per definition; duplicate link refused by PK; composite FK refuses a provision paired with another requirement version; overlap of published valid ranges refused |
| API | Create = definition + revision 1; open revision; edit only the open one; 409 `OPEN_REVISION_REQUIRED` on released-only definitions; revisions listed in order; verify/withdraw only if decided |
| Release | A draft pins a revision; publication binds the revision and its links in the signed manifest; a newer published revision supersedes the older; A9b verifies the binding and detects a tampered link row |
| Regression | Applicability and adoption golden flows unchanged, including UNCERTAIN → no obligation and re-adoption refused; dossier provisions equal the adopted revision's links after a newer revision is released; historical release verification unchanged |
| Concurrency | Two authors opening a revision at once (one wins); concurrent link insert (PK); publication racing a link edit (row lock or trigger) |
| Generated inventories | `test_authorization` allowed sets, read matrix regeneration, `test_role_registry` global params, event ratchet, CI `--require` lines |

## 8. Approval checklist

| # | Item | Approver | Status |
|---|---|---|---|
| 1 | B1-a revisions of a stable definition, not new keys | Regulatory Content owner | Open |
| 2 | B1-b valid-time profile for revisions (and DOC-CON-003) | TIM owner + Data Architect | Open |
| 3 | B1-c one revision pinned per rule release; binding only via release | RUL owner | Open |
| 4 | B1-d obligation instances keep their adopted revision; a newer revision triggers re-evaluation/review, not re-pointing | Obligation / Assurance domain owner | Open |
| 5 | B2 authoring permission | IAM Lead + Regulatory Content owner | Open |
| 6 | B3 independent legal review of definition revisions (yes/no; who) | RUL owner + GC | Open |
| 7 | B4 schedule codes and structure | GLS owner + Regulatory Content owner | Open |
| 8 | B5 field classification and retirement/withdrawal semantics | Regulatory Content owner + GLS owner | Open |
| 9 | B6 drafts pin a revision; re-target to release against a newer one | RUL owner | Open |
| 10 | B7 linkable states; COMPENSATION refusal; single-instrument definitions | Regulatory Content owner + STA owner | Open |
| 11 | Migration numbering relative to 0014 (catalogue lifecycle events) | Data Architect + you | Open |
| 12 | Backfill rules in §5 (derived record time, missing-provision handling, legacy releases UNVERIFIABLE) | Data Architect | Open |
| 13 | Permission to upgrade the dev database after a verified backup | You | Not requested yet |

## 9. Implementation sequence (after approval)

1. Record the decisions (ADR or decision log; ADR-REQ-006: AI does not approve them).
2. Migration (expand + backfill), with its rehearsal on a restored copy and the guard tests.
3. Engine: revision model in `engines/catalog.py`; the current authoring functions become operations on the open
   revision. Release binding and `publish_bound` pin and transition revisions.
4. API: the additive endpoints and fields (§3); generated-test inventories.
5. Release verification: `DEFINITION_REVISION_BINDING` check.
6. Obligation dossier and provenance read the instance's revision. Adoption records the revision (B1-d).
7. Full suite on SQLite and fresh PostgreSQL 17; CI gate lines.
8. Dev-database upgrade only on your instruction, after a verified backup.
9. Contract migration after a release cycle.
