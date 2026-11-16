"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Boxes, CircleOff, Database, Globe2, History, Layers, Network, Plug, RefreshCw, Server, Timer, Workflow } from "lucide-react";
import { ApiError, api, errText } from "@/lib/api";
import { Button, Callout, DescriptionList, EmptyState, PLANES, PlaneBadge, Skeleton, StatusMark } from "./ds";
import { STATUS_ICON, STATUS_TEXT, STATUS_TONE, type StatusKey } from "./ds/Status";
import { fmtUtcSeconds } from "./platformQueue";
import { usePageTitle } from "./ui";

/* Service Health: the Zoiko Super Admin's view of this deployment's API and its dependencies (Platform Control Plane).

   One source only: the public health check GET /api/health (backend/app/routers/core.py `health`). It runs a live
   PostgreSQL query and a live Redis PING on every call and answers
     { status: "ok" | "degraded" | "unavailable", cell_id, dictionary_version,
       components: { application: {state}, postgresql: {state, reason?}, redis: {state, required, tls?, reason?} } }
   with HTTP 503 when the status is "unavailable". A 503 is still a health report (the API answered), so it is read
   from the error the API client raises; it is never treated as a connection failure. Everything is shown as the API
   reported it: nothing here is sample data, and nothing the endpoint does not report (uptime, history, workers,
   queues, connectors, other cells) is shown as a figure. The API reports no time or duration, so "checked" and
   "response time" are measured by the browser and labelled so. Checked on opening the page and on "Check again" only. */
const TITLE = "Service Health";
const SOURCE = "GET /api/health";
/** A hung request would otherwise leave the page loading for good. Redis is given 2 s per attempt by default. */
const TIMEOUT_MS = 20_000;

const PANEL = "rounded-panel border border-line bg-surface-raised/70 p-5 shadow-panel sm:p-6";
const CARD = "rounded-card border border-line bg-surface-card p-card shadow-card";
const H2 = "text-[18px] font-semibold leading-7 text-fg";

/* --------------------------------------------------------------------------------------------- the health report */
/** One dependency as reported. `reason` is an error type name or a short message (never a URL or credential);
 *  `required` and `tls` are reported for Redis only, `tls` once Redis is configured. */
type Dependency = { key: string; state: string; reason?: string; required?: boolean; tls?: boolean };
/** `dependencies` is null when the answer has no `components` object. */
type Report = { status: string; cellId?: string; dictionaryVersion?: string; dependencies: Dependency[] | null };

const ORDER = ["application", "postgresql", "redis"];
const rank = (k: string) => (ORDER.includes(k) ? ORDER.indexOf(k) : ORDER.length);

/** The health report in an API answer, or null when the answer is not one (a gateway page, an unrelated error). */
function parseReport(x: unknown): Report | null {
  if (!x || typeof x !== "object" || Array.isArray(x)) return null;
  const o = x as Record<string, unknown>;
  if (typeof o.status !== "string") return null;
  const text = (v: unknown) => (typeof v === "string" && v ? v : undefined);
  const flag = (v: unknown) => (typeof v === "boolean" ? v : undefined);
  const c = o.components;
  const dependencies = c && typeof c === "object" && !Array.isArray(c)
    ? Object.entries(c as Record<string, unknown>).map(([key, v]): Dependency => {
        const d = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
        return { key, state: text(d.state) ?? "", reason: text(d.reason), required: flag(d.required), tls: flag(d.tls) };
      }).sort((a, b) => rank(a.key) - rank(b.key) || a.key.localeCompare(b.key))
    : null;
  return { status: o.status, cellId: text(o.cell_id), dictionaryVersion: text(o.dictionary_version), dependencies };
}

/** The outcome of one check. `at` and `ms` are the browser's: when the answer (or the failure) arrived, and how long
 *  after sending the request, network time included. */
