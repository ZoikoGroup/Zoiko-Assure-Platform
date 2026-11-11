"use client";

import { useEffect } from "react";
import type { ReactNode } from "react";
import { ArrowRight, Braces, ChevronRight, FlaskConical, Inbox, Library } from "lucide-react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { PlatformCommandCenter } from "@/components/PlatformCommandCenter";
import SCREEN_ACCESS from "@/lib/screen-access.json";
import { Badge, EmptyState, ErrorBox, LoadingSkeleton, Notice, PageHeader, usePageTitle } from "@/components/ui";

/* The Organization Command Center. Every figure is read from the backend (dashboard projection, controls, IPE
   anomalies, organisation record) and links to the screen where it is worked. Nothing here derives applicability or
   assurance state in the browser, nothing is a compliance score, and nothing is demonstration data: where the backend
   records no figure, none is shown. */
const TITLE = "Organization Command Center";

type Tone = "bad" | "warn" | "info" | "ink";
const VALUE_TONE: Record<Tone, string> = { bad: "text-red-700", warn: "text-yellow-700", info: "text-teal-700", ink: "text-slate-900" };
type Kpi = { key: string; label: string; hint: string; tone: Tone; href: string; value?: number; failed?: boolean; detail?: string };

const PANEL = "rounded-xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]";
const list = (pairs: [string, number][]) => pairs.map(([k, v]) => `${v} ${k}`).join(" · ");
const tally = (codes: string[]) => Object.entries(codes.reduce((m: Record<string, number>, c) => ({ ...m, [c]: (m[c] ?? 0) + 1 }), {}));
const utcTime = (v: string) => { const t = new Date(v); return isNaN(t.getTime()) ? v : t.toISOString().slice(11, 16); };
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export default function Dashboard() {
  const { me } = useAuth();
  const persona = personaOf(me);
  // Three home screens: the platform Super Admin's Platform Command Center (its organisation registry is Tenants &
  // Entities, /organisations), content staff's workspace, and the Organization Command Center.
  if (me && persona === "platform") return <PlatformCommandCenter />;
  if (me && persona !== "tenant") return <ContentOpsHome name={me.display_name} />;
  return <CommandCenter />;
}

/* Which of the Command Center's reads this person's roles are served (lib/screen-access.json, generated from the
   backend guards). A read that would be refused is not made: every refusal is an audit record. */
const holds = (perms: string[], mine?: readonly string[]) => perms.some((p) => mine?.includes(p));

function CommandCenter() {
  const { me } = useAuth();
  const reader = holds(SCREEN_ACCESS.panels.overview.perms, me?.permissions);
  if (!reader) return <AccessAdminHome orgName={me?.tenant_name ?? ""} />;
  return <CommandCenterView />;
}

/** A role administrator (e.g. TENANT_SECURITY_ADMIN) administers access, not the regulatory record (ZA-IAM-001 §08),
 *  so the Command Center's figures are not served to it. Its home says so and points to its own screen. */
function AccessAdminHome({ orgName }: { orgName: string }) {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return (
    <>
      <PageHeader kicker={orgName} title={TITLE} subtitle="Your organisation's current assurance position." />
      <Notice>Your roles administer who can access {orgName || "this organization"}, not its regulatory records, so there are no figures to show here.
        Manage people, roles and sessions in <Link href="/admin/users" className="font-semibold text-teal-700 hover:underline">Team &amp; Access</Link>.</Notice>
    </>
  );
}

/** One open item from the organisation's own records, and where it is worked. */
type Attention = { key: string; level: "high" | "medium"; what: string; detail: string; href: string };

