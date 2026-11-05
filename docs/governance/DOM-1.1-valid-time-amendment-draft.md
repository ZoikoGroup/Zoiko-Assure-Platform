# ZA-DOM-001 v1.1: valid-time amendment (DRAFT)

> **Status: DOC_DRAFT. Not submitted for review. Not approved.**
> Prepared by engineering with AI assistance on 2026-11-04.
>
> - ADR-REQ-006: AI systems must not accept, reject, supersede or approve ADRs.
> - ZA-DOC-001 DOC-REQ-003: document existence must not be treated as approval.
>
> This draft becomes a review candidate only when a human submits it under ZA-DOC-001 §09, with an exact artifact
> hash, a change summary, the dependency impact, open conflicts and the required approver list.

## 0. Draft control

| Field | Value |
|---|---|
| Target document | ZA-DOM-001 Canonical Domain Model & Regulatory Ontology, v1.0 (6 October 2026; "Proposed for engineering lock -- approval is not inferred") |
| Proposed successor | ZA-DOM-001 v1.1 (minor: correction of a material error plus a backward-compatible clarification, ZA-DOC-001 §08) |
| Change impact class | **C2 CROSS-DOMAIN** (temporal meaning; ZA-DOC-001 §06). Requires impact traversal across dependent documents and multi-owner approval; an ADR is "often required" |
| Conflict closed | **DOC-CON-003** (HIGH, BASELINE BLOCKER; ZA-DOC-001 §07). DOC-REQ-039 requires closure before the first locked G1 baseline |
| Accountable owner | CTO / Domain Architecture Lead (DOM §0) |
| Subject-matter authority for temporal semantics | ZA-TIM-001 owner: Data Architect; technical co-owner: Backend Platform Lead. DOC-CON-003: "ZA-TIM-001 controls temporal semantics" |
| Required reviewers (DOM §0) | Product Lead; General Counsel; Head of Risk & Compliance; Assurance Lead; Data Architect; Security Architect |
| Additional reviewers proposed | Regulatory Content Lead (new obligation-definition text); TIM accountable approvers CTO, CPO, Head of Assurance, GC (TIM §00) |
| Governance register decisions served | **B1-b** (definition-revision valid time); also feeds B1-c, B1-d, C1, C2 ([decision register](../regulatory-catalogue-governance-decision-register.md)) |

### 0.1 Approval status of ZA-ADR-001 and TIM-ADR-03

- **ZA-ADR-001 is not approved.**
  - Its status is "Proposed for architecture-governance lock".
  - Its own text says: "Approval of ZA-ADR-001 v1.0 ratifies the entries marked ACCEPTED* as the engineering
    baseline".
  - I found no approval record in the spec volumes or in this repository: no named approver, no artifact hash, no
    authenticated timestamp (ZA-DOC-001 §09).
- **TIM-ADR-03 ("Native `daterange`; no UTC-midnight coercion"; T0, Data Architect + CTO) is ACCEPTED\* and so not in
  force.** This draft cites it as evidence of intended direction only.

The amendment below rests on ZA-TIM-001 text (TIM-REQ-002/003, §02) and on the DOC-CON-003 resolution. It does not
rest on TIM-ADR-03. Both TIM-001 and DOC-001 are themselves "Proposed".

## 1. The conflict: exact text

| Source | Exact text | Section |
|---|---|---|
| ZA-DOM-001 v1.0 | `valid_period` — "[start, end) when the represented fact is true." Rule: "**UTC**; open end allowed." | §2.1 Identity and revision envelope |
| ZA-TIM-001 v1.0 | "One universal timestamp range would create legal-time errors. Assure therefore supports two approved temporal profiles. Every temporal object class declares one profile in ZA-DAT-001; mixed-profile comparison requires an explicit conversion rule and cannot happen implicitly." | §02 |
| ZA-TIM-001 v1.0 | LEGAL_DATE_RANGE: "PostgreSQL `daterange`; canonical half-open [from_date, through_date_exclusive)". Examples: "RequirementVersion effective dates; RuleVersion legal effect; regulatory transition periods; some obligation windows." | §02 |
| ZA-TIM-001 v1.0 | "LEGAL_DATE_RANGE is never converted to UTC midnight merely for storage convenience." / "A date-only source cannot be silently upgraded to a precise timestamp." | §02 canonical interval rules |
| ZA-TIM-001 v1.0 | TIM-REQ-003: "LEGAL_DATE_RANGE and OPERATIONAL_INSTANT_RANGE MUST remain distinct; implicit date-to-instant conversion is prohibited." | §24 |
| ZA-DOC-001 v1.0 | DOC-CON-003 required action: "Issue DOM 1.1 replacing universal UTC language with temporal-profile semantics and update schema mapping." | §07 |

