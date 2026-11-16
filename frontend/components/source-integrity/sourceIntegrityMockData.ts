import type { Jurisdiction, RegulatorySource, VerificationCheck } from "./types";

/* DEMONSTRATION DATA ONLY. Replace this file's use in useSourceIntegrityData.ts when the backend serves real reads.

   Every record below is fictional. No regulatory source, publisher, version, check or person here exists, and nothing
   here was read from this deployment or verified by anyone:
   - Publishers use well-known fictitious company names (Contoso, Fabrikam, Northwind, Woodgrove, Tailspin, Litware,
     Proseware, Adventure Works), never a real authority's name.
   - Reference locations use the reserved .example top-level domain (RFC 2606), so none of them resolves.
   - Checksums are random hexadecimal strings. They are not digests of any document, and the screen never computes or
     compares a checksum or a signature: each "match", "mismatch" and signature outcome is written here by hand.
   - Identifiers carry DEMO so that a screenshot cannot be mistaken for a real record. */

const GB: Jurisdiction = { code: "GB", name: "United Kingdom" };
const EU: Jurisdiction = { code: "EU", name: "European Union" };
const US: Jurisdiction = { code: "US", name: "United States" };
const SG: Jurisdiction = { code: "SG", name: "Singapore" };
const AU: Jurisdiction = { code: "AU", name: "Australia" };
const INTL: Jurisdiction = { code: "INTL", name: "International" };
const INTERNAL: Jurisdiction = { code: "INTERNAL", name: "Internal (no jurisdiction)" };

