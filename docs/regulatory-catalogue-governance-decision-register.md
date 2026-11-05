# Regulatory catalogue governance: decision register

Date prepared: 2026-11-04. Prepared by engineering (AI-assisted) for the ZA specification owners, architects and
engineering leads. **This document approves nothing.**
- ADR-REQ-006: "AI systems MUST NOT accept, reject, supersede or approve ADRs."
- ADR-REQ-005: "T0 decisions MUST NOT be accepted solely by their author."

Every "Recommendation" below is an engineering recommendation, not a decision. Every decision starts **OPEN**.

Sources (controlled specifications take precedence over the reports and designs):
- ZA-DOM-001, ZA-GLS-001, ZA-RUL-001, ZA-SEC-001, ZA-DAT-001 (DAT-REQ-012/024/026), ZA-ARCH-001, ZA-TIM-001;
  ZA-ADR-001 (decision tiers; C-02, C-07; RUL-ADR-06; TIM-ADR-03/04; DAT-ADR-06/07); ZA-DOC-001 §07 (DOC-CON-003);
  ZA-APP-001, ZA-STA-001, ZA-EVT-001.
- Engineering documents:
  - [obligation-definition-versioning-architecture.md](obligation-definition-versioning-architecture.md)
  - [obligation-definition-authoring-implementation-report.md](obligation-definition-authoring-implementation-report.md)
  - [regulatory-release-signature-verification-report.md](regulatory-release-signature-verification-report.md)
  - [regulatory-requirement-traceability-implementation-report.md](regulatory-requirement-traceability-implementation-report.md)
  - [backend-decision-brief-content-signature-trust.md](backend-decision-brief-content-signature-trust.md)
  - [backend-architecture-roadmap.md](backend-architecture-roadmap.md) (Stage 3c, B2)

All ZA documents are v1.0 "Proposed for lock". ZA-ADR-001 is unapproved, so its baseline entries are "ACCEPTED* on
approval of this register" and **not in force**. Where an entry below cites one, it is evidence of intent, not an
approved decision.

## Executive summary

**Verdict: PARTIALLY READY.** 18 of the 21 decisions can be approved or rejected from this pack as written. Three cannot
be closed by a decision alone, because the controlled text they depend on does not exist or is a registered conflict:

| Decision | Missing specification evidence | What has to happen first |
|---|---|---|
| **K3** (signature algorithm profile) | ZA-RUL-001 §12 delegates the profile to "ZA-KEY-001 / security baseline"; **ZA-KEY-001 does not exist** | Commission and approve ZA-KEY-001, or name the baseline that replaces it |
| **B4** (schedule codes) | ZA-GLS-001 has **no** schedule-kind codes; DOM-001 §6.1 names five kinds only in prose | GLS-001 amendment (new controlled codes; DAT-REQ-025) |
| **B1-b** (valid-time profile) | DOM-001's universal UTC `valid_period` is the registered baseline blocker **DOC-CON-003**; ZA-TIM-001 controls | Issue DOM 1.1 per DOC-CON-003's resolution, or confirm the TIM profile for this object class |

**What is blocked and why:**
- **Changing released obligation definitions** (versioning): B1-a…d, B2, B5, plus schema.
- **Trustworthy (VALID) release verification:** K1–K3, K5, K6.
- **Database-enforced catalogue immutability** (migration 0014): the event-table convention and the role model (A10).

Today's working tree enforces these rules in the API only, and verifies releases to UNVERIFIABLE at best.

**Resolve together** (§3): B1-a/b/c, R1, K2/K3 (key and algorithm references) and the 0014 convention. They all change
the same immutable tables (`catalog_content_release`, `catalog_rule_version`) and the signed-manifest format. Deciding
them separately would force two incompatible manifest versions and two migrations over immutable rows.

## How to use this register

Each decision has the same fields:

| Field | Meaning |
|---|---|
| Tier | ZA-ADR-001 class. T0 = trust/legal/safety-critical, including signing keys, applicability semantics, IAM authority, and an ADR always required with named accountable approval. T1 = platform/contract, including database strategy |
| Effect | **BLOCKING** (implementation cannot start), **SCOPE** (changes what can ship), **INFO** |
| Spec mandate | Quoted controlled text; what is *required* |
| Code today | Current behaviour in the working tree |
| Gap / if unresolved | Consequence of no decision |
| Options | Credible options (ADR-REQ-004) |
| Recommendation | Engineering's view, only where evidence supports one, labelled as such |
| Owner / approvers | Accountable owner and required approvers |
| Depends on | Other decisions |
| Decision record | Outcome, rationale, approvers' names, date, ADR ID. **Blank until decided** |

