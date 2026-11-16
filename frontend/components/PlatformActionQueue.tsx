"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity, ArrowRight, Bot, CircleAlert, CircleOff, Eye, FileCheck2, FileText, KeyRound, RefreshCw,
  ServerCog, ShieldAlert, Siren, Workflow,
} from "lucide-react";
import {
  Alert, Button, EmptyState, ErrorState, KpiCard, PLANES, PlaneBadge, SearchInput, Select, Skeleton, TabPanel, Tabs, buttonClasses,
} from "./ds";
import {
  CATEGORY, CLASSIFICATION, CategoryBadge, ClassificationBadge, REGISTRY, SESSIONS, count, errorText, failedSources, fmtUtcSeconds, usePlatformQueue,
  type Category, type Classification, type PlatformQueue, type QueueItem as Item,
} from "./platformQueue";
import { usePageTitle } from "./ui";

/* The Platform Action Queue page (Milestone 3, Phase 1): the whole queue, with its category tabs, classification
   filter, organisation-name search, data sources and the areas not monitored yet. What is in the queue, and in what
   order, is decided in one place, components/platformQueue.tsx (its two reads, three rules, order and refresh), which
   the Platform Command Center shares. The page never refreshes itself, because every registry read is an audit record. */
const TITLE = "Action Queue";

/* --------------------------------------------------------------------------------------------------------- the page */
const PANEL = "rounded-panel border border-line bg-surface-raised/70 p-5 shadow-panel sm:p-6";
const H2 = "text-[18px] font-semibold leading-7 text-fg";

export function PlatformActionQueue() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  const q = usePlatformQueue();
  const { items, firstLoad } = q;
  const action = items.filter((i) => i.classification === "ACTION_REQUIRED").length;
  const review = items.length - action;
  const otherSessions = q.sessions?.filter((s) => !s.current).length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-[26px] font-bold leading-tight tracking-tight text-fg sm:text-[32px]">{TITLE}</h1>
          <p className="mt-2 max-w-3xl text-[15px] text-fg-secondary">
            Platform conditions that need the Super Admin, worked out from the organisation registry and your own sessions each time
            you refresh.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 lg:justify-end">
          <p className="type-caption text-fg-muted">
            Last successful refresh:{" "}
            {q.lastSuccess
              ? <time dateTime={q.lastSuccess} className="font-semibold tabular-nums text-fg-secondary">{fmtUtcSeconds(q.lastSuccess)}</time>
              : <span className="font-semibold text-fg-secondary">{q.loading ? "loading…" : "not yet"}</span>}
          </p>
          <Button variant="secondary" icon={RefreshCw} loading={q.loading} onClick={() => void q.refresh()}>
            {q.loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </div>
      <p role="status" aria-live="polite" className="sr-only">{q.announcement}</p>

      {/* The PlaneBanner layout, stacked on phones so the text keeps its width beside the wide plane badge. */}
      <div className={`flex flex-col gap-2 rounded-card border border-l-4 border-line bg-surface-card/70 px-4 py-3 md:flex-row md:items-start md:gap-4 ${PLANES.platform.edge}`}>
        <span className="shrink-0"><PlaneBadge plane="platform" withWho /></span>
        <p className="min-w-0 flex-1 type-caption text-fg-secondary">
          <span className="font-semibold text-fg">Cross-tenant metadata only.</span> This queue reads organisation registry metadata (name,
          status, legal entities, Organisation Admins) and your own sign-in sessions. It never reads or shows customer evidence, facts,
          assurance records or an organisation&apos;s own audit content.
        </p>
      </div>

      <ul aria-label="Action Queue summary" className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <li><KpiCard label="Action required" tone="warning" icon={CircleAlert} loading={firstLoad}
          value={q.orgs ? action : "—"} hint={q.orgs ? "Active organisations without an Organisation Admin" : "Registry not loaded"} /></li>
        <li><KpiCard label="Review" tone="info" icon={Eye} loading={firstLoad}
          value={q.orgs || q.sessions ? review : "—"} hint="Suspended organisations and your other sessions" /></li>
        <li><KpiCard label="Checked" tone="neutral" icon={Activity} loading={firstLoad}
          value={q.orgs ? count(q.orgs.length, "organisation") : "—"}
          hint={q.sessions ? `Plus ${count(q.sessions.length, "live session")} of your account (${otherSessions} besides this one)` : "Your sessions not loaded"} /></li>
      </ul>

      <QueuePanel items={items} q={q} firstLoad={firstLoad} />
      <NotMonitored />
    </div>
  );
}

/* ----------------------------------------------------------------------------------------------------- queue panel */
type View = "all" | Category;