export const DEMO_SOURCES: RegulatorySource[] = [
  {
    id: "SRC-DEMO-001", name: "Contoso Conduct of Business Rulebook", type: "REGULATORY_AUTHORITY", jurisdiction: GB,
    publisher: "Contoso Financial Conduct Board",
    description: "Conduct rules for retail and wholesale firms: client categorisation, communications and suitability. A fictional rulebook for this demonstration.",
    currentVersion: "2026.3", previousVersion: "2026.2", publisherLatestVersion: "2026.3", publishedAt: "2026-09-15",
    lastVerifiedAt: "2026-11-11T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [
      { label: "Publisher reference", value: "CFCB/COBS/2026.3" },
      { label: "Document identifier", value: "DEMO-DOC-0001" },
      { label: "Location", value: "https://contoso.example/rulebook/cobs" },
    ],
  },
  {
    id: "SRC-DEMO-002", name: "Fabrikam Anti-Money Laundering Guidance", type: "REGULATORY_AUTHORITY", jurisdiction: EU,
    publisher: "Fabrikam Supervisory Authority",
    description: "Guidance on customer due diligence, transaction monitoring and suspicious-activity reporting. A fictional guidance document for this demonstration.",
    currentVersion: "4.1", previousVersion: "4.0", publisherLatestVersion: "4.1", publishedAt: "2026-10-20",
    lastVerifiedAt: "2026-11-10T14:22:00Z", status: "VERIFICATION_FAILED",
    statusNote: "The content received for version 4.1 does not match the checksum the publisher lists for it, in two checks running.",
    references: [
      { label: "Publisher reference", value: "FSA-AML-GL-4.1" },
      { label: "Document identifier", value: "DEMO-DOC-0002" },
      { label: "Location", value: "https://fabrikam.example/guidance/aml" },
    ],
  },
  {
    id: "SRC-DEMO-003", name: "Northwind Data Protection Code", type: "GOVERNMENT_PUBLICATION", jurisdiction: GB,
    publisher: "Northwind Information Office",
    description: "A code of practice on lawful processing, retention and data-subject requests. A fictional publication for this demonstration.",
    currentVersion: "1.8", previousVersion: "1.7", publisherLatestVersion: "1.9", publishedAt: "2026-03-02",
    lastVerifiedAt: "2026-10-28T08:15:00Z", status: "REVIEW_REQUIRED",
    statusNote: "The publisher has issued version 1.9. The platform still holds version 1.8, which passed its last check; a person must review the change before 1.9 is adopted.",
    references: [
      { label: "Publisher reference", value: "NIO-DPC-1.8" },
      { label: "Document identifier", value: "DEMO-DOC-0003" },
      { label: "Location", value: "https://northwind.example/codes/data-protection" },
    ],
  },
  {
    id: "SRC-DEMO-004", name: "Woodgrove Operational Resilience Standard", type: "INDUSTRY_STANDARD", jurisdiction: INTL,
    publisher: "Woodgrove Standards Council",
    description: "An industry standard for mapping important business services and setting impact tolerances. A fictional standard for this demonstration.",
    currentVersion: "2025 edition", previousVersion: "2022 edition", publisherLatestVersion: "2025 edition", publishedAt: "2025-11-30",
    lastVerifiedAt: "2026-11-09T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [
      { label: "Standard number", value: "WSC 4410:2025" },
      { label: "Document identifier", value: "DEMO-DOC-0004" },
    ],
  },
  {
    id: "SRC-DEMO-005", name: "Tailspin Consumer Credit Sourcebook", type: "REGULATORY_AUTHORITY", jurisdiction: US,
    publisher: "Tailspin Consumer Finance Bureau",
    description: "Rules on affordability, disclosures and arrears handling for consumer lending. A fictional sourcebook for this demonstration.",
    currentVersion: "12.0", previousVersion: "11.4", publisherLatestVersion: "12.0", publishedAt: "2026-11-06",
    lastVerifiedAt: "2026-08-19T06:00:00Z", status: "PENDING_VERIFICATION",
    statusNote: "Version 12.0 was received on 7 Nov 2026 and is queued for its first check. The last completed check covered version 11.4.",
    references: [
      { label: "Publisher reference", value: "TCFB-CONC-12" },
      { label: "Document identifier", value: "DEMO-DOC-0005" },
      { label: "Location", value: "https://tailspin.example/sourcebooks/consumer-credit" },
    ],
  },
  {
    id: "SRC-DEMO-006", name: "Internal Control Taxonomy", type: "INTERNAL_REFERENCE", jurisdiction: INTERNAL,
    publisher: "Content Operations (internal, demo)",
    description: "The internal taxonomy that control mappings are classified against. A demonstration record, not the platform's actual taxonomy.",
    currentVersion: "3.2", previousVersion: "3.1", publisherLatestVersion: "3.2", publishedAt: "2026-07-01",
    lastVerifiedAt: "2026-11-05T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [{ label: "Document identifier", value: "DEMO-DOC-0006" }],
  },
  {
    id: "SRC-DEMO-007", name: "Litware Market Conduct Digest", type: "GOVERNMENT_PUBLICATION", jurisdiction: EU,
    publisher: "Litware Official Publications Office",
    description: "A consolidated digest of market-conduct provisions as published in an official journal. A fictional publication for this demonstration.",
    currentVersion: "2.4", previousVersion: "2.3", publisherLatestVersion: "2.4", publishedAt: "2026-05-18",
    lastVerifiedAt: "2026-11-03T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [
      { label: "Publisher reference", value: "LOPO/2026/0518" },
      { label: "Document identifier", value: "DEMO-DOC-0007" },
      { label: "Location", value: "https://litware.example/journal/market-conduct" },
    ],
  },
  {
    id: "SRC-DEMO-008", name: "Proseware Payment Services Guidance", type: "REGULATORY_AUTHORITY", jurisdiction: SG,
    publisher: "Proseware Monetary Authority",
    description: "Guidance on safeguarding, strong customer authentication and incident reporting for payment firms. A fictional guidance document for this demonstration.",
    currentVersion: "5.0", previousVersion: "4.2", publisherLatestVersion: "5.0", publishedAt: "2026-10-12",
    lastVerifiedAt: "2026-09-12T06:00:00Z", status: "REVIEW_REQUIRED",
    statusNote: "Version 5.0 is signed with a different publisher key from version 4.2. Its content matches the publisher's checksum, but a person must confirm the key change before the version is relied on.",
    references: [
      { label: "Publisher reference", value: "PMA-PSG-5.0" },
      { label: "Document identifier", value: "DEMO-DOC-0008" },
      { label: "Location", value: "https://proseware.example/guidance/payments" },
    ],
  },
  {
    id: "SRC-DEMO-009", name: "Adventure Works Outsourcing Guidelines", type: "REGULATORY_AUTHORITY", jurisdiction: AU,
    publisher: "Adventure Works Prudential Regulator",
    description: "Guidelines on material outsourcing arrangements and their notification. A fictional guideline for this demonstration.",
    currentVersion: "1.3", previousVersion: "1.2", publisherLatestVersion: "1.3", publishedAt: "2025-02-14",
    lastVerifiedAt: "2026-06-28T06:00:00Z", status: "INACTIVE",
    statusNote: "Withdrawn by the publisher on 30 Jun 2026. Kept for history; it is no longer re-checked.",
    references: [
      { label: "Publisher reference", value: "AWPR-OUT-1.3" },
      { label: "Document identifier", value: "DEMO-DOC-0009" },
    ],
  },
  {
    id: "SRC-DEMO-010", name: "Fabrikam ICT Risk Technical Standard", type: "INDUSTRY_STANDARD", jurisdiction: EU,
    publisher: "Fabrikam Standards Body",
    description: "A technical standard for ICT risk-management frameworks and incident classification. A fictional standard for this demonstration.",
    currentVersion: "1.0", previousVersion: null, publisherLatestVersion: "1.0", publishedAt: "2026-01-17",
    lastVerifiedAt: "2026-11-07T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [
      { label: "Standard number", value: "FSB-TS-ICT-01" },
      { label: "Document identifier", value: "DEMO-DOC-0010" },
    ],
  },
  {
    id: "SRC-DEMO-011", name: "Contoso Senior Managers Handbook", type: "REGULATORY_AUTHORITY", jurisdiction: GB,
    publisher: "Contoso Financial Conduct Board",
    description: "Rules on senior-manager responsibilities, statements of responsibility and certification. A fictional handbook for this demonstration.",
    currentVersion: "2026.1", previousVersion: "2025.4", publisherLatestVersion: "2026.1", publishedAt: "2026-11-10",
    lastVerifiedAt: "2026-09-30T06:00:00Z", status: "PENDING_VERIFICATION",
    statusNote: "Version 2026.1 was received on 11 Nov 2026 and is queued for its first check. The last completed check covered version 2025.4.",
    references: [
      { label: "Publisher reference", value: "CFCB/SMH/2026.1" },
      { label: "Document identifier", value: "DEMO-DOC-0011" },
      { label: "Location", value: "https://contoso.example/rulebook/smh" },
    ],
  },
  {
    id: "SRC-DEMO-012", name: "Northwind Climate Disclosure Framework", type: "INDUSTRY_STANDARD", jurisdiction: INTL,
    publisher: "Northwind Sustainability Standards Board",
    description: "A framework for climate-related governance, strategy and metrics disclosures. A fictional framework for this demonstration.",
    currentVersion: "2.0", previousVersion: "1.1", publisherLatestVersion: "2.0", publishedAt: "2026-09-01",
    lastVerifiedAt: "2026-11-06T13:05:00Z", status: "VERIFICATION_FAILED",
    statusNote: "The publisher's signature on version 2.0 did not verify in the latest check, although the content matches the publisher's checksum.",
    references: [
      { label: "Standard number", value: "NSSB-CDF-2" },
      { label: "Document identifier", value: "DEMO-DOC-0012" },
      { label: "Location", value: "https://northwind.example/standards/climate" },
    ],
  },
  {
    id: "SRC-DEMO-013", name: "Tailspin Privacy Rule Compilation", type: "GOVERNMENT_PUBLICATION", jurisdiction: US,
    publisher: "Tailspin Federal Register Office",
    description: "A compilation of privacy and safeguards rules for financial institutions. A fictional publication for this demonstration.",
    currentVersion: "7.2", previousVersion: "7.1", publisherLatestVersion: "7.3", publishedAt: "2026-04-09",
    lastVerifiedAt: "2026-11-02T06:00:00Z", status: "VERIFIED",
    statusNote: "Version 7.2 passed its latest check. The publisher has since issued version 7.3, which the platform has not received.",
    references: [
      { label: "Publisher reference", value: "TFRO-PRIV-7.2" },
      { label: "Document identifier", value: "DEMO-DOC-0013" },
    ],
  },
  {
    // Deliberately sparse: shows how the screen presents a record with no description, publication date, previous
    // version or references.
    id: "SRC-DEMO-014", name: "Internal Records Retention Schedule", type: "INTERNAL_REFERENCE", jurisdiction: INTERNAL,
    publisher: "Records Management (internal, demo)", description: null,
    currentVersion: "1.0", previousVersion: null, publisherLatestVersion: "1.0", publishedAt: null,
    lastVerifiedAt: "2026-10-15T06:00:00Z", status: "VERIFIED", statusNote: null, references: [],
  },
  {
    // Never checked: shows the empty verification history.
    id: "SRC-DEMO-015", name: "Woodgrove Third-Party Risk Questionnaire", type: "INDUSTRY_STANDARD", jurisdiction: INTL,
    publisher: "Woodgrove Standards Council",
    description: "A standard due-diligence questionnaire for assessing third-party service providers. A fictional standard for this demonstration.",
    currentVersion: "3.0", previousVersion: null, publisherLatestVersion: "3.0", publishedAt: "2026-10-31",
    lastVerifiedAt: null, status: "PENDING_VERIFICATION",
    statusNote: "Received on 3 Nov 2026 and not yet checked. No check has been recorded for this source.",
    references: [
      { label: "Standard number", value: "WSC 5120:2026" },
      { label: "Document identifier", value: "DEMO-DOC-0015" },
    ],
  },
  {
    id: "SRC-DEMO-016", name: "Proseware Technology Risk Notice", type: "REGULATORY_AUTHORITY", jurisdiction: SG,
    publisher: "Proseware Monetary Authority",
    description: "A notice on technology-risk governance, system availability and cyber hygiene. A fictional notice for this demonstration.",
    currentVersion: "2.1", previousVersion: "2.0", publisherLatestVersion: "2.1", publishedAt: "2026-06-05",
    lastVerifiedAt: "2026-11-08T06:00:00Z", status: "VERIFIED", statusNote: null,
    references: [
      { label: "Publisher reference", value: "PMA-TRN-2.1" },
      { label: "Document identifier", value: "DEMO-DOC-0016" },
      { label: "Location", value: "https://proseware.example/notices/technology-risk" },
    ],
  },
];

