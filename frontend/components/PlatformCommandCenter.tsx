"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, Building2, ChevronRight, ClipboardCheck, Eye, RefreshCw, Server } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { Alert, Button, EmptyState, ErrorState, Skeleton, buttonClasses } from "./ds";
import { STATUS_SURFACE, STATUS_TEXT, type StatusKey } from "./ds/Status";
import {
  CLASSIFICATION, CategoryBadge, ClassificationBadge, count, errorText, failedSources, fmtUtcSeconds, usePlatformQueue, type PlatformQueue,
} from "./platformQueue";
import { usePageTitle } from "./ui";

/* The Platform Command Center: the Zoiko Super Admin's home ("/").

   Every figure is read from this deployment. The Action Queue figure and Needs attention are the Platform Action Queue
   itself (components/platformQueue.tsx, the same reads, rules and order as /action-queue): organisation-registry
   metadata (GET /platform/organisations, served to the Super Admin role, manage_organisations) and the signed-in
   person's own sessions (GET /sessions), read on opening this page and on Refresh, never on a timer, because every
   registry read is an audit record. The organisation count uses that same registry read (no second request). Service
   health and the cell pill read the public API health check (GET /health) once when the page opens, the way Service
   Health reads it: a 503 carrying a health report is the API answering "unavailable", not a failure to answer.

   No incident, regulatory-intake, evidence-integrity, agent-hold, governance-gate or read-model feed is served to this
   role, so none is shown: the reference design's demonstration figures were removed rather than presented beside real
   ones. Nothing here reads or shows customer evidence, facts or assurance records (INV-14, IAM-REQ-024). The
   organisation registry itself is Tenants & Entities (/organisations). */
const TITLE = "Platform Command Center";

/* ------------------------------------------------------------------------------------------ the API health check */
const HEALTH_TIMEOUT_MS = 20_000;

/** The outcome of GET /health. A report is an answer carrying a health report, HTTP 503 included; no answer (a
 *  connection failure or a timeout) and an answer without a report (for example a gateway error page) are kept apart. */
type HealthView =
  | { kind: "checking" }
  | { kind: "report"; status: string; cellId: string | null; http503: boolean }
  | { kind: "no-answer"; timedOut: boolean }
  | { kind: "bad-response"; httpStatus: number | null };

/** The fields this page uses from a health report (backend/app/routers/core.py `health`), or null when it is not one. */
function healthReport(x: unknown): { status: string; cellId: string | null } | null {
  if (!x || typeof x !== "object" || Array.isArray(x)) return null;
  const o = x as Record<string, unknown>;
  if (typeof o.status !== "string") return null;
  return { status: o.status, cellId: typeof o.cell_id === "string" && o.cell_id ? o.cell_id : null };
}

/** One read when the page opens, never on a timer. It is a plain GET, so identical in-flight reads share one request
 *  (lib/api.ts); a read that has not answered after HEALTH_TIMEOUT_MS is reported as no answer. */
function useHealthCheck(): HealthView {
  const [view, setView] = useState<HealthView>({ kind: "checking" });
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<HealthView>((resolve) => {
      timer = setTimeout(() => resolve({ kind: "no-answer", timedOut: true }), HEALTH_TIMEOUT_MS);
    });
    const answer = api<unknown>("/health").then(
      (body): HealthView => {
        const r = healthReport(body);
        return r ? { kind: "report", ...r, http503: false } : { kind: "bad-response", httpStatus: null };
      },
      (e: unknown): HealthView => {
        // The API client raises on any non-2xx answer. A 503 carrying a health report is the API saying "unavailable".
        const r = e instanceof ApiError && e.status === 503 ? healthReport(e.detail) : null;
        if (r) return { kind: "report", ...r, http503: true };
        return e instanceof ApiError ? { kind: "bad-response", httpStatus: e.status } : { kind: "no-answer", timedOut: false };
      });
    void Promise.race([answer, timeout]).then((v) => { if (current) setView(v); }).finally(() => clearTimeout(timer));
    return () => { current = false; clearTimeout(timer); };
  }, []);
  return view;
}

type Look = { tone: StatusKey; label: string };
const REPORTED: Record<string, Look> = {
  ok: { tone: "verified", label: "Healthy" },
  degraded: { tone: "warning", label: "Degraded" },
  unavailable: { tone: "critical", label: "Unavailable" },
};

function healthLook(v: HealthView): Look {
  switch (v.kind) {
    case "checking": return { tone: "unknown", label: "Checking…" };
    case "report": return REPORTED[v.status] ?? { tone: "unknown", label: v.status || "Not reported" };
    case "no-answer": return { tone: "critical", label: "Not answering" };
    case "bad-response": return { tone: "critical", label: "Unexpected answer" };
  }
}

