"use client";

import Link from "next/link";
import {
  Activity, AlertTriangle, Archive, ArrowRight, Building2, CheckCircle2, ClipboardCheck, Clock, FileClock, Plug,
} from "lucide-react";
import { useApi } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { A, Badge, ErrorBox, Notice } from "@/components/ui";
import { delay, useCountUp } from "@/lib/motion";
import { BarChart, CHART, Donut, LineChart } from "@/components/charts";

const MONTH = (m: string) => new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });
const SPLIT: Record<string, string> = { PROVEN: "bg-navy-600", NOT_PROVEN: "bg-brand-400", FAILING: "bg-red-500", REQUIRES_REVIEW: "bg-violet-500" };

function CountUp({ value, delayMs = 0 }: { value: number; delayMs?: number }) {
  return <>{useCountUp(value, 1100, delayMs)}</>;
}

function StatCard({ label, value, icon: Icon, tone = "navy", foot, href, i = 0 }: { label: string; value: React.ReactNode; icon: React.ElementType; tone?: "navy" | "orange"; foot: React.ReactNode; href?: string; i?: number }) {
  const tile = tone === "orange" ? "from-brand-400 to-brand-600 shadow-brand-600/30" : "from-navy-600 to-navy-900 shadow-navy-900/30";
  const body = (
    <div className="za-fade-up card group h-full p-4 transition duration-300 hover:-translate-y-1 hover:shadow-xl" style={delay(i * 110)}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-sm text-slate-brand">{label}</div>
          <div className="mt-1 text-3xl font-bold tabular-nums tracking-tight text-navy-900">{typeof value === "number" ? <CountUp value={value} delayMs={i * 110 + 150} /> : value}</div>
        </div>
        <span className={`za-shine flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br text-white shadow-lg transition-transform duration-300 group-hover:rotate-6 group-hover:scale-110 ${tile}`} style={delay(1200 + i * 400)}>
          <span className="za-pop inline-flex" style={delay(250 + i * 110)}><Icon size={22} /></span>
        </span>
      </div>
      <div className="mt-4 border-t border-navy-50 pt-3 text-sm text-slate-600">{foot}</div>
    </div>
  );
  return href ? <Link href={href} className="block">{body}</Link> : body;
}

function ChartCard({ title, subtitle, children, foot, i = 0 }: { title: string; subtitle: string; children: React.ReactNode; foot: string; i?: number }) {
  return (
    <section className="za-fade-up card flex flex-col p-5 transition duration-300 hover:shadow-xl" style={delay(400 + i * 130)}>
      <h2 className="text-base font-bold text-navy-900">{title}</h2>
      <p className="mb-4 text-sm text-slate-brand">{subtitle}</p>
      <div className="flex-1">{children}</div>
      <div className="mt-4 flex items-center gap-1.5 border-t border-navy-50 pt-3 text-xs text-slate-500"><Clock size={13} />{foot}</div>
    </section>
  );
}

