# Final pre-push audit: `naveen-changes`

Date: 2026-11-05. A strictly read-only audit of Naveen's backend modules (regulatory catalogue, rules, applicability
and obligation generation, ObligationInstance lifecycle, control mappings and SatisfactionExpressions, governed test
results, issues, exceptions, compensations). Baseline:
[backend-pre-push-audit-report.md](backend-pre-push-audit-report.md) and
[backend-remediation-report.md](backend-remediation-report.md).

No source file was edited. Nothing was staged, committed, pushed, pulled, stashed, reset, restored or cleaned. No
migration command was run against any database. The PostgreSQL tests built their schema in fresh scratch databases
through the test fixture, as CI does. The canonical `assure` database received one read-only status query. The only
file created in the repository is this report. Probe scripts live in the session scratchpad.

## Verdict: SAFE TO PUSH WITH WARNINGS

- **Every remediation claim is present in the code.** Both suites pass on this tree: PostgreSQL 17 528/528 with 0
  skipped; SQLite 398 passed with 130 `PostgreSQL-only` skips.
- **All three CI gates pass locally.**
- **The change set is clean:** no secrets, no accidental files, no modified migration, no canonical-database change.
- **Both concurrency protections were proven load-bearing** by negative controls on PostgreSQL.

**Warnings that remain:**
- **W-1:** the committed F-05 regression test does not detect removal of its lock. The code is right; the test is
  timing-dependent.
- **R-01, R-02:** still open.
- **External dependencies:** F-10, F-11, F-12 and F-14.
- **Branch state:** the work is uncommitted, and the branch mixes ownership areas.

## 1. Git state

| Item | Observed |
|---|---|
| Branch / HEAD | `naveen-changes` at `73106fe` (= `origin/main`) |
| vs `origin/naveen-changes` (`f5220e9`) | Ahead 3, behind 0: the merges `84ba16a`, `8734dcd`, `73106fe`. Their committed delta is 49 files, **all frontend** design-system work already on `origin/main`. A push would fast-forward; no force is needed |
| Staged | **None** |
| Unstaged | 38 modified tracked files (+2,298 / −307) |
| Untracked | 37 paths |
| Total status entries | 75: the 68 from the audit baseline, `backend-pre-push-audit-report.md`, the 5 remediation paths (`engines/locks.py`, `tests/test_backend_remediation.py`, first-time edits to `engines/applicability.py`, `engines/assurance.py`, `tests/test_governance_events.py`) and `backend-remediation-report.md`. This report makes 76 |
| Ignored (correctly excluded) | `backend/.env`, `backend/var/` (keys), `.venv/`, `frontend/.env.*.local`, `.next/`, `node_modules/`, `__pycache__/`, `.pytest_cache/` |
| **Important** | **Nothing from the backend work is committed.** Pushing `naveen-changes` today publishes only the three `main` merges. The work must be reviewed and committed on explicit instruction first |

## 2. Remediation claims verified in the code

| Finding | Claim | Verified at |
|---|---|---|
| F-01 | Skip reasons are exactly `PostgreSQL-only` | `test_regulatory_traceability.py:642`, `test_obligation_definition_authoring.py:309`, `test_backend_remediation.py:578`; no `PostgreSQL-only:` variant remains |
| F-02/F-07 | STA §03 vocabularies; shared validator; strict k; evaluator re-validates; configuration error, not a state | `engines/assurance.py:35-37, 41, 52, 81, 210`; `routers/assurance.py:314, 321`; the old `k_threshold or len(req)` is gone |
| F-03 | Non-empty, real, unique keys; primary in the expression; checked at proposal and approval; empty nodes are errors | `routers/assurance.py:473`; `engines/governance.py:159, 218, 297`; `engines/assurance.py:425` |
| F-04 | Publication row-locks the definition (refreshed) before binding | `routers/rules.py:266`; `engines/catalog.py:714` |
| F-05 | Database-held advisory lock in `attest`; also per obligation for compensation decisions | `engines/applicability.py:385`; `engines/governance.py:287`; `engines/locks.py:29` |
| F-06 | Re-adoption from CLOSED starts at `max(determination start, closure)` | `engines/applicability.py:465-466` |
| F-08 | Compensation status read as of the cutoff from the event history | `engines/assurance.py:720` |
| F-09 | No fallback; `NO_EXPRESSION_IN_FORCE` | `engines/assurance.py:530, 609`; the old `[0]` fallback is gone |
| F-13 | Audit rows, window checks, tenant-filtered dedupe | `routers/assurance.py:358, 387, 397, 577`; `engines/assurance.py:778` |
| F-15/F-16 | Bounded verification records with a fingerprint; header; §18 citation | `routers/catalog.py:561, 570`; `engines/release_verification.py:291, 301`; no "§19" remains |