### 1.1 Missing baseline text: required for B1-b but present in no controlled document

| # | Gap | Evidence |
|---|---|---|
| M1 | **ObligationDefinition is not designated bitemporal.** DOM §9.1 lists the objects that "MUST be bitemporal". The list includes ProvisionVersion, RequirementVersion, RuleVersion, ObligationInstance and ObligationOccurrence. It does **not** include ObligationDefinition, ObligationScheduleDefinition or SatisfactionExpressionTemplate. These are the objects DOM §6.1 calls "Versioned" | DOM §9.1, §6.1 |
| M2 | **ObligationDefinition has no temporal profile.** TIM §25 G1 gate: "Every domain object in ZA-DAT-001 is assigned LEGAL_DATE_RANGE, OPERATIONAL_INSTANT_RANGE, POINT_EVENT or PERIOD_ASSERTION semantics". DAT §05 defines `catalog.obligation_definition` as "obligation_definition_id, provision refs, subject type, coverage mode, rule lineage", with no valid range | TIM §25; DAT §05 |
| M3 | **The schema mapping DOC-CON-003 asks to update is not identified.** DOM §18.1 requires "Bitemporal ranges for all objects designated bitemporal" but names no profile | DOM §18.1 |
| M4 | **The same universal-UTC wording appears in ZA-ARCH-001 v3.0 §5.2, and that is not registered.** §5.2 reads "Use half-open ranges [start, end) and UTC", and its DDL uses `valid_period TSTZRANGE NOT NULL -- business/legal truth interval`. DOC-CON-003 lists only ZA-DOM-001 as affected. **This draft does not resolve M4**; see §5 | ARCH §5.2; DOC §07 |

## 2. Proposed amendment text

The text in the boxes is proposed normative text. Everything else is explanation. Where the evidence supports more
than one formulation, alternatives are given, and the approvers must choose one.

### 2.1 Replace the `valid_period` row in DOM §2.1

Current row:

> valid_period | [start, end) when the represented fact is true. | UTC; open end allowed.

Proposed replacement:

> **valid_period** | [start, end) when the represented fact is true, expressed in the temporal profile declared for the
> object class. | Half-open; open end allowed; sentinel end values prohibited. Profile per §2.4: LEGAL_DATE_RANGE uses
> calendar dates, OPERATIONAL_INSTANT_RANGE uses UTC instants. No implicit conversion between profiles, including
> UTC-midnight conversion of a legal date.
>
> **record_period** | [start, end) when Assure held this revision as current knowledge. | Always an instant range
> assigned by the authoritative write path (ZA-TIM-001 §03). Corrections close, never overwrite.

The `record_period` addition repeats TIM §00 ("Record time is always an instant") so that §2.1 does not inherit the
ambiguity being removed from `valid_period`.

### 2.2 Insert a new DOM §2.4 "Temporal profiles"

> **2.4 Temporal profiles**
>
> ZA-TIM-001 is the controlling authority for temporal semantics. Every object class that carries valid time declares
> exactly one profile from ZA-TIM-001 §02: LEGAL_DATE_RANGE, OPERATIONAL_INSTANT_RANGE, POINT_EVENT or
> PERIOD_ASSERTION. ZA-DAT-001 records the declaration. Where this document and ZA-TIM-001 differ on the meaning or
> representation of time, ZA-TIM-001 controls.
>
> | Object class (this document) | Valid-time profile |
> |---|---|
> | RequirementVersion, ProvisionVersion | LEGAL_DATE_RANGE |
> | RuleVersion (legal effect) | LEGAL_DATE_RANGE |
> | ObligationDefinition revision | *see §2.3, Alternative A or B* |
> | ObligationOccurrence required window | LEGAL_DATE_RANGE where the source states a date-only deadline; otherwise OPERATIONAL_INSTANT_RANGE (ZA-WRK-001 §07) |
> | FactAssertion | As declared by its FactDefinition (ZA-DAT-001 `valid_date_range` xor `valid_instant_range`) |
> | ControlImplementation state, access grant, connector certification | OPERATIONAL_INSTANT_RANGE |
> | TestRun, Attestation, evidence capture | POINT_EVENT |
> | AssuranceSnapshot, evidence coverage | PERIOD_ASSERTION |

