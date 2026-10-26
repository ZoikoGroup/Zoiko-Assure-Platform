# Zoiko Assure — 5 Week Frontend & Backend Build Plan

> **SUPERSEDED FOR EXECUTION:**
> Frontend work is tracked in [`frontend/frontend-build-plan.md`](../frontend/frontend-build-plan.md).
> Backend work is tracked in [`backend/backend-build-plan.md`](../backend/backend-build-plan.md).
> This combined plan is kept as historical/reference material only. Its governance IDs (G-1…G-11) differ from the backend plan's G-1…G-8 table.

> **Status:** plan only. Nothing in this document is implemented by writing it. Checkboxes are open until the work and its tests land.
> **Baseline:** repository commit `06d4244` plus local environment setup and P0-8A (repeatable PostgreSQL bootstrap) as of 14 Oct 2026.
> **Authority:** the 22 controlled ZA-* specifications plus ZA-ENG-BRIEF-001. All 22 are "Proposed for lock" (ZA-DOC-001). Controlled specs override this plan, the README and ARCHITECTURE.md. Conflicts are raised, never resolved silently in code (ZA-DOC-001 §01, ZA-ADR-001 §04).
> **Local environment:** frontend `http://localhost:5173` → backend `http://localhost:8000` → PostgreSQL 17.11 `127.0.0.1:5433` (`assure` for development and the only runtime database, `assure_test` for tests only).

**How to read this plan**

- Spec references are cited where the ID was verified against the controlled text. Where no ID is confirmed, only the spec name is given.
- `BLOCKED BY GOVERNANCE DECISION` marks an item that depends on an unresolved document conflict or open ADR item. Build what is unblocked around it; do not pick a side in code.
- Backend capability actions use the audit vocabulary: **KEEP / MODIFY / REPLACE / DISABLE / REMOVE / BUILD**.
- Sizing assumption: a small team (≈2 backend, 1–2 frontend, part-time QA/security). Scope that does not fit is listed under *Deferred*, not squeezed in.

---

## Ground rules (apply every week)

- **Build the chain, not a dashboard** (ENG-BRIEF): Authoritative Source → Atomic Requirement → Applicability Evaluation → Customer Adoption / Attestation → Obligation → Control Expression → Evidence + IPE → Assurance Snapshot → Verifiable Assurance Package. Every screen shows where it sits in this chain.
- **Controlled terminology only** (ZA-GLS-001, API-REQ-050):

  | Dimension | Codes |
  |---|---|
  | Applicability | `APPLIES` / `DOES_NOT_APPLY` / `UNCERTAIN` |
  | Obligation assurance | `PROVEN` / `NOT_PROVEN` / `FAILING` / `REQUIRES_REVIEW` |
  | Control | `OPERATING` / `DEGRADED` / `FAILING` / `NOT_EVALUATED` / `REQUIRES_REVIEW` |
  | Evidence, test, IPE | Separate dimensions per GLS / IPE-001 |

  Never use `UNDETERMINED`, `PROVEN_WITH_EXCEPTION`, `COMPLIANT`, `NON_COMPLIANT`, or a green/red compliance score.
- **AI may propose, never bind.** No AI actor adopts, approves or attests (INV-01, APP-REQ-009, STA-REQ-029). No model call sits on an authorization or enforcement path (INV-02, SEC-REQ-003).
- **The frontend never computes legal or assurance truth.** It renders server read models that reference snapshot IDs and watermarks (STA-REQ-028, DAT-REQ-019).
- **History is never overwritten.** Corrections create new revisions (TIM-REQ-007). Exceptions never change underlying state (STA-REQ-014, ADR-001 C-14).
- **Every material write** needs explicit authorization, an audit record and a catalogued event in the same transaction (IAM-REQ-007, IAM-REQ-037, EVT-REQ-001).
- **Reuse correct engines.** The audit marked these KEEP: dictionary, typed rule language and evaluator, bitemporal facts, applicability core, roll-up math, evidence ledger, Merkle and offline verifier, IPE derivation, rule-publication chain, AI-governance core. Do not rewrite them.

## Release-1 target and non-goals

**Target of these 5 weeks:**
- Meet the **P0 Foundation** exit: golden vectors pass, no AI on binding paths, core invariants automated.
- Make the **R1 vertical slice demonstrable**: the slice runs end to end without DB intervention, and an external auditor can validate a sample package offline (ZA-PRD-001 §6).

This is **not** R1 Controlled Production. That gate also needs one full Customer Zero quarter (ADR-001 C-12), content operations readiness (PRD §15), the open infrastructure decisions below, and QA release evidence (ZA-QA-001).

**Explicit non-goals** (PRD §3.2 and product position):
- [ ] No full GRC replacement. Assure consumes control and evidence state from Vanta, Drata, ServiceNow GRC, Archer, cloud posture tools and similar. It does not rebuild them.
- [ ] No universal or "190-country" legal coverage. Content not encoded at provision level is labelled `MONITORING_ONLY`.
- [ ] No legal-advice chatbot. No autonomous legal determinations.
- [ ] No inline agent proxy. Assure provides a policy decision endpoint and adapter only.
- [ ] No sanctions/PEP data network, no general no-code workflow builder, no blockchain evidence store.

---

## Governance decisions and blockers

| ID | Decision needed | Affects | Owner (per spec) | Label |
|---|---|---|---|---|
| G-1 | Successor ADR for ADR-001 C-01. C-01 makes Go the authoritative runtime, and the code is Python, which is memory-safe. | All authoritative backend paths | CTO + Head of Assurance (T0) | BLOCKED BY GOVERNANCE DECISION. This plan continues on the existing Python engines **pending** the ADR. If rejected, Weeks 2–5 backend items must be re-planned as a port that uses the Python engines as conformance oracles. |
| G-2 | DOC-CON-003: legal-date `daterange` vs DOM's UTC `valid_period` | Fact, rule, obligation and assurance period types | TIM/DOM owners (DOC-001 §07) | BLOCKED BY GOVERNANCE DECISION. The stated direction (ENG-BRIEF, DOC-001 §07) is the TIM profiles; DOM 1.1 is pending. |
| G-3 | DOC-CON-001/002 doc revisions: UNCERTAIN, and STA states instead of PROVEN_WITH_EXCEPTION | PRD J-04 wording, ARCH/DOM/TRC text | Doc owners | Code already follows GLS/STA as ENG-BRIEF directs. Not blocking code. Never implement PRD J-04's `PROVEN_WITH_EXCEPTION`. |
| G-4 | Evidence provenance vocabulary (ARCH P0–P4, DOM P1_DIRECT_AUTHORITY…, APP fact classes, EVD `origin_class`) | Evidence provenance codes and display | GLS/EVD owners | BLOCKED BY GOVERNANCE DECISION. Introduce no new provenance codes. Keep the existing EVD `origin_class` and current classes until resolved. |
| G-5 | Actor taxonomy (DOM HUMAN/SERVICE/AGENT/SIGNED_CONTENT/EXTERNAL_AUDITOR vs IAM HUMAN/WORKLOAD/AGENT/EXTERNAL_PARTY) | Principal-class schema | DOM/IAM owners | BLOCKED BY GOVERNANCE DECISION for schema renames. Keep the existing actor types. |
| G-6 | `OCCURRENCES` node in the satisfaction expression (code extension; not in the STA v1 grammar) | Occurrence-based obligations | STA owner (STA-REQ-032) | BLOCKED BY GOVERNANCE DECISION for new use. Keep existing behaviour; ratify via ADR plus golden vectors, or remodel. |
| G-7 | Evidence `MISSING`/`CONFLICTING`: GLS evidence states vs EVD requirement-level conditions (EVD-REQ-033) | Evidence condition display | GLS/EVD owners | BLOCKED BY GOVERNANCE DECISION for any new code. |
| G-8 | ADR OPEN-03: qualified RFC 3161 TSA provider | Package timestamps | Security + GC + CTO | BLOCKED BY GOVERNANCE DECISION. Report `TSA_PENDING` honestly (EVD-REQ-008). |
| G-9 | ADR OPEN-05 managed queue; OPEN-06 managed Temporal; OPEN-01 cloud/first cell; OPEN-04 model provider | Event delivery, production workflows, cells, any model use | Platform Lead / CTO / Security / Privacy | BLOCKED BY GOVERNANCE DECISION for production use. Local development may use local equivalents. No model calls at all. |
| G-10 | Coverage labels (PRD COVERED… vs GLS ENCODED…) and content lifecycle ("RELEASED" in ARCH INV-09 vs GLS `PUBLISHED`) | Catalogue codes | GLS owner | Not blocking: GLS governs codes (DOC-001 §01). Register ConflictRecords. |
| G-11 | Visual design system. PRD §13 says visual design is owned by UI/UX specifications, and none exists in the 22-doc register. | Theme, light/dark mode, visual polish | Product | No dark mode is planned, because no wireframe specifies it. Use the WF contracts and the existing brand tokens. Product decision needed for anything further. |

