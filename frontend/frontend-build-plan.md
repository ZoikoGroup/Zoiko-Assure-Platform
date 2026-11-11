# Zoiko Assure — Frontend Build Plan

**Stack:** Next.js 15 (App Router) · React 19 · TypeScript · Tailwind CSS v4
**Scope:** the Release-1 UI for the obligation-to-proof chain:
regulatory sources → applicability → adoption → obligations → controls → evidence & IPE → period assurance → verifiable audit packages.
It also covers the AI/agent asset dossier and administration/trust.
**Runs on:** `http://localhost:5173`, calling the API at `http://localhost:8000`.

> Status: **already scaffolded and partly built.** The first implementation has 25 routes and a shared component set. This plan extends and hardens them; it does not restart the app.
> Backend counterpart: [`../backend/backend-build-plan.md`](../backend/backend-build-plan.md).

---

## What the UI must make clear

- **The chain:** every screen shows where it sits in Source → Requirement → Applicability → Adoption → Obligation → Controls → Evidence → Assurance → Package.
- **Truth comes from the backend.** The UI never calculates applicability, control or assurance state.
- **Wireframes:** the specs define what each screen must show and allow:
  - PRD §13: WF-01…WF-08
  - DOM §14: WF-DOM-01…07
  - GLS §11: wording and status rules

  There is no separate visual spec, so keep the existing brand tokens (navy, orange, slate).

---

## Information architecture (existing routes)

The sidebar follows the controlled navigation in `frontend/components/nav.ts`; each page title matches its nav label.

```
/login                                  Sign-in
Overview
  /                                     Dashboard
Regulatory Content
  /regulatory                           Regulatory Inbox                   (WF-01)
  /sources                              Sources & Provisions               (WF-DOM-01)
  /rules, /rules/[id]                   Rule Authoring                     (WF-RUL-01..03)
  /rules/workbench                      Rule Test Workbench                (WF-RUL-04)
Applicability
  /entities, /entities/[id]             Entities & Facts                   (WF-DOM-02, WF-APP-02)
  /applicability                        Applicability Review               (WF-APP-01)
  /fact-requests                        Fact Requests                      (WF-02)
  /applicability/trace, /[id]           Applicability Trace                (WF-DOM-03)
Assurance
  /obligations, /[lineage]              Obligation Dossiers                (WF-DOM-04, WF-STA-01)
  /controls, /[id]                      Controls & Tests                   (WF-STA-02)
  /assurance                            Assurance Periods                  (WF-STA-05, WF-TIM-04)
  /governance                           Exceptions & Issues                (WF-STA-04)
Evidence
  /evidence, /[id]                      Evidence Records                   (WF-DOM-05, WF-EVD-01)
  /ipe                                  IPE (anomalies, policies)          (WF-IPE-04)
  /ipe/runs, /ipe/[id]                  Source Runs                        (WF-IPE-01/02)
Audit & History
  /packages                             Audit Package Builder              (WF-EVD-05/06)
  /history                              Historical Reconstruction          (WF-DOM-06, WF-TIM-02)
  /ledger                               Ledger                             (WF-EVD-03)
AI & Agents
  /ai                                   AI/Agent Asset Dossiers            (WF-DOM-07)
Integrations
  /connectors                           Connectors + event outbox          (FR-CON, WF-EVT-03)
Administration & Trust                  (/admin opens Users & Roles)
  /admin/users                          Users & Roles                      (WF-IAM-01/02)
  /admin/identity                       Identity Integrations              NOT YET ENABLED (IAM-REQ-014, IAM-001 §04)
  /admin/cells                          Cells & Residency                  (WF-TEN-01/02)
  /admin/releases                       Content Releases
  /admin/retention                      Retention & Legal Holds            (WF-EVD-04, WF-PRI-05; retention policies not yet enabled)
  /admin/access-log                     Privileged Access Log              (audit trail; privileged sessions not yet enabled)
  /admin/dictionary                     Controlled Dictionary              (Jump to)
```

