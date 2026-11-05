# Obligation definition authoring API: implementation report

Date: 2026-11-04. Branch `naveen-changes`, working tree on top of `73106fe`. No migration; schema head is still 0013.
The `assure` database was not connected to.

**Verdict: PARTIAL.** The independently specified part is implemented and tested.

- **Implemented:** creating obligation definitions from explicit, validated provision links; adding and removing links
  while a definition is unreleased; audit history; read and traceability integration.
- **Refused:** changes to a *released* definition (409), because specifications require definitions to be "versioned"
  but the schema has no definition versioning and the rules for it are not specified. §9 gives a migration design for
  approval.
- **Not invented:** no permission, controlled code or lifecycle state.

## 1. Specification requirements and references

All ZA documents are v1.0, "Proposed for lock". Quotations are from the extracted specification text.

| Question | Spec requires (quoted) | Source |
|---|---|---|
| What a definition is | ObligationDefinition: "Versioned reusable obligation semantics derived from one or more provision versions." Authority: "Regulatory Content owns." | ZA-DOM-001 §6.1 |
| | Obligation Template: "Zoiko-authored structured obligation derived from one or more provisions before entity-specific instantiation. Carries applicability rule references." | ZA-GLS-001 terms |
| | ObligationTemplate: "Provision + applicability rule + default control-expression template + evidence policy; versioned by content release." | ZA-ARCH-001 §5.1 |
| Link cardinality | "ProvisionVersion → ObligationDefinition 1 : N where distinct duty-bearer/use-case encodings are necessary; each link is explicit." "ObligationDefinition → RuleVersion 1 : N over time; exactly one released rule version is effective for a valid instant." | ZA-DOM-001 §13 |
| Physical record | `catalog.obligation_definition`: "obligation_definition_id, provision refs, subject type, coverage mode, rule lineage" — "No binding applicability when coverage != ENCODED." | ZA-DAT-001 §05 |
| Immutability | "PUBLISHED catalogue artifacts MUST be immutable in activated cell catalogues." | DAT-REQ-012 |
| Release | Released provisions link to an authoritative source reference and fingerprint ("FK + non-null release gate"); AI-extracted content needs VERIFIED review before release | ARCH INV-06, INV-09; PRD FR-REG-001/002 |
| Lifecycle of linked content | WITHDRAWN: "removed from release eligibility or use"; SUPERSEDED: "A newer published version replaces this version for future valid time"; AI_EXTRACTED: "non-binding" proposals | ZA-GLS-001 §04 |
| AI provenance | "No AI-generated proposal is indistinguishable from VERIFIED/PUBLISHED content" | DOM-INV-13 |
| Coverage codes | ENCODED / MONITORING_ONLY / NOT_COVERED / RETIRED | ZA-GLS-001 (coverage mode) |
| Satisfaction template | "SatisfactionExpressionTemplate: Default logical requirement over control roles/evidence expectations. Content/Assurance owns; versioned." SatisfactionExpression v1 "MUST support CONTROL, ALL, ANY, K_OF_N and governed COMPENSATION nodes." | ZA-DOM-001 §6.1; STA-REQ-009/010 |
| API conventions | Named actions instead of generic CRUD for governed resources; unknown fields refused; field limits; `GET /v1/obligation-definitions/{id}` "Retrieve encoded obligation definition metadata" | API-REQ-003/014/033; ZA-API-001 §16 |
| Who authors | Not specified. RUL-001 §10 roles and its separation-of-duties matrix cover rule drafts and releases only. Its authoring APIs are `/rule-drafts` and `/content-releases`; there is none for obligation definitions | ZA-RUL-001 §10, §17 |
| Versioning mechanism, lifecycle, approval | Not specified beyond "versioned" / "versioned by content release" | — |

## 2. Existing implementation assessment