---

## Frontend route / screen map

Wireframes are the controlled state-and-action contracts in **PRD §13 (WF-01…WF-08)**, **DOM §14 (WF-DOM-01…07)** and **GLS §11** (UI and audit-language rules). Existing routes are reused; new ones are marked PROPOSED ROUTE.

| Screen / Route | Wireframe | Main purpose | Backend dependency | Planned week |
|---|---|---|---|---|
| `/login` | WF-08 (identity), IAM-001 | Sign-in; cookie session; no demo account list | BFF cookie session, later OIDC (IAM-REQ-005, IAM-REQ-018) | W1 (OIDC W5) |
| App shell (all routes) | ENG-BRIEF chain | Chain-ordered, role-aware navigation | `GET /principals/{id}/effective-access` (IAM-001) | W1 |
| `/regulatory` | WF-01 Regulatory Change Inbox | Change triage: regime, source, release status, impact, reviewer, severity | Sources, content releases, impact | W2 |
| `/regulatory/[sourceId]` (PROPOSED ROUTE) | WF-DOM-01 Source / Provision Dossier | Source snapshot, provision revisions, supersession chain | Source + provision revisions | W2 |
| `/rules`, `/rules/[id]` | RUL-001 lifecycle (content operations; no PRD WF) | Rule drafting, review, shadow, signed release | Rule lifecycle (KEEP) | W2 |
| `/entities`, `/entities/[subjectId]` | WF-DOM-02 Subject / Entity Profile (+ WF-02 "supply fact") | Facts with provenance and valid time, conflicts, home cell | Fact API, cell assignment | W2 |
| `/fact-requests` | WF-02 (missing facts) | Missing-fact workflow | Fact requests and resolutions | W2 |
| `/applicability`, `/applicability/[id]` | WF-02 Applicability Review + WF-DOM-03 Trace | Outcome + determination, trace tree, challenge, attest/adopt | Evaluations, determinations, attestation | W2 |
| `/obligations`, `/obligations/[lineage]` | WF-03 Obligation Dossier + WF-DOM-04 | Source → adoption → expression → controls → evidence → assurance | Obligation dossier read model | W3 |
| `/obligations/[lineage]/assurance` (PROPOSED ROUTE, or a dossier tab) | WF-05 Assurance Period View | Period result, per-leaf coverage, gaps, conflicts, exception intervals | Snapshots, assurance read model | W4 |
| `/controls`, `/controls/[id]` | GLS §11 control detail (+ WF-03 "open control") | Control state, evidence condition, latest test, validity, provenance | Control evaluations, test runs | W3 |
| `/evidence`, `/evidence/[id]` | WF-04 Evidence Record + WF-DOM-05 | Provenance, scope, population, proof, validity, hold | Evidence, upload intents, ledger | W3 |
| `/ipe`, `/ipe/[id]` | WF-04 ("open source run") | Source runs; completeness and accuracy shown separately | Source runs, IPE evaluations | W3 |
| `/governance` | WF-03 / WF-05 actions | Issues, exceptions, compensations, risk acceptance; separate from state | Governance revisions | W3 |
| `/connectors` | WF-08 (connector certification) | Availability, certification and runtime health as separate dimensions | Certification registry | W3 |
| `/` (executive exposure) | GLS §11 + STA-REQ-031 (no PRD WF) | Exposure counts with a declared denominator; UNCERTAIN shown separately | Current assurance projection | W4 |
| `/history` | WF-DOM-06 Historical Reconstruction | Valid period + perspective, revisions used, corrections since | Historical reconstruction and comparison APIs | W4 |
| `/packages` | WF-06 Audit Package Builder | Validate, generate, revoke access, verify | Audit packages, operations, verifier | W4 |
| `/ledger` | WF-04 / WF-08 (integrity proof) | Ledger head, checkpoints, verification runs | Ledger and checkpoint APIs | W4 |
| `/ai` | WF-07 AI / Agent Asset Dossier + WF-DOM-07 | Asset version, classification, obligations, grants, receipts | AI registry, grants, PDP receipts | W5 |
| `/admin` | WF-08 Administration / Trust | Cells, identity integrations, connector certification, privileged-access log, content releases, retention | Admin read models | W5 (entity-creation fix W1) |

## Design-system primitives

Every state primitive shows **text + machine code + icon**, never colour alone (GLS-REQ-011/014/016).

| Primitive | Existing | Action | Notes |
|---|---|---|---|
| Button, Input, Select, Search | CSS classes only | BUILD components | Labels, errors, disabled-with-reason |
| Card, KV, PageHeader, Tabs, Modal | `ui.tsx` | KEEP + a11y (focus trap, Esc, labelled) | — |
| Status pill / badges: Assurance, Applicability (+ determination status), Control, Evidence condition, IPE, Test | `Badge`, `toneOf` | MODIFY | Driven by the dictionary; Applicability is never shown without determination status (GLS §11) |
| Exception badge | `ExceptionBadge` | KEEP | Shown beside the state, never recolours it (GLS §11) |
| Metric card | `Stat` | MODIFY | Requires a denominator and as-of (STA-REQ-031) |
| Table / data grid | `Table` | MODIFY + BUILD | Cursor pagination with pinned watermark (API-REQ-012) |
| Trace viewer (rule, expression) | `RuleTrace`, `ExprTrace` | KEEP | — |
| Period segment ribbon | `SegmentRibbon` | KEEP | — |
| Hash / proof display | `Hash` | KEEP | — |
| Date / period selector, Entity selector, Perspective selector | — | BUILD | Perspectives CURRENT / AS_KNOWN_THEN / RECONSTRUCTED_NOW / CUSTOM_BITEMPORAL; legal-date mode is G-2 |
| As-of / watermark chip | — | BUILD | Required on every current or historical derived view (GLS §11, TIM-REQ-017) |
| Timeline (revisions, record vs valid time) | — | BUILD | — |
| Evidence card | — | BUILD | Provenance, validity, integrity, IPE as separate fields |
| Drawer, Tooltip, Disclosure, Breadcrumbs | — | BUILD | — |
| Empty state, Loading skeleton, Toast | `Loading` | MODIFY + BUILD | — |
| Error display | `ErrorBox` | MODIFY | Renders problem+json `code` and `request_id` (API-REQ-017) |
| Confirmation dialog | — | BUILD | High-risk confirmation / dual control (WF-08) |
| Audit trail | — | BUILD | Actor, grant, time, reason |

