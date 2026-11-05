# Connector Build and Source-Version Certification (D2) — Design

> **Date:** 3 November 2026. **Status:** design for review. Every ZA-* specification is "Proposed for lock". IPE-ADR-05 is an unapproved register entry, a "recommended default" (ZA-ADR-001 entries become ACCEPTED* only "on approval of this register").
> **Scope:** design decision D2 of [connector-certification-history-design.md](connector-certification-history-design.md). D1 and D3–D7 stay open and are not touched.

## 1. What the specifications require

| Source | Text (abridged) |
|---|---|
| **IPE-REQ-028** | "Connector certification MUST be versioned by connector build and relevant source API/report, ExtractionContract and MappingContract compatibility." |
| ZA-IPE-001 §18 | "A connector is CERTIFIED only for a specific tested contract envelope. Certification is not a blanket statement…" |
| ZA-IPE-001 WF-IPE-05 | "Certification envelope: Connector build, source API/report versions, auth scopes, extraction/mapping contracts." Result "from central connector registry; no independent UI override" |
| **IPE-REQ-029** | "A material source API/schema/auth/pagination change MUST trigger recertification or suspension of affected connector **contract versions**." |
| ZA-IPE-001 §04, ExtractionContractVersion | `extraction_contract_version_id`: "**Immutable** version referenced by SourceRun". Endpoint/report identifier: "Stable endpoint/report/file type identifier". Connector compatibility: "Connector build/version **range** certified against contract" |
| ZA-IPE-001 §11, MappingContractVersion | `mapping_contract_version_id`: "**Immutable** version bound to connector build/source schema version". `source_schema_version`: "API/report/file version **or fingerprint**" |
| ZA-IPE-001 threat table | "Manipulated mapping makes bad source look good: MappingContractVersion is reviewed/certified, **immutable** and traceable; material change recertifies." |
| IPE-REQ-002 | Every IPE-evaluated SourceRun MUST reference the exact ExtractionContractVersion, MappingContractVersion, IPEPolicyVersion and connector build |
| IPE-INV-08 | Every material mapping/transformation is versioned and reproducible |
| ZA-EVD-001 (manifest) | "connector build **hash**: Exact collection implementation" |
| IPE-ADR-05 (proposed) | "Certify exact connector build × source API/report version × extraction/mapping contract envelope" (owner: T1 Head of Assurance + Integrations Lead) |
| ZA-GLS-001 §07 | Certification codes `UNASSESSED · CERTIFIED · SUSPENDED · REVOKED`; the registry is the source of public claims |

**Reading.** The specifications place version-level certification on the **contract versions**:
- they are suspended or recertified (IPE-REQ-029);
- they are what is "reviewed/certified, immutable and traceable";
- they carry the source identity (the extraction endpoint/report identifier; the mapping `source_schema_version` "or fingerprint");
- they carry the build binding (the extraction "connector compatibility" range; mapping "bound to connector build").

## 2. Existing implementation

| Spec requires | Code does | Gap |
|---|---|---|
| Contract versions are immutable (IPE §04, §11) | `integration_extraction_contract_version` and `integration_mapping_contract_version`: **no RLS, no trigger, runtime UPDATE and DELETE** (live 0010 snapshot). No code writes them except the seed | Rewritable by any runtime context, which is the "manipulated mapping" threat |
| One exact, stable identity per version (IPE §04, §11; IPE-REQ-002) | `(connector_id, version)` with **no uniqueness**; `connector_id` with **no foreign key**; `evidence_source_run` contract references with **no foreign key** | Duplicate or dangling identities are possible |
| Contract versions carry certification and can be suspended (IPE-REQ-029) | A `status` column (default `CERTIFIED`), unconstrained, with no writer, no history, no audit and no event | No version-level certification lifecycle |
| Source version identity (IPE §11) | `MappingContractVersion.source_schema_fingerprint` (a fingerprint is permitted); `ExtractionContractVersion.endpoint_id` | Present |
| Build identity: "connector build hash" (EVD) / "build/version range" (IPE §04) / "bound to connector build" (IPE §11) | One string `integration_connector_definition.build_version` (immutable since 0012); `SourceRun.connector_build` copies it. There is **no build entity, no compatibility range and no build binding on contract versions** | Multiple builds of one connector cannot be represented |
| Certification history identifies the exact subject | 0012 history keys on `connector_id` plus `build_version` only | A contract-level subject is not identifiable |
| IPE uses certified contracts | `ipe.execute_source_run` takes the **latest** contract version **regardless of status** | Effect of version-level certification on runs is not defined |
| Global registry vs version certification | The registry column is independent | No defined relationship |

## 3. Decisions

