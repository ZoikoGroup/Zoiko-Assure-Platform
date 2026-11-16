import { FlaskConical } from "lucide-react";
import { ToneBadge, type BadgeOpts } from "@/components/platformDemo";
import { ACCESS, APPROVAL, EVENT, type EventKind } from "./model";
import type { AccessStatus, ApprovalStatus } from "./types";

/* JIT Access badges (the shared ToneBadge with this module's vocabulary) and the marker for local demonstration
   changes. */

export const ApprovalBadge = ({ status, ...o }: BadgeOpts & { status: ApprovalStatus }) => <ToneBadge meta={APPROVAL[status]} kind="Approval status" {...o} />;
export const AccessBadge = ({ status, ...o }: BadgeOpts & { status: AccessStatus }) => <ToneBadge meta={ACCESS[status]} kind="Access status" {...o} />;
export const EventBadge = ({ kind, ...o }: BadgeOpts & { kind: EventKind }) => <ToneBadge meta={EVENT[kind]} kind="Event" {...o} />;

/** Marks something changed on this page: local demonstration state that was never sent anywhere. */
export function LocalTag() {
  return (
    <span title="Changed on this page only: local demonstration state, lost on reload. Nothing was sent and no access changed."
      className="inline-flex shrink-0 items-center gap-1 rounded-chip border border-dashed border-accent/60 bg-accent/10 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-accent-fg">
      <FlaskConical size={11} aria-hidden />Local demo<span className="sr-only">: changed on this page only, nothing was sent</span>
    </span>
  );
}