## Backend capability map

Existing status comes from the implementation audit and the PostgreSQL verification.

| Capability | Existing status | Action | Governing spec | Planned week |
|---|---|---|---|---|
| Controlled dictionary (`codes.py`) | Correct; non-GLS `RELEASED` status | MODIFY | GLS-REQ-001/012, DAT-REQ-025 | W1 |
| Rule language and evaluator | Correct and pure | KEEP | ZA-RUL-001, APP-REQ-002/004/007 | — |
| Rule publication chain | Correct SoD/shadow/golden flow; draft reads unguarded | KEEP (+ guard reads) | ZA-RUL-001, APP-REQ-006/013/014 | W2 |
| Bitemporal fact store | Correct carving; client-chosen provenance | MODIFY | APP-REQ-005, TIM-REQ-004/007/008, API-REQ-014 | W1–W2 |
| Applicability engine | Correct core; key omits record cutoff; synchronous cross-tenant impact | MODIFY | ZA-APP-001, APP-REQ-019, API-REQ-021 | W2 |
| Adoption / attestation | Human-only; free-text authority; no step-up | MODIFY | APP-REQ-008/009, IAM-REQ-014/034, DAT-REQ-036 | W2 |
| Regulatory sources / content releases | Present; in-place provision edit; no author ≠ verifier | MODIFY | INV-06, INV-09, ZA-RUL-001, DAT-REQ-012 | W2 |
| Obligations and occurrences | Present; occurrences mutated in place | MODIFY | DOM-INV-04/14, STA-REQ-027 | W3 |
| Control evaluation and test runs | Correct precedence; admissibility gaps | MODIFY | STA-REQ-003/006/008/025 | W1 (containment), W3 |
| Satisfaction expression and roll-up | Correct math; `OCCURRENCES` extension (G-6) | KEEP | STA-REQ-009…018 | W3–W4 |
| Exceptions / compensations / issues / risk acceptance | Mutated in place; missing authorization | MODIFY | STA-REQ-013/014/023/024, IAM-REQ-034, ADR-001 C-14 | W1 (authz), W3 (revisions) |
| Evidence vault and ledger | Correct capture, hashing, supersession, erasure events | KEEP | EVD-REQ-001…006/018/021 | — |
| Evidence upload ingress | Direct multipart; no quarantine; client entity | REPLACE (ingress only) | API-REQ-025/033, SEC-REQ-010 | W3 |
| Evidence bindings and conflicts | No authorization; status mutated | MODIFY | EVD-REQ-014/019, STA-REQ-005 | W1, W3 |
| Legal hold / erasure | Works; hold status mutated | MODIFY | EVD-REQ-020/021, TIM-REQ-023 | W3 |
| Merkle checkpoints | Correct construction; on-demand only; TSA pending | MODIFY (scheduling) | EVD-REQ-007/008/025 | W4 (TSA: G-8) |
| Audit packages | Signed and verifiable; latest-snapshot selection; whole-chain disclosure | MODIFY | EVD-REQ-026/027/035, TIM-REQ-015 | W4 |
| Offline verifier | Correct; never asserts compliance | KEEP | EVD-REQ-029/030 | W4 (vectors, purpose keys) |
| Signing keys | One key for all purposes | MODIFY | ADR-001 C-07, EVD-REQ-012, SEC-REQ-026 | W1 |
| Canonical serialization | Python `json.dumps` profile, no vectors | MODIFY | ADR-001 C-02, EVD-REQ-004 | W1 |
| IPE derivation | Correct | KEEP | IPE-REQ-001/005/006/020…023 | — |
| IPE ingress (source runs) | Client-posted claims stamped `SYSTEM_DIRECT` | DISABLE (W1) → MODIFY (W3 connector worker) | IPE-REQ-002/003, EVD-REQ-015, SEC-REQ-017 | W1, W3 |
| Connector certification registry | Tenant admin mutates global state | MODIFY | INV-11, IPE-REQ-028/029, GLS-REQ-017 | W1, W3 |
| Assurance snapshots and periods | Correct math; reads current status; UTC coercion | MODIFY | TIM-REQ-003/013/015, STA-REQ-015…021 | W3–W4 (G-2) |
| Historical reconstruction | Compare/reproduce exist; current-state leaks | MODIFY | ZA-TIM-001, TIM-REQ-006/017 | W4 |
| Current projections / dashboard | Computed live; latest by `created_at` | MODIFY | STA-REQ-028/031, DAT-REQ-019 | W4 |
| Identity / session | Local password, localStorage bearer, 8 h | REPLACE | ADR-001 C-08, IAM-REQ-003/005/018, SEC-REQ-020/021 | W1 (session), W5 (OIDC) |
| Authorization | Role map; many unchecked mutations | MODIFY | IAM-REQ-007/010/034, SEC-REQ-022 | W1 |
| Delegated authority / external engagement | Missing | BUILD | APP-REQ-009, IAM-REQ-027/030 | W2, W4 |
| Workload identity (connectors, agents) | Dev header only | BUILD | IAM-REQ-028/031, SEC-REQ-004 | W3, W5 |
| Demo mode and seed | Default on; unconditional start-up seed | MODIFY | IAM-REQ-001/037 | W1 |
| PostgreSQL bootstrap | Repeatable with least-privilege owner (P0-8A, regression tests on PG 17.11) | KEEP | DAT-REQ-004/027 | Done |
| RLS and DB roles | ENABLE only; self-enabled `app.system`; blanket UPDATE/DELETE | MODIFY | INV-04, DAT-REQ-003/004/005/011/012/027, SEC-REQ-008/009/023/029 | W1–W2 |
| Migrations | `create_all` at start-up | BUILD | DAT-REQ-023/039 | W1 |
| Tenancy / cells | Client-chosen `home_cell_id`; no assignment history | MODIFY + BUILD | TEN-INV-02, TEN-REQ-002/004/005, API-REQ-009 | W1, W5 |
| API contract | `/api`, no idempotency/ETag/problem+json | MODIFY | API-REQ-001/002/004/005/006/014/017 | W1, then per week |
| Events / outbox | Outbox in transaction; off-catalogue names; no relay | MODIFY | EVT-REQ-001…012, ADR-001 C-09 | W1 (envelope), W5 (relay/inbox; G-9) |
| Workflows (ZOL) | None | BUILD (local only) | WRK-REQ-001/002, WRK-INV-01 | W4–W5 (production: G-9) |
| Privacy | Legal hold/erasure only; no privacy schema | BUILD (minimal) | ZA-PRI-001, EVD-REQ-020/021 | W3, W5; rest deferred |
| AI governance (registry, grants, PDP) | Deterministic core correct; revoke not human-gated; caller unauthenticated | MODIFY | INV-01/02, IAM-REQ-030, API-REQ-037/038 | W5 |
| Traceability registry | Missing | BUILD | ZA-TRC-001, ZA-DOC-001 | W5 |
| Model gateway / model-assisted extraction | Regex extractor only; no model calls | — (deferred, G-9) | SEC-REQ-015, ADR OPEN-04 | — |