## 3. Test evidence (executed in this audit)

All commands ran from `backend/` with `PYTHONDONTWRITEBYTECODE=1 ZA_SKIP_DOTENV=1 ZA_DEMO_MODE=0`. PostgreSQL 17.11
ran on a scratch cluster at 127.0.0.1:55450 with fresh databases. Credentials are redacted.

| # | Check | Command | Result |
|---|---|---|---|
| 1 | SQLite full suite | `python -m pytest tests -ra --tb=short -p no:cacheprovider --junitxml=…/backend-unit.xml` | **398 passed, 130 skipped, 0 failed** (91.7 s) |
| 2 | CI gate 1: backend-unit | `junit_gate.py --report …/backend-unit.xml --allow-skip-reason PostgreSQL-only` | **PASS** |
| 3 | RLS tests | `pytest tests/test_rls.py` on fresh `assure_test_final_rls` | **5 passed** |
| 4 | CI gate 2: RLS | `junit_gate.py --report …/rls.xml --no-skips --require test_rls=5` | **PASS** |
| 5 | PostgreSQL full suite | `pytest tests` on fresh `assure_test_final` | **528 passed, 0 skipped, 0 failed** (257.6 s) |
| 6 | CI gate 3: full suite | `junit_gate.py --no-skips` plus all 23 `--require` lines read from `ci.yml` | **PASS** (including `test_backend_remediation` 56/56) |
| 7 | Repository hygiene | `python .github/scripts/repo_hygiene.py` | **PASS** (252 paths, 249 files scanned) |

**Not run:**
- Lint, type and static analysis: no backend tool is configured, and none was installed.
- Frontend checks: out of scope.
- GitHub Actions: requires a PR; pushes of `naveen-changes` alone trigger no CI.
- The CI migration CLI step (`app.migrate status/upgrade/check`): not run, per this audit's no-migration rule. It last
  passed in the remediation run, and `test_migrations` (16/16) passed here.

## 4. F-04 and F-05 concurrency, on PostgreSQL

**Executed in run 5 (JUnit-confirmed PASSED, not skipped):**
- `test_publication_waits_for_an_in_flight_link_edit_and_signs_the_committed_links` (1.84 s);
- `test_a_link_edit_waiting_behind_a_publication_is_refused_after_the_release` (1.79 s; both include the 1.5 s blocking
  window);
- `test_concurrent_or_repeated_adoption_creates_exactly_one_obligation` (0.60 s).

Ten other existing PostgreSQL concurrency tests also executed and passed.

**Negative control.** The same tests were run on a fresh database with the protections disabled *in-process* by a
scratch pytest plugin. It monkeypatches `applicability.xact_lock` to a no-op and makes publish's `lock_definition` an
unlocked read. No file was changed.

| Protection | With protection | Protection disabled | Conclusion |
|---|---|---|---|
| F-04 publication lock, test (a) | Pass | **Fail**: the signed binding lacks the concurrently added provision (stale link set signed) | Load-bearing; the test detects removal |
| F-04 publication lock, test (b) | Pass | **Fail**: `{'edit': 'ok'}`, a link edit committed during publication | Load-bearing; the test detects removal |
| F-05 adoption lock, committed test | Pass | **Pass** | **W-1: the test does not detect removal** |

**Deterministic F-05 probe** (scratchpad). The first attestation was paused inside its transaction, then a second
attestation of the same determination started.