| # | Evidence | Finding |
|---|---|---|
| E1 | **Code does** | `catalog_obligation_definition` has `key` (unique), `title`, `instrument_id` FK, `provision_ids` (JSON list, no FK), `subject_type`, `coverage_mode`, `schedule_kind`, `recurrence_days`, `material`, `satisfaction_template`. It has no version, lifecycle, author or record-time columns. |
| E1 | **Gap** | DOM's "versioned" definition has no physical representation. |
| E2 | **Code does** | No API created or changed definitions. They came only from the seed. Earlier tests set links below the API. |
| E2 | **Gap** | MISSING IMPLEMENTATION. |
| E3 | **Code does** | Read: `GET /catalog/obligation-definitions` (list) and `/{id}/trace`. |
| E3 | **Gap** | No single-definition read (API-001 names one). |
| E4 | **Code does** | The release gate (`engines/catalog.release_binding`) refuses to publish a rule unless every linked provision is VERIFIED/PUBLISHED, of the definition's instrument, and grounded in an intact source snapshot. The signed manifest then binds the exact links. |
| E4 | **Assessment** | IMPLEMENTED CORRECTLY. Reused unchanged. |
| E5 | **Code does** | Applicability evaluates a definition's PUBLISHED rule; UNCERTAIN creates no determination; adoption creates one obligation per subject + definition and continues the lineage on re-adoption. |
| E5 | **Assessment** | IMPLEMENTED CORRECTLY. Not changed. |
| E6 | **Code does** | Nothing protected definition links once released: they could be changed below the API (not in 0004). |
| E6 | **Gap** | DAT-REQ-012 not enforced for definitions. Now enforced in the API; database enforcement is part of the migration design in §9. |

## 3. Implemented API endpoints and contracts

All under `/api/catalog`; errors are FastAPI `detail` objects `{error, message, …}` (or a message string for schema
validation).

| Method | Path | Permission | Behaviour |
|---|---|---|---|
| POST | `/obligation-definitions` | `content_author` + platform principal | Create an unreleased definition with 1–50 explicit links → **201** |
| GET | `/obligation-definitions/{odef_id}` | catalogue read (`read`, `audit_read`, content-operations) | Definition with each link resolved to provision, requirement version (with version number) and source snapshot; `released`; `current_rule_version` |
| POST | `/obligation-definitions/{odef_id}/links` | `content_author` + platform | Add one link to an unreleased definition → **201** |
| POST | `/obligation-definitions/{odef_id}/links/{provision_id}/remove` | `content_author` + platform | Remove one link from an unreleased definition, reason required (API-REQ-003 named action, not DELETE) |

Create request (unknown fields refused; whitespace stripped; lengths = column sizes):

```json
{"key": "≤120", "title": "≤300", "instrument_identifier": "…", "subject_type": "LEGAL_ENTITY | BUSINESS_UNIT | REGULATED_ACTIVITY | PRODUCT_SERVICE | ENVIRONMENT | AI_ASSET | CONTRACT_THIRD_PARTY",
 "coverage_mode": "ENCODED (default) | MONITORING_ONLY | NOT_COVERED | RETIRED",
 "schedule_kind": "CONTINUOUS (default) | RECURRING | EVENT_TRIGGERED", "recurrence_days": "1–3660, required for RECURRING only",
 "material": true, "satisfaction_template": {"node_type": "ALL | ANY | K_OF_N | CONTROL | OCCURRENCES | OPTIONAL_EVIDENCE", "…": "…"},
 "links": [{"requirement_version_id": "…", "provision_id": "…"}], "reason": "≤500, optional"}
```

Response (create, read, link and unlink): the definition columns plus:

```json
{"instrument": {"instrument_id": "…", "identifier": "…"},
 "links": [{"provision_id": "…", "provision": {"…": "…"}, "requirement_version": {"requirement_version_id": "…", "version_number": 1, "…": "…"},
            "source": {"source_id": "…", "fingerprint": "…", "locator": "…"}, "missing": false}],
 "released": false, "current_rule_version": null}
```

Documented errors:

| Status | `error` | When |
|---|---|---|
| 422 | (schema) | unknown/server-owned field, length, empty links, invalid subject type / coverage mode / schedule kind, `recurrence_days` inconsistent with schedule, invalid satisfaction template |
| 422 | `DUPLICATE_LINK` | the same provision twice in one request |
| 404 | `REQUIREMENT_VERSION_NOT_FOUND`, `PROVISION_NOT_FOUND` | referenced record missing |
| 404 | — / `LINK_NOT_FOUND` | unknown instrument or definition; provision not linked |
| 409 | `PROVISION_NOT_IN_REQUIREMENT_VERSION` | the provision belongs to a different requirement version |
| 409 | `REQUIREMENT_INSTRUMENT_MISMATCH` | the requirement version is of another instrument |
| 409 | `CONTENT_NOT_LINKABLE` | requirement version or provision not REVIEW_REQUIRED/VERIFIED/PUBLISHED |
| 409 | `OBLIGATION_DEFINITION_KEY_EXISTS` | key taken, including by a concurrent creation |
| 409 | `DUPLICATE_LINK` | provision already linked |
| 409 | `LAST_LINK` | removing the only link |
| 409 | `OBLIGATION_DEFINITION_RELEASED` | any link change once a rule version of the definition is PUBLISHED |
| 403 | — | not a platform content author (recorded as `authorization.denied`) |
| 401 | — | not signed in |

