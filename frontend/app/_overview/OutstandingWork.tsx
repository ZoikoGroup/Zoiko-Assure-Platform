"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { fmtDate } from "@/lib/format";
import { roleLabel } from "@/lib/roles";
import { Badge, EmptyState, LoadingSkeleton, Notice } from "@/components/ui";
import {
  PANEL, codeWords, evidenceGaps, isPast, joinWords, list, plural, sentence, stateOf,
  type Open, type Read, type Reads,
} from "./types";

/* C. Outstanding work: what needs someone now, straight from the organisation's records. Each row says what it is,
   its recorded state and due date where the record has one, and opens the screen where it is worked. Obligation
   occurrences are a count: the organisation-wide view records no per-occurrence list (each obligation's page does). */

/** `due`: undefined when the kind of record has no due date, null when this record has none. */
type WorkItem = {
  key: string; level: "high" | "medium"; what: string; detail: string;
  state?: string; due?: string | null; overdue?: boolean; href: string;
};

const SHOWN = 8;
const CONTROL_TEXT: Record<string, string> = {
  FAILING: "Control failing", REQUIRES_REVIEW: "Control requires review", NOT_EVALUATED: "Not yet tested", DEGRADED: "Control degraded",
};
const HIGH_SEVERITY = ["CRITICAL", "HIGH"];

function workItems(r: Reads, open: Open, now: number): WorkItem[] {
  const d = r.dash.data;
  const occ = d?.obligation_occurrences;
  const det = d?.determinations;
  const items: WorkItem[] = [];

  if (occ?.overdue) {
    items.push({ key: "occ", level: "high", overdue: true, what: `${plural(occ.overdue, "obligation occurrence")} past due`,
      detail: "Record fulfilment with evidence · due dates are on each obligation's page", href: "/obligations" });
  }
  for (const c of open.controlsOff ?? []) {
    const s = c.current_state.state;
    const name = c.definition?.title || c.method;
    const href = `/controls/${encodeURIComponent(c.control_implementation_id)}`;
    items.push(s === null
      ? { key: `ctl-${c.control_implementation_id}`, level: "high", what: name, detail: `Configuration error: cannot be evaluated · ${c.subject}`, href }
      : { key: `ctl-${c.control_implementation_id}`, level: s === "FAILING" ? "high" : "medium", what: name,
          detail: `${CONTROL_TEXT[s] ?? "Not operating"} · ${c.subject}`, state: s, href });
  }
  if (open.issues) {
    for (const i of open.issues) {
      items.push({ key: `iss-${i.issue_id}`, level: HIGH_SEVERITY.includes(i.severity) || isPast(i.due_at, now) ? "high" : "medium",
        what: i.title, detail: `${sentence(codeWords(i.issue_type))} · ${codeWords(i.severity)} severity · ${i.priority}`,
        state: i.state, due: i.due_at, href: "/governance" });
    }
  } else if (d?.open_issues.total) {  // the list could not be read: the recorded count stands in for it
    const sev = d.open_issues.by_severity;
    items.push({ key: "issues", level: sev.CRITICAL || sev.HIGH ? "high" : "medium", what: plural(d.open_issues.total, "open issue"),
      detail: list(Object.entries(sev).filter(([, v]) => v > 0)), href: "/governance" });
  }
  for (const x of open.exceptions ?? []) {
    items.push({ key: `exc-${x.exception_id}`, level: "medium", what: `Exception request: ${x.reason}`,
      detail: `${sentence(codeWords(x.target_type))} · requested for ${fmtDate(x.valid_from)} → ${fmtDate(x.valid_to)}`,
      state: x.state, href: "/governance" });
  }
  for (const c of open.compensations ?? []) {
    items.push({ key: `cmp-${c.compensation_id}`, level: "medium", what: `Compensating control proposed for ${c.primary_control_key}`,
      detail: `${c.equivalence_rationale} · from ${fmtDate(c.valid_from)}`, state: c.status, href: "/governance" });
  }
  if (det && det.PROPOSED + det.IN_REVIEW) {
    items.push({ key: "det", level: "medium", what: `${plural(det.PROPOSED + det.IN_REVIEW, "decision")} awaiting attestation`,
      detail: `Applicability determinations: ${det.PROPOSED} proposed · ${det.IN_REVIEW} in review · needs an authorised human`, href: "/applicability" });
  }
  if (open.facts) {
    for (const f of open.facts) {
      items.push({ key: `fact-${f.request_id}`, level: isPast(f.due_at, now) ? "high" : "medium", what: `Fact needed: ${f.fact_label}`,
        detail: `${f.subject} · owner ${roleLabel(f.owner_role)}`, state: f.status, due: f.due_at, href: "/fact-requests" });
    }
  } else if (d?.open_fact_requests) {  // not read for this role, or it failed: the recorded count stands in for it
    items.push({ key: "facts", level: "medium", what: `${plural(d.open_fact_requests, "fact request")} open`,
      detail: "Missing facts keep applicability UNCERTAIN", href: "/fact-requests" });
  }
  for (const a of open.anomalies ?? []) {
    items.push({ key: `ipe-${a.anomaly_id}`, level: HIGH_SEVERITY.includes(a.severity) ? "high" : "medium",
      what: `IPE anomaly: ${codeWords(a.anomaly_class)}`, detail: `${codeWords(a.severity)} severity · awaiting disposition · raised ${fmtDate(a.created_at)}`,
      href: "/ipe" });
  }
  for (const [k, v] of d ? evidenceGaps(d) : []) {
    items.push({ key: `ev-${k}`, level: k === "INVALID" ? "high" : "medium", what: `${plural(v, "evidence record")} ${codeWords(k)}`,
      detail: "Cannot support current-period proof", href: "/evidence" });
  }
  return items;
}

