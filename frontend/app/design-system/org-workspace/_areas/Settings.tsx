"use client";

import Link from "next/link";
import { useState } from "react";
import { Plus, Save } from "lucide-react";
import {
  Alert, Button, Card, ConfirmDialog, DescriptionList, ErrorState, Field, Modal, Select, Spinner, StateBadge, Switch, TBody, THead, Table, Td, TextInput, Th, Tr,
  useToast,
} from "@/components/ds";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section, delay } from "../../_shared/demo";
import { LiveTag } from "../../super-admin/_planes/live";
import { CELLS, ENTITIES, OBLIGATIONS, ORG } from "./data";
import { Proposed } from "./shared";

/* Organization settings, mirroring Cells & Residency (app/admin/cells → GET /org/tenant, POST /org/entities) and the
   other administration screens. The organization record (legal name, contracting region, isolation tier, status) is
   set when the Super Admin registers it and is read-only here; an Organisation Admin can register a legal entity
   (legal name and country required), which the server places in its own cell. Jurisdictions follow from where each
   entity is registered. There are no organization preferences today: that card is a labelled proposal, not stored.
   The one live read on this page is the opt-in view of your own organization record. */

type Live = { state: "off" | "loading" | "ok" | "error"; data?: TenantRecord; error?: unknown };
type TenantRecord = {
  tenant: { tenant_id: string; legal_name: string; contracting_region: string; isolation_tier: string; status: string; created_at: string };
  entities: { entity_id: string; legal_name: string; country: string; registration_ref?: string | null; home_cell_id: string }[];
  cells: { cell_id: string; region: string; status: string; isolation_tier?: string }[];
};
type Entity = (typeof ENTITIES)[number];
const COUNTRY: Record<string, string> = { GB: "United Kingdom", NL: "Netherlands", IE: "Ireland", DE: "Germany", FR: "France" };
const EU = ["NL", "IE", "DE", "FR"];
const CONFIG = [
  { href: "/admin/users", screen: "Users & Roles", what: "Who is signed in, their roles and what each role allows", can: "Read-only" },
  { href: "/admin/identity", screen: "Identity Integrations", what: "Enterprise single sign-on (OIDC) and provisioning (SCIM)", can: "NOT ENABLED" },
  { href: "/admin/cells", screen: "Cells & Residency", what: "The organization record, legal entities and the cells that hold their data", can: "Register a legal entity (Manage tenant)" },
  { href: "/admin/retention", screen: "Retention & Legal Holds", what: "Evidence under hold and erased payloads; retention policies are not yet enabled", can: "Holds are placed from each evidence record" },
  { href: "/admin/access-log", screen: "Privileged Access Log", what: "Privileged access, support sessions and security-relevant actions", can: "Read-only" },
  { href: "/admin/dictionary", screen: "Controlled Dictionary", what: "The state vocabulary every screen uses; deliberately no COMPLIANT state", can: "Read-only" },
];