Each decision is recorded as a domain-prefixed ADR (ADR-REQ-001/002). For T0 decisions: context, drivers, options,
decision, rationale and consequences (ADR-REQ-004), and approval by someone other than the author (ADR-REQ-005).

---

## A. Obligation definition versioning

### B1-a: Is a released definition changed by a new revision of the same definition, or by a new definition?

| Field | Content |
|---|---|
| Tier / Effect | T0 (applicability semantics) / **BLOCKING** |
| Spec mandate | DOM §6.1: ObligationDefinition = "Versioned reusable obligation semantics derived from one or more provision versions… Regulatory Content owns." DOM §1.1: "Stable identity is separate from revision identity… each revision is immutable once closed in record time." DOM §13: "ObligationDefinition → RuleVersion 1:N over time"; "Adopted Determination → ObligationInstance 1:0..1 per canonical scoped obligation revision." |
| Code today | One mutable row per definition; links can change only before the first published rule (409 `OBLIGATION_DEFINITION_RELEASED` after) |
| Gap / if unresolved | A released definition can never take on a newer requirement version; the only workaround is a new key, which breaks every adopting organisation's obligation lineage |
| Options | (1) New immutable revision under the same stable definition. (2) A new definition per change, with a "replaces" link. (3) Mutable definition with history table only |
| Recommendation | **Option 1.** DOM §6.1 and §1.1 describe exactly a stable identity with immutable revisions. Option 3 conflicts with DAT-REQ-012 for released content; option 2 conflicts with "reusable" and with lineage continuity |
| Owner / approvers | Regulatory Content owner (accountable); CTO; Head of Assurance |
| Depends on | — (root decision) |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer (state trigger and interim behaviour, ADR-REQ-012) — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B1-b: Valid-time profile of a definition revision

| Field | Content |
|---|---|
| Tier / Effect | T0 (temporal semantics) / **BLOCKING**; depends on missing evidence (DOC-CON-003) |
| Spec mandate | ARCH §5.2: every assurance object carries valid time and record time; "Corrections create new record-time versions; they do not erase prior knowledge states." DOC-CON-003 (HIGH, baseline blocker): DOM's UTC `valid_period` "is incorrect for date-only legal effectiveness"; "ZA-TIM-001 controls temporal semantics: LEGAL_DATE_RANGE uses calendar dates; OPERATIONAL_INSTANT_RANGE uses UTC instants. No implicit UTC-midnight conversion." TIM-ADR-03 (ACCEPTED*, T0, Data Architect + CTO): "Native `daterange`; no UTC-midnight coercion." |
| Code today | Requirement and rule versions use `valid_from`/`valid_to` dates (legal dates) and `record_seq`; definitions have no valid time |
| Gap / if unresolved | The revision table cannot be designed; overlap constraints (DAT-ADR-06) cannot be specified |
| Options | (1) LEGAL_DATE_RANGE valid time + record sequence. (2) OPERATIONAL_INSTANT_RANGE. (3) Record time only, with valid time inherited from rule versions |
| Recommendation | **Option 1.** It matches DOC-CON-003's stated resolution, TIM-ADR-03 and the existing requirement and rule versions. It still needs DOM 1.1 or explicit TIM-owner confirmation |
| Owner / approvers | TIM owner (accountable); Data Architect; CTO |
| Depends on | B1-a; DOC-CON-003 resolution |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B1-c: Must every rule release pin exactly one definition revision?

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING** |
| Spec mandate | DAT-REQ-026: "Material references required for reconstruction MUST point to immutable revision/version IDs, not only mutable logical IDs." ZA-RUL-001 §02: ContentRelease is the "Signed package of RequirementVersion(s), RuleVersion(s), SetVersion refs…". DOM-INV-10: snapshots store exact revision IDs. ARCH §5.1: ObligationTemplate "versioned by content release". |
| Code today | A RuleVersion names only `obligation_definition_id`. The signed manifest names the exact requirement versions and provision IDs (`requirement_versions[]`), not a definition revision |
| Gap / if unresolved | Historical reconstruction cannot prove which definition semantics a rule was released under (DAT-REQ-026) |
| Options | (1) Each new RuleVersion and its manifest pin one revision; a revision becomes binding only through a release. (2) Pin in the manifest only. (3) No pin; rely on the requirement binding |
| Recommendation | **Option 1.** DAT-REQ-026 and RUL §02 together require the exact reference, and the signed manifest is the place RUL §12 makes authoritative |
| Owner / approvers | RUL owner (accountable); Data Architect |
| Depends on | B1-a; R1 (manifest format); K2/K3 (manifest key/algorithm fields) |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B1-d: Do existing obligations keep the definition revision they were adopted under?

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING** for the obligation part; SCOPE for the catalogue part |
| Spec mandate | DOM §6.1: ObligationInstance "references exact content + attestation" (Obligation domain owns). DAT-REQ-009: corrections "create new revisions rather than overwriting history". DAT-REQ-026 (above). DOM §13 "per canonical scoped obligation revision". |
| Code today | Obligation instances reference only `obligation_definition_id`; the exact chain is recoverable through determination → evaluation → rule version → signed binding |
| Gap / if unresolved | DAT-REQ-026 is not met for the definition reference; nothing says whether a newer revision silently changes existing obligations |
| Options | (1) Keep the adopted revision; a newer released revision triggers the existing re-evaluation and review path; never silent re-pointing. (2) Re-point to the newest revision. (3) Require re-adoption |
| Recommendation | **Option 1.** Option 2 rewrites the meaning of an attested adoption (DAT-REQ-009, DOM-INV-04 attestation). Option 3 is a valid variant of option 1's review outcome and is for the domain owner to choose |
| Owner / approvers | Obligation / Assurance domain owner (accountable); Head of Assurance; Data Architect |
| Depends on | B1-a, B1-c |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B2: Authoring permission: `content_author`, and the Encoding Engineer's authority