## 4. Authorization and lifecycle decisions

| Decision | Basis | Status |
|---|---|---|
| Authoring needs `content_author` held by a platform principal (Zoiko CONTENT_ANALYST / ENCODING_ENGINEER) | DOM §6.1 "Regulatory Content owns"; GLS "Zoiko-authored". `content_author` is the existing permission for every catalogue authoring step (sources, extraction, requirements, provisions, rule drafts). No new permission | **Interpretation; owner confirmation needed.** No spec assigns definition authoring to a named role |
| Legal reviewer and release manager cannot author definitions | RUL-001 separation of duties keeps authoring, legal approval and release distinct; they hold no `content_author` | Follows existing permissions |
| Linkable content = requirement version and provision both REVIEW_REQUIRED, VERIFIED or PUBLISHED, of the definition's instrument, the provision belonging to the named requirement version | Derived from GLS §04 meanings (WITHDRAWN/SUPERSEDED out of release eligibility; AI_EXTRACTED non-binding), INV-09, DOM-INV-13, and the existing release gate's instrument rule | **Derived rule; flagged for Regulatory Content owner confirmation** |
| At least one link; no duplicate link; a provision may serve several definitions | DOM §6.1 "one or more provision versions"; DOM §13 1:N, "each link is explicit" | Specified |
| A definition is **released** once any of its rule versions is PUBLISHED; links change only before that | DAT-REQ-012; ARCH "versioned by content release"; signed releases already bind the links | Specified (immutability); `released` is a derived boolean, not a new lifecycle code |
| Changing a released definition | Needs definition versioning | **Blocked** (§9) |
| Satisfaction template validation | STA-REQ-010 node set plus the evaluator's OCCURRENCES / OPTIONAL_EVIDENCE nodes; CONTROL keys must exist. COMPENSATION is refused in catalogue content because its `compensation_id` names one organisation's governed approval (DOM-INV-11) | Specified grammar; COMPENSATION refusal derived |
| Schedule kinds | Only the three values the schema already carries | **Gap:** GLS defines no schedule codes; DOM's "one-time" and "windowed" kinds have none |
| Audit | `obligation_definition.create`, `.link`, `.unlink`: actor, time, links, reason; append-only platform audit | No new state codes |

## 5. Traceability integration

- **Forward.** `GET /catalog/requirements/{id}/trace` and `/sources/{id}/trace` list the authored definitions
  through the explicit links.
- **Reverse.** `GET /catalog/obligation-definitions/{id}` and `/{id}/trace` resolve each link to its requirement
  version and source fingerprint, and report missing provisions.
- **Release.** Publication binds exactly the authored links into the signed manifest (tested). Release verification
  then checks them (A9b).
- **Obligation.** The dossier (`GET /obligations/{lineage}`) lists the definition's provisions, each with its
  requirement version ID. Its `regulatory_provenance` names the exact requirement version and provisions that the
  evaluation's signed release bound (tested end to end on an API-authored definition).

## 6. Security and tenant isolation

- Authoring is a platform function. Organisation users of every role, external auditors, agents and the Super Admin
  are refused, and each refusal is recorded. Refused requests change nothing (database fingerprint tests).
- Definitions are global catalogue content with no tenant data; organisations may read them. The obligations made from
  them stay tenant-scoped: another organisation gets 404 (tested).
- Released definitions and historical releases are not modified. Every link change on a released definition, seeded
  ones included, is refused, and the release still verifies INTACT afterwards.
- Applicability evaluation, UNCERTAIN handling, adoption and re-adoption duplicate prevention are unchanged and
  regression-tested on an API-authored definition.

## 7. Changed files

| File | Change |
|---|---|
| `backend/app/engines/catalog.py` | Authoring section: `create_definition`, `add_link`, `remove_link`, `definition_view`, `is_released`, `validate_template`, link rules |
| `backend/app/routers/catalog.py` | Three authoring routes and the single-definition read |
| `backend/tests/test_obligation_definition_authoring.py` (new) | 12 tests (10 + 2 PostgreSQL-only) |
| `backend/tests/test_authorization.py` | The three new content-author mutations in the content analyst's allowed set |
| `backend/tests/read_authorization_matrix.json` | Regenerated: one new read (`catalog.obligation_definition`) for the same 15 catalogue-reading roles; nothing removed |
| `.github/workflows/ci.yml` | `--require test_obligation_definition_authoring=12` |

