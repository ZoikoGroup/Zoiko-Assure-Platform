# Decision brief: snapshot manifest format, test-definition order and engine versioning

> **Purpose:** prepare a governed decision. This brief is **not an ADR** and approves nothing.
> - ADR-REQ-006: "AI systems MUST NOT accept, reject, supersede or approve ADRs".
> - ADR-REQ-005: a T0 decision cannot be accepted by its author alone.
>
> **Status:** no approved decision exists. The blocked work stays unimplemented until one does.
> **Date:** 28 October 2026.
> **Prepared from:** the controlled DOCX sources, the repository code and the stored data described below.
> **Context:** roadmap Stage 3a ([backend-architecture-roadmap.md](backend-architecture-roadmap.md)) and audit finding I-03 ([backend-architecture-audit.md](backend-architecture-audit.md), §12).

## 1. What is decided, and what is not

| Document | Version | Status (header) | Accountable approvers |
|---|---|---|---|
| ZA-STA-001 Assurance State Machines & Roll-up | 1.0 | Proposed for G1 assurance-semantics lock | CTO; CPO; Head of Assurance; General Counsel |
| ZA-TIM-001 Bitemporal Data & Historical Reconstruction | 1.0 | Proposed for G1 temporal-model lock | CTO; CPO; Head of Assurance; General Counsel |
| ZA-DAT-001 Logical & Physical Data Model | 1.0 | Proposed for G1 database-contract lock | CTO; Head of Assurance; Security Architect |
| ZA-ADR-001 Architecture Decision Record Register | 1.0 | Proposed for architecture-governance lock | CTO; domain accountable owner for T0/T1; GC / Security / Privacy / Head of Assurance where affected |
| ZA-QA-001 Master Verification & Test Strategy | 1.0 | Proposed for master verification lock | CTO; CPO; Head of Assurance; Head of Security |
| ZA-ARCH-001 Backend Architecture | 3.0 | Proposed for Architecture Lock | CTO / Product / Risk & Compliance |

ZA-ADR-001 states: "Approval of ZA-ADR-001 v1.0 ratifies the entries marked ACCEPTED* as the engineering baseline." ZA-ADR-001 is not approved. So **none of the baseline ADRs below is in force**, including TIM-ADR-04 and the consolidated decision C-02. No repository ADR record exists either: there is no `governance/adr/`, and the machine-readable register required by ADR-REQ-024 is missing (audit M-08).

## 2. The controlling text

- **STA-REQ-032** (ZA-STA-001): "Roll-up engine/version changes MUST be governed by ADR, golden-vector regression and explicit snapshot versioning."
- **TIM-ADR-04** (ZA-TIM-001, "ADRs to close"; ZA-ADR-001 register row): topic "Snapshot canonical manifest format". Recommended default: "Canonical JSON manifest with exact revision IDs/hashes and roll-up/evaluator versions; signature under audit/evidence architecture." Register risk tier **T0**; owners **Data Architect + CTO**.
- **C-02 "Canonical serialization"** (ZA-ADR-001 consolidated decision; covers APP-ADR-02, RUL-ADR-01, EVD-ADR-01, TIM-ADR-04): "Canonical JSON v1 with published conformance vectors is the Phase-1 authority for signed/hashes rule, ledger and snapshot artifacts."
- **STA-ADR-06 "Snapshot signing"** (T0; Head of Assurance + CPO): "Dependency digest in Assure DB; signed bundle through evidence/audit-package architecture for external verification."
- **TIM-ADR-01 "Record watermark representation"** (T0; Data Architect + CTO): "Cell-local `(recorded_at, record_seq)` token…"
- **Related requirements:**
  - STA-REQ-019: completed evaluations and snapshots are immutable.
  - STA-REQ-020: deterministic reason codes and a trace sufficient to reproduce.
  - STA-REQ-021: a snapshot pins exact dependency revisions/hashes.
  - TIM-REQ-005: a monotonic record sequence.
  - TIM-REQ-015: pin dependencies and a record cutoff; no "latest" joins.
  - DAT-REQ-007: `record_seq` gives deterministic knowledge ordering.
- **ZA-STA-001 uniqueness rule:** an identical control-evaluation input digest + evaluator/policy version + temporal context *may* deduplicate.
- **ZA-STA-001 policy mode `PRIMARY_PLUS_CORROBORATION`:** "**Named** primary test must pass."
- **ZA-QA-001:** a "Roll-up golden corpus" (ALL/ANY/K-of-N/compensation/conflict/exception/time-segment vectors) under **Head of Assurance review**. Golden corpora are "versioned with reviewer ownership". Snapshot tests must cover "AS_KNOWN_THEN vs RECONSTRUCTED_NOW after corrections".

