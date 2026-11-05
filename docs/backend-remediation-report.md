# Backend remediation before push

Date: 2026-11-04/05. Remediation of the findings in [backend-pre-push-audit-report.md](backend-pre-push-audit-report.md),
limited to Naveen's backend responsibilities (work split, Developer 2): regulatory catalogue, rules, applicability and
obligation generation, ObligationInstance lifecycle, control mappings and SatisfactionExpressions, governed test results,
issues, exceptions, compensations, and historical revisions. All changes are local. Nothing was staged, committed,
pushed, pulled or stashed. No migration was created, modified or run against the canonical `assure` database, which
remains at head 0013 (checked read-only with `python -m app.migrate status`).

## 1. Final readiness: SAFE TO PUSH WITH WARNINGS

**What changed:**
- Both HIGH backend defects (F-02, F-03) are fixed, as is the HIGH CI defect (F-01).
- The MEDIUM findings F-04, F-05, F-06, F-07 and F-09 are fixed. F-08 is fixed within the existing history convention.
- The LOW findings F-13, F-15 and F-16 are fixed.
- Every fix has regression tests.
- The full suite passes on PostgreSQL 17 (528/528, 0 skipped) and on SQLite (398 passed, 130 skipped, all
  `PostgreSQL-only`).
- Every CI gate passes locally.

**Warnings that remain:**
- **Not fixed, and why:** F-10 needs the EVT owner; F-11 is platform-wide; F-12 is review by another owner; F-14 is a
  policy decision.
- **Constraint-level uniqueness** for current revisions needs an unapproved schema change (R-01).
- **An observed workflow gap** (R-02): re-review of an adopted determination cannot complete through the API. It needs
  an APP-001 decision.
- **The branch still mixes ownership areas**, and the work is uncommitted (audit §1, §12).

## 2. Findings, corrections and evidence

### F-01: CI unit gate failed on two skip messages (HIGH)

| | |
|---|---|
| Requirement | `.github/workflows/ci.yml` backend-unit gate: `--allow-skip-reason PostgreSQL-only` (exact match) |
| Previous behaviour | `pytest.skip("PostgreSQL-only: concurrent transactions and row locks")` and `"…, unique constraint and row locks"`; the gate failed |
| Correction | The reason is exactly `"PostgreSQL-only"`; the diagnostic detail moved into a comment. The gate is unchanged |
| Files | `backend/tests/test_regulatory_traceability.py`, `backend/tests/test_obligation_definition_authoring.py` (and the new `pg` fixture in `test_backend_remediation.py` follows the same rule) |
| Evidence | SQLite gate **PASS** (it previously reported FAIL with "4 test(s) skipped for an unexpected reason"); the affected tests run and pass on PostgreSQL |

### F-02: invalid control test policy could yield OPERATING (HIGH)

| | |
|---|---|
| Requirement | ZA-STA-001 §03: "ControlTestPolicy aggregation v1" = `ALL_REQUIRED`, `K_OF_N_TESTS` ("at least K of N peer tests must pass"), `PRIMARY_PLUS_CORROBORATION` ("Named primary test must pass"), `CUSTOM_VERSIONED_POLICY` (constrained AST only); `AutomationRequirement` = `NONE`, `PREFERRED`, `REQUIRED`. STA-REQ-006, STA-REQ-025; STA §16 |
| Previous behaviour | Any string mode, automation value or integer k was accepted. `k = policy.k_threshold or len(req)` made k = −5 OPERATING with no evidence or with every test failing. An unknown mode fell through to ALL_REQUIRED. A policy of optional tests only was vacuously OPERATING. PRIMARY_PLUS silently used the first required test |
| Correction | `engines/assurance.py`: vocabularies taken from STA §03; `policy_problems()` is shared by the API and the evaluator. `routers/assurance.py` `ControlIn`: `StrictInt` k plus a model validator. Rules: mode in the three implemented v1 modes; `CUSTOM_VERSIONED_POLICY` explicitly refused as not implemented (no semantics invented); automation in NONE/PREFERRED/REQUIRED (case-sensitive); k required iff K_OF_N_TESTS with `1 ≤ k ≤ required tests`; at least one required test when tests are given; exactly one named primary for PRIMARY_PLUS. `evaluate_control` re-validates stored policies before deriving anything, and `_aggregate` refuses an unknown mode, out-of-range k, no required tests or no named primary (defence in depth) |
| Evidence | 16 invalid-policy API cases → 422 and nothing written; boundary cases (k = 1, k = n, PRIMARY_PLUS, REQUIRED) → 200; a stored k = −5 policy with failing tests → 409 `CONTROL_TEST_POLICY_INVALID` and **no ControlEvaluation row**; 14 pure aggregation vectors |

