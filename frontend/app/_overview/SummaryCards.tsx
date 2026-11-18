"use client";

import Link from "next/link";
import { Badge, LoadingSkeleton } from "@/components/ui";
import {
  PANEL, evidenceGaps, list, monthLabel, plural, stateOf, tally,
  type Dashboard, type Open, type Read, type Reads,
} from "./types";

/* B. Summary cards. Each figure is a count the backend records, opens the screen where it is worked, and says what it
   counts in its tooltip. A figure that could not be read shows "—" and says so; it is never shown as zero. */

type Tone = "bad" | "warn" | "info" | "ink";
const VALUE_TONE: Record<Tone, string> = { bad: "text-red-700", warn: "text-yellow-700", info: "text-teal-700", ink: "text-slate-900" };
type Kpi = {
  key: string; label: string; hint: string; tone: Tone; href: string;
  value?: number; failed?: boolean; denied?: boolean; detail?: string;
};

/** Whether the reads a figure needs failed, or were refused to this role. */
function unread(...reads: Read<unknown>[]) {
  const s = reads.map(stateOf);
  return { failed: s.some((x) => x === "failed" || x === "denied"), denied: s.some((x) => x === "denied") };
}

export function SummaryCards({ r, open, opens }: { r: Reads; open: Open; opens: (href: string) => boolean }) {
  const d = r.dash.data;
  const dash = unread(r.dash);
  const totals = d?.assurance_totals;
  const adopted = totals ? Object.values(totals).reduce((a, v) => a + v, 0) : undefined;
  // Recorded occurrences of adopted obligations with no fulfilment yet, and how many are past due.
  const occ = d?.obligation_occurrences;
  const off = open.controlsOff;
  const controlCount = r.controls.data?.length ?? 0;
  const offStates = (off ?? []).map((c) => c.current_state.state);
  const gaps = d ? evidenceGaps(d) : [];
  const anomalies = open.anomalies;
  const issues = d?.open_issues;
  const months = d?.test_runs_by_month ?? [];
  const runs = d ? months.reduce((a, m) => ({ PASS: a.PASS + m.PASS, FAIL: a.FAIL + m.FAIL, OTHER: a.OTHER + m.OTHER }), { PASS: 0, FAIL: 0, OTHER: 0 }) : undefined;
  const span = months.length ? `${monthLabel(months[0].month)} to ${monthLabel(months[months.length - 1].month)}` : "the last six months";
  const det = d?.determinations;
  const exc = open.exceptions, comp = open.compensations;

  const position: Kpi[] = [
    { key: "adopted", label: "Adopted obligations", hint: "applicable, in scope", tone: "ink", href: "/obligations", value: adopted, ...dash,
      detail: totals ? `By latest assurance state: ${list(Object.entries(totals))}. ${d?.denominator_rule ?? ""}` : undefined },
    { key: "obligations", label: "Obligations due / late", tone: occ?.overdue ? "bad" : "warn", href: "/obligations",
      value: occ?.unfulfilled, ...dash, hint: occ ? `${occ.overdue} overdue` : "awaiting fulfilment",
      detail: occ ? `Obligation occurrences awaiting fulfilment: ${occ.unfulfilled} · past due: ${occ.overdue}. ${occ.rule}` : undefined },
    { key: "controls", label: "Controls need action", tone: "warn", href: "/controls",
      value: off?.length, ...unread(r.controls),
      hint: !off ? "not operating"
        : off.length === 0 ? (controlCount ? `all ${controlCount} operating` : "no controls recorded")
        : `of ${controlCount} · ${offStates.every((s) => s === "FAILING" || s === "NOT_EVALUATED") ? "failed or untested" : "not operating"}`,
      detail: off?.length ? `${list(tally(offStates.map((s) => s ?? "configuration error")))} (24-hour convenience view)` : undefined },
    { key: "tests", label: "Test runs (6 months)", tone: "ink", href: "/controls",
      value: runs ? runs.PASS + runs.FAIL + runs.OTHER : undefined, ...dash,
      hint: runs ? `${runs.PASS} pass · ${runs.FAIL} fail${runs.OTHER ? ` · ${runs.OTHER} other` : ""}` : "control tests",
      detail: runs ? `Control test runs recorded ${span}: ${runs.PASS} PASS, ${runs.FAIL} FAIL or ERROR, ${runs.OTHER} other. Counts of runs, not a pass rate.` : undefined },
    { key: "evidence", label: "Evidence gaps", hint: "not current / IPE review", tone: "warn", href: "/evidence",
      value: d && anomalies ? gaps.reduce((a, [, v]) => a + v, 0) + anomalies.length : undefined, ...unread(r.dash, r.anomalies),
      detail: d && anomalies ? `Evidence not CURRENT: ${list(gaps) || "none"} · IPE anomalies awaiting disposition: ${anomalies.length}` : undefined },
  ];

  const people: Kpi[] = [
    { key: "issues", label: "Open issues", tone: issues?.by_severity?.CRITICAL || issues?.by_severity?.HIGH ? "bad" : "warn", href: "/governance",
      value: issues?.total, ...dash,
      hint: issues ? `${(issues.by_severity.CRITICAL ?? 0) + (issues.by_severity.HIGH ?? 0)} critical or high` : "findings in remediation",
      detail: issues ? `Open issues by severity: ${list(Object.entries(issues.by_severity))}` : undefined },
    { key: "approvals", label: "Approvals pending", tone: "info", href: "/governance",
      value: exc && comp ? exc.length + comp.length : undefined, ...unread(r.exceptions, r.compensations),
      hint: exc && comp ? `${plural(exc.length, "exception")} · ${plural(comp.length, "compensation")}` : "awaiting a decision",
      detail: exc && comp ? `Awaiting an approver's decision (PENDING_APPROVAL): ${plural(exc.length, "exception request")} and ${plural(comp.length, "compensating-control proposal")}.` : undefined },
    { key: "decisions", label: "Decisions awaiting attestation", tone: "info", href: "/applicability",
      value: det ? det.PROPOSED + det.IN_REVIEW : undefined, ...dash,
      hint: det ? `${det.PROPOSED} proposed · ${det.IN_REVIEW} in review` : "human sign-off",
      detail: det ? `Applicability determinations awaiting adoption: ${det.PROPOSED} proposed · ${det.IN_REVIEW} in review.${det.needs_review
        ? ` ${plural(det.needs_review, "adopted determination")} flagged for review because a newer evaluation's outcome differs.` : ""}` : undefined },
    { key: "facts", label: "Fact requests", hint: "facts needed to decide", tone: "info", href: "/fact-requests",
      value: d?.open_fact_requests, ...dash,
      detail: d ? `Open fact requests raised by UNCERTAIN applicability evaluations: ${d.open_fact_requests}` : undefined },
  ];

  return (
    <div className="mb-6 space-y-5">
      <KpiGroup id="kpi-position" title="Assurance position" items={position} opens={opens} cols="grid-cols-2 md:grid-cols-6 xl:grid-cols-5" spans={POSITION_SPANS} />
      <KpiGroup id="kpi-people" title="Waiting on people" items={people} opens={opens} cols="grid-cols-2 md:grid-cols-4" />
    </div>
  );
}

/* Five cards fill whole rows at every width: two per row with the last one full-width on phones, three then two on
   tablets, five on desktops. */
const POSITION_SPANS = [
  "md:col-span-2 xl:col-span-1", "md:col-span-2 xl:col-span-1", "md:col-span-2 xl:col-span-1",
  "md:col-span-3 xl:col-span-1", "col-span-2 md:col-span-3 xl:col-span-1",
];

function KpiGroup({ id, title, items, cols, spans, opens }:
  { id: string; title: string; items: Kpi[]; cols: string; spans?: string[]; opens: (href: string) => boolean }) {
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="mb-2 text-[12px] font-semibold uppercase tracking-[0.06em] text-slate-500">{title}</h2>
      <ul className={`grid gap-3 ${cols}`}>
        {items.map((k, i) => <li key={k.key} className={`min-w-0 ${spans?.[i] ?? ""}`}><KpiCard k={k} link={opens(k.href)} /></li>)}
      </ul>
    </section>
  );
}

