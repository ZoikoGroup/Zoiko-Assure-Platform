"use client";

import { useState } from "react";
import { ArrowLeftRight } from "lucide-react";
import { Card, DescriptionList, Field, InfoPanel, Select, StateBadge, Table, TBody, THead, Td, Th, Tr } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";
import { CELLS, TENANTS } from "./data";

/* Tenant metadata: exactly the registry fields the platform stores (models.py Tenant, LegalEntity, Cell), who sets
   each, and what can change. No configuration or settings fields exist, so none are shown. */

const FIELDS: [string, string, string, string][] = [
  ["tenant_id", "Organisation ID", "Server, at registration", "Never"],
  ["legal_name", "Legal name", "Super Admin, at registration", "Yes, by the Super Admin"],
  ["contracting_region", "Contracting region", "Server: copied from its cell's region", "No"],
  ["isolation_tier", "Isolation tier", "Server: always POOLED today", "No"],
  ["status", "Lifecycle status", "Super Admin", "ACTIVE ↔ SUSPENDED, with a reason"],
  ["created_at", "Created", "Server, at registration", "Never"],
];

export function TenantMetadata() {
  return (
    <DemoFrame id="sa-metadata" plane="platform" title="Tenant metadata"
      intro="What the platform records about each organisation: identity, placement and lifecycle. This is the whole registry record; the organisation's own data never appears here."
      app={{ screen: "Organisation record", route: "/", action: "GET /platform/organisations/{id} · PATCH (legal name only)", permission: "manage_organisations",
        enforced: ["Registration accepts no role, cell, tier or ID fields: the server assigns them", "Only the legal name can be edited"] }}
      availability={{ level: "available", note: "Every field shown exists on the organisation record. Values are samples. There is no separate residency field: residency follows each entity's home cell." }}
      empty={{ title: "No organisations yet", description: "Metadata appears once an organisation is registered." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const [id, setId] = useState(TENANTS[0].id);
  const t = TENANTS.find((x) => x.id === id)!;
  return (
    <div className="space-y-stack">
      <div className="max-w-sm"><Field label="Organisation"><Select value={id} onChange={(e) => setId(e.target.value)} options={TENANTS.map((x) => ({ value: x.id, label: `${x.name} (${x.id})` }))} /></Field></div>
      <div className="grid gap-stack xl:grid-cols-2">
        <Card title="Identity and lifecycle" actions={<StateBadge code={t.status} size="sm" />}>
          <DescriptionList items={[
            ["Organisation ID", <code key="i" className="type-code">{t.id}</code>], ["Legal name", t.name], ["Created", fmtDate(t.created)],
            ["Contracting region", t.region], ["Isolation tier", <code key="t" className="type-code">{t.tier}</code>], ["Lifecycle status", <StateBadge key="s" code={t.status} size="sm" />],
          ]} />
          <div className="mt-5"><Section title="Lifecycle">
            <div className="flex flex-wrap items-center gap-3">
              <StateBadge code="ACTIVE" /><ArrowLeftRight size={16} aria-hidden className="text-fg-muted" /><span className="sr-only">changes to and from</span><StateBadge code="SUSPENDED" />
            </div>
            <ul className="list-disc space-y-1 pl-5 type-caption text-fg-secondary">
              <li>Suspending revokes every live session; users then see “This organisation is suspended; access is disabled.”</li>
              <li>Reactivating restores sign-in. Both need a reason and are recorded in the organisation&apos;s audit trail.</li>
              <li>No other lifecycle states exist.</li>
            </ul>
          </Section></div>
        </Card>
        <Card title="Residency" description="Where each legal entity's records are held">
          <ul className="divide-y divide-line rounded-card border border-line">
            {t.entities.map((e) => {
              const cell = CELLS.find((c) => c.id === e.cell);
              return (
                <li key={e.name} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                  <span><span className="block type-secondary font-medium text-fg">{e.name}</span><span className="type-caption text-fg-muted">{e.country}{e.ref ? ` · ${e.ref}` : ""}</span></span>
                  <span className="text-right"><code className="block type-code text-fg">{e.cell}</code><span className="type-caption text-fg-muted">{cell?.region}</span></span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 type-caption text-fg-muted">Raw entity evidence and customer assurance state stay in the entity&apos;s home cell. Moving an entity between cells is not supported today.</p>
        </Card>
      </div>
      <Card title="Registry fields" description="The complete organisation record and who controls each field" bodyClassName="p-card space-y-4">
        <Table caption="Organisation registry fields" minWidth="40rem">
          <THead><tr><Th>Field</Th><Th>Meaning</Th><Th>Set by</Th><Th>Can change</Th></tr></THead>
          <TBody>{FIELDS.map(([f, m, by, edit]) => <Tr key={f}><Td><code className="type-code text-accent-fg">{f}</code></Td><Td>{m}</Td><Td className="text-fg-secondary">{by}</Td><Td className="text-fg-secondary">{edit}</Td></Tr>)}</TBody>
        </Table>
        <InfoPanel title="Not stored">
          The platform holds no tenant configuration or settings, no separate residency field and no isolation tier other than POOLED. Adding any would be a registry change, not a screen change.
        </InfoPanel>
        <p className="type-caption text-fg-muted">Organisation values are samples<Sample />. Cells mirror the demo seed.</p>
      </Card>
    </div>
  );
}