| Field | Content |
|---|---|
| Tier / Effect | T0 (IAM authority) / **BLOCKING** for revision authoring; today's interim use needs ratification |
| Spec mandate | DOM §6.1 "Regulatory Content owns"; GLS: "Zoiko-authored". RUL §10 roles: Regulatory Analyst "Create/edit RuleDraft, source mappings…"; Encoding Engineer "Edit typed AST/decision-table structures" and is prohibited from "Change legal meaning without analyst/legal review". **No role is assigned obligation-definition authoring.** |
| Code today | `content_author` (held by CONTENT_ANALYST and ENCODING_ENGINEER) on a platform principal |
| Gap / if unresolved | The authoring guard rests on an interpretation; the Encoding Engineer can change legal provision selection, which RUL prohibits without analyst/legal review |
| Options | (1) Ratify `content_author` for both roles. (2) Analyst authors; Encoding Engineer cannot change links/semantic fields (separate permission). (3) Both may author, with mandatory analyst/legal review per B3 |
| Recommendation | Evidence supports excluding unreviewed legal-meaning changes by the Encoding Engineer (RUL §10), so option 2 or 3. Which one depends on B3. No further recommendation |
| Owner / approvers | IAM Lead (accountable); Regulatory Content owner |
| Depends on | B3 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B3: Is independent legal review of a definition revision required?

| Field | Content |
|---|---|
| Tier / Effect | T0 (legal) / SCOPE (adds a VERIFIED transition and its permission) |
| Spec mandate | No direct mandate. Analogues: requirement content becomes VERIFIED by a "Qualified reviewer" (GLS §04); rules need legal approval of "interpretation and source mapping" (RUL §10); "State machine requires distinct author/reviewer/release authorities where segregation policy applies" (RUL §19). |
| Code today | No definition review; safeguards are verified provisions, rule-draft legal approval, and the release gate |
| Gap / if unresolved | Which provisions form one obligation, and the duty-bearer/use-case split (DOM §13), is legal interpretation with no named reviewer |
| Options | (1) Mandatory independent legal review (REVIEW_REQUIRED → VERIFIED) before a revision can be released. (2) Covered by the rule draft's legal review, if B6 pins the revision. (3) No review |
| Recommendation | **Engineering recommendation: option 1 or 2.** Both keep the RUL separation pattern. Option 2 is sufficient only if B6 adopts revision pinning. Evidence does not decide between them |
| Owner / approvers | RUL owner (accountable); General Counsel; Head of Assurance |
| Depends on | B1-a, B6 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B4: Schedule-code vocabulary

| Field | Content |
|---|---|
| Tier / Effect | T1 (controlled vocabulary) / SCOPE; **depends on missing evidence** |
| Spec mandate | DOM §6.1 ObligationScheduleDefinition: "one-time, recurring, event-triggered, continuous or windowed"; DOM §4.2: timing "Encoded as structured deadline/schedule rule, not text only". DAT-REQ-025: codes "MUST follow ZA-GLS-001". **GLS-001 defines no schedule codes.** |
| Code today | `CONTINUOUS`, `RECURRING`, `EVENT_TRIGGERED` (pre-existing schema values) + `recurrence_days` |
| Gap / if unresolved | One-time and windowed duties cannot be represented; the values used are not controlled codes |
| Options | (1) GLS amendment with five codes and a structured rule schema. (2) Ratify the three existing values as interim controlled codes |
| Recommendation | None; vocabulary is the GLS owner's |
| Owner / approvers | GLS owner (accountable); Regulatory Content owner |
| Depends on | — ; informs B5 (schedule as a semantic field) |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR / GLS change ID: ______ |

