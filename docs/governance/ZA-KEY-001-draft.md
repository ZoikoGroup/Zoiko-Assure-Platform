# ZA-KEY-001: Platform cryptographic profile & signing-key management (DRAFT)

> **Status: DOC_DRAFT, proposed new controlled document. Not registered, not reviewed, not approved.**
>
> Prepared by engineering with AI assistance on 2026-11-04. This draft **selects no algorithm, KMS/HSM provider,
> rotation period or trust-root mechanism.** Every such choice appears as a **SELECTION REQUIRED** item with options,
> and only the named human approvers can close it (ADR-REQ-005/006; ZA-DOC-001 §06 class C3).

## 0. Draft control

| Field | Value |
|---|---|
| Proposed Document ID | ZA-KEY-001 |
| Why this ID | ZA-RUL-001 §12 names it: "Hash algorithm: Platform cryptographic profile defined by ZA-KEY-001 / security baseline; algorithm identifier stored with digest." **It is not in the Mandatory Engineering Document Register (22 documents) or the ZA-DOC-001 estate.** The register allows "specialist … child specifications" to be "added as their modules enter build" (Mandatory Register §01) |
| Registration | The ZA-DOC-001 owner creates the DocumentRecord (`engineering.document_registered`; DOC-REQ-001: stable ID, version, owner, authority domain, lifecycle state). The precedence position relative to ZA-SEC-001 and ZA-EVD-001 is set at registration. Proposal: below ZA-EVD-001 for evidence keys, and controlling for content-release signing |
| Alternative (RUL §12 "/ security baseline") | Name an existing security baseline as the profile authority instead. The SEC-REQ-049 declarations in Part A would then move into that baseline. That choice belongs to the ZA-DOC-001 owner and CTO |
| Change impact class | **C3 ARCHITECTURAL / LIABILITY** (cryptographic trust; ZA-DOC-001 §06): "T0 ADR; CTO plus relevant Legal/Security/Privacy/Assurance approval; baseline cannot advance until reconciled" |
| Proposed owner | Security Architect (ZA-SEC-001 §14 is the platform cryptography boundary; EVD-ADR-02/03 owners are Security Architect + Evidence Lead) |
| Accountable approvers (proposed) | CTO; Global Head of Cybersecurity; Head of Assurance and General Counsel. HoA and GC sign RUL-ADR-06 (content-signing key profile), which this document details |
| Required reviewers (proposed) | Evidence Lead; Data Architect; Regulatory Content Lead; API Lead; SRE Lead; QA Lead; Privacy Lead |
| Decisions served | **K3** (closes directly); **K1, K2, K5, K6, R1** (supplies the profile they reference); **B1-c** (manifest key/algorithm fields). See the [decision register](../regulatory-catalogue-governance-decision-register.md) |

### 0.1 How to read this draft

| Marker | Meaning |
|---|---|
| **MANDATED** | Restates controlled-specification text; the source is cited. ZA-KEY-001 cannot relax it |
| **PROPOSED** | Engineering-proposed normative text that no controlled spec states. Approve, amend or delete it |
| **SELECTION REQUIRED** | A choice among listed options; blank until a named approver decides |
| *Engineering note* | Non-normative context, including current implementation behaviour |

All cited specifications are v1.0 "Proposed". ADR register entries are ACCEPTED\*, meaning "on approval of" ZA-ADR-001,
and **ZA-ADR-001 is not approved**. Citations to them show intended direction, not authority in force.

---

## Part A: Binding requirements (proposed normative text)

### A1. Scope and the cryptographic-use declaration

**A1.1 MANDATED** (SEC-REQ-049): "All cryptographic uses MUST declare purpose, algorithm/profile, key owner,
rotation/verification requirements and MUST avoid custom cryptographic primitives."

**A1.2 PROPOSED:** Every use is declared as a row of the Cryptographic Use Register (A1.3). A use without an approved
row MUST NOT be released to production.

**A1.3 PROPOSED register structure.** The purpose identifiers below are proposed names, not GLS codes. Profile and key
columns stay blank until the Part B selections are made.

