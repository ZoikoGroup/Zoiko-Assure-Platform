"use client";

import Link from "next/link";
import { Fragment, useEffect } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive, ArrowRight, ArrowUp, Briefcase, ChevronRight, ClipboardCheck, Eye, RefreshCw, Server, ShieldCheck, TriangleAlert,
} from "lucide-react";
import { useApi } from "@/lib/api";
import { Alert, Button, EmptyState, ErrorState, Skeleton, buttonClasses } from "./ds";
import { STATUS_SURFACE, STATUS_TEXT, STATUS_TONE, type StatusKey } from "./ds/Status";
import {
  CategoryBadge, ClassificationBadge, count, errorText, failedSources, fmtUtcSeconds, usePlatformQueue, type Org, type PlatformQueue,
} from "./platformQueue";
import { useSyntheticDataBadge } from "./Shell";
import { usePageTitle } from "./ui";

/* The Platform Command Center: the Zoiko Super Admin's home ("/"), laid out as the Platform Command Center reference.

   Live: the Action Queue figure and Needs attention are the Platform Action Queue itself (components/platformQueue.tsx,
   the same reads, rules and order as /action-queue): organisation-registry metadata (GET /platform/organisations,
   served to the Super Admin role, manage_organisations) and the signed-in person's own sessions (GET /sessions), read
   on opening this page and on Refresh, never on a timer, because every registry read is an audit record. Tenant fleet
   uses that same registry read (no second request), and the cell pill reads the public API health check (GET /health).
   Synthetic: no incident, regulatory-intake, evidence-integrity, agent-hold, cell-check, governance-gate or read-model
   feed is served to this role, so those figures are the reference design's demonstration values, each marked SYNTHETIC
   and never presented as this platform's state. Nothing here reads or shows customer evidence, facts or assurance
   records (INV-14, IAM-REQ-024). The organisation registry itself is Tenants & Entities (/organisations). */
const TITLE = "Platform Command Center";

type Health = { status: string; cell_id: string; dictionary_version: string };

/* ------------------------------------------------------------------------------------- synthetic demonstration data */
type Kpi = {
  key: string; label: string; value: ReactNode; hint: string; tone: StatusKey; icon: LucideIcon;
  plainIcon?: boolean; valueClass?: string; corner?: ReactNode;
  /** A decorative trend: synthetic figures only, since no history is recorded for any figure here. */
  bars?: number[];
  /** Replaces the SYNTHETIC tag (a live figure passes its LIVE tag). */
  tag?: ReactNode;
  /** Sits where the trend bars would, beside the value (a live figure's link). */
  aside?: ReactNode;
  /** A read for this figure is in flight. */
  busy?: boolean;
};
const KPIS: Kpi[] = [
  { key: "incidents", label: "Critical incidents", value: "03", hint: "Platform runtime", tone: "critical", icon: TriangleAlert,
    bars: [8, 14, 10, 19, 28], corner: <Delta value="2" /> },
  { key: "intake", label: "Regulatory intake", value: "DEGRADED", hint: "2 stale sources", tone: "warning", icon: Archive,
    valueClass: `text-[26px] tracking-normal ${STATUS_TEXT.warning}`, bars: [14, 22, 9, 26, 18] },
  { key: "evidence", label: "Evidence integrity", value: "02", hint: "Platform verification failures", tone: "critical", icon: ShieldCheck,
    plainIcon: true, bars: [5, 14, 8, 20, 28] },
  { key: "agents", label: "Agent actions held", value: "07", hint: "Policy-gated holds", tone: "info", icon: Briefcase, bars: [10, 16, 12, 24, 16] },
  { key: "cells", label: "Cell health", value: <>98.9<span className="ml-0.5 text-[0.6em] font-semibold leading-none text-fg-secondary">%</span></>,
    hint: "Scheduled checks passing", tone: "verified", icon: Server, bars: [18, 26, 14, 28, 22],
    corner: <span className="type-caption font-semibold text-status-verified">Healthy</span> },
];

const GATES = [
  { label: "Awaiting human decision", count: 6 },
  { label: "Release holds", count: 2 },
  { label: "JIT sessions active", count: 1, info: true },
];

const SIGNALS = [
  { steps: ["Regulation", "requirement"], value: "3 ingestion retries", owner: "Source operations" },
  { steps: ["Control", "test", "proof"], value: "2 verify failures", owner: "Evidence operations" },
  { steps: ["Decision", "action"], value: "7 agent holds", owner: "AI governance" },
];