type Stamp = { at: string; ms: number };
type Answered = Stamp & { kind: "report"; report: Report; http503: boolean };
type Check =
  | Answered
  /** Nothing came back: the API is down or unreachable (connection refused, DNS, CORS, offline) or did not answer in time. */
  | (Stamp & { kind: "no-response"; error: unknown; timedOut: boolean })
  /** Something answered, but not with a health report (for example a gateway error page). */
  | (Stamp & { kind: "bad-response"; httpStatus?: number; error: unknown });

const stamp = (t0: number): Stamp => ({ at: new Date().toISOString(), ms: Math.round(performance.now() - t0) });

function useHealthCheck() {
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(true);
  const seq = useRef(0);
  const inFlight = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    inFlight.current?.abort();
    const id = ++seq.current;
    const ctrl = new AbortController();
    inFlight.current = ctrl;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, TIMEOUT_MS);
    setBusy(true);
    const t0 = performance.now();
    let next: Check;
    try {
      const body = await api<unknown>("/health", { signal: ctrl.signal });
      const report = parseReport(body);
      next = report ? { kind: "report", report, http503: false, ...stamp(t0) } : { kind: "bad-response", error: null, ...stamp(t0) };
    } catch (e) {
      // The API client raises on any non-2xx answer. A 503 carrying a health report is the API saying "unavailable".
      const report = e instanceof ApiError && e.status === 503 ? parseReport(e.detail) : null;
      next = report ? { kind: "report", report, http503: true, ...stamp(t0) }
        : e instanceof ApiError ? { kind: "bad-response", httpStatus: e.status, error: e, ...stamp(t0) }
        : { kind: "no-response", error: e, timedOut, ...stamp(t0) };
    } finally {
      clearTimeout(timer);
    }
    if (id !== seq.current) return;  // a newer check started, or the page closed
    inFlight.current = null;
    setCheck(next);
    setBusy(false);
  }, []);

  useEffect(() => {
    void run();
    return () => { seq.current++; inFlight.current?.abort(); };
  }, [run]);

  return { check, busy, run };
}

/* ------------------------------------------------------------------------------------------------- status looks */
/** How a reported state is shown: its canonical colour family and a plain label. */
type Look = { status: StatusKey; label: string };

const OVERALL: Record<string, Look> = {
  ok: { status: "verified", label: "Healthy" },
  degraded: { status: "warning", label: "Degraded" },
  unavailable: { status: "critical", label: "Unavailable" },
};
const overallLook = (s: string): Look => OVERALL[s] ?? { status: "unknown", label: s || "Not reported" };

const STATE_LABEL: Record<string, string> = { healthy: "Healthy", unavailable: "Unavailable", misconfigured: "Misconfigured", disabled: "Disabled" };

/** A dependency's colour shows its effect on the platform, by the API's own rule: PostgreSQL, or Redis when required,
 *  makes the platform unavailable; an optional Redis only degrades it; a disabled Redis does not count. */
function dependencyLook(d: Dependency): Look {
  const label = STATE_LABEL[d.state] ?? (d.state || "Not reported");
  if (d.state === "healthy") return { status: "verified", label };
  if (d.state === "disabled") return { status: "unknown", label };
  if (d.state === "unavailable" || d.state === "misconfigured") {
    return { status: d.key === "redis" && d.required !== true ? "warning" : "critical", label };
  }
  return { status: "unknown", label };
}
const isProblem = (d: Dependency) => ["warning", "critical"].includes(dependencyLook(d).status);

const KNOWN: Record<string, { name: string; icon: LucideIcon; role: string }> = {
  application: { name: "API application", icon: Server, role: "The Zoiko Assure API process that answered this check." },
  postgresql: { name: "PostgreSQL database", icon: Database, role: "The authoritative record store. Checked with a live query on every health check." },
  redis: { name: "Redis cache", icon: Layers, role: "A cache in front of PostgreSQL, which stays authoritative: nothing in Redis is treated as a record. Checked with a live PING when configured." },
};
const nameOf = (key: string) => KNOWN[key]?.name ?? key;