**Existing components to keep:** `StatusPill`/`Badge`, `Card`, `Table`, `Tabs`, `Modal`, `Drawer`, `RuleTrace`, `ExprTrace`, `SegmentRibbon`, `AttestModal`, `AssertModal`, `RequirementViews`, `RuleTesting`.

---

## Week 1 — Foundation & design system

**Goal:** a consistent, accessible app shell and component library, safely connected to the backend.

- [ ] App shell:
  - sidebar ordered along the chain
  - header with entity selector, user menu and an "as of" indicator
  - responsive collapse on small screens
- [ ] Design tokens in Tailwind v4: existing brand colors, type scale, spacing, radius, elevation. No dark mode, because no spec asks for it.
- [ ] Base components: `Button`, `Input`, `Select`, `Search`, `FormField`, `Table`, `Tabs`, `Modal`, `Drawer`, `Tooltip`, `Breadcrumbs`, `Disclosure`, `Toast`, `ConfirmDialog`.
- [ ] Status badges for applicability (always paired with adoption status), assurance, control, evidence condition, IPE and test result. Each shows **icon + text + color + code**, read from the backend dictionary.
- [ ] Loading, empty and error states. The error state shows the error code and request ID.
- [ ] Typed API client:
  - one base URL from `NEXT_PUBLIC_API_URL`
  - structured error handling
  - an `Idempotency-Key` header on every write
- [ ] Auth shell:
  - login page with no demo accounts or passwords
  - cookie session instead of a token in `localStorage`
  - CSRF header on writes
- [ ] Role-aware UI: navigation and action buttons driven by the permissions the server returns.
- [ ] Environment:
  - add `frontend/.env.example`
  - keep local values in ignored `.env.*.local` files
  - ports 5173 and 8000 only; remove every `:3100` / `:8100` reference
- [ ] Accessibility baseline: visible focus, skip link, keyboard-usable modals, `aria-live` for async results, reduced motion.
- [ ] CI terminology check that fails on `COMPLIANT`, `UNDETERMINED`, `PROVEN_WITH_EXCEPTION` and "you are compliant".

**Depends on:** backend Week 1 (cookie session and current-user permissions).

**Exit criteria:**
- All existing pages use the new shell and components.
- No token is kept in browser storage.
- The terminology check reports 0 hits.
- `tsc` and `npm run build` pass.

---

## Week 2 — Regulatory sources & applicability

**Goal:** users can see what regulation applies to each legal entity, and why: **Source → Requirement → Entity facts → Applicability → Adoption.**

- [ ] **Regulatory change inbox (WF-01)**
  - Shows: regime, source, dates, release status, impacted entities, missing facts, reviewer, severity and coverage label.
  - Actions: open source, diff, impact, assign review.
  - Never offers "publish" for unverified AI extraction.
- [ ] **Source detail & versions:** fingerprint, version history and provisions.
  - Requirement list and detail, with `AI_PROPOSED` visibly separate from `VERIFIED` / `PUBLISHED`.
  - "Verify" is available only to content legal reviewers, never to the author.
- [ ] **Rule authoring & trace** (Zoiko content staff): keep the existing flow. Show golden tests, and shadow impact as totals only.
- [ ] **Entity profile & facts**
  - Shows: value, provenance, valid time, conflicts, missing facts, history timeline.
  - "Supply or correct fact" requires a reason. There is **no provenance selector**; the server decides provenance.
- [ ] **Missing fact requests:** open, in progress and resolved; resolve with a source reference.
- [ ] **Applicability review & trace**
  - Shows the `APPLIES` / `DOES_NOT_APPLY` / `UNCERTAIN` badge beside the adoption status, plus the trace tree, facts used, missing facts, valid and recorded time, and challenge history.
  - The challenge dialog states that the original evaluation is not changed.
