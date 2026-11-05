# Backend pre-push audit: `naveen-changes`

Date: 2026-11-04. An independent audit, AI-assisted, of Naveen's backend responsibilities
(`Zoiko_Assure_Developer_Work_Split.pdf`, Developer 2: "Regulatory & Obligation Core Backend", weeks 1–5). This is
an audit only: no file other than this report was created or changed, nothing was staged, committed, pushed or
stashed, and the canonical `assure` database was not touched.

## 1. Executive verdict

**SAFE TO PUSH WITH WARNINGS**

**Why it is safe to push:**
- No secrets, keys, environment files or databases are in the change set. The CI hygiene script passes, and a scan of
  tracked and untracked files found no credentials.
- No committed migration (0001–0010) is modified. 0011–0013 are new, linear and documented.
- The full backend suite passes on a fresh PostgreSQL 17 database: 472/472, 0 skipped. Every CI `--require` gate
  passes.
- The SQLite suite passes: 344 passed, 0 failed.

**Warnings that must be understood before a push:**
1. **The CI `backend-unit` gate fails as the branch stands** (F-01, HIGH). Two new tests skip on SQLite with a reason
   string the gate does not allow. A pull request will therefore show `ci-success` red. Fix this before opening the PR.
2. **Nothing new is committed.** Every change on the branch is unstaged or untracked. `naveen-changes` is 3 commits
   ahead of `origin/naveen-changes` only because it is at the `main` merge (`73106fe`). Pushing now publishes only
   those merge commits, not this work. The work must first be reviewed and committed on explicit instruction.
3. **Two HIGH assurance-integrity defects exist in Naveen's scope** (F-02, F-03). Both were proven by execution. Both
   are **already on `main`**, not introduced by this branch, so pushing does not make them worse. They must be fixed
   before any production or Customer Zero use.
4. **The branch mixes ownership areas.** It contains IAM authority-grant work (migration 0011), connector certification
   (0012/0013), sidebar/IAM routers and frontend admin pages. These belong to Nagesh's and Vijay's areas in the work
   split. They need those owners' review, and DAT-REQ-039 review of 0011–0013, before merge.

Publication readiness is not production readiness. Release trust stays UNVERIFIABLE by design, database-enforced
catalogue immutability is pending (0014), and governance decisions remain open (§10).

## 2. Audited branch and comparison baseline

| Item | Value |
|---|---|
| Branch | `naveen-changes`, tip `73106fe` (= `origin/main`, "Merge pull request #4 from ZoikoGroup/vijay-changes") |
| Upstream | `origin/naveen-changes` at `f5220e9`; local is ahead by 3 commits, behind by 0. A push would fast-forward; no force is needed |
| Intended target | `main` (`origin/main` = `73106fe`; merge-base = `73106fe`) |
| Staged | **None** (`git diff --cached` is empty) |
| Unstaged modified | 35 tracked files (+1,913 / −268 lines) |
| Untracked | 33 paths: 4 backend modules, 3 migrations, 10 test modules, 16 docs (including `docs/governance/`), and `frontend/lib/screen-access.json` |
| Ignored, present locally, correctly excluded | `backend/.env`, `backend/var/` (keys), `backend/.venv/`, `frontend/.env.*.local`, `frontend/.next/`, `node_modules/`, `__pycache__/`, `.pytest_cache/` |
| Line endings | Git warns that LF will become CRLF in `backend/app/routers/catalog.py` and two frontend pages; cosmetic |

## 3. Scope and changed-file inventory

**In scope (Naveen):** regulatory catalogue and traceability; ObligationInstance lifecycle; applicability and adoption
integration; control mappings; SatisfactionExpression; governed test-result APIs; issues, exceptions and compensations;
historical revisions. In scope here means both the branch changes and the existing in-scope code on `main`.