function QueuePanel({ items, q, firstLoad }: { items: Item[]; q: PlatformQueue; firstLoad: boolean }) {
  const headingId = useId();
  const [view, setView] = useState<View>("all");
  const [klass, setKlass] = useState<"all" | Classification>("all");
  const [text, setText] = useState("");
  const needle = text.trim().toLowerCase();
  const filtered = (cat: View) => items.filter((i) => (cat === "all" || i.category === cat)
    && (klass === "all" || i.classification === klass)
    && (!needle || (i.organisation?.name.toLowerCase().includes(needle) ?? false)));
  const shown = filtered(view);
  const filtering = klass !== "all" || !!needle || view !== "all";
  const clear = () => { setView("all"); setKlass("all"); setText(""); };
  const failed = failedSources(q);
  const nothingLoaded = !q.orgs && !q.sessions;

  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={headingId} className={H2}>Queue</h2>
          <LiveTag />
        </div>
        <p className="type-caption text-fg-muted" aria-live="polite">{firstLoad ? "Loading…" : `${count(shown.length, "item")} shown of ${items.length}`}</p>
      </div>
      <p className="mt-1 max-w-3xl type-caption text-fg-muted">
        <span className="font-semibold text-fg-secondary">{CLASSIFICATION.ACTION_REQUIRED.label}</span>: {CLASSIFICATION.ACTION_REQUIRED.meaning}.{" "}
        <span className="font-semibold text-fg-secondary">{CLASSIFICATION.REVIEW.label}</span>: {CLASSIFICATION.REVIEW.meaning}.
        Items are listed while their condition holds; nothing here is assigned, scheduled or stored.
      </p>

      {/* Tabs on their own row until the content area is wide enough for tabs, filter and search side by side. */}
      <div className="mt-5 flex flex-col gap-3 2xl:flex-row 2xl:items-center 2xl:justify-between">
        <Tabs id="aq-view" label="Category" variant="pills" value={view} onChange={(v) => setView(v as View)} items={[
          { value: "all", label: "All", count: filtered("all").length },
          { value: "tenant", label: CATEGORY.tenant.label, icon: CATEGORY.tenant.icon, count: filtered("tenant").length },
          { value: "account", label: CATEGORY.account.label, icon: CATEGORY.account.icon, count: filtered("account").length },
        ]} />
        <div className="grid gap-3 sm:grid-cols-2 2xl:w-136 2xl:shrink-0">
          <Select aria-label="Classification" value={klass} onChange={(e) => setKlass(e.target.value as "all" | Classification)} options={[
            { value: "all", label: "All classifications" },
            { value: "ACTION_REQUIRED", label: CLASSIFICATION.ACTION_REQUIRED.label },
            { value: "REVIEW", label: CLASSIFICATION.REVIEW.label },
          ]} />
          <SearchInput aria-label="Search organisation name" placeholder="Search organisation name" value={text}
            onChange={(e) => setText(e.target.value)} onClear={() => setText("")} />
        </div>
      </div>
      {needle && <p className="mt-2 type-caption text-fg-muted">The search matches organisation names, so items about your own account are hidden while it is set.</p>}

      {failed.length > 0 && !nothingLoaded && (
        <div className="mt-4 space-y-2">
          {failed.map((f) => (
            <Alert key={f.name} tone="critical" title={`${f.name} could not be loaded`}>
              <p>{errorText(f.error)} ({f.source}). Its items are not shown, so this list is incomplete.</p>
              <div className="mt-2"><Button size="sm" variant="secondary" icon={RefreshCw} loading={q.loading} onClick={() => void q.refresh()}>Retry</Button></div>
            </Alert>
          ))}
        </div>
      )}

      <TabPanel id="aq-view" value={view} className="mt-4 focus-visible:outline-offset-4">
        {firstLoad ? <LoadingList /> : nothingLoaded && failed.length > 0 ? (
          <ErrorState error={failed[0].error} title="The Action Queue could not be loaded" onRetry={() => void q.refresh()} />
        ) : shown.length === 0 ? (
          filtering && items.length > 0 ? (
            <EmptyState title="No items match these filters" description="Change the category, classification or search."
              action={<Button size="sm" variant="secondary" onClick={clear}>Clear filters</Button>} />
          ) : failed.length > 0 ? (
            <EmptyState title="No items from the sources that loaded"
              description="This list is incomplete until the source that failed loads. Use Retry above." />
          ) : (
            <EmptyState icon={Eye} title="Nothing needs attention"
              description="Checked on the last refresh: active organisations without an Organisation Admin, suspended organisations and other sessions on your account." />
          )
        ) : (
          <ul aria-label="Action Queue items" className="space-y-3">
            {shown.map((i) => <li key={i.key}><QueueItem item={i} /></li>)}
          </ul>
        )}
      </TabPanel>

      <Sources q={q} />
    </section>
  );
}