### F-03: an empty compensation could satisfy an obligation (HIGH)

| | |
|---|---|
| Requirement | ZA-STA-001 §09: compensating_expression is "One or more controls…"; primary_scope is the "Exact control/obligation branch being compensated"; "cannot apply outside approved interval"; "lacks required rationale" → invalid. STA-REQ-013; STA §16 |
| Previous behaviour | `compensating_control_keys: []` was stored as an empty `ALL`, approved, and evaluated as vacuously SATISFIED. Unknown, duplicate or self-referencing keys and a primary absent from the expression were accepted; `valid_to ≤ valid_from` was accepted |
| Correction | `routers/assurance.py` `CompIn`: `min_length=1`. `engines/governance.py` `compensation_problems()` is checked at proposal **and again at approval**: a structurally valid, non-empty compensating expression; no duplicate keys; primary not among the compensating keys; every key a catalogue `ControlDefinition`; a non-empty valid period; the primary a compensable CONTROL leaf of the current expression version. Approval also refuses if the wrapped expression is invalid or unchanged. `engines/assurance.py`: an ALL/ANY/K_OF_N with no binding children, a bad k or an unknown node is a configuration error (409), never vacuous truth |
| Not invented | "Eligible" means an existing catalogue control on an existing expression branch. Whether a compensating control must already be *implemented* for the subject is evaluated per segment (UNKNOWN when it is not); STA §09 states it as "independently evidenced", not as a proposal precondition |
| Evidence | 7 invalid proposal cases (empty, unknown, duplicate, self, unknown primary, primary not in expression, empty period) → 422/409, nothing written. Full lifecycle: propose → approve → expression v2 with the COMPENSATION node → a second compensation of the same branch refused → revoke. A stored empty proposal cannot be approved (422, status unchanged, no new expression version). A stored expression holding an empty compensating branch → preview 409 `SATISFACTION_EXPRESSION_INVALID` |

### F-04: a definition link edit could race publication (MEDIUM, branch-introduced)

| | |
|---|---|
| Requirement | DAT-REQ-012 (released content immutable); the branch's authoring rule: links change only while unreleased |
| Previous behaviour | `add_link`/`remove_link` row-locked the definition, but `publish` read the links unlocked, so a link edit could commit alongside a publication and the released links could differ from the signed binding |
| Correction | `routers/rules.py` `publish` takes `catalog.lock_definition()` before `release_binding()`. `lock_definition` uses `populate_existing` so a stale in-session copy is never signed. The lock order is the same on both paths (definition row, then the record-sequence row); `publish` writes nothing before the lock. A waiting edit sees the release and gets 409 `OBLIGATION_DEFINITION_RELEASED`; a waiting publication binds the committed links |
| Evidence (PostgreSQL) | (a) An in-flight edit holds the lock; publication blocks, then signs the committed links including the new provision, and the binding equals the definition. (b) Publication paused after taking the lock; a concurrent edit blocks, then is refused 409, and the binding equals the definition. Without the fix, (a) signs a stale set and (b) lets the edit commit |

### F-05: concurrent adoption could create two obligations (MEDIUM)

| | |
|---|---|
| Requirement | ZA-DOM-001 §13 (adopted determination → ObligationInstance 1:0..1 per canonical scoped revision); TIM-REQ-021 |
| Previous behaviour | `attest` read the current determination without a lock; there is no partial unique index on current revisions |
| Correction | `engines/locks.py` (new): `xact_lock()`, a PostgreSQL transaction-level advisory lock, which is **database-held** and serialises across processes. `attest` reads the determination, takes the lock on (tenant, subject, obligation definition), then **re-reads** with a fresh snapshot and validates. A second ADOPT that waited sees the committed ADOPTED revision and gets 409. `decide_compensation` uses the same mechanism per obligation, so two approvals cannot both write expression version N+1 |
| Blocked part (R-01) | A constraint-level guarantee needs a partial unique index, which is a schema change (§4) |
| Evidence | PostgreSQL: two concurrent ADOPTs → `[409, "ok"]`, one lineage, one current revision. SQLite: repeated ADOPT → the same result |