| Area | Files | Ownership (work split) | Audited |
|---|---|---|---|
| Regulatory catalogue / traceability | `app/engines/catalog.py` (new), `app/engines/release_verification.py` (new), `app/routers/catalog.py`, `app/routers/rules.py`, `app/engines/keys.py` (read-only accessors) | Naveen | Full |
| Applicability / obligation provenance | `app/routers/applicability.py`, `app/routers/assurance.py` (additive `regulatory_provenance`) | Naveen | Full |
| Obligation, control, test, exception, compensation, issue engines (pre-existing) | `app/engines/assurance.py`, `app/engines/applicability.py`, `app/engines/governance.py` §1–6, `app/routers/assurance.py` | Naveen | Full |
| Tests (in scope) | `test_regulatory_traceability.py`, `test_release_signature_verification.py`, `test_obligation_definition_authoring.py`; updated `test_authorization.py`, `test_role_registry.py`, `test_dat_req_039_review.py`, `read_authorization_matrix.json` | Naveen | Full |
| Agent authority grants | `governance.py` §7, `models.py`, `routers/ai.py`, migration 0011, `test_authority_grant_events.py` | Nagesh (IAM) | Inventory, hygiene, tests only |
| Connector certification | `engines/connectors.py`, `routers/integration.py`, `models.py`, migrations 0012/0013, two test modules | Nagesh | Inventory, hygiene, tests only |
| Sidebar / IAM / platform / dashboard routers, `pagination.py` | `routers/core.py`, `iam.py`, `platform.py`, `dashboard.py`, `pagination.py`, `test_sidebar_integration.py` | Shared / Nagesh | Inventory; cursor signing checked |
| Dev auto-migration | `app/devmigrate.py`, `app/main.py`, `.env.example`, `test_dev_auto_migration.py` | Platform | Safety checked (§7) |
| Frontend | 10 files plus `lib/screen-access.json` | Vijay | **Not audited** (out of scope) |
| CI / docs | `.github/workflows/ci.yml`, `docs/*` | Shared | CI fully; docs for consistency |

## 4. Controlled-specification compliance matrix

Legend: ✅ conforms · ⚠️ partial · ❌ gap · ⏸ pending a governance decision. "Pre-existing" means the finding is already
on `main`.

| Requirement | Spec requires | Code does | Status | Finding |
|---|---|---|---|---|
| DOM §6.1, §13 | ObligationDefinition derives from one or more provision versions; Regulatory Content owns | Authoring requires ≥1 link to human-controlled provisions; platform principal + `content_author` | ✅ / ⏸ (role interpretation) | §10 |
| DOM-INV-03; GLS-REQ-007 | UNCERTAIN cannot be adopted or coerced | `attest` refuses UNCERTAIN (409); no obligation is created | ✅ | — |
| DOM §13 | One adopted determination → 0..1 obligation per canonical scoped revision | Reuses lineage sequentially; **no lock or unique index for concurrent attestation** | ⚠️ | F-05 |
| DOM §9.2; TIM-REQ-007 | Corrections preserve prior knowledge; valid periods accurate | Re-adoption after closure reuses the original `valid_from` | ⚠️ | F-06 |
| GLS §04 | Content lifecycle transitions | `catalog.TRANSITIONS` encodes exactly the GLS §04 table | ✅ | — |
| GLS-REQ-001; DAT-REQ-025 | Stored controlled codes come from an approved dictionary | `aggregation_mode`, `automation_requirement`, `method` accept any string; schedule codes are not in GLS | ❌ / ⏸ | F-02, §10 |
| STA-REQ-006 | Missing or unevaluated required proof MUST NOT yield OPERATING | `K_OF_N_TESTS` with a negative `k_threshold` yields OPERATING with no evidence or all tests failing | ❌ pre-existing | **F-02** |
| STA-REQ-025 | Automation-required control is not OPERATING from manual evidence alone | Holds only when `automation_requirement == "REQUIRED"` exactly; unvalidated values bypass it | ⚠️ pre-existing | F-02 |
| STA-REQ-008 | External tool status is input, never authoritative state | `POST /test-runs` stores TestRun inputs; state is derived | ✅ | — |
| STA-REQ-010/011/012 | CONTROL / ALL / ANY / K_OF_N / COMPENSATION with four-value semantics | `combine_all/any/k` and the COMPENSATION node match the STA tables | ✅ | — |
| STA §09; STA-REQ-013 | A compensating expression is "one or more controls"; compensation is versioned, scoped, independently evidenced | API accepts an empty list → empty `ALL` → **vacuously SATISFIED**; keys unvalidated; the row is updated in place | ❌ pre-existing | **F-03**, F-08 |
| STA §16 ("Invalid SatisfactionExpression … blocked before production use"); STA-REQ-022 | Configuration errors are not business states | `k_threshold > n` gives false FAILING; an unknown mode silently becomes ALL_REQUIRED | ❌ pre-existing | F-07 |
| STA-REQ-014; STA-REQ-024 | Exceptions and risk acceptance never alter state | Reported as treatments only; tests cover this | ✅ | — |
| STA-REQ-019/021; TIM-REQ-015 | Immutable snapshots pinning exact dependencies | Snapshots pinned; **but the expression fallback picks the latest version even outside its validity** | ⚠️ pre-existing | F-09 |
| STA-REQ-023 | Issue closure does not change assurance | Issue transitions are governance-only | ✅ | — |
| STA-REQ-027 | Missed occurrences never overwritten | Fulfilment is a new event; a missed occurrence stays missed | ✅ | — |
| DAT-REQ-012 | PUBLISHED catalogue immutable | API-enforced for sources, requirements, provisions and definitions; DB-enforced for rule versions and releases (0004) | ⚠️ ⏸ (0014 / C1) | §10 |
| DAT-REQ-026 | Exact revision references | Releases bind requirement versions and provisions; obligations reference the definition by logical ID | ⚠️ ⏸ (B1-c/d) | §10 |
| DAT-REQ-039 | Security/data/QA review before applying a revision outside development | 0011–0013 docstrings require it; review report exists for earlier revisions | ⏸ | F-12 |
| API-REQ-004/005 | Idempotency key on every externally reachable mutation | Not implemented for any in-scope mutation (only evidence upload) | ❌ pre-existing, platform-wide | F-11 |
| API-REQ-006 | Optimistic concurrency where lost update matters | Link changes serialised by row lock; no ETag/If-Match | ⚠️ | F-11 |
| API-REQ-014 | Unknown fields refused; no mass assignment of server fields | `extra="forbid"` on every in-scope model | ✅ | — |
| API-REQ-008 | Tenant from authenticated context | `ctx.tenant` / `ctx.scoped`; RLS proofs pass | ✅ | — |
| API-REQ-017 | `application/problem+json` errors with stable codes | FastAPI `{"detail": …}`; mixed string and dict details | ❌ pre-existing, platform-wide | F-11 |
| TIM-REQ-003; DOC-CON-003 | No implicit date→instant conversion | Assurance uses an *explicit* "UTC start-of-day" policy (stated in every response) | ⏸ | §10 |
| TIM-REQ-005 | Monotonic record sequence | `next_record_seq` on every write path audited | ✅ | — |
| EVT-REQ-001 | Domain events via the outbox in the same transaction | `emit()` in the same session for all in-scope mutations | ✅ | — |
| EVT-REQ-008 | Every event type registered with one owner and schema | Several in-scope names diverge from the EVT catalogue; catalogue events missing | ❌ pre-existing | F-10 |

