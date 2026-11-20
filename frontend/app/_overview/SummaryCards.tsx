"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Badge, LoadingSkeleton } from "@/components/ui";
import {
  CODE, LINK, PANEL, PANEL_TITLE, evidenceGaps, list, monthLabel, plural, stateOf, tally,
  type Dashboard, type Open, type Read, type Reads,
} from "./types";

/* B. Key figures. Each figure is a count the backend records, opens the screen where it is worked, and says what it
   counts in its tooltip. A figure that could not be read shows "—" and says so; it is never shown as zero. The six
   that open the page are cards in one row; the Overview's three further counts are listed in "More figures" below. */

type Tone = "bad" | "warn" | "info" | "ink";
const VALUE_TONE: Record<Tone, string> = { bad: "text-red-700", warn: "text-yellow-700", info: "text-teal-700", ink: "text-slate-900" };
export type Kpi = {
  key: string; label: string; hint: string; tone: Tone; href: string;
  value?: number; failed?: boolean; denied?: boolean; detail?: string;
};

/** The kinds of record awaiting an approver, naming only those that have any so the hint stays on one line (the
 *  card's tooltip and screen-reader text give both counts). */
const pendingKinds = (exceptions: number, compensations: number) =>
  [exceptions ? plural(exceptions, "exception") : "", compensations ? plural(compensations, "compensation") : ""]
    .filter(Boolean).join(" · ") || "exceptions & compensations";

/** Whether the reads a figure needs failed, or were refused to this role. */
function unread(...reads: Read<unknown>[]) {
  const s = reads.map(stateOf);
  return { failed: s.some((x) => x === "failed" || x === "denied"), denied: s.some((x) => x === "denied") };
}

