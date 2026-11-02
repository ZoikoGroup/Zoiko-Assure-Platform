"use client";

import { useMemo, useState } from "react";
import { ArrowRight, Braces, Building2, FlaskConical, Inbox, Library } from "lucide-react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { personaOf, useAuth, type Me } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { rolesText } from "@/lib/roles";
import { SuperAdminDashboard } from "@/components/PlatformAdmin";
import {
  A, Badge, Card, EmptyState, EntitySelector, ErrorBox, LoadingSkeleton, MetricCard, Notice, PageHeader, Table, toneIcon,
} from "@/components/ui";

/* Every number on this page comes from the backend (dashboard projection, obligations, controls, IPE, catalogue).
   Nothing here derives applicability or assurance state in the browser, and nothing is a compliance score. */
const STATES = ["PROVEN", "NOT_PROVEN", "FAILING", "REQUIRES_REVIEW"] as const;
const BAR: Record<string, string> = { PROVEN: "bg-emerald-500", NOT_PROVEN: "bg-amber-400", FAILING: "bg-red-500", REQUIRES_REVIEW: "bg-violet-500", NO_SNAPSHOT: "bg-slate-300" };

export default function Dashboard() {
  const { me } = useAuth();
  const persona = personaOf(me);
  const tenant = persona === "tenant";
  const dash = useApi<any>(tenant ? "/dashboard" : null);
  const obls = useApi<any[]>(tenant ? "/obligations" : null);
  const sources = useApi<any[]>(tenant ? "/catalog/sources" : null);
  const org = useApi<any>(tenant ? "/org/tenant" : null);
  const controls = useApi<any[]>(tenant ? "/controls" : null);
  const anomalies = useApi<any[]>(tenant ? "/ipe/anomalies" : null);
  const [entity, setEntity] = useState("");

  // Two dashboards: the platform Super Admin's, and the Organisation Dashboard below. Content staff land on their workspace.
  if (me && persona === "platform") return <SuperAdminDashboard />;
  if (me && !tenant) return <ContentOpsHome name={me.display_name} />;

  const d = dash.data;
  const scoped = d && entity ? d.by_entity.find((e: any) => e.entity_id === entity) : null;
  const counts: Record<string, number> = scoped ? scoped.assurance_counts : d?.assurance_totals ?? {};
  const denominator = d ? (scoped ? scoped.denominator : d.by_entity.reduce((a: number, e: any) => a + e.denominator, 0)) : 0;
  const uncertain = d ? (scoped ? scoped.uncertain_evaluations : d.by_entity.reduce((a: number, e: any) => a + e.uncertain_evaluations, 0)) : 0;
  // When this projection was read, and the home-cell record watermark it reflects. Snapshot states keep their own period and cutoff.
  const readAt = d ? `Read ${fmtDate(d.as_of.valid_at, true)} · known as of ${d.as_of.cell_id}#${d.as_of.record_seq}` : "";
  const scopeNote = entity ? "All entities (not available per entity)" : undefined;

  return (
    <>
      <PageHeader title="Organisation Dashboard"
        subtitle="What needs attention, and the evidential assurance position of your organisation against named requirement versions and periods." />
      <OrgSummary me={me} org={org.data} />
      <ErrorBox error={dash.error} />
      {dash.loading && !d && <div className="card"><LoadingSkeleton rows={6} /></div>}
      {d && (
        <div className="space-y-4">
          <NeedsAttention d={d} controls={controls.data} anomalies={anomalies.data} readAt={readAt} />

          <div className="flex flex-wrap items-end justify-between gap-3 pt-2">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-slate-900">Assurance position</h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Each adopted obligation&apos;s most recently frozen period snapshot, {scoped ? <>for <b className="font-medium text-slate-700">{scoped.legal_name}</b></> : "across all legal entities"}. Snapshots can cover different periods and record cutoffs, so read each state with the period beside it. Evidential states, not a compliance score.
              </p>
            </div>
            <EntitySelector entities={d.by_entity} value={entity} onChange={setEntity} className="w-full sm:w-60" />
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <MetricCard label="Adopted obligations" value={denominator} href="/obligations" tone="info"
              hint={<>{counts.NO_SNAPSHOT ?? 0} not yet assessed{uncertain ? <> · {uncertain} UNCERTAIN applicability reported separately</> : null}</>} />
            <MetricCard label="Proven" value={counts.PROVEN ?? 0} code="PROVEN" href="/assurance" hint={`of ${denominator} · latest snapshot`} />
            <MetricCard label="Not proven" value={counts.NOT_PROVEN ?? 0} code="NOT_PROVEN" href="/assurance" hint={`of ${denominator} · latest snapshot`} />
            <MetricCard label="Failing" value={counts.FAILING ?? 0} code="FAILING" href="/assurance" hint={`of ${denominator} · latest snapshot`} />
            <MetricCard label="Requires review" value={counts.REQUIRES_REVIEW ?? 0} code="REQUIRES_REVIEW" href="/assurance" hint={`of ${denominator} · latest snapshot`} />
            <MetricCard label="Current evidence" value={d.evidence_conditions.CURRENT ?? 0} code="CURRENT" href="/evidence"
              hint={entity ? scopeNote : `${d.evidence_conditions.STALE ?? 0} stale · ledger head #${d.ledger.head_seq}`} />
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <AssuranceByState className="xl:col-span-2" counts={counts} denominator={denominator} rows={obls.data} error={obls.error} entity={entity} readAt={readAt} />
            <ApplicabilitySummary d={d} scopeNote={scopeNote} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <EvidenceFreshness d={d} scopeNote={scopeNote} />
            <RecentChanges rows={sources.data} error={sources.error} />
            <RecordChain d={d} sources={sources.data} controls={controls.data} />
          </div>

          <p className="text-[11px] text-slate-500">{d.denominator_rule} {d.language_note}</p>
        </div>
      )}
    </>
  );
}