## 5. Functional findings

Each finding states **Spec requires / Code does / Gap / Recommendation**. "Proven" means reproduced by running code in a
scratch probe (§8.3).

**F-02: K_OF_N_TESTS with an invalid threshold makes a control OPERATING without proof (HIGH, proven, pre-existing)**
- **Spec requires:** STA-REQ-006: missing or unevaluated required proof MUST NOT yield OPERATING. DAT-REQ-025:
  controlled codes.
- **Code does:**
  - `POST /api/controls` (`routers/assurance.py:313`) accepts any `aggregation_mode`, any integer `k_threshold`
    (including negative values) and any `automation_requirement`. Probe P2 returned 200.
  - In `_aggregate` (`engines/assurance.py:216-227`), `k = policy.k_threshold or len(req)`, so k = −5 makes
    `ok >= k` always true.
  - Proven: "k=−5, no test evidence → OPERATING"; "k=−5, both required tests FAIL → OPERATING".
- **Gap:** any holder of `manage_controls` (a tenant control owner) can configure a control that is always OPERATING
  and therefore feeds PROVEN. In addition, `automation_requirement` other than the exact string `REQUIRED` disables
  the STA-REQ-025 manual-evidence check.
- **Recommendation:** validate in `ControlIn`:
  - `aggregation_mode ∈ {ALL_REQUIRED, PRIMARY_PLUS_CORROBORATION, K_OF_N_TESTS}`;
  - `k_threshold` required iff `K_OF_N_TESTS`, with `1 ≤ k ≤ number of required tests`;
  - `automation_requirement` from a closed set.

  Defensively, make `_aggregate` raise a configuration error (not a business state) for an out-of-range k or an
  unknown mode. Add tests for each.

**F-03: An approved compensation with an empty compensating expression satisfies the obligation (HIGH, proven, pre-existing)**
- **Spec requires:**
  - STA §09: compensating_expression is "One or more controls expressed through normal SatisfactionExpression nodes".
  - STA-REQ-013: compensations are "independently evidenced".
  - STA §16: an invalid SatisfactionExpression "must be blocked before production use".
- **Code does:**
  - `POST /api/compensations` (`routers/assurance.py:445-458`) accepts `compensating_control_keys: []` and stores
    `{"node_type": "ALL", "children": []}`. Probe P1 returned 200.
  - Approval wraps it into a new expression version (`governance._wrap_comp_node`).
  - `combine_all([])` returns SATISFIED. Proven: a FAILING primary plus the approved empty compensation evaluates to
    SATISFIED with `satisfied_via_compensation=True`.
  - `primary_control_key` and the compensating keys are not checked against `ControlDefinition` or against the current
    expression. A nonexistent primary key (probe P1 used `"X"`) is accepted, and approval then produces an unchanged
    expression under a "Compensation …" rationale.
  - `valid_to > valid_from` is not checked.