### F-06: re-adoption after closure back-dated the obligation (MEDIUM)

| | |
|---|---|
| Requirement | ZA-DOM-001 §9.2; ZA-TIM-001 TIM-REQ-007/008 (corrections preserve history; valid-time changes are explicit) |
| Previous behaviour | Re-activation from CLOSED copied the original `valid_from`, re-claiming the closed interval |
| Correction | `engines/applicability.py` `_activate_obligation`: from CLOSED, the new revision starts at `max(adopted determination valid_from, closed revision valid_to)`. Re-attesting a still-ADOPTED obligation keeps its start. Earlier revisions are never modified; no timestamps are invented |
| Evidence | An engine-boundary test: ADOPTED → CLOSED `[start, closed)` → re-adoption under an earlier-dated determination starts at `closed` (previously `start`); a later determination starts at its own date; earlier revisions are unchanged in record-time history |
| Note | Through the API this transition is currently unreachable (R-02), so the defect was latent |

### F-07: configuration errors became business states (MEDIUM)

| | |
|---|---|
| Requirement | ZA-STA-001 §16 ("errors are not business states"; an invalid SatisfactionExpression "is a release/configuration error and must be blocked before production use"); STA-REQ-022 |
| Correction | `AssuranceConfigurationError` (HTTP 409, `{"error": "ASSURANCE_CONFIGURATION_INVALID", "code": …}`; logged with identifiers only). It is raised for an invalid policy, an invalid expression node (previously HTTP 500 or KeyError), an expression not in force, and an invalid default template at adoption. Raised before any evaluation, snapshot or issue is written. Convenience views (`GET /controls`, `GET /controls/{id}`) show `state: null` plus `configuration_error`, never a state |
| Evidence | 7 invalid-expression cases; the stored-invalid-policy test (no evaluation row; list and detail show the error); empty-compensation and missing-expression previews return 409 with no snapshot |

### F-08: compensation records updated in place (MEDIUM)

| | |
|---|---|
| Requirement | ZA-STA-001 §09 ("Stable logical ID + immutable version"); STA-REQ-021; TIM-REQ-006 |
| Existing convention | `assurance_compensation_revision` is a projection with append-only history in `assurance_compensation_event` (0008). Migration 0010 allows updating only `status` and `approved_by`, and makes REJECTED and REVOKED final |
| Defect fixed in code | The evaluator read the projection's *current* status, so recomputing an earlier record cutoff after a revocation gave a different answer. It now uses the status as of the cutoff from the event history (`_compensation_status_at`). Rows with no history at all (pre-0008, e.g. seeded) fall back to the projection, labelled `PROJECTION_NO_HISTORY` in the trace |
| Blocked | Immutable compensation *versions* and the STA §09 fields (subject scope, conditions/limits, review/expiry) need a schema and revision-model decision, the same convention decision as governance register C2. Not invented here |
| Evidence | After approve → revoke, a preview at the post-approval cutoff shows `APPROVED`, `EVENT_HISTORY`; the current preview shows `REVOKED`, invalid |

### F-09: the expression fallback used a version that was not in force (MEDIUM)

| | |
|---|---|
| Requirement | STA-REQ-021, TIM-REQ-015 (exact pinned dependencies; no latest substitution); STA-REQ-016 |
| Previous behaviour | With no version valid for a segment, the highest version was used anyway |
| Correction | `in_force(a, b)` in `compute_snapshot`: the highest version whose valid period covers the segment, among versions recorded by the cutoff. If there is none, the result is 409 `NO_EXPRESSION_IN_FORCE`. The segment is not excluded, because excluding an applicable segment could let a period read PROVEN (STA-REQ-016). Each chosen version is validated once |
| Evidence | Current, superseded, future-effective and record-perspective versions select correctly; a missing version for an applicable segment → 409; the in-force period still evaluates |

### F-13: smaller defects (LOW)