| | Results | Second blocked | Obligation revisions | Current determination revisions |
|---|---|---|---|---|
| With lock | first ok, **second 409** | yes, on the advisory lock | 1 | **1** (ADOPTED) |
| Lock disabled | first ok, **second ok** | yes, only on the record-sequence row | **2** | **2** (ADOPTED, ADOPTED) |

The F-05 lock is effective and necessary. Without it, the race leaves **two current ADOPTED determination revisions**,
a double attestation and a spurious obligation revision. (The record-sequence row lock happens to keep the obligation
lineage single, so the original audit's "two lineages" description understated a different integrity breach.)

**W-1 remediation (not applied; read-only):** replace the barrier-based F-05 test with the deterministic pause pattern
used by F-04 test (b) and by this probe. Assert a 409 for the second attestation and exactly one current determination
revision.

## 5. Secrets, accidental files, unrelated changes, migrations

| Check | Result |
|---|---|
| Secrets | Hygiene PASS. The three scratch-cluster passwords appear in no tracked or untracked file. The only env-like files are the placeholder `backend/.env.example` and `frontend/.env.example`. Keys under `backend/var/` are ignored |
| Accidental files | None. No scratch, probe, database, log, key or certificate file is in the working tree |
| Migrations | 0001–0010 **identical to HEAD**. 0011–0013 untracked and unmodified since 2026-11-03 17:30, before the audit and remediation. No 0014/0015 exists |
| Unrelated changes in Naveen's modules | None found. The remediation touched only Naveen's engines, routers and tests plus one `ci.yml` gate line |
| Other owners' work in the branch | Present and unchanged by the remediation: frontend (Vijay), IAM authority grants / migration 0011, connector certification / 0012–0013, sidebar/IAM/platform routers (Nagesh). These need their owners' review before merge (work split: "No unrelated files are changed") |

## 6. Remaining blockers (re-verified in this audit)

| ID | Status | Evidence | What is needed |
|---|---|---|---|
| R-01 | **Open.** Obligation and determination uniqueness is enforced by the F-05 advisory lock in `attest`, not by a constraint. `_derive` revisions are not serialised with it | No partial unique index in `migrations/versions/*.py` or `models.py` | A reviewed migration (proposal in the remediation report §4): a pre-check for existing duplicates, then partial unique indexes `WHERE record_to_seq IS NULL`. DAT-REQ-039 review and Data Architect approval |
| R-02 | **Open.** Re-review of an adopted determination cannot complete, so closure of an adopted obligation is unreachable through the API | Re-run probe: after a fact change the determination is flagged `needs_review`; re-attest → 409 "A newer evaluation exists"; after a challenge → still 409 | An APP-001 decision on how re-review binds the newer evaluation |

## 7. External ownership and governance dependencies

These are documented in the remediation report §3 and §8 as open with an owner:

| ID | Dependency | Owner |
|---|---|---|
| F-10 | EVT event names and payloads (EVT-REQ-008/021) | EVT owner / Nagesh |
| F-11 | Idempotency keys, problem+json, URI versioning, ETags (API-REQ-002/004/006/017) | API Lead / Nagesh |
| F-12 | DAT-REQ-039 review of migrations 0011–0013 | Nagesh, Data Architect |
| F-14 | Compensation separation of duties (STA §09 "according to policy") | Head of Assurance (policy) |

The governance decisions in [regulatory-catalogue-governance-decision-register.md](regulatory-catalogue-governance-decision-register.md)
(B1–B7, K1–K6, R1, C1–C4) remain open. They block production claims, not publication.

## 8. Canonical database

- The only contact was one read-only `python -m app.migrate status`. It reported
  `assure@127.0.0.1:5433/assure: at the head revision 0013`, the same as before the remediation.
- Every write in this audit went to the scratch cluster on port 55450 (a separate server), into disposable databases.

## 9. Remaining risks and recommendations before merge

1. **W-1:** make the F-05 regression test deterministic (§4) so CI would catch a regression of the lock.
2. Commit only on explicit instruction, separating Naveen's backend work from other owners' areas, and get Nagesh's and
   Vijay's review for theirs.
3. Open the PR so GitHub Actions runs the same three gates.
4. Track R-01 (migration approval) and R-02 (APP-001 decision) as blockers for production obligation-lifecycle claims.
