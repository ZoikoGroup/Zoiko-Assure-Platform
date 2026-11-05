# D2 Connector Build and Source-Version Certification — Implementation Report

> **Date:** 3 November 2026. **Branch:** `naveen-changes`, working tree only. Nothing was staged, committed, pushed or pulled.
> **Design:** [connector-version-certification-design.md](connector-version-certification-design.md).
> **Result:**
> - **Implemented:** the specification-supported part of D2 (D2-a…d) as migration 0013.
> - **Open, documented, not implemented:** D2-e…i (build identity and range, envelope composition, registry relationship, run enforcement, per-version evidence).
> - **Stage 3c is not complete.** D1, D3–D7, G-08 and the DAT-REQ-039 reviews of 0011–0013 remain open.

## 1. Specification references

All checked against the specification text:
- **ZA-IPE-001:** IPE-REQ-028 (the controlling requirement), IPE-REQ-029, IPE-REQ-002 and IPE-INV-08; §04 (ExtractionContractVersion: "Immutable version referenced by SourceRun"; "connector compatibility: build/version range"); §11 (MappingContractVersion: "Immutable version bound to connector build/source schema version"; `source_schema_version` "version or fingerprint"); §18 ("CERTIFIED only for a specific tested contract envelope"); WF-IPE-05 (the certification envelope); the threat row "MappingContractVersion is reviewed/certified, immutable and traceable".
- **IPE-ADR-05** (proposed, owner T1 Head of Assurance + Integrations Lead; not approved).
- **ZA-EVD-001:** the manifest's "connector build hash".
- **ZA-GLS-001 §07:** the certification codes.
- **ZA-EVT-001:** `connector.certification_changed` and the STATE_CHANGED profile.
- **ZA-DAT-001:** DAT-REQ-014/024/027/037/039.
- **ZA-IAM-001:** IAM-INV-05.

## 2. Gap analysis (before 0013)

| Spec requires | Code did | Gap | Recommendation / outcome |
|---|---|---|---|
| Certification per build × source × extraction × mapping (IPE-REQ-028) | One certification per connector definition | **MISSING** | Version-level subjects implemented (contract versions); envelope composition open (D2-f) |
| Contract versions immutable (IPE §04, §11) | Both tables: no RLS, no trigger, runtime UPDATE and DELETE (live 0010 snapshot) | **SPEC VIOLATION** | Implemented: status-only updates, no delete, platform-only writes |
| Exact, unique version identity; exact references (IPE-REQ-002) | No `(connector_id, version)` uniqueness; no foreign keys from contracts to the connector or from source runs to contracts | **MISSING** | Implemented |
| Suspension or recertification of contract versions (IPE-REQ-029) | An unconstrained `status` column with no writer, no history and no audit | **PARTIAL** | Implemented: GLS-constrained status, a service, a platform route, history, audit, outbox |
| Source version identity (IPE §11 "version or fingerprint") | `MappingContractVersion.source_schema_fingerprint`; `ExtractionContractVersion.endpoint_id` | IMPLEMENTED (unchanged) | Carried in the event's `effective_scope` |
| Build identity: hash (EVD), range (IPE §04), binding (IPE §11) | One immutable string `build_version` per connector; `SourceRun.connector_build` | **BLOCKED (D2-e)** | Not implemented; nothing invented |
| History names the exact certified subject | 0012 history keyed by connector plus `build_version` | **MISSING** | Implemented: `subject_type` / `subject_id`, chained per subject |
| Effect of version certification on runs and IPE | The engine uses the latest contract version regardless of status | **BLOCKED (D2-h)** | Unchanged and documented |

## 3. Design and implementation decisions

Each decision, with its authority:

| Decision | Authority |
|---|---|
| Contract versions are the certified subjects; GLS codes | IPE-REQ-028/029; IPE threat row; GLS §07 |
| Immutable except `status`; no delete; platform-only writes (0012 predicates); readable by every runtime context | IPE §04/§11 "Immutable"; D4 precedent (0005/0007/0012) |
| Uniqueness, foreign keys and the CHECK | IPE §04/§11; IPE-REQ-002 |
| History extension (expand only) and per-subject chains | IPE "traceable"; DAT-REQ-037; DAT-REQ-024 (no rewrite, no backfill) |
| `CERTIFIED` needs the connector's matrix to pass | FR-CON-002; fail-closed interim for D2-i |
| Route under the existing `certify_connectors` (platform, HUMAN) | Existing authorization; no new role (D3) |
| Event `connector.certification_changed`, STATE_CHANGED payload | EVT catalogue ("Connector certification state changed"); IPE-REQ-028 makes contract certification part of connector certification |

**Not decided** (owner: Head of Assurance + Integrations Lead unless noted):
- **D2-e:** build identity, hash vs version string, range syntax.
- **D2-f:** envelope composition, including auth scopes (IPE-ADR-05 approval).
- **D2-g:** registry ↔ version relationship (IPE, GLS and Product).
- **D2-h:** effect of a SUSPENDED/REVOKED/UNASSESSED contract on SourceRuns and IPE.
- **D2-i:** per-version certification evidence.

