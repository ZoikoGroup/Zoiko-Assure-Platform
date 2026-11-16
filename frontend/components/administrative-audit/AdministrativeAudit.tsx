"use client";

import { useEffect, useMemo, useState } from "react";
import { Lock, ScrollText } from "lucide-react";
import { Alert, EmptyState, ErrorState, Field, Select, Skeleton, TabPanel, Tabs } from "@/components/ds";
import { RegistryHeading, RegistryScope, RegistrySection } from "@/components/PlatformAdmin";
import { useSyntheticDataBadge } from "@/components/Shell";
import { DemoTag, useTabInView } from "@/components/platformDemo";
import { usePageTitle } from "@/components/ui";
import { AuditEventDetails } from "./AuditEventDetails";
import { AuditFilters } from "./AuditFilters";
import { AuditExportControls, AuditIntegrityPanel } from "./AuditIntegrityPanel";
import { AuditInventory } from "./AuditInventory";
import { AuditSummaryCards } from "./AuditSummaryCards";
import { AuditTimeline } from "./AuditTimeline";
import { DEFAULT_SORT, NO_FILTERS, applyFilters, buildRows, sortRows, summarise, type Filters, type Scope, type Sort, type SortKey } from "./model";
import { AuditLoadError, previewFromUrl, useAdministrativeAuditData, type PreviewState } from "./useAdministrativeAuditData";

/* Administrative Audit, for the Zoiko Super Admin (Platform Control Plane): a demonstration of exploring
   platform-level and organisation-level administrative events.

   FRONTEND ONLY, ON DEMONSTRATION DATA. Every record comes from auditMockData.ts through useAdministrativeAuditData.ts,
   which makes no network request. No audit, organisation-detail, evidence-ledger, export or verification endpoint is
   called, nothing is recorded, and the live Super Admin backend offers no cross-organisation audit read. Export and
   Verify are shown disabled with the reason. */
const TITLE = "Administrative Audit";
export const DEMO_NOTICE = "Demonstration interface – live platform audit records are not connected.";

const PREVIEWS: { value: PreviewState; label: string }[] = [
  { value: "", label: "Demonstration records" }, { value: "loading", label: "Loading" }, { value: "empty", label: "Empty audit history" },
  { value: "unavailable", label: "API unavailable" }, { value: "denied", label: "Permission denied" },
];
const VIEWS = [{ value: "inventory", label: "Event inventory" }, { value: "timeline", label: "Timeline" }];

