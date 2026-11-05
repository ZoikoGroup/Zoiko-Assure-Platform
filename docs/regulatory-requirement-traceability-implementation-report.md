# Regulatory requirement and traceability engine: implementation report

Date: 2026-11-04. Branch `naveen-changes`, working tree on top of `73106fe`. Owner: Regulatory & Obligation Core Backend.
Schema head is unchanged at **0013**: this work adds no migration (§9 explains why, and what one would add).

Chain covered: **Source → Requirement → Facts → Applicability → Adoption → Obligation → Controls**. Controls are reached
through the obligation's existing satisfaction expression. Nothing in that part changed.

## 1. Specification references

All ZA documents are still "Proposed for lock". Every ID below was checked against the extracted specification text.

| Document | Sections used | What it fixes |
|---|---|---|
| ZA-DOM-001 | §1 (canonical chain), §4.1 regulatory catalogue ontology, §4.2 provision anatomy, §13 cardinalities, §14 WF-DOM-01, §15 DOM-INV-01/04/13/15, §16 end-to-end trace | RegulatorySource 1:N **immutable** SourceSnapshot (digest); Requirement 1:N RequirementVersion; RequirementVersion 1:N ProvisionVersion; ProvisionVersion → ObligationDefinition by explicit link; a correction preserves history |
| ZA-DAT-001 | §02 catalog schema, §05 regulatory catalogue schema, DAT-REQ-009/012/014/026 | `source_record`: "Fingerprint + authoritative reference required before VERIFIED/PUBLISHED content"; `requirement_version`: "PUBLISHED immutable; exact source fingerprint FK"; PUBLISHED catalogue immutable |
| ZA-GLS-001 | §04 regulatory content lifecycle | The only allowed moves: DISCOVERED→INGESTED/WITHDRAWN; INGESTED→CLASSIFIED/WITHDRAWN; CLASSIFIED→AI_EXTRACTED/REVIEW_REQUIRED; AI_EXTRACTED→REVIEW_REQUIRED; REVIEW_REQUIRED→VERIFIED/WITHDRAWN; VERIFIED→PUBLISHED/REVIEW_REQUIRED; PUBLISHED→SUPERSEDED/WITHDRAWN; WITHDRAWN→REVIEW_REQUIRED; SUPERSEDED terminal |
| ZA-ARCH-001 | §5.1 obligation anatomy, §7 content operations, INV-06, INV-09 | INV-06 "Every released provision links to an authoritative source reference and source fingerprint. Proof: FK + non-null release gate"; INV-09 AI content needs VERIFIED human review before release |
| ZA-PRD-001 | FR-REG-001, FR-REG-002, FR-REG-005 | Released requirement versions link to source reference + fingerprint; AI content never PUBLISHED without VERIFIED; rights metadata controls display |
| ZA-RUL-001 | §02 (ContentRelease = "signed package of RequirementVersion(s), RuleVersion(s)…"; RuleVersion envelope `requirement_version_ids[]`), §08 source mapping, §11 validation stage 2, RUL-REQ-008/026 | Releases bind the requirement versions they rest on; source mappings carry `requirement_version_id`/`provision_id` and `source_fingerprint`; "release tooling must show coverage and orphaned mappings" |
| ZA-EVT-001 | §19 catalogue | `requirement.version_published`, `requirement.version_superseded` (catalog, WEBHOOK_ELIGIBLE) |
| ZA-API-001 | API-REQ-012/014/023/033, §04, §11 | Cursor pagination with stable order and pinned watermark; unknown fields refused; allowlisted typed filters; field-length limits |
| ZA-APP-001 / ZA-DOM-001 DOM-INV-02/03/04 | — | Evaluations store exact rule version and fact revisions; UNCERTAIN never becomes an obligation; obligations only from attested adoption |

ZA-TRC-001 is the *engineering* trace contract (spec ↔ code ↔ test). It does not govern regulatory source → requirement
traceability, so it is not the authority for Phase C. Its PRD matrix does name the FR-REG-001 proof: "release FK/non-null
+ provenance tests".

## 2. Existing implementation, assessed before changing anything

Classification per finding category: IMPLEMENTED CORRECTLY / PARTIAL / MISSING / SPEC VIOLATION / DOCUMENT GAP.