const CARD = "flex h-full min-h-[108px] flex-col rounded-xl border border-slate-200 bg-white px-4 pb-2 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.06)]";

/** A figure card. `link`: the person can open the screen it points to; otherwise the card is not a link, so it never
 *  leads to a screen the server would refuse. */
function KpiCard({ k, link }: { k: Kpi; link: boolean }) {
  const loading = k.value === undefined && !k.failed;
  const body = (
    <>
      <span className="text-[14px] font-semibold leading-[19px] text-slate-800">{k.label}</span>
      <span className="mt-1 flex items-center justify-between gap-2">
        {loading ? <span role="status" aria-label="Loading" className="skeleton my-1 h-6 w-10" />
          : <span className={`text-[28px] font-bold leading-8 tabular-nums ${k.value ? VALUE_TONE[k.tone] : "text-slate-900"}`}>{k.value === undefined ? "—" : k.value}</span>}
      </span>
      <span className="mt-1 text-[13px] leading-[17px] text-slate-500">{k.denied ? "Not available to your role" : k.failed ? "Unavailable" : k.hint}</span>
      {k.detail && <span className="sr-only">. {k.detail}</span>}
    </>
  );
  return link ? (
    <Link href={k.href} title={k.detail}
      className={`${CARD} transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-[0_4px_12px_rgba(15,23,42,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600`}>
      {body}
    </Link>
  ) : <div title={k.detail} className={CARD}>{body}</div>;
}

/** Adopted obligations by their latest assurance state (GET /dashboard `assurance_totals`). Not a score. */
export function AssurancePosition({ dash, opens }: { dash: Read<Dashboard>; opens: (href: string) => boolean }) {
  const d = dash.data;
  const s = stateOf(dash);
  const totals = d?.assurance_totals ?? null;
  return (
    <section aria-labelledby="coverage-h" className={`${PANEL} px-6 pb-5 pt-5`}>
      <h2 id="coverage-h" className="text-[18px] font-semibold leading-6 text-slate-900">Obligation assurance</h2>
      <p className="mt-0.5 text-[12px] text-slate-500" title={d?.denominator_rule}>Adopted obligations by latest assurance state. Not a score.</p>
      {totals ? (
        <dl className="mt-3 space-y-2 text-[14px]">
          {(["PROVEN", "NOT_PROVEN", "FAILING", "REQUIRES_REVIEW", "NO_SNAPSHOT"] as const).map((k) => (
            <div key={k} className="flex items-center justify-between gap-2">
              <dt>{k === "NO_SNAPSHOT" ? <span className="text-[13px] text-slate-600">No period snapshot yet</span> : <Badge code={k} />}</dt>
              <dd className="font-semibold tabular-nums text-slate-900">{totals[k] ?? 0}</dd>
            </div>
          ))}
          {opens("/assurance") && <div className="pt-1"><Link href="/assurance" className="text-[13px] font-semibold text-teal-700 hover:underline">Open assurance reports</Link></div>}
        </dl>
      ) : s === "denied" ? <p className="mt-3 text-[13px] text-slate-500">Not available to your role</p>
        : s === "failed" ? <p className="mt-3 text-[13px] text-slate-500">Unavailable</p>
        : <LoadingSkeleton rows={3} className="px-0" />}
    </section>
  );
}
