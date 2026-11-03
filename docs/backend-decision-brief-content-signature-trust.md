# Decision brief: content-release signature trust (APP-REQ-006)

> **Purpose:** prepare a governed decision. This brief is **not an ADR** and approves nothing.
> - ADR-REQ-006: AI systems must not accept or approve ADRs.
> - ADR-REQ-005: a T0 decision cannot be accepted by its author alone.
>
> **Status:** no approved decision exists. Signature verification stays unimplemented until one does. A9a (section 4)
> was implemented on 29 October 2026 (Task 3; not committed).
> **Date:** 28 October 2026; A9a status added 29 October 2026.
> **Context:** roadmap §6 rows A9a and A9b, and audit finding I-05 ([backend-architecture-audit.md](backend-architecture-audit.md)).

## 1. What the controlled sources require

All the sources below are v1.0 and marked "Proposed for … lock". ZA-ADR-001 is unapproved, so none of its ACCEPTED* baseline entries is in force (G-03).

| Source | Text |
|---|---|
| APP-REQ-006 | "A binding RuleVersion MUST be PUBLISHED, signature-valid, effective for the evaluation time and based on ENCODED coverage." |
| ZA-APP-001 §13 | Rule tampering: "Only signed PUBLISHED RuleVersions accepted for binding evaluation; signature/release ID checked before input freeze." |
| ZA-DAT-001 §5 | Content releases: "Cells verify before activation." Catalogue activation: "validate signatures/hashes/FKs/compatibility, then atomically activate". |
| RUL-REQ-027 | Release manifests "MUST bind exact RuleVersion and SetVersion hashes and be signed by authorized release infrastructure." |
| ZA-RUL-001 §12 | Hash algorithm: "Platform cryptographic profile defined by ZA-KEY-001 / security baseline". **No ZA-KEY-001 exists** in the Mandatory Engineering Documents Register or the Master Engineering Document Evidence Register. |
| ZA-RUL-001 §18 | Error code `RUL_SIGNATURE_INVALID`: "Released artifact/manifest signature or hash invalid." |
| SEC-REQ-026 | Content-release signing keys "MUST be purpose-separated and accessible only to authorized release/signing services." |
| SEC-REQ-048 | A content-signature verification failure "MUST fail closed for the affected binding/release operation and raise a security signal." |
| SEC-REQ-049 | Every cryptographic use "MUST declare purpose, algorithm/profile, key owner, rotation/verification requirements". |
| ZA-SEC-001 §14 | "Key rotation preserves ability to verify historical signatures/hashes where required." |
| ZA-ADR-001 C-07 | "Purpose-separated keys: evidence ledger/checkpoint, content release and package signing are distinct." |
| RUL-ADR-06 (T0) | Content-signing key profile: "Managed signing key under regional security architecture; release service only, no analyst-held private keys." Owners: CTO + Head of Assurance + GC. |

## 2. What the code does

- **One key for everything.** `engines/keys.py` generates a single Ed25519 key on first use (`backend/var/keys/cell-signing-ed25519.pem`). It signs checkpoints, evidence packages and content releases (I-05). Its key ID is derived from the public key, and it has no version.
- **The signer is the evaluator.** Content releases are signed by the same cell process that later evaluates against them (`routers/rules.py`, `seed.py`).
- **No check at evaluation.** `engines/applicability.resolve_rule` checks status, record cutoff, valid range and coverage mode only. It checks neither the release signature nor the AST hash.
- **Partial protection since `0004`.** Published rule versions, releases and set versions can no longer be changed through ordinary DML. The table owner can still disable the trigger (A10), and nothing detects a restored or tampered copy.

## 3. Why signature verification needs a decision first

Verifying a release against the key the verifying process itself holds proves only that this process signed it. APP-REQ-006, RUL-REQ-027 and C-07 require provenance from a **separate content-release signing authority** whose public key the cell trusts. Implementing verification now means inventing that trust anchor: which key, where its public part comes from, and how it rotates. The user's instructions and SEC-REQ-049 rule that out.

## 4. What can proceed without the decision (A9a)