- **Gap:** a governed approval (RISK_APPROVER, human) is still required, but the approver sees an approvable record
  that yields vacuous proof.
- **Recommendation:**
  - In `CompIn`, require `min_length=1` and require that each key exists in `ControlDefinition`.
  - Require `primary_control_key` to appear as a CONTROL leaf of the current expression version, and require
    `valid_to > valid_from` when given.
  - In `eval_expression`, treat an ALL/ANY/K_OF_N node with no binding children as a configuration error, mirroring
    `catalog.validate_template`'s "needs at least one child".

**F-05: Concurrent adoption can create two obligation lineages (MEDIUM, by inspection, not reproduced, pre-existing)**
- **Spec requires:**
  - DOM §13: adopted determination → ObligationInstance 1:0..1 per canonical scoped revision.
  - API-REQ-004: idempotency keys.
  - TIM-REQ-021: reject unintended overlapping current revisions.
- **Code does:**
  - `applicability.attest` reads the current determination without `FOR UPDATE`.
  - `_activate_obligation` finds the current obligation by `(tenant, subject, definition, record_to_seq IS NULL)`
    without a lock.
  - No partial unique index enforces one current revision for determinations or obligation instances (none in
    0001–0013).
- **Gap:** two concurrent ADOPT requests (double submit, retried request) can both pass the PROPOSED check and both
  create lineages. Sequential re-adoption is correct.
- **Recommendation:**
  - Lock the current determination row (`with_for_update()`) at the start of `attest`, and lock or upsert the current
    obligation in `_activate_obligation`.
  - Add a partial unique index `(tenant_id, subject_id, obligation_definition_id) WHERE record_to_seq IS NULL` in a
    future reviewed migration. This is schema work requiring DAT review; none is created here.
  - Add a PostgreSQL concurrency test like those in `test_regulatory_traceability.py`.

**F-06: Re-adoption after closure back-dates the obligation (MEDIUM, by inspection, pre-existing)**
- **Spec requires:** DOM §9.2 / TIM-REQ-007/008: valid-time changes are explicit; history is preserved and not
  silently re-carved.
- **Code does:**
  - `_close_obligation` writes a CLOSED revision `[valid_from, at)`.
  - A later `_activate_obligation` reuses the lineage with `valid_from=cur.valid_from`: the *original* start, taken
    from the CLOSED revision.
- **Gap:** current knowledge then says the obligation applied continuously, including the closed interval. Snapshots
  over that interval would treat the obligation as applicable.
- **Recommendation:** on re-activation from CLOSED, start the new revision at the re-adopted determination's
  `valid_from` (or `at`), not the closed revision's start. Add a regression test: adopt → DOES_NOT_APPLY adoption →
  APPLIES adoption, then check that the gap stays excluded.

**F-07: Configuration errors become business states (MEDIUM, proven, pre-existing)**
- **Spec requires:** STA §16 (errors are not business states; invalid expressions are blocked); STA-REQ-022.
- **Code does:** `K_OF_N_TESTS` with k > n returns FAILING even when every test passes (proven). An unknown
  `aggregation_mode` falls through to ALL_REQUIRED without error. `eval_expression` raises HTTP 500 for an unknown node
  type, which is correct, but a missing `k` raises an unhandled KeyError.
- **Recommendation:** covered by the F-02 validation. Also validate stored expression ASTs at
  `SatisfactionExpressionVersion` creation (adoption default template, compensation wrap) with the same validator as
  `catalog.validate_template`, extended for COMPENSATION.

**F-08: Compensation is updated in place (MEDIUM, pre-existing; partly mitigated)**
- **Spec requires:** STA §09: "compensation_id / version: Stable logical ID + immutable version"; also subject scope,
  conditions/limits and review/expiry fields; STA §16 lists CompensationVersion as a "Bitemporal governed revision".
- **Code does:** `assurance_compensation_revision` rows are mutated (`status`, `approved_by`). The append-only
  `assurance_compensation_event` history (0008) records each transition. No subject-scope, conditions or expiry-review
  fields exist.
- **Recommendation:** treat this as a design item. Either keep the event table as the authoritative history and record
  that decision, or move to immutable versions. This is the same event-table convention decision as C2 in the
  governance register.

**F-09: The expression-version fallback uses a version outside its validity (MEDIUM, pre-existing)**
- **Spec requires:** STA-REQ-021 / TIM-REQ-015: exact pinned dependencies; no "latest" substitution.
- **Code does:** `compute_snapshot` (`engines/assurance.py:463-466`) falls back to the highest version when no version
  covers the segment.