### B5: Editable fields and retirement semantics

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING** for revision implementation |
| Spec mandate | DAT-REQ-012: PUBLISHED catalogue immutable. GLS coverage `RETIRED`: "Previously supported coverage no longer maintained." GLS §04 WITHDRAWN: "removed from release eligibility or use; reason required." |
| Code today | Create only; no edits or retirement |
| Gap / if unresolved | The revision model cannot say which changes need a new revision or how a definition stops being used |
| Options | Field classes: (a) immutable identity: key, instrument; (b) non-semantic, revisable in place: title; (c) semantic, new revision: subject type, schedule, materiality, coverage, links, satisfaction template. Retirement: (1) a revision with coverage `RETIRED`; (2) GLS `WITHDRAWN` of the current revision; (3) both, for different meanings (no longer maintained, versus not to be used) |
| Recommendation | The field classes follow from DAT-REQ-012 and are recommended as proposed. Retirement: none; the owner must also say whether it stops new evaluations, existing obligations, or both |
| Owner / approvers | Regulatory Content owner (accountable); GLS owner (codes); Head of Assurance (effect on obligations) |
| Depends on | B1-a, B4 |
| Decision record | ☐ Approve ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B6: Rule-draft approval invalidation and revision retargeting

| Field | Content |
|---|---|
| Tier / Effect | T0 / SCOPE |
| Spec mandate | RUL §19 "Post-approval mutation — Release-ready canonical artifact frozen and hashed; any edit invalidates approvals/tests and creates new draft revision." RUL §17: each draft save "creates auditable draft revision". |
| Code today | Definition link changes do not affect rule drafts; the release gate re-checks links at publish |
| Gap / if unresolved | A legal reviewer may approve source mappings to provisions that are later unlinked |
| Options | (1) A draft pins one definition revision; releasing against a newer revision requires re-targeting, which is a new draft revision and invalidates approvals. (2) Any definition change invalidates every open draft. (3) No coupling (today) |
| Recommendation | **Option 1.** It applies RUL §19 to the immutable revision the draft depends on, and does not invalidate unrelated drafts |
| Owner / approvers | RUL owner (accountable) |
| Depends on | B1-a, B1-c |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### B7: Linkable content states, COMPENSATION refusal, single-instrument definitions

| Field | Content |
|---|---|
| Tier / Effect | T0 / INFO (ratification of interim derived rules) |
| Spec mandate | GLS §04: WITHDRAWN "removed from release eligibility or use"; SUPERSEDED "replaces this version for future valid time"; AI_EXTRACTED "non-binding". INV-09; DOM-INV-13; STA-REQ-010 "governed COMPENSATION"; DOM-INV-11 (tenant-scoped objects). The model has one `instrument_id` per definition; DOM does not restrict definitions to one instrument |
| Code today | Links only to REVIEW_REQUIRED / VERIFIED / PUBLISHED content of the definition's instrument; COMPENSATION refused in catalogue templates |
| Gap / if unresolved | Rules stand as derived, not quoted; cross-instrument obligations cannot be modelled |
| Options | Ratify each of the three rules, or replace it with a stated alternative |
| Recommendation | Ratify the state and COMPENSATION rules (they follow from the cited text). Single-instrument: no recommendation |
| Owner / approvers | Regulatory Content owner (accountable); STA owner (COMPENSATION) |
| Depends on | — |
| Decision record | States ☐ Approve ☐ Reject — COMPENSATION ☐ Approve ☐ Reject — Single-instrument ☐ Approve ☐ Reject — Rationale: ______ — Approvers: ______ — Date: ______ |

---

## B. Release signature trust

Context:
- **Spec mandates:**
  - APP-REQ-006: a binding RuleVersion "MUST be PUBLISHED, signature-valid…".
  - RUL-REQ-027: manifests "signed by authorized release infrastructure".
  - SEC-REQ-026: content-release keys "purpose-separated and accessible only to authorized release/signing services".
  - SEC-REQ-048: a verification failure "MUST fail closed… and raise a security signal".
  - SEC-REQ-049: every cryptographic use "MUST declare purpose, algorithm/profile, key owner, rotation/verification
    requirements and MUST avoid custom cryptographic primitives".
- **Code today:** one mixed-purpose Ed25519 cell key signs releases, packages and checkpoints (audit I-05).
  Verification reports intact releases as **UNVERIFIABLE**, never VALID.