export default function Dashboard() {
  const { me } = useAuth();
  const { data: d, error, loading } = useApi<any>(me?.tenant_id ? "/dashboard" : null);

  if (me && !me.tenant_id) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold text-navy-900">Welcome, {me.display_name.replace(/\s*\(.*\)/, "")}</h1>
        <Notice>Zoiko content-operations staff have no standing access to customer data (INV-14). Your workspace is regulatory content and rule authoring.</Notice>
        <div className="grid gap-4 sm:grid-cols-2">
          <StatCard label="Regulatory inbox" value="Sources" icon={FileClock} foot={<span className="flex items-center gap-1">Ingest, extract and verify <ArrowRight size={14} /></span>} href="/regulatory" />
          <StatCard label="Rule authoring" value="Drafts" icon={ClipboardCheck} tone="orange" foot={<span className="flex items-center gap-1">Validate, review and release <ArrowRight size={14} /></span>} href="/rules" />
        </div>
      </div>
    );
  }

  const t = d?.assurance_totals;
  const denominator = d ? d.by_entity.reduce((a: number, e: any) => a + e.denominator, 0) : 0;
  const uncertain = d ? d.by_entity.reduce((a: number, e: any) => a + e.uncertain_evaluations, 0) : 0;
  const gaps = t ? t.NOT_PROVEN + t.FAILING + t.REQUIRES_REVIEW : 0;
  const asOf = d ? `as of ${fmtDate(d.as_of.valid_at, true)} · seq ${d.as_of.record_seq}` : "";

  return (
    <>
      <div className="za-fade-up mb-6">
        <h1 className="text-3xl font-bold tracking-tight text-navy-900">Dashboard</h1>
        <p className="mt-1 text-slate-brand">Evidential assurance across your legal entities, against named requirement versions and periods.</p>
      </div>
      <ErrorBox error={error} />
      {loading && !d && <DashboardSkeleton />}
      {d && (
        <div className="space-y-6">
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard i={0} label="Adopted obligations" value={denominator} icon={ClipboardCheck} href="/obligations"
              foot={uncertain ? <><b className="text-brand-600">{uncertain}</b> uncertain applicability, reported separately</> : "No uncertain applicability"} />
            <StatCard i={1} label="Proven (latest period)" value={t.PROVEN} icon={CheckCircle2} tone="orange" href="/obligations"
              foot={<><b className="text-navy-700">{t.PROVEN}</b> of {denominator} adopted · {t.NO_SNAPSHOT} not yet assessed</>} />
            <StatCard i={2} label="Gaps & failures" value={gaps} icon={AlertTriangle} href="/governance"
              foot={<><b className="text-brand-600">{t.FAILING}</b> failing · {t.NOT_PROVEN} not proven · {d.open_issues.total} open issue(s)</>} />
            <StatCard i={3} label="Current evidence" value={d.evidence_conditions.CURRENT ?? 0} icon={Archive} tone="orange" href="/evidence"
              foot={<><b className="text-brand-600">{d.evidence_conditions.STALE ?? 0}</b> stale · ledger head #{d.ledger.head_seq}</>} />
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <ChartCard i={0} title="Evidence coverage" subtitle="Artifacts by coverage-start month" foot={asOf}>
              <BarChart label="Evidence artifacts by coverage month" data={d.evidence_by_coverage_month.map((x: any) => ({ x: MONTH(x.month), y: x.count }))} />
            </ChartCard>
            <ChartCard i={1} title="Control test results" subtitle="Recorded test runs per month (inputs, not control state)" foot={asOf}>
              <LineChart label="Control test runs by month" labels={d.test_runs_by_month.map((x: any) => MONTH(x.month))}
                series={[{ name: "Pass", color: CHART.NAVY, values: d.test_runs_by_month.map((x: any) => x.PASS) },
                  { name: "Fail / error", color: CHART.ORANGE, values: d.test_runs_by_month.map((x: any) => x.FAIL) }]} />
            </ChartCard>
            <ChartCard i={2} title="Applicability" subtitle="Latest evaluation per subject and obligation" foot={`${d.determinations.PROPOSED} proposed · ${d.determinations.ADOPTED} adopted`}>
              <Donut label="Applicability outcomes"
                parts={[{ name: "Applies", value: d.applicability_outcomes.APPLIES, color: CHART.NAVY },
                  { name: "Does not apply", value: d.applicability_outcomes.DOES_NOT_APPLY, color: "#aecadc" },
                  { name: "Uncertain", value: d.applicability_outcomes.UNCERTAIN, color: CHART.ORANGE }]}
                center={<><span className="text-2xl font-bold text-navy-900">{Object.values(d.applicability_outcomes as Record<string, number>).reduce((a, b) => a + b, 0)}</span><span className="text-[10px] uppercase tracking-wide text-slate-500">evaluations</span></>} />
            </ChartCard>
          </div>

          <div className="grid gap-5 xl:grid-cols-3">
            <section className="za-fade-up card p-5 xl:col-span-2" style={delay(800)}>
              <div className="mb-4 flex items-start justify-between">
                <div>
                  <h2 className="text-base font-bold text-navy-900">Obligations</h2>
                  <p className="text-sm text-slate-brand"><b className="text-navy-700">{d.obligations.length}</b> instances · bars show how the latest assessed period splits by state</p>
                </div>
                <A href="/obligations">View all</A>
              </div>
              <div className="scroll-thin overflow-x-auto">
                <table className="min-w-full">
                  <thead><tr><th className="th">Obligation</th><th className="th">Subject</th><th className="th">State</th><th className="th w-[30%]">Period split</th></tr></thead>
                  <tbody className="divide-y divide-navy-50">
                    {d.obligations.map((o: any, oi: number) => (
                      <tr key={o.lineage_id} className="za-slide-in transition-colors hover:bg-navy-50/50" style={delay(950 + oi * 110)}>
                        <td className="td"><Link href={`/obligations/${o.lineage_id}`} className="font-semibold text-navy-900 hover:text-brand-600">{o.title}</Link><div className="mono text-[11px] text-slate-500">{o.key}</div></td>
                        <td className="td text-sm text-slate-700">{o.subject}</td>
                        <td className="td">{o.state ? <Badge code={o.state} /> : <span className="text-xs text-slate-400">Not assessed</span>}</td>
                        <td className="td">
                          {o.period_split_pct ? (
                            <>
                              <div className="flex h-2 overflow-hidden rounded-full bg-navy-50">
                                <div className="za-grow-x flex h-full w-full" style={delay(1100 + oi * 110)}>
                                  {Object.entries(o.period_split_pct as Record<string, number>).map(([k, v]) => <div key={k} className={SPLIT[k]} style={{ width: `${v}%` }} title={`${k} ${v}%`} />)}
                                </div>
                              </div>
                              <div className="mt-1 text-[11px] text-slate-500">
                                {Object.entries(o.period_split_pct as Record<string, number>).map(([k, v]) => `${k.replace("_", " ").toLowerCase()} ${Math.round(v)}%`).join(" · ")} · {fmtDate(o.period[0])} → {fmtDate(o.period[1])}
                              </div>
                            </>
                          ) : <span className="text-xs text-slate-400">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="za-fade-up card p-5" style={delay(900)}>
              <h2 className="text-base font-bold text-navy-900">Recent activity</h2>
              <p className="mb-4 text-sm text-slate-brand">Latest domain events (transactional outbox)</p>
              <ol className="relative space-y-4 border-l-2 border-navy-50 pl-5">
                {d.recent_events.map((e: any, ei: number) => (
                  <li key={e.record_seq + e.event_type} className="za-slide-in relative" style={delay(1050 + ei * 90)}>
                    <span className={`absolute -left-[27px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full ring-4 ring-white ${ei === 0 ? "za-pulse" : ""} ${e.event_type.includes("gap") || e.event_type.includes("fail") ? "bg-brand-500" : "bg-navy-600"}`} />
                    <div className="mono text-[12px] font-semibold text-navy-900">{e.event_type}</div>
                    <div className="text-[11px] text-slate-500">{e.aggregate_type} · seq {e.record_seq} · {fmtDate(e.at, true)}</div>
                  </li>
                ))}
              </ol>
              <Link href="/admin" className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-navy-700 hover:text-brand-600">Full event log <ArrowRight size={14} /></Link>
            </section>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <section className="za-fade-up card p-5" style={delay(1100)}>
              <div className="mb-3 flex items-center gap-2"><Building2 size={18} className="text-brand-500" /><h2 className="text-base font-bold text-navy-900">By legal entity</h2></div>
              <ul className="divide-y divide-navy-50">
                {d.by_entity.map((e: any, bi: number) => (
                  <li key={e.entity_id} className="za-slide-in flex items-center justify-between gap-3 py-3" style={delay(1200 + bi * 100)}>
                    <div><div className="font-semibold text-navy-900">{e.legal_name}</div><div className="mono text-[11px] text-slate-500">{e.country} · {e.home_cell_id}</div></div>
                    <div className="flex flex-wrap justify-end gap-1.5 text-xs">
                      {e.denominator ? Object.entries(e.assurance_counts as Record<string, number>).filter(([, v]) => v).map(([k, v]) => <Badge key={k} code={k === "NO_SNAPSHOT" ? "NOT_EVALUATED" : k} label={`${v} ${k.replace("_", " ")}`} />) : <span className="text-slate-400">No adopted obligations</span>}
                      {e.uncertain_evaluations > 0 && <Badge code="UNCERTAIN" label={`${e.uncertain_evaluations} UNCERTAIN`} />}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <section className="za-fade-up card p-5" style={delay(1200)}>
              <div className="mb-3 flex items-center gap-2"><Plug size={18} className="text-brand-500" /><h2 className="text-base font-bold text-navy-900">Connectors</h2><span className="ml-auto text-xs text-slate-500">from the certification registry</span></div>
              <ul className="divide-y divide-navy-50">
                {d.connectors.map((c: any, ci: number) => (
                  <li key={c.name} className="za-slide-in flex items-center justify-between gap-3 py-3" style={delay(1300 + ci * 90)}>
                    <span className="flex items-center gap-2 font-medium text-navy-900"><Activity size={15} className="text-slate-400" />{c.name}</span>
                    <span className="flex gap-1.5"><Badge code={c.certification} /><Badge code={c.runtime_health} /></span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
          <p className="text-center text-xs text-slate-500">{d.language_note}</p>
        </div>
      )}
    </>
  );
}

function DashboardSkeleton() {
  const bar = "za-skeleton rounded-lg";
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card p-4">
            <div className="flex justify-between"><div className="space-y-2"><div className={`${bar} h-3 w-28`} /><div className={`${bar} h-8 w-16`} /></div><div className={`${bar} h-14 w-14 rounded-xl`} /></div>
            <div className={`${bar} mt-5 h-3 w-full`} />
          </div>
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        {[0, 1, 2].map((i) => <div key={i} className="card p-5"><div className={`${bar} h-4 w-40`} /><div className={`${bar} mt-2 h-3 w-56`} /><div className={`${bar} mt-6 h-40 w-full`} /></div>)}
      </div>
      <div className="card p-5"><div className={`${bar} h-4 w-32`} />{[0, 1, 2].map((i) => <div key={i} className={`${bar} mt-4 h-10 w-full`} />)}</div>
    </div>
  );
}
