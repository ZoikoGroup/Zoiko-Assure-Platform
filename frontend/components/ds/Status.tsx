import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Ban, Check, Clock, Info } from "lucide-react";
import { toneOf } from "@/components/ui";
import { STATUSES, type StatusKey } from "@/lib/design-tokens";

/* The canonical status vocabulary (Task 1) as components. Visual only: StatusBadge shows a status the caller chose, and
   StateBadge shows a controlled backend code in the visual family the application already gives it (toneOf in
   components/ui.tsx); neither decides what a record's state is. Colour is never the only signal: every use pairs the
   colour with an icon and text. */

export type { StatusKey };

export const STATUS_ICON: Record<StatusKey, LucideIcon> = { verified: Check, warning: Clock, critical: AlertTriangle, unknown: Ban, info: Info };
/** Border and tint only, for surfaces such as callouts. */
export const STATUS_SURFACE: Record<StatusKey, string> = {
  verified: "border-status-verified-line bg-status-verified-tint",
  warning: "border-status-warning-line bg-status-warning-tint",
  critical: "border-status-critical-line bg-status-critical-tint",
  unknown: "border-status-unknown-line bg-status-unknown-tint",
  info: "border-status-info-line bg-status-info-tint",
};
export const STATUS_TEXT: Record<StatusKey, string> = {
  verified: "text-status-verified", warning: "text-status-warning", critical: "text-status-critical", unknown: "text-status-unknown", info: "text-status-info",
};
/** Border, tint and text together, for badges, chips and marks. */
export const STATUS_TONE: Record<StatusKey, string> = Object.fromEntries(
  (Object.keys(STATUS_SURFACE) as StatusKey[]).map((k) => [k, `${STATUS_SURFACE[k]} ${STATUS_TEXT[k]}`]),
) as Record<StatusKey, string>;

export const statusLabel = (s: StatusKey) => STATUSES.find((x) => x.key === s)?.label ?? s;

/** A canonical status badge. The label is always the canonical one; `children` adds a qualifier such as a count. */
export function StatusBadge({ status, size = "md", children, className = "" }: { status: StatusKey; size?: "sm" | "md"; children?: ReactNode; className?: string }) {
  const Icon = STATUS_ICON[status];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border font-semibold ${size === "sm" ? "px-1.5 type-caption" : "px-2 py-0.5 type-caption"} ${STATUS_TONE[status]} ${className}`}>
      <Icon size={size === "sm" ? 11 : 12} strokeWidth={2.5} aria-hidden className="shrink-0" />
      {statusLabel(status)}
      {children != null && <><span aria-hidden className="opacity-60">·</span><span className="tabular-nums">{children}</span></>}
    </span>
  );
}

/** The canonical visual family for a controlled code, from the application's existing tone mapping. Proposed alignment
 *  for the two tones with no canonical equivalent: "review" (e.g. IN_REVIEW) and "exception" show as Needs Review. */
export function statusOfCode(code: string): StatusKey {
  const tone = toneOf(code);
  return tone === "good" ? "verified" : tone === "bad" ? "critical" : tone === "info" ? "info"
    : tone === "warn" || tone === "review" || tone === "exception" ? "warning" : "unknown";
}

/** A controlled state code (for example ATTESTED or IN_REVIEW) exactly as recorded, in its visual family. */
export function StateBadge({ code, status, size = "md", className = "" }: { code: string; status?: StatusKey; size?: "sm" | "md"; className?: string }) {
  const s = status ?? statusOfCode(code);
  const Icon = STATUS_ICON[s];
  return (
    <span title={`${code}: ${statusLabel(s)}`} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border ${size === "sm" ? "px-1.5" : "px-2 py-0.5"} ${STATUS_TONE[s]} ${className}`}>
      <Icon size={size === "sm" ? 11 : 12} strokeWidth={2.5} aria-hidden className="shrink-0" />
      <span className="type-code font-medium">{code}</span>
    </span>
  );
}

/** The round icon mark used in status tiles, toasts and alerts. Decorative: put the status in text beside it. */
export function StatusMark({ status, size = "md" }: { status: StatusKey; size?: "sm" | "md" | "lg" }) {
  const Icon = STATUS_ICON[status];
  const box = size === "sm" ? "h-6 w-6" : size === "lg" ? "h-10 w-10" : "h-8 w-8";
  return (
    <span aria-hidden className={`flex ${box} shrink-0 items-center justify-center rounded-full border ${STATUS_TONE[status]}`}>
      <Icon size={size === "sm" ? 13 : size === "lg" ? 18 : 15} />
    </span>
  );
}