| Use (proposed identifier) | Purpose source | Separation requirement | Profile ID | Key owner | Rotation / verification |
|---|---|---|---|---|---|
| `CONTENT_RELEASE_SIGNING` | RUL §12; RUL-REQ-027 | C-07: "content release and package signing are distinct"; SEC-REQ-026 | *B1/B2* | *B4* | A6, A7 |
| `CONTENT_DIGEST` (rule AST, set, manifest, requirement content) | RUL §12 | Not a key; a digest | *B3* | n/a | A3 |
| `EVIDENCE_PACKAGE_SIGNING` | EVD §09 | C-07; EVD-REQ-012 | Defined by ZA-EVD-001 | Evidence Lead + Security | ZA-EVD-001 |
| `LEDGER_CHECKPOINT_SIGNING` | EVD §09; ARCH-ADR-007 | C-07 | Defined by ZA-EVD-001 | Evidence Lead + Security | ZA-EVD-001 |
| `LEDGER_EVENT_HASH` | EVD-ADR-02 (SHA-256 with domain separator; ACCEPTED\*) | n/a | ZA-EVD-001 | n/a | EVD-REQ-004 |
| `EVIDENCE_FINGERPRINT_HMAC` | EVD-REQ-010; EVD §09 | "Distinct from encryption/signing keys; HMAC-only purpose" | ZA-EVD-001 | Tenant hierarchy | ZA-EVD-001 |
| `WEBHOOK_SIGNING` | C-11 / API-ADR-06 (Ed25519 profile; ACCEPTED\*) | SEC §14: per installation/subscription | API-001 | API Lead | SEC §14 |
| `PRODUCTION_ARTIFACT_SIGNING` | SEC-REQ-026/027 | SEC-REQ-026 | SEC-ADR-04 | Release engineering | SEC-REQ-044 |

**A1.4 PROPOSED:** For evidence, ledger and webhook uses, ZA-KEY-001 records only the declaration and defers the
profile to ZA-EVD-001 or ZA-API-001. Parts A3–A9 and Part B govern `CONTENT_RELEASE_SIGNING` and `CONTENT_DIGEST`.

### A2. Canonical payload encoding

**A2.1 MANDATED:**
- C-02: "Canonical JSON v1 with published conformance vectors is the Phase-1 authority for signed/hashes rule, ledger
  and snapshot artifacts. CBOR is not an authoritative v1 format unless separately approved."
- RUL §12: "UTF-8; deterministic object-key ordering; normalized numbers/dates; no insignificant fields in signed
  executable payload."
- RUL-ADR-01: "do not depend on ordinary serializer output."
- "Parse(canonicalize(rule)) must preserve exact semantics and canonical hash."

**A2.2 PROPOSED:** A signed or hashed payload is the canonical-JSON byte sequence of a JSON value that declares its
canonicalization profile ID. The verifier recomputes the bytes from the stored structured value under that profile. It
never trusts stored bytes, or a stored digest, alone.

**A2.3 PROPOSED:** The canonicalization profile publishes conformance vectors before it is used. At minimum the vectors
cover:
- key ordering with non-ASCII keys;
- string escaping and Unicode;
- integers at the boundaries;
- the rejection or normalisation of non-integer numbers;
- dates;
- null handling;
- duplicate-key rejection.

Every implementation (Go evaluator per C-01, Python worker, standalone verifier) MUST pass them.

**A2.4 SELECTION REQUIRED (B5):** the definition of Canonical JSON v1. See Part B.

### A3. Hashing and algorithm identification

**A3.1 MANDATED:**
- RUL §12: "algorithm identifier stored with digest".
- DAT §03: "Hashes `bytea` plus algorithm code".

**A3.2 PROPOSED:** Every stored or signed digest is the pair `(algorithm_id, value)`.
- A digest whose algorithm is implied only by a field name (for example `ast_sha256`) is non-conformant for new
  artifacts.
- Existing artifacts keep their field names, because immutable history is never rewritten (A9).

**A3.3 PROPOSED:** Every content digest is domain-separated by a profile string that names the artifact type and
version, for example `za-requirement-content-v1`. The value hashed is `profile ‖ separator ‖ canonical bytes`. The
precedent is EVD-ADR-02's "explicit domain separator/version" for ledger hashing.

**A3.4 SELECTION REQUIRED (B3):** the approved hash algorithm(s).

### A4. Signature envelope

**A4.1 MANDATED** (RUL §12): "ContentRelease signs manifest containing every RuleVersion hash and referenced
SetVersion hash."

**A4.2 PROPOSED:** A content-release signature record carries:
- `signature_profile_id`;
- `algorithm_id`;
- `key_id` and `key_version` (A5);
- the manifest digest as `(algorithm_id, value)`;
- `canonicalization_profile_id`;
- the signature value.

The manifest itself carries `manifest_schema_version`, so the B1-c, R1 and K2 fields form one versioned manifest
format.