---

## Week 1 — Foundation, security & existing-code alignment

**Goal:** a secure, repeatable foundation. Governed mutations are authorized. Provenance and scope are server-derived. PostgreSQL RLS is hardened. Demo mode is off by default. The frontend shell talks only to `http://localhost:8000` with no browser-stored token.

### Frontend
- [ ] Freeze the route map against WF-01…08 / WF-DOM-01…07 (table above). Record each PROPOSED ROUTE for review.
- [ ] Order the app shell navigation along the chain (Sources → Applicability → Obligations → Controls → Evidence → Assurance → Packages; Administration last). Keep the existing `Shell`.
- [ ] Make navigation and action visibility role-aware from server effective-access data. Infer nothing from role names on the client (IAM-REQ-007).
- [ ] Put design tokens in the Tailwind v4 theme: existing brand colours, type scale, spacing, radius, elevation. Dark mode is not planned (G-11).
- [ ] Build state badges for assurance, applicability + determination, control, evidence condition, IPE and test from the server dictionary (`/dictionary`): text + code + icon (GLS-REQ-011/016).
- [ ] Build shared states: loading skeleton, empty state, problem+json error panel showing `code` and `request_id`, toast, confirmation dialog.
- [ ] Build form primitives (Input, Select, Search) with labels, inline errors and disabled-with-reason.
- [ ] Build a typed API client for `/v1`: parse problem+json (API-REQ-017), send `Idempotency-Key` on every mutation (API-REQ-004), support `If-Match` (API-REQ-006).
- [ ] Move the auth shell to cookie sessions: delete localStorage token storage and the demo-account list/password on `/login`; send the CSRF header on mutations (ADR-001 C-08, IAM-REQ-018, SEC-REQ-020/021).
- [ ] Separate environments: add `frontend/.env.example`, keep local overrides in ignored `.env.*.local`, and align the `dev`/`start` scripts and docs to port 5173 and API `http://localhost:8000`.
- [ ] Accessibility baseline: visible focus, skip link, focus-trapped modals, `aria-live` for async results, reduced-motion support.
- [ ] Add a CI terminology lint that fails on prohibited copy (GLS §11 list) and on `UNDETERMINED`, `PROVEN_WITH_EXCEPTION`, `COMPLIANT`, `NON_COMPLIANT`.

### Backend
- [ ] Add explicit permission (+ HUMAN where governed), audit record and catalogued event to every unchecked mutation: evidence conflicts (open/resolve), bindings, exceptions, compensations, issue transitions, occurrence fulfilment, control evaluation, IPE re-evaluation, anomaly dispositions, PDP calls (IAM-REQ-007/037, SEC-REQ-022).
- [ ] Derive fact provenance on the server: remove `provenance_class` from the request; humans get P3/P4 only (APP-REQ-005, API-REQ-014).
- [ ] Resolve entity and cell scope on the server: validate every `entity_id` against the tenant, remove client `home_cell_id`, and validate exception targets (IAM-REQ-009, TEN-INV-02, TEN-REQ-005, API-REQ-009).
- [ ] Move connector certification out of tenant roles into a Zoiko-realm role, as append-only certification records (INV-11, IPE-REQ-028/029).
- [ ] Containment until the Week 3 review workflow lands: manual or external `TestRun` results and `HUMAN_UPLOAD` evidence cannot yield `OPERATING` without a review record (STA-REQ-003/025, EVD-REQ-017).
- [ ] DISABLE client-posted `POST /ipe/source-runs` as an authoritative path. Allow it in the dev profile only, labelled as import, with IPE capped at `IPE_INCONCLUSIVE` (IPE-REQ-002/003).
- [ ] Demo mode defaults OFF. The start-up seed is removed from the app lifespan and becomes an explicit dev command. `/auth/demo-accounts` never returns a password (IAM-REQ-001/037).
- [ ] Introduce a migration framework: baseline migration of the current 63-table schema, replace `create_all`, adopt an expand/contract policy (DAT-REQ-023/039).
- [ ] API foundation: `/v1` mount (keep `/api` behind a deprecation flag), problem+json with stable `code`, unknown-field rejection, `platform.idempotency_record` and middleware (API-REQ-002/004/005/014/017).
- [ ] Dictionary alignment: `RELEASED` → `PUBLISHED`, plus a dictionary-conformance test over persisted status columns (GLS-REQ-001/012).
- [ ] `emit()` uses a CloudEvents-compatible envelope with catalogue names, aggregate version and tenant/entity/cell (EVT-REQ-007…012). The relay comes in Week 5.
- [ ] Record governance requests G-1 (runtime ADR) and G-2 (DOC-CON-003) with their owners. These are decisions, not code.

### Security / Platform
- [ ] RLS hardening migration: FORCE RLS on all tenant tables; replace the self-settable `app.system` bypass with a dedicated least-privilege platform role or SECURITY DEFINER functions; revoke runtime UPDATE/DELETE on the immutable tables (`applicability_evaluation`, `assurance_control_evaluation`, `assurance_snapshot`, `governance_attestation`, `ai_policy_decision_receipt`, `evidence_ledger_checkpoint`) with append-only triggers; protect `org_actor` and `auth_*`; make the global catalogue read-only to the runtime role (INV-04, DAT-REQ-003/004/011/012/027, SEC-REQ-023/029).
- [ ] Cookie session: HttpOnly, Secure, SameSite; 5–15 min access with rotating refresh and reuse detection; CSRF protection (ADR-001 C-08, IAM-REQ-018/019, SEC-REQ-021).
- [ ] Purpose-separated signing keys (ledger/checkpoint, content release, package) with key IDs (ADR-001 C-07, EVD-REQ-012, SEC-REQ-026).
- [ ] Canonical JSON v1 profile with published conformance vectors, before more hashed artifacts accumulate (ADR-001 C-02, EVD-REQ-004).
- [ ] CORS: exact `ZA_CORS` origins only; narrow `allow_methods` and `allow_headers` (API-REQ-034).
- [ ] Secrets hygiene: rotate the local `assure_app` password (setup failures may have logged it); keep the `postgres` superuser out of all app config; untrack `frontend/.env.local` (git index change needs approval).
- [ ] Test DB isolation: a disposable PostgreSQL database or schema per test session, never `assure`; fixtures create their own credentials instead of relying on demo mode (audit Finding 29).
- [ ] Start-up guard that refuses `ZA_DEV_IDENTITY_HEADER=1` outside dev/test profiles.