- `POST /controls`: audit row `control_implementation.create`; a concurrent duplicate returns 409 instead of 500.
- `POST /test-runs`: empty or inverted windows refused (TIM §02); audit row `test_run.record`.
- `POST /risk-acceptances`: empty periods refused; audit row `risk_acceptance.create`.
- `_issue_hooks` dedupe lookup is now tenant-filtered.
- No new state codes were invented: audit `new_state` is set only where it is an existing code (the test result).

Tests: audit rows for creation and test runs; window refusal.

### F-15: GET verification wrote unbounded audit records (LOW, branch-introduced)

| | |
|---|---|
| Requirement | ZA-SEC-001 §18 (key/signing telemetry content: key ID/purpose, caller, operation, result; never key bytes); SEC-REQ-048 (a failure raises a security signal). Neither requires a record per read |
| Correction | `release_verification.record()` records when a result is **new for the caller**: the first verification and every change of outcome, integrity, signature, trust or failed/undecided checks. The result fingerprint is stored in the audit text. An identical repeat writes nothing. Every recorded INVALID raises the signal, so each distinct failure is signalled. Records are serialised per release and principal. The GET contract and body are unchanged (the frontend calls it); a `Za-Verification-Recorded: true/false` header reports the decision |
| Evidence | 5 GETs by a new reader → 1 record (`true`, then `false` ×4) with identical bodies. The existing tamper tests still record INVALID plus the signal each time and record the restoration. The existing authorization and tenant-isolation test is unchanged and passes |

### F-16: citation (INFO)

"ZA-SEC-001 §19" was corrected to §18 in `engines/release_verification.py` and `routers/catalog.py`.

## 3. Findings intentionally not fixed

| ID | Reason | Evidence / owner |
|---|---|---|
| F-10 EVT naming and payloads | Renaming events breaks consumers and webhooks; EVT-REQ-008/021 need the EVT owner's agreement. No event names were changed or added | EVT owner / Nagesh |
| F-11 idempotency keys, problem+json, URI versioning, ETags | Platform-wide API contract (API-REQ-002/004/017/006), outside this ownership | API Lead / Nagesh |
| F-12 DAT-REQ-039 review of 0011–0013 | Another owner's migrations; a review, not code | Nagesh, Data Architect |
| F-14 compensation self-approval | STA §09 says "approved_by / authority … according to policy"; no separation-of-duties rule is stated, so adding one would be a policy assumption. The F-05 lock makes concurrent decisions safe | Head of Assurance (policy) |

## 4. Remaining limitations and approval blockers

| # | Limitation | What is needed |
|---|---|---|
| R-01 | **Constraint-level uniqueness of current revisions.** F-05 is closed with a database-held advisory lock in `attest`; no constraint exists. Paths other than `attest` that revise determinations (`_derive` during evaluation) are not serialised with it. Adding the lock to `_derive` would take several keys in `evaluate-all` and risk deadlocks without an ordering design | Proposed migration (**not created**): `CREATE UNIQUE INDEX … ON assurance_obligation_instance_revision (tenant_id, subject_id, obligation_definition_id) WHERE record_to_seq IS NULL`, and the same on `applicability_determination_revision`. **Data impact:** existing duplicates must be detected first; a pre-check query fails the migration if any exist; no backfill. Needs DAT-REQ-039 review, Data Architect approval and an explicit migration number |
| R-02 | **Re-review of an adopted determination cannot complete through the API** (found during this work; probe on throwaway SQLite). After a fact change, the new evaluation flags the adopted determination (`needs_review`) but keeps the old evaluation. `attest` then refuses "A newer evaluation exists", and a challenge does not help, since it only moves PROPOSED → IN_REVIEW. Closure of an adopted obligation, and therefore F-06's path, is unreachable | An APP-001 decision on how re-review binds the newer evaluation. Not changed here (lifecycle semantics) |
| R-03 | `CUSTOM_VERSIONED_POLICY` is refused | Its constrained AST and governance are not specified for implementation (STA §03) |
| R-04 | Compensation versions and STA §09 scope, conditions and expiry fields | Revision-model and schema decision (governance register C2) |
| R-05 | Governance decisions in the [decision register](regulatory-catalogue-governance-decision-register.md) (B1–B7, K1–K6, R1, C1–C4) | Unchanged; they block production claims, not publication |