### K1: Authorized content-release signing key and its distribution

| Field | Content |
|---|---|
| Tier / Effect | T0 (signing keys) / **BLOCKING** for VALID verification |
| Spec mandate | C-07 (ACCEPTED*): "content release and package signing are distinct" keys. RUL-ADR-06 (T0, ACCEPTED*; CTO + Head of Assurance + GC): "Managed signing key under regional security architecture; release service only, no analyst-held private keys." SEC §14: "Dedicated signing purpose… tightly limited release service; alert on use." THR-012 controls include "cell verification". |
| Code today | Cell key; no trust anchor; `SIGNER_TRUST` always UNVERIFIABLE |
| Gap / if unresolved | No release can be proven authentic (APP-REQ-006 not met); a key holder below the application can forge releases undetected |
| Options | (1) Dedicated release-service key in KMS/HSM; cells pin its public key or certificate through signed configuration. (2) Certificate chain with a content-release CA. (3) Interim dedicated software key, pending K6 |
| Recommendation | None on the mechanism; the mandate requires *a* dedicated release-service key |
| Owner / approvers | CTO, Head of Assurance, GC (RUL-ADR-06); Security Architect (G-06) |
| Depends on | K3, K6 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### K2: Key identity, versioning and rotation

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING** (manifest/key-reference format) |
| Spec mandate | SEC §14: "Key rotation preserves ability to verify historical signatures/hashes where required; key revocation and compromise procedures are documented/tested." SEC-REQ-044: rotation and revocation "operationally testable". SEC §18 telemetry: "Key ID/purpose… never key bytes." |
| Code today | Key ID = `ed25519:` + 16 hex of the public key; unversioned; no registry; a release naming another key is UNVERIFIABLE |
| Gap / if unresolved | Rotation would make all earlier releases unverifiable |
| Options | (1) Versioned key IDs plus a verify-only trust store of retired public keys, each with a validity period and revocation status. (2) Re-sign or re-release on every rotation |
| Recommendation | **Option 1.** SEC §14 requires historical verification; option 2 rewrites or duplicates immutable releases (RUL-INV-01) |
| Owner / approvers | CTO; Security Architect |
| Depends on | K1, K3 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### K3: Cryptographic algorithm profile (ZA-KEY-001)

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING**; **depends on missing evidence** |
| Spec mandate | ZA-RUL-001 §12: "Hash algorithm: Platform cryptographic profile defined by ZA-KEY-001 / security baseline; algorithm identifier stored with digest." SEC-REQ-049. C-02 (ACCEPTED*): "Canonical JSON v1… is the Phase-1 authority for signed/hashes rule, ledger and snapshot artifacts." Ed25519 appears in the specs only for webhooks (C-11, API-ADR-06). **ZA-KEY-001 is not in the Mandatory Engineering Documents Register.** |
| Code today | Ed25519 signatures, SHA-256 digests; algorithm identified by key-ID prefix and field names (`ast_sha256`), not stored with `manifest_hash` |
| Gap / if unresolved | The verifier cannot claim profile conformance; digests lack stored algorithm identifiers |
| Options | (1) Author ZA-KEY-001 (purpose, algorithm, key owner, rotation per SEC-REQ-049). (2) Name an existing security baseline as the profile authority |
| Recommendation | Option 1 or 2 is required before closing K3. No algorithm recommendation: that is the Security Architect's |
| Owner / approvers | CTO; Security Architect; ZA-DOC-001 register owner (new controlled document) |
| Depends on | — ; K1, K2 and R1 depend on it |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — Document ID: ______ |

### K4: Where verification runs: catalogue activation and/or binding evaluation

| Field | Content |
|---|---|
| Tier / Effect | T0 / SCOPE (changes the evaluation path) |
| Spec mandate | **Both are mandated.** DAT §05: "Cells verify before activation"; catalogue activation: "validate signatures/hashes/FKs/compatibility, then atomically activate"; DAT-ADR-07 (ACCEPTED*): "Staging + full verification + atomic activation". APP-REQ-006 ("signature-valid") and ZA-APP-001 §13: "signature/release ID checked before input freeze." |
| Code today | No activation stage. Evaluation runs A9a integrity checks (no signature check); on-demand verification exists |
| Gap / if unresolved | APP-REQ-006 not met at evaluation; no activation gate |
| Options | The question is not *whether* but how. (1) Verify at activation and re-verify at binding evaluation, with cached per-release results invalidated on key revocation. (2) Activation only, with binding evaluation trusting the activated state |
| Recommendation | **Option 1** matches both mandates literally. Option 2 needs an explicit owner ruling that activation satisfies APP-REQ-006 |
| Owner / approvers | Data Architect; CTO; APP owner |
| Depends on | K1–K3 (nothing to verify against before them); the A10 role model |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### K5: Releases already signed with the cell key

