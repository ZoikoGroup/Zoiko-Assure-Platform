# ZA-GLS-001 v1.1: obligation schedule-kind vocabulary (DRAFT)

> **Status: DOC_DRAFT. Not submitted for review. Not approved.**
>
> Prepared by engineering with AI assistance on 2026-11-04. Three of the five codes have controlled-specification
> support for their meaning. **Two (`ONE_TIME`, `WINDOWED`) have only a single word of support** ("one-time … or
> windowed", DOM §6.1). Their definitions are marked **OWNER CONTENT REQUIRED**. They cannot be approved as written
> until the Regulatory Content owner supplies or confirms the semantics.

## 0. Draft control

| Field | Value |
|---|---|
| Target document | ZA-GLS-001 Controlled Terminology & Status Dictionary, v1.0 ("Proposed for semantic lock") |
| Proposed successor | ZA-GLS-001 v1.1: new codes, a backward-compatible addition, minor version (ZA-DOC-001 §08). Historical records keep `dictionary_version` (GLS-REQ-010) |
| GLS owner | Chief Product Officer / CTO (joint semantic authority) |
| Semantic-area owner (GLS §12) | "Regulatory content terms: Head of Regulatory Content + General Counsel" |
| Required reviewers (GLS §00) | Head of Risk & Compliance; General Counsel; Head of Assurance; Data Architect; API Lead; QA Lead; Design Lead |
| Additional reviewers proposed | STA owner (occurrence assurance, STA §13); WRK owner (deadline and calendar, WRK §07); TIM owner, Data Architect (temporal profile of windows) |
| Governance route | GLS §12 has no row for a new *classification* code. The nearest rows: "Add new state code: ADR; domain owner; Data/API/Event/QA impact review; migration and client compatibility plan"; "Add new reason code: Domain owner + QA". **Proposed: the state-code route (ADR)**, because the schedule kind decides occurrence generation, deadlines and assurance treatment. GLS §12: "Any change that alters the meaning, transition logic or downstream treatment of a controlled code is a breaking domain change". The owner confirms the route |
| Change impact class | C2 CROSS-DOMAIN (a controlled code with event, API and data effects; ZA-DOC-001 §06) |
| Decisions served | **B4** (closes); informs **B5** (schedule is a semantic field) and **B1-b** (window profile). See the [decision register](../regulatory-catalogue-governance-decision-register.md) |

## 1. Controlled-specification evidence

| Source | Exact text |
|---|---|
| DOM §6.1 | ObligationScheduleDefinition: "Structured timing: one-time, recurring, event-triggered, continuous or windowed." Authority: "Regulatory Content owns; validated by rule/content tests." |
| DOM §4.2 | Timing examples: "before use, within N days, continuous, annual". Rule: "Encoded as structured deadline/schedule rule, not text only." Trigger/condition: "Can reference applicability facts or occurrence events." |
| DOM §6.2 | "A recurring obligation is not adequately represented by a single due_date field. Annual, quarterly, continuous and event-triggered duties need separate occurrences …" |
| DOM §13 | "ObligationInstance → ObligationOccurrence: 1 : N for recurring/event-triggered duties; 0..1 for continuous/standing duties as defined." |
| DOM-INV-14 | "Every recurring/event-triggered obligation uses ObligationOccurrence rather than overwriting one due date." |
| DOM §6.1 | ObligationOccurrence: "One due/assessment instance generated from schedule/trigger. Deterministic scheduler; occurrence identity survives due-date correction via revision." |
| STA §13 | Occurrence inputs: `REQUIRED_WINDOW` ("Valid/due window in which the required action must occur"), `FULFILLMENT_EVENT`, `TIMELINESS_RULE`, `EVIDENCE_RULE`, `WAIVER / EXTENSION` ("Governed valid-time object … never an ad hoc due-date edit"). Also: "explicit occurrence aggregation rule defined by the ObligationDefinition. Missed occurrence history is never overwritten by a later successful occurrence." |
| TIM §07 | "Deadline tied to event rather than fixed date: ObligationOccurrence derives event-relative due range; source/event revisions remain traceable." "Grace period after formal effective date: Represent as explicit legal condition/obligation window, not by shifting source effective date." |
| TIM §02 | LEGAL_DATE_RANGE examples include "some obligation windows"; "LEGAL_DATE_RANGE is never converted to UTC midnight merely for storage convenience." |
| WRK §07 | Deadline inputs: `deadline_rule_version`, base event/date, `calendar_id/version`, `time_zone` ("Explicit when instant deadline matters; legal date-only deadlines remain date semantics until operational scheduling converts under policy"), adjustment convention (`NEXT_BUSINESS_DAY`, `PREVIOUS_BUSINESS_DAY`, `NONE` or controlled rule), calculated due, recalc reason |
| WRK-REQ-023/024/025 | Versioned calendar and deadline rules with provenance; a correction creates new calculation history; an already-overdue correction invokes governed impact, never a fabricated timer |
| GLS §10 | "Machine codes are uppercase ASCII SNAKE_CASE and immutable after release." "New codes require a default-safe behavior in every consumer before publication." |
| DAT-REQ-025 | Codes "MUST follow ZA-GLS-001" |

**Evidence defects found (routed, not resolved):**
- **E1. DOM is inconsistent about continuous duties.**
  - DOM §6.2 says continuous duties "need separate occurrences".
  - DOM §13 gives continuous duties "0..1" occurrences.
  - STA §13 treats occurrences as dated actions, in contrast to "continuously satisfiable controls".
- **E2. DOM §13 gives no occurrence cardinality for one-time or windowed duties.**
- Both belong in DOM 1.1 (see [DOM 1.1 draft](DOM-1.1-valid-time-amendment-draft.md) §5). The DOM owner must resolve
  them.

## 2. Proposed amendment text: new GLS §04 code family "Obligation schedule kind"

Insert after "Coverage mode" in GLS §04. The boxed text is the proposed normative text.

> **Obligation schedule kind** (`schedule_kind`): the structural timing class of an ObligationScheduleDefinition
> (ZA-DOM-001 §6.1). Exactly one per ObligationDefinition revision.
>
> | Code | Meaning | Product behavior |
> |---|---|---|
> | `CONTINUOUS` | The duty requires a state to be maintained throughout the obligation's valid time, e.g. "maintain" (DOM §4.2 "continuous"). | Assurance is evaluated as period assurance over every temporal segment (ZA-TIM-001 §09; ZA-STA-001). No recurring due date; occurrences 0..1 per ZA-DOM-001 §13. |
> | `RECURRING` | The duty requires a dated action repeated on a calendar cadence, e.g. annual or quarterly (DOM §4.2, §6.2). | One ObligationOccurrence per required instance (DOM §13 1:N; DOM-INV-14), each with its own REQUIRED_WINDOW, evidence and timeliness (ZA-STA-001 §13). A missed occurrence is never overwritten by a later one. |
> | `EVENT_TRIGGERED` | The duty requires an action within a deadline that starts at a qualifying event, e.g. "within N days" of a material incident (DOM §4.2). | One ObligationOccurrence per qualifying event, with an event-relative due range derived from the event and its revision (ZA-TIM-001 §07; ZA-WRK-001 §07 base event). Event revisions stay traceable. |
> | `ONE_TIME` | **OWNER CONTENT REQUIRED.** Candidate: the duty requires one action, once, with no recurrence and no triggering event. | **OWNER CONTENT REQUIRED.** Candidate: exactly one occurrence. Its window is fixed by a legal date or a stated condition. |
> | `WINDOWED` | **OWNER CONTENT REQUIRED.** Candidate: the duty applies only within an explicit legal window distinct from the provision's own effective period, e.g. a transition or grace period (ZA-TIM-001 §07). | **OWNER CONTENT REQUIRED.** Candidate: occurrences or period assurance limited to the window; the window is a LEGAL_DATE_RANGE where the source states dates (ZA-TIM-001 §02). |
>
> Consumers MUST treat an unknown future `schedule_kind` as unschedulable: no occurrence is generated and no deadline is
> inferred. It is surfaced for review rather than coerced to a known code (GLS-REQ-015; GLS §10 default-safe behavior).

### 2.1 Questions the owner must answer for `ONE_TIME` and `WINDOWED`

These are not answered by any controlled text. Engineering must not choose the answers.

| # | Question | Why it matters |
|---|---|---|
| Q1 | Does `ONE_TIME` include "before use" duties (DOM §4.2) that fire when an activity starts? If so, how does it differ from `EVENT_TRIGGERED`? | Same occurrence model, different trigger source (fact versus event) |
| Q2 | Can a `WINDOWED` duty also recur within its window, or be event-triggered within it? Is `WINDOWED` a schedule kind or a constraint that combines with the other kinds? | A single-valued code cannot express a combination. If windows combine, `WINDOWED` may belong in a separate field (`applicability_window`) rather than in this family |
| Q3 | What is the occurrence cardinality for `ONE_TIME` and `WINDOWED` (E2)? | DOM §13 must state it |
| Q4 | Is a grace period (TIM §07) a `WINDOWED` duty, or a `WAIVER / EXTENSION` object (STA §13) on a normal occurrence? | Two spec passages could both claim it |

## 3. Required fields and validation (proposed)

The schedule rule is structured, not text (DOM §4.2). Field groups:
- **REQUIRED_WINDOW**, **TIMELINESS_RULE**, **EVIDENCE_RULE**: STA §13.
- **deadline rule**, **base event/date**, **calendar**, **time zone**, **adjustment convention**: WRK §07.
- **occurrence aggregation rule**: STA §13.

The representation of each group is a **SELECTION REQUIRED** item (§3.2).

### 3.1 Field requirements by kind (proposed)

| Field group | `CONTINUOUS` | `RECURRING` | `EVENT_TRIGGERED` | `ONE_TIME` | `WINDOWED` |
|---|---|---|---|---|---|
| Cadence (period unit, count, anchor) | Prohibited | **Required** | Prohibited | Prohibited | Q2 |
| Trigger event type reference | Prohibited | Prohibited | **Required** | Q1 | Q2 |
| Relative deadline (amount, unit) | Prohibited | Optional (offset from period anchor) | **Required** | Q1 | Q2 |
| Fixed legal due date or range | Prohibited | Prohibited | Prohibited | Q1 | **Required** (window) |
| Calendar ID/version + adjustment convention | Prohibited | Required when the deadline counts business days or adjusts | Same | Same | Same |
| Time zone | Prohibited | Required only for an instant deadline (WRK §07) | Same | Same | Same |
| Occurrence aggregation rule | n/a | Required when a reporting period holds several occurrences (STA §13) | Same | n/a | Q2 |
| Evidence rule, timeliness rule | Via the satisfaction template | **Required** | **Required** | **Required** | Q2 |

**Validation rules:**
- **V1:** `schedule_kind` must be a code from the approved dictionary version.
- **V2:** required groups must be present; prohibited groups must be absent.
- **V3:** a business-day deadline without a calendar reference is rejected. "Do not guess a legal deadline" (WRK §07).
- **V4:** a date-only legal deadline must not carry an instant or a time zone unless the source states one. "A
  date-only source cannot be silently upgraded" (TIM §02).
- **V5:** an unknown code on read is tolerated and surfaced (GLS-REQ-015). On write it is rejected.

### 3.2 Representation selections

| # | Selection | Options | Approvers | Decision |
|---|---|---|---|---|
| S1 | Cadence representation for `RECURRING` | (a) Calendar unit {MONTH, YEAR, …} × count + anchor date or anchor rule. (b) An iCalendar RRULE subset (RFC 5545). (c) Day-count interval: today's `recurrence_days`. **(c) cannot express "quarterly" or "annual" as calendar periods** (DOM §6.2) | Regulatory Content owner; Data Architect | ☐ ___ |
| S2 | Trigger event type vocabulary for `EVENT_TRIGGERED` | Reference to ZA-EVT-001 event types; reference to FactDefinitions (DOM §4.2 "applicability facts or occurrence events"); or a new GLS family | Regulatory Content owner; EVT owner; GLS owner | ☐ ___ |
| S3 | Calendar and deadline service dependency | WRK-ADR-06 (ACCEPTED\*): "Assure-owned versioned calendar/deadline service backed by controlled data". **Not built.** Until it exists, business-day deadlines cannot be validated (V3) | WRK owner; CTO | ☐ ___ |

## 4. Temporal interpretation rules (all from controlled text)

| Rule | Source |
|---|---|
| Windows are half-open `[from, until_exclusive)`; open end allowed; no sentinel dates | TIM-REQ-002; TIM §02 |
| A window stated in legal dates is LEGAL_DATE_RANGE and is never converted to UTC midnight | TIM §02; TIM-REQ-003 |
| A time zone is explicit only when an instant deadline matters; date-only deadlines keep date semantics until operational scheduling converts them under policy | WRK §07 |
| Deadlines use a versioned calendar and deadline rule, and record their calculation provenance | WRK-REQ-023 |
| A correction creates a new calculation and history and reschedules future timers; it never rewrites prior calculations | WRK-REQ-024; DOM §6.1 (occurrence identity survives via revision) |
| A correction that makes a deadline already overdue starts governed impact and escalation; it never fabricates a historical timer | WRK-REQ-025 |
| Waivers and extensions are governed valid-time objects, never due-date edits | STA §13 |
| An event-relative due range derives from the exact event or source revision, which stays traceable | TIM §07 |

## 5. Impact: APIs, database models and migrations

| Area | Current implementation | Effect once approved (nothing implemented by this draft) |
|---|---|---|
| Stored values | `catalog_obligation_definition.schedule_kind` is `varchar(32)` with values `CONTINUOUS`, `RECURRING`, `EVENT_TRIGGERED` (0001 baseline). `recurrence_days` is an integer | The three codes become dictionary-controlled. Their spellings match GLS §10 rules and they were never in an approved dictionary, so ratifying them is not a meaning change. **Seeded `RECURRING` rows use a day-count**; under S1 (a) or (b) they need a structured cadence in a new definition revision, not an in-place edit |
| API | `POST /catalog/obligation-definitions` validates the kind against `SCHEDULE_KINDS` (three values) and requires `recurrence_days` iff `RECURRING` | Validation follows §3.1. The two new codes are accepted only after Q1–Q4 are answered. Responses carry `dictionary_version` (GLS §10 event and API fields) |
| Occurrences | `assurance_obligation_occurrence.window_from/due_at` are `timestamptz`. `governance.schedule_occurrence` is called with explicit instants; **no scheduler derives occurrences from `schedule_kind`** | A scheduler is new work: it depends on S1–S3 and the WRK calendar service. Date-only windows need a LEGAL_DATE_RANGE representation (TIM-REQ-003); see the DOM 1.1 draft §4 |
| Frontend | Shows the raw `schedule_kind` code (obligations, sources pages) | Labels resolved from dictionary version and locale (GLS §10); unknown codes shown as unknown |
| Events | None carry `schedule_kind` today | `obligation.occurrence_created` and related events must carry `dictionary_version` (GLS §10) |
| Migrations | Head 0013 | **No migration for the codes themselves** (an application-validated text column). Structured schedule fields are semantic definition fields (decision B5), so they go into the definition-revision migration ("0015 or later"), not 0014. **None is created here** |

## 6. Review and approval record

The record follows ZA-DOC-001 §09. All fields are blank.

| Role | Name | Decision (Approve / Reject / Request change) | Q1–Q4 answers / S1–S3 selections | Artifact hash | Date (authenticated) | Conditions / reasons |
|---|---|---|---|---|---|---|
| GLS owner: CPO | | | | | | |
| GLS owner: CTO | | | | | | |
| Head of Regulatory Content | | | | | | |
| General Counsel | | | | | | |
| Head of Assurance | | | | | | |
| Data Architect | | | | | | |
| API Lead | | | | | | |
| QA Lead | | | | | | |
| STA owner / WRK owner (proposed) | | | | | | |
