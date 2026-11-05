# Regulatory release signature verification (A9b): implementation report

Date: 2026-11-04. Branch `naveen-changes`, working tree on top of `73106fe`. No migration; schema head is still 0013.
The `assure` database was not connected to.

**Verdict: PARTIAL.** Every part of A9b that the controlled specifications define is implemented and tested. The part
they leave undecided, which key a cell may *trust* as the content-release authority, is not invented. While it is
undecided, no release is ever reported VALID. An intact release is UNVERIFIABLE with the reason stated, and any detected
alteration is INVALID.

## 1. Specification references and acceptance criteria

All ZA documents are v1.0, "Proposed for lock". ZA-ADR-001 is unapproved, so its ACCEPTED* entries (including C-07 and
RUL-ADR-06) are not in force. Quotations are from the extracted specification text.

| Source | Text (quoted) | What it fixes for A9b |
|---|---|---|
| APP-REQ-006 | "A binding RuleVersion MUST be PUBLISHED, signature-valid, effective for the evaluation time and based on ENCODED coverage." | Signature validity is required for binding use |
| RUL-REQ-027 | "Rule release manifests MUST bind exact RuleVersion and SetVersion hashes and be signed by authorized release infrastructure." | The signer must be *authorized release infrastructure* |
| ZA-RUL-001 §12 | Canonical format: "Versioned canonical JSON profile in v1; UTF-8; deterministic object-key ordering…". Hash algorithm: "Platform cryptographic profile defined by ZA-KEY-001 / security baseline; algorithm identifier stored with digest". Signature: "ContentRelease signs manifest containing every RuleVersion hash and referenced SetVersion hash." | Signed payload = canonical manifest; algorithm must be identifiable |
| ZA-RUL-001 §02 | ContentRelease: "Signed package of RequirementVersion(s), RuleVersion(s), SetVersion refs, changelog, shadow-impact evidence and rollback metadata." | Requirement versions are part of what is signed |
| ZA-RUL-001 §18 | `RUL_SIGNATURE_INVALID`: "Released artifact/manifest signature or hash invalid." | Failure code |
| ZA-DAT-001 §05 | `catalog.content_release`: "content_release_id, manifest hash, signature refs, published_at, status" — "Cells verify before activation." Catalogue activation: "validate signatures/hashes/FKs/compatibility, then atomically activate" | What is verified, and when (activation) |
| DAT-REQ-012 | "PUBLISHED catalogue artifacts MUST be immutable in activated cell catalogues." | Released content must not change |
| PRD FR-REG-001 / FR-REG-003 | Released requirement versions link to an authoritative source reference and fingerprint; "Content releases are signed, independently versioned from software and rollback-capable." | Source fingerprints are part of release integrity |
| SEC-REQ-026 | "Content-release and production-artifact signing keys MUST be purpose-separated and accessible only to authorized release/signing services." | Key separation |
| SEC-REQ-048 | "A content-signature, artifact-signature or evidence-integrity verification failure MUST fail closed for the affected binding/release operation and raise a security signal." | Security signal on failure |
| SEC-REQ-049 | "All cryptographic uses MUST declare purpose, algorithm/profile, key owner, rotation/verification requirements and MUST avoid custom cryptographic primitives." | No undeclared crypto conventions |
| ZA-SEC-001 §14 | "Content-release signing keys: Dedicated signing purpose, separated from encryption keys; tightly limited release service; alert on use." "Key rotation preserves ability to verify historical signatures/hashes where required." | Key handling, historical verification |
| ZA-SEC-001 §19 (security logging) | "Key/signing: Key ID/purpose, caller workload, operation class, result; never key bytes." | Audit content of a verification |
| ZA-ADR-001 C-07 | "Purpose-separated keys: evidence ledger/checkpoint, content release and package signing are distinct." | The cell's shared key cannot be the content authority |
| RUL-ADR-06 (T0) | "Content-signing key profile. Managed signing key under regional security architecture; release service only, no analyst-held private keys." Owners: CTO + Head of Assurance + GC | The trust anchor is a T0 decision |
| THR-012 | "Content-release signing key compromise publishes malicious legal rules globally" — controls include "cell verification" | Why cells verify |