/* Random demonstration strings, one per source version (see the note at the top). */
const SUM = {
  contoso_2026_2: "4d743d0c7382402b1455d41081f54b41510518eb35316ac09c0eb7e3403056d1",
  contoso_2026_3: "7cc3e70c36c7425ca51ba0e9df98184b8b745d6764dea72f55ffcb050fc927ec",
  fabrikamAml_4_0: "75db7b33d8bc3fc8ef500856cb7b1d465728814b11fc522b1fb5019841d38df8",
  fabrikamAml_4_1: "df073c26166fe34121392f9c8ecfe7bfe1fb50bb625c7a8de98eb2fd7cf3cc08",
  fabrikamAml_4_1_received: "a791fee5622ecd517f76b4a129404a39417b7c6c75227d0cf1c46c9f93933e28",
  northwindDp_1_8: "5665fb933e0ebfbce60ac1d69c90a1ee1e78064d12f758521e3e3b70eb30d62a",
  woodgrove_2025: "a24214cd25eb50bbf131b2565dfe5bcd4112fac0e7bc0821417e2b879a44cbae",
  tailspinCredit_11_4: "02aeccfd69cf9c23cb4344d948606a302840b78a5e3aeb273c07c81b0cfe0c6c",
  tailspinCredit_12_0: "8b2329bf61ae5b1558cc4d18f8931697498d7f5c509ca0e1c9ea773d3947a059",
  taxonomy_3_2: "1e19264f8d7b407b53234cbcb9eea27b0a90d6a0e4d5520ae1ee40364005a424",
  litware_2_4: "35751649ea771d03954c0ff60163e18aaf5a8410f10466ea0d112cd3f9cb1569",
  prosewarePay_4_2: "c5757da8582d7414cd6d7e60533686cec4eb6590b7f7607e6db6b9e8b876a714",
  prosewarePay_5_0: "d88a18d0694ff43d482fe5c3c55bdcebcd1cae26550678b4e476794d35dfffdb",
  adventure_1_3: "40c4b1d6f636a78778568adf56950844bf9f32443f228b3a0c9fd66db0da43d2",
  fabrikamIct_1_0: "a754ec2f0c75fe975b326f92f22912d5e6aafb1d015b3089f27b113cef4f5714",
  contosoSmh_2025_4: "e6a5ea18719b0cd095eed004d6a7dc369a60dd1026aaac09e4db242b163d7ad1",
  contosoSmh_2026_1: "52be20234b5af15de358b356ad247944bf4d048d62f164c8fe4da92a62701423",
  northwindClimate_1_1: "60d9349c47e1d8e7f82d5fb4d3e72aa684acac2280665a28a8429a6202612f52",
  northwindClimate_2_0: "e0f7986a439ef6a26db9098383ad07ea4a5950bec7f92c340c4fa90814634954",
  tailspinPrivacy_7_2: "30fea10e0bee2e00fa858b96977cf026d6c323405428e9541c92e57d3e99cbda",
  retention_1_0: "d4937f57545c63ca0a4435132c08079e73d4a15e36d5d2563eb98094e4005e75",
  prosewareTech_2_1: "4a806e3a9346eca0b4841fc2998b4e880b7fc1d4ab7d959ea900d572ed46ca6c",
} as const;