| Field | Content |
|---|---|
| Tier / Effect | T0 / SCOPE |
| Spec mandate | RUL-REQ-026: "Every correction of a released rule MUST preserve the prior RuleVersion and record-time history." DAT-REQ-012 (immutable). SEC §14 historical verifiability. |
| Code today | All releases (4 seeded, plus any local) are cell-key-signed; they verify as UNVERIFIABLE with stated reason |
| Gap / if unresolved | After K1 they would stay permanently unverifiable, or be wrongly reported |
| Options | (1) Re-release each under the content key as a correction release (new IDs), keeping the originals immutable and marked legacy. (2) Record a signed legacy-acceptance disposition per release. (3) Both |
| Recommendation | None; this is a risk-acceptance decision |
| Owner / approvers | Head of Assurance; CTO |
| Depends on | K1, K2 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### K6: KMS/HSM provider

| Field | Content |
|---|---|
| Tier / Effect | T0 / **BLOCKING** for production signing |
| Spec mandate | RUL-ADR-06 "Managed signing key under regional security architecture"; THR-012 "Dedicated signing service/key; separation; HSM/KMS". OPEN-01 (cloud provider and first cell region, including KMS requirements; CTO + Security + Privacy/GC). G-06 is the internal audit item for the KMS/HSM provider and key-purpose catalogue ([backend-architecture-audit.md](backend-architecture-audit.md)). Correction 2026-11-04: an earlier version cited SEC-ADR-03, which is the secrets broker, not signing-key custody |
| Code today | PEM file on disk (`backend/var/keys`) |
| Options | Provider choice (cloud KMS, HSM service, regional variants) |
| Recommendation | None |
| Owner / approvers | CTO; Security Architect; Privacy/GC |
| Depends on | K1 |
| Decision record | ☐ Approve ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### R1: `content_sha256` in the signed release manifest

| Field | Content |
|---|---|
| Tier / Effect | T0 (signed artefact format) / SCOPE |
| Spec mandate | RUL §02 (release packages its RequirementVersions); RUL §12 canonical JSON and "algorithm identifier stored with digest"; C-02 (ACCEPTED*) canonical JSON v1. The manifest's field-level format is **not** specified |
| Code today | New releases record, per bound requirement version: `content_profile: za-requirement-content-v1` and `content_sha256`. SHA-256 over canonical JSON of fixed requirement and provision content fields, lifecycle excluded |
| Gap / if unresolved | The field is an unratified format element of a signed artefact |
| Options | (1) Ratify as is. (2) Ratify with a different field set or algorithm identifier (per K3). (3) Remove from future releases; existing ones keep it, immutably |
| Recommendation | **Option 1 or 2.** Without a content digest, a post-release edit of requirement text is undetectable until database immutability exists. The final algorithm identifier should follow K3 |
| Owner / approvers | RUL owner (Regulatory Content); Security Architect (algorithm) |
| Depends on | K3; B1-c (same manifest version) |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

---

## C. Catalogue immutability

### C1: Scope of migration 0014

| Field | Content |
|---|---|
| Tier / Effect | T1 (database strategy) / **BLOCKING** for database-enforced immutability |
| Spec mandate | DAT-REQ-012; DAT §05 ("requirement_version… PUBLISHED immutable; exact source fingerprint FK"); DAT-REQ-009; DOM §4.1 ("SourceSnapshot: Immutable"). Migration 0004's own scope note: source records, provisions and requirement versions "need an append-only lifecycle first (Stage 3c)" (audit I-04) |
| Code today | API-only immutability for these tables; lifecycle history in the append-only audit table |
| Gap / if unresolved | Below-the-API edits of released content are prevented by nothing. They are only detected afterwards: by release verification where a release binds the content, and by traceability validation |
| Options | (1) 0014 = catalogue lifecycle events + guards for source records, requirement versions and provisions only. (2) Combine 0014 with definition revisions (B1). (3) 0014 now, B1 schema later |
| Recommendation | **Option 2, or option 3 sequenced 0014 → 0015 in one approval.** Both touch `catalog_provision` and the same guard pattern, and B1's composite link FK needs `catalog_provision` constraints. Designing them apart risks two incompatible guard sets |
| Owner / approvers | Data Architect (accountable); CTO |
| Depends on | C2; B1-a…c if option 2 |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### C2: Append-only event-table convention