| Area | State found | Class |
|---|---|---|
| Source registration (`POST /catalog/sources`) | Stored fingerprint = SHA-256 of the text and a rights record, but: no field validation; an unknown instrument identifier was silently ignored; the same snapshot could be registered any number of times; no snapshot history | PARTIAL |
| Source lifecycle | Extraction set the source to REVIEW_REQUIRED from **any** state (an INGESTED, unclassified source skipped CLASSIFIED; a PUBLISHED source regressed) | SPEC VIOLATION (GLS §04) |
| Classification | Only possible at ingestion; an unclassified source could never be classified later | MISSING |
| Requirement creation | Only through the AI extractor; no Zoiko-encoded path; no identity or duplicate checks; no audit of creation | PARTIAL |
| Extractor heuristic | `must not` / `shall not` matched as `must` / `shall`, so prohibitions were proposed as MUST | SPEC VIOLATION (DOM §4.2: no legal force inferred wrongly) |
| Provision editing | In-place edit with no history; SUPERSEDED or WITHDRAWN provisions editable; parent version not checked | PARTIAL (DOM-INV-15) |
| Verification | REVIEW_REQUIRED → VERIFIED by a human legal reviewer; AI proposals detected only by a `TBD_BY_REVIEWER` marker; source grounding not checked; no row lock | PARTIAL |
| Requirement retrieval | One version by ID only; no list, filter, pagination or version history | MISSING |
| Traceability | No source→requirement→obligation or reverse lookups; obligation definition → provision link is an unvalidated JSON list | MISSING |
| Release gate | Rule publication never checked that the provisions it releases were verified or source-grounded; the release manifest did not name requirement versions; no path ever took a requirement version to PUBLISHED | MISSING (INV-06, INV-09, FR-REG-001/002, RUL §02) |
| Source mappings | Validator checks each predicate *carries* a mapping reference, not that the reference resolves; seeded mapping records have only a text locator | PARTIAL (RUL §08, §11) |
| Applicability | Evaluation stores the exact rule version, frozen inputs, evaluator build; UNCERTAIN creates no determination | IMPLEMENTED CORRECTLY (not changed) |
| Obligation generation | Only from an ADOPTED determination of an APPLIES evaluation, with attestation; one current revision per subject + definition (re-adoption continues the lineage) | IMPLEMENTED CORRECTLY (not changed) |
| Database immutability | 0004 protects rule versions, releases, set versions, fact definitions. Its docstring excludes source records, provisions and requirement versions: "they need an append-only lifecycle first (Stage 3c)" (audit I-04) | PARTIAL, see §9 |

## 3. Backend modules changed

| File | Change |
|---|---|
| `backend/app/engines/catalog.py` (new) | The engine. Covers: the GLS-001 §04 transition table and `move()`, which applies and records every lifecycle change; source snapshot identity and versions; fingerprint integrity; requirement identity, duplicate and version-number rules; creation; verification; provision revision with change history; the release binding (`release_binding`), with publication and supersession of bound versions (`publish_bound`); forward and reverse traces; source-mapping coverage; whole-catalogue link validation |
| `backend/app/routers/catalog.py` | Strict request models (unknown fields refused, whitespace stripped, lengths = column sizes). All lifecycle changes go through the engine. Extractor alternation fixed. New routes (§4). Every existing response shape is kept |
| `backend/app/routers/rules.py` | Publication: release gate before any write; requirement binding in the signed manifest; bound versions published, older ones superseded, in the publication transaction |
| `backend/app/routers/applicability.py` | `GET /applicability/evaluations/{id}` gains `regulatory_provenance` (additive) |
| `backend/app/routers/assurance.py` | `GET /obligations/{lineage}` gains `regulatory_provenance` (additive) |
| `backend/tests/test_regulatory_traceability.py` (new) | 18 tests (16 + 2 PostgreSQL-only) |
| `backend/tests/test_authorization.py` | The two new content-author mutations added to the content analyst's allowed set |
| `backend/tests/test_role_registry.py` | `source_id`, `odef_id` classified as global catalogue identifiers in the generated cross-tenant test |
| `backend/tests/test_dat_req_039_review.py` | The two new event names classified as ZA-EVT-001-catalogued |
| `backend/tests/read_authorization_matrix.json` | Regenerated: additions only (§5) |
| `.github/workflows/ci.yml` | `--require test_regulatory_traceability=18` |

Not touched: connector certification, AI authority, applicability evaluation algorithm, migrations 0001–0013, the
`assure` database.

## 4. API endpoints

