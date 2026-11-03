"use client";

import { useEffect } from "react";
import { ArrowRight, Braces, ChevronRight, FlaskConical, Inbox, Library } from "lucide-react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { SuperAdminDashboard } from "@/components/PlatformAdmin";
import { useSyntheticDataBadge } from "@/components/Shell";
import { ErrorBox, Notice, PageHeader, usePageTitle } from "@/components/ui";

/* The Organization Command Center. A figure is read from the backend (dashboard projection, controls, IPE anomalies,
   organisation record) wherever the backend records that same thing. Nothing here derives applicability or assurance
   state in the browser, and nothing is a compliance score. Where the backend has no equivalent record, the reference
   design's synthetic demonstration figures are shown, each marked SYNTHETIC and never presented as this organisation's
   results or decisions. */
const TITLE = "Organization Command Center";

/* Synthetic demonstration data from the reference design (fictional "Orion" entities). Not records of any organisation. */
const DEMO_PRIORITIES = [
  { priority: "P0", id: "OBL-2047", task: "statutory filing", entity: "Orion UK", due: "Today", owner: "Legal" },
  { priority: "P0", id: "CTL-1012", task: "control test failed", entity: "Orion US", due: "Today", owner: "Controls" },
  { priority: "P1", id: "EVD-0881", task: "IPE validation needed", entity: "Orion EU", due: "Tomorrow", owner: "Evidence" },
  { priority: "P1", id: "REG-0234", task: "impact review", entity: "All 4 entities", due: "Nov 02", owner: "Analyst" },
  { priority: "P1", id: "AGT-0148", task: "proposal held", entity: "Orion US", due: "Today", owner: "AI Owner" },
] as const;
const DEMO_COVERAGE = { verified: 64, paths: 81, untested: 11, unknown: 6 };

/* Where each decision queue is worked. Links only: the backend has no count for these queues as named. */
const DECISION_QUEUES = [
  { label: "Waiting on legal applicability", href: "/applicability", opens: "Decisions & Attestations" },
  { label: "Attestation draft reviews", href: "/applicability", opens: "Decisions & Attestations" },
  { label: "Exceptions expiring soon", href: "/governance", opens: "Findings & Actions" },
];

type Tone = "bad" | "warn" | "info" | "ink";
const VALUE_TONE: Record<Tone, string> = { bad: "text-red-700", warn: "text-yellow-700", info: "text-teal-700", ink: "text-slate-900" };
type Kpi = { key: string; label: string; hint: string; tone: Tone; href: string; value?: number; failed?: boolean; synthetic?: boolean; detail?: string };

const PANEL = "rounded-xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]";
const list = (pairs: [string, number][]) => pairs.map(([k, v]) => `${v} ${k}`).join(" · ");
const tally = (codes: string[]) => Object.entries(codes.reduce((m: Record<string, number>, c) => ({ ...m, [c]: (m[c] ?? 0) + 1 }), {}));
const utcTime = (v: string) => { const t = new Date(v); return isNaN(t.getTime()) ? v : t.toISOString().slice(11, 16); };

export default function Dashboard() {
  const { me } = useAuth();
  const persona = personaOf(me);
  // Three home screens: the platform Super Admin's, content staff's workspace, and the Organization Command Center.
  if (me && persona === "platform") return <SuperAdminDashboard />;
  if (me && persona !== "tenant") return <ContentOpsHome name={me.display_name} />;
  return <CommandCenter />;
}