- [ ] **Adoption / attestation dialog:** attestation statement, delegated-authority picker (no free-text authority) and step-up prompt. `UNCERTAIN` cannot be adopted.
- [ ] **Regulatory change impact view:** affected entities and obligations, and correction notices.
- [ ] Shared: keyboard-navigable trace tree, revision timeline (valid vs recorded time), provenance chip, "AI proposal" label.

**Depends on:** backend Week 2 (source, requirement, fact, evaluation, attestation and impact APIs).

**Exit criteria:**
- All pages use real data, with no mocks.
- End-to-end tests for J-01 (change → impact → adoption) and J-02 (missing fact → determination) pass.
- `UNCERTAIN` can never be adopted from the UI.
- `tsc` and the build pass.

---

## Week 3 — Obligations, controls & evidence

**Goal:** show how an adopted obligation is supported by controls and trustworthy evidence.

- [ ] **Obligations list:** ID and source, applicability with adoption status, latest state **with its period**, reason, any exception shown separately, as-of.
- [ ] **Obligation dossier**
  - A clickable chain strip at the top: Source → Requirement → Applicability → Adoption → Obligation → Controls → Evidence → Assurance.
  - Shows: owner, occurrences, satisfaction expression, controls, evidence coverage, exceptions.
  - Actions: request remediation, generate snapshot.
- [ ] **Controls & test results**
  - Separate rows for operating state, evidence condition, latest test, validity, provenance and exception.
  - Each test result shows its origin and review status.
  - "Record result" is shown only to permitted roles.
- [ ] **Control expression view:** keep `ExprTrace`. Show ALL / ANY / K_OF_N / COMPENSATION with a result for each part.
- [ ] **Evidence list & record**
  - Shows: source, collector, query, scope, population, connector version, hash and checkpoint proof, validity, legal hold, provenance.
  - Actions: verify, supersede, hold.
- [ ] **Evidence upload:** intent → upload → quarantine/scan → finalize. Evidence is unusable until finalized, and failures show clear messages.
- [ ] **Evidence review queue:** manual and external-import evidence awaiting review. The reviewer must not be the uploader.
- [ ] **Source runs & IPE:** completeness and accuracy shown **separately**, the derived overall result, and anomalies. Results are read-only.
- [ ] **Issues, exceptions, compensations, remediation**
  - An exception always shows "underlying state remains X".
  - Approvals respect separation of duties.
- [ ] **Connectors:** availability, certification and runtime health as three separate badges.

**Governance:**
- No UI path edits a control, obligation or assurance state directly.
- An uploaded file plus "PASS" is never proof on its own.

**Depends on:** backend Week 3 (dossier read model, upload intents, evidence review, revisions).

**Exit criteria:**
- End-to-end tests for J-03 (connector run → evidence → control state) and J-04 (exception and compensating control) pass.
- The dossier shows the full chain.
- Evidence cannot be used before it is finalized.
- `tsc` and the build pass.

---

## Week 4 — Assurance, history & audit packages

**Goal:** make period assurance and historical proof easy to read. Display the backend's state; never compute it.

- [ ] **Assurance overview & entity view**
  - Counts come from the backend projection, with the denominator shown.
  - `UNCERTAIN` is shown separately.
  - Every number carries a period and an as-of time.
  - Entity and period selectors.
- [ ] **Assurance period view (WF-05)**
  - Shows: coverage per control, gaps, conflicts, exception intervals, evaluator version, and the resulting `PROVEN` / `NOT_PROVEN` / `FAILING` / `REQUIRES_REVIEW`.
  - Actions: explain, create issue, export snapshot.
  - There is **no manual override**.
- [ ] **Snapshot detail & obligation history:** pinned revisions, perspective, watermark and reproducibility status, plus snapshots over time.
- [ ] **Evidence freshness & regulatory exposure views.**
- [ ] **Historical reconstruction**
  - Current vs as-known-then, side by side.
  - Perspective selector: `CURRENT`, `AS_KNOWN_THEN`, `RECONSTRUCTED_NOW`, `CUSTOM_BITEMPORAL`.
  - Shows the corrections made since.
