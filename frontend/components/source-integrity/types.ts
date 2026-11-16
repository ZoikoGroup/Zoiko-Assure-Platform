/* Source Integrity (Platform Control Plane): what the screen displays.

   These shapes describe the screen, not an API. No backend contract for Source Integrity exists yet, and none is
   implied here. When the backend defines its reads, map their responses onto these types in useSourceIntegrityData.ts
   and leave the components unchanged. Every value the screen shows today comes from sourceIntegrityMockData.ts. */

export const SOURCE_TYPES = ["REGULATORY_AUTHORITY", "GOVERNMENT_PUBLICATION", "INDUSTRY_STANDARD", "INTERNAL_REFERENCE"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

export const INTEGRITY_STATUSES = ["VERIFIED", "PENDING_VERIFICATION", "VERIFICATION_FAILED", "REVIEW_REQUIRED", "INACTIVE"] as const;
export type IntegrityStatus = (typeof INTEGRITY_STATUSES)[number];

export const CHECK_RESULTS = ["PASSED", "FAILED", "PENDING"] as const;
export type CheckResult = (typeof CHECK_RESULTS)[number];

export type CheckType = "CHECKSUM_COMPARISON" | "SIGNATURE_CHECK" | "VERSION_CHANGE_REVIEW" | "SCHEDULED_RECHECK";

/** What a check found about the publisher's signature. KEY_CHANGED: signed, but not with the key earlier versions used. */
export type SignatureStatus = "VALID" | "INVALID" | "KEY_CHANGED" | "NOT_SIGNED" | "NOT_CHECKED";

/** Whether the received content's checksum matched the publisher's, as the check recorded it. The screen only displays
 *  this outcome; it never compares the two values itself. */
export type ChecksumOutcome = "MATCH" | "MISMATCH" | "NOT_COMPARED";

export type Jurisdiction = { code: string; name: string };

/** A labelled reference to where the source comes from (a publisher reference, document identifier or location). */
export type SourceReference = { label: string; value: string };

export type RegulatorySource = {
  id: string;
  name: string;
  type: SourceType;
  jurisdiction: Jurisdiction;
  publisher: string;
  description: string | null;
  /** The version the platform holds. */
  currentVersion: string | null;
  previousVersion: string | null;
  /** The newest version the publisher is known to have issued. Differs from currentVersion when the platform is behind. */
  publisherLatestVersion: string | null;
  /** The current version's publication date (YYYY-MM-DD). */
  publishedAt: string | null;
  /** When the last completed check finished (ISO 8601, UTC). Null when the source has never been checked. */
  lastVerifiedAt: string | null;
  status: IntegrityStatus;
  /** Why the source has its status, when that needs saying. */
  statusNote: string | null;
  references: SourceReference[];
};

export type VerificationCheck = {
  id: string;
  sourceId: string;
  /** The source version the check covered. */
  version: string;
  /** When the check finished, or, for a pending check, when it was queued (ISO 8601, UTC). */
  checkedAt: string;
  type: CheckType;
  result: CheckResult;
  performedBy: string;
  checksumAlgorithm: string;
  /** The checksum the publisher lists for this version. Null when the publisher lists none. */
  expectedChecksum: string | null;
  /** The checksum of the content received. Null when the content has not been read yet. */
  observedChecksum: string | null;
  checksumOutcome: ChecksumOutcome;
  signature: SignatureStatus;
  explanation: string;
};

export type SourceIntegrityData = { sources: RegulatorySource[]; checks: VerificationCheck[] };