### Testing / Verification
- [ ] Generated authorization-matrix test: every mutating route × principal class (HUMAN, SERVICE, AGENT, EXTERNAL_AUDITOR) → expected allow/deny.
- [ ] PostgreSQL CI job runs `test_rls.py` + `test_pg_bootstrap.py` and fails rather than skips when PostgreSQL is missing.
- [ ] New RLS proofs: FORCE on every tenant table (catalogue query); runtime cannot enable a system scope; UPDATE/DELETE on immutable tables rejected; catalogue read-only to runtime.
- [ ] Negative tests: human P1 provenance rejected; foreign `entity_id` → 404; `home_cell_id` rejected; tenant role cannot certify a connector; manual PASS inadmissible without review.
- [ ] Canonical JSON vectors and dictionary conformance in CI.
- [ ] Frontend: `npx tsc --noEmit`, `npm run build` and the terminology lint pass.

### Dependencies / blockers
- G-1: needed before Week 2 engine changes. Week 1 work is DB, security and config, and does not depend on the runtime choice.
- Approval to untrack `frontend/.env.local` and to recreate `assure_test` per test session.
- P0-8A (repeatable bootstrap) is a prerequisite and is already in place.

### Exit criteria
- The authorization matrix shows 0 mutating routes without explicit authorization.
- On PostgreSQL: FORCE RLS on 100% of tenant tables; the system-scope self-bypass proof fails as intended; 0 runtime UPDATE/DELETE grants on immutable tables.
- The PostgreSQL CI job executes RLS and bootstrap tests with 0 skips.
- A fresh start against an empty database seeds nothing, and demo mode is off.
- The frontend on 5173 calls only `http://localhost:8000/v1`. No token in localStorage. The terminology lint passes with 0 hits. `tsc` and the build pass.

---

## Week 2 — Regulatory source, requirements & applicability

**Goal:** the first half of the chain works end to end, from authoritative source to customer adoption. This covers journeys J-01 (change → impact → adoption) and J-02 (missing fact → determination).

### Frontend
- [ ] WF-01 Regulatory change inbox (`/regulatory`). Fields: regime, authority, source, publication/effective dates, content-release status, impacted entity count, missing-fact count, reviewer, severity. Actions: open source, view diff, open impact, assign review. No action publishes unverified extraction.
- [ ] Source / provision dossier, WF-DOM-01 (PROPOSED ROUTE `/regulatory/[sourceId]`): snapshot digest, stable + revision IDs, locator, valid/record periods, semantic origin, content release, supersession chain.
- [ ] Requirement review/verify flow for content legal reviewers. `AI_PROPOSED` content is visibly distinct from `VERIFIED`/`PUBLISHED` (DOM-INV-13, INV-09).
- [ ] Rule authoring (`/rules`), existing flow kept: lifecycle states, golden vectors, shadow impact as aggregate buckets only (APP-REQ-014).
- [ ] Show the coverage label wherever monitoring-only content could be mistaken for encoded coverage (GLS-REQ-018).
- [ ] Entity profile, WF-DOM-02 (`/entities/[subjectId]`): home cell (read-only), facts with provenance and valid time, conflicts, missing facts, fact history timeline (valid vs record time).
- [ ] Supply-fact form with no provenance selector. After save it shows the server-derived provenance. Corrections carry a change kind and reason.
- [ ] Fact requests (`/fact-requests`): open, in-progress and resolved; a resolve action links the evidence/source.
- [ ] Applicability review, WF-02 + WF-DOM-03 (`/applicability`, `/applicability/[id]`): outcome badge beside determination status (GLS §11), trace tree (keep `RuleTrace`), fact values with provenance, valid/record time, challenge history.
- [ ] Attest/adopt dialog: controlled statement text; delegated-authority grant picker instead of free text; step-up prompt on `step_up_required`; `UNCERTAIN` cannot be adopted (disabled with reason).
- [ ] Challenge dialog with challenge class and detail. It shows that the original evaluation is unchanged (APP-REQ-011).
- [ ] Change-impact view: affected entities and obligations per content release, and customer correction notices.

### Backend
- [ ] Catalogue read APIs per API-001: `/v1/regulatory-instruments`, `/v1/requirements/{id}`, `/v1/obligation-definitions/{id}`, `/v1/content-releases[/{id}/impact]`.
- [ ] Provision edits before `VERIFIED` become revisions (close/insert) instead of in-place `PUT`. Requirement verification enforces author ≠ verifier (ZA-RUL-001 SoD, TIM-REQ-007, IAM-REQ-034).
- [ ] Content release signed with the content-release key. The release gate checks the source fingerprint (INV-06, APP-REQ-006, DAT-REQ-012).
- [ ] Fact APIs under `/v1` with server-derived provenance and a history endpoint exposing both axes, with no `as_of` (TIM-REQ-006, API-REQ-011, DAT-REQ-032).
- [ ] Legal-date profile (`daterange`) for fact, rule and obligation validity (TIM-REQ-003, DAT-REQ-008). **BLOCKED BY GOVERNANCE DECISION (G-2 / DOC-CON-003).**
- [ ] Applicability evaluations API (`/v1/applicability-evaluations[/{id}/trace]`). Keep the pure evaluator and frozen input bundle. The evaluation key includes the record cutoff (APP-001 dedupe rule). Infrastructure errors are never `UNCERTAIN` (APP-REQ-019, API-REQ-018).
- [ ] Missing-fact workflow: FactRequest dedupe, `/v1/fact-requests/{id}/resolutions`, deterministic re-evaluation (APP-REQ-010).
- [ ] Challenges as governed records (`/v1/determinations/{id}/challenges`) that never rewrite the evaluation (APP-REQ-011).
- [ ] Adoption/attestation references a DelegatedAuthorityGrant instead of free text. Human only. SoD. Targets the exact evaluation revision. Returns a `step_up_required` problem when policy demands it (APP-REQ-008/009, DAT-REQ-036, IAM-REQ-014/034, API-REQ-019).
- [ ] Move rule-publication impact re-evaluation out of the publish request into durable per-tenant jobs carrying tenant/entity context (API-REQ-021, TEN-REQ-030).
- [ ] Entity-scoped role assignments and entity-level RLS predicates (IAM-REQ-010, TEN-REQ-021/022, ZA-DAT-001 RLS model).
- [ ] Applicability, determination and fact-request events with EVT catalogue names (EVT-REQ-007/008).

### Security / Platform
- [ ] Shadow-impact output to content operations stays aggregate-only, with no customer identities or fact values (APP-REQ-014).
- [ ] Content-operations staff have no tenant data access (INV-14, EVD-REQ-024), proven at the DB layer.
- [ ] Draft rule reads and the sandbox are restricted to content roles.
- [ ] Extraction stays proposal-only with no model calls (INV-01/02, SEC-REQ-003/013; model use is G-9 / OPEN-04).
- [ ] Trace rendering redacts sensitive fact values by caller authorization (APP-REQ-018).

### Testing / Verification
- [ ] Applicability golden vectors for every rule (positive, negative, unknown, material vs non-material) in CI; they cannot be edited to match code (ZA-QA-001).
- [ ] Bitemporal correction tests: `AS_KNOWN_THEN` vs `RECONSTRUCTED_NOW`, including retroactive carve cases (TIM-REQ-007/008).
- [ ] Attestation negative tests: agent, no grant, expired grant, SoD violation, `UNCERTAIN` outcome.
- [ ] `/v1` contract tests: idempotent replay, key reuse with a different body → `idempotency_conflict`.
- [ ] E2E on PostgreSQL: J-01 and J-02 through the UI.