- [ ] **Audit package builder (WF-06)**
  - Choose entity, period and obligations, then inclusion and redaction.
  - Validate → generate (with progress) → revoke external access.
  - Past packages are read-only.
- [ ] **Verification result:** `PASS` / `PASS_WITH_WARNINGS` / `FAIL`, with each step and warning. Never a compliance claim.
- [ ] **External auditor read-only views:** engagement-scoped, with the expiry date shown. Plus the ledger & checkpoints page.
- [ ] Shared: period, perspective and entity selectors, an "as of" chip, a metric card that requires a denominator, and an operation-progress component.

**Depends on:** backend Week 4 (snapshots, projection, history, packages, operations).

**Exit criteria:**
- Every number links to a snapshot and an as-of time.
- The J-05 end-to-end test passes, including a tampered package showing `FAIL`.
- 0 assurance calculations in frontend code.
- `tsc` and the build pass.

---

## Week 5 — Quality, security & release readiness

**Goal:** complete and polish the Release-1 journey:
regulatory change → applicability → adoption → obligation → control → evidence → IPE → assurance → audit package.

- [ ] Remaining wireframes: AI / agent asset dossier (WF-07) and administration / trust (WF-08).
- [ ] OIDC sign-in through the backend, session expiry and refresh, step-up re-authentication.
- [ ] Wireframe coverage review: every required field and action in WF-01…08 and WF-DOM-01…07.
- [ ] Permission-denied UX, plus an audit trail on decisions showing who, when and under which authority.
- [ ] Switch the API client to `/v1`. Handle 409/412 conflicts with a "refresh and retry" message.
- [ ] Responsive, keyboard and screen-reader review, with automated accessibility checks in CI.
- [ ] Coverage review of loading, empty and error states and confirmation flows. Terminology review against GLS §11.
- [ ] Frontend security:
  - Content-Security-Policy
  - no secrets in `NEXT_PUBLIC_*`
  - no stack traces shown
  - a dependency audit, including the Next.js/PostCSS advisory
  - replace the deprecated `next lint` wizard with an ESLint CLI configuration
- [ ] End-to-end journeys J-01…J-06 against PostgreSQL, through 5173 → 8000.

**Exit criteria:**
- Every wireframe field and action is present.
- J-01…J-06 pass.
- 0 forbidden terms.
- 0 tokens in browser storage.
- Accessibility checks pass.
- `tsc` and the build pass.

---

## Cross-cutting standards

| Concern | Standard |
|---------|----------|
| Terminology | Applicability: `APPLIES` / `DOES_NOT_APPLY` / `UNCERTAIN`. Assurance: `PROVEN` / `NOT_PROVEN` / `FAILING` / `REQUIRES_REVIEW`. Never `COMPLIANT`, `NON_COMPLIANT`, `UNDETERMINED` or `PROVEN_WITH_EXCEPTION`. |
| Status display | Icon + text + color + machine code; never color alone. |
| Context | Assurance state always shows its period and as-of time. Applicability always shows adoption status. |
| Exceptions | Shown beside the state; they never turn a state green. |
| Truth | Every state comes from the backend; no client-side roll-up. |
| AI | Suggestions are labelled as proposals. AI can never adopt or attest. |
| Security | Cookie session + CSRF; no tokens in `localStorage`; no secrets in `NEXT_PUBLIC_*`. |
| Accessibility | Keyboard use, visible focus, `aria-live`, reduced motion; automated checks. |
| Data | Real APIs only; no mock assurance state ships. |

## Suggested delivery order

```
Week 1 foundation → Week 2 applicability → Week 3 obligations/evidence → Week 4 assurance/packages → Week 5 hardening
Each week builds pages against the matching backend week's APIs.
WF-07 (AI dossier) and WF-08 (admin) can be built in parallel at any point after Week 1.
```

Sizing: 1–2 frontend engineers, working alongside the backend plan week by week.