function AssuranceByState({ counts, denominator, rows, error, entity, readAt, className = "" }:
  { counts: Record<string, number>; denominator: number; rows: any[] | null; error: unknown; entity: string; readAt: string; className?: string }) {
  const list = useMemo(() => (rows ?? []).filter((o) => o.lifecycle === "ADOPTED" && (!entity || o.entity_id === entity)), [rows, entity]);
  const segments = [...STATES, "NO_SNAPSHOT"].map((k) => ({ k, n: counts[k] ?? 0 })).filter((s) => s.n > 0);
  return (
    <Card className={className} title="Assurance by state" subtitle="Most recently frozen period snapshot per adopted obligation. States are computed by the backend roll-up and never edited here."
      actions={<A href="/assurance">View all</A>} bodyClassName=""
      footer={`${readAt}. States are not re-evaluated at that time: each comes from the snapshot in its row, with that snapshot's own period, perspective and record cutoff.`}>
      <div className="space-y-2 px-4 pt-4">
        {denominator > 0 ? (
          <>
            <div className="flex h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
              {segments.map((s) => <div key={s.k} className={BAR[s.k]} style={{ width: `${(s.n / denominator) * 100}%` }} />)}
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1.5 pb-1" aria-label="Assurance state counts">
              {[...STATES].map((k) => <li key={k} className="flex items-center gap-1.5"><Badge code={k} /><span className="text-[13px] tabular-nums text-slate-700">{counts[k] ?? 0}</span></li>)}
              <li className="flex items-center gap-1.5"><Badge code="NOT_EVALUATED" label="NOT ASSESSED" /><span className="text-[13px] tabular-nums text-slate-700">{counts.NO_SNAPSHOT ?? 0}</span></li>
            </ul>
          </>
        ) : <p className="text-[13px] text-slate-500">No adopted obligations in scope.</p>}
      </div>
      <ErrorBox error={error} />
      <div className="mt-2">
        <Table head={["Obligation", "Subject", "State", "Period (end exclusive) · perspective", "Record cutoff", "Exceptions"]} empty={!!rows && list.length === 0}>
          {list.map((o) => {
            const s = o.latest_snapshot;
            return (
              <tr key={o.lineage_id}>
                <td className="td"><Link href={`/obligations/${o.lineage_id}`} className="font-medium text-slate-900 hover:text-navy-700">{o.definition.title}</Link><div className="mono text-[11px] text-slate-500">{o.definition.key}</div></td>
                <td className="td text-slate-600">{o.subject.display_name}</td>
                <td className="td">{s ? <Badge code={s.state} /> : <span className="text-xs text-slate-500">Not assessed</span>}</td>
                <td className="td text-xs text-slate-600">{s ? <><span className="whitespace-nowrap">{fmtDate(s.period_from)} → {fmtDate(s.period_to)}</span><div className="mono text-[11px] text-slate-500">{s.perspective}</div></> : "—"}</td>
                <td className="td text-xs text-slate-600">{s ? <><span className="mono">{s.record_cutoff_seq != null ? `#${s.record_cutoff_seq}` : "Not recorded"}</span><div className="whitespace-nowrap text-[11px] text-slate-500">frozen {fmtDate(s.created_at, true)}</div></> : "—"}</td>
                <td className="td">{o.open_exceptions ? <Badge tone="exception" label={`${o.open_exceptions} APPROVED`} title="Approved exception(s): shown beside the state, never changing it" /> : <span className="text-xs text-slate-500">None</span>}</td>
              </tr>
            );
          })}
        </Table>
        {!rows && !error && <LoadingSkeleton rows={3} />}
      </div>
    </Card>
  );
}