- **Recommendation:** when no expression version is valid for a segment, mark the segment excluded or NOT_PROVEN with
  an explicit reason (for example `EXPRESSION_NOT_IN_FORCE`). Don't evaluate it under a version that was not in force.
  Add a golden-vector test. This changes the roll-up, so per STA-REQ-032 it needs ADR and golden-vector review.

**F-04: A definition link change can race a rule publication (MEDIUM, by inspection, introduced by this branch)**
- **Spec requires:** DAT-REQ-012 (released content immutable). The authoring rule this branch introduces: links
  change only while the definition is unreleased.
- **Code does:**
  - `catalog.add_link` / `remove_link` lock the definition row (`lock_definition`) and check `is_released`.
  - `rules.publish` reads `odef.provision_ids` through `catalog.release_binding` **without** that lock.
  - Under READ COMMITTED a link change can pass `is_released` before the publication commits. Both then commit, and
    the definition's links differ from the signed requirement binding.
- **Recommendation:** in `routers/rules.py` `publish`, call `catalog.lock_definition(ctx.db, d.obligation_definition_id)`
  before `catalog.release_binding(...)`. Add a PostgreSQL concurrency test (link vs publish).

**F-10: Event names and payloads diverge from the EVT catalogue (LOW, pre-existing)**
- **Spec requires:** EVT-REQ-008 (registered event types); EVT §05 catalogue; STA §16 minimum payloads.
- **Code does:**

  | Code emits | EVT catalogue |
  |---|---|
  | `obligation.adopted` | `obligation.activated` |
  | `obligation.occurrence_scheduled` / `_fulfilled` | `obligation.occurrence_created` / `_completed` |
  | `assurance.issue_created` / `assurance.issue_transitioned` | `issue.created` / `issue.closed` |
  | (none) | `determination.rejected`, `obligation.revised` (on closure), `control.state_changed`, `compensation.expired`, `exception.expired` |

  - Uncatalogued events emitted: `compensation.submitted`, `compensation.rejected`, `exception.requested`,
    `test.run_recorded`.
  - The `control.evaluated` payload lacks window, record cutoff and subject/control revision.
  - The `compensation.*` payload lacks version, valid range and authority.
- **Recommendation:** align the names with the catalogue, or register the variants with the EVT owner. This is
  coordinated work (consumers and webhooks), so it needs owner agreement before renaming.

**F-11: API contract gaps (LOW, pre-existing, platform-wide)**
- API-REQ-004/005: no idempotency keys on mutations.
- API-REQ-017: no problem+json.
- API-REQ-002: no version segment in the URI.
- API-REQ-006: no ETag/If-Match on revisioned resources.
- **Recommendation:** a platform decision (Nagesh/API Lead). Track it in the roadmap rather than per endpoint.

**F-13: Smaller defects (LOW, pre-existing)**
- `POST /api/controls` writes no audit row and no outbox event.
- A race on the unique constraint returns 500 instead of 409.
- `POST /api/test-runs` does not validate `window_to > window_from` and writes no audit row.
- `POST /api/risk-acceptances` writes no audit row or event.
- The `Issue` dedupe lookup in `_issue_hooks` is not tenant-filtered. It is safe under PostgreSQL RLS, and keys contain
  lineage UUIDs.
- **Recommendation:** add `audit()` and `emit()` to the three mutations, catch `IntegrityError` → 409, validate test-run
  windows, and add `Issue.tenant_id == tenant_id` to the dedupe query.

## 6. Security and authorization findings

| # | Severity | Finding | Remediation |
|---|---|---|---|
| S-1 | ✅ | Tenant isolation: PostgreSQL RLS proofs 5/5, tenant-security 28/28, read-authorization matrix and role registry pass on a fresh PG17 database | — |
| S-2 | ✅ | Catalogue mutations require `content_author` + platform principal; requirement verification requires a human `content_legal_review`; exception and compensation decisions require human approvers; exception self-approval is blocked (`governance.py:104`) | — |
| S-3 | ✅ | No SQL built from request input; f-string SQL exists only in `migrate.py`, with quoted identifiers; no pickle, YAML or eval; cursor tokens HMAC-signed with `compare_digest` (`pagination.py`) | — |
| S-4 | ✅ | Responses expose public keys only. The A9b verifier never mints a key (`keys.existing_public_key`). The audit text carries key ID and purpose, never key bytes | — |
| **F-12** | LOW / process | Migrations 0011–0013 state that DAT-REQ-039 security/data/QA review is required before they are applied outside development. The work is outside Naveen's ownership | Nagesh's and the Data Architect's review before merge |
| **F-14** | LOW | Compensation self-approval is not blocked, unlike exceptions. It is latent: RISK_APPROVER, the only role with `approve_compensation`, lacks `request_exception`, so an actor needs both roles. STA §09 requires a "named … authority according to policy" but no explicit separation-of-duties rule, so **this is an engineering recommendation, not a spec mandate** | Add a `proposer != approver` check mirroring `decide_exception`; record `proposed_by` |
| **F-15** | LOW | `GET /api/catalog/releases/{id}/verify` writes: an audit row per call (probe P4: 3 calls → 3 rows), and a `security.content_signature_failed` event when INVALID. Any catalogue reader can generate unbounded audit and security records. A safe-method GET with side effects also fails under a read-only database role | Make verification `POST /catalog/releases/{id}/verifications` (an explicit action), or rate-limit and deduplicate per release, actor and outcome. Owner decision |
| F-16 | INFO | The docstrings in `release_verification.record` and the catalogue verify route cite "ZA-SEC-001 §19"; the key-logging text is §18 | Correct the citation |

