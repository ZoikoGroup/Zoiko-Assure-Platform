"use client";

import Link from "next/link";
import { AlertCircle, History, Lock } from "lucide-react";
import { fmtDate, short } from "@/lib/format";
import { EmptyState, LoadingSkeleton } from "@/components/ui";
import { PANEL, describeAggregate, describeEvent, stateOf, type Dashboard, type Read, type RecentEvent } from "./types";

/* D. Recent activity: the organisation's latest recorded events (GET /dashboard `recent_events`, the eight newest
   outbox events), newest first. Each is described from its recorded event type and names the record it concerns;
   events carry no actor, so none is shown or implied. */

/** Detail screens keyed by the identifier the event carries: evidence by evidence ID, an obligation by its lineage ID,
 *  an entity's subject by subject ID. Other records have no detail screen keyed that way, so they are named, not linked. */
const DETAIL: Record<string, (id: string) => string> = {
  EvidenceArtifact: (id) => `/evidence/${encodeURIComponent(id)}`,
  ObligationInstance: (id) => `/obligations/${encodeURIComponent(id)}`,
  Subject: (id) => `/entities/${encodeURIComponent(id)}`,
};

export function RecentActivity({ dash, opens }: { dash: Read<Dashboard>; opens: (href: string) => boolean }) {
  const s = stateOf(dash);
  const events = dash.data?.recent_events ?? [];
  return (
    <section aria-labelledby="activity-h" className={`${PANEL} px-5 pb-4 pt-5 sm:px-6`}>
      <h2 id="activity-h" className="text-[18px] font-semibold leading-7 text-slate-900">Recent activity</h2>
      <p className="mt-0.5 text-[12px] text-slate-500">The latest changes recorded for your organisation, newest first.</p>
      {s === "loading" ? <LoadingSkeleton rows={4} className="px-0" />
        : s === "denied" ? <EmptyState compact icon={Lock} title="Not available to your role" description="Your roles are not served your organisation's recorded events." />
        : s === "failed" ? <EmptyState compact icon={AlertCircle} title="Activity unavailable" description="Your organisation's records could not be read. Use Retry at the top of the page." />
        : events.length === 0 ? <EmptyState compact icon={History} title="No activity recorded yet" description="Changes to your organisation's records appear here as they are recorded." />
        : (
          <ol className="mt-2 divide-y divide-slate-100">
            {events.map((e, i) => <EventRow key={`${i}-${e.record_seq}-${e.aggregate_id}`} e={e} opens={opens} />)}
          </ol>
        )}
      {s === "ready" && (
        <p className="mt-2 text-[12px] leading-5 text-slate-500">
          These events do not record who acted.
          {opens("/ledger") && <>{" "}The <Link href="/ledger" className="font-semibold text-teal-700 hover:underline">Audit Trail</Link> lists recorded actions with their actor.</>}
        </p>
      )}
    </section>
  );
}

function EventRow({ e, opens }: { e: RecentEvent; opens: (href: string) => boolean }) {
  const href = DETAIL[e.aggregate_type]?.(e.aggregate_id);
  const item = <>{describeAggregate(e.aggregate_type)} <span className="mono">{short(e.aggregate_id, 8)}</span></>;
  return (
    <li className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <p className="text-[14px] font-semibold text-slate-900">{describeEvent(e.event_type)}</p>
        <p className="mt-0.5 break-words text-[12px] text-slate-500">
          <span className="mono break-all">{e.event_type}</span>{" · "}
          {href && opens(href)
            ? <Link href={href} title={`${describeAggregate(e.aggregate_type)} ${e.aggregate_id}`} className="font-medium text-teal-700 hover:underline">{item}</Link>
            : <span title={`${describeAggregate(e.aggregate_type)} ${e.aggregate_id}`}>{item}</span>}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-x-2 text-[12px] text-slate-500 sm:flex-col sm:items-end">
        <time dateTime={e.at}>{fmtDate(e.at, true)}</time>
        <span className="mono" title="Record sequence: the order in which the record was written">record #{e.record_seq}</span>
      </div>
    </li>
  );
}
