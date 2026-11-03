"use client";

import { RefreshCw } from "lucide-react";
import { Alert, Button, Card, KpiCard, StateBadge, Timeline } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";
import { AUDIT, CELLS, TENANTS } from "./data";
import { LiveHealthBlock, useLiveHealth } from "./live";

const ACTION_TEXT: Record<string, string> = {
  "organisation.created": "Organisation registered", "organisation.updated": "Organisation renamed",
  "organisation.status_changed": "Organisation status changed", "organisation.admin_appointed": "Organisation Admin appointed",
};

export function Overview() {
  return (
    <DemoFrame id="sa-overview" plane="platform" title="Platform overview"
      intro="The state of the platform at a glance: how many organisations it hosts and in what status, whether this deployment answers, what needs attention, and what platform administrators did recently."
      app={{ screen: "Super Admin Dashboard", route: "/", action: "GET /platform/organisations", permission: "manage_organisations",
        enforced: ["Super Admin only: a human account outside any organisation, holding manage_organisations", "Returns registry metadata only: no customer evidence, facts, assurance or audit content"] }}
      availability={{ level: "partial", note: "Organisation counts and statuses exist (Super Admin Dashboard). Health is limited to the two live checks shown; platform alerts and a platform event feed are not built, so those panels are samples." }}
      empty={{ title: "No organisations yet", description: "Register the first customer organisation to get started." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const { live, run } = useLiveHealth();
  const active = TENANTS.filter((t) => t.status === "ACTIVE").length;
  const recent = AUDIT.filter((a) => a.action.startsWith("organisation.")).slice().reverse().slice(0, 4);
  return (
    <div className="space-y-stack">
      <div className="grid gap-stack sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Organisations" value={TENANTS.length} tone="info" hint="sample" />
        <KpiCard label="Active" value={active} tone="verified" hint="ACTIVE · users can sign in" />
        <KpiCard label="Suspended" value={TENANTS.length - active} tone="critical" hint="SUSPENDED · sign-in refused" />
        <KpiCard label="Legal entities" value={TENANTS.reduce((n, t) => n + t.entities.length, 0)} hint="across all organisations" />
      </div>

      <div className="grid gap-stack xl:grid-cols-2">
        <Card title="Platform health" description="This deployment, checked now" actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={run}>Check again</Button>}>
          <LiveHealthBlock live={live} onRetry={run} />
          <div className="mt-5"><Section title="Cells">
            <ul className="divide-y divide-line rounded-card border border-line">
              {CELLS.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                  <span><code className="type-code text-fg">{c.id}</code><span className="ml-2 type-caption text-fg-muted">{c.region} · {c.tier}</span></span>
                  <StateBadge code={c.status} status={c.status === "ACTIVE" ? "verified" : "unknown"} size="sm" />
                </li>
              ))}
            </ul>
            <p className="type-caption text-fg-muted">Cells as in the demo seed<Sample />. No endpoint lists cells to the Super Admin today.</p>
          </Section></div>
        </Card>

        <Card title="Platform alerts" description="Sample alerts · no alert model exists yet">
          <div className="space-y-3">
            <Alert tone="critical" title="Connector runtime FAILED: document-export">Set by its operator at 07:40 UTC. Connector health is recorded manually, not measured.</Alert>
            <Alert tone="warning" title="Cell uk-south-cell-b is on STANDBY">New organisations are placed on eu-west-cell-a, the cell this server runs.</Alert>
            <Alert tone="info" title="Content release REL-DEMO-0042 received">Signature verified on receipt. Organisations receive it at their next evaluation.</Alert>
          </div>
          <p className="mt-3 type-caption text-fg-muted">Incidents inside an organisation, such as an evidence integrity failure, stay in that organisation&apos;s workspace and are never shown here.</p>
        </Card>
      </div>

      <div className="grid gap-stack xl:grid-cols-2">
        <Card title="Recent administrative activity" description="From the affected organisations' audit trails">
          <Timeline label="Recent platform administration, newest first" items={recent.map((a) => ({
            id: String(a.seq), title: ACTION_TEXT[a.action] ?? a.action, status: a.next === "SUSPENDED" ? "warning" : "info",
            meta: `${a.objectId} · ${a.actor} · ${fmtDate(a.at, true)} · record #${a.seq}`,
            description: a.reason ?? (a.prior && a.next ? `${a.prior} → ${a.next}` : undefined),
          }))} />
          <p className="mt-3 type-caption text-fg-muted">Sample records<Sample /></p>
        </Card>
        <Card title="Critical platform events" description="Sample events · no platform event feed exists yet">
          <Timeline label="Critical platform events" items={[
            { id: "e1", title: "Organisation suspended: ten-demo-03", meta: "31 Oct 2026, 16:22 UTC · 12 live sessions revoked", description: "Its users are refused with “This organisation is suspended; access is disabled.”", status: "critical" },
            { id: "e2", title: "Content release signature check failed: REL-DEMO-0039", meta: "29 Oct 2026, 11:03 UTC", description: "Not distributed; content operations must re-sign it.", status: "critical" },
            { id: "e3", title: "Super Admin account bootstrapped", meta: "4 Mar 2026, 09:00 UTC", description: "Created from the deployment's environment; no API grants this role.", status: "info" },
          ]} />
        </Card>
      </div>
    </div>
  );
}