Every row is taken from the TIM §02 examples, DAT §26/27 DDL or WRK §07, apart from two:
- **ObligationDefinition revision** is the open B1-b decision.
- **ObligationOccurrence** maps WRK §07 ("legal date-only deadlines remain date semantics until operational scheduling
  converts under policy").

The table is deliberately not exhaustive. TIM §25 requires the full assignment in ZA-DAT-001, and DAT is not amended
here.

### 2.3 Obligation-definition revisions: amend DOM §6.1 and §9.1

The evidence points two ways:

| Points toward valid time on the revision | Points toward record time only |
|---|---|
| DOM §6.1: "Versioned reusable obligation semantics" | DOM §9.1 omits ObligationDefinition from the mandatory bitemporal list |
| ARCH §5.2: "Every assurance object carries two clocks" | ARCH §5.1: ObligationTemplate is "versioned by content release" |
| DOM §1.1: each revision "is immutable once closed in record time" | Legal effect is already carried by RuleVersion (LEGAL_DATE_RANGE, TIM §02) |
| TIM §07: transitional regimes are modelled with "obligation definitions with overlapping legal validity by design" | |

That TIM §07 sentence gives obligation definitions their own legal validity, which supports Alternative A.

**Alternative A: definition revisions carry legal valid time.** Engineering recommends this alternative; it is not
approved.

> Add to §9.1 list: "ObligationDefinition revisions (LEGAL_DATE_RANGE)".
>
> Amend the ObligationDefinition row of §6.1: "Versioned reusable obligation semantics derived from one or more
> provision versions. A stable ObligationDefinition identity has one or more immutable revisions. Each revision carries
> a LEGAL_DATE_RANGE valid_period for the legal period its semantics describe, and a record_period. A revision becomes
> binding only when a published RuleVersion pins it. | Regulatory Content owns."
>
> Add to §15 as DOM-INV-16: "A RuleVersion's valid_period MUST lie within the valid_period of the
> ObligationDefinition revision it pins."

**Alternative B: definition revisions carry record time only.**

> Amend the ObligationDefinition row of §6.1: "… Each revision is immutable and carries a record_period. Legal valid
> time is carried by the RuleVersion(s) that pin the revision; an ObligationDefinition revision has no independent
> valid_period."
>
> Add to §9.1 (after the list): "ObligationDefinition, ObligationScheduleDefinition and SatisfactionExpressionTemplate
> revisions are record-time versioned only; their legal effect is the valid time of the pinning RuleVersion."

**Trade-off:**
- **Alternative A** adds a containment invariant, DOM-INV-16, which engineering proposes. No controlled text states
  it. The invariant gives TIM §07 overlapping-validity transition regimes an explicit home.
- **Alternative B** is simpler, but it needs a separate RuleVersion, and therefore a separate legal-basis record, for
  every transition branch.

**The choice is the TIM owner's (B1-b).** The Regulatory Content owner must agree, because DOM §6.1 says "Regulatory
Content owns".

### 2.4 Amend DOM §18.1 (the schema mapping DOC-CON-003 asks for)

Replace:

> Bitemporal ranges for all objects designated bitemporal in this document, with non-overlap/current-revision
> constraints appropriate to the object.

with:

> Bitemporal ranges for all objects designated bitemporal in this document. The valid-time column uses the declared
> temporal profile: `daterange` for LEGAL_DATE_RANGE, `tstzrange` in UTC for OPERATIONAL_INSTANT_RANGE, both with
> canonical `[)` bounds (ZA-DAT-001 §03). Record time uses the cell record watermark (ZA-TIM-001 §03).
> Non-overlap/current-revision constraints are appropriate to the object and profile.

### 2.5 Amend DOM §9.3 canonical query forms

Add a note under the table:

> A valid-time point in a query is a TemporalPoint of the queried object's profile (ZA-TIM-001 §02): a DATE for
> LEGAL_DATE_RANGE objects, an INSTANT for OPERATIONAL_INSTANT_RANGE objects. A query that compares objects of
> different profiles states its conversion rule explicitly.

## 3. Out of scope: other changes DOM 1.1 must also carry

ZA-DOC-001 §08's supersession example names the DOM 1.1 reason as "DOC-CON-001 + DOC-CON-003 normalization".
DOC-CON-002 also lists ZA-DOM-001 as an affected artifact. **This draft does not provide text for these two
conflicts.** Issuing DOM 1.1 without them would need a further DOM revision before G1 (DOC-REQ-039).

| Conflict | DOM passages found (v1.0) |
|---|---|
| DOC-CON-001 (`UNDETERMINED` → `UNCERTAIN`) | §5.3 three-valued result table; §7.2 reserved vocabularies; DOM-INV-03 |
| DOC-CON-002 (`PROVEN_WITH_EXCEPTION`) | §7.2 "Period assurance PROVEN / PROVEN_WITH_EXCEPTION / NOT_PROVEN / UNDETERMINED" |

## 4. Impact: APIs, database models and migrations

| Area | Current implementation | Effect of this amendment |
|---|---|---|
| Requirement versions, rule versions | `Date` `valid_from`/`valid_to`, half-open (`valid_to > effective`; creation refuses `valid_to <= valid_from`) | **Already conforms** to LEGAL_DATE_RANGE. Physical columns are a date pair rather than `daterange`. TIM-ADR-03 (ACCEPTED\*, not in force) would require native `daterange`; the change waits for that ADR |
| Obligation definitions | One mutable row; no valid time; no revision | **Alternative A:** the proposed `catalog_obligation_definition_revision` table gains a legal-date range plus a containment check at rule publication. **Alternative B:** record sequence only |
| Obligation occurrences | `window_from`/`due_at` as `timestamptz` instants | Date-only legal deadlines must not be stored as UTC midnight (TIM-REQ-003). This is a **separate, later change**, coupled to the GLS schedule vocabulary ([GLS draft](GLS-001-schedule-vocabulary-amendment-draft.md)) |
| Public API | Dates serialised as ISO date strings | Historical endpoints should adopt the TIM TemporalPoint/TemporalRange union; that is an API-001 alignment item, not a DOM change |
| Migrations | Head 0013; 0014 reserved for catalogue lifecycle events | **No migration is proposed or created here.** Definition revisions belong to the definition-revision migration ("0015 or later") described in [obligation-definition-versioning-architecture.md](../obligation-definition-versioning-architecture.md) |

## 5. Items routed to other owners (not resolved here)

| Item | Owner | Action requested |
|---|---|---|
| M4: ARCH §5.2 universal "UTC" and `TSTZRANGE` for legal truth | ZA-DOC-001 owner (CTO / Engineering Governance / QA); ARCH owner | Record a ConflictRecord (DOC-REQ-008), either a new DOC-CON-004 or a widened DOC-CON-003. ARCH 3.1 is already expected for DOC-CON-001 (DOC §28) and could carry the fix |
| M2: DAT temporal-profile assignment for `catalog.obligation_definition` | Data Architect (DAT owner) | DAT revision after B1-b |
| TIM-ADR-03 and ZA-ADR-001 approval | CTO / Architecture Governance | Approve or reject the register (or the entry). Until then the `daterange` requirement is intent only |

## 6. Review and approval record

Fields follow ZA-DOC-001 §09: named approver, role or authority, exact artifact hash, authenticated timestamp, decision
and conditions. **All fields are blank.** The artifact hash is recorded when the draft is frozen for review. Editing
this file afterwards invalidates the hash (DOC-REQ-006).

| Role | Name | Decision (Approve / Reject / Request change) | Alternative chosen (§2.3) | Artifact hash | Date (authenticated) | Conditions / reasons |
|---|---|---|---|---|---|---|
| Accountable owner: CTO / Domain Architecture Lead | | | | | | |
| TIM owner: Data Architect | | | | | | |
| Product Lead | | | | | | |
| General Counsel | | | | | | |
| Head of Risk & Compliance | | | | | | |
| Assurance Lead | | | | | | |
| Security Architect | | | | | | |
| Regulatory Content Lead (proposed) | | | | | | |

Rejections or change requests: record the exact hash and the blocking comments; the next draft is a new review
revision (ZA-DOC-001 §09).