**A4.3 PROPOSED (algorithm-substitution defence):**
- The verifier determines the algorithm from the **trusted key record** (A6), never from the envelope alone.
- If the envelope algorithm differs from the key record's algorithm, the outcome is INVALID.

### A5. Key identity and versioning

**A5.1 MANDATED:**
- SEC §18 telemetry: "Key ID/purpose, caller workload, operation class, result; never key bytes."
- EVD-REQ-012: "Encryption, fingerprint and signing keys MUST be purpose-separated and versioned."

**A5.2 PROPOSED:** A key record has:
- a stable `key_id` (the key lineage);
- a monotonically increasing `key_version`;
- `purpose` (one A1.3 use);
- `algorithm_id`;
- the public key (SubjectPublicKeyInfo or equivalent);
- a full-length public-key fingerprint `(algorithm_id, value)`;
- a signing-validity interval as an OPERATIONAL_INSTANT_RANGE (ZA-TIM-001 §02);
- a status (A7.2).

Trust lookup matches the full fingerprint. A truncated identifier is a display label only.

**A5.3 PROPOSED:** One key version has one purpose. Reusing a key version across purposes is prohibited (C-07;
SEC §14 key-use separation).

### A6. Public-key trust and distribution

**A6.1 MANDATED:**
- DAT §05: "Cells verify before activation".
- DAT-ADR-07 (ACCEPTED\*): "Staging + full verification + atomic activation".
- THR-012 controls include "cell verification".
- SEC-REQ-048: verification failure "MUST fail closed for the affected binding/release operation and raise a security
  signal."
- APP-REQ-006: a binding RuleVersion "MUST be PUBLISHED, signature-valid".

**A6.2 PROPOSED:**
- Each cell holds a **content-release trust store**: the set of key records (A5.2) it accepts for
  `CONTENT_RELEASE_SIGNING`.
- A change to the trust store is itself authenticated (B6) and recorded append-only. The prior trust-store version
  stays available for historical verification.

**A6.3 PROPOSED outcome rule.** Verification yields:
- VALID only when every integrity check passes and the signing key version is in the trust store;
- INVALID when any check fails, or the key is REVOKED for that signature (A8);
- UNVERIFIABLE otherwise.

This is the existing A9b verifier contract (`outcome_of`). The three outcome codes are not in ZA-GLS-001. **Their
registration is a GLS change** for the Security/identity terms owner (GLS §12 ownership: Security Architect).

### A7. Private-key protection, rotation and status

**A7.1 MANDATED:**
- RUL-ADR-06 (ACCEPTED\*): "Managed signing key under regional security architecture; release service only, no
  analyst-held private keys."
- SEC §14: "Dedicated signing purpose, separated from encryption keys; tightly limited release service; alert on use."
  Also: "Application services do not receive root/master key material; they request cryptographic operations".
- THR-012: "Dedicated signing service/key; separation; HSM/KMS; multi-party release approval; audit/alerting; rapid
  revoke/rollback".
- SEC §02: "KMS signing/root keys" are A0 root security authority.
- SEC §22: "KMS/key policy change: Two-person/restricted approval"; "Content-signing/release change: A0 control
  review, signing verification, release/rollback test."
- SEC-REQ-044: rotation and revocation "operationally testable and generate evidence of completion."
- SEC §14: "Key rotation preserves ability to verify historical signatures/hashes where required".

**A7.2 PROPOSED status vocabulary** (subject to GLS registration by the Security Architect):

| Status | Meaning |
|---|---|
| `ACTIVE` | May sign new releases; verifies |
| `RETIRED_VERIFY_ONLY` | Does not sign; verifies signatures made within its signing-validity interval |
| `REVOKED` | Does not sign; verification behaviour per A8 |

**A7.3 PROPOSED:**
- At most one `ACTIVE` version per purpose per signing authority.
- A rotation activates the new version and retires the old one in one recorded trust-store change.
- A retired version stays in the trust store for as long as any release it signed remains supported (ZA-DOC-001
  §08: "Prior release manifests continue to reference v1.0 … No historical manifest is rewritten").

### A8. Revocation and compromise

**A8.1 MANDATED** (THR-012): "rapid revoke/rollback"; "A0 key compromise has rehearsed containment and rollback path."

**A8.2 PROPOSED:** A revocation records the key version, the reason, the actor and authority (two-person per SEC §22),
and a `compromised_from` instant if one is known.

**A8.3 SELECTION REQUIRED (B8):** treatment of releases signed by a revoked key. See Part B.

### A9. Historical verification of existing releases