function ApplicabilitySummary({ d, scopeNote }: { d: any; scopeNote?: string }) {
  const out = d.applicability_outcomes as Record<string, number>;
  const total = (out.APPLIES ?? 0) + (out.DOES_NOT_APPLY ?? 0) + (out.UNCERTAIN ?? 0);
  return (
    <Card title="Applicability summary" subtitle="Latest evaluation per subject and obligation" actions={<A href="/applicability">Review</A>} footer={scopeNote}>
      <ul className="space-y-2.5">
        {["APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"].map((k) => (
          <li key={k}>
            <div className="flex items-center justify-between gap-2"><Badge code={k} /><span className="text-[13px] font-medium tabular-nums text-slate-900">{out[k] ?? 0}</span></div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden>
              <div className="h-full bg-navy-400" style={{ width: total ? `${((out[k] ?? 0) / total) * 100}%` : "0%" }} />
            </div>
          </li>
        ))}
      </ul>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 pt-3 text-[13px]">
        {[["Proposed", d.determinations.PROPOSED], ["In review", d.determinations.IN_REVIEW], ["Adopted", d.determinations.ADOPTED], ["Needs review", d.determinations.needs_review]].map(([k, v]) => (
          <div key={k as string} className="flex justify-between"><dt className="text-slate-500">{k}</dt><dd className="font-medium tabular-nums text-slate-900">{v ?? 0}</dd></div>
        ))}
      </dl>
      <p className="mt-3 text-[11px] text-slate-500">An APPLIES evaluation is not an adoption; adoption needs a named human attestation.</p>
    </Card>
  );
}