## 7. Database and migration findings

| Check | Result |
|---|---|
| Migrations 0001–0010 unchanged | ✅ `git status` shows no tracked change under `backend/migrations/versions/` |
| New revisions | 0011 (`down_revision 0010`), 0012 (`0011`), 0013 (`0012`): linear, single head |
| Fresh-database upgrade | ✅ On disposable `assure_test_audit_rls`: `status` → exit 1 (EMPTY, as CI expects), `upgrade` → 0013, second `upgrade` → no-op, `status` → 0013, `check` → passed |
| Upgrade/downgrade, adoption, refusal, rollback | ✅ `test_migrations` 16/16 on PG17 (throwaway schemas) |
| In-scope schema changes | **None.** The catalogue, traceability, A9b and authoring work adds no migration and uses existing tables |
| Constraints in scope | `ControlImplementation` unique `(tenant, definition, subject, variant)` ✅. **Missing:** one-current-revision uniqueness for determinations and obligation instances (F-05) |
| Automatic migration of `assure` | ✅ Not possible. `devmigrate` requires `ZA_ENVIRONMENT=development`, opt-in `ZA_AUTO_MIGRATE`, an allowlisted loopback `host:port/db`, a database-owner approval comment, and refuses the name `assure` on every host. The API process never holds the migration credential |
| Credentials or env files in the change set | ✅ None. `.env.example` has placeholders only; `backend/.env` and `backend/var/` are ignored |
| Canonical `assure` database | ✅ Not accessed. All runs used a separate scratch cluster (127.0.0.1:55450) and fresh databases `assure_test_audit` and `assure_test_audit_rls` |

## 8. Test and CI results

### 8.1 Commands and results (credentials redacted; all outputs written to the session scratchpad)

| # | Command (from `backend/`) | Result |
|---|---|---|
| 1 | `PYTHONDONTWRITEBYTECODE=1 ZA_SKIP_DOTENV=1 ZA_DEMO_MODE=0 .venv/Scripts/python.exe -m pytest tests -ra --tb=short -p no:cacheprovider -q --junitxml=<scratch>/backend-unit.xml` (SQLite) | **344 passed, 128 skipped, 0 failed** (86.2 s), exit 0 |
| 2 | `python ../.github/scripts/junit_gate.py --report <scratch>/backend-unit.xml --label "Backend unit (no PostgreSQL)" --allow-skip-reason PostgreSQL-only` | **FAIL**: "4 test(s) skipped for an unexpected reason" (F-01) |
| 3 | `python -m app.migrate status / upgrade / upgrade / status / check` on fresh `assure_test_audit_rls` | **PASS** (exit 1 then 0, 0, 0, 0 as CI expects) |
| 4 | `python -m pytest tests/test_rls.py -ra --tb=short -p no:cacheprovider` on `assure_test_audit_rls` | **5 passed**; gate `--no-skips --require test_rls=5` **PASS** |
| 5 | `python -m pytest tests -ra --tb=short -p no:cacheprovider` on fresh `assure_test_audit` (PG 17.11) | **472 passed, 0 skipped, 0 failed** (172.6 s), exit 0 |
| 6 | CI full-suite gate with all 22 `--require` lines from `ci.yml` and `--no-skips` | **PASS** (for example `test_obligation_definition_authoring` 12/12, `test_regulatory_traceability` 18/18, `test_release_signature_verification` 28/28, `test_migrations` 16/16, `test_tenant_security` 28/28) |
| 7 | `python .github/scripts/repo_hygiene.py` (from repo root; scans tracked and untracked files) | **PASS** (248 paths, 245 text files) |

### 8.2 Status by category