## 4. Files changed

| File | Change |
|---|---|
| `backend/migrations/versions/0013_contract_version_certification.py` (new) | §5 |
| `backend/app/models.py` | Constraints and foreign keys on both contract models; source-run foreign keys; history `subject_type` / `subject_id`, nullable health, subject CHECK |
| `backend/app/engines/connectors.py` | `_prior_event` (chain per subject; the registry path now chains only connector-level events); `CONTRACT_KINDS`; `set_contract_certification` |
| `backend/app/routers/integration.py` | `POST /api/connectors/{connector_id}/contracts/{kind}/{version_id}/certification` (strict body `{certification, reason}`) |
| `backend/tests/test_connector_version_certification.py` (new, 23) | §8 |
| `backend/tests/test_connector_certification_history.py` | History grouped per subject; helper filtered to connector-level events |
| `backend/tests/test_immutability.py` | Contract tables GUARDED {status} with probes and GLOBAL context; history probe values; owner probe applies probe values |
| `backend/tests/test_tenant_security.py` | Reviewed RLS inventory: the contract tables and their policy sets |
| `backend/tests/test_migrations.py` | `POST_BASELINE_CONSTRAINTS` (0013 constraints dropped from the legacy pre-Stage-1 schema) |
| `.github/workflows/ci.yml` | `--require test_connector_version_certification=23` |
| `docs/connector-version-certification-design.md` (new) | Design |
| `docs/connector-version-certification-implementation-report.md` (new) | This report |
| `docs/database-migrations.md`, `docs/backend-architecture-roadmap.md` | 0013 row; Stage 3c status |

No migration from 0001 to 0012 was edited. No frontend change.

## 5. Migration 0013

1. **Both contract-version tables:**
   - `uq_*_connector_version` UNIQUE (connector_id, version);
   - `fk_*_connector` to `integration_connector_definition`;
   - `ck_*_certification` status in the GLS codes;
   - ENABLE + FORCE RLS: `contract_read` (SELECT, `true`), `contract_platform_insert` and `contract_platform_update` (verified platform principal, not system scope), `migrator_all`;
   - REVOKE UPDATE, DELETE, then GRANT UPDATE (status);
   - `immutable_columns` trigger (status excepted).
2. **`evidence_source_run`:** `fk_source_run_extraction_contract` and `fk_source_run_mapping_contract`. Validated against existing rows; a dangling reference fails the migration, which rolls back.
3. **`integration_connector_certification_event`:**
   - `subject_type` (NOT NULL, default `CONNECTOR_DEFINITION`) and `subject_id`;
   - runtime-health columns made nullable;
   - `ck_connector_certification_event_subject`.

   This is expand only: existing rows take the default and are not rewritten.
4. **Downgrade** restores 0012 exactly, and refuses (with nothing changed) when contract-level history exists, because those rows cannot be represented at 0012 and history is append-only.

## 6. Compatibility handling

- **Registry rows and route:** unchanged (tested). **Existing contract rows:** keep their `status` (seed: `CERTIFIED`; tested in a throwaway 0012 → 0013 upgrade).
- **Existing history rows:** become `CONNECTOR_DEFINITION` subjects with `subject_id` NULL, values unchanged (tested).
- **No invented identifiers:** no build identifier, range or hash is invented, and no history is backfilled.
- **Global-to-version mapping:** not mapped (D2-g).
- **IPE behaviour:** unchanged (D2-h). The existing IPE and source-run tests pass.

## 7. Database security verification

Run as the runtime role and the owner role, below the API, on PostgreSQL 17:
- **Runtime role on both contract tables:** no table UPDATE, no DELETE, no TRUNCATE; UPDATE only on `status`. `immutable_columns` is enabled, and FORCE RLS is on.
- **Other contexts** (tenant, anonymous, system with an actor): can read a contract version. A status UPDATE affects 0 rows, and a tenant INSERT fails on row-level security.
- **Platform principal:** can change `status`. Changing `endpoint_id` / `source_schema_fingerprint` or `version` is denied, and so is DELETE.
- **Owner role:** refused by the trigger on protected columns and on DELETE.
- **Integrity:**
  - a source run with a dangling contract reference is refused by the foreign key, while real references are accepted;
  - history rows whose subject and health values disagree are refused by the CHECK;
  - duplicate `(connector, version)`, an unknown connector and a non-GLS status are refused (both databases).
- **Unchanged:** 0012 protections (registry and history; `test_connector_certification_history` 21) and the append-only history.

## 8. Test results

All runs used an exported copy of exactly the files a commit would contain; afterwards the copy was confirmed identical to the working tree (`app`, `tests`, `migrations`, `ci.yml`). PostgreSQL ran in a throwaway PostgreSQL 17.11 cluster set up with CI's bootstrap SQL, deleted afterwards.