| ID | Decision | Spec support | Status |
|---|---|---|---|
| D2-a | Certified subjects are contract versions (extraction, mapping), each with a GLS certification code | IPE-REQ-028/029, IPE §11 threat row, GLS §07 | **Supported**: implement |
| D2-b | Contract versions are immutable except their certification; platform-only writes; no delete | IPE §04/§11 "Immutable"; 0012 global model (D4 precedent) | **Supported**: implement |
| D2-c | `(connector_id, version)` unique; foreign key to the connector; source-run references are foreign keys | IPE §04/§11, IPE-REQ-002 | **Supported**: implement |
| D2-d | Version-level changes recorded in the existing 0012 history, with an explicit subject | IPE threat row "traceable"; DAT-REQ-037 | **Supported**: implement |
| D2-e | **Build identity:** semantic version string vs build hash (EVD "connector build hash"); a build entity; the extraction contract's "build/version **range**" syntax; the mapping contract's build binding | Formats undefined; conflict (string vs hash) | **Open**. Owner: Head of Assurance + Integrations Lead (IPE-ADR-05) |
| D2-f | **Envelope composition:** does one certification record cover build × source × extraction × mapping (IPE-ADR-05) and auth scopes (WF-IPE-05; IPE-REQ-028 omits them)? | IPE-ADR-05 proposed only | **Open**. Same owner; ADR approval |
| D2-g | **Registry ↔ version relationship:** is the connector registry value derived from version certifications, independent, or the current build's? | Not specified | **Open**. Owners: IPE + GLS (+ Product, PRD FR-CON-001) |
| D2-h | **Enforcement:** must a SourceRun refuse, or an IPE result downgrade, on a SUSPENDED/REVOKED/UNASSESSED contract version? | IPE-REQ-029 says "suspension", effect unstated | **Open**. Head of Assurance + Integrations Lead |
| D2-i | **Evidence for CERTIFIED at version level:** per-version test matrix (IPE §18 "specific tested envelope") | Matrix exists only per connector | **Open**. Interim: fail-closed reuse of the connector matrix |

**Safest interim behaviour** for the open items:
- The registry and IPE run selection are unchanged.
- Version-level certification is recorded, protected and audited, but has **no effect on runs** until D2-h is decided. This is documented, not hidden.
- `CERTIFIED` for a contract version is refused unless its connector's certification matrix passes: the existing FR-CON-002 evidence, the strictest available.
- No build entity, range or hash is invented.

## 4. Model (implemented part)

**Contract version tables** (both):
- **Identity and integrity:**
  - `UNIQUE (connector_id, version)`;
  - `FOREIGN KEY (connector_id) → integration_connector_definition`;
  - `CHECK (status IN ('UNASSESSED','CERTIFIED','SUSPENDED','REVOKED'))` (the GLS codes; existing rows are `CERTIFIED`).
- **Runtime role:** SELECT and INSERT; UPDATE (`status`) only; no DELETE or TRUNCATE.
- **Triggers:** `immutable_columns` (every other column immutable, DELETE refused for every role).
- **RLS** (ENABLE + FORCE): read for every runtime context (customer surfaces and IPE read contracts); INSERT and UPDATE only for a verified platform principal (as 0012: no tenant, an authenticated actor, not system scope).

**`evidence_source_run`:** foreign keys on `extraction_contract_version_id` and `mapping_contract_version_id`. Foreign-key checks run as the table owner, so tenant RLS on the run is unaffected.

**History** (`integration_connector_certification_event`, 0012), changed expand-only:
- `subject_type` (`CONNECTOR_DEFINITION` default, `EXTRACTION_CONTRACT_VERSION`, `MAPPING_CONTRACT_VERSION`) and `subject_id` (null for the connector itself).
- Runtime-health columns become nullable.
- A CHECK binds the subject: a connector row has no subject and has health values; a contract row has a subject and no health values.
- Existing rows default to `CONNECTOR_DEFINITION`, so nothing is rewritten. `prior_event_id` chains per subject.

**Service:** `connectors.set_contract_certification(ctx, connector_id, kind, version_id, certification, reason)`.
- Same authorization as the registry: HUMAN, platform, `certify_connectors`.
- Reason required; GLS codes only; the version must belong to the connector (otherwise 404).
- Row lock; a no-op when nothing changes.
- One transaction with the history row, the audit record (`connector.certification_changed`, object type set to the contract kind) and the outbox event.

**Route:** `POST /api/connectors/{connector_id}/contracts/{kind}/{version_id}/certification`, with `kind` = `extraction` | `mapping`. It is a platform write under the existing permission; no read route is added (G-08).

**Event:** `connector.certification_changed` (catalogued: "Connector certification state changed"; IPE-REQ-028 makes contract certification part of connector certification), STATE_CHANGED profile:
- `resource_id` = the version ID;
- `resource_type` = the contract kind;
- prior/new `{certification}`;
- revision refs;
- `effective_scope` = `{connector_id, contract_version, source_schema_version | endpoint_id}`;
- `reason_codes: []`.

## 5. Compatibility

- Existing registry rows, their certification and the registry route are unchanged.
- Existing contract rows keep their `status`; no build identifier is invented, and no history is backfilled (DAT-REQ-024).
- `SourceRun.connector_build` is unchanged.
- How today's global certification maps to version-level certification is open (D2-g), and it is not mapped.