| Category | Status |
|---|---|
| Backend unit / API integration / authorization / regression | **Passed** on both PostgreSQL and SQLite |
| RLS on fresh PostgreSQL 17 | **Passed** (5/5) |
| Concurrency / idempotency tests (PostgreSQL) | **Passed** where they exist: traceability 2, authoring 2, role registry, tenant security. **Skipped on SQLite** by design. No concurrency test covers attestation (F-05) or link vs publish (F-04) |
| CI `backend-unit` gate | **Failed** (F-01) |
| Type, lint and static analysis | **Not run**: no backend lint, type or security-scan tool is installed or configured, and CI has none. Nothing was installed during the audit |
| Frontend `tsc` / `next build` / `npm audit` | **Not run**: out of scope |
| GitHub Actions | **Not run**: CI triggers on `pull_request` and on pushes to `main`/`develop`/`release/**` only; a push of `naveen-changes` alone runs no CI |
| Warnings | `StarletteDeprecationWarning` (httpx TestClient), not a failure |

### 8.3 Behaviour probes (scratch scripts outside the repo, run on throwaway SQLite or as pure functions)

| Probe | Result |
|---|---|
| P1: `POST /api/compensations` with `compensating_control_keys: []` and primary key `"X"` | 200, stored (F-03) |
| P2: `POST /api/controls` with `aggregation_mode="NOT_A_MODE"`, `k_threshold=-5`, `automation_requirement="WHATEVER"` | 200, stored (F-02) |
| P3: proposer approving own compensation | Inconclusive: the proposer lacked `approve_compensation` (F-14 is latent) |
| P4: 3× `GET /api/catalog/releases/{id}/verify` | Owner-visible audit rows 18 → 21 (F-15) |
| P5: `eval_expression` COMPENSATION with empty compensating `ALL` | FAILING primary → SATISFIED, `satisfied_via_compensation=True` (F-03) |
| P6: `_aggregate` `K_OF_N_TESTS` | k=−5 → OPERATING with no evidence and with all tests failing; k=3 > n=2 → FAILING with all passing (F-02, F-07) |

## 9. Findings by severity

| Severity | ID | Title | Introduced by branch? |
|---|---|---|---|
| CRITICAL | — | None | — |
| HIGH | F-01 | CI `backend-unit` gate fails on two new skip messages | **Yes** |
| HIGH | F-02 | Invalid `K_OF_N_TESTS` threshold or mode yields OPERATING without proof | No (on `main`) |
| HIGH | F-03 | Empty compensation expression yields vacuous SATISFIED | No (on `main`) |
| MEDIUM | F-04 | Definition link change can race publication | **Yes** |
| MEDIUM | F-05 | Concurrent adoption can create duplicate obligation lineages | No |
| MEDIUM | F-06 | Re-adoption after closure back-dates the obligation | No |
| MEDIUM | F-07 | Configuration errors become business states | No |
| MEDIUM | F-08 | Compensation updated in place; missing STA §09 fields | No |
| MEDIUM | F-09 | Expression fallback uses a version outside its validity | No |
| LOW | F-10 | EVT catalogue name and payload divergence | No |
| LOW | F-11 | API-001 idempotency, problem+json, versioning, ETag gaps | No (platform-wide) |
| LOW | F-12 | 0011–0013 need DAT-REQ-039 review; outside Naveen's ownership | Yes (process) |
| LOW | F-13 | Missing audit/events, window validation, IntegrityError → 500, untenanted dedupe | No |
| LOW | F-14 | Compensation self-approval not blocked (recommendation) | No |
| LOW | F-15 | GET verify route has write side effects | **Yes** |
| INFO | F-16 | SEC §19 → §18 citation in docstrings | **Yes** |

## 10. Unresolved ZA specification decisions

These are recorded in [regulatory-catalogue-governance-decision-register.md](regulatory-catalogue-governance-decision-register.md)
and [governance/specification-gap-resolution-report.md](governance/specification-gap-resolution-report.md). **None
blocks publication of the branch.** They block production claims.

| Decision | Effect on this branch |
|---|---|
| B2: `content_author` + platform principal as the authoring permission | Interim interpretation in code; needs IAM / Regulatory Content ratification |
| B1-a…d, B5: obligation-definition versioning | Released definitions refuse change (409); no revision model |
| K1–K6, R1: release signature trust; `content_sha256` | Verification never reports VALID; intact releases are UNVERIFIABLE |
| C1/C2/C3: 0014 scope and DB-enforced catalogue immutability | Sources, requirements and provisions are immutable at the API only |
| B4: GLS schedule codes | `schedule_kind` uses three uncontrolled values |
| DOC-CON-003 / TIM-REQ-003 | Assurance converts legal dates at UTC start-of-day under an explicit, declared policy |
| Seed defects | 6 unresolved source mappings, 5 ungrounded mapping records, 4 unbound releases, reported by `/catalog/traceability/validation` |