| Run | Result |
|---|---|
| New module + 0012 module on SQLite | 31 passed, 13 skipped (`PostgreSQL-only`) |
| Full SQLite suite + `backend-unit` gate | **244 passed, 120 skipped** (all `PostgreSQL-only`), 0 failed; **gate PASS** |
| CI migration step on an empty database (status, upgrade, repeat, status, check) | Built to **0013**; **check passed** (no model drift; RLS/FORCE coverage) |
| RLS gate | 5/5, PASS |
| Full suite on PostgreSQL 17 + gate (`--no-skips`, every `--require`) | **364 passed, 0 failed, 0 skipped; gate PASS**. `test_connector_version_certification` **23/23**, `test_connector_certification_history` 21/21, `test_immutability` 7/7, `test_migrations` 16/16 (incl. newest-revision downgrade/upgrade and legacy adoption), `test_tenant_security` 49/49 |
| `repo_hygiene.py`, `git diff --check` | PASS |

`test_connector_version_certification.py` (23), against the brief:

| Brief scenario | Test |
|---|---|
| Multiple **source/contract versions** of one connector stay distinct; version-specific identity | `test_each_contract_version_is_its_own_certification_subject` (extraction v1 vs v2; mapping v1 vs extraction v1) |
| History points to the correct subject | Same test; `test_suspension_and_recertification_chain_per_subject…`; `test_audit_and_outbox_record_the_exact_subject` |
| Duplicate identities rejected | `test_duplicate_and_dangling_identities_are_rejected` (duplicate `(connector, version)` for both kinds) |
| Invalid references rejected | Same test (unknown connector; non-GLS status); `test_source_runs_must_reference_existing_contract_versions` (PostgreSQL); `test_invalid_requests…` (another connector's version, unknown version, unknown kind: 404) |
| Existing global certification compatible | `test_the_global_registry_route_is_unchanged`; the 0012 module (21) |
| Unauthorised tenant writes rejected | `test_only_a_platform_principal…` (tenant context: 0 rows), `test_a_tenant_cannot_insert_a_contract_version`, `test_only_a_human_platform_certifier…` (7 callers, 403, fingerprint unchanged) |
| Unauthorised system-job writes rejected | `test_only_a_platform_principal…` (system context with an actor: 0 rows) |
| Authorised platform writes work | Same test (status update = 1 row); the API tests above |
| Append-only protections remain | Owner and runtime refusals on protected columns and DELETE; `test_history_subject_and_health_are_bound`; 0012 history tests |
| Audit and outbox atomic; failures leave nothing | `test_a_failure_mid_transition…[emit, audit]`; `test_audit_and_outbox_record_the_exact_subject` (one `record_seq`) |
| Retries and duplicates | `test_repeats_record_nothing`; `test_concurrent_version_changes_serialise` (PostgreSQL) |
| Existing records preserved, nothing backfilled; downgrade safety | `test_upgrade_preserves_existing_records_and_downgrade_refuses_to_lose_history` (PostgreSQL) |
| **Multiple builds of one connector distinguishable** | **Not implemented, so not tested.** The build identity is undecided (D2-e) |

**Defects found and fixed during the work.** The first PostgreSQL run failed one existing generic test, `test_immutability::test_completed_results_cannot_be_rewritten_even_by_the_owner_role`. Its owner-role probe inserted a bare history row, which the new subject CHECK correctly refused. The probe now supplies the required health values; the check itself is unchanged. The suite was re-run on fresh databases.

## 9. Remaining blockers and decision owners

| Item | Owner |
|---|---|
| D2-e build identity, hash vs version, compatibility range | Head of Assurance + Integrations Lead (IPE-ADR-05) |
| D2-f envelope composition (IPE-ADR-05 approval; auth scopes) | Same; ADR approval |
| D2-g registry ↔ version certification | IPE + GLS owners, Product (PRD FR-CON-001) |
| D2-h effect on SourceRuns/IPE of non-CERTIFIED versions | Head of Assurance + Integrations Lead |
| D2-i per-version certification evidence (interim: connector matrix) | Same |
| D1 vocabulary/lifecycle; D3 role; D4 confirmation; D5 health/availability events; D6 reason codes; D7 stale-request refusal | As in [connector-certification-history-design.md](connector-certification-history-design.md) |
| G-08 history read authorization (no endpoint added) | IAM Lead |
| DAT-REQ-039 security/data/QA review of 0011, 0012 and 0013 | Security, Data Architect, QA |

## 10. Canonical `assure`

Not migrated or modified, and kept at 0010. `python -m app.migrate status` (read-only, after the work): `assure@127.0.0.1:5433/assure: at revision 0010; pending 0011, 0012, 0013 (head 0013)`.
- No test or run used its credentials, and no database was approved for automatic migration.
- The local API keeps refusing to start until `assure` is upgraded manually, after a backup.

## 11. Readiness

- **Ready for code review:** the D2-a…d slice, together with the DAT-REQ-039 review.
- **Not ready:** IPE-REQ-028 is only **partly** satisfied. Builds and envelopes are not yet certification subjects (D2-e/f), and version certification does not yet affect runs (D2-h).