### Dependencies / blockers
- G-1 before modifying evaluator/applicability code. G-2 for the legal-date profile.
- Entity-level RLS depends on Week 1 RLS hardening and migrations.
- Real AAL3 needs an IdP (Week 5). Week 2 ships the step-up hook only.

### Exit criteria
- J-01 and J-02 run end to end on PostgreSQL through the UI with no DB intervention.
- 100% of applicability golden vectors pass.
- 100% of adopted determinations replay to the same outcome from stored revisions (PRD §14).
- 0 adoptions exist without a grant-referenced human attestation.

---

## Week 3 — Obligations, controls, evidence & IPE

**Goal:** connect adopted obligations to operational proof (journey J-03). Evidence is admissible only with integrity, provenance, completeness, accuracy, freshness and validity. An uploaded file is not proof on its own.

### Frontend
- [ ] Obligations list (`/obligations`) with the GLS §11 header: stable ID + source reference, applicability + adoption, latest state **with period**, primary reason, exception shown separately, as-of.
- [ ] Obligation dossier, WF-03 + WF-DOM-04 (`/obligations/[lineage]`): source/provision, adopted scope, owner, dates, satisfaction expression (keep `ExprTrace`), mapped controls, evidence coverage, exceptions, current and historical assurance. Actions: open control, open evidence, request remediation, generate period snapshot.
- [ ] Occurrences panel that preserves every missed, late and fulfilled occurrence (STA-REQ-027, DOM-INV-14).
- [ ] Controls (`/controls`, `/controls/[id]`) with the GLS §11 control detail: operating state, evidence condition, latest test, validity, source provenance and exception as separate rows.
- [ ] Test-result entry only for permitted roles. Manual results show as "manual — pending review". No direct state editing anywhere (DOM-INV-05).
- [ ] Evidence list and record, WF-04 + WF-DOM-05 (`/evidence`, `/evidence/[id]`): source, collector, query/parameters, scope, population count, connector version, hash/checkpoint proof, validity, legal hold, provenance. Actions: verify integrity, supersede, place/release hold (authorized), open source run.
- [ ] Upload flow: create upload intent → upload → quarantine status → finalize. Not usable as evidence until finalized.
- [ ] Evidence review queue for manual and external-import evidence. Reviewer ≠ uploader. The review result is shown beside the original provenance (EVD §12).
- [ ] Source runs and IPE (`/ipe`, `/ipe/[id]`): completeness and accuracy shown separately, overall IPE shown as derived, reason codes, anomaly dispositions with no result editing (IPE-REQ-001/020/032).
- [ ] Governance (`/governance`): exception request/approve with SoD, always showing "underlying assurance state remains …"; compensations with designer ≠ approver; issue lifecycle; risk acceptance. All shown apart from assurance state (GLS-REQ-016, STA-REQ-014/024).
- [ ] Evidence-conflict view: open and resolve as governed actions with a reason, showing the `REQUIRES_REVIEW` effect.
- [ ] Connectors (`/connectors`): availability, certification and runtime health as separate dimensions; certification read-only for tenants (GLS-REQ-017).

### Backend
- [ ] ObligationInstance and ObligationOccurrence revisions, with no in-place `fulfilled_at` (DOM-INV-14, STA-REQ-027, TIM-REQ-007).
- [ ] Revision tables (close/insert) for exceptions, compensations, legal holds, evidence conflicts, authority grants and IPE anomaly dispositions, with `btree_gist` exclusion constraints for current non-overlap (TIM-REQ-007/021/023, STA-REQ-013, EVD-REQ-020, IPE-REQ-032, DAT-REQ-009/010).
- [ ] The snapshot engine reads those records as of the record cutoff, with no current-status leaks (TIM-REQ-015, STA-REQ-021).
- [ ] Control mappings: framework mappings stay views over canonical controls, one implementation per control and scope (INV-08, DOM-INV-06).
- [ ] TestRun origin classes plus an EvidenceReview object. Admissibility per AutomationRequirement: REQUIRED, PREFERRED, NONE (STA-REQ-003/025, EVD-REQ-017, DAT-REQ-035).
- [ ] External GRC/vendor status enters through a governed mapping into test input with provenance. It never sets control or obligation state (STA-REQ-008, IPE-REQ-024, API-REQ-039).
- [ ] Upload intents: `/v1/evidence-upload-intents` + `/finalize`; quarantine; size and media limits; content sniffing; server-resolved entity (API-REQ-025/033, SEC-REQ-010).
- [ ] Evidence bindings as versioned, authorized records (EVD-REQ-014). Evidence conflicts as governed relationships (EVD-REQ-019, STA-REQ-005).
- [ ] Connector-worker path for source runs: workload identity, installation-pinned Extraction/Mapping/IPEPolicy versions, actual scope recorded. Only this path may produce `SYSTEM_DIRECT` (IPE-REQ-002/003, EVD-REQ-015, SEC-REQ-017, IAM-REQ-028).
- [ ] Connector certification versioned per build and contract. A material change triggers recertification or suspension (IPE-REQ-028/029).
- [ ] Exceptions and compensations: requester ≠ approver, designer ≠ approver, no AI approval, never mutate state (STA-REQ-014/029, IAM-REQ-034, ADR-001 C-14).
- [ ] Minimal privacy: classification fields on evidence; legal hold as a versioned governed object (ZA-PRI-001, EVD-REQ-020).

### Security / Platform
- [ ] Untrusted-file boundary: uploads stay quarantined until scanning passes; fail closed if the scanner is unavailable (SEC-REQ-010/011).
- [ ] Payload object keys carry no customer names or PII (EVD-REQ-013).
- [ ] Connector credentials remain vault references only (DAT-REQ-016, SEC-REQ-006/007).
- [ ] Every evidence read and download is authorized at access time and logged (EVD-REQ-035, API-REQ-026).
- [ ] Payload ingestion idempotent under retries and duplicate delivery (EVD-REQ-038).

### Testing / Verification
- [ ] Admissibility golden vectors:
  - manual PASS, no review → `NOT_EVALUATED`
  - independent review → `OPERATING`, flagged lower automation
  - automation REQUIRED with manual evidence only → not `OPERATING`
  - IPE required but not `IPE_VERIFIED` → not `OPERATING` (IPE-REQ-022)
- [ ] IPE contract tests: zero rows need positive proof, terminal pagination, schema drift, count-only insufficient (IPE-REQ-005/006/009/039).
- [ ] Evidence tamper tests over ledger hashes and the upload/finalize path.
- [ ] Revision tests: approving or revoking never changes an earlier snapshot's `AS_KNOWN_THEN` replay.
- [ ] E2E on PostgreSQL: J-03 (connector run → IPE → evidence → test → control state).

### Dependencies / blockers
- New occurrence-expression use is **BLOCKED BY GOVERNANCE DECISION (G-6)**. Any new evidence provenance code is **BLOCKED BY GOVERNANCE DECISION (G-4)**. New `MISSING`/`CONFLICTING` handling is **BLOCKED BY GOVERNANCE DECISION (G-7)**.
- A malware-scanning component must be selected. It is not covered by an ADR OPEN item, so record the decision.
- Revisions depend on the Week 1 migration framework.

