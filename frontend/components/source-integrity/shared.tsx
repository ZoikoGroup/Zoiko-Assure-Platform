import { CheckCircle2, CircleHelp, CircleOff, Clock, Eye, KeyRound, MinusCircle, XCircle } from "lucide-react";
import { ToneBadge, type BadgeMeta as Meta, type BadgeOpts } from "@/components/platformDemo";
import type {
  CheckResult, CheckType, ChecksumOutcome, IntegrityStatus, RegulatorySource, SignatureStatus, SourceType, VerificationCheck,
} from "./types";

/* The Source Integrity vocabulary (labels, tones, meanings), its badges, and small pure helpers shared by the screen's
   components. The generic pieces (the badge, the "Synthetic" marker, "no value" text, UTC time) live in
   components/platformDemo.tsx and are re-exported here, so this module's imports are unchanged. */

export { DemoTag, Missing, fmtTimeUtc } from "@/components/platformDemo";

export const INTEGRITY_STATUS: Record<IntegrityStatus, Meta & { rank: number }> = {
  VERIFICATION_FAILED: { rank: 0, label: "Verification Failed", tone: "critical", icon: XCircle,
    meaning: "The latest check failed: the content or its signature does not match what the publisher issued." },
  REVIEW_REQUIRED: { rank: 1, label: "Review Required", tone: "warning", icon: Eye,
    meaning: "A person must review a change before the source is relied on." },
  PENDING_VERIFICATION: { rank: 2, label: "Pending Verification", tone: "info", icon: Clock,
    meaning: "The current version has not completed a check yet." },
  VERIFIED: { rank: 3, label: "Verified", tone: "verified", icon: CheckCircle2,
    meaning: "The current version passed its latest check." },
  INACTIVE: { rank: 4, label: "Inactive", tone: "unknown", icon: CircleOff,
    meaning: "No longer in use and no longer re-checked." },
};

export const CHECK_RESULT: Record<CheckResult, Meta> = {
  PASSED: { label: "Passed", tone: "verified", icon: CheckCircle2, meaning: "The check found nothing wrong." },
  FAILED: { label: "Failed", tone: "critical", icon: XCircle, meaning: "The check found a mismatch or an invalid signature." },
  PENDING: { label: "Pending", tone: "info", icon: Clock, meaning: "The check has not finished or is waiting for a person." },
};

export const SIGNATURE: Record<SignatureStatus, Meta> = {
  VALID: { label: "Valid", tone: "verified", icon: CheckCircle2, meaning: "The publisher's signature verified with the key used for earlier versions." },
  INVALID: { label: "Did not verify", tone: "critical", icon: XCircle, meaning: "The publisher's signature did not verify." },
  KEY_CHANGED: { label: "Signed with a changed key", tone: "warning", icon: KeyRound, meaning: "Signed, but not with the key recorded for earlier versions." },
  NOT_SIGNED: { label: "Not signed", tone: "unknown", icon: MinusCircle, meaning: "The source carries no signature to check." },
  NOT_CHECKED: { label: "Not checked yet", tone: "unknown", icon: CircleHelp, meaning: "The check has not reached the signature yet." },
};

export const CHECKSUM_OUTCOME: Record<ChecksumOutcome, Meta> = {
  MATCH: { label: "Matches the expected checksum", tone: "verified", icon: CheckCircle2, meaning: "The observed checksum is the one the publisher lists." },
  MISMATCH: { label: "Does not match the expected checksum", tone: "critical", icon: XCircle, meaning: "The observed checksum differs from the one the publisher lists." },
  NOT_COMPARED: { label: "Not compared", tone: "unknown", icon: CircleHelp, meaning: "There was nothing to compare yet." },
};

export const SOURCE_TYPE_LABEL: Record<SourceType, string> = {
  REGULATORY_AUTHORITY: "Regulatory Authority",
  GOVERNMENT_PUBLICATION: "Government Publication",
  INDUSTRY_STANDARD: "Industry Standard",
  INTERNAL_REFERENCE: "Internal Reference",
};

export const CHECK_TYPE_LABEL: Record<CheckType, string> = {
  CHECKSUM_COMPARISON: "Checksum comparison",
  SIGNATURE_CHECK: "Signature check",
  VERSION_CHANGE_REVIEW: "Version change review",
  SCHEDULED_RECHECK: "Scheduled re-check",
};

/* --------------------------------------------------------------------------------------------------- badges */
export const IntegrityStatusBadge = ({ status, ...o }: BadgeOpts & { status: IntegrityStatus }) =>
  <ToneBadge meta={INTEGRITY_STATUS[status]} kind="Integrity status" {...o} />;
export const CheckResultBadge = ({ result, ...o }: BadgeOpts & { result: CheckResult }) =>
  <ToneBadge meta={CHECK_RESULT[result]} kind="Result" {...o} />;
export const SignatureBadge = ({ status, ...o }: BadgeOpts & { status: SignatureStatus }) =>
  <ToneBadge meta={SIGNATURE[status]} kind="Signature" {...o} />;
export const ChecksumOutcomeBadge = ({ outcome, ...o }: BadgeOpts & { outcome: ChecksumOutcome }) =>
  <ToneBadge meta={CHECKSUM_OUTCOME[outcome]} kind="Checksum" {...o} />;

/* --------------------------------------------------------------------------------------------------- helpers */
/** Newest first. */
export const byNewest = (a: VerificationCheck, b: VerificationCheck) => b.checkedAt.localeCompare(a.checkedAt);

/** The platform holds an older version than the publisher has issued. */
export const isOutdated = (s: RegulatorySource) =>
  !!s.currentVersion && !!s.publisherLatestVersion && s.currentVersion !== s.publisherLatestVersion;

/** Each source's checks, newest first. */
export function checksBySource(checks: VerificationCheck[]): Map<string, VerificationCheck[]> {
  const m = new Map<string, VerificationCheck[]>();
  for (const c of [...checks].sort(byNewest)) m.set(c.sourceId, [...(m.get(c.sourceId) ?? []), c]);
  return m;
}