## 11. Exact remediation for each finding

| ID | Remediation (file: change) |
|---|---|
| F-01 | `backend/tests/test_regulatory_traceability.py:640` and `backend/tests/test_obligation_definition_authoring.py:307`: change the skip reason to exactly `"PostgreSQL-only"` (move the detail into a comment), **or** add `--allow-skip-reason` lines for the two exact strings in `.github/workflows/ci.yml`'s `backend-unit` gate. The first keeps one convention. Re-run the gate locally |
| F-02 | `backend/app/routers/assurance.py` `ControlIn`: closed sets for `aggregation_mode` and `automation_requirement`; a validator requiring `1 ≤ k_threshold ≤ count(required tests)` iff `K_OF_N_TESTS`. `backend/app/engines/assurance.py` `_aggregate`: raise a configuration error for an out-of-range k or an unknown mode. Tests for k ≤ 0, k > n, unknown mode, unknown automation requirement |
| F-03 | `routers/assurance.py` `CompIn`: `compensating_control_keys` `min_length=1`; keys must exist in `ControlDefinition`; `primary_control_key` must be a CONTROL leaf of the current expression; `valid_to > valid_from`. `engines/assurance.py` `eval_expression`: ALL/ANY/K_OF_N with no binding children is a configuration error. Regression test: empty compensation is refused, and cannot satisfy if stored by older data |
| F-04 | `routers/rules.py` `publish`: `catalog.lock_definition(ctx.db, d.obligation_definition_id)` before `catalog.release_binding(...)`. PG concurrency test: link vs publish |
| F-05 | `engines/applicability.py` `attest`: `.with_for_update()` on the current determination query; lock the current obligation in `_activate_obligation`. Later reviewed migration: partial unique indexes on current revisions. PG concurrency test: double ADOPT → one lineage |
| F-06 | `engines/applicability.py` `_activate_obligation`: when `cur.lifecycle == "CLOSED"`, use the new determination's `valid_from`, not `cur.valid_from`. Regression test as described in F-06 |
| F-07 | As F-02, plus one shared AST validator used for adoption default templates, compensation wraps and catalogue templates |
| F-08 | Record the decision (event-table history versus immutable versions) under governance C2; add scope, conditions and expiry fields after the decision |
| F-09 | `engines/assurance.py:463-466`: no fallback; exclude the segment or give it an explicit reason. ADR plus golden vectors (STA-REQ-032) |
| F-10 | Agree names and payloads with the EVT owner; then rename or register, emit the missing catalogue events, and extend the payloads |
| F-11 | Platform roadmap items: an idempotency-key middleware using `platform_idempotency_record`, a problem+json exception handler, URI versioning, ETags |
| F-12 | Nagesh's review and DAT-REQ-039 review records for 0011–0013 before merge |
| F-13 | Add `audit()` and `emit()` to `create_control`, `ingest_test_run` and `accept_risk`; `IntegrityError` → 409; validate test-run windows; add a tenant filter to the `_issue_hooks` dedupe |
| F-14 | `engines/governance.py` `decide_compensation`: refuse APPROVE when the proposer is the approver (store `proposed_by`) |
| F-15 | Replace with a POST action, or deduplicate and rate-limit verification records; owner decision |
| F-16 | Correct "ZA-SEC-001 §19" to "§18" in `release_verification.py` and the catalogue route docstring |

## 12. Final GitHub publication recommendation

**SAFE TO PUSH WITH WARNINGS.**

1. **Before committing:**
   - Review the diff by ownership area.
   - Consider splitting the out-of-scope IAM, connector, sidebar and frontend work into commits or PRs owned by Nagesh
     and Vijay (work split: "No unrelated files are changed").
2. **Before opening the PR:** fix **F-01**, or `ci-success` will fail. Then fix **F-04**, which this branch introduced.
3. **Before any production or Customer Zero assurance claim:**
   - Fix **F-02** and **F-03**. They pre-date this branch and should be prioritised in Naveen's Week 5 hardening.
   - Then fix F-05 to F-07.
4. **The branch is safe to publish independently of merge or deployment:**
   - It contains no secrets and modifies no existing migration.
   - It cannot migrate the canonical database.
   - Every test passes on PostgreSQL 17.
5. **Merging and deploying are separate decisions.** They depend on the reviews above, DAT-REQ-039 for 0011–0013, and
   the governance decisions in §10.

This audit made no changes to the branch or the workspace beyond creating this report. The working tree has the same
68 status entries as at the start, plus this file (untracked), and nothing is staged. I'm waiting for explicit
instructions.