**A9.1 MANDATED:**
- DAT-REQ-012 (PUBLISHED catalogue immutable).
- RUL-REQ-026 (prior RuleVersion and record-time history preserved).
- ZA-DOC-001 §08 ("No historical manifest is rewritten").

**A9.2 PROPOSED:**
- Releases signed before ZA-KEY-001 takes effect are verified under a frozen, verify-only **legacy profile**
  describing exactly what they used (see the *engineering note* below).
- Under the legacy profile the verifier MUST NOT report VALID unless the K5 disposition approves that. It reports
  UNVERIFIABLE with reason `LEGACY_PROFILE`; tampering still yields INVALID.
- Legacy rows are never re-signed in place.

*Engineering note: current implementation, describing the legacy profile.*
- **Algorithm:** Ed25519 (pure), via the `cryptography` library.
- **Key:** one cell key used for content releases, evidence packages and checkpoints (mixed purpose, audit I-05).
  Stored as an unencrypted PKCS#8 PEM file at `backend/var/keys/cell-signing-ed25519.pem`.
- **Key ID:** `ed25519:` + the first 16 hex characters of SHA-256 over the raw public key. That is a 64-bit label,
  used only for matching.
- **Signed bytes:** `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8; the
  signature is the hex of the Ed25519 signature over those bytes.
- **`manifest_hash`:** SHA-256 hex of the same bytes, with no stored algorithm identifier.
- **Requirement content digests** (`content_sha256`, profile `za-requirement-content-v1`): SHA-256 over the same
  serializer.
- **Non-conformance.** This is Python's ordinary serializer output, which RUL-ADR-01 says not to depend on. Key order is
  Python string ordering, and floats are not normalised. It is also not a published Canonical JSON v1 profile and has
  no conformance vectors.

### A10. Logging, alerting and evidence

**A10.1 MANDATED:**
- SEC §18 ("never key bytes").
- SEC §14 ("alert on use").
- SEC-REQ-048 (security signal on verification failure).

**A10.2 PROPOSED:**
- Every signing operation and every trust-store change emits a security event with the key ID, version, purpose,
  caller workload, operation and result.
- A rotation or revocation drill produces retained evidence (SEC-REQ-044).

---

## Part B: Selections requiring human approval

Each item is blank. The options are those with spec precedent or common standard use. Listing an option is not a
recommendation unless the item says so.

| # | Selection | Options | Spec precedent / considerations | Approvers | Decision |
|---|---|---|---|---|---|
| B1 | Content-release **signature algorithm and parameters** | (a) Ed25519, RFC 8032, pure. (b) ECDSA P-256 with SHA-256, FIPS 186-5, deterministic or randomized nonce stated. (c) RSA-PSS ≥3072 with SHA-256. (d) A post-quantum or hybrid scheme (e.g. ML-DSA, FIPS 204) for long-lived signatures | (a) is the current implementation and the C-11/API-ADR-06 webhook profile (ACCEPTED\*). Support in the chosen KMS/HSM (B4) must be confirmed for any option. Long historical-verification horizons (A7.3) bear on (d) | CTO; Global Head of Cybersecurity; Head of Assurance; GC | ☐ ___ |
| B2 | **Signature profile ID** format and the profile's exact contents | e.g. `za-content-release-sig-v1` = {algorithm, parameters, canonicalization profile, digest algorithm} | Follows from B1, B3, B5 | Security Architect | ☐ ___ |
| B3 | **Digest algorithm(s)** for `CONTENT_DIGEST` | (a) SHA-256. (b) SHA-384 / SHA-512. (c) SHA3-256 | (a) matches EVD-ADR-02/03 (ledger, Merkle; ACCEPTED\*) and every current digest. Algorithm agility is required either way (A3) | Security Architect; Evidence Lead | ☐ ___ |
| B4 | **KMS/HSM provider and custody** | Cloud KMS, dedicated HSM service, regional variants; certification level (e.g. FIPS 140-3 level) | OPEN-01 (cloud provider and first cell region; trigger "before provisioning the first production cell"); G-06 (internal); RUL-ADR-06 "regional security architecture". Not decidable before OPEN-01 | CTO; Security; Privacy/GC (OPEN-01 owners) | ☐ ___ |
| B5 | **Canonical JSON v1 definition** | (a) RFC 8785 JSON Canonicalization Scheme. (b) A Zoiko-defined profile, e.g. integers only (no floats), NFC-normalised strings, ISO-8601 dates as strings, duplicate keys rejected, key order by Unicode code point | C-02 and RUL-ADR-01 require a versioned profile with published vectors. (a) is an external standard. (b) avoids JCS's ECMAScript number formatting, but is a custom spec that needs its own vectors. **This also governs ledger and snapshot hashing (C-02; EVD-ADR-01; TIM-ADR-04), so the Evidence Lead and Data Architect must agree** | Security Architect; Evidence Lead; Data Architect; CTO | ☐ ___ |
| B6 | **Trust-root mechanism** for the cell trust store (K1) | (a) Trust-store document signed by a dedicated offline trust-root key pinned in the cell's deployment configuration, itself admitted through production artifact verification (SEC-REQ-027). (b) X.509 chain with a content-release CA. (c) A KMS key reference with provider-attested public key | (b) parallels EVD §17's "public verification certificate/chain … stable trust endpoint" for packages | CTO; Head of Assurance; GC (RUL-ADR-06); Security Architect | ☐ ___ |
| B7 | **Rotation period** and overlap | A fixed interval (e.g. annual) and/or event-driven rotation only | No spec states a period. SEC-REQ-044 requires testability, not a period | Security Architect | ☐ ___ |
| B8 | **Revoked-key treatment** (A8.3) | (a) Everything the key signed becomes INVALID. (b) Signatures whose verified cell activation record precedes `compromised_from` stay UNVERIFIABLE (`REVOKED_KEY_PRIOR_SIGNATURE`) pending re-release; later ones are INVALID. (c) (b) plus mandatory correction re-release (K5 path) | A compromised key can back-date anything it signs, so the signature cannot prove its own signing time. Only cell-side activation records (DAT-ADR-07), or a TSA token if adopted, can. RUL-REQ-026 governs the correction re-release | Head of Assurance; CTO; GC | ☐ ___ |
| B9 | **Legacy-profile disposition** (A9; K5) | (a) Stay UNVERIFIABLE permanently. (b) Correction re-release under the new key. (c) A signed legacy-acceptance attestation per release | Risk acceptance; the decision register lists this as K5 | Head of Assurance; CTO | ☐ ___ |
| B10 | **Document placement** | (a) ZA-KEY-001 as a new controlled document. (b) Fold into ZA-SEC-001 v1.1 as a "security baseline" annex | RUL §12 allows either ("ZA-KEY-001 / security baseline") | ZA-DOC-001 owner; CTO | ☐ ___ |

---

## Part C: Impact

| Area | Impact once approved (nothing is implemented by this draft) |
|---|---|
| Signed manifest (B1-c, R1, K2) | New `manifest_schema_version`. Digests become `(algorithm_id, value)`, the envelope fields follow A4.2, and `content_sha256` becomes a profile-identified content digest (R1). **One manifest version for all four decisions** (decision register §3) |
| Database | `catalog_content_release` is immutable (0004 guards). New releases need new columns for signature profile, key version and digest algorithm, or a separate append-only signature-record table. Plus a trust-store table, or signed configuration, with append-only history. A migration is required; **none is created here**. Legacy rows are untouched |
| A9b verifier | `SIGNER_TRUST` moves from always-UNVERIFIABLE to trust-store lookup. The algorithm comes from the key record (A4.3). The legacy profile is reported as `LEGACY_PROFILE`. Revocation is handled per B8 |
| Release publication | Signing moves behind a release-signing service interface (KMS or HSM operation, no private key in the API process). The development file backend stays for local and test use only |
| Evidence packages and checkpoints | Must stop sharing the cell key (C-07). Governed by ZA-EVD-001 and the roadmap's Stage 7, not by this document |
| Canonical JSON (B5) | Changing the serializer changes bytes for **new** artifacts only. Old artifacts verify under the legacy profile. Rule AST hashes (`ast_sha256`) in existing rule versions stay as recorded |
| Tests | Conformance vectors (A2.3); rotation and revocation drills (SEC-REQ-044); algorithm-substitution negative tests (A4.3) |

## Review and approval record

The record follows ZA-DOC-001 §09. All fields are blank. No human has reviewed this draft.

| Role | Name | Decision (Approve / Reject / Request change) | Part B items decided | Artifact hash | Date (authenticated) | Conditions / reasons |
|---|---|---|---|---|---|---|
| CTO (accountable) | | | | | | |
| Global Head of Cybersecurity | | | | | | |
| Security Architect (proposed owner) | | | | | | |
| Head of Assurance | | | | | | |
| General Counsel | | | | | | |
| Evidence Lead | | | | | | |
| Data Architect | | | | | | |
| ZA-DOC-001 owner (registration) | | | | | | |