function CommandCenter() {
  const { me } = useAuth();
  const dash = useApi<any>("/dashboard");
  const org = useApi<any>("/org/tenant");
  const controls = useApi<any[]>("/controls");
  const anomalies = useApi<any[]>("/ipe/anomalies");
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  useSyntheticDataBadge();

  const d = dash.data;
  const orgName: string = org.data?.tenant.legal_name ?? me?.tenant_name ?? "";
  const jurisdictions = [...new Set<string>((org.data?.entities ?? []).map((e: any) => e.country))];
  // When the projection was read, and the home-cell record watermark it reflects.
  const readAt = d ? `read ${fmtDate(d.as_of.valid_at, true)}, known as of ${d.as_of.cell_id}#${d.as_of.record_seq}` : "";

  const ctlOff = (controls.data ?? []).filter((c) => c.current_state.state !== "OPERATING");
  const ev = (d?.evidence_conditions ?? {}) as Record<string, number>;
  const notCurrent = Object.entries(ev).filter(([k, v]) => v > 0 && k !== "CURRENT" && k !== "ERASED");  // erasure is a governed act, not a gap
  const openIpe = (anomalies.data ?? []).filter((a) => !a.disposition_code).length;
  const proposed = d?.determinations.PROPOSED ?? 0, inReview = d?.determinations.IN_REVIEW ?? 0;

  const kpis: Kpi[] = [
    { key: "obligations", label: "Obligations due / late", hint: "5 overdue", tone: "bad", href: "/obligations", value: 14, synthetic: true },
    { key: "controls", label: "Controls need action", tone: "warn", href: "/controls",
      value: controls.data ? ctlOff.length : undefined, failed: !!controls.error,
      hint: ctlOff.every((c) => ["FAILING", "NOT_EVALUATED"].includes(c.current_state.state)) ? "failed or untested" : "not operating",
      detail: ctlOff.length ? `${list(tally(ctlOff.map((c) => c.current_state.state)))} (24-hour convenience view)` : undefined },
    { key: "evidence", label: "Evidence assurance gaps", hint: "proof / IPE review", tone: "warn", href: "/evidence",
      value: d && anomalies.data ? notCurrent.reduce((a, [, v]) => a + v, 0) + openIpe : undefined, failed: !!(dash.error || anomalies.error),
      detail: d && anomalies.data ? `Evidence not CURRENT: ${list(notCurrent) || "none"} · IPE anomalies awaiting disposition: ${openIpe}` : undefined },
    { key: "changes", label: "Changes needing review", hint: "not legal decisions", tone: "warn", href: "/regulatory", value: 8, synthetic: true },
    { key: "approvals", label: "Approvals pending", hint: "human sign-off", tone: "info", href: "/applicability",
      value: d ? proposed + inReview : undefined, failed: !!dash.error,
      detail: d ? `Applicability determinations awaiting adoption: ${proposed} proposed · ${inReview} in review` : undefined },
    { key: "agents", label: "Agent actions held", hint: "policy gates", tone: "ink", href: "/ai", value: 3, synthetic: true },
  ];

  return (
    <div className="pt-1">
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <h1 className="text-[26px] font-semibold leading-tight text-slate-800 sm:text-[30px]">{TITLE}</h1>
          <p className="mt-1.5 text-[14px] text-slate-600">Enterprise assurance | evidence-backed, entity-scoped</p>
        </div>
        <div className="min-w-0 sm:max-w-[40%] sm:pt-2 sm:text-right">
          <div title={`Organization ${orgName}`} className="truncate text-[15px] font-bold uppercase tracking-[0.04em] text-teal-700">{orgName}</div>
          <div title={readAt ? `Dashboard ${readAt}` : undefined} className="mt-1 text-[13px] text-slate-600">
            {d ? `As of ${utcTime(d.as_of.valid_at)} UTC` : dash.error ? "Records unavailable" : "Reading records…"}
          </div>
        </div>
      </div>

      <section aria-label="Dashboard scope"
        className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-slate-200 bg-white px-4 py-2.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <span className="rounded-md bg-slate-100 px-2.5 py-1 text-[12px] font-semibold uppercase tracking-[0.03em] text-slate-700">Org / All authorized entities</span>
        <span className="text-[14px] text-slate-600">Jurisdictions: {jurisdictions.length ? jurisdictions.join(" • ") : "…"}</span>
        <span aria-hidden className="hidden h-5 w-px bg-slate-200 sm:block" />
        <span title="Each live figure is the latest recorded position when this page was read, not a total over a period." className="text-[14px] text-slate-600">
          Reporting: Current position
        </span>
        <Link href="/entities" title="Entities & Scope: the legal entities in this organization"
          className="ml-auto rounded text-[14px] font-semibold text-teal-700 hover:text-teal-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
          Change scope
        </Link>
      </section>

      <ErrorBox error={dash.error ?? controls.error ?? anomalies.error} />

      <ul aria-label="Key figures" className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {kpis.map((k) => <li key={k.key} className="min-w-0"><KpiCard k={k} /></li>)}
      </ul>

      <div className="grid grid-cols-1 gap-x-4 gap-y-6 xl:grid-cols-3">
        <Priorities className="xl:col-span-2" orgName={orgName} />
        <div className="grid content-start gap-6 md:grid-cols-2 xl:flex xl:flex-col">
          <section aria-labelledby="coverage-h" className={`${PANEL} relative px-6 pb-6 pt-[26px]`}>
            <h2 id="coverage-h" className="text-[18px] font-semibold leading-6 text-slate-900">Assurance coverage, not a score</h2>
            <SyntheticTag className={ON_BORDER} />
            <dl className="mt-4 space-y-2.5 text-[14px] leading-[22px]">
              <div className="flex flex-wrap items-baseline gap-x-2 font-semibold text-green-700">
                <dt>Verified proof paths</dt><dd className="tabular-nums">{DEMO_COVERAGE.verified} / {DEMO_COVERAGE.paths}</dd>
              </div>
              <div className="flex flex-wrap items-baseline gap-x-2 font-semibold text-yellow-700">
                <dt>Untested / unmapped</dt><dd className="tabular-nums">{DEMO_COVERAGE.untested}</dd>
              </div>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <dt className="text-slate-600">Unknown / under review</dt><dd className="font-semibold tabular-nums text-slate-900">{DEMO_COVERAGE.unknown}</dd>
              </div>
            </dl>
          </section>

          <section aria-labelledby="decisions-h" className={`${PANEL} px-6 pb-3 pt-[26px] xl:flex-1`}>
            <h2 id="decisions-h" className="text-[18px] font-semibold leading-6 text-slate-900">Decision integrity</h2>
            <ul className="mt-1 divide-y divide-slate-100">
              {DECISION_QUEUES.map((q) => (
                <li key={q.label}>
                  <Link href={q.href} title={`Open ${q.opens}`}
                    className="group flex items-center justify-between gap-3 rounded py-3.5 text-[15px] text-slate-700 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
                    {q.label}<ChevronRight size={16} aria-hidden className="shrink-0 text-slate-400 group-hover:text-teal-700" />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>

      <p className="mt-6 max-w-4xl text-[12px] leading-5 text-slate-600">
        Figures marked <span className="font-semibold">SYNTHETIC</span> are demonstration data from the reference design, not records of {orgName || "your organization"}.
        The other figures are read from your organization&apos;s records{readAt ? ` (${readAt})` : ""}. {d?.language_note}
      </p>
    </div>
  );
}

/* A panel's SYNTHETIC tag sits on its top border, so the panel's own layout stays as designed. */
const ON_BORDER = "absolute -top-2 right-5 bg-white";

/** Marks a figure or panel as synthetic demonstration data, never this organisation's records. */
function SyntheticTag({ className = "" }: { className?: string }) {
  return (
    <span title="Synthetic demonstration data, not a record of your organization"
      className={`inline-flex shrink-0 items-center rounded border border-dashed border-slate-300 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-600 ${className}`}>
      Synthetic<span className="sr-only"> demonstration data, not a record of your organization</span>
    </span>
  );
}

function KpiCard({ k }: { k: Kpi }) {
  const loading = k.value === undefined && !k.failed;
  return (
    <Link href={k.href} title={k.detail}
      className="flex h-full min-h-[118px] flex-col rounded-xl border border-slate-200 bg-white px-4 pb-2 pt-[18px] shadow-[0_1px_3px_rgba(15,23,42,0.06)] transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-[0_4px_12px_rgba(15,23,42,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
      <span className="text-[14px] font-semibold leading-[19px] text-slate-800">{k.label}</span>
      <span className="mt-1 flex items-center justify-between gap-2">
        {loading ? <span role="status" aria-label="Loading" className="skeleton my-1 h-6 w-10" />
          : <span className={`text-[30px] font-bold leading-8 tabular-nums ${k.value ? VALUE_TONE[k.tone] : "text-slate-900"}`}>{k.value === undefined ? "—" : String(k.value).padStart(2, "0")}</span>}
        {k.synthetic && <SyntheticTag />}
      </span>
      <span className="mt-1 text-[13px] leading-[17px] text-slate-500">{k.failed ? "Unavailable" : k.hint}</span>
      {k.detail && <span className="sr-only">. {k.detail}</span>}
    </Link>
  );
}

function PriorityPill({ p }: { p: "P0" | "P1" }) {
  return <span className={`inline-flex rounded px-1.5 text-[11px] font-semibold leading-[18px] ${p === "P0" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800"}`}>{p}</span>;
}

/** Priorities requiring your team: synthetic demonstration rows, a table from sm up and a stacked list on phones. */
function Priorities({ className = "", orgName }: { className?: string; orgName: string }) {
  return (
    <section aria-labelledby="priorities-h" className={`${PANEL} relative flex flex-col px-5 pb-5 pt-5 sm:px-6 sm:pt-6 ${className}`}>
      <h2 id="priorities-h" className="text-[20px] font-semibold leading-7 text-slate-900">Priorities requiring your team</h2>
      <SyntheticTag className={ON_BORDER} />
      <table className="mt-5 hidden w-full table-fixed border-collapse sm:table">
        <caption className="sr-only">Synthetic demonstration priorities from the reference design, not records of {orgName || "your organization"}</caption>
        <colgroup><col className="w-[65px]" /><col /><col className="w-[17%]" /><col className="w-[14%]" /><col className="w-[12%]" /></colgroup>
        <thead>
          <tr className="border-b border-slate-200">
            {["Priority", "Record / Task", "Entity", "Due", "Owner"].map((h) => (
              <th key={h} scope="col" className="pb-3.5 pr-3 text-left text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {DEMO_PRIORITIES.map((r) => (
            <tr key={r.id} className="border-b border-slate-100 last:border-0">
              <td className="py-4 pr-3"><PriorityPill p={r.priority} /></td>
              <td className="py-4 pr-3 text-[14px] font-semibold leading-[21px] text-slate-900">
                {r.id}<span aria-hidden className="mx-1 font-normal text-slate-400">•</span><span className="sr-only">, </span>{r.task}
              </td>
              <td className="py-4 pr-3 text-[14px] leading-[21px] text-slate-700">{r.entity}</td>
              <td className="py-4 pr-3 text-[14px] leading-[21px] text-slate-700">{r.due}</td>
              <td className="py-4 text-[14px] leading-[21px] text-slate-700">{r.owner}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul aria-label="Synthetic demonstration priorities" className="mt-3 divide-y divide-slate-100 sm:hidden">
        {DEMO_PRIORITIES.map((r) => (
          <li key={r.id} className="flex items-start gap-2.5 py-3">
            <span className="pt-0.5"><PriorityPill p={r.priority} /></span>
            <div className="min-w-0">
              <div className="text-[14px] font-semibold leading-[21px] text-slate-900">
                {r.id}<span aria-hidden className="mx-1 font-normal text-slate-400">•</span><span className="sr-only">, </span>{r.task}
              </div>
              <div className="mt-0.5 text-[13px] text-slate-600">{r.entity} · Due {r.due} · {r.owner}</div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ContentOpsHome({ name }: { name: string }) {
  return (
    <>
      <PageHeader kicker="Zoiko content operations" title={`Welcome, ${name.replace(/\s*\(.*\)/, "")}`}
        subtitle="Your workspace is the shared regulatory content and rules that customer organisations receive. You are not a member of any customer organisation." />
      <Notice>Zoiko content-operations staff have no standing access to customer data (INV-14).</Notice>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {[{ href: "/regulatory", label: "Regulatory Inbox", hint: "Ingest sources and propose extractions", icon: Inbox },
          { href: "/sources", label: "Sources & Provisions", hint: "Encode and verify requirements", icon: Library },
          { href: "/rules", label: "Rule Authoring", hint: "Validate, review and release", icon: Braces },
          { href: "/rules/workbench", label: "Rule Test Workbench", hint: "Golden vectors and synthetic fixtures", icon: FlaskConical }].map(({ href, label, hint, icon: Icon }) => (
          <Link key={href} href={href} className="card flex items-center gap-3 p-4 transition-colors hover:border-navy-300">
            <Icon size={18} aria-hidden className="text-navy-600" />
            <span><span className="block text-[13px] font-medium text-slate-900">{label}</span><span className="block text-xs text-slate-500">{hint}</span></span>
            <ArrowRight size={14} aria-hidden className="ml-auto text-slate-400" />
          </Link>
        ))}
      </div>
    </>
  );
}