export function PlatformAdministrativeAudit() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  useSyntheticDataBadge();  // the header's "Synthetic demo data" badge, while this page is open

  const [preview, setPreview] = useState<PreviewState>(() => previewFromUrl());
  const { data, error, loading, reload } = useAdministrativeAuditData(preview);
  const rows = useMemo(() => (data ? buildRows(data) : []), [data]);
  const summary = useMemo(() => summarise(rows), [rows]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const [order, setOrder] = useState<"newest" | "oldest">("newest");
  const [view, setView] = useState("inventory");
  const [openId, setOpenId] = useState<string | null>(null);
  useTabInView("audit-view", view);
  useTabInView("audit-scope", filters.scope || "ALL");

  const filtered = useMemo(() => applyFilters(rows, filters), [rows, filters]);
  const sorted = useMemo(() => sortRows(filtered, sort), [filtered, sort]);
  const timeline = useMemo(() => sortRows(filtered, { key: "time", dir: order === "oldest" ? "asc" : "desc" }), [filtered, order]);
  // The drawer steps through the list the person is looking at, in the order shown.
  const stepList = view === "timeline" ? timeline : sorted;
  const open = openId ? rows.find((r) => r.e.id === openId) : undefined;
  const at = open ? stepList.findIndex((r) => r.e.id === open.e.id) : -1;
  const related = open?.e.correlation_id ? rows.filter((r) => r.e.correlation_id === open.e.correlation_id && r.e.id !== open.e.id) : [];

  const choosePreview = (v: PreviewState) => {
    setPreview(v);
    setOpenId(null);
    const url = new URL(window.location.href);
    if (v) url.searchParams.set("demo-state", v); else url.searchParams.delete("demo-state");
    window.history.replaceState(window.history.state, "", url);
  };
  const toggleSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "action" ? "asc" : "desc" }));
  const resetFilters = () => setFilters(NO_FILTERS);
  const scopeTabs = [
    { value: "ALL", label: "All events", count: rows.length },
    { value: "PLATFORM", label: "Platform events", count: summary.platform },
    { value: "ORGANISATION", label: "Organisation events", count: summary.organisation },
  ];
  const kind = error instanceof AuditLoadError ? error.kind : error ? "unavailable" : null;

  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title={TITLE}
        actions={<div className="w-full sm:w-64">
          <Field label="Preview state" hint="Demonstration states; nothing is requested">
            <Select value={preview} onChange={(e) => choosePreview(e.target.value as PreviewState)} options={PREVIEWS} />
          </Field>
        </div>}>
        <p>Who did what, to which record, when and why: a demonstration of exploring platform-level and organisation-level administrative events.</p>
      </RegistryHeading>
      <p role="status" aria-live="polite" className="sr-only">
        {loading ? "" : kind === "denied" ? "Permission denied preview." : kind ? "The audit service is unavailable (preview)." : data ? `${rows.length} demonstration audit events loaded.` : ""}
      </p>

      <RegistryScope>
        <span className="font-semibold text-fg">{DEMO_NOTICE}</span> Every record, person and organisation here is fictional demonstration data,
        never read from or written to any audit table. The live Super Admin backend does not provide cross-organisation audit retrieval; this
        screen only illustrates how such records could be explored. Field names follow the audit model; display names, outcome, category and
        review flags are illustrative.
      </RegistryScope>

      {loading && !data && !error ? <AuditLoading /> : kind === "denied" ? (
        <RegistrySection title="Audit events">
          <div role="alert">
            <EmptyState icon={Lock} title="Permission denied"
              description={<>{(error as Error).message} With a live backend, this is what a role without permission to read audit records would see.</>} />
          </div>
        </RegistrySection>
      ) : kind ? (
        <RegistrySection title="Audit events">
          <ErrorState error={error} title="The audit service is unavailable" onRetry={reload} />
        </RegistrySection>
      ) : rows.length === 0 ? (
        <RegistrySection title="Audit events">
          <EmptyState icon={ScrollText} title="No audit events recorded"
            description="Administrative events appear here once they are recorded. (This is the empty audit history, previewed with demonstration data.)" />
        </RegistrySection>
      ) : (
        <>
          <AuditSummaryCards s={summary} />
          <RegistrySection title="Audit events" aside={<DemoTag />}
            description="Search, filter and sort the demonstration records, or follow them on the timeline. The filters apply to both views.">
            <Tabs id="audit-scope" label="Platform and organisation events" variant="pills" value={filters.scope || "ALL"} items={scopeTabs}
              onChange={(v) => setFilters({ ...filters, scope: v === "ALL" ? "" : (v as Scope) })} />
            <div id="audit-scope-panel" role="tabpanel" aria-labelledby={`audit-scope-tab-${filters.scope || "ALL"}`} className="mt-3 space-y-5">
              <Alert tone="info" title="How platform and organisation events are told apart">
                A record with an empty <code className="type-code">tenant_id</code> is platform-level: organisation registry reads, platform staff
                sign-ins and denials, and content and catalogue administration. A record with a <code className="type-code">tenant_id</code> is associated
                with that organisation. When the Super Admin creates, changes or suspends an organisation, the record carries that organisation&apos;s
                <code className="type-code"> tenant_id</code>, so it is listed under organisation events. Categories are an illustrative grouping by action name.
              </Alert>
              <AuditFilters rows={rows} matched={filtered.length} filters={filters} onChange={setFilters} />
              <div>
                <Tabs id="audit-view" label="Audit views" value={view} onChange={setView} items={VIEWS} />
                <TabPanel id="audit-view" value={view} className="rounded-card pt-4">
                  {view === "inventory"
                    ? <AuditInventory rows={sorted} total={rows.length} sort={sort} onSort={toggleSort} onOpen={setOpenId} onReset={resetFilters} />
                    : <AuditTimeline rows={timeline} order={order} onOrder={setOrder} onOpen={setOpenId} onReset={resetFilters} />}
                </TabPanel>
              </div>
            </div>
          </RegistrySection>
        </>
      )}

      <AuditExportControls />
      <AuditIntegrityPanel />

      {open && (
        <AuditEventDetails row={open} position={at + 1} total={stepList.length} related={related}
          onPrev={at > 0 ? () => setOpenId(stepList[at - 1].e.id) : null}
          onNext={at >= 0 && at < stepList.length - 1 ? () => setOpenId(stepList[at + 1].e.id) : null}
          onOpen={setOpenId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}

function AuditLoading() {
  return (
    <div role="status" aria-label="Loading the demonstration audit records" className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="space-y-3 rounded-card border border-line bg-surface-card p-5">
            <Skeleton className="h-4 w-32" /><Skeleton className="h-8 w-16" /><Skeleton className="h-3.5 w-3/4" />
          </div>
        ))}
      </div>
      <div className="rounded-panel border border-line bg-surface-raised/70 p-5 sm:p-6">
        <Skeleton className="h-5 w-56" />
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="mt-4 h-10 w-full" />)}
      </div>
    </div>
  );
}