const SCHEDULED = "Scheduled re-check (system, demo)";
const INGESTION = "Ingestion pipeline (system, demo)";
const ANALYST_A = "Content analyst A (demo)";
const ANALYST_B = "Content analyst B (demo)";
const ALG = "SHA-256";

/** A check whose content matched the publisher's listed checksum and whose signature (if any) was valid. */
const passed = (id: string, sourceId: string, version: string, checkedAt: string, type: VerificationCheck["type"], performedBy: string,
  sum: string, signature: VerificationCheck["signature"] = "VALID", explanation?: string): VerificationCheck => ({
  id, sourceId, version, checkedAt, type, result: "PASSED", performedBy,
  checksumAlgorithm: ALG, expectedChecksum: sum, observedChecksum: sum, checksumOutcome: "MATCH", signature,
  explanation: explanation ?? (signature === "NOT_SIGNED"
    ? "The content matches the recorded checksum. Internal references are not signed, so no signature was checked."
    : "The content matches the checksum the publisher lists, and the publisher's signature is valid."),
});

export const DEMO_CHECKS: VerificationCheck[] = [
  passed("CHK-DEMO-0101", "SRC-DEMO-004", "2025 edition", "2026-01-12T06:00:00Z", "CHECKSUM_COMPARISON", INGESTION, SUM.woodgrove_2025),
  passed("CHK-DEMO-0102", "SRC-DEMO-010", "1.0", "2026-01-20T09:45:00Z", "CHECKSUM_COMPARISON", INGESTION, SUM.fabrikamIct_1_0),
  passed("CHK-DEMO-0103", "SRC-DEMO-007", "2.4", "2026-05-19T08:30:00Z", "CHECKSUM_COMPARISON", INGESTION, SUM.litware_2_4),
  passed("CHK-DEMO-0104", "SRC-DEMO-016", "2.1", "2026-06-06T10:00:00Z", "VERSION_CHANGE_REVIEW", ANALYST_A, SUM.prosewareTech_2_1, "VALID",
    "A content analyst compared version 2.1 with 2.0 and accepted the change. The content matches the publisher's checksum and the signature is valid."),
  passed("CHK-DEMO-0105", "SRC-DEMO-009", "1.3", "2026-06-28T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.adventure_1_3, "VALID",
    "The content still matches the publisher's checksum and the signature is valid. This was the last check before the publisher withdrew the source."),
  passed("CHK-DEMO-0106", "SRC-DEMO-006", "3.2", "2026-07-02T10:20:00Z", "VERSION_CHANGE_REVIEW", ANALYST_B, SUM.taxonomy_3_2, "NOT_SIGNED",
    "A content analyst compared version 3.2 with 3.1 and accepted the change. Internal references are not signed, so no signature was checked."),
  passed("CHK-DEMO-0107", "SRC-DEMO-001", "2026.2", "2026-08-02T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.contoso_2026_2),
  passed("CHK-DEMO-0108", "SRC-DEMO-005", "11.4", "2026-08-19T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.tailspinCredit_11_4),
  passed("CHK-DEMO-0109", "SRC-DEMO-012", "1.1", "2026-08-20T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.northwindClimate_1_1),
  passed("CHK-DEMO-0110", "SRC-DEMO-002", "4.0", "2026-09-03T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.fabrikamAml_4_0),
  passed("CHK-DEMO-0111", "SRC-DEMO-008", "4.2", "2026-09-12T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.prosewarePay_4_2),
  passed("CHK-DEMO-0112", "SRC-DEMO-011", "2025.4", "2026-09-30T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.contosoSmh_2025_4),
  passed("CHK-DEMO-0113", "SRC-DEMO-001", "2026.3", "2026-10-01T11:10:00Z", "VERSION_CHANGE_REVIEW", ANALYST_A, SUM.contoso_2026_3, "VALID",
    "A content analyst compared version 2026.3 with 2026.2 and accepted the change. The content matches the publisher's checksum and the signature is valid."),
  passed("CHK-DEMO-0114", "SRC-DEMO-014", "1.0", "2026-10-15T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.retention_1_0, "NOT_SIGNED"),
  passed("CHK-DEMO-0115", "SRC-DEMO-003", "1.8", "2026-10-28T08:15:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.northwindDp_1_8),
  {
    id: "CHK-DEMO-0116", sourceId: "SRC-DEMO-008", version: "5.0", checkedAt: "2026-10-30T11:40:00Z", type: "SIGNATURE_CHECK", result: "PENDING",
    performedBy: INGESTION, checksumAlgorithm: ALG, expectedChecksum: SUM.prosewarePay_5_0, observedChecksum: SUM.prosewarePay_5_0, checksumOutcome: "MATCH", signature: "KEY_CHANGED",
    explanation: "The content matches the publisher's checksum, but version 5.0 is signed with a key not recorded for earlier versions. The check is held until a person confirms the key change.",
  },
  passed("CHK-DEMO-0117", "SRC-DEMO-013", "7.2", "2026-11-02T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.tailspinPrivacy_7_2),
  passed("CHK-DEMO-0118", "SRC-DEMO-007", "2.4", "2026-11-03T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.litware_2_4),
  {
    id: "CHK-DEMO-0119", sourceId: "SRC-DEMO-002", version: "4.1", checkedAt: "2026-11-04T09:10:00Z", type: "CHECKSUM_COMPARISON", result: "FAILED",
    performedBy: INGESTION, checksumAlgorithm: ALG, expectedChecksum: SUM.fabrikamAml_4_1, observedChecksum: SUM.fabrikamAml_4_1_received, checksumOutcome: "MISMATCH", signature: "VALID",
    explanation: "The content received for version 4.1 does not match the checksum the publisher lists. The version was not adopted; version 4.0 remains in use.",
  },
  passed("CHK-DEMO-0120", "SRC-DEMO-006", "3.2", "2026-11-05T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.taxonomy_3_2, "NOT_SIGNED"),
  {
    id: "CHK-DEMO-0121", sourceId: "SRC-DEMO-012", version: "2.0", checkedAt: "2026-11-06T13:05:00Z", type: "SIGNATURE_CHECK", result: "FAILED",
    performedBy: INGESTION, checksumAlgorithm: ALG, expectedChecksum: SUM.northwindClimate_2_0, observedChecksum: SUM.northwindClimate_2_0, checksumOutcome: "MATCH", signature: "INVALID",
    explanation: "The content matches the publisher's checksum, but the publisher's signature on version 2.0 did not verify. The version must not be relied on until the publisher re-issues it or the failure is explained.",
  },
  passed("CHK-DEMO-0122", "SRC-DEMO-010", "1.0", "2026-11-07T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.fabrikamIct_1_0),
  {
    id: "CHK-DEMO-0123", sourceId: "SRC-DEMO-005", version: "12.0", checkedAt: "2026-11-07T10:05:00Z", type: "CHECKSUM_COMPARISON", result: "PENDING",
    performedBy: INGESTION, checksumAlgorithm: ALG, expectedChecksum: SUM.tailspinCredit_12_0, observedChecksum: null, checksumOutcome: "NOT_COMPARED", signature: "NOT_CHECKED",
    explanation: "Queued after version 12.0 was received. The content has not been read yet, so there is no observed checksum or signature outcome.",
  },
  passed("CHK-DEMO-0124", "SRC-DEMO-016", "2.1", "2026-11-08T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.prosewareTech_2_1),
  {
    id: "CHK-DEMO-0125", sourceId: "SRC-DEMO-003", version: "1.9", checkedAt: "2026-11-08T09:30:00Z", type: "VERSION_CHANGE_REVIEW", result: "PENDING",
    performedBy: ANALYST_B, checksumAlgorithm: ALG, expectedChecksum: null, observedChecksum: null, checksumOutcome: "NOT_COMPARED", signature: "NOT_CHECKED",
    explanation: "A content analyst opened a review of the publisher's version 1.9. The new version has not been received yet, so nothing has been compared.",
  },
  passed("CHK-DEMO-0126", "SRC-DEMO-004", "2025 edition", "2026-11-09T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.woodgrove_2025),
  {
    id: "CHK-DEMO-0127", sourceId: "SRC-DEMO-002", version: "4.1", checkedAt: "2026-11-10T14:22:00Z", type: "SCHEDULED_RECHECK", result: "FAILED",
    performedBy: SCHEDULED, checksumAlgorithm: ALG, expectedChecksum: SUM.fabrikamAml_4_1, observedChecksum: SUM.fabrikamAml_4_1_received, checksumOutcome: "MISMATCH", signature: "VALID",
    explanation: "The re-check found the same mismatch as CHK-DEMO-0119: the content received for version 4.1 does not match the checksum the publisher lists.",
  },
  passed("CHK-DEMO-0128", "SRC-DEMO-001", "2026.3", "2026-11-11T06:00:00Z", "SCHEDULED_RECHECK", SCHEDULED, SUM.contoso_2026_3),
  {
    id: "CHK-DEMO-0129", sourceId: "SRC-DEMO-011", version: "2026.1", checkedAt: "2026-11-11T07:30:00Z", type: "CHECKSUM_COMPARISON", result: "PENDING",
    performedBy: INGESTION, checksumAlgorithm: ALG, expectedChecksum: SUM.contosoSmh_2026_1, observedChecksum: null, checksumOutcome: "NOT_COMPARED", signature: "NOT_CHECKED",
    explanation: "Queued after version 2026.1 was received. The content has not been read yet, so there is no observed checksum or signature outcome.",
  },
];