const FALLBACK_TO_POSTGRES = "Reads fall back to PostgreSQL, so the platform keeps serving and the API reports it Degraded.";
const REQUIRED_SO_UNAVAILABLE = "Because Redis is required, the API reports the platform Unavailable (HTTP 503).";

/** What a dependency's reported state means, in the API's own terms (core.py `health`, redis_client.py). */
function explain(d: Dependency): string {
  const unknown = d.state
    ? `Reported by the API as “${d.state}”. This screen has no description for this ${KNOWN[d.key] ? "state" : "component"}.`
    : "The API reported this component without a state.";
  if (d.key === "application") return d.state === "healthy" ? "The API process answered this request." : unknown;
  if (d.key === "postgresql") {
    if (d.state === "healthy") return "A live query succeeded.";
    if (d.state === "unavailable") return "A live query failed. PostgreSQL holds the platform's records, so the API reports the platform Unavailable (HTTP 503) until it can be reached again.";
    return unknown;
  }
  if (d.key === "redis") {
    const required = d.required === true;
    switch (d.state) {
      case "healthy": return "Redis answered a live PING, so the cache is in use.";
      case "disabled": return "Redis is not in use: no Redis connection is configured for this deployment, so reads go straight to PostgreSQL. "
        + "A disabled cache never lowers the overall status." + (required ? " Redis is marked required, but that setting only applies once Redis is configured." : "");
      case "unavailable": return "Redis is configured but did not answer a live PING. It is checked again on every health check. "
        + (required ? REQUIRED_SO_UNAVAILABLE : FALLBACK_TO_POSTGRES);
      case "misconfigured": return "Redis is configured but cannot be used with the current settings or installation (for example an invalid setting or a missing "
        + "client library). The exact cause is written to the API server's log only. " + (required ? REQUIRED_SO_UNAVAILABLE : FALLBACK_TO_POSTGRES);
    }
  }
  return unknown;
}

/** Reported details beyond the state, explained. Only fields present in the answer are shown. */
function facts(d: Dependency): [ReactNode, ReactNode][] {
  const out: [ReactNode, ReactNode][] = [];
  if (d.reason) out.push(["Reason reported", <span key="r" className="break-words">{d.reason}</span>]);
  if (d.required !== undefined) {
    out.push(["Required", d.key !== "redis" ? (d.required ? "Yes" : "No") : d.required
      ? "Yes. If Redis is unreachable or misconfigured, the API reports Unavailable (HTTP 503)."
      : "No. If Redis is unreachable or misconfigured, reads fall back to PostgreSQL and the API reports Degraded."]);
  }
  if (d.tls !== undefined) out.push(["TLS", d.tls ? "On, with the certificate and host name verified" : "Off"]);
  return out;
}

/* ------------------------------------------------------------------------------------------------------ the page */
export function PlatformServiceHealth() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  const { check, busy, run } = useHealthCheck();

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-[26px] font-bold leading-tight tracking-tight text-fg sm:text-[32px]">{TITLE}</h1>
          <p className="mt-2 max-w-3xl text-[15px] text-fg-secondary">
            Whether this deployment&apos;s API, its PostgreSQL database and its Redis cache are working, read live from the API&apos;s health check.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 lg:justify-end">
          <p className="type-caption text-fg-muted">
            {check && check.kind !== "report" ? "Last attempt" : "Last checked"}:{" "}
            {check
              ? <time dateTime={check.at} title="Your browser's clock, when the answer arrived" className="font-semibold tabular-nums text-fg-secondary">{fmtUtcSeconds(check.at)}</time>
              : <span className="font-semibold text-fg-secondary">checking…</span>}
          </p>
          <Button variant="secondary" icon={RefreshCw} loading={busy} onClick={() => void run()}>{busy ? "Checking…" : "Check again"}</Button>
        </div>
      </div>
      <p role="status" aria-live="polite" className="sr-only">{busy ? "" : announcement(check)}</p>

      {/* The PlaneBanner layout, stacked on phones so the text keeps its width beside the wide plane badge. */}
      <div className={`flex flex-col gap-2 rounded-card border border-l-4 border-line bg-surface-card/70 px-4 py-3 md:flex-row md:items-start md:gap-4 ${PLANES.platform.edge}`}>
        <span className="shrink-0"><PlaneBadge plane="platform" withWho /></span>
        <p className="min-w-0 flex-1 type-caption text-fg-secondary">
          <span className="font-semibold text-fg">Deployment health only.</span> This page reads the health check of the API serving this
          deployment. It shows the state of the API and its dependencies, never an organisation&apos;s data.
        </p>
      </div>

      <div aria-busy={busy || undefined} className="space-y-6">
        {!check ? <Loading /> : check.kind === "report" ? <Result check={check} /> : <Failure check={check} busy={busy} onRetry={() => void run()} />}
      </div>

      <NotCovered />

      <p className="max-w-4xl type-caption text-fg-muted">
        Source: <code className="type-code">{SOURCE}</code>, which needs no sign-in. Checked when this page opens and when you select Check again;
        the page never refreshes on its own. Times and response times are measured by your browser.
      </p>
    </div>
  );
}

