"use client";

import { useId } from "react";
import { Ban, Building2, CheckCircle2, Flag, ScrollText, Server } from "lucide-react";
import { KpiCard } from "@/components/ds";
import { DemoTag } from "@/components/platformDemo";
import { count } from "@/components/platformQueue";
import type { Summary } from "./model";

/* The summary figures, all counted from the same demonstration records (filters below do not change them). The last
   three rest on illustrative fields the audit model does not store, and say so on the card. */

function DemoOnly() {
  return <span className="rounded-chip border border-dashed border-line-strong px-1 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-secondary">Demo-only metric</span>;
}

export function AuditSummaryCards({ s }: { s: Summary }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="type-secondary font-semibold text-fg-secondary">Audit summary</h2>
        <DemoTag />
        <span className="type-caption text-fg-muted">Counted from all the demonstration records below; the filters do not change these figures.</span>
      </div>
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <li><KpiCard label="Total audit events" icon={ScrollText} value={s.total}
          hint={s.total ? `Records #${s.seqMin} to #${s.seqMax} (record_seq)` : "No records"} /></li>
        <li><KpiCard label="Platform events" tone="info" icon={Server} value={s.platform} hint="Records with an empty tenant_id" /></li>
        <li><KpiCard label="Organisation events" icon={Building2} value={s.organisation}
          hint={`Records with a tenant_id, across ${count(s.organisations, "organisation")}`} /></li>
        <li><KpiCard label="Successful activities" tone="verified" icon={CheckCircle2} value={s.succeeded}
          hint={<><DemoOnly /> Outcome derived from the action name</>} /></li>
        <li><KpiCard label="Denied activities" tone="critical" icon={Ban} value={s.denied}
          hint={<><DemoOnly /> Refused or failed attempts, by action name</>} /></li>
        <li><KpiCard label="Events requiring review" tone="warning" icon={Flag} value={s.review}
          hint={<><DemoOnly /> An illustrative review rule, not a platform rule</>} /></li>
      </ul>
    </section>
  );
}