These integrity checks need **no trust anchor** and are fully specified:
- recompute the AST hash from the stored canonical AST and pinned set hashes (ZA-RUL-001 §12 hash input; RUL-REQ-016)
- check each pinned SetVersion's hash against its members (RUL-REQ-014)
- check that the release manifest hash matches its canonical manifest
- check that the manifest binds this exact RuleVersion ID and AST hash (RUL-REQ-027)

A mismatch fails the binding evaluation as an integrity error (`RUL_SIGNATURE_INVALID`; APP-REQ-019: never `UNCERTAIN`). It also raises the security signal (SEC-REQ-048; event `security.content_signature_failed`, ZA-SEC-001 §23).

**How the signal persists (implemented; for confirmation by the EVT/SEC owners).** The failed operation's transaction is
rolled back, so nothing it wrote survives. The verification result is then committed in a new transaction on the same
session and runtime role: an audit record (`content.verify`) plus the `security.content_signature_failed` event in the
transactional outbox. This follows ZA-EVT-001's durability rule for `SECURITY_CRITICAL` events ("originate from the same
transactional outbox when tied to a committed domain/security state"), with the audit record as that state. It uses no
second connection, owner credential or new store. The outbox is not relayed yet (P-04) and there is no
`SECURITY_CRITICAL` route, so the event is durable but not yet delivered.

**What A9a cannot detect.** Every A9a check compares stored artefacts with each other. Someone with write access below
the application (the owner role with the trigger disabled, A10, or a tampered restore) who rewrites the rule row, its
release manifest and the manifest hash consistently passes all of them. Only verifying the release signature against a
trusted key detects that, which is A9b.

**Proof for A9a** (`backend/tests/test_rule_integrity.py`):
- tamper tests on a disposable database (the owner disables the trigger, alters the AST, and evaluation must refuse)
- mismatch tests
- every published rule version still verifies, on the test fixture and on the development data (read-only)

## 5. Decisions required (A9b)

| # | Question | Sources | Approvers |
|---|---|---|---|
| K1 | Which key a cell trusts for content releases, and how its public part is distributed and pinned (key, certificate chain, KMS key reference) | C-07, RUL-ADR-06, SEC-REQ-026, DAT §5 | RUL-ADR-06 (T0): CTO + Head of Assurance + GC. G-06: CTO + Security |
| K2 | Key identity, versioning and rotation, including verify-only old keys so earlier releases stay verifiable | ZA-SEC-001 §14, SEC-REQ-044/049 | G-06: CTO + Security |
| K3 | Algorithm and signature profile, given that ZA-KEY-001 does not exist | ZA-RUL-001 §12, SEC-REQ-049, C-02 | CTO + Security; document owner for ZA-KEY-001 or its replacement |
| K4 | Where verification runs: at catalogue activation (DAT-ADR-07 staging), at every binding evaluation (APP §5/§13), or both | DAT §5, DAT-ADR-07, APP-REQ-006 | Data Architect + CTO |
| K5 | What happens to releases already signed with the mixed-purpose cell key: re-release under the new key, or accept as legacy with a recorded disposition. Published rows are immutable (RUL-INV-01) | RUL-REQ-026, RUL-INV-01, I-05 | Head of Assurance + CTO |
| K6 | Provider and KMS/HSM for the signing key | OPEN-01, SEC-ADR-03, G-06 | CTO + Security + Privacy/GC |

None of these is decided by this brief.

## 6. Affected files

| Item | Code | Tests | Migration |
|---|---|---|---|
| A9a | `engines/applicability.py` (`resolve_rule` or a pre-freeze check), `engines/rules.py` (hash recomputation), security event emission | new tamper and mismatch tests (PostgreSQL), regression on stored evaluations | none |
| A9b | `engines/keys.py` (purpose-separated signing and verification keys), `routers/rules.py` and `seed.py` (sign with the content key), catalogue activation | signature-tamper, wrong-key and rotation tests | possibly a key-reference column on `catalog_content_release` (an immutable table: DAT-REQ-039 review) |