New (all under `/api/catalog`):

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/sources/{source_id}` | catalogue read¹ | One snapshot, rights-aware text, `fingerprint_intact`, the requirement versions grounded in it |
| GET | `/sources/{source_id}/versions` | catalogue read | Every snapshot of the same source (authority + jurisdiction + authoritative locator), numbered, with `fingerprint_changed` |
| GET | `/sources/{source_id}/trace` | catalogue read | Forward: snapshot → requirement versions → provisions → obligation definitions → current rule and its binding |
| POST | `/sources/{source_id}/classify` | `content_author` + platform | INGESTED → CLASSIFIED against a registered instrument |
| POST | `/requirements` | `content_author` + platform | Zoiko-encoded requirement version with 1–50 provisions, REVIEW_REQUIRED; same key + instrument = next version |
| GET | `/requirements` | catalogue read | Filters `instrument`, `requirement_key`, `lifecycle`, `origin`, `source_id`, `jurisdiction`, `valid_at`; cursor envelope (§04/§11) |
| GET | `/requirements/{rv_id}/history` | catalogue read | All versions of the requirement with their source snapshots; plus, for Zoiko content staff, the recorded lifecycle and edit events |
| GET | `/requirements/{rv_id}/trace` | catalogue read | Reverse to the exact snapshot (and newer snapshots of it), forward to provisions, definitions, rules; this version's validation issues |
| GET | `/obligation-definitions/{odef_id}/trace` | catalogue read | Definition → provisions → requirement versions → snapshots; missing provision links; current rule provenance |
| GET | `/traceability/validation` | Zoiko content staff | End-to-end link check of the whole catalogue (§6) |

¹ catalogue read = `read`, `audit_read` or a content-operations permission, the same set as every existing catalogue
read.

Changed:

| Method | Path | Change |
|---|---|---|
| POST | `/catalog/sources` | Validation; duplicate snapshot → 409 `DUPLICATE_SOURCE_SNAPSHOT` (with the existing `source_id`); unknown instrument → 404; blank instrument = not classified yet (the frontend form's default, so backward compatible); audited as `source.ingest` then `source.classify` |
| POST | `/catalog/sources/{id}/extract` | Needs a CLASSIFIED (not withdrawn/superseded) source whose rights permit derivation → else 409 `SOURCE_NOT_CLASSIFIED` / `SOURCE_NOT_USABLE`; identity and duplicate checks; `MUST_NOT` detected; audited `requirement.create` |
| PUT | `/catalog/provisions/{id}` | Lengths; optional `reason`; only AI_EXTRACTED / REVIEW_REQUIRED provisions of a REVIEW_REQUIRED version → else 409 `PROVISION_NOT_EDITABLE`; each edit audited `provision.revise` with the prior values of the changed fields |
| POST | `/catalog/requirements/{id}/verify` | Row-locked; needs an intact, current, grounded source snapshot (`SOURCE_NOT_GROUNDED`), at least one provision (`REQUIREMENT_HAS_NO_PROVISIONS`), every provision reviewed (`PROVISIONS_NOT_REVIEWED`). Error bodies are now coded objects `{error, message, …}`; the frontend's `describeDetail` already renders those |
| GET | `/catalog/requirements/{id}` | + `version_number`, `versions_total` |
| POST | `/rules/drafts/{id}/publish` | Release gate → 409 `RELEASE_CONTENT_NOT_VERIFIED` with `problems[]`, before any write. Manifest gains `requirement_versions[]`. Response gains `requirement_versions: {published, superseded}` |
| GET | `/applicability/evaluations/{id}` | + `regulatory_provenance` |
| GET | `/obligations/{lineage}` | + `regulatory_provenance` (with the evaluation and determination revision it came from) |

## 5. Database entities and relationships (unchanged schema)

```
catalog_source_record (one snapshot: fingerprint = SHA-256(raw_text); identity = authority + jurisdiction + locator)
   1 ── N  catalog_requirement_version (requirement_key = stable identity within one instrument; source_id FK = exact snapshot)
              N ── 1  catalog_regulatory_instrument
              1 ── N  catalog_provision (atomic duty)
                         N ── M  catalog_obligation_definition.provision_ids (JSON link, validated in code)
                                    1 ── N  catalog_rule_version ── 1  catalog_content_release
                                                                        manifest.requirement_versions[] (signed binding, new)
                                    1 ── N  applicability_evaluation (rule_version_id) → determination → obligation instance
platform_audit_event (append-only): source.ingest/classify/lifecycle, requirement.create/verify/lifecycle,
                                    provision.lifecycle/revise