| Field | Content |
|---|---|
| Tier / Effect | T1 / **BLOCKING** for 0014 |
| Spec mandate | DAT-REQ-009; ARCH §5.2 revision fields; DAT-ADR-06 (ACCEPTED*): "Row/lineage lock + exclusion constraints". The roadmap records this convention as a prerequisite that "the ZA-TIM, ZA-STA and ZA-DAT owners must agree" (B2) |
| Code today | Applied without formal agreement in 0008 (governance events), 0011 (authority grants) and 0012/0013 (connector certification): event table + projection, status-only update, append-only trigger, `record_seq`, actor, reason |
| Gap / if unresolved | 0014 either follows an unratified convention or waits |
| Options | (1) Ratify the 0008/0011/0012 convention. (2) Specify a different convention, which may then require retrofits to 0008–0013 |
| Recommendation | **Option 1.** It is implemented and tested three times, and is consistent with DAT-REQ-009 and DAT-ADR-06 |
| Owner / approvers | Data Architect; TIM owner; STA owner |
| Depends on | — |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### C3: Strength of database-enforced immutability (owner role, A10)

| Field | Content |
|---|---|
| Tier / Effect | T0 (RLS/security-definer class) / INFO now, BLOCKING for production |
| Spec mandate | ZA-ADR-001 T0 examples include "RLS/security-definer changes"; DAT-REQ-004 (runtime roles without DDL ownership); DAT-REQ-012 |
| Code today | Triggers stop UPDATE/DELETE for every role, but the table owner can disable them with DDL (A10); detection relies on signed releases and verification |
| Options | (1) Accept trigger-plus-signature detection for Phase 1. (2) Separate non-login owner and migration roles so no service credential can alter triggers (A2/A10 role-model work) |
| Recommendation | None; it is part of the A2 role-model decision |
| Owner / approvers | Data Architect; Security Architect |
| Depends on | A2 role model (roadmap) |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ — ADR ID: ______ |

### C4: Migration and upgrade approval process

| Field | Content |
|---|---|
| Tier / Effect | T1 / INFO (process ratification) |
| Spec mandate | DAT-REQ-023 (expand/contract, history preserved); DAT-REQ-024 (idempotent backfills, no fabricated record time); DAT-REQ-030 / QA-REQ-036 / SEC-REQ-036 (restore must prove semantic reconstruction); the DAT-REQ-039 migration review checklist |
| Code today | Working practice: rehearsal on a restored copy in a scratch PostgreSQL 17 cluster; verified encrypted-backup requirement noted; the dev database is upgraded only on the user's explicit instruction; `python -m app.migrate upgrade`; the API refuses to start on a schema mismatch |
| Options | (1) Ratify this process, adding named approvers per migration. (2) Define a different gate |
| Recommendation | **Option 1**, adding a named Data Architect sign-off on the DAT-REQ-039 checklist for each catalogue migration |
| Owner / approvers | Data Architect; Engineering lead; QA lead |
| Depends on | — |
| Decision record | ☐ Approve option ___ ☐ Reject ☐ Defer — Rationale: ______ — Approvers: ______ — Date: ______ |

---

## 3. Dependency map

```
DOC-CON-003 (DOM 1.1) ──► B1-b
ZA-KEY-001 ──► K3 ──► K1 ──► K6
                │      └──► K2 ──► K5
                └──► R1
B1-a ──► B1-b, B1-c, B1-d, B3, B5, B6
B3 ──► B2
B4 ──► B5
C2 ──► C1 ◄── (option 2) B1-a/b/c
A2 role model ──► C3 ──► K4 (production)
K1–K3 ──► K4
```

How the implementation layers depend on each other:

| Layer | Needs | Why |
|---|---|---|
| Definition revisions (schema) | B1-a, B1-b, B5, C2 | Revision identity, valid time, field classes, convention |
| Rule-version revision reference | B1-c, B6 | The pin and draft retargeting |
| Signed manifest format | B1-c, R1, K2, K3 | Revision binding, content digest, key/algorithm references: **one manifest version** |
| A9b trust (VALID) | K1, K2, K3, K6; K5 for legacy | Trust anchor and historical keys |
| Historical obligation reconstruction | B1-d, B1-c, manifest format | Obligation → revision → release proof (DAT-REQ-026) |
| Database-enforced immutability | C1, C2, C3 | Guards and events |

**Must be decided together, to avoid incompatible migrations.**

1. **The manifest group:** B1-c + R1 + K2 + K3.
   - They define one signed-manifest version and the new reference columns on immutable
     `catalog_content_release` / `catalog_rule_version` rows.
   - Deciding them apart means two manifest versions, and immutable rows that can never gain later-decided fields.
