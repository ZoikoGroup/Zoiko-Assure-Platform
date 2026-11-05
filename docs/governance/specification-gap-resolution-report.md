# Specification gap resolution: report

Date: 2026-11-04. Prepared by engineering with AI assistance for the ZA specification owners, architects and
engineering leads. Primary reference: [regulatory-catalogue-governance-decision-register.md](../regulatory-catalogue-governance-decision-register.md).

**Nothing in this report or the drafts is approved.**
- ADR-REQ-006: AI systems must not accept, reject, supersede or approve ADRs.
- ADR-REQ-005: T0 decisions are not accepted solely by their author.
- DOC-REQ-003: document existence is not approval.

## Verdict: PARTIALLY READY

| Gap | Draft | Ready for human review? | Can be approved as drafted? |
|---|---|---|---|
| G1: valid-time baseline (B1-b) | [DOM-1.1-valid-time-amendment-draft.md](DOM-1.1-valid-time-amendment-draft.md) | **Yes**, with a choice between Alternative A and Alternative B | **The valid-time text, yes.** **Issuing DOM 1.1, no:** DOM 1.1 must also close DOC-CON-001/002 (not drafted), and ARCH §5.2 has the same defect but is unregistered |
| G2: ZA-KEY-001 (K3) | [ZA-KEY-001-draft.md](ZA-KEY-001-draft.md) | **Yes** | **Part A requirements, yes. Part B, only by decision:** 10 selections (algorithm, digest, canonical JSON, trust root, KMS/HSM and others), and the KMS/HSM selection waits for OPEN-01 |
| G3: schedule vocabulary (B4) | [GLS-001-schedule-vocabulary-amendment-draft.md](GLS-001-schedule-vocabulary-amendment-draft.md) | **Yes** | **3 of 5 codes, yes** (`CONTINUOUS`, `RECURRING`, `EVENT_TRIGGERED`). **`ONE_TIME` and `WINDOWED`, no:** the controlled specs give one word for each, and owner questions Q1–Q4 are open |

Not READY FOR HUMAN APPROVAL: two codes have no supporting semantics, and DOM 1.1 needs text this pack does not
contain. Not BLOCKED BY MISSING EVIDENCE: every draft can go to its reviewers now, and most of the content is anchored
in quoted controlled text.

## 1. Status by artefact

| Artefact | Drafted | Reviewed | Approved | Still blocked |
|---|---|---|---|---|
| DOM 1.1 valid-time amendment | ✅ 2026-11-04 | ❌ No human review. Engineering checked every citation against the extracted spec text; that is not a ZA-DOC-001 §09 review | ❌ | Alternative A/B choice (TIM owner); DOC-CON-001/002 text for DOM 1.1; ConflictRecord for ARCH §5.2 (F1); DAT profile assignment (M2); ZA-ADR-001 approval for TIM-ADR-03 |
| ZA-KEY-001 | ✅ 2026-11-04 | ❌ | ❌ | Registration by the ZA-DOC-001 owner (B10); Part B selections B1–B9; B4 needs OPEN-01 (cloud provider and first cell); GLS registration of verification-outcome and key-status codes |
| GLS-001 schedule vocabulary | ✅ 2026-11-04 | ❌ | ❌ | Q1–Q4 for `ONE_TIME`/`WINDOWED`; S1 cadence representation; S2 trigger vocabulary; S3 calendar service (WRK-ADR-06, not built); DOM E1/E2 |
| Decision register (existing) | ✅ corrected on 2026-11-04 | ❌ | ❌ | Two citation corrections (F9), otherwise unchanged |
| ZA-ADR-001 | n/a | n/a | ❌ **"Proposed for architecture-governance lock"; no approval evidence found** in the spec volumes or the repository | Every ACCEPTED\* entry cited here (C-02, C-07, C-11, RUL-ADR-01/06, TIM-ADR-03/04, DAT-ADR-06/07, EVD-ADR-02/03, WRK-ADR-06) is intent, not authority in force |

## 2. Findings from the evidence review

None of these is resolved by the drafts. Each is routed to its owner.

| # | Finding | Evidence | Routed to |
|---|---|---|---|
| F1 | **ZA-ARCH-001 v3.0 §5.2 has the DOC-CON-003 defect but is not in the conflict register.** It reads "half-open ranges [start, end) and UTC" and uses `valid_period TSTZRANGE` for "business/legal truth" | ARCH §5.2; DOC §07 lists only DOM | ZA-DOC-001 owner: new ConflictRecord or a widened DOC-CON-003 (DOC-REQ-008) |
| F2 | **ObligationDefinition is "Versioned" (DOM §6.1) but not in DOM §9.1's mandatory-bitemporal list, and has no temporal profile in DAT.** This is the missing baseline text behind B1-b | DOM §9.1; DAT §05; TIM §25 | DOM owner (DOM 1.1 §2.3); Data Architect |
| F3 | **DOM 1.1 is also required for DOC-CON-001 and DOC-CON-002.** DOC §08's example names the DOM 1.1 reason as "DOC-CON-001 + DOC-CON-003 normalization" | DOC §07, §08, §28 | DOM owner |
| F4 | **The current canonical JSON is Python's ordinary serializer** (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`). RUL-ADR-01 says "do not depend on ordinary serializer output"; C-02 requires published conformance vectors, and none exist | RUL-ADR-01; C-02; `backend/app/engines/rules.py` `canonical_bytes` | Security Architect / Evidence Lead (ZA-KEY-001 B5) |
| F5 | **No stored digest carries an algorithm identifier.** Algorithms are implied by field names (`ast_sha256`, `manifest_hash`, `content_sha256`) | RUL §12; DAT §03 "Hashes `bytea` plus algorithm code" | ZA-KEY-001 A3 (new artefacts only) |
| F6 | **The A9b verification outcomes (VALID/INVALID/UNVERIFIABLE) and the proposed key statuses are not GLS codes** | GLS-REQ-001 | Security Architect (GLS §12 Security/identity terms) |
| F7 | **DOM is internally inconsistent about continuous duties**, and silent on occurrence cardinality for one-time and windowed duties | DOM §6.2 versus §13 | DOM owner |
| F8 | **`recurrence_days` cannot express calendar "annual" or "quarterly" cadence, and occurrences store date-only legal deadlines as instants** | DOM §6.2; TIM-REQ-003; `assurance_obligation_occurrence` | Regulatory Content owner (GLS S1); later schema work |
| F9 | **Two citations in the decision register were wrong and have been corrected.** K6 cited SEC-ADR-03, which is the secrets broker, not signing-key custody. K2 cited "SEC §19" for key logging; the text is in SEC §18 | SEC §18, SEC-ADR-03 | Done, as an editorial correction to the register |