function CommandCenterView() {
  const { me } = useAuth();
  const { can } = useCan();
  const dash = useApi<any>("/dashboard");
  const org = useApi<any>(holds(SCREEN_ACCESS.shell.entity_scope.perms, me?.permissions) ? "/org/tenant" : null);
  const controls = useApi<any[]>("/controls");
  const anomalies = useApi<any[]>("/ipe/anomalies");
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);

  const d = dash.data;
  const orgName: string = org.data?.tenant.legal_name ?? me?.tenant_name ?? "";
  const jurisdictions = [...new Set<string>((org.data?.entities ?? []).map((e: any) => e.country))];
  // When the projection was read, and the home-cell record watermark it reflects.
  const readAt = d ? `read ${fmtDate(d.as_of.valid_at, true)}, known as of ${d.as_of.cell_id}#${d.as_of.record_seq}` : "";

  const ctlOff = (controls.data ?? []).filter((c) => c.current_state.state !== "OPERATING");
  const ev = (d?.evidence_conditions ?? {}) as Record<string, number>;
  const notCurrent = Object.entries(ev).filter(([k, v]) => v > 0 && k !== "CURRENT" && k !== "ERASED");  // erasure is a governed act, not a gap
  const openAnomalies = (anomalies.data ?? []).filter((a) => !a.disposition_code);
  const proposed = d?.determinations.PROPOSED ?? 0, inReview = d?.determinations.IN_REVIEW ?? 0;
  // Recorded occurrences of adopted obligations with no fulfilment yet, and how many are past due (GET /dashboard).
  const occ = d?.obligation_occurrences as { unfulfilled: number; overdue: number; rule: string } | undefined;
  const issues = d?.open_issues as { total: number; by_severity: Record<string, number> } | undefined;

  const kpis: Kpi[] = [
    { key: "obligations", label: "Obligations due / late", tone: occ?.overdue ? "bad" : "warn", href: "/obligations",
      value: occ?.unfulfilled, failed: !!dash.error, hint: occ ? `${occ.overdue} overdue` : "awaiting fulfilment",
      detail: occ ? `Obligation occurrences awaiting fulfilment: ${occ.unfulfilled} · past due: ${occ.overdue}. ${occ.rule}` : undefined },
    { key: "controls", label: "Controls need action", tone: "warn", href: "/controls",
      value: controls.data ? ctlOff.length : undefined, failed: !!controls.error,
      hint: ctlOff.every((c) => ["FAILING", "NOT_EVALUATED"].includes(c.current_state.state)) ? "failed or untested" : "not operating",
      detail: ctlOff.length ? `${list(tally(ctlOff.map((c) => c.current_state.state)))} (24-hour convenience view)` : undefined },
    { key: "evidence", label: "Evidence gaps", hint: "not current / IPE review", tone: "warn", href: "/evidence",
      value: d && anomalies.data ? notCurrent.reduce((a, [, v]) => a + v, 0) + openAnomalies.length : undefined, failed: !!(dash.error || anomalies.error),
      detail: d && anomalies.data ? `Evidence not CURRENT: ${list(notCurrent) || "none"} · IPE anomalies awaiting disposition: ${openAnomalies.length}` : undefined },
    { key: "issues", label: "Open issues", tone: issues?.by_severity?.CRITICAL || issues?.by_severity?.HIGH ? "bad" : "warn", href: "/governance",
      value: issues?.total, failed: !!dash.error,
      hint: issues ? `${(issues.by_severity.CRITICAL ?? 0) + (issues.by_severity.HIGH ?? 0)} critical or high` : "findings in remediation",
      detail: issues ? `Open issues by severity: ${list(Object.entries(issues.by_severity))}` : undefined },
    { key: "approvals", label: "Approvals pending", hint: "human sign-off", tone: "info", href: "/applicability",
      value: d ? proposed + inReview : undefined, failed: !!dash.error,
      detail: d ? `Applicability determinations awaiting adoption: ${proposed} proposed · ${inReview} in review` : undefined },
    { key: "facts", label: "Fact requests", hint: "facts needed to decide", tone: "info", href: "/fact-requests",
      value: d?.open_fact_requests, failed: !!dash.error,
      detail: d ? `Open fact requests raised by UNCERTAIN applicability evaluations: ${d.open_fact_requests}` : undefined },
  ];

  // What needs someone now, straight from the records above. Each row says what it is and opens where it is worked.
  const attention: Attention[] = [
    ...(occ?.overdue ? [{ key: "occ", level: "high" as const, what: `${plural(occ.overdue, "obligation occurrence")} past due`, detail: "Record fulfilment with evidence", href: "/obligations" }] : []),
    ...ctlOff.filter((c) => c.current_state.state === "FAILING").map((c) => ({ key: c.control_implementation_id, level: "high" as const,
      what: c.definition?.title ?? c.method, detail: `Control failing · ${c.subject}`, href: `/controls/${c.control_implementation_id}` })),
    ...openAnomalies.map((a) => ({ key: a.anomaly_id, level: (["CRITICAL", "HIGH"].includes(a.severity) ? "high" : "medium") as Attention["level"],
      what: `IPE anomaly: ${a.anomaly_class.replace(/_/g, " ").toLowerCase()}`, detail: `${a.severity} · awaiting disposition`, href: "/ipe" })),
    ...(issues?.total ? [{ key: "issues", level: (issues.by_severity.CRITICAL || issues.by_severity.HIGH ? "high" : "medium") as Attention["level"],
      what: `${plural(issues.total, "open issue")}`, detail: list(Object.entries(issues.by_severity).filter(([, v]) => v > 0)), href: "/governance" }] : []),
    ...(proposed + inReview ? [{ key: "det", level: "medium" as const, what: `${plural(proposed + inReview, "applicability determination")} awaiting adoption`,
      detail: "Needs an authorised human to attest", href: "/applicability" }] : []),
    ...(d?.open_fact_requests ? [{ key: "facts", level: "medium" as const, what: `${plural(d.open_fact_requests, "fact request")} open`,
      detail: "Missing facts keep applicability UNCERTAIN", href: "/fact-requests" }] : []),
    ...ctlOff.filter((c) => c.current_state.state === "NOT_EVALUATED").map((c) => ({ key: c.control_implementation_id, level: "medium" as const,
      what: c.definition?.title ?? c.method, detail: `Not yet tested · ${c.subject}`, href: `/controls/${c.control_implementation_id}` })),
    ...notCurrent.map(([k, v]) => ({ key: `ev-${k}`, level: (k === "INVALID" ? "high" : "medium") as Attention["level"],
      what: `${plural(v, "evidence record")} ${k.toLowerCase()}`, detail: "Cannot support current-period proof", href: "/evidence" })),
  ];
  const attentionLoading = (!d && !dash.error) || (!controls.data && !controls.error) || (!anomalies.data && !anomalies.error);

  // The actions this person's roles allow, as shortcuts to the screens that perform them.
  const shortcuts = [
    { perm: "submit_evidence", label: "Upload evidence", href: "/evidence" },
    { perm: "attest", label: "Review determinations", href: "/applicability" },
    { perm: "manage_facts", label: "Provide requested facts", href: "/fact-requests" },
    { perm: "manage_controls", label: "Implement a control", href: "/controls" },
    { perm: "manage_issues", label: "Work open issues", href: "/governance" },
    { perm: "approve_exception", label: "Decide exceptions", href: "/governance" },
    { perm: "package", label: "Build an audit package", href: "/packages" },
    { perm: "manage_ai", label: "Register an AI asset", href: "/ai" },
  ].filter((s) => can(s.perm));

  const totals = (d?.assurance_totals ?? null) as Record<string, number> | null;

  return (
    <div className="pt-1">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <h1 className="text-[26px] font-semibold leading-tight text-slate-800 sm:text-[28px]">{TITLE}</h1>
          <p className="mt-1 text-[14px] text-slate-600">What needs your team&apos;s attention, from your organisation&apos;s own records.</p>
        </div>
        <div className="min-w-0 sm:max-w-[40%] sm:pt-1 sm:text-right">
          <div title={`Organization ${orgName}`} className="truncate text-[15px] font-bold uppercase tracking-[0.04em] text-teal-700">{orgName}</div>
          <div title={readAt ? `Dashboard ${readAt}` : undefined} className="mt-1 text-[13px] text-slate-600">
            {d ? `As of ${utcTime(d.as_of.valid_at)} UTC` : dash.error ? "Records unavailable" : "Reading records…"}
            {jurisdictions.length ? ` · ${jurisdictions.join(", ")}` : ""}
            {" · "}<Link href="/entities" className="font-semibold text-teal-700 hover:underline">Entities</Link>
          </div>
        </div>
      </div>

      <ErrorBox error={dash.error ?? controls.error ?? anomalies.error} />
      {(dash.error || controls.error || anomalies.error) && (
        <div className="-mt-1 mb-3"><button className="btn-ghost" onClick={() => { dash.reload(); controls.reload(); anomalies.reload(); }}>Retry</button></div>
      )}

      <ul aria-label="Key figures" className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {kpis.map((k) => <li key={k.key} className="min-w-0"><KpiCard k={k} /></li>)}
      </ul>

      <div className="grid grid-cols-1 gap-x-4 gap-y-6 xl:grid-cols-3">
        <section aria-labelledby="attention-h" className={`${PANEL} flex flex-col px-5 pb-4 pt-5 sm:px-6 xl:col-span-2`}>
          <h2 id="attention-h" className="text-[18px] font-semibold leading-7 text-slate-900">Needs attention</h2>
          {attentionLoading ? <LoadingSkeleton rows={4} className="px-0" />
            : attention.length === 0 ? <EmptyState compact title="Nothing needs attention right now" description="No failing or untested controls, open issues, pending determinations, fact requests or evidence gaps are recorded." />
            : (
              <ul className="mt-2 divide-y divide-slate-100">
                {attention.slice(0, 8).map((r) => (
                  <li key={r.key}>
                    <Link href={r.href} className="group flex items-center gap-3 rounded py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
                      <span className={`inline-flex w-14 shrink-0 justify-center rounded px-1.5 text-[11px] font-semibold leading-[18px] ${r.level === "high" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-800"}`}>
                        {r.level === "high" ? "High" : "Medium"}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[14px] font-semibold text-slate-900 group-hover:text-teal-800">{r.what}</span>
                        <span className="block truncate text-[12px] text-slate-500">{r.detail}</span>
                      </span>
                      <ChevronRight size={16} aria-hidden className="shrink-0 text-slate-400 group-hover:text-teal-700" />
                    </Link>
                  </li>
                ))}
                {attention.length > 8 && <li className="pt-3 text-[12px] text-slate-500">and {attention.length - 8} more — open each area from the figures above.</li>}
              </ul>
            )}
        </section>

        <div className="grid content-start gap-6 md:grid-cols-2 xl:flex xl:flex-col">
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
                <div className="pt-1"><Link href="/assurance" className="text-[13px] font-semibold text-teal-700 hover:underline">Open assurance reports</Link></div>
              </dl>
            ) : dash.error ? <p className="mt-3 text-[13px] text-slate-500">Unavailable</p> : <LoadingSkeleton rows={3} className="px-0" />}
          </section>

          <section aria-labelledby="actions-h" className={`${PANEL} px-6 pb-3 pt-5 xl:flex-1`}>
            <h2 id="actions-h" className="text-[18px] font-semibold leading-6 text-slate-900">Your actions</h2>
            {shortcuts.length ? (
              <ul className="mt-1 divide-y divide-slate-100">
                {shortcuts.map((q) => (
                  <li key={q.label}>
                    <Link href={q.href}
                      className="group flex items-center justify-between gap-3 rounded py-3 text-[14px] text-slate-700 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
                      {q.label}<ChevronRight size={16} aria-hidden className="shrink-0 text-slate-400 group-hover:text-teal-700" />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-2 pb-3 text-[13px] text-slate-500">Your role can view records but has no actions to take here.</p>}
          </section>
        </div>
      </div>

      {(readAt || d?.language_note) && (
        <p className="mt-6 max-w-4xl text-[12px] leading-5 text-slate-500">
          Figures are your organisation&apos;s records{readAt ? ` (${readAt})` : ""}. {d?.language_note}
        </p>
      )}
    </div>
  );
}

function KpiCard({ k }: { k: Kpi }) {
  const loading = k.value === undefined && !k.failed;
  return (
    <Link href={k.href} title={k.detail}
      className="flex h-full min-h-[108px] flex-col rounded-xl border border-slate-200 bg-white px-4 pb-2 pt-4 shadow-[0_1px_3px_rgba(15,23,42,0.06)] transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-[0_4px_12px_rgba(15,23,42,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
      <span className="text-[14px] font-semibold leading-[19px] text-slate-800">{k.label}</span>
      <span className="mt-1 flex items-center justify-between gap-2">
        {loading ? <span role="status" aria-label="Loading" className="skeleton my-1 h-6 w-10" />
          : <span className={`text-[28px] font-bold leading-8 tabular-nums ${k.value ? VALUE_TONE[k.tone] : "text-slate-900"}`}>{k.value === undefined ? "—" : k.value}</span>}
      </span>
      <span className="mt-1 text-[13px] leading-[17px] text-slate-500">{k.failed ? "Unavailable" : k.hint}</span>
      {k.detail && <span className="sr-only">. {k.detail}</span>}
    </Link>
  );
}

function ContentOpsHome({ name }: { name: string }): ReactNode {
  return (
    <>
      <PageHeader kicker="Zoiko content operations" title={`Welcome, ${name.replace(/\s*\(.*\)/, "")}`}
        subtitle="Shared regulatory content and rules that customer organisations receive." />
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