/** Past due first, then high, then medium; within each, the earliest due date first, then records without one. */
function ordered(items: WorkItem[], now: number) {
  const key = (i: WorkItem): [number, number] => {
    const due = i.due ? Date.parse(i.due) : NaN;
    const late = i.overdue || isPast(i.due, now);
    return [late ? 0 : i.level === "high" ? 1 : 2, isNaN(due) ? Infinity : due];
  };
  return [...items].sort((a, b) => { const [ta, da] = key(a), [tb, db] = key(b); return ta - tb || da - db; });
}

// What each read contributes, in the words the section uses when it cannot be read.
const SOURCES: [keyof Reads, string][] = [
  ["dash", "obligation, decision and evidence figures"], ["controls", "controls"], ["issues", "issues"],
  ["exceptions", "exception requests"], ["compensations", "compensation proposals"], ["facts", "fact requests"],
  ["anomalies", "IPE anomalies"],
];

export function OutstandingWork({ r, open, now, opens, onRetry }:
  { r: Reads; open: Open; now: number; opens: (href: string) => boolean; onRetry: () => void }) {
  const [all, setAll] = useState(false);
  const states = SOURCES.map(([k, label]) => [label, stateOf(r[k] as Read<unknown>)] as const);
  const loading = states.some(([, s]) => s === "loading");
  const failed = states.filter(([, s]) => s === "failed").map(([l]) => l);
  const denied = states.filter(([, s]) => s === "denied").map(([l]) => l);
  const items = loading ? [] : ordered(workItems(r, open, now), now);
  const shown = all ? items : items.slice(0, SHOWN);

  return (
    <section aria-labelledby="work-h" className={`${PANEL} flex flex-col px-5 pb-4 pt-5 sm:px-6`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="work-h" className="text-[18px] font-semibold leading-7 text-slate-900">Outstanding work</h2>
        {!loading && items.length > 0 && <span className="text-[12px] text-slate-500">{plural(items.length, "item")}</span>}
      </div>
      {loading ? <LoadingSkeleton rows={4} className="px-0" /> : (
        <>
          {(failed.length > 0 || denied.length > 0) && (
            <div className="mt-2 space-y-2">
              {failed.length > 0 && (
                <Notice tone="warn">
                  {sentence(joinWords(failed))} could not be read, so this list may be incomplete.{" "}
                  <button type="button" onClick={onRetry} className="font-semibold underline underline-offset-2">Retry</button>
                </Notice>
              )}
              {denied.length > 0 && <Notice>{sentence(joinWords(denied))} are not available to your role, so they are not listed.</Notice>}
            </div>
          )}
          {items.length === 0 ? (
            failed.length || denied.length
              ? <p className="mt-3 text-[13px] text-slate-500">Nothing is outstanding in the records that could be read.</p>
              : <EmptyState compact title="Nothing needs attention right now"
                  description="No overdue obligations, controls that are not operating, open issues, pending approvals or decisions, open fact requests, IPE anomalies or evidence gaps are recorded." />
          ) : (
            <>
              <ul id="work-list" className="mt-2 divide-y divide-slate-100">
                {shown.map((item) => <WorkRow key={item.key} item={item} now={now} link={opens(item.href)} />)}
              </ul>
              {items.length > SHOWN && (
                <div className="pt-3">
                  <button type="button" aria-expanded={all} aria-controls="work-list" onClick={() => setAll(!all)}
                    className="rounded text-[13px] font-semibold text-teal-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
                    {all ? "Show fewer" : `Show all ${items.length}`}
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function Due({ due, overdue }: { due?: string | null; overdue: boolean }) {
  if (due === undefined) return overdue ? <span className="whitespace-nowrap text-[12px] font-semibold text-red-700">Past due</span> : null;
  if (due === null) return <span className="whitespace-nowrap text-[12px] text-slate-500">No due date</span>;
  return (
    <time dateTime={due} className={`whitespace-nowrap text-[12px] ${overdue ? "font-semibold text-red-700" : "text-slate-600"}`}>
      {overdue ? "Overdue · " : "Due "}{fmtDate(due)}
    </time>
  );
}

/** `link`: the person can open the screen the row points to; otherwise the row is not a link. */
function WorkRow({ item, now, link }: { item: WorkItem; now: number; link: boolean }) {
  const overdue = !!item.overdue || isPast(item.due, now);
  const hasMeta = !!item.state || item.due !== undefined || overdue;
  const meta: ReactNode = <>{item.state && <Badge code={item.state} />}<Due due={item.due} overdue={overdue} /></>;
  const body = (
    <>
      <span className={`mt-0.5 inline-flex w-14 shrink-0 justify-center rounded px-1.5 text-[11px] font-semibold leading-[18px] ${item.level === "high" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800"}`}>
        {item.level === "high" ? "High" : "Medium"}
      </span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[14px] font-semibold text-slate-900 ${link ? "group-hover:text-teal-800" : ""}`}>{item.what}</span>
        <span className="block truncate text-[12px] text-slate-500">{item.detail}</span>
        {hasMeta && <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 sm:hidden">{meta}</span>}
      </span>
      {hasMeta && <span className="hidden shrink-0 flex-col items-end gap-1 sm:flex">{meta}</span>}
      {link && <ChevronRight size={16} aria-hidden className="mt-0.5 shrink-0 text-slate-400 group-hover:text-teal-700" />}
    </>
  );
  return (
    <li>
      {link ? (
        <Link href={item.href} title={item.what}
          className="group flex items-start gap-3 rounded py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
          {body}
        </Link>
      ) : <div title={item.what} className="flex items-start gap-3 py-3">{body}</div>}
    </li>
  );
}
