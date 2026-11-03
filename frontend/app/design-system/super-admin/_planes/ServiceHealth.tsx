"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button, Card, DataTable, StateBadge, TabPanel, Tabs, WarningPanel, type DataColumn } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample } from "../../_shared/demo";
import { LiveHealthBlock, useLiveHealth } from "./live";

/* Service health. The live block is real (GET /health, /watermark). The service matrix is a proposal on sample data:
   the platform has no service- or component-level health model. It reuses the dictionary's only health vocabulary
   (HEALTHY · DEGRADED · FAILED · DISABLED, from connector runtime health). */

type Svc = { id: string; name: string; component: string; status: "HEALTHY" | "DEGRADED" | "FAILED" | "DISABLED"; availability: string; checked: string; incidents: number; note?: string };
const SERVICES: Svc[] = [
  { id: "api", name: "Platform API", component: "api · eu-west-cell-a", status: "HEALTHY", availability: "99.98%", checked: "2026-11-03T08:55:00Z", incidents: 0 },
  { id: "app", name: "Applicability engine", component: "engines/applicability", status: "HEALTHY", availability: "99.95%", checked: "2026-11-03T08:55:00Z", incidents: 0 },
  { id: "vault", name: "Evidence vault", component: "object store · encryption", status: "HEALTHY", availability: "99.99%", checked: "2026-11-03T08:54:00Z", incidents: 0 },
  { id: "ledger", name: "Evidence ledger", component: "hash chain · checkpoints", status: "DEGRADED", availability: "99.40%", checked: "2026-11-03T08:52:00Z", incidents: 1, note: "Checkpoints wait for a timestamp authority (TSA_PENDING)" },
  { id: "conn", name: "Connector runtime", component: "document-export", status: "FAILED", availability: "96.10%", checked: "2026-11-03T07:40:00Z", incidents: 2, note: "Marked FAILED by its operator" },
  { id: "outbox", name: "Event publisher", component: "outbox", status: "DISABLED", availability: "—", checked: "2026-11-03T08:50:00Z", incidents: 0, note: "Not running in this deployment" },
];

export function ServiceHealth() {
  return (
    <DemoFrame id="sa-health" plane="platform" title="Service health"
      intro="Whether the platform is serving, which components need attention, when each was last checked, and any open incidents."
      app={{ screen: "No service-health screen exists yet", action: "GET /health · GET /watermark (public)", enforced: ["Both endpoints are public and read-only", "Neither checks the database or other services"] }}
      availability={{ level: "partial", note: "Only GET /health and GET /watermark exist; both are shown live. There is no service or component health model, and connector runtime health is set by hand, not measured. The service table is a proposal on sample data." }}
      empty={{ title: "No services registered", description: "Services appear here once the platform reports component health." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const { live, run } = useLiveHealth();
  const [view, setView] = useState("all");
  const attention = SERVICES.filter((s) => s.status !== "HEALTHY");
  const rows = view === "all" ? SERVICES : attention;
  const columns: DataColumn<Svc>[] = [
    { key: "name", header: "Service", sortValue: (s) => s.name, render: (s) => <><span className="block font-medium text-fg">{s.name}</span><code className="type-code text-fg-muted">{s.component}</code></> },
    { key: "status", header: "Status", sortValue: (s) => ["FAILED", "DEGRADED", "DISABLED", "HEALTHY"].indexOf(s.status), render: (s) => <><StateBadge code={s.status} size="sm" />{s.note && <span className="mt-1 block type-caption text-fg-muted">{s.note}</span>}</> },
    { key: "availability", header: "Availability (30 days)", align: "right", sortValue: (s) => parseFloat(s.availability) || -1, render: (s) => <span className="tabular-nums">{s.availability}</span> },
    { key: "incidents", header: "Open incidents", align: "right", sortValue: (s) => s.incidents, render: (s) => s.incidents ? <StateBadge code={`${s.incidents} OPEN`} status="critical" size="sm" /> : <span className="type-caption text-fg-muted">None</span> },
    { key: "checked", header: "Last health check", sortValue: (s) => s.checked, render: (s) => <span className="whitespace-nowrap tabular-nums text-fg-secondary">{fmtDate(s.checked, true)}</span> },
  ];
  return (
    <div className="space-y-stack">
      <Card title="This deployment" description="Checked live when you opened this section" actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={run}>Check again</Button>}>
        <LiveHealthBlock live={live} onRetry={run} />
      </Card>
      {attention.length > 0 && (
        <WarningPanel title={`${attention.length} services need attention (sample)`}>
          {attention.map((s) => `${s.name}: ${s.status}`).join(" · ")}. Degraded services keep serving but may delay checkpoints or connector runs.
        </WarningPanel>
      )}
      <Tabs id="sa-health-view" label="Show services" variant="pills" value={view} onChange={setView}
        items={[{ value: "all", label: "All services", count: SERVICES.length }, { value: "attention", label: "Needs attention", count: attention.length }]} />
      <TabPanel id="sa-health-view" value={view} className="">
        <DataTable caption="Platform services (proposal, sample data)" captionVisible columns={columns} rows={rows} rowKey={(s) => s.id} defaultSort={{ key: "status", dir: "asc" }} minWidth="46rem"
          empty={{ title: "Everything is healthy", description: "No service needs attention." }} />
      </TabPanel>
      <p className="type-caption text-fg-muted">Service rows are samples<Sample />. Use the scenario control above for the loading, empty and error states.</p>
    </div>
  );
}