**Acceptance criteria derived** (the roadmap row "A9" lists "tamper and mismatch tests" and blocks on "the verification
key's trust and purpose separation (I-05, C-07, G-06)"):

1. A signature is never accepted without cryptographic verification over the exact signed payload.
2. Altered, missing, substituted or inconsistent release content is detected.
3. Each requirement version and source fingerprint the release records is checked against the catalogue.
4. Outcomes distinguish valid, invalid and unverifiable releases.
5. A failure raises the security signal, and every verification is recorded without key bytes.
6. Nothing is re-signed or modified.
7. The verifying key must be a trusted, purpose-separated content-release key. **This one cannot be met yet; see §4.**

## 2. Existing implementation findings

| # | Evidence | Finding |
|---|---|---|
| F1 | **Code does** | `engines/keys.py` holds one Ed25519 key that signs checkpoints, evidence packages and content releases. Its key ID is `ed25519:` + 16 hex of SHA-256(raw public key), with no version. The key is generated on first use, including by `signer_key_id()` and `verify()`. |
| F1 | **Spec requires** | Purpose-separated content-release keys (SEC-REQ-026, C-07, §14). |
| F1 | **Gap** | The signing key is not a content-release authority (audit I-05). A verification call could *mint* a key. |
| F2 | **Code does** | `GET /catalog/releases/{id}/verify` returned `manifest_hash_ok`, and `signature_ok = keys.verify(manifest, signature)` against the cell key whatever key the release names. It recorded nothing. |
| F2 | **Gap** | `signature_ok` implied trust it does not prove. No integrity check of rule, requirement or source content; no audit; no security signal. |
| F3 | **Code does** | A9a (`engines/applicability._integrity_failure`, before input freeze) re-hashes SetVersions, the AST and the manifest, and checks that the PUBLISHED, *signed* release lists the exact RuleVersion. It does **not** verify the signature. |
| F3 | **Gap** | A consistent rewrite of manifest + manifest hash below the application passes every A9a check. |
| F4 | **Code does** | Publication signs `canonical_json(manifest)`, the RUL §12 canonical profile. Since the traceability work the manifest binds each requirement version, source snapshot ID and fingerprint, locators and provision IDs. |
| F4 | **Gap** | No digest of the released requirement/provision *content*, so an edit of released text below the API was undetectable (database immutability is migration 0014, deferred). |
| F5 | **Spec requires** | "algorithm identifier stored with digest" (RUL §12). |
| F5 | **Code does** | The algorithm is carried only in the key ID prefix (`ed25519:`) and in field names (`ast_sha256`). `manifest_hash` has no algorithm identifier, and the release row has no algorithm column. |
| F6 | **Code does** | Releases are created and verified in the same cell; there is no activation/staging step. |
| F6 | **Spec requires** | "Cells verify before activation" (DAT §05). |

## 3. A9b gaps and how each was handled

| Gap | Handled |
|---|---|
| Signature accepted without proof of trust (F1, F2) | **Implemented, partially.** The signature is verified cryptographically, under the key the release names, over the exact canonical manifest. Trust is reported NOT_ESTABLISHED, so the outcome can never be VALID. **Blocked:** a trusted key (K1) |
| Consistent manifest rewrite undetected (F3) | **Implemented.** The signature check catches it (test: only `SIGNATURE` fails) |
| Requirement/source integrity (F4) | **Implemented.** Bound requirement identity, lifecycle, provisions, source fingerprint, locator and re-hashed source text are checked. New releases record a content digest (`content_sha256`) that is checked |
| Unknown key / unsupported algorithm | **Implemented.** Read from the recorded key ID; reported UNVERIFIABLE, never VALID |
| Key minted by verification (F1) | **Fixed.** Verification uses read-only key access (`existing_public_key`) |
| Audit and security signal (F2) | **Implemented** per SEC §19 and SEC-REQ-048 |
| Verification at activation or binding evaluation (F6, APP-REQ-006) | **Not done:** decision K4, and the brief forbids changing evaluation |
| Algorithm identifier with stored digests (F5) | **Not done:** needs ZA-KEY-001 (K3); a release-row column would need a migration |

## 4. Architecture and implementation decisions

- **Separate module.** `app/engines/release_verification.py` is read-only on catalogue content and does not take part in
  creating or publishing releases. It reuses A9a's `_integrity_failure`, the catalogue engine's `fingerprint_intact` and
  `requirement_content_digest`, and `keys`.
- **Deterministic report.** The same stored state gives the same report: checks run in a fixed order, contain no
  timestamps, and DB-derived lists are sorted.
- **Outcome rule** (`outcome_of`): any FAIL → **INVALID**; else any UNVERIFIABLE → **UNVERIFIABLE**; else **VALID**. The
  `SIGNER_TRUST` check is UNVERIFIABLE until K1 is decided, so VALID is unreachable in every deployment today. That is
  deliberate: "Never treat the existence of a signature record as proof", and RUL-REQ-027 requires *authorized*
  release infrastructure, which the cell's mixed-purpose key is not.
- **Content digest for new releases.** The release binding gains `content_profile` (`za-requirement-content-v1`) and
  `content_sha256`.
  - Algorithm: SHA-256 over the platform canonical JSON profile v1, the same algorithm and convention as the existing
    `ast_sha256` and `manifest_hash`. The field name carries the algorithm, as with `ast_sha256`.
  - Hashed: the requirement version's content fields and the bound provisions' content fields.
  - Excluded: lifecycle codes and the release reference, because publication and supersession change them by design.
  - It is part of the signed manifest, so it is itself signature-protected. Releases made earlier report this check as
    UNVERIFIABLE.
  - *This adds a field to the manifest format, which no specification fixes in detail; it is flagged for RUL owner
    confirmation.*
- **No new algorithm or key convention.** Ed25519 and SHA-256 are what the platform already uses. The verifier
  supports exactly the algorithm the key module implements. Any other recorded algorithm is "unsupported", not guessed.

## 5. API endpoint and response contract

`GET /api/catalog/releases/{release_id}/verify` keeps its path and guard (catalogue readers: `read`, `audit_read` or
content-operations permissions). `404` for an unknown release, `401` unauthenticated, `403` (recorded) for principals
without catalogue read (Super Admin, agents). Response, HTTP 200 for every outcome:

```json
{
  "content_release_id": "…", "label": "…", "published_at": "…", "verifier_version": "za-release-verifier:1.0",
  "outcome": "VALID | INVALID | UNVERIFIABLE",
  "integrity": "INTACT | ALTERED",
  "signature": {"status": "VALID | INVALID | MISSING | MALFORMED | UNSUPPORTED_ALGORITHM | KEY_UNAVAILABLE",
                "algorithm": "ed25519", "key_id": "ed25519:…", "verification_key_id": "ed25519:… | null",
                "key_purpose": "CELL_KEY_NOT_PURPOSE_SEPARATED"},
  "trust": {"status": "NOT_ESTABLISHED", "reason": "…", "required_decisions": ["K1 …", "…"], "references": ["…"]},
  "manifest_hash": "…",
  "checks": [{"dimension": "integrity | signature | trust", "check": "…", "status": "PASS | FAIL | UNVERIFIABLE",
              "message": "…", "rule_version_id | requirement_version_id | source_id": "…"}],
  "summary": {"pass": 0, "fail": 0, "unverifiable": 0},
  "manifest_hash_ok": true, "signature_ok": true, "signer_key_id": "ed25519:…", "public_key_pem": "-----BEGIN PUBLIC KEY-----…"
}
```

The last four fields are the route's pre-A9b response, with unchanged meaning, so the Content Releases page and the
design-system integrity panel keep working. `signature_ok` is true only for a cryptographically valid signature under
an available key with a supported algorithm. **Recommendation:** those screens should show `outcome`, not
`signature_ok`, as the verification result.

Checks, in order:

| Group | Checks |
|---|---|
| Manifest | `MANIFEST_WELL_FORMED`, `MANIFEST_HASH`, `MANIFEST_MATCHES_RELEASE_RECORD`, `RELEASE_STATUS` |
| Signature | `SIGNATURE_PRESENT`, `SIGNATURE_ALGORITHM`, `SIGNATURE_FORMAT`, `SIGNER_KEY_AVAILABLE`, `SIGNATURE` |
| Trust | `SIGNER_TRUST` |
| Per manifest rule version | `RULE_VERSION_PRESENT`, `RULE_VERSION_BINDS_RELEASE`, `RULE_VERSION_INTEGRITY` (A9a); then `RELEASE_COVERS_RULE_VERSIONS` |
| Requirement binding | `REQUIREMENT_BINDING`; per bound version `REQUIREMENT_VERSION_PRESENT`, `_IDENTITY`, `_LIFECYCLE`, `PROVISIONS_PRESENT`, `REQUIREMENT_CONTENT_DIGEST`, `SOURCE_PRESENT`, `SOURCE_FINGERPRINT`, `SOURCE_LOCATOR`, `SOURCE_CONTENT` |

**Side effects** (per verification):
- An audit record `content.verify` (object `ContentRelease`, new state = outcome) in the verifier's own trail: the
  organisation for tenant users, the platform for Zoiko staff. Its reason follows SEC §19: `operation=verify
  purpose=content_release key_id=… verifier=… result=… integrity=… signature=… trust=… failed=[…] unverifiable=[…]`.
- On **INVALID** only, `security.content_signature_failed` (A9a's event and payload keys, plus `failed_checks`) in the
  same tenant scope.
- The release itself is never changed, re-signed or re-hashed.

## 6. Cryptographic and key-management approach

| Item | What is used | Specification status |
|---|---|---|
| Signed payload | `canonical_json(manifest)`: sorted keys, UTF-8, no insignificant whitespace | RUL §12 canonical JSON profile v1 (**Spec requires**; code complies) |
| Signature algorithm | Ed25519, the platform's only signing algorithm; identified by the `ed25519:` key-ID prefix | **Gap:** ZA-KEY-001 (named by RUL §12) does not exist; Ed25519 appears in the specs only for webhooks (C-11, API-ADR-06) |
| Digests | SHA-256 (`manifest_hash`, `ast_sha256`, new `content_sha256`, source fingerprints) | **Gap:** "algorithm identifier stored with digest" is met only by field naming, not for `manifest_hash` |
| Verifying key | The cell signing key, read without creation; must equal the key ID the release names | **Gap:** C-07 / SEC-REQ-026 require a purpose-separated content-release key; RUL-ADR-06 (T0) undecided |
| Key distribution, versioning, rotation, historical verification | None: one key, no registry; a release naming another key is UNVERIFIABLE | **Gap:** SEC §14 requires historical verification across rotation (K2) |
| Key bytes | Never in responses, logs or audit; only the public PEM, which the route already returned | SEC §19 (**Spec requires**; code complies) |

## 7. Security and tenant isolation

- Verification never treats a stored signature as valid: it checks presence, algorithm, format and key, then
  cryptographic validity over the recomputed payload.
- An absent signature is INVALID (`SIGNATURE_PRESENT`). A9a also refuses an unsigned release (`RELEASE_UNSIGNED`).
- Nothing is regenerated. A test proves the original signature is unchanged after every failed verification, and that
  verification does not create a key file when none exists.
- Existing source fingerprint checks are unchanged and reused. The release check adds the bound-fingerprint comparison.
- Releases are global content: the report contains no tenant data and is identical for every caller. Each tenant's
  verification records stay in its own trail (tenant predicate + FORCE RLS). Organisations do not see each other's
  records or Zoiko's (tested).
- Evaluation and adoption logic are unchanged. `SEC-REQ-048`'s "fail closed for the affected binding/release
  operation" remains A9a's integrity check at evaluation. Adding a signature check there is decision K4.

## 8. Files created and modified

| File | Change |
|---|---|
| `backend/app/engines/release_verification.py` (new) | Verification engine, outcome rule, audit/security-signal recording |
| `backend/app/engines/keys.py` | Read-only accessors: `existing_public_key`, `key_id_of`, `public_pem_of`, `verify_with`, `SIGNING_KEY_FILE`; existing functions unchanged in behaviour |
| `backend/app/engines/catalog.py` | `requirement_content_digest`, `CONTENT_PROFILE`; the release binding records `content_profile` + `content_sha256` |
| `backend/app/routers/catalog.py` | `GET /releases/{id}/verify` uses the engine and records the verification |
| `backend/tests/test_release_signature_verification.py` (new) | 28 tests |
| `backend/tests/test_regulatory_traceability.py` | Binding assertion includes the digest and checks it verifies |
| `.github/workflows/ci.yml` | `--require test_release_signature_verification=28` |

Not modified: migrations, applicability evaluation, obligation adoption, connector certification, AI authority, the
frontend.

## 9. Test commands and actual results

```
# SQLite (isolated; from backend/)
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests
# PostgreSQL 17: throwaway initdb cluster on 127.0.0.1:55450, CI bootstrap SQL (non-superuser owner `assure`),
# fresh databases assure_test_sig_rls and assure_test_sig
ZA_TEST_DATABASE_ADMIN_URL=…/assure_test_sig_rls ZA_TEST_DATABASE_URL=…/assure_test_sig_rls pytest tests/test_rls.py
ZA_TEST_DATABASE_ADMIN_URL=…/assure_test_sig ZA_TEST_DATABASE_URL=…/assure_test_sig pytest tests --junitxml=…
python ../.github/scripts/junit_gate.py --report … --no-skips --require … (every CI line)
```

The new module's 28 tests:

| Area | Tests |
|---|---|
| Valid case | intact release |
| Signatures | invalid, not hex, truncated, missing |
| Keys and algorithms | unknown key, unsupported algorithm, undeclared algorithm, no key held (nothing minted) |
| Altered manifest | altered; rewritten consistently with its hash |
| Substitution | substituted manifest/signature; rule version re-pointed |
| Changed records | requirement locator, title, lifecycle; provision text; source fingerprint, text and locator |
| Missing references | missing provision; missing requirement version |
| Behaviour | repeated verification (deterministic, audited); release never modified; outcome rules (5) |
| Access | authorization, tenant isolation, audit trail, tenant-scoped security signal |

None of the 28 is PostgreSQL-only: `_tampered` lifts the immutability trigger on PostgreSQL and needs nothing on SQLite.

| Run | Result |
|---|---|
| `tests/test_release_signature_verification.py`, SQLite | 28 passed |
| Closest regression suites (traceability, rule integrity, immutability, read matrix, event ratchet, sidebar), SQLite | 83 passed, 13 skipped (PostgreSQL-only) |
| `tests/test_rls.py`, fresh PostgreSQL 17.11 | 5 passed |
| Full suite, isolated SQLite | **334 passed, 126 skipped (PostgreSQL-only), 0 failed** |
| Full suite, fresh PostgreSQL 17.11 `assure_test_sig` | **460 passed, 0 skipped, 0 failed** |
| `junit_gate.py` with every CI `--require` + `test_release_signature_verification=28` | **PASS** (28/28) |

## 10. Remaining blockers and decisions required

| # | Decision | Why it blocks | Approvers (per spec) |
|---|---|---|---|
| K1 | Which key a cell trusts for content releases, and how its public part is distributed and pinned | Without it no release can be VALID (RUL-REQ-027, C-07, SEC-REQ-026) | RUL-ADR-06 (T0): CTO + Head of Assurance + GC; G-06: CTO + Security |
| K2 | Key identity, versioning, rotation, verify-only historical keys | SEC §14 historical verification; today a release naming another key is UNVERIFIABLE | CTO + Security |
| K3 | Algorithm/signature profile and digest algorithm identifiers | RUL §12 names ZA-KEY-001, which does not exist | CTO + Security; ZA-KEY-001 owner |
| K4 | Where verification runs: catalogue activation (DAT §05 staging) and/or every binding evaluation (APP-REQ-006) | Changes the evaluation path, out of scope here; there is no activation stage today | Data Architect + CTO |
| K5 | Disposition of releases signed with the cell key (re-release under the content key, or recorded legacy acceptance) | Published rows are immutable (RUL-INV-01) | Head of Assurance + CTO |
| K6 | KMS/HSM provider for the content-release key | RUL-ADR-06 "managed signing key"; THR-012 | CTO + Security + GC |
| R1 | Confirm the `content_sha256` manifest field and its hashed field set | Manifest format beyond RUL §02/§12 is not specified | RUL owner (Regulatory Content) |

**Migration required?** Not for this work. Implementing K1–K3 later will probably need one. Candidates: a signature
algorithm/key-version reference on `catalog_content_release`, and possibly a key registry or trust store. These must
be designed with those decisions, since the table is immutable (0004) and the DAT-REQ-039 review applies.