platform_outbox_event: requirement.version_published / requirement.version_superseded (platform, no tenant)
```

The `source_id` FK pins the exact snapshot, and the snapshot's fingerprint is never rewritten by the API. That is how
DAT §05's "exact source fingerprint FK" is met on the current schema. The database does not yet enforce it (§9).

Read-policy delta (`tests/read_authorization_matrix.json`, provisional pending G-08). It is additions only:

- The 7 new catalogue GETs: readable by exactly the roles that already read the catalogue.
- `traceability_validation`: Zoiko content staff only.

## 6. Requirement traceability design

- **Source identity and versions.** A source snapshot is registered once per fingerprint. Changed text of the same
  source (same authority, jurisdiction, authoritative locator) is a new snapshot. Earlier snapshots and the requirement
  versions grounded in them are never altered. `fingerprint_intact` re-hashes the stored text, so an altered record is
  detected.
- **Requirement identity and versions.** `requirement_key` belongs to one instrument (`REQUIREMENT_KEY_CONFLICT`). The
  same source snapshot + locator + effective date is not recorded twice (`DUPLICATE_REQUIREMENT_VERSION`). A new version
  is a new row. `version_number` is its order by record sequence. Creation is serialised per instrument by a row lock,
  so concurrent duplicates are refused.
- **Lifecycle and history.** Every move goes through the GLS-001 §04 table. Each move writes an append-only audit record
  (prior → new state, actor, reason, record sequence), and each provision edit records the prior values of what it
  changed. Released content is never edited: a change is a new version.
- **Release binding (the obligation boundary on the content side).** Publication refuses unless every provision of the
  obligation definition belongs to a VERIFIED/PUBLISHED requirement version grounded in an intact, non-withdrawn
  snapshot of the same instrument. The signed manifest then names each requirement version, its key, source snapshot,
  source fingerprint, locators and provision IDs. Bound VERIFIED versions and their provisions become PUBLISHED. Older
  PUBLISHED versions of the same requirement become SUPERSEDED, with their provisions, and their content is untouched.
- **Provenance from tenant records.** An evaluation names its exact rule version. Provenance follows that rule version to
  its release binding (`RELEASE_BOUND`). For releases made before binding existed it follows the definition's current
  links (`DEFINITION_DERIVED`, stated as such). An obligation's provenance goes through its adopted determination's
  evaluation, so it still names the version actually used after a newer one supersedes it. Each required fact is linked
  to the source mappings of the predicates that read it, and through them to requirement versions and provisions.
- **Validation.** `GET /catalog/traceability/validation` checks the following.
  - Sources: altered text (`SOURCE_FINGERPRINT_MISMATCH`).
  - Requirement versions:
    - missing source or instrument;
    - ungrounded source (ERROR once verified or published);
    - released content resting on a withdrawn source;
    - AI content without a reviewer;
    - no provisions.
  - One requirement key under several instruments.
  - Provisions: orphaned, or lifecycle ahead of their version.
  - Obligation definitions:
    - no provisions;
    - missing provision;
    - provision never verified behind a released rule;
    - instrument mismatch.
  - Rules: unresolved, ungrounded and orphaned source mappings (RUL §08); releases not binding requirements; binding
    fingerprint drift.

## 7. Implemented functionality against the completion criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Validated regulatory source references | Done: validation, instrument check, duplicate snapshot refusal, grounding and integrity checks at verification and release |
| 2 | Structured regulatory requirements | Done: Zoiko-encoded creation with atomic provisions; AI extraction kept non-binding |
| 3 | Source-version traceability | Done at the API and in signed releases; database-level immutability of source and requirement content is §9 |
| 4 | Requirement version history | Done: `/history` (versions + recorded lifecycle and edit events) |
| 5 | Approved applicability integration | Done without changing the algorithm: provenance from the exact rule version, fact → source-mapping → requirement links |
| 6 | Defined obligation-generation boundary | Done: content side = release gate + binding; tenant side = existing adoption rules (verified by tests, unchanged) |
| 7 | PostgreSQL 17 test evidence | §8 |
| 8 | Implementation and gap documentation | This report |

## 8. Test results

`backend/tests/test_regulatory_traceability.py` (18 tests). It covers:
- source validation, invalid instrument references, duplicate snapshots and source-version integrity;
- classification and the lifecycle rules;
- extraction preconditions, requirement validation, duplicate and cross-instrument identifiers, and version history;
- verification rules, including that AI content is never verified directly;
- listing filters and cursor pagination;
- forward and reverse traceability, and missing or broken links;
- the release gate refusing unverified content, the signed binding, publication, and supersession without rewriting;
- facts → evaluation → adoption → obligation provenance and tenant isolation;
- unauthorized modification by every non-owning role, with each refusal recorded;
- transaction rollback after a mid-creation failure;
- concurrent verification and concurrent duplicate creation on PostgreSQL.

| Run | Database | Result |
|---|---|---|
| Full backend suite | isolated SQLite | **306 passed, 126 skipped (PostgreSQL-only), 0 failed** |
| `tests/test_regulatory_traceability.py` | isolated SQLite | 16 passed, 2 skipped (PostgreSQL-only) |
| `tests/test_rls.py` (CI fail-fast step) | fresh PostgreSQL 17.11 `assure_test_rt_rls`, throwaway cluster (port 55450, CI bootstrap SQL, non-superuser owner) | 5 passed |
| Full backend suite | fresh PostgreSQL 17.11 `assure_test_rt`, same cluster | **432 passed, 0 skipped, 0 failed** |
| `.github/scripts/junit_gate.py`, every CI `--require` + `test_regulatory_traceability=18` | same report | **PASS** (18/18 for the new module) |

During this work, the new tests found and the fixes covered two defects:
- **Extractor:** prohibitions ("must not", "shall not") were proposed as MUST.
- **Supersession:** an obligation-definition link to a superseded version's provisions would still have been releasable.

The canonical `assure` database was never connected to.

## 9. Remaining gaps and owner decisions

| # | Gap | Spec | Why not done here | Decision owner |
|---|---|---|---|---|
| 1 | **Database-enforced immutability and append-only lifecycle for catalogue content.** An event table (`catalog_content_lifecycle_event`, the 0008/0011/0012 convention) and guards would cover two things. First: source record content columns, VERIFIED/PUBLISHED requirement-version and provision content, and DELETE. Second: the runtime role keeping only the lifecycle column change. History is currently the append-only audit table, and immutability is enforced in the API only | DAT-REQ-009/012, DAT §05, DOM §4.1, audit I-04, roadmap Stage 3c (B2) | It needs migration **0014**. The local API refuses to start until `assure` is upgraded, and this brief forbids touching `assure`. The event-table convention is the open B2 item for the ZA-TIM/STA/DAT owners | You (approve 0014 and a dev-DB upgrade window); Data Architect (convention) |
| 2 | Physical model is flatter than DOM-001: no InstrumentVersion, no stable Requirement / RegulatorySource rows, no source → instrument column; source mappings are JSON inside RuleVersion although DAT §02 lists `catalog.source_mapping` | DOM §4.1, §13; DAT §02, §05 | Logical vs physical model gap; resolving it is a schema design decision, not inferable | Data Architect + Regulatory Content owner (DOCUMENT GAP) |
| 3 | Seeded releases cite 6 source mappings they do not carry and hold 5 mapping records with no requirement/provision reference. All 4 seeded releases predate requirement binding | RUL §08, §11 stage 2, RUL-REQ-026 | Published rows are immutable; the fix is corrected content releases | Content Release Manager |
| 4 | Unresolved source-mapping references are reported, not blocking. RUL §11 stage 2 says they should fail validation | RUL §11 | Making it blocking prevents republishing the seeded rules until #3 is fixed | Content Release Manager / RUL owner |
| 5 | Withdrawal (WITHDRAWN with reason) and reintroduction of sources and requirement versions are not exposed | GLS §04 | GLS names "content release authority" / "authorized content owner" / "content workflow", but no mapping to IAM permissions exists | Regulatory Content owner + IAM Lead |
| 6 | Obligation-definition authoring (creating definitions, re-pointing provision links) has no API; the tests set links below the API | DOM §13 (explicit links), RUL WF-RUL-01 | A content-authoring workflow not specified at this depth | Regulatory Content owner |
| 7 | No controlled vocabularies for modality, duty-bearer role, rights class, source type or requirement identifier format; validated by length only | DOM §4.2 ("controlled role vocabulary"), GLS | GLS-001 does not list them; inventing codes is out of bounds | GLS owner |
| 8 | Binding evaluation does not refuse content withdrawn after release (GLS: WITHDRAWN = "removed from release eligibility or use") | GLS §04 | No withdrawal path exists yet (#5); changes the evaluation path | APP/RUL owners |
| 9 | Content-release signature verification (A9b) | RUL-REQ-027, SEC | Trust anchor undecided (G-06, C-07) | Security Architect |
| 10 | Outbox delivery: `requirement.version_*` events are written but no relay delivers them; `regulation.version_published` is not emitted for snapshots (ingestion still emits the uncatalogued `regulatory.source_ingested`) | EVT-001 | Relay is Stage 8; mapping the legacy name is the EVT owner's call | EVT owner |
| 11 | Error bodies are FastAPI `detail` objects, not `application/problem+json`; no Idempotency-Key | API-REQ-017, API-REQ-004 | Cross-cutting contract changes | API / Platform Engineering Lead |