function announcement(check: Check | null): string {
  if (!check) return "";
  if (check.kind === "report") return `Service health checked: overall ${overallLook(check.report.status).label}.`;
  if (check.kind === "no-response") return check.timedOut ? "Health check failed: the API did not answer in time." : "Health check failed: could not connect to the API.";
  return "Health check failed: the API answered without a health report.";
}

/* ---------------------------------------------------------------------------------------------------- the report */
function Result({ check }: { check: Answered }) {
  const { report, http503 } = check;
  const look = overallLook(report.status);
  const deps = report.dependencies ?? [];
  const problems = deps.filter(isProblem);
  const summaryId = useId();
  const componentsId = useId();
  const headline = report.status === "ok" ? "Every dependency in use is healthy."
    : report.status === "degraded" ? "The API is serving requests, but a non-essential dependency cannot be used."
    : report.status === "unavailable" ? `The API answered${http503 ? " with HTTP 503" : ""} because an essential dependency cannot be used.`
    : `The API reported the status “${report.status}”, which this screen does not recognise.`;

  return (
    <>
      <section aria-labelledby={summaryId} className={PANEL}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <StatusMark status={look.status} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id={summaryId} className={H2}>Overall: <span className={STATUS_TEXT[look.status]}>{look.label}</span></h2>
              <LiveTag />
            </div>
            <p className="mt-1 type-secondary text-fg-secondary">{headline}</p>
          </div>
        </div>
        <DescriptionList columns={3} className="mt-5 border-t border-line pt-5" items={[
          ["Reported status", <span key="s"><code className="type-code">{report.status}</code>{http503 && <span className="text-fg-secondary"> · HTTP 503</span>}</span>],
          ["Cell", report.cellId ? <code key="c" className="break-all type-code">{report.cellId}</code> : "Not reported"],
          ["Dictionary version", report.dictionaryVersion ? <code key="d" className="break-all type-code">{report.dictionaryVersion}</code> : "Not reported"],
          ["Response time (browser-measured)", <span key="m"><span className="tabular-nums">{check.ms.toLocaleString("en-GB")} ms</span>
            <span className="block type-caption text-fg-muted">From sending the request to receiving the answer, network time included. Not the server&apos;s processing time.</span></span>],
          ["Checked", <span key="t"><time dateTime={check.at} className="tabular-nums">{fmtUtcSeconds(check.at)}</time>
            <span className="block type-caption text-fg-muted">Your browser&apos;s clock, when the answer arrived. The API reports no check time.</span></span>],
        ]} />
      </section>

      {look.status !== "verified" && (
        <Callout tone={look.status === "critical" ? "critical" : "warning"}
          title={report.status === "degraded" ? "Degraded" : report.status === "unavailable" ? "Unavailable" : "Status not recognised"}>
          {problems.length > 0 && (
            <ul className="mb-1.5 list-disc space-y-0.5 pl-5">
              {problems.map((d) => <li key={d.key}><span className="font-semibold text-fg">{nameOf(d.key)}</span>: {dependencyLook(d).label}{d.reason ? ` (${d.reason})` : ""}</li>)}
            </ul>
          )}
          {report.status === "degraded" ? "Requests keep working with reduced capability. Each component below says what its state means."
            : report.status === "unavailable" ? "Requests that need the unavailable dependency fail until it recovers. Check again once it has been restored."
            : "The components below are shown as the API reported them."}
        </Callout>
      )}

      <section aria-labelledby={componentsId} className="space-y-3">
        <div>
          <h2 id={componentsId} className={H2}>Components</h2>
          <p className="type-caption text-fg-muted">Each dependency the API checked, as reported in this answer.</p>
        </div>
        {deps.length === 0 ? (
          <div className={CARD}>
            <EmptyState icon={Boxes} title="No component details in this answer" compact
              description="The API reported an overall status but no components, so individual services cannot be shown. The overall status above is as reported." />
          </div>
        ) : (
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {deps.map((d) => <DependencyCard key={d.key} d={d} />)}
          </ul>
        )}
      </section>
    </>
  );
}