export function Settings() {
  return (
    <DemoFrame id="ow-settings" plane="workspace" title="Organization settings"
      intro="The organization's own record: its profile, the legal entities in scope, the jurisdictions they bring, where their data lives, and which configuration the application offers."
      app={{ screen: "Cells & Residency", route: "/admin/cells", action: "GET /org/tenant · POST /org/entities", permission: "manage_tenant",
        enforced: ["Reading the record needs an organization account; Zoiko staff are refused (403, INV-14)", "Registering a legal entity needs Manage tenant", "The server places each new entity in its own cell; a request naming a cell is rejected", "Legal name, contracting region and isolation tier are fixed when the organization is registered"] }}
      availability={{ level: "partial", note: "The organization record, entities and cells are readable and an Organisation Admin can register a legal entity. There are no editable organization settings or preferences: the preference fields are proposals and are not stored." }}
      empty={{ title: "No organization record", description: "Your account is not a member of a customer organization." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const toast = useToast();
  const [live, setLive] = useState<Live>({ state: "off" });
  const [entities, setEntities] = useState<Entity[]>(ENTITIES);
  const [registering, setRegistering] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [form, setForm] = useState({ name: "", country: "", ref: "" });
  const [tried, setTried] = useState(false);
  const [prefs, setPrefs] = useState({ period: "QUARTERLY", landing: "overview", notify: true });
  const [prefsNote, setPrefsNote] = useState(false);

  const read = async () => {
    setLive({ state: "loading" });
    try { setLive({ state: "ok", data: await api("/org/tenant") }); } catch (error) { setLive({ state: "error", error }); }
  };
  const toggleLive = (on: boolean) => { if (on) read(); else setLive({ state: "off" }); };

  const errors = {
    name: !form.name.trim() ? "Enter the entity's legal name." : undefined,
    country: !form.country.trim() ? "Enter the country of registration." : !/^[A-Za-z]{2}$/.test(form.country.trim()) ? "Use a two-letter ISO 3166 code, such as IE (proposed check)." : undefined,
  };
  const err = (k: keyof typeof errors) => (tried ? errors[k] : undefined);
  const register = async () => {
    await delay(800);
    const country = form.country.trim().toUpperCase();
    setEntities((es) => [...es, { id: `nw-${country.toLowerCase()}-${es.length}`, name: form.name.trim(), country, ref: form.ref.trim(), cell: CELLS[0].id, status: "ACTIVE" }]);
    toast(`${form.name.trim()} registered in ${CELLS[0].id} (simulated: no entity was created)`, "good");
    setForm({ name: "", country: "", ref: "" }); setTried(false);
  };

  const jurisdictions = [...new Set(entities.map((e) => e.country))].map((c) => ({
    code: c, name: COUNTRY[c] ?? c, eu: EU.includes(c), entities: entities.filter((e) => e.country === c).map((e) => e.name),
    sources: [...new Set(OBLIGATIONS.filter((o) => entities.some((e) => e.id === o.entity && e.country === c)).map((o) => o.source.jurisdiction))],
  }));

  return (
    <div className="space-y-stack">
      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title={<>Organization profile<Sample /></>} description="Set when the organization is registered; read-only here" actions={<StateBadge code={ORG.status} size="sm" />}>
          <DescriptionList items={[
            ["Legal name", ORG.name], ["Organization ID", <code key="i" className="type-code">{ORG.id}</code>], ["Contracting region", ORG.region],
            ["Isolation tier", <code key="t" className="type-code">{ORG.tier}</code>], ["Registered", fmtDate(ORG.created)], ["Status", ORG.status],
          ]} />
        </Card>
        <Card title="Your organization" description="An opt-in, read-only view of your own record from this deployment">
          <div className="space-y-4">
            <Switch checked={live.state !== "off"} onChange={toggleLive} label="Show my organization's live record" description="Reads GET /api/org/tenant once. Nothing is written." />
            {live.state === "loading" && <Spinner showLabel label="Reading your organization record…" />}
            {live.state === "error" && <ErrorState error={live.error} title="Your organization record could not be read" onRetry={read} compact />}
            {live.state === "ok" && live.data && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2"><LiveTag /><StateBadge code={live.data.tenant.status} size="sm" /></div>
                <DescriptionList items={[
                  ["Legal name", live.data.tenant.legal_name], ["Contracting region", live.data.tenant.contracting_region],
                  ["Isolation tier", <code key="t" className="type-code">{live.data.tenant.isolation_tier}</code>], ["Registered", fmtDate(live.data.tenant.created_at)],
                  ["Legal entities", live.data.entities.length], ["Home cells", [...new Set(live.data.entities.map((e) => e.home_cell_id))].join(", ") || "—"],
                ]} />
              </div>
            )}
            {live.state === "off" && <p className="type-caption text-fg-muted">Off by default. Zoiko staff and the Super Admin are refused (INV-14), which shows as an error here.</p>}
          </div>
        </Card>
      </div>

      <Card title="Entity scope" description="The legal entities whose obligations, evidence and assurance this organization manages" actions={<Button size="sm" icon={Plus} onClick={() => { setRegistering(true); setTried(false); }}>Register legal entity</Button>}>
        <Table caption="Legal entities (sample)" minWidth="40rem">
          <THead><Tr><Th>Legal entity</Th><Th>Country</Th><Th>Registration ref</Th><Th>Home cell</Th><Th>Status</Th></Tr></THead>
          <TBody>{entities.map((e) => (
            <Tr key={e.id}><Td><span className="font-medium text-fg">{e.name}</span></Td><Td><code className="type-code">{e.country}</code></Td><Td>{e.ref || "—"}</Td><Td><code className="type-code">{e.cell}</code></Td><Td><StateBadge code={e.status} size="sm" /></Td></Tr>
          ))}</TBody>
        </Table>
      </Card>

      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Jurisdictions" description="Derived from where each entity is registered; not a separate setting">
          <ul className="divide-y divide-line">{jurisdictions.map((j) => (
            <li key={j.code} className="py-2.5 first:pt-0 last:pb-0">
              <p className="type-secondary font-medium text-fg"><code className="type-code">{j.code}</code> {j.name}{j.eu && <span className="ml-1.5 type-caption text-fg-muted">EU member state</span>}</p>
              <p className="type-caption text-fg-muted">{j.entities.join(", ")}{j.sources.length ? ` · obligation sources: ${j.sources.join(", ")}` : " · no obligations adopted yet"}</p>
            </li>
          ))}</ul>
        </Card>
        <Card title="Cells & data residency" description="Raw evidence and assurance state stay in each entity's home cell">
          <Table caption="Cells (sample)" minWidth="24rem">
            <THead><Tr><Th>Cell</Th><Th>Region</Th><Th>Status</Th><Th>Entities</Th></Tr></THead>
            <TBody>{CELLS.map((c) => (
              <Tr key={c.id}><Td><code className="type-code">{c.id}</code></Td><Td>{c.region}</Td><Td><StateBadge code={c.status} size="sm" /></Td><Td className="tabular-nums">{entities.filter((e) => e.cell === c.id).length}</Td></Tr>
            ))}</TBody>
          </Table>
          <p className="mt-3 type-caption text-fg-muted">The server chooses each entity&apos;s home cell. Moving an entity to another cell is not available in the application.</p>
        </Card>
      </div>

      <Card title={<>Workspace preferences<Proposed>Proposed: not stored</Proposed></>} description="The application has no organization preferences today; these show where they would sit">
        <div className="grid gap-form sm:grid-cols-2 lg:max-w-3xl">
          <Field label="Default reporting period"><Select value={prefs.period} onChange={(e) => setPrefs((p) => ({ ...p, period: e.target.value }))} options={[{ value: "QUARTERLY", label: "Quarterly" }, { value: "MONTHLY", label: "Monthly" }, { value: "ANNUAL", label: "Annual" }]} /></Field>
          <Field label="Open the workspace on"><Select value={prefs.landing} onChange={(e) => setPrefs((p) => ({ ...p, landing: e.target.value }))} options={[{ value: "overview", label: "Organization overview" }, { value: "approvals", label: "Human approvals" }, { value: "reporting", label: "Assurance reporting" }]} /></Field>
          <div className="sm:col-span-2"><Switch checked={prefs.notify} onChange={(v) => setPrefs((p) => ({ ...p, notify: v }))} label="Tell me when an approval or attestation waits for me" description="The application sends no notifications today." /></div>
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <Button icon={Save} onClick={() => { setPrefsNote(true); toast("Not saved: workspace preferences are a proposal and nothing is stored", "info"); }}>Save preferences</Button>
            <span className="type-caption text-fg-muted">Timestamps are always shown in UTC.</span>
          </div>
          {prefsNote && <Alert tone="info" live="polite" className="sm:col-span-2" onDismiss={() => setPrefsNote(false)}>Not saved. These preferences exist only on this page; the application has no place to store them yet.</Alert>}
        </div>
      </Card>

      <Section title="Available configuration in the application">
        <Table caption="Administration screens an organization can use" minWidth="44rem">
          <THead><Tr><Th>Screen</Th><Th>What it holds</Th><Th>What you can do</Th></Tr></THead>
          <TBody>{CONFIG.map((c) => (
            <Tr key={c.href}>
              <Td><Link href={c.href} className="font-medium text-accent-fg underline-offset-4 hover:underline">{c.screen}</Link></Td>
              <Td className="text-fg-secondary">{c.what}</Td>
              <Td>{c.can === "NOT ENABLED" ? <StateBadge code="NOT_ENABLED" status="unknown" size="sm" /> : c.can}</Td>
            </Tr>
          ))}</TBody>
        </Table>
      </Section>

      <Modal open={registering} onClose={() => setRegistering(false)} title="Register a legal entity" description="The server places it in its own cell and creates its subject record."
        footer={<>
          <Button variant="secondary" onClick={() => setRegistering(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => { setTried(true); if (!Object.values(errors).some(Boolean)) { setRegistering(false); setConfirming(true); } }}>Review registration</Button>
        </>}>
        <div className="grid gap-form sm:grid-cols-2">
          <Field label="Legal name" required error={err("name")} className="sm:col-span-2"><TextInput value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></Field>
          <Field label="Country" required error={err("country")} hint="ISO 3166 alpha-2"><TextInput value={form.country} maxLength={2} placeholder="IE" onChange={(e) => setForm((f) => ({ ...f, country: e.target.value }))} /></Field>
          <Field label="Registration reference" optional><TextInput value={form.ref} onChange={(e) => setForm((f) => ({ ...f, ref: e.target.value }))} /></Field>
        </div>
        <p className="mt-4 type-caption text-fg-muted">No cell field: the server chooses the home cell and rejects a request that names one.</p>
      </Modal>
      {confirming && (
        <ConfirmDialog title={`Register ${form.name.trim()}?`} confirmLabel="Register legal entity" busyLabel="Registering…" onClose={() => setConfirming(false)} onConfirm={register}>
          <DescriptionList columns={1} items={[["Country", form.country.trim().toUpperCase()], ["Registration reference", form.ref.trim() || "—"], ["Home cell", `${CELLS[0].id} (chosen by the server)`]]} />
          <p>Simulated: no entity or subject is created and the server is not called.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