function healthDetail(v: HealthView): string {
  switch (v.kind) {
    case "checking": return "Checking the API health endpoint…";
    case "report": return `Reported by GET /health${v.http503 ? " with HTTP 503" : ""}${v.cellId ? ` from cell ${v.cellId}` : ""}`;
    case "no-answer": return v.timedOut ? `No answer within ${HEALTH_TIMEOUT_MS / 1000} seconds` : "Could not connect to the API";
    case "bad-response": return v.httpStatus ? `HTTP ${v.httpStatus} without a health report` : "An answer without a health report";
  }
}

/* ---------------------------------------------------------------------------------------------------------- styles */
const PANEL = "rounded-panel border border-line bg-surface-card p-5 shadow-panel sm:p-6";
const H2 = "text-[18px] font-semibold leading-7 text-fg";
// Literal class names per tone, so Tailwind generates them.
const DOT: Record<StatusKey, string> = {
  critical: "bg-status-critical", warning: "bg-status-warning", info: "bg-status-info", verified: "bg-status-verified", unknown: "bg-fg-muted",
};

export function PlatformCommandCenter() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  const health = useHealthCheck();
  // The Action Queue's own reads: its items, and the organisation registry that the organisation figure counts.
  const q = usePlatformQueue();
  const kpis = [queueKpi(q), organisationsKpi(q), healthKpi(health)];

  return (
    <div>
      <p role="status" aria-live="polite" className="sr-only">{q.announcement}</p>
      <div className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="min-w-0">
          <h1 className="text-[26px] font-semibold leading-tight text-fg sm:text-[28px]">{TITLE}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[14px] text-fg-secondary">
            <span>Cross-tenant metadata only</span><span aria-hidden className="h-4 w-px bg-line-strong" /><span>Super Admin operations</span>
          </p>
        </div>
        <ContextPills health={health} />
      </div>

      <ul aria-label="Key platform figures" className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {kpis.map((k) => <KpiTile key={k.key} k={k} />)}
      </ul>

      <NeedsAttention q={q} className="mt-6" />

      <p className="mt-6 max-w-4xl type-caption text-fg-muted">
        Every figure here is read from this deployment: the Action Queue and the organisation count from the organisation registry and your own
        sessions (on opening this page and on Refresh; each registry read is recorded in the platform&apos;s audit trail), and service health from the
        API health check when the page opens. The first pill at the top names the API cell that answered that check, with the status it reported;
        the deployment environment&apos;s name is not reported to the browser, so it is not shown. Global metadata is organisation registry metadata
        across every organisation on this cell. No incident, regulatory-intake, evidence-integrity, agent-hold, governance-gate or read-model feed is
        served to the Super Admin role, so none is shown. Customer evidence, facts and assurance records are never shown here.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------------------------------------------------- pieces */
/** The API cell (GET /health) and the scope this role works in. No API reports the deployment's environment name to
 *  the browser, so the cell that answered is shown in its place, followed by the reported status as a word (the dot
 *  repeats it in colour only). What the API answered in detail is the Service health figure's caption; what the two
 *  pills mean is in the page's footnote. */
function ContextPills({ health }: { health: HealthView }) {
  const look = healthLook(health);
  const cell = health.kind === "report" ? health.cellId ?? "Cell not reported"
    : health.kind === "checking" ? "Checking API…"
    : health.kind === "no-answer" ? "API not answering" : "Unexpected API answer";
  return (
    <div role="group" aria-label="Dashboard context"
      className="flex w-fit max-w-full flex-wrap items-center rounded-control border border-line bg-surface-control/70 text-[14px] text-fg shadow-card">
      <span className="flex min-w-0 items-center gap-2 px-4 py-2.5">
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[look.tone]}`} />
        <span className="sr-only">API cell, live: </span><span className="truncate">{cell}</span>
        {health.kind === "report" && <>
          <span aria-hidden className="text-fg-muted">·</span>
          <span className="whitespace-nowrap font-semibold"><span className="sr-only">, status </span>{look.label}</span>
        </>}
      </span>
      <span aria-hidden className="h-5 w-px bg-line-strong" />
      <span className="px-4 py-2.5">Scope: Global metadata</span>
    </div>
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

type Kpi = {
  key: string; label: string; value: ReactNode; hint: string; tone: StatusKey; icon: LucideIcon;
  /** Replaces the default value styling (a status word rather than a number). */
  valueClass?: string;
  /** Sits beside the value: the link to the figure's own screen. */
  aside: ReactNode;
  /** A read for this figure is in flight. */
  busy?: boolean;
};

function KpiTile({ k }: { k: Kpi }) {
  const Icon = k.icon;
  return (
    // A figure card as on the organisation Overview (app/page.tsx KpiCard): label, value, hint.
    <li aria-busy={k.busy || undefined} className="min-w-0 rounded-card border border-line bg-surface-card px-4 pb-3 pt-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <span aria-hidden className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-control border ${STATUS_SURFACE[k.tone]} ${STATUS_TEXT[k.tone]}`}>
          <Icon size={16} />
        </span>
        <LiveTag />
      </div>
      <p className="mt-3 text-[14px] font-semibold leading-5 text-fg">{k.label}</p>
      {/* The value keeps its full width; when a status word and the link do not fit side by side, the link wraps below. */}
      <div className="mt-1 flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
        <p className={`font-bold leading-8 tabular-nums transition-opacity ${k.busy ? "opacity-60" : ""} ${k.valueClass ?? "text-[28px] text-fg"}`}>{k.value}</p>
        {k.aside}
      </div>
      <p className="mt-1 text-[13px] leading-[17px] text-fg-muted">{k.hint}</p>
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
  const base = { key: "queue", label: "Action Queue", icon: ClipboardCheck, aside: <TileLink href="/action-queue" label="Open Action Queue" />, busy: q.loading };
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

/** The organisations in the registry, from the Action Queue's own registry read (no second request). A failed read is
 *  said so, never shown as zero. */
function organisationsKpi(q: PlatformQueue): Kpi {
  const base = { key: "organisations", label: "Organisations", icon: Building2, aside: <TileLink href="/organisations" label="Open Tenants & Entities" />, busy: q.loading };
  if (!q.orgs) {
    if (q.loading) {
      return { ...base, tone: "unknown", hint: q.firstLoad ? "Loading the registry…" : "Refreshing…",
        value: <span role="status" aria-label="Loading the organisation registry" className="skeleton my-0.5 block h-8 w-14" /> };
    }
    return { ...base, tone: "unknown", value: <><span aria-hidden>—</span><span className="sr-only">Not available</span></>,
      hint: "Unavailable: the organisation registry could not be loaded" };
  }
  const active = q.orgs.filter((o) => o.status === "ACTIVE").length;
  const suspended = q.orgs.filter((o) => o.status === "SUSPENDED").length;
  return { ...base, tone: "info", value: String(q.orgs.length), hint: q.loading ? "Refreshing…" : `${active} active · ${suspended} suspended` };
}

/** Service health as GET /health reported it when the page opened; Service Health has the components and re-checks. */
function healthKpi(health: HealthView): Kpi {
  const look = healthLook(health);
  return {
    key: "health", label: "Service health", icon: Server, tone: look.tone, busy: health.kind === "checking",
    value: health.kind === "checking" ? <span role="status" aria-label="Checking service health" className="skeleton my-0.5 block h-8 w-28" /> : look.label,
    valueClass: health.kind === "checking" ? undefined : `text-[26px] tracking-normal ${STATUS_TEXT[look.tone]}`,
    hint: healthDetail(health), aside: <TileLink href="/service-health" label="Open Service Health" />,
  };
}

function TileLink({ href, label }: { href: string; label: string }) {
  return (
    <Link href={href} className="mb-0.5 inline-flex min-h-6 shrink-0 items-center gap-0.5 rounded-chip type-caption font-semibold text-accent-fg hover:underline">
      {label}<ChevronRight size={14} aria-hidden />
    </Link>
  );
}

const ATTENTION_ROWS = 5;

/** Needs attention: the first five Action Queue items, in the queue's own order, each with its existing action. Only
 *  what the queue holds is shown (classification, title, organisation, category); it has no owners, due dates,
 *  references or severities, so none are shown. */
function NeedsAttention({ q, className = "" }: { q: PlatformQueue; className?: string }) {
  const rows = q.items.slice(0, ATTENTION_ROWS);
  const hintIds = useId();  // each row's action hint describes its action link
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
        <>
          {/* What each classification means, as on the Action Queue (the chips name it; this says what it is). */}
          <p className="mt-1 max-w-3xl type-caption text-fg-muted">
            <span className="font-semibold text-fg-secondary">{CLASSIFICATION.ACTION_REQUIRED.label}</span>: {CLASSIFICATION.ACTION_REQUIRED.meaning}.{" "}
            <span className="font-semibold text-fg-secondary">{CLASSIFICATION.REVIEW.label}</span>: {CLASSIFICATION.REVIEW.meaning}.
          </p>
          <ul aria-label="Action Queue items needing attention" className="mt-3 divide-y divide-line">
            {rows.map((i, n) => (
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
                  <p id={`${hintIds}-${n}`} className="mt-1 type-caption text-fg-muted">{i.action.hint}</p>
                </div>
                <Link href={i.action.href} aria-describedby={`${hintIds}-${n}`} className={`${buttonClasses({ variant: "secondary", size: "sm" })} w-full shrink-0 sm:w-auto`}>
                  {i.action.label}{i.organisation && <span className="sr-only"> for {i.organisation.name}</span>}
                  <ArrowRight size={14} aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