function DependencyCard({ d }: { d: Dependency }) {
  const known = KNOWN[d.key];
  const Icon = known?.icon ?? Boxes;
  const items = facts(d);
  return (
    <li className={`flex flex-col ${CARD}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control border border-line bg-surface-raised text-fg-secondary"><Icon size={17} /></span>
          <div className="min-w-0">
            <h3 className="type-card text-fg">{nameOf(d.key)}</h3>
            <code className="type-code text-fg-muted">{d.key}</code>
          </div>
        </div>
        <HealthBadge look={dependencyLook(d)} raw={d.state} />
      </div>
      <p className="mt-3 type-secondary text-fg-secondary">{explain(d)}</p>
      {known && <p className="mt-2 type-caption text-fg-muted">{known.role}</p>}
      {items.length > 0 && <DescriptionList columns={1} className="mt-4 border-t border-line pt-4" items={items} />}
    </li>
  );
}

/** A reported state in its colour family, with an icon and a label (colour is never the only signal). */
function HealthBadge({ look, raw }: { look: Look; raw: string }) {
  const Icon = STATUS_ICON[look.status];
  return (
    <span title={raw ? `Reported as “${raw}”` : "No state reported"}
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-chip border px-2 py-0.5 type-caption font-semibold ${STATUS_TONE[look.status]} ${look.status === "unknown" ? "border-dashed" : ""}`}>
      <Icon size={12} strokeWidth={2.5} aria-hidden className="shrink-0" />{look.label}
    </span>
  );
}

/** Marks values read from this deployment on the last check. */
function LiveTag() {
  return (
    <span title="Read from this deployment on the last check"
      className="inline-flex shrink-0 items-center rounded-chip border border-status-verified-line bg-status-verified-tint px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-status-verified">
      Live<span className="sr-only"> data read from this deployment</span>
    </span>
  );
}