## 8. Actual test results

| Run | Result |
|---|---|
| `tests/test_obligation_definition_authoring.py`, isolated SQLite | 10 passed, 2 skipped (PostgreSQL-only concurrency) |
| Inventory and regression suites (authorization, read matrix, role registry, traceability, release verification, rule integrity, sidebar contract, event ratchet), SQLite | 146 passed, 9 skipped (PostgreSQL-only) |
| `tests/test_rls.py`, fresh PostgreSQL 17.11 `assure_test_oda_rls` (throwaway cluster, port 55450, CI bootstrap SQL) | 5 passed |
| Full suite, fresh PostgreSQL 17.11 `assure_test_oda` | **472 passed, 0 skipped, 0 failed** (both concurrency tests ran) |
| `junit_gate.py` with every CI `--require` + `test_obligation_definition_authoring=12` | **PASS** (12/12) |
| Full suite, isolated SQLite | **344 passed, 128 skipped (PostgreSQL-only), 0 failed** |

The new module covers:
- valid creation with a satisfaction template, and forward and reverse traceability;
- 17 invalid-input cases, missing requirement versions and provisions, unrelated provisions, other-instrument
  provisions, AI-extracted provisions, and duplicate keys;
- withdrawn, superseded and unreviewed provisions (parametrized);
- link add and remove: duplicate, unknown, last link, reason, history;
- released immutability, including the seeded definitions and historical release integrity;
- the full applicability → adoption → obligation regression;
- unauthorized authoring by seven principal types (recorded) and tenant isolation;
- rollback after a mid-creation failure;
- concurrent duplicate creation and concurrent duplicate linking (PostgreSQL).

## 9. Outstanding blockers, owners and migration design

| # | Blocked or open | Needed | Owner |
|---|---|---|---|
| B1 | **Changing a released definition** (for example adopting a newer requirement version). Refused today | Definition versioning (design below) and its rules: whether every rule release pins a definition revision, and how obligation instances reference it (DAT-REQ-026: references for reconstruction point to immutable revision IDs) | Regulatory Content owner (DOM: "Regulatory Content owns") + Data Architect + RUL owner |
| B2 | Which role authors definitions; `content_author` is used as the closest existing permission | Confirm or assign | IAM Lead + Regulatory Content owner |
| B3 | Whether a definition (its provision selection, duty-bearer/use-case split, DOM §13) needs its own legal review before release; today only the rule draft's legal review and the release gate apply | Approval rule | RUL owner + GC |
| B4 | Schedule kinds: GLS has no codes; "one-time" and "windowed" (DOM §6.1) cannot be represented | Controlled codes | GLS owner |
| B5 | Editing other definition fields (title, schedule, template), retirement or withdrawal of a definition | Lifecycle for definitions | Regulatory Content owner |
| B6 | Whether link changes after a rule draft's legal approval must invalidate that approval (RUL "post-approval mutation" covers the rule artifact only) | Rule | RUL owner |
| B7 | Linkable-content rule (§4) and COMPENSATION refusal in templates are derived, not quoted | Confirmation | Regulatory Content owner; STA owner |

**Migration design (for approval; not created, and not migration 0014).**

1. `catalog_obligation_definition_revision`.
   - Columns: `definition_revision_id`, `obligation_definition_id` FK, `revision` (unique per definition), the
     definition's semantic fields (subject type, coverage mode, schedule, material, satisfaction-template hash), a
     legal-date valid range, `record_seq`, `authored_by`, `reason`.
   - Insert-only for the runtime role. An immutability trigger once a release references the row (DAT-REQ-012; same
     two layers as 0004).
2. `catalog_obligation_definition_link`.
   - Columns: `definition_revision_id` FK, `provision_id` FK, `requirement_version_id` FK.
   - Unique (`definition_revision_id`, `provision_id`).
   - It replaces the JSON `provision_ids`, which has no foreign key. Adding real FKs removes the
     `OBLIGATION_PROVISION_MISSING` class of defect.
3. Nullable `definition_revision_id` on new `catalog_rule_version` rows. The release manifest names it.
4. Existing definitions get revision 1 from today's links. This is a derivable, idempotent backfill (DAT-REQ-024).
5. The JSON column remains a read cache until contract.

The current application and API would move onto these tables unchanged in contract: link changes on a released
definition would create a new revision instead of being refused.