2. **The schema group:** B1-a + B1-b + B5 + C1 + C2.
   - The revision tables, the provision constraints and the catalogue event tables share `catalog_provision` and one
     guard convention.

## 4. Recommended approval sequence

| Step | Approve | Approvers | Unblocks |
|---|---|---|---|
| 0 | Approve ZA-ADR-001 (or the relevant entries); issue DOM 1.1 for DOC-CON-003; commission ZA-KEY-001; GLS amendment for B4 | CTO; document owners; ZA-DOC-001 register owner | Missing specification evidence |
| 1 | C2, C4 | Data Architect, TIM/STA owners, QA lead | 0014 design |
| 2 | B1-a, B7, B3, B2 | Regulatory Content owner, RUL owner, GC, IAM Lead | Revision model and authority |
| 3 | B1-b, B5, B4, B6 | TIM owner, Data Architect, GLS owner, RUL owner | Revision schema and lifecycle |
| 4 | K3, then K1, K2, R1, B1-c (together: manifest group) | CTO, Security Architect, Head of Assurance, GC, RUL owner | Manifest version and key references |
| 5 | B1-d, C1 (and C3 position) | Obligation domain owner, Data Architect | Obligation references, migration scope |
| 6 | K4, K5, K6 | Data Architect, CTO, APP owner, Head of Assurance, Security, GC | Verification placement, legacy releases, KMS |

## 5. Phased implementation sequence (after approval; nothing implemented now)

| Phase | Work | Entry criterion |
|---|---|---|
| 1 Governance approvals | ADRs recorded for every decision above (ADR-REQ-001/004/005) | Steps 0–6 complete or explicitly deferred with interim behaviour (ADR-REQ-012) |
| 2 Architecture and schema finalisation | Final DDL for revision, link, event and key-reference tables; manifest schema version | Schema and manifest groups decided |
| 3 Migration design and backfill | 0014/0015 per C1; backfill per DAT-REQ-024: derived record time marked; missing JSON links reported, never invented; legacy rule versions and releases untouched | C4 process; rehearsal plan on a restored copy |
| 4 Definition revisions | Engine and API per the versioning architecture §3; authoring guard per B2; review transition per B3 | Phase 3 rehearsed |
| 5 Rule/release integration | Drafts pin revisions (B6); publication binds revision + content digest + key reference (manifest group) | Phase 4 |
| 6 Signature trust | Content key (K1/K6), trust store and rotation (K2), profile (K3), verification at activation/evaluation (K4), legacy disposition (K5) | K decisions; KMS available |
| 7 Historical verification and compatibility | Verifier checks revision binding; dossiers read the adopted revision; legacy paths labelled DERIVED/UNVERIFIABLE | Phases 5–6 |
| 8 PostgreSQL and SQLite verification | Full suites on fresh PostgreSQL 17 and SQLite; CI `--require` gates; migration up/down; restored-copy parity | Phase 7 |
| 9 Production readiness | Semantic restore proof (DAT-REQ-030); key-rotation drill (SEC-REQ-044); release-signing alerting (SEC §14); ADR implementation and verification evidence (ADR-REQ-010) | All above |

## 6. Implementation-readiness criteria

Implementation of a phase may start only when all of these hold:

1. Every decision the phase depends on (§3) has an approved ADR with named approvers other than its author (T0) and a
   stated effective scope (ADR-REQ-009). A deferral must state its trigger and interim behaviour (ADR-REQ-012).
2. The missing specification evidence for K3, B4 and B1-b is resolved by an approved controlled-document change.
3. The schema group and the manifest group are each decided as a whole.
4. A migration rehearsal plan exists under C4, including restored-copy parity and the DAT-REQ-039 checklist.
5. No phase changes applicability evaluation or adoption semantics unless the decision explicitly authorises it (K4
   changes evaluation by mandate, and only once K1–K3 exist).
6. The canonical `assure` database is upgraded only on the user's explicit instruction, after a verified backup.

## 7. Current interim behaviour (in force until decided)

| Area | Interim behaviour |
|---|---|
| Definitions | Created and linked only while unreleased; released ones refuse changes (409) |
| Linking | Only REVIEW_REQUIRED/VERIFIED/PUBLISHED content of the definition's instrument; COMPENSATION refused in templates |
| Authoring | `content_author` on a platform principal |
| Release verification | Intact releases UNVERIFIABLE (trust not established); alterations INVALID with security signal; never VALID |
| Signed manifests | Bind requirement versions, source fingerprints, provision IDs and `content_sha256` |
| Immutability | API-enforced for sources, requirement versions, provisions and definitions; database-enforced (0004) for rule versions, releases, set versions and fact definitions |