### Exit criteria
- J-03 runs end to end on PostgreSQL.
- Golden vectors show 0 paths where self-asserted or unreviewed manual evidence reaches `OPERATING`.
- Evidence is unusable before finalize, enforced by tests.
- 0 in-place updates on governed records, enforced by the DB.
- 100% of connector evidence shows completeness and accuracy separately (PRD §14 IPE metric).

---

## Week 4 — Assurance, history, dashboards & audit package

**Goal:** turn obligation, control and evidence state into defensible period assurance, reconstructable history, and a package an external auditor can verify offline (journey J-05).

### Frontend
- [ ] Assurance period view, WF-05: period selector, per-leaf coverage, gaps, conflicts, exception intervals, evaluator version, resulting state. Actions: explain result, create issue, export snapshot. No manual override control exists.
- [ ] Snapshot detail: pinned revisions, dependency digest, perspective, watermark, reproducibility status.
- [ ] Executive exposure (`/`): counts read only from the current assurance projection, with declared denominator, `UNCERTAIN` reported separately, and period + perspective on every state (STA-REQ-028/030/031). Entity selector.
- [ ] Evidence-freshness view by requirement using evidence condition codes and `valid_until`.
- [ ] Regulatory change impact on assurance: content release → affected obligations and snapshots.
- [ ] Historical reconstruction, WF-DOM-06 (`/history`): valid period plus perspective selector (`CURRENT`, `AS_KNOWN_THEN`, `RECONSTRUCTED_NOW`, `CUSTOM_BITEMPORAL`), revisions used, corrections since, diff classes.
- [ ] As-of / watermark chip on every current and historical derived view (GLS §11, TIM-REQ-017).
- [ ] Audit package builder, WF-06 (`/packages`): entity/scope, period, obligations, inclusion/redaction, a "validate" readiness check, generate (operation progress), revoke external access. Historical packages are immutable.
- [ ] Verification result view: `PASS` / `PASS_WITH_WARNINGS` / `FAIL` with steps and warnings such as `TIMESTAMP_NOT_AVAILABLE`. Never a compliance claim.
- [ ] Ledger and checkpoints (`/ledger`): head, checkpoint status, verification runs.
- [ ] External-auditor experience: engagement-scoped, read-only, time-limited package views (PRD §4 roles, IAM-REQ-027).

### Backend
- [ ] Snapshots pin exact revisions and the record cutoff; no "latest" joins. Snapshot lifecycle includes `SUPERSEDED_FOR_CURRENT_VIEW` and `INVALIDATED_BY_INTEGRITY_FAILURE` (TIM-REQ-015, STA-REQ-019/021, DOM-INV-10).
- [ ] KEEP period segmentation and roll-up. Changing period types to legal dates is **BLOCKED BY GOVERNANCE DECISION (G-2)**.
- [ ] Read APIs take a perspective or `valid_at` + `known_at` (no `as_of`) and echo the RecordWatermark (API-REQ-011/013, TIM-REQ-006/017, DAT-REQ-032).
- [ ] Historical reconstruction and comparison APIs (ZA-TIM-001 endpoints), reusing the existing compare and reproduce logic.
- [ ] `current_assurance_projection` referencing snapshot ID + watermark, rebuildable (STA-REQ-028, DAT-REQ-019, TIM-REQ-016).
- [ ] Assurance read models: `GET /v1/obligations/{id}/assurance`, `/v1/assurance-snapshots/{id}[/trace]`.
- [ ] Packages select snapshots by exact entity + period + perspective, validate entity membership, and build as an Operation (202 + `/v1/operations/{id}`) (EVD-REQ-027, TIM-REQ-015, API-REQ-021).
- [ ] Package minimization: non-selected ledger events reduced to hashes, redactions as derived members, payload inclusion per policy (EVD-REQ-027/031, TEN-REQ-018).
- [ ] Packages signed with the package-purpose key. HMAC verified before signing. The verifier pins keys by purpose (ADR-001 C-07, EVD-REQ-028/029).
- [ ] 15-minute checkpointing of changed heads and an hourly verification job that opens an incident on mismatch (EVD-REQ-007/025). Uses a local durable runtime; production is **BLOCKED BY GOVERNANCE DECISION (G-9 / OPEN-06)**. RFC 3161 is **BLOCKED BY GOVERNANCE DECISION (G-8 / OPEN-03)**, so status is reported as `TSA_PENDING` (EVD-REQ-008/037).
- [ ] ExternalEngagement: engagement-scoped, time-boxed, resource-allowlisted auditor access that is revoked on expiry (IAM-REQ-027/040, EVD-REQ-035).
- [ ] Infrastructure and evaluator errors never become `NOT_PROVEN` or `REQUIRES_REVIEW` (STA-REQ-022, API-REQ-018).

### Security / Platform
- [ ] Packages are immutable after signing. Revoking access never mutates a package (EVD-REQ-026).
- [ ] `/packages/verify-upload` gets authentication plus size, entry-count and compression-ratio limits (API-REQ-033).
- [ ] Exports stay within the entity home cell. Export is recorded as a ledger event (EVD-REQ-023, TEN-REQ-010/011).
- [ ] High-sensitivity export and erasure/hold require step-up (IAM-001 step-up matrix). Hook now; AAL3 in Week 5.

### Testing / Verification
- [ ] STA golden roll-up vectors for every expression pattern. Period precedence `FAILING` > `REQUIRES_REVIEW` > `NOT_PROVEN` > `PROVEN`. An approved exception never turns a failure into `PROVEN`.
- [ ] Reproducibility: 100% of sampled snapshots reproduce from stored inputs, including after later corrections (PRD §14).
- [ ] Package verification: the offline verifier passes a generated package; tampering → `FAIL`; wrong-purpose key → `FAIL`; period/entity mismatch → 409/422.
- [ ] Canonical JSON cross-language vectors consumed by the verifier.
- [ ] E2E on PostgreSQL: J-05 (period → package → offline verification).

### Dependencies / blockers
- Week 3 revisions are needed for correct `AS_KNOWN_THEN`.
- G-2 for legal-date periods; G-8 for TSA; G-9 for the production workflow runtime.

### Exit criteria
- An external-auditor-style offline verification of a sample package passes (PRD §6 R1 exit criterion).
- 100% of sampled snapshots reproduce.
- Every dashboard number is traceable to a snapshot ID + watermark.
- A frontend code check finds 0 roll-up or state-derivation logic in the UI.

---

## Week 5 — Integration, hardening, QA & release readiness

**Goal:** make the Release-1 vertical slice demonstrable, testable and defensible. That means all journeys J-01…J-06, real identity, isolation proven at every layer, and release evidence produced by CI.