## 3. Why these changes need governance

Each open item would change what the engine **outputs** for existing data. That means one or more of:
- the control-evaluation dependency digest
- the snapshot dependency digest or hash
- a trace
- a state

Such a change is a roll-up/evaluator engine change (STA-REQ-032) or a manifest-format change (TIM-ADR-04). Both are T0, the register's highest tier ("assurance roll-up" is named there), and need named accountable approval.

What Stage 3a already did, without governance, changed **no** output:
- **What changed:** inputs are read in record order, with explicit tenant predicates, and duplicate evaluations resolve to the earliest.
- **Evidence:** an old-versus-new engine comparison was byte-identical on 20 snapshot computations, 113 stored control-evaluation digests and 8 stored snapshots (audit §12).

## 4. Decisions required

### D1. Order of test definitions in the control-evaluation digest

**Current behaviour:**
- `evaluate_control` concatenates run IDs per test definition, in the definitions' stored (physical) order.
- Test definitions have no `record_seq`, no `created_at` and no ordinal.
- Their stored order is the order the policy author listed them. It survives unless the table is rewritten (`CLUSTER`, `pg_repack`).
- The same order also picks the primary test when a `PRIMARY_PLUS_CORROBORATION` policy names none (`req[:1]`).

| Option | What changes | Output change for existing data | Needs |
|---|---|---|---|
| **A. Keep stored order** (status quo) | Nothing; document the residual risk | None | A decision to accept the residual risk (table rewrites) |
| **B. Set semantics** | Sort run and evidence IDs inside the control-evaluation manifest; bump `CONTROL_EVALUATOR_VERSION` (and `ROLLUP_ENGINE_VERSION`, since snapshot digests embed control digests); keep the 1.0 algorithm callable for reproducing 1.0 records | Yes, for every new evaluation (the version is part of the digest). 1.0 records reproduce only through the 1.0 path | ADR (STA-REQ-032), golden-vector regression, snapshot versioning |
| **C. Persist authoring order** | Add an `ordinal` to `assurance_test_definition_version`; backfill from the current stored order (recorded in each evaluation's `inputs.tests`); order by it | None for existing data if the backfill matches; verifiable against stored inputs | Data Architect schema decision; DAT-REQ-039 review; DAT-REQ-024 backfill rules |

**Related, D1a:** a `PRIMARY_PLUS_CORROBORATION` policy with no named primary. ZA-STA-001 requires a named primary. Should such a policy be rejected when it is created, or should the fallback stay? Rejecting changes state for affected policies, so it is also an STA-REQ-032 change.

### D2. Engine versioning and reproduction policy (STA-REQ-032)

If any option changes output, decide:
- the new version identifiers for the control evaluator and the roll-up engine
- how `reproduce` selects the algorithm from a stored record's version
- how long the old algorithm stays callable
- what "explicit snapshot versioning" means on the snapshot record

Today the snapshot stores `rollup_engine_version`, but `reproduce` always recomputes with the current engine.

### D3. Snapshot canonical manifest (TIM-ADR-04, C-02; with STA-ADR-06)

**Today:**
- The manifest holds sorted dependency **IDs**.
- Engine versions sit in separate columns and inside each control digest, not in the manifest.
- `snapshot_hash` covers state, segments, the dependency digest, the period and the cutoff. It does not cover the roll-up version, the perspective or an evaluation instant.

**The recommended default** ("exact revision IDs/hashes and roll-up/evaluator versions") would add fields. That is a new manifest version, and it is what Stage 3e (full-hash comparison on reproduction) would compare.

**C-02** also requires published canonical-JSON conformance vectors, which do not exist yet (audit M-11).

### D4. Evaluation instant (Stage 3d, wall clock)

Occurrence logic compares due dates with the wall clock. Pinning an evaluation instant needs two things:
- a definition of that instant
- storage for it in the manifest

TIM-ADR-01 describes a `(recorded_at, record_seq)` watermark. The cell stores neither `recorded_at` per sequence nor `record_batch_id`. Any choice changes output for snapshots whose period extends past their creation, so it is also an STA-REQ-032 change.

## 5. Compatibility

| Stored data | Dev database (28 Oct 2026) | Effect of A | Effect of B | Effect of C |
|---|---|---|---|---|
| Control evaluations | 22, all `control-eval-1.0`; none depends on order (no policy has more than one test definition; no test has two or more runs) | none | new evaluations get new digests; the 22 reproduce only under 1.0 | none if the backfill reproduces stored order |
| Snapshots | 2, both reproducible (digest and state) | none | reproduce under 1.0 only | none |
| Audit evidence | Packages and attestations cite exact IDs and hashes | unaffected | cited digests remain valid only for the 1.0 path; must be documented in release evidence | unaffected |

## 6. Golden-vector regression required before any approved change

**ZA-QA-001's roll-up golden corpus,** versioned, with Head of Assurance review. It must contain at least:
1. ALL / ANY / K_OF_N / COMPENSATION / conflict / exception / time-segment vectors (ZA-QA-001).
2. Multi-test policies, each test with runs, under every aggregation mode.
3. Two or more runs per test with record order ≠ ID order, plus evidence lists.
4. `PRIMARY_PLUS_CORROBORATION` with and without a named primary (D1a).
5. A physical table rewrite (`CLUSTER`) between creation and reproduction (the existing `tests/test_snapshot_determinism.py` technique).
6. Reproduction of every stored 1.0 evaluation and snapshot under its stored version, using the old-versus-new comparison method from audit §12.
7. AS_KNOWN_THEN vs RECONSTRUCTED_NOW after a correction (ZA-QA-001).
8. Canonical-JSON conformance vectors for every hashed artifact touched (C-02).

## 7. Who must approve

| Decision | Tier | Approvers (from the controlled sources) |
|---|---|---|
| Precondition: ratify the baseline | n/a | Approval of ZA-ADR-001 (G-03): CTO and the domain accountable owners |
| D3 / TIM-ADR-04, D4 / TIM-ADR-01 | T0 | Data Architect + CTO (register). ZA-TIM-001 accountable approvers: CTO, CPO, Head of Assurance, General Counsel |
| D1, D1a, D2 (STA-REQ-032 ADR) | T0 ("assurance roll-up") | A new domain-prefixed ADR (ADR-REQ-002; no ID is assigned here). STA ADR owners: Head of Assurance + CPO. ZA-STA-001 accountable approvers: CTO, CPO, Head of Assurance, General Counsel |
| D1 option C (schema) | — | Data Architect (ZA-DAT-001 owner), with DAT-REQ-039 security/data/QA review |
| Golden corpus | — | Head of Assurance review (ZA-QA-001) |

Not by its author alone (ADR-REQ-005), and never by an AI system (ADR-REQ-006).

## 8. Open questions for the approvers

1. D1: A, B or C? And D1a: reject policies without a named primary, or keep the fallback?
2. Is a change to the control-evaluation digest a "roll-up engine/version change" under STA-REQ-032? (This brief assumes yes.)
3. D2: version identifiers, the algorithm-selection rule in `reproduce`, and the retention period of old algorithms.
4. D3: which manifest fields (revision hashes or IDs, engine versions, perspective, evaluation instant), a manifest version field, and the signing route (STA-ADR-06).
5. Stage 3e: compare the full snapshot hash only for snapshots created under the new manifest version?
6. D4: the definition of the evaluation instant, and whether the cell must store `recorded_at` and `record_batch_id` per sequence (TIM-ADR-01).
7. Where the golden corpus lives and who owns it (QA-ADR-04: test evidence storage).

## 9. Affected files, tests and migrations

| Option | Code | Tests | Migration |
|---|---|---|---|
| A | none (a comment exists in `engines/assurance.py`) | none | none |
| B | `app/engines/assurance.py` (`evaluate_control` manifest; version-dispatched paths), `app/codes.py` (versions), `app/routers/assurance.py` (`reproduce` by stored version) | `tests/test_snapshot_determinism.py` (multi-test cases), `tests/test_authorization.py` and `tests/test_api.py` snapshot assertions, a new golden corpus | none |
| C | `app/models.py` (`TestDefinition.ordinal`), `app/routers/assurance.py` (`create_control`), `app/seed.py`, `app/engines/assurance.py` (order by ordinal) | as B, plus backfill verification against stored `inputs.tests` | yes: add the column, backfill, `NOT NULL` |
| D3 / 3e | `app/engines/assurance.py` (manifest and hash), `app/routers/assurance.py` (`reproduce`) | reproduction suite | possibly a manifest-version column on `assurance_snapshot` (an immutable table: DAT-REQ-039 review) |

## 10. What stays blocked until approval

- The engine version bump.
- Any change to test-definition order or the primary-test fallback.
- Manifest format changes.
- Full snapshot-hash comparison (Stage 3e).
- Pinning the evaluation instant (Stage 3d wall clock).

Stage 3d's current-state reads are also blocked:
- Evidence lifecycle as of the cutoff needs `record_seq` on ledger events (Stage 3b).
- Conflict, exception, compensation and occurrence status as of the cutoff need append-only status events (Stage 3c), which wait on the event-table convention.
