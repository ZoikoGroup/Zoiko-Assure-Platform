"use client";

import { useMemo, useRef, useState } from "react";
import { Button, Card, DataTable, DescriptionList, Field, SearchInput, Select, StateBadge, TabPanel, Tabs, type DataColumn } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample } from "../../_shared/demo";
import { AUDIT, outcomeOf, type AuditRow } from "./data";

/* Administrative audit, on the real AuditEvent fields (models.py platform_audit_event). Records are samples. The table
   is append-only in the database. There is no result field, so the outcome is read from the action name, and there is
   no platform-wide view today: platform actions are written to the affected organisation's audit trail. */

const GROUPS = [{ value: "all", label: "All actions" }, { value: "organisation.", label: "Organisation" }, { value: "account.", label: "Account" }, { value: "auth.", label: "Sign-in" }];

export function AdminAudit() {
  return (
    <DemoFrame id="sa-audit" plane="platform" title="Administrative audit"
      intro="Who did what, to which record, when, and with what outcome: every administrative change, searchable, with the full event behind each row."
      app={{ screen: "Organisation record › Administrative history", route: "/", action: "GET /platform/organisations/{id} (its platform changes)", permission: "manage_organisations",
        enforced: ["Audit events are append-only: a database trigger refuses edits and deletes", "Platform actions are written to the affected organisation's audit trail"] }}
      availability={{ level: "partial", note: "Audit events are recorded and each organisation record shows its platform changes. There is no platform-wide or cross-organisation view, no search or filters, and no result field: the outcome here is read from the action name. The Super Admin's own sign-ins are recorded but not shown anywhere." }}
      empty={{ title: "No audit events", description: "Administrative actions appear here as they are recorded." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const [sel, setSel] = useState(4133);
  const detail = useRef<HTMLDivElement>(null);
  const rows = useMemo(() => AUDIT.filter((a) => (group === "all" || a.action.startsWith(group)) && (outcome === "all" || outcomeOf(a.action) === outcome)
    && (!q.trim() || `${a.actor} ${a.action} ${a.objectType} ${a.objectId} ${a.reason ?? ""} ${a.tenant ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()))), [q, group, outcome]);
  const current = AUDIT.find((a) => a.seq === sel);

  const columns: DataColumn<AuditRow>[] = [
    { key: "seq", header: "Record", sortValue: (a) => a.seq, render: (a) => <code className="type-code text-fg-muted">#{a.seq}</code> },
    { key: "at", header: "Timestamp", sortValue: (a) => a.at, render: (a) => <span className="whitespace-nowrap tabular-nums text-fg-secondary">{fmtDate(a.at, true)}</span> },
    { key: "actor", header: "Actor", sortValue: (a) => a.actor, render: (a) => <><code className="type-code text-fg">{a.actor}</code><span className="block type-caption text-fg-muted">{a.actorType}</span></> },
    { key: "action", header: "Action", sortValue: (a) => a.action, render: (a) => <code className="type-code text-accent-fg">{a.action}</code> },
    { key: "target", header: "Target", sortValue: (a) => a.objectId, render: (a) => <><span className="block">{a.objectType}</span><code className="type-code text-fg-muted">{a.objectId}</code></> },
    { key: "outcome", header: "Outcome", sortValue: (a) => outcomeOf(a.action), render: (a) => <StateBadge code={outcomeOf(a.action).toUpperCase()} status={outcomeOf(a.action) === "Refused" ? "critical" : "verified"} size="sm" /> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (a) => (
      <Button size="sm" variant={a.seq === sel ? "secondary" : "ghost"} aria-pressed={a.seq === sel}
        onClick={() => { setSel(a.seq); setTimeout(() => detail.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0); }}>
        Details<span className="sr-only"> for record {a.seq}</span>
      </Button>
    ) },
  ];

  return (
    <div className="grid gap-stack min-[1680px]:grid-cols-[minmax(0,1fr)_23rem]">
      <div className="min-w-0 space-y-3">
        <div className="grid gap-form sm:grid-cols-[minmax(0,1fr)_12rem]">
          <Field label="Search audit events"><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Actor, action, target or reason" /></Field>
          <Field label="Outcome"><Select value={outcome} onChange={(e) => setOutcome(e.target.value)} options={[{ value: "all", label: "Any outcome" }, { value: "Recorded", label: "Recorded" }, { value: "Refused", label: "Refused" }]} /></Field>
        </div>
        <Tabs id="sa-audit-group" label="Filter by action" variant="pills" value={group} onChange={setGroup}
          items={GROUPS.map((g) => ({ ...g, count: AUDIT.filter((a) => g.value === "all" || a.action.startsWith(g.value)).length }))} />
        <TabPanel id="sa-audit-group" value={group} className="">
          <DataTable caption={`Administrative audit events (sample records): ${rows.length} shown`} columns={columns} rows={rows} rowKey={(a) => String(a.seq)} defaultSort={{ key: "seq", dir: "desc" }} minWidth="52rem"
            empty={{ title: "No events match", description: "Change the search or filters.", action: <Button size="sm" onClick={() => { setQ(""); setGroup("all"); setOutcome("all"); }}>Clear filters</Button> }} />
        </TabPanel>
        <p className="type-caption text-fg-muted">Sample records<Sample />. Outcome is derived from the action name; the audit model stores no result.</p>
      </div>
      <div ref={detail} className="scroll-mt-6"><Card title="Audit event" description={current ? `Record #${current.seq}` : undefined} bodyClassName="p-card">
        {current ? (
          <DescriptionList columns={1} items={[
            ["Action", <code key="a" className="type-code">{current.action}</code>], ["Recorded at", fmtDate(current.at, true)],
            ["Actor", <span key="ac"><code className="type-code">{current.actor}</code> · {current.actorType}</span>],
            ["Target", <span key="t">{current.objectType} <code className="type-code">{current.objectId}</code></span>],
            ["Organisation trail", current.tenant ? <code key="o" className="type-code">{current.tenant}</code> : "None (not exposed)"],
            ["Transition", current.prior || current.next ? `${current.prior ?? "—"} → ${current.next ?? "—"}` : "—"],
            ["Reason", current.reason ?? "—"], ["Correlation ID", "Not set by the server today"],
            ["Outcome (derived)", outcomeOf(current.action)],
          ]} />
        ) : <p className="type-secondary text-fg-muted">Choose an event.</p>}
      </Card></div>
    </div>
  );
}