/* --------------------------------------------------------------------------------------------- loading, failure */
function Loading() {
  return (
    <div role="status" aria-label="Checking service health" className="space-y-6">
      <div className={PANEL}>
        <div className="flex items-start gap-4">
          <Skeleton className="h-10 w-10" />
          <div className="flex-1 space-y-2"><Skeleton className="h-6 w-48" /><Skeleton className="h-4 w-2/3" /></div>
        </div>
        <div className="mt-5 grid gap-4 border-t border-line pt-5 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4].map((i) => <div key={i} className="space-y-1.5"><Skeleton className="h-3 w-24" /><Skeleton className="h-4 w-32" /></div>)}
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className={`space-y-3 ${CARD}`}>
            <div className="flex items-center justify-between gap-3"><Skeleton className="h-9 w-40" /><Skeleton className="h-5 w-20" /></div>
            <Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-3/4" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** No health report: unlike an Unavailable answer (the API answered and named the failing dependency), nothing is
 *  known about the API, PostgreSQL or Redis. */
function Failure({ check, busy, onRetry }: { check: Exclude<Check, Answered>; busy: boolean; onRetry: () => void }) {
  const noResponse = check.kind === "no-response";
  const title = noResponse
    ? (check.timedOut ? `The API did not answer within ${TIMEOUT_MS / 1000} seconds` : "Could not connect to the API")
    : "The API answered without a health report";
  const detail = noResponse
    ? (check.timedOut ? "The health check was stopped after waiting. The API may be overloaded or stuck." : errText(check.error))
    : check.httpStatus
      ? `The answer was HTTP ${check.httpStatus}, but it did not contain a health report (for example, an error page from a proxy or gateway in front of the API).`
      : "The answer was not a health report.";
  const reference = check.error instanceof ApiError ? check.error.reference : undefined;
  return (
    <div role="alert" className={`${PANEL} flex flex-col items-center px-6 py-10 text-center`}>
      <StatusMark status="critical" size="lg" />
      <h2 className={`mt-3 ${H2}`}>{title}</h2>
      <p className="mt-1 max-w-xl type-secondary text-fg-secondary">{detail}</p>
      <p className="mt-2 max-w-xl type-caption text-fg-muted">
        The state of the API, PostgreSQL and Redis is unknown. This is not an Unavailable result: that comes from the API itself, which
        answers HTTP 503 and names the dependency that is down.
      </p>
      {reference && <p className="mt-1 type-caption text-fg-muted">{reference.label}: <span className="select-all type-code">{reference.value}</span></p>}
      <p className="mt-2 type-caption text-fg-muted">
        {noResponse ? "Gave up" : "Answered"} after <span className="tabular-nums">{check.ms.toLocaleString("en-GB")} ms</span> (browser-measured) ·{" "}
        <time dateTime={check.at} className="tabular-nums">{fmtUtcSeconds(check.at)}</time>
      </p>
      <Button variant="secondary" size="sm" icon={RefreshCw} loading={busy} onClick={onRetry} className="mt-4">{busy ? "Checking…" : "Try again"}</Button>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- not covered */
const NOT_COVERED: { title: string; icon: LucideIcon; text: string }[] = [
  { title: "Background workers and queues", icon: Workflow, text: "The health check reports no worker or queue, and the backend has no background worker, job queue or scheduler to check." },
  { title: "Connector runtime health", icon: Plug, text: "Recorded by hand in the connector registry, not measured, and not served to the Super Admin role." },
  { title: "Other cells", icon: Globe2, text: "Only the cell of the API that answered is reported (its cell ID). No endpoint lists other cells or their health to this role." },
  { title: "Other API instances", icon: Network, text: "Each check reaches one API instance. If several run behind a load balancer, the others are not checked." },
  { title: "Uptime, history and incidents", icon: History, text: "Each check is a single reading taken now. The platform records no health history, uptime or incidents." },
  { title: "Server-side timing", icon: Timer, text: "The API reports no check time or processing time. The times on this page come from your browser." },
];

function NotCovered() {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={headingId} className={H2}>Not covered by this check</h2>
        <span className="inline-flex items-center gap-1 rounded-chip border border-line bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted">
          <CircleOff size={11} aria-hidden />Not available
        </span>
      </div>
      <p className="mt-1 max-w-3xl type-caption text-fg-muted">
        Limits of {SOURCE}. These are gaps, not signals: nothing here is live data or a sample.
      </p>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {NOT_COVERED.map((n) => (
          <li key={n.title} className="flex gap-3 rounded-card border border-dashed border-line-strong p-4">
            <n.icon size={17} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <p className="type-secondary font-semibold text-fg-secondary">{n.title}</p>
              <p className="mt-0.5 type-caption text-fg-muted">{n.text}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
