"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { CircleOff, Fingerprint, Library, PencilOff, PlugZap, RefreshCw, ScrollText } from "lucide-react";
import { Alert, Button, EmptyState, ErrorState, Skeleton } from "@/components/ds";
import { RegistryHeading, RegistryScope, RegistrySection } from "@/components/PlatformAdmin";
import { useSyntheticDataBadge } from "@/components/Shell";
import { count, fmtUtcSeconds } from "@/components/platformQueue";
import { usePageTitle } from "@/components/ui";
import { errText } from "@/lib/api";
import { checksBySource } from "./shared";
import { SourceDetails } from "./SourceDetails";
import { SourceIntegrityAlerts } from "./SourceIntegrityAlerts";
import { SourceInventory } from "./SourceInventory";
import { SourceSummaryCards } from "./SourceSummaryCards";
import { useSourceIntegrityData } from "./useSourceIntegrityData";
import { DEMO_NOTICE } from "./VerificationResult";
import { VerificationHistory } from "./VerificationHistory";

/* Source Integrity, for the Zoiko Super Admin (Platform Control Plane): the authenticity, verification status, version
   changes and check history of the regulatory sources the platform distributes.

   FRONTEND ONLY, ON DEMONSTRATION DATA. Every figure comes from sourceIntegrityMockData.ts through
   useSourceIntegrityData.ts, which makes no network request. No catalogue, release, verification, audit, evidence,
   ledger, package, outbox or trust endpoint is called, no checksum or signature is computed, and nothing is recorded.
   Actions that would need the backend (running a verification) are shown disabled with the reason. */
const TITLE = "Source Integrity";

export function PlatformSourceIntegrity() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  useSyntheticDataBadge();  // the header's "Synthetic demo data" badge, while this page is open

  const { data, error, loading, loadedAt, reload } = useSourceIntegrityData();
  const [openId, setOpenId] = useState<string | null>(null);
  const sources = useMemo(() => data?.sources ?? [], [data]);
  const checks = useMemo(() => data?.checks ?? [], [data]);
  const sourceMap = useMemo(() => new Map(sources.map((s) => [s.id, s])), [sources]);
  const bySource = useMemo(() => checksBySource(checks), [checks]);
  const checksFor = useCallback((id: string) => bySource.get(id) ?? [], [bySource]);
  const open = openId ? sourceMap.get(openId) : undefined;
  const firstLoad = loading && !data;

  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title={TITLE}
        actions={<div className="flex flex-wrap items-center gap-x-4 gap-y-2 xl:justify-end">
          <p className="type-caption text-fg-muted">
            Demonstration data loaded:{" "}
            {loadedAt
              ? <time dateTime={loadedAt} className="font-semibold tabular-nums text-fg-secondary">{fmtUtcSeconds(loadedAt)}</time>
              : <span className="font-semibold text-fg-secondary">{loading ? "loading…" : "not yet"}</span>}
          </p>
          <Button variant="secondary" icon={RefreshCw} loading={loading} onClick={reload}>{loading ? "Loading…" : "Reload demo data"}</Button>
        </div>}>
        <p>Monitor regulatory source authenticity, verification status, version changes and integrity-check history across the platform.</p>
      </RegistryHeading>
      <p role="status" aria-live="polite" className="sr-only">
        {loading ? "" : error ? "The demonstration data could not be loaded." : data ? `Demonstration data loaded: ${count(sources.length, "source")} and ${count(checks.length, "check")}.` : ""}
      </p>

      <RegistryScope>
        <span className="font-semibold text-fg">{DEMO_NOTICE}</span> Every source, publisher, version, checksum and check on this page is
        fictional sample data held in the frontend. Nothing is read from this deployment, no verification runs from this screen, and nothing
        is recorded in any audit trail.
      </RegistryScope>

      {firstLoad ? <IntegrityLoading /> : !data ? (
        <RegistrySection title="Source inventory">
          <ErrorState error={error} title="The demonstration data could not be loaded" onRetry={reload} />
        </RegistrySection>
      ) : (
        <div aria-busy={loading || undefined} className="space-y-6">
          {!!error && (
            <Alert tone="critical" live="assertive" title="The demonstration data could not be loaded again">
              {errText(error)} The records below are from the last successful load{loadedAt ? `, ${fmtUtcSeconds(loadedAt)}` : ""}.
            </Alert>
          )}
          {sources.length === 0 ? (
            <RegistrySection title="Source inventory">
              <EmptyState icon={Library} title="No regulatory sources to show"
                description="Sources and their integrity checks appear here once they are registered. (This is the empty state, previewed with demonstration data.)" />
            </RegistrySection>
          ) : (
            <>
              <SourceSummaryCards sources={sources} checks={checks} />
              <SourceIntegrityAlerts sources={sources} checksFor={checksFor} onOpen={setOpenId} />
              <SourceInventory sources={sources} onOpen={setOpenId} />
              <VerificationHistory checks={checks} sources={sourceMap} onOpenSource={setOpenId} />
            </>
          )}
        </div>
      )}

      <NotConnected />

      {open && <SourceDetails key={open.id} source={open} checks={checksFor(open.id)} onClose={() => setOpenId(null)} />}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ not connected */
const NOT_CONNECTED: { title: string; icon: LucideIcon; text: string }[] = [
  { title: "Live verification", icon: PlugZap, text: "No check can be started here. Run verification is disabled until live verification is connected." },
  { title: "Checksum and signature checks", icon: Fingerprint, text: "The screen never computes a checksum or checks a signature. Every outcome shown is written into the demonstration data." },
  { title: "Audit and evidence records", icon: ScrollText, text: "Checks shown here are not recorded in any audit trail, and no customer evidence, ledger or package is read." },
  { title: "Source management", icon: PencilOff, text: "Sources cannot be added, edited, retired or re-checked from this screen." },
];

function NotConnected() {
  return (
    <RegistrySection title="Not connected"
      aside={<span className="inline-flex items-center gap-1 rounded-chip border border-line bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted"><CircleOff size={11} aria-hidden />Not available</span>}
      description="What this demonstration does not do. These are gaps, not values: nothing here is live data.">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {NOT_CONNECTED.map((n) => (
          <li key={n.title} className="flex gap-3 rounded-card border border-dashed border-line-strong p-4">
            <n.icon size={17} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <p className="type-secondary font-semibold text-fg-secondary">{n.title}</p>
              <p className="mt-0.5 type-caption text-fg-muted">{n.text}</p>
            </div>
          </li>
        ))}
      </ul>
    </RegistrySection>
  );
}

/* --------------------------------------------------------------------------------------------------- loading */
function IntegrityLoading() {
  return (
    <div role="status" aria-label="Loading the demonstration data" className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className={`space-y-3 rounded-card border border-line bg-surface-card p-5 ${i === 4 ? "sm:col-span-2" : ""}`}>
            <Skeleton className="h-4 w-32" /><Skeleton className="h-8 w-16" /><Skeleton className="h-3.5 w-3/4" />
          </div>
        ))}
      </div>
      <div className="rounded-panel border border-line bg-surface-card p-5 sm:p-6">
        <Skeleton className="h-5 w-56" />
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="mt-4 h-10 w-full" />)}
      </div>
    </div>
  );
}