/* ---------------------------------------------------------------------------------------------------------- styles */
const PANEL = "rounded-[22px] border border-line bg-surface-raised/70 p-5 shadow-panel sm:p-6";
const H2 = "text-[18px] font-semibold leading-7 text-fg";
// Literal class names per tone, so Tailwind generates them.
const BAR: Record<StatusKey, string> = {
  critical: "bg-status-critical", warning: "bg-status-warning", info: "bg-status-info", verified: "bg-status-verified", unknown: "bg-status-unknown",
};
const GLOW: Record<StatusKey, string> = {
  critical: "bg-status-critical/20", warning: "bg-status-warning/20", info: "bg-status-info/20", verified: "bg-status-verified/15", unknown: "bg-status-unknown/15",
};
const BAR_OPACITY = ["opacity-35", "opacity-55", "opacity-55", "opacity-80", "opacity-100"];

export function PlatformCommandCenter() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  useSyntheticDataBadge();
  const health = useApi<Health>("/health");
  // The Action Queue's own reads: its items, and the organisation registry that Tenant fleet counts.
  const q = usePlatformQueue();
  // The live Action Queue figure takes the reference design's second place (its synthetic "Actions awaiting").
  const kpis = [...KPIS.slice(0, 1), queueKpi(q), ...KPIS.slice(1)];

  return (
    <div>
      <p role="status" aria-live="polite" className="sr-only">{q.announcement}</p>
      <div className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="min-w-0">
          <h1 className="text-[26px] font-bold leading-tight tracking-tight text-fg sm:text-[32px]">{TITLE}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[15px] text-fg-secondary">
            <span>Cross-tenant metadata only</span><span aria-hidden className="h-4 w-px bg-line-strong" /><span>Super Admin operations</span>
          </p>
        </div>
        <ContextPills health={health} />
      </div>

      <ul aria-label="Key platform figures" className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {kpis.map((k) => <KpiTile key={k.key} k={k} />)}
      </ul>

      <div className="mt-6 grid grid-cols-1 gap-5 xl:grid-cols-3">
        <NeedsAttention q={q} className="xl:col-span-2" />
        <div className="grid content-start gap-5 md:grid-cols-2 xl:grid-cols-1">
          <GovernanceGates />
          <DataProvenance health={health} />
        </div>
      </div>

      <AssuranceSignals orgs={{ data: q.orgs, error: q.orgError, loading: q.loading }} />

      <p className="mt-6 max-w-4xl type-caption text-fg-muted">
        Figures marked <span className="font-semibold text-fg-secondary">SYNTHETIC</span> are demonstration values from the reference design, not platform
        records: no incident, regulatory-intake, evidence-integrity, agent-hold, cell-check, governance-gate or read-model feed is served to the Super Admin
        role yet, and no figure here has a recorded history. Figures marked <span className="font-semibold text-fg-secondary">LIVE</span> are read from this
        deployment: the Action Queue (worked out from the organisation registry and your own sessions on opening this page and on Refresh), the tenant
        fleet from that same registry read, and the API health check. Customer evidence, facts and assurance records are never shown here.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------------------------------------------------- pieces */
/** The live API cell (GET /health) and the scope this role works in. No API reports the deployment's environment name
 *  to the browser, so the cell that answered is shown in its place. */
function ContextPills({ health }: { health: { data: Health | null; error: unknown } }) {
  const h = health.data;
  const dot = health.error ? "bg-status-critical" : h ? (h.status === "ok" ? "bg-status-verified" : "bg-status-warning") : "bg-fg-muted";
  const cell = h?.cell_id ?? (health.error ? "API not answering" : "Checking API…");
  const tip = h ? `Live from GET /health: the API answered "${h.status}" from cell ${h.cell_id}. The deployment environment's name is not reported to the browser, so it is not shown.`
    : health.error ? "The API health check (GET /health) did not answer." : "Checking the API health endpoint…";
  return (
    <div role="group" aria-label="Dashboard context"
      className="flex w-fit max-w-full flex-wrap items-center rounded-control border border-line bg-surface-control/70 text-[14px] text-fg shadow-card">
      <span title={tip} className="flex min-w-0 items-center gap-2 px-4 py-2.5">
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <span className="sr-only">API cell, live: </span><span className="truncate">{cell}</span>
      </span>
      <span aria-hidden className="h-5 w-px bg-line-strong" />
      <span title="Organisation registry metadata across every organisation on this cell; never customer evidence, facts or assurance records."
        className="px-4 py-2.5">Scope: Global metadata</span>
    </div>
  );
}

/** Marks a figure or panel as synthetic demonstration data, never a platform record. */
function SyntheticTag() {
  return (
    <span title="Synthetic demonstration data from the reference design, not a platform record"
      className="inline-flex shrink-0 items-center rounded-chip border border-dashed border-line-strong px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted">
      Synthetic<span className="sr-only"> demonstration data, not a platform record</span>
    </span>
  );
}

/** Marks a figure read from this deployment just now. */
function LiveTag() {
  return (
    <span title="Read from this deployment just now"
      className="inline-flex shrink-0 items-center rounded-chip border border-status-verified-line bg-status-verified-tint px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-status-verified">
      Live<span className="sr-only"> data read from this deployment</span>
    </span>
  );
}

function Delta({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center gap-0.5 rounded-chip border px-1.5 py-0.5 type-caption font-semibold tabular-nums ${STATUS_TONE.critical}`}>
      <ArrowUp size={12} aria-hidden /><span className="sr-only">Up </span>{value}
    </span>
  );
}

/** A decorative five-step trend; the figure beside it carries the meaning. */
function Bars({ heights, tone }: { heights: number[]; tone: StatusKey }) {
  return (
    <span aria-hidden className="flex h-7 shrink-0 items-end gap-1.5 pb-1">
      {heights.map((h, i) => <span key={i} className={`w-1.5 rounded-xs ${BAR[tone]} ${BAR_OPACITY[i]}`} style={{ height: h }} />)}
    </span>
  );
}

function KpiTile({ k }: { k: Kpi }) {
  const Icon = k.icon;
  return (
    <li aria-busy={k.busy || undefined} className="relative isolate min-w-0 overflow-hidden rounded-[18px] border border-line bg-surface-card/70 p-5 shadow-card">
      <span aria-hidden className={`pointer-events-none absolute -right-14 -top-20 -z-10 h-48 w-48 rounded-full blur-3xl ${GLOW[k.tone]}`} />
      <div className="flex items-start justify-between gap-3">
        <span aria-hidden className={`flex h-10.5 w-10.5 shrink-0 items-center justify-center rounded-[11px] border ${k.plainIcon
          ? "border-line-strong bg-fg/5 text-fg" : `${STATUS_SURFACE[k.tone]} ${STATUS_TEXT[k.tone]}`}`}>
          <Icon size={18} />
        </span>
        <span className="flex flex-wrap items-center justify-end gap-2">{k.corner}{k.tag ?? <SyntheticTag />}</span>
      </div>
      <p className="mt-5 text-[14px] leading-5 text-fg-secondary">{k.label}</p>
      <div className="mt-1.5 flex items-end justify-between gap-3">
        <p className={`min-w-0 font-bold leading-9 tabular-nums transition-opacity ${k.busy ? "opacity-60" : ""} ${k.valueClass ?? "text-[32px] tracking-tight text-fg"}`}>{k.value}</p>
        {k.aside ?? (k.bars && <Bars heights={k.bars} tone={k.tone} />)}
      </div>
      <p className="mt-2 type-caption text-fg-muted">{k.hint}</p>
    </li>
  );
}

/** The live Action Queue figure: every open item in the queue, whatever its classification. It is a count only when
 *  both sources loaded on the latest refresh. A read that failed, in whole or in part, is said so (never shown as zero
 *  or as a partial total), and while a read is in flight the figure is marked as refreshing. No trend is drawn: the
 *  queue keeps no history. */
function queueKpi(q: PlatformQueue): Kpi {
  const action = q.items.filter((i) => i.classification === "ACTION_REQUIRED").length;
  const review = q.items.filter((i) => i.classification === "REVIEW").length;
  const base = { key: "queue", label: "Action Queue", icon: ClipboardCheck, tag: <LiveTag />, aside: <QueueLink />, busy: q.loading };
  if (q.loading && !q.complete) {
    return { ...base, tone: "unknown", hint: q.firstLoad ? "Loading the queue…" : "Refreshing…",
      value: <span role="status" aria-label="Loading the Action Queue" className="skeleton my-0.5 block h-8 w-14" /> };
  }
  if (!q.complete) {
    const missing = [!q.orgs && "organisation registry", !q.sessions && "your sessions"].filter(Boolean).join(" and ");
    return { ...base, tone: "unknown", value: <><span aria-hidden>—</span><span className="sr-only">Not available</span></>,
      hint: q.orgs || q.sessions ? `Incomplete: ${missing} not loaded` : "Unavailable: the queue could not be loaded" };
  }
  return { ...base, tone: action ? "warning" : review ? "info" : "verified", value: String(q.items.length),
    hint: q.loading ? "Refreshing…" : `${action} action required · ${review} to review` };
}

function QueueLink() {
  return (
    <Link href="/action-queue" className="mb-0.5 inline-flex min-h-6 shrink-0 items-center gap-0.5 rounded-chip type-caption font-semibold text-accent-fg hover:underline">
      Open Action Queue<ChevronRight size={14} aria-hidden />
    </Link>
  );
}

const ATTENTION_ROWS = 5;

/** Needs attention: the first five Action Queue items, in the queue's own order, each with its existing action. Only
 *  what the queue holds is shown (classification, title, organisation, category); it has no owners, due dates,
 *  references or severities, so none are shown. */
function NeedsAttention({ q, className = "" }: { q: PlatformQueue; className?: string }) {
  const rows = q.items.slice(0, ATTENTION_ROWS);
  const failed = failedSources(q);
  const nothingLoaded = !q.orgs && !q.sessions;
  const pending = q.loading && nothingLoaded;  // no answer to show until this read completes
  const summary = pending ? "Reading the Action Queue…"
    : nothingLoaded ? "The Action Queue is unavailable"
    : `${q.items.length > rows.length ? `Showing ${rows.length} of ${q.items.length} open items` : count(q.items.length, "open item")}${q.complete ? "" : " (incomplete)"}, in Action Queue order`;
  return (
    <section aria-labelledby="pcc-attention-h" aria-busy={q.loading || undefined} className={`${PANEL} ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="pcc-attention-h" className={H2}>Needs attention</h2>
          <LiveTag />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button variant="secondary" size="sm" icon={RefreshCw} loading={q.loading} onClick={() => void q.refresh()}>
            {q.loading ? "Refreshing…" : "Refresh"}
          </Button>
          <Link href="/action-queue" className="inline-flex min-h-6 items-center gap-0.5 rounded-chip type-secondary font-semibold text-accent-fg hover:underline">
            View all<span className="sr-only"> in the Action Queue</span><ChevronRight size={16} aria-hidden />
          </Link>
        </div>
      </div>
      <p className="mt-1 type-caption text-fg-muted">
        {summary}. Last successful refresh:{" "}
        {q.lastSuccess
          ? <time dateTime={q.lastSuccess} className="font-semibold tabular-nums text-fg-secondary">{fmtUtcSeconds(q.lastSuccess)}</time>
          : <span className="font-semibold text-fg-secondary">{q.loading ? "loading…" : "not yet"}</span>}
      </p>

      {failed.length > 0 && !nothingLoaded && (
        <div className="mt-4 space-y-2">
          {failed.map((f) => (
            <Alert key={f.name} tone="critical" title={`${f.name} could not be loaded`}>
              <p>{errorText(f.error)} ({f.source}). Its items are not shown, so this list and the Action Queue figure are incomplete. Use Refresh to try again.</p>
            </Alert>
          ))}
        </div>
      )}

      {pending ? (
        <div role="status" aria-label="Loading the Action Queue" className="mt-4 divide-y divide-line">
          {[0, 1, 2].map((i) => (
            <div key={i} className="py-3.5">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="mt-2 h-4 w-3/4" />
            </div>
          ))}
        </div>
      ) : nothingLoaded && failed.length > 0 ? (
        <ErrorState compact error={failed[0].error} title="The Action Queue could not be loaded" onRetry={() => void q.refresh()} />
      ) : rows.length === 0 ? (
        failed.length > 0 ? (
          <EmptyState compact title="No items from the sources that loaded"
            description="This list is incomplete until the source that failed loads. Use Refresh to try again." />
        ) : (
          <EmptyState compact icon={Eye} title="Nothing needs attention"
            description="Checked on the last refresh: active organisations without an Organisation Admin, suspended organisations and other sessions on your account." />
        )
      ) : (
        <ul aria-label="Action Queue items needing attention" className="mt-3 divide-y divide-line">
          {rows.map((i) => (
            <li key={i.key} className="flex flex-col gap-3 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <ClassificationBadge classification={i.classification} />
                  <CategoryBadge category={i.category} />
                </div>
                <p className="mt-2 text-[14px] font-medium leading-5 text-fg">{i.title}</p>
                {i.organisation && (
                  <p className="mt-0.5 break-words type-caption text-fg-secondary">Organisation: <span className="font-semibold text-fg">{i.organisation.name}</span></p>
                )}
              </div>
              <Link href={i.action.href} title={i.action.hint} className={`${buttonClasses({ variant: "secondary", size: "sm" })} w-full shrink-0 sm:w-auto`}>
                {i.action.label}{i.organisation && <span className="sr-only"> for {i.organisation.name}</span>}
                <ArrowRight size={14} aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function GovernanceGates() {
  return (
    <section aria-labelledby="pcc-gates-h" className={PANEL}>
      <div className="flex items-center justify-between gap-3">
        <h2 id="pcc-gates-h" className={H2}>Governance gates</h2>
        <SyntheticTag />
      </div>
      <ul className="mt-4 space-y-2.5">
        {GATES.map((g) => (
          <li key={g.label} className="flex items-center justify-between gap-3 rounded-xl border border-line bg-fg/3 px-4 py-3 text-[14px] text-fg-secondary">
            {g.label}
            <span className={`inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-full px-1.5 text-[12px] font-semibold tabular-nums ${g.info ? `border ${STATUS_TONE.info}` : "bg-fg/10 text-fg"}`}>{g.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DataProvenance({ health }: { health: { data: Health | null; error: unknown } }) {
  const h = health.data;
  return (
    <section aria-labelledby="pcc-provenance-h" className={PANEL}>
      <div className="flex items-center justify-between gap-3">
        <h2 id="pcc-provenance-h" className={H2}>Data provenance</h2>
        <SyntheticTag />
      </div>
      <p className="mt-3 text-[14px] font-medium leading-5 text-accent-fg">Read models: healthy / 4m lag max</p>
      <p className="mt-1.5 type-caption text-fg-muted">
        Sources: signed<span aria-hidden className="mx-1.5">•</span><span className="sr-only">, </span>Metrics: reconciled
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3 type-caption text-fg-secondary">
        <LiveTag />
        {h ? <span>API health <span className="font-semibold uppercase">{h.status}</span> · cell <span className="type-code">{h.cell_id}</span></span>
          : health.error ? <span>The API health check did not answer</span> : <span>Checking API health…</span>}
      </div>
    </section>
  );
}

/** "Control → test → proof", read by screen readers as "Control to test to proof". */
function Flow({ steps }: { steps: string[] }) {
  return <>{steps.map((s, i) => <Fragment key={s}>{i > 0 && <><span aria-hidden> → </span><span className="sr-only"> to </span></>}{s}</Fragment>)}</>;
}

function AssuranceSignals({ orgs }: { orgs: { data: Org[] | null; error: unknown; loading: boolean } }) {
  const list = orgs.data;
  const suspended = list?.filter((o) => o.status === "SUSPENDED").length ?? 0;
  const card = "h-full rounded-[14px] border border-line bg-surface-card/60 p-4";
  return (
    <section aria-labelledby="pcc-signals-h" className={`${PANEL} mt-6`}>
      <h2 id="pcc-signals-h" className={H2}>Platform assurance signals</h2>
      <ul className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {SIGNALS.map((s) => (
          <li key={s.owner} className={`min-w-0 ${card}`}>
            <h3 className="text-[14px] font-semibold leading-5 text-fg"><Flow steps={s.steps} /></h3>
            <p className="mt-1.5 text-[14px] leading-5 text-fg-secondary">{s.value}</p>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 type-caption text-fg-muted"><span className="whitespace-nowrap">{s.owner}</span><SyntheticTag /></div>
          </li>
        ))}
        <li className="min-w-0">
          <Link href="/organisations" title="Open Tenants & Entities, the organisation registry"
            className={`group block transition-colors hover:border-line-strong ${card}`}>
            <h3 className="text-[14px] font-semibold leading-5 text-fg">Tenant fleet</h3>
            <div className="mt-1.5 text-[14px] leading-5 text-fg-secondary">
              {list ? `${list.length} ${list.length === 1 ? "organisation" : "organisations"} · ${suspended} suspended`
                : orgs.error ? "Registry unavailable"
                : <span role="status" aria-label="Loading the organisation registry" className="skeleton my-0.5 block h-4 w-40" />}
            </div>
            <div className="mt-3 flex items-center justify-between gap-2 type-caption text-fg-muted">
              Metadata only
              <span className="flex items-center gap-1.5"><LiveTag /><ChevronRight size={14} aria-hidden className="shrink-0 transition-colors group-hover:text-fg" /></span>
            </div>
          </Link>
        </li>
      </ul>
    </section>
  );
}