## 3. Impact analysis

### B1-b and the definition revision model

- The DOM 1.1 draft gives two exact texts: A (legal valid time plus record time) and B (record time only).
- **Alternative A** adds a proposed invariant, DOM-INV-16: a RuleVersion's valid period lies within the pinned
  revision's valid period. That needs a check at rule publication.
- **Alternative B** needs nothing extra, but requires separate RuleVersions for transition regimes.
- Either way, the revision model in [obligation-definition-versioning-architecture.md](../obligation-definition-versioning-architecture.md)
  stays valid. Only the valid-time columns of the proposed revision table depend on the choice.
- **Still blocked:** the TIM owner's choice, and DOM 1.1 approval.

### B1-c and signed manifest references

- ZA-KEY-001 A4.2 introduces `manifest_schema_version`.
- The definition-revision pin (B1-c), the digest pairs (A3) and the key reference (A5) belong in that **one** manifest
  version.
- This confirms the register's "manifest group": B1-c, R1, K2 and K3 must be approved together. Otherwise immutable
  release rows end up with two incompatible manifest formats.

### K2 and K3

- **K3** closes when ZA-KEY-001 is registered (or the "security baseline" alternative is chosen, B10) and Part B items
  B1, B3 and B5 are decided.
- **B4 (KMS/HSM)** cannot be decided before OPEN-01. In practice the algorithm (B1) must be one that provider supports,
  so K3 and K6 are coupled through OPEN-01.
- **K2** is defined by A5/A7:
  - versioned key records;
  - full-fingerprint trust matching;
  - `RETIRED_VERIFY_ONLY` keys that remain verifiable;
  - a trust store with append-only history.
- K2 still needs selections B6 (trust root) and B7 (rotation period).

### R1 and `content_sha256`

- Under A3, the field becomes `(algorithm_id, value)` with domain separation by the existing profile string
  `za-requirement-content-v1`.
- Existing releases keep their `content_sha256` field unchanged (A9), because immutable rows are never rewritten.
- If B5 changes the canonicalization, the content digest profile needs a new version (`…-v2`) for new releases.
- R1 can be approved as is (option 1 in the register) or with the A3 encoding (option 2).

### B4 and schedule validation

- The three existing codes can be ratified now.
- API validation would then follow the §3.1 field matrix.
- `ONE_TIME` and `WINDOWED` stay unaccepted until Q1–Q4 are answered.
- Validating business-day deadlines needs the WRK calendar service (S3), which does not exist.

### Migration 0014 and the definition-revision migration

- **0014 scope (C1: catalogue lifecycle events plus database guards) is not changed** by any of the three amendments.
  It still depends on C2 (event-table convention) and C3.
- **The definition-revision migration ("0015 or later") absorbs:**
  - the revision valid-time columns (if Alternative A);
  - the structured schedule fields (GLS S1/S2; B5 semantic field);
  - the revision pin on rule versions (B1-c).
- **The release-signature fields and the trust store** (ZA-KEY-001 Part C) are a further schema change. Whether they
  join the definition-revision migration or get their own depends on the manifest group being approved together.
- **No migration is proposed, created or run here.**

### A9b verification and historical releases

- Existing releases map to the frozen **legacy profile** (ZA-KEY-001 A9: Ed25519 cell key, ordinary-serializer bytes,
  unlabelled SHA-256).
- They keep reporting UNVERIFIABLE, now with reason `LEGACY_PROFILE`, and INVALID if altered. That is today's
  behaviour.
- VALID becomes possible only for releases signed by a trust-store key under an approved profile.
- How revoked keys and legacy releases are treated depends on selections B8 and B9 (K5).

## 4. Recommended order for human review

1. **ZA-DOC-001 owner:**
   - register ZA-KEY-001 or choose the "security baseline" alternative (B10);
   - record the F1 ConflictRecord;
   - confirm that the GLS amendment takes the state-code (ADR) route.
2. **TIM owner and DOM owner:**
   - choose Alternative A or B;
   - commission the DOC-CON-001/002 text so DOM 1.1 can be issued in one revision.
3. **Regulatory Content owner and GC:** answer Q1–Q4 and select S1/S2.
4. **CTO, Security, Head of Assurance and GC:**
   - ZA-KEY-001 B1, B3, B5, B6 and B7 together with the manifest group (B1-c, R1, K2);
   - B4 at the OPEN-01 trigger;
   - B8 and B9 (K5).

## 5. Restrictions observed

- No backend code changed.
- No migration created or executed; 0001–0013 untouched.
- The canonical `assure` database was not accessed.
- Existing workspace changes preserved.
- No git commands run.
- No draft marked approved.
- The only edits outside `docs/governance/` are the two citation corrections in the decision register (F9).