### Frontend
- [ ] AI/agent asset dossier, WF-07 + WF-DOM-07 (`/ai`): asset version, owner, entity, purpose, model/provider, component bill, classification facts, applicable obligations, required evaluations, active grants, recent policy receipts. Actions: update fact, trigger applicability, propose grant, suspend grant.
- [ ] Administration / trust, WF-08 (`/admin`): cells and residency, identity integrations, connector certification (read-only for tenants), privileged-access log, content-release versions, retention policy. High-risk actions need confirmation or dual control.
- [ ] OIDC sign-in through the BFF (Auth Code + PKCE): session expiry/refresh UX and step-up re-authentication flow.
- [ ] Authorization-aware actions everywhere: rendered from server decisions; 403 problems explain the reason.
- [ ] Responsive layouts for dossiers, trace trees and data grids at narrow widths.
- [ ] Accessibility pass: keyboard-only runs of J-01…J-06; accessible names for badges and trace trees; automated accessibility checks in CI.
- [ ] Historical-context audit: every state shows its period and perspective (STA-REQ-030, GLS-REQ-009).
- [ ] Copy audit against the GLS §11 prohibited-copy list. Machine codes included in exports (GLS-REQ-014).
- [ ] Frontend security: CSP, no secrets in `NEXT_PUBLIC_*`, no stack traces shown, a decision on the Next.js/PostCSS audit advisory.
- [ ] Replace the deprecated `next lint` setup wizard with an ESLint CLI configuration (adds dev dependencies; reviewed change).
- [ ] Production build validation: `next build` with production env; the bundle points only at the configured API base.

### Backend
- [ ] OIDC Auth Code + PKCE through the BFF; identity keyed by issuer + subject, never email; no auto-linking (IAM-REQ-003/004/005, ADR-001 C-08). Dev IdP choice recorded.
- [ ] Short-lived, audience-bound workload credentials for the connector worker and agents. PDP callers authenticate as agent principals (IAM-REQ-028/031, SEC-REQ-004, API-REQ-037).
- [ ] AI-governance hardening: grants as revisions, revoke requires HUMAN, agent decision responses use API-001 wire values (INV-01/02, IAM-REQ-030, API-REQ-037/038).
- [ ] Outbox publisher and durable consumer inbox on local transport: at-least-once, effect-once via inbox (EVT-REQ-001…005, ADR-001 C-09). A managed queue is **BLOCKED BY GOVERNANCE DECISION (G-9 / OPEN-05)**.
- [ ] ZOL human tasks: HumanTask/TaskDecision records authoritative in PostgreSQL for attestation and exception approval; workflow state is never truth (WRK-REQ-002, WRK-INV-01). Production Temporal is **BLOCKED BY GOVERNANCE DECISION (G-9 / OPEN-06)**.
- [ ] Idempotency-Key and `If-Match` on every remaining mutation: 409/412 on conflict (API-REQ-004/005/006).
- [ ] Complete problem+json coverage with stable codes and no stack traces (API-REQ-017, SEC-REQ-042).
- [ ] Bitemporal EntityCellAssignment and server-side route resolution (single cell); routing epoch carried by jobs (TEN-INV-02, TEN-REQ-002/004/005/030).
- [ ] Event and log payload minimization: no secrets, no raw evidence (EVT-REQ-024/025, SEC-REQ-031, IAM-REQ-043).
- [ ] Audit completeness: every material privileged action records principal, effective grant, policy version, scope, correlation and time (IAM-REQ-037, API-REQ-020).
- [ ] Performance checks against spec budgets: pure evaluation p95 < 50 ms (ZA-APP-001); PDP p99 ≤ 50 ms (ZA-IAM-001); agent decision p99 ≤ 150 ms (ZA-API-001).
- [ ] Traceability registry (`governance/traceability/traceability.yaml`) mapping implemented requirements → tests → evidence (ZA-TRC-001, ZA-DOC-001).

### Security / Platform
- [ ] Release-blocking cross-tenant and cross-entity negative suite over read/write paths, historical tables, exports and jobs (DAT-REQ-028, SEC-REQ-009).
- [ ] Threat-model update for the new boundaries: BFF, connector worker, package export (SEC-REQ-039).
- [ ] Key-rotation drill with recorded evidence (SEC-REQ-044). KMS/HSM before any shared environment (depends on G-9 / OPEN-01).
- [ ] Privacy checks: a hold blocks erasure; erasure preserves ledger history; logs and events are minimized (ZA-PRI-001, EVD-REQ-020/021).
- [ ] Supply chain: SBOM and dependency audit for backend and frontend (SEC-REQ-028).
- [ ] Rotate all local credentials used during setup.

### Testing / Verification
- [ ] Unit and integration suites on PostgreSQL with 0 skips in the release job. `SKIPPED` never counts as proof (ZA-QA-001).
- [ ] RLS proofs + authorization matrix + cross-tenant/entity negatives all green.
- [ ] Golden vectors: applicability, assurance roll-up, canonical serialization (cross-language).
- [ ] Bitemporal reconstruction suite (`AS_KNOWN_THEN` / `RECONSTRUCTED_NOW`) and evidence tamper suite.
- [ ] Package verification with the standalone verifier and a pinned trusted key.
- [ ] Frontend `tsc`, production build, accessibility checks, terminology lint.
- [ ] E2E critical journeys J-01…J-06 on PostgreSQL through 5173 → 8000.
- [ ] Dependency test: enforcement and authorization modules cannot import any model client (INV-02 verification method).

### Dependencies / blockers
- A dev IdP is needed for OIDC; real AAL3 depends on IdP capability.
- G-5 for any principal-class schema changes. G-9 for managed queue and Temporal. G-1 outcome.

### Exit criteria — Release-1 vertical-slice milestone
- J-01…J-06 pass end to end on PostgreSQL through the UI (5173 → 8000) without DB intervention.
- An external auditor validates a sample package offline (PRD §6 R1 exit criterion).
- 0 successful unauthorized cross-tenant reads or writes in the release test suite (PRD §14).
- 100% of adopted obligations replay; 100% of sampled snapshots reproduce; 100% of acknowledged evidence manifests verify (PRD §14).
- No AI on binding paths, proven by the dependency test. Golden vectors 100%. 0 test skips in the release job.
- `tsc`, build, accessibility and terminology checks pass. 0 browser-stored tokens.
- Explicitly **not yet R1 Controlled Production**. Still outstanding: Customer Zero quarter (ADR-001 C-12), content operations (PRD §15), G-1/G-2/G-8/G-9 decisions, QA release evidence.

---

## Deferred beyond these 5 weeks (not planned here)

- Moving tables into DAT bounded schemas (`org`, `catalog`, `applicability`, …) and UUIDv7 for existing IDs. This is a large migration; schedule it after the migration framework is proven.
- Multi-cell routing, federation, TransferPolicy and cell migration (ZA-TEN-001), which depend on OPEN-01.
- SCIM, JIT and break-glass, SupportAccessGrant, access reviews (ZA-IAM-001).
- Full privacy case management: rights requests, retention engine, deletion executions (ZA-PRI-001).
- Model gateway and model-assisted extraction (OPEN-04). Webhooks with HTTP Message Signatures (ADR-001 C-11).
- Additional regimes and connectors beyond the R1 set: only once their proof path exists (ENG-BRIEF).

## Definition of done (every task)

- [ ] The PR names its governing spec and requirement IDs, and touches only what the task needs.
- [ ] Tests sit next to the rule: unit tests, affected golden vectors, and PostgreSQL tests where data or security is involved.
- [ ] No prohibited terminology. Machine codes come from the dictionary.
- [ ] Material writes have authorization + audit + catalogued event in one transaction.
- [ ] Migrations are expand/contract and preserve historical reconstruction (DAT-REQ-023).
- [ ] No document conflict is resolved silently. Anything needing a decision is labelled `BLOCKED BY GOVERNANCE DECISION`.
