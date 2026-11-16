"use client";

import { useId } from "react";
import { AlarmClock, Ban, Clock, FileStack, LockOpen, TimerOff } from "lucide-react";
import { KpiCard } from "@/components/ds";
import { DemoTag } from "@/components/platformDemo";
import { ILLUSTRATIVE, type Summary } from "./model";

/* The summary figures, each counted from the records on the page (including any local demonstration changes). */
export function JitSummaryCards({ s, local }: { s: Summary; local: boolean }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="type-secondary font-semibold text-fg-secondary">JIT access summary</h2>
        <DemoTag />
        <span className="type-caption text-fg-muted">
          Counted from the demonstration records below{local ? ", including your local demonstration changes" : ""}, at the demonstration clock.
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <li><KpiCard label="Total requests" icon={FileStack} value={s.total} hint={`${s.approved} approved · ${s.pending} pending · ${s.rejected} rejected`} /></li>
        <li><KpiCard label="Pending approval" tone="warning" icon={Clock} value={s.pending} hint="Waiting for an approver's decision" /></li>
        <li><KpiCard label="Active temporary sessions" tone="info" icon={LockOpen} value={s.active} hint={`Including ${s.expiringSoon} expiring soon`} /></li>
        <li><KpiCard label="Expiring soon" tone="warning" icon={AlarmClock} value={s.expiringSoon} hint={`Active sessions ending within ${ILLUSTRATIVE.expiringSoonMinutes} minutes (illustrative)`} /></li>
        <li><KpiCard label="Expired sessions" icon={TimerOff} value={s.expired} hint={`Ended at their expiry · ${s.revoked} more revoked early`} /></li>
        <li><KpiCard label="Rejected requests" tone="critical" icon={Ban} value={s.rejected} hint="No access was granted" /></li>
      </ul>
    </section>
  );
}