## 5. Files changed by this remediation

**Code:**
- `backend/app/engines/locks.py` (new).
- `backend/app/engines/assurance.py`.
- `backend/app/engines/applicability.py`.
- `backend/app/engines/governance.py` (compensation section only).
- `backend/app/engines/catalog.py` (`lock_definition`).
- `backend/app/engines/release_verification.py`.
- `backend/app/routers/assurance.py`.
- `backend/app/routers/rules.py`.
- `backend/app/routers/catalog.py` (verify route).

**Tests:**
- `backend/tests/test_backend_remediation.py` (new, 56 tests).
- `backend/tests/test_release_signature_verification.py`: the repeated-verification test now asserts bounded recording.
- `backend/tests/test_governance_events.py`: the compensation test now uses real seeded controls on an obligation
  whose expression contains the primary. The old non-existent keys `SEC-01`…`SEC-03` are refused by F-03.
- `test_regulatory_traceability.py`, `test_obligation_definition_authoring.py`: skip reasons only (F-01).

**CI and docs:**
- `.github/workflows/ci.yml`: one added gate line, `--require test_backend_remediation=56`.
- This report.

**Not touched:** frontend, sidebar, IAM authority grants, connector certification, migrations, controlled specifications
and governance decisions.

## 6. Tests added (`tests/test_backend_remediation.py`)

| Finding | Tests |
|---|---|
| F-02/F-07 | `test_invalid_control_policies_are_refused_at_the_api` (16 cases), `test_valid_policies_at_the_boundaries_are_accepted_recorded_and_unique`, `test_a_stored_invalid_policy_is_a_configuration_error_never_operating`, `test_control_aggregation_semantics_and_configuration_errors` (14 vectors), `test_invalid_expression_nodes_are_configuration_errors` (7) |
| F-03 | `test_compensation_semantics_follow_sta_section_09`, `test_invalid_compensations_are_refused` (7), `test_compensation_lifecycle_from_proposal_to_approval_evaluation_and_revocation`, `test_a_stored_empty_compensation_can_neither_be_approved_nor_satisfy` |
| F-08 | Inside the lifecycle test (status as of cutoff before and after revocation) |
| F-09 | `test_only_the_expression_version_in_force_is_used`, `test_an_applicable_segment_without_an_expression_in_force_is_a_configuration_error` |
| F-05 | `test_concurrent_or_repeated_adoption_creates_exactly_one_obligation` (concurrent on PostgreSQL, sequential on SQLite) |
| F-06 | `test_readoption_after_closure_never_backdates_over_the_closed_interval` |
| F-13 | `test_test_run_windows_must_be_non_empty_and_runs_are_recorded`, plus audit assertions above |
| F-04 | `test_publication_waits_for_an_in_flight_link_edit_and_signs_the_committed_links`, `test_a_link_edit_waiting_behind_a_publication_is_refused_after_the_release` (PostgreSQL) |
| F-15 | `test_release_signature_verification.py::test_repeated_verification_is_deterministic_and_a_result_is_recorded_once_per_change` |

## 7. Commands and results

All commands run from `backend/`, with `PYTHONDONTWRITEBYTECODE=1 ZA_SKIP_DOTENV=1 ZA_DEMO_MODE=0`. PostgreSQL used a
disposable 17.11 cluster on 127.0.0.1:55450 (scratchpad) with fresh databases `assure_test_rem` and
`assure_test_rem_rls`. Credentials are redacted.