function EvidenceFreshness({ d, scopeNote }: { d: any; scopeNote?: string }) {
  const conds = Object.entries(d.evidence_conditions as Record<string, number>).sort((a, b) => b[1] - a[1]);
  return (
    <Card title="Evidence freshness" subtitle="Each artifact's condition when this dashboard was read, not within any snapshot period" actions={<A href="/evidence">Evidence</A>}
      footer={scopeNote ?? `Ledger head #${d.ledger.head_seq} · last checkpoint #${d.ledger.last_checkpointed_seq}`}>
      {conds.length === 0 ? <EmptyState title="No evidence captured yet" compact /> : (
        <ul className="divide-y divide-slate-100">
          {conds.map(([k, v]) => <li key={k} className="flex items-center justify-between py-1.5"><Badge code={k} /><span className="text-[13px] font-medium tabular-nums text-slate-900">{v}</span></li>)}
        </ul>
      )}
    </Card>
  );
}

function RecentChanges({ rows, error }: { rows: any[] | null; error: unknown }) {
  const recent = useMemo(() => [...(rows ?? [])].sort((a, b) => String(b.fetched_at).localeCompare(String(a.fetched_at))).slice(0, 5), [rows]);
  return (
    <Card title="Recent regulatory changes" subtitle="Latest ingested authoritative sources" actions={<A href="/regulatory">Sources</A>}>
      <ErrorBox error={error} />
      {!rows && !error ? <LoadingSkeleton rows={3} className="p-0" /> : recent.length === 0 ? <EmptyState title="No sources ingested" compact /> : (
        <ul className="divide-y divide-slate-100">
          {recent.map((s) => (
            <li key={s.source_id} className="py-2">
              <div className="flex items-start justify-between gap-2">
                <span className="text-[13px] font-medium leading-5 text-slate-900">{s.title}</span>
                <Badge code={s.lifecycle} />
              </div>
              <div className="mt-0.5 text-[11px] text-slate-500">{s.authority} · {s.jurisdiction} · effective {fmtDate(s.effective_date)}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Which organisation this workspace is for, kept apart from who is signed in. */
function OrgSummary({ me, org }: { me: Me | null; org: any }) {
  const entities: any[] = org?.entities ?? [];
  const cells = [...new Set(entities.map((e) => e.home_cell_id))];
  return (
    <section aria-label="Organisation" className="card mb-4 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-navy-50 text-navy-700"><Building2 size={18} aria-hidden /></span>
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Organisation</div>
          <div className="text-[15px] font-semibold leading-6 text-slate-900">{org?.tenant.legal_name ?? me?.tenant_name}</div>
          {org && (
            <div className="text-xs text-slate-500">
              {entities.length} legal {entities.length === 1 ? "entity" : "entities"}: {entities.map((e) => `${e.legal_name} (${e.country})`).join(", ")}
              {cells.length > 0 && <> · data held in <span className="mono">{cells.join(", ")}</span></>}
            </div>
          )}
        </div>
      </div>
      <div className="min-w-0 border-t border-slate-100 pt-3 sm:border-l sm:border-t-0 sm:pl-4 sm:pt-0">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Signed in as</div>
        <div className="text-[13px] font-medium text-slate-900">{me?.display_name.replace(/\s*\(.*\)/, "")}</div>
        <div className="text-xs text-slate-500">{rolesText(me)} · <A href="/admin/users">Your access</A></div>
      </div>
    </section>
  );
}

type Attention = { key: string; n: number; code: string; label: string; why: string; href: string; next: string };

/** Work that blocks or qualifies assurance, most severe first. Organisation-wide; only non-zero items are listed. */
function NeedsAttention({ d, controls, anomalies, readAt }: { d: any; controls: any[] | null; anomalies: any[] | null; readAt: string }) {
  const t = d.assurance_totals as Record<string, number>;
  const sev = d.open_issues.by_severity as Record<string, number>;
  const ev = d.evidence_conditions as Record<string, number>;
  const notCurrent = Object.entries(ev).filter(([k, v]) => v > 0 && k !== "CURRENT" && k !== "ERASED");  // erasure is a governed act, not a gap
  const ctlOff = (controls ?? []).filter((c) => c.current_state.state !== "OPERATING");
  const ctlByState = Object.entries(ctlOff.reduce((m: Record<string, number>, c) => ({ ...m, [c.current_state.state]: (m[c.current_state.state] ?? 0) + 1 }), {}));
  const openIpe = (anomalies ?? []).filter((a) => !a.disposition_code).length;
  const uncertain = d.by_entity.reduce((a: number, e: any) => a + e.uncertain_evaluations, 0);
  const list = (pairs: [string, number][]) => pairs.map(([k, v]) => `${v} ${k}`).join(" · ");
  const items: Attention[] = [
    { key: "failing", n: t.FAILING ?? 0, code: "FAILING", label: "Obligations FAILING", why: "A control or required occurrence failed in the obligation's most recently frozen period snapshot.", href: "/assurance", next: "Assurance Periods" },
    { key: "review", n: t.REQUIRES_REVIEW ?? 0, code: "REQUIRES_REVIEW", label: "Obligations REQUIRES_REVIEW", why: "Conflicting or unresolved inputs need a human decision.", href: "/assurance", next: "Assurance Periods" },
    { key: "notproven", n: t.NOT_PROVEN ?? 0, code: "NOT_PROVEN", label: "Obligations NOT_PROVEN", why: "Evidence is missing, stale or IPE-unverified for part of the period.", href: "/assurance", next: "Assurance Periods" },
    { key: "unassessed", n: t.NO_SNAPSHOT ?? 0, code: "NOT_EVALUATED", label: "Adopted obligations not yet assessed", why: "No period snapshot has been frozen. Open the dossier and assess a period.", href: "/obligations", next: "Obligation Dossiers" },
    { key: "adopt", n: (d.determinations.PROPOSED ?? 0) + (d.determinations.IN_REVIEW ?? 0), code: "PROPOSED", label: "Applicability determinations awaiting adoption", why: "An APPLIES result becomes an obligation only after a named person attests it.", href: "/applicability", next: "Applicability Review" },
    { key: "rereview", n: d.determinations.needs_review ?? 0, code: "REQUIRES_REVIEW", label: "Adopted determinations flagged for re-review", why: "A newer evaluation disagrees with the adopted outcome.", href: "/applicability", next: "Applicability Review" },
    { key: "uncertain", n: uncertain, code: "UNCERTAIN", label: "UNCERTAIN applicability evaluations", why: "Missing facts prevent a definitive outcome. Reported separately, never excluded.", href: "/applicability", next: "Applicability Review" },
    { key: "facts", n: d.open_fact_requests, code: "OPEN", label: "Open fact requests", why: "Facts requested from their owners to resolve UNCERTAIN outcomes.", href: "/fact-requests", next: "Fact Requests" },
    { key: "controls", n: ctlOff.length, code: ctlOff.some((c) => c.current_state.state === "FAILING") ? "FAILING" : "NOT_EVALUATED", label: "Controls not OPERATING", why: `${list(ctlByState as [string, number][])} (24-hour convenience view; open a control for its recorded evaluations).`, href: "/controls", next: "Controls & Tests" },
    { key: "evidence", n: notCurrent.reduce((a, [, v]) => a + v, 0), code: ev.INVALID ? "INVALID" : "STALE", label: "Evidence not CURRENT", why: `${list(notCurrent)}. Stale or invalid evidence cannot support current-period proof.`, href: "/evidence", next: "Evidence Records" },
    { key: "ipe", n: openIpe, code: "IPE_INCONCLUSIVE", label: "IPE anomalies awaiting disposition", why: "A population behind a test is not yet established as complete and accurate.", href: "/ipe", next: "IPE" },
    { key: "issues", n: d.open_issues.total, code: (sev.CRITICAL || sev.HIGH) ? "FAIL" : "OPEN", label: "Open issues", why: list(Object.entries(sev).filter(([, v]) => v).map(([k, v]) => [k.toLowerCase(), v])), href: "/governance", next: "Exceptions & Issues" },
  ];
  const open = items.filter((i) => i.n > 0);
  return (
    <Card title="Needs attention" subtitle="Work that blocks or qualifies assurance, across all legal entities, most severe first." bodyClassName=""
      footer={`${readAt}. Obligation counts use each obligation's most recently frozen snapshot. An empty list is not a statement of compliance.`}>
      {open.length === 0 ? <p className="px-4 py-4 text-[13px] text-slate-600">Nothing is flagged in the latest recorded data.</p> : (
        <ul className="grid divide-y divide-slate-100 lg:grid-cols-2 lg:divide-y-0">
          {open.map((i) => {
            const { Icon, iconCls } = toneIcon(i.code);
            return (
              <li key={i.key} className="lg:border-b lg:border-slate-100 lg:odd:border-r">
                <Link href={i.href} className="group flex items-start gap-3 px-4 py-2.5 hover:bg-slate-50/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400">
                  <Icon size={16} aria-hidden className={`mt-0.5 shrink-0 ${iconCls}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span className="text-[15px] font-semibold tabular-nums text-slate-900">{i.n}</span>
                      <span className="text-[13px] font-medium text-slate-900 group-hover:text-navy-700">{i.label}</span>
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-500">{i.why}</span>
                  </span>
                  <span className="hidden shrink-0 items-center gap-1 pt-0.5 text-xs text-navy-700 sm:flex">{i.next}<ArrowRight size={13} aria-hidden /></span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** How the records on this platform connect, with this organisation's own counts at each step. */
function RecordChain({ d, sources, controls }: { d: any; sources: any[] | null; controls: any[] | null }) {
  const t = d.assurance_totals as Record<string, number>;
  const adopted = Object.values(t).reduce((a, v) => a + v, 0);
  const out = d.applicability_outcomes as Record<string, number>;
  const evidence = Object.values(d.evidence_conditions as Record<string, number>).reduce((a, v) => a + v, 0);
  const n = (v: number | undefined) => (v === undefined ? "…" : v);
  const steps = [
    { label: "Requirements", value: `${n(sources?.length)} regulatory source(s)`, about: "Encoded from authoritative regulatory text.", href: "/sources" },
    { label: "Applicability", value: `${out.APPLIES ?? 0} APPLIES · ${out.UNCERTAIN ?? 0} UNCERTAIN`, about: "Evaluates whether each requirement applies to each subject.", href: "/applicability" },
    { label: "Obligations", value: `${adopted} adopted`, about: "Determinations a named person has attested.", href: "/obligations" },
    { label: "Controls", value: `${n(controls?.length)} implemented`, about: "Shared controls that satisfy obligations.", href: "/controls" },
    { label: "Evidence", value: `${evidence} artifact(s)`, about: "Test results and records behind control evaluations, IPE-checked where required.", href: "/evidence" },
    { label: "Assurance", value: `${adopted - (t.NO_SNAPSHOT ?? 0)} of ${adopted} assessed`, about: "Period states computed from everything above.", href: "/assurance" },
  ];
  return (
    <Card title="How your records connect" subtitle="Each step builds on the one before it">
      <ol className="space-y-0">
        {steps.map((s, i) => (
          <li key={s.label} className="relative flex gap-3 pb-3 last:pb-0">
            {i < steps.length - 1 && <span aria-hidden className="absolute left-[9px] top-5 h-[calc(100%-12px)] w-px bg-slate-200" />}
            <span aria-hidden className="mt-0.5 flex h-[19px] w-[19px] shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white text-[11px] font-semibold text-slate-500">{i + 1}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <Link href={s.href} className="text-[13px] font-medium text-slate-900 hover:text-navy-700">{s.label}</Link>
                <span className="text-xs tabular-nums text-slate-600">{s.value}</span>
              </div>
              <p className="text-[11px] leading-4 text-slate-500">{s.about}</p>
            </div>
          </li>
        ))}
      </ol>
    </Card>
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