function QueueItem({ item }: { item: Item }) {
  const titleId = useId();
  const hintId = useId();
  return (
    <article aria-labelledby={titleId} className="rounded-card border border-line bg-surface-card p-4 shadow-card sm:p-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between md:gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <ClassificationBadge classification={item.classification} />
            <CategoryBadge category={item.category} />
          </div>
          <h3 id={titleId} className="mt-2.5 text-[15px] font-semibold leading-6 text-fg">{item.title}</h3>
          {item.organisation && (
            <p className="mt-0.5 type-secondary text-fg-secondary">Organisation: <span className="font-semibold text-fg">{item.organisation.name}</span></p>
          )}
          <p className="mt-2 type-secondary text-fg-secondary"><span className="font-semibold text-fg">Why it appears: </span>{item.why}</p>
          <div className="mt-3 rounded-control border border-line bg-fg/3 px-3 py-2.5">
            <p className="type-caption text-fg-secondary"><span className="font-semibold text-fg">Rule: </span>{item.rule}</p>
            <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2 xl:grid-cols-4">
              {item.observed.map(([k, v]) => (
                <div key={k} className="min-w-0"><dt className="type-caption text-fg-muted">{k}</dt><dd className="type-caption font-medium text-fg">{v}</dd></div>
              ))}
            </dl>
            <p className="mt-2 type-caption text-fg-muted">Source: <code className="type-code">{item.source}</code>, read live</p>
          </div>
        </div>
        <div className="shrink-0 md:w-60">
          <Link href={item.action.href} aria-describedby={hintId} className={`${buttonClasses({ variant: "primary", size: "sm" })} w-full md:w-auto`}>
            {item.action.label}{item.organisation && <span className="sr-only"> for {item.organisation.name}</span>}
            <ArrowRight size={14} aria-hidden />
          </Link>
          <p id={hintId} className="mt-2 type-caption text-fg-muted">{item.action.hint}</p>
        </div>
      </div>
    </article>
  );
}

function LoadingList() {
  return (
    <div role="status" aria-label="Loading the Action Queue" className="space-y-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-card border border-line bg-surface-card p-5">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="mt-3 h-4 w-3/4" />
          <Skeleton className="mt-2 h-4 w-1/2" />
        </div>
      ))}
    </div>
  );
}

function Sources({ q }: { q: PlatformQueue }) {
  const rows: [string, string, string][] = [
    ["Organisation registry", REGISTRY, q.orgError ? "not loaded" : q.orgs ? count(q.orgs.length, "organisation") : "loading"],
    ["Your sessions", SESSIONS, q.sessionError ? "not loaded" : q.sessions ? count(q.sessions.length, "live session") : "loading"],
  ];
  return (
    <div className="mt-6 border-t border-line pt-4">
      <h3 className="type-label text-fg-muted">Data sources</h3>
      <ul className="mt-2 space-y-1">
        {rows.map(([name, source, state]) => (
          <li key={name} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 type-caption text-fg-secondary">
            <span className="font-semibold text-fg">{name}</span><code className="type-code text-fg-muted">{source}</code><span aria-hidden>·</span><span>{state}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 max-w-3xl type-caption text-fg-muted">
        Read on opening this page and on Refresh only. The page never refreshes itself: each registry read is recorded in the platform audit
        trail as platform.organisation_registry_read.
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------- not monitored yet */
const NOT_MONITORED: { title: string; icon: LucideIcon; text: string }[] = [
  { title: "Platform incidents and alerts", icon: Siren, text: "No incident or alert model exists in the platform." },
  { title: "Service and cell health", icon: ServerCog, text: "GET /health checks the API, its PostgreSQL database and, when configured, Redis (see Service Health), but adds no items to this queue. No other services or cells are checked." },
  { title: "Organisation Admin first sign-in", icon: KeyRound, text: "Whether a newly appointed admin has replaced their temporary password is not served to the Super Admin role." },
  { title: "Platform security events", icon: ShieldAlert, text: "Failed sign-ins, lockouts and refused requests are recorded in the audit trail, but no Super Admin view of them exists." },
  { title: "Content release integrity", icon: FileCheck2, text: "Release signature verification exists for catalogue readers, but is not served to the Super Admin role." },
  { title: "Regulatory source intake", icon: FileText, text: "Source ingestion belongs to content operations and is not served to the Super Admin role." },
  { title: "Agent action holds", icon: Bot, text: "No platform-level queue of held agent actions exists." },
  { title: "Privileged access requests", icon: KeyRound, text: "No just-in-time, break-glass or support-access model exists." },
  { title: "Integration jobs and event delivery", icon: Workflow, text: "No job model or event-delivery worker exists. Connector health is set by hand and not served to this role." },
];

function NotMonitored() {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={headingId} className={H2}>Not monitored yet</h2>
        <span className="inline-flex items-center gap-1 rounded-chip border border-line bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted">
          <CircleOff size={11} aria-hidden />Not available
        </span>
      </div>
      <p className="mt-1 max-w-3xl type-caption text-fg-muted">
        Areas of the platform design that have no data source this role can read. They are limitations, not signals: nothing here is live
        data or a sample, and none of them can add an item to the queue.
      </p>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {NOT_MONITORED.map((n) => (
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

/** Marks figures read from this deployment on the last refresh. */
function LiveTag() {
  return (
    <span title="Read from this deployment on the last refresh"
      className="inline-flex shrink-0 items-center rounded-chip border border-status-verified-line bg-status-verified-tint px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-status-verified">
      Live<span className="sr-only"> data read from this deployment</span>
    </span>
  );
}