| # | Command | Result |
|---|---|---|
| 1 | `python -m app.migrate status` (canonical, read-only) | `assure@127.0.0.1:5433/assure: at the head revision 0013` |
| 2 | `python -m pytest tests -q -x -p no:cacheprovider` (SQLite, after code changes, before new tests) | 344 passed, 128 skipped |
| 3 | `python -m pytest tests/test_backend_remediation.py tests/test_release_signature_verification.py tests/test_governance_events.py` (SQLite) | 54 passed in the new module, 2 skipped (`PostgreSQL-only`); updated modules pass |
| 4 | `python -m pytest tests -ra --tb=short -p no:cacheprovider --junitxml=…/backend-unit.xml` (SQLite, full) | **398 passed, 130 skipped, 0 failed**, exit 0 |
| 5 | `python ../.github/scripts/junit_gate.py --report …/backend-unit.xml --allow-skip-reason PostgreSQL-only` | **PASS** |
| 6 | `python -m app.migrate status / upgrade / upgrade / status / check` on fresh `assure_test_rem_rls` | exit 1 (EMPTY), 0, 0 (no-op), 0 (0013), 0 (check passed) |
| 7 | `python -m pytest tests/test_rls.py` on `assure_test_rem_rls` + gate `--no-skips --require test_rls=5` | 5 passed; **PASS** |
| 8 | `python -m pytest tests -ra --tb=short -p no:cacheprovider --junitxml=…/backend-postgres.xml` on fresh `assure_test_rem` | **528 passed, 0 skipped, 0 failed** (243 s), exit 0 |
| 9 | Full-suite gate with every `--require` line from `ci.yml` (23, including the new one) and `--no-skips` | **PASS** (for example `test_backend_remediation` 56/56, `test_release_signature_verification` 28/28, `test_governance_events` 10/10, `test_regulatory_traceability` 18/18, `test_obligation_definition_authoring` 12/12) |
| 10 | `python .github/scripts/repo_hygiene.py` (repo root) | **PASS** (251 paths) |
| 11 | Probe `probe_rereview.py` (scratchpad, throwaway SQLite) | Re-attest after an outcome change → 409 "A newer evaluation exists", also after a challenge (R-02) |

The first PostgreSQL full run was stopped by the end of the previous session after RLS had passed. The cluster needed
crash recovery, the database was recreated empty, and run 8 is the complete re-run.

**Not run:**
- Lint, type and static analysis: no backend tool is configured, and none was installed.
- Frontend checks: out of scope.
- GitHub Actions: requires a push or PR.

## 8. Re-audit (read-only) and reconciliation

The pre-push checks were repeated against the remediated tree: git state, diff scope, migrations, hygiene, every CI
gate, and both suites.

| Audit finding | Original | Now | Evidence |
|---|---|---|---|
| F-01 CI unit gate | HIGH | **Resolved** | Gate PASS (run 5) |
| F-02 invalid policy → OPERATING | HIGH | **Resolved** | §2, 31 tests |
| F-03 empty compensation satisfies | HIGH | **Resolved** | §2, 10 tests |
| F-04 link edit vs publish race | MEDIUM | **Resolved** | 2 PostgreSQL concurrency tests |
| F-05 concurrent adoption | MEDIUM | **Resolved (lock); constraint blocked** (R-01) | PostgreSQL concurrency test |
| F-06 re-adoption back-dating | MEDIUM | **Resolved** (latent path, R-02) | Engine test |
| F-07 config errors as states | MEDIUM | **Resolved** | §2 |
| F-08 compensation in place | MEDIUM | **Partly resolved** (historical status); version model blocked (R-04) | Lifecycle test |
| F-09 expression fallback | MEDIUM | **Resolved** | 2 tests |
| F-10 EVT naming | LOW | Open: owner decision | §3 |
| F-11 API platform gaps | LOW | Open: platform owner | §3 |
| F-12 DAT-REQ-039 review 0011–0013 | LOW | Open: other owner | §3 |
| F-13 smaller defects | LOW | **Resolved** | §2 |
| F-14 compensation SoD | LOW | Open: policy decision | §3 |
| F-15 GET verify writes | LOW | **Resolved** | Bounded-recording test |
| F-16 citation | INFO | **Resolved** | — |

**Re-audit checks:**
- No CRITICAL or HIGH finding remains.
- No secret, environment file or database is in the change set (hygiene PASS).
- Migrations 0001–0013 are unmodified, and no migration was added.
- The canonical database was not accessed except for the read-only status check.
- The working tree had 74 status entries before this report was written: the 68 from the audit baseline, the audit
  report, and the 5 paths this remediation added (two new modules and three first-time edits). This report makes 75.
  Nothing is staged.
- The branch is still `naveen-changes` at `73106fe`, uncommitted.
- The publication caveats from the audit (§12) still apply: review the diff by owner, commit only on explicit
  instruction, and get Nagesh's and Vijay's review for their areas.

**Verdict: SAFE TO PUSH WITH WARNINGS.**