/** Every figure the Overview reads: `key`, the six that open the page, and `more`, its three further counts. */
export function keyFigures(r: Reads, open: Open): { key: Kpi[]; more: Kpi[] } {
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

  // The six figures that open the page, as one row of cards.
  const key: Kpi[] = [
    { key: "obligations", label: "Obligations due / late", tone: occ?.overdue ? "bad" : "warn", href: "/obligations",
      value: occ?.unfulfilled, ...dash, hint: occ ? `${occ.overdue} overdue` : "awaiting fulfilment",
      detail: occ ? `Obligation occurrences awaiting fulfilment: ${occ.unfulfilled} · past due: ${occ.overdue}. ${occ.rule}` : undefined },
    { key: "controls", label: "Controls need action", tone: "warn", href: "/controls",
      value: off?.length, ...unread(r.controls),
      hint: !off ? "not operating"
        : off.length === 0 ? (controlCount ? `all ${controlCount} operating` : "no controls recorded")
        : offStates.every((s) => s === "FAILING" || s === "NOT_EVALUATED") ? "failed or untested" : "not operating",
      // The number of controls the figure is out of is in the tooltip and screen-reader text, keeping the hint short.
      detail: off?.length ? `${list(tally(offStates.map((s) => s ?? "configuration error")))} of ${plural(controlCount, "control")} (24-hour convenience view)` : undefined },
    { key: "evidence", label: "Evidence gaps", hint: "not current / IPE review", tone: "warn", href: "/evidence",
      value: d && anomalies ? gaps.reduce((a, [, v]) => a + v, 0) + anomalies.length : undefined, ...unread(r.dash, r.anomalies),
      detail: d && anomalies ? `Evidence not CURRENT: ${list(gaps) || "none"} · IPE anomalies awaiting disposition: ${anomalies.length}` : undefined },
    { key: "issues", label: "Open issues", tone: issues?.by_severity?.CRITICAL || issues?.by_severity?.HIGH ? "bad" : "warn", href: "/governance",
      value: issues?.total, ...dash,
      hint: issues ? `${(issues.by_severity.CRITICAL ?? 0) + (issues.by_severity.HIGH ?? 0)} critical or high` : "findings in remediation",
      detail: issues ? `Open issues by severity: ${list(Object.entries(issues.by_severity))}` : undefined },
    { key: "approvals", label: "Approvals pending", tone: "info", href: "/governance",
      value: exc && comp ? exc.length + comp.length : undefined, ...unread(r.exceptions, r.compensations),
      hint: exc && comp ? pendingKinds(exc.length, comp.length) : "awaiting a decision",
      detail: exc && comp ? `Awaiting an approver's decision (PENDING_APPROVAL): ${plural(exc.length, "exception request")} and ${plural(comp.length, "compensating-control proposal")}.` : undefined },
    { key: "facts", label: "Fact requests", hint: "facts needed to decide", tone: "info", href: "/fact-requests",
      value: d?.open_fact_requests, ...dash,
      detail: d ? `Open fact requests raised by UNCERTAIN applicability evaluations: ${d.open_fact_requests}` : undefined },
  ];
  // The three further counts the Overview reads, listed in "More figures" below the page's opening view.
  const more: Kpi[] = [
    { key: "adopted", label: "Adopted obligations", hint: "applicable, in scope", tone: "ink", href: "/obligations", value: adopted, ...dash,
      detail: totals ? `By latest assurance state: ${list(Object.entries(totals))}. ${d?.denominator_rule ?? ""}` : undefined },
    { key: "decisions", label: "Decisions awaiting attestation", tone: "info", href: "/applicability",
      value: det ? det.PROPOSED + det.IN_REVIEW : undefined, ...dash,
      hint: det ? `${det.PROPOSED} proposed · ${det.IN_REVIEW} in review` : "human sign-off",
      detail: det ? `Applicability determinations awaiting adoption: ${det.PROPOSED} proposed · ${det.IN_REVIEW} in review.${det.needs_review
        ? ` ${plural(det.needs_review, "adopted determination")} flagged for review because a newer evaluation's outcome differs.` : ""}` : undefined },
    { key: "tests", label: "Test runs (6 months)", tone: "ink", href: "/controls",
      value: runs ? runs.PASS + runs.FAIL + runs.OTHER : undefined, ...dash,
      hint: runs ? `${runs.PASS} pass · ${runs.FAIL} fail${runs.OTHER ? ` · ${runs.OTHER} other` : ""}` : "control tests",
      detail: runs ? `Control test runs recorded ${span}: ${runs.PASS} PASS, ${runs.FAIL} FAIL or ERROR, ${runs.OTHER} other. Counts of runs, not a pass rate.` : undefined },
  ];

  return { key, more };
}

/** The six key figures, one row of equal cards on desktops (two per row on phones, three on tablets). */
export function SummaryCards({ kpis, opens }: { kpis: Kpi[]; opens: (href: string) => boolean }) {
  return (
    <ul aria-label="Key figures" className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {kpis.map((k) => <li key={k.key} className="min-w-0"><KpiCard k={k} link={opens(k.href)} /></li>)}
    </ul>
  );
}

/** What a figure's hint line says: its meaning, or why it is missing. */
const note = (k: Kpi) => (k.denied ? "Not available to your role" : k.failed ? "Unavailable" : k.hint);

/** A figure, or its placeholder while it loads. The figure fades in where its placeholder was (no movement; off for
 *  reduced motion). */
function Figure({ k, size, placeholder }: { k: Kpi; size: string; placeholder: string }) {
  if (k.value === undefined && !k.failed) return <span role="status" aria-label="Loading" className={`skeleton ${placeholder}`} />;
  return <span className={`za-fade-in font-bold tabular-nums ${size} ${k.value ? VALUE_TONE[k.tone] : "text-slate-900"}`}>{k.value === undefined ? "—" : k.value}</span>;
}

const CARD = "flex h-full min-h-[108px] flex-col rounded-xl border border-slate-200 bg-white px-4 pb-2 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.06)]";

/** A figure card. `link`: the person can open the screen it points to; otherwise the card is not a link, so it never
 *  leads to a screen the server would refuse. */
function KpiCard({ k, link }: { k: Kpi; link: boolean }) {
  const body = (
    <>
      <span className="text-[14px] font-semibold leading-[19px] text-slate-800">{k.label}</span>
      <span className="mt-1 flex items-center justify-between gap-2"><Figure k={k} size="text-[28px] leading-8" placeholder="my-1 h-6 w-10" /></span>
      <span className="mt-1 text-[13px] leading-[17px] text-slate-500">{note(k)}</span>
      {k.detail && <span className="sr-only">. {k.detail}</span>}
    </>
  );
  return link ? (
    <Link href={k.href} title={k.detail}
      className={`${CARD} transition-[border-color,box-shadow] duration-150 ease-standard hover:border-slate-300 hover:shadow-[0_4px_12px_rgba(15,23,42,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600`}>
      {body}
    </Link>
  ) : <div title={k.detail} className={CARD}>{body}</div>;
}

/** The Overview's further counts (adopted obligations, decisions awaiting attestation, test runs), one row each, with
 *  the same placeholder, tooltip, screen-reader detail and role-limited link as the key figures. */
export function MoreFigures({ kpis, opens }: { kpis: Kpi[]; opens: (href: string) => boolean }) {
  return (
    <section aria-labelledby="more-h" className={`${PANEL} px-6 pb-3 pt-5`}>
      <h2 id="more-h" className={PANEL_TITLE}>More figures</h2>
      <p className="mt-0.5 text-[12px] text-slate-500">Further counts from your organisation&apos;s records.</p>
      <ul className="mt-1 divide-y divide-slate-100">
        {kpis.map((k) => <li key={k.key}><FigureRow k={k} link={opens(k.href)} /></li>)}
      </ul>
    </section>
  );
}

function FigureRow({ k, link }: { k: Kpi; link: boolean }) {
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[14px] font-semibold text-slate-800 ${link ? "transition-colors duration-150 group-hover:text-teal-800" : ""}`}>{k.label}</span>
        <span className="block truncate text-[12px] text-slate-500">{note(k)}</span>
      </span>
      <Figure k={k} size="text-[20px] leading-7" placeholder="h-5 w-8" />
      {link && <ChevronRight size={16} aria-hidden className="shrink-0 text-slate-400 transition-[color,transform] duration-150 group-hover:translate-x-0.5 group-hover:text-teal-700" />}
      {k.detail && <span className="sr-only">. {k.detail}</span>}
    </>
  );
  return link ? (
    <Link href={k.href} title={k.detail}
      className="group flex items-center gap-3 rounded py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
      {body}
    </Link>
  ) : <div title={k.detail} className="flex items-center gap-3 py-3">{body}</div>;
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
              <dt className={k === "NO_SNAPSHOT" ? undefined : CODE}>{k === "NO_SNAPSHOT" ? <span className="text-[13px] text-slate-600">No period snapshot yet</span> : <Badge code={k} />}</dt>
              <dd className="font-semibold tabular-nums text-slate-900">{totals[k] ?? 0}</dd>
            </div>
          ))}
          {/* Assurance Periods is the Obligations tab whose own address is /assurance; the link opens the tab directly. */}
          {opens("/assurance") && <div className="pt-1"><Link href="/obligations?tab=assurance-periods" className={`${LINK} text-[13px]`}>Open assurance periods</Link></div>}
        </dl>
      ) : s === "denied" ? <p className="mt-3 text-[13px] text-slate-500">Not available to your role</p>
        : s === "failed" ? <p className="mt-3 text-[13px] text-slate-500">Unavailable</p>
        : <LoadingSkeleton rows={3} className="px-0" />}
    </section>
  );
}
