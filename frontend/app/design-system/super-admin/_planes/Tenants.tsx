"use client";

import { useMemo, useRef, useState } from "react";
import { Building2, PauseCircle, PlayCircle, Plus } from "lucide-react";
import {
  Button, Card, ConfirmDialog, DataTable, DescriptionList, Field, Modal, SearchInput, StateBadge, TabPanel, Tabs, TextInput, Textarea, Timeline, useToast,
  type DataColumn,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section, delay, now } from "../../_shared/demo";
import { AUDIT, CELL, REGION, TENANTS, maskEmail, type AuditRow, type Tenant } from "./data";

/* Tenant management, mirroring the Super Admin Dashboard and organisation record (components/PlatformAdmin.tsx,
   app/organisations/[id]/page.tsx → /platform/organisations). Registration uses the backend's validation; status is
   ACTIVE ↔ SUSPENDED with a reason; the server assigns ID, cell, contracting region and isolation tier. */

const EMPTY = { name: "", entity: "", country: "", ref: "", admin: "", email: "" };

export function Tenants() {
  return (
    <DemoFrame id="sa-tenants" plane="platform" title="Tenant management"
      intro="Find an organisation, open its registry record, change its status or register a new one. Each change is recorded in that organisation's own audit trail."
      app={{ screen: "Super Admin Dashboard › Customer organisations", route: "/", action: "GET · POST /platform/organisations · POST /platform/organisations/{id}/status", permission: "manage_organisations",
        enforced: ["Status is ACTIVE or SUSPENDED; repeating the current status is refused (409)", "Suspending revokes every live session; the organisation's users are then refused (403)", "The server assigns the organisation ID, cell, contracting region and isolation tier"] }}
      availability={{ level: "available", note: "Listing, registration, renaming, ACTIVE ↔ SUSPENDED and appointing Organisation Admins exist. Search, filters and sorting are a proposal: today's list has none." }}
      empty={{ title: "No organisations yet", description: "Register the first customer organisation to get started." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const toast = useToast();
  const [tenants, setTenants] = useState<Tenant[]>(TENANTS);
  const [audit, setAudit] = useState<AuditRow[]>(AUDIT);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("ALL");
  const [selected, setSelected] = useState("ten-demo-01");
  const [registering, setRegistering] = useState(false);
  const [confirmRegister, setConfirmRegister] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [tried, setTried] = useState(false);
  const [changing, setChanging] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState(false);
  const detail = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => tenants.filter((t) => (status === "ALL" || t.status === status)
    && (!q.trim() || `${t.name} ${t.id} ${t.entities.map((e) => `${e.name} ${e.country}`).join(" ")}`.toLowerCase().includes(q.trim().toLowerCase()))), [tenants, q, status]);
  const current = tenants.find((t) => t.id === selected);
  const count = (s: string) => tenants.filter((t) => s === "ALL" || t.status === s).length;
  const record = (row: Omit<AuditRow, "seq" | "at">) => setAudit((a) => [...a, { ...row, seq: Math.max(...a.map((x) => x.seq)) + 1, at: now() }]);

  const errors = {
    name: form.name.trim().length < 2 || form.name.trim().length > 200 ? "Enter the organisation's legal name (2–200 characters)." : undefined,
    entity: form.entity.trim().length < 2 || form.entity.trim().length > 200 ? "Enter the first legal entity's name (2–200 characters)." : undefined,
    country: !/^[A-Za-z]{2}$/.test(form.country.trim()) ? "Enter a two-letter ISO 3166 country code, such as GB." : undefined,
    ref: form.ref.trim().length > 100 ? "Keep the registration reference to 100 characters or fewer." : undefined,
    admin: form.admin.trim().length < 2 || form.admin.trim().length > 120 ? "Enter the Organisation Admin's name (2–120 characters)." : undefined,
    email: !/.+@.+\..+/.test(form.email.trim()) ? "Enter the Organisation Admin's email address." : undefined,
  };
  const err = (k: keyof typeof errors) => (tried ? errors[k] : undefined);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const register = async () => {
    await delay(800);
    const id = `ten-demo-${String(tenants.length + 1).padStart(2, "0")}`;
    setTenants((ts) => [...ts, { id, name: form.name.trim(), status: "ACTIVE", region: REGION, tier: "POOLED", created: now().slice(0, 10),
      entities: [{ name: form.entity.trim(), country: form.country.trim().toUpperCase(), ref: form.ref.trim() || undefined, cell: CELL }], admins: [{ name: form.admin.trim(), email: form.email.trim() }] }]);
    record({ actor: "you", actorType: "HUMAN", action: "organisation.created", objectType: "Tenant", objectId: id, next: "ACTIVE", tenant: id });
    setSelected(id); setForm(EMPTY); setTried(false); setStatus("ALL"); setQ("");
    toast(`${form.name.trim()} registered (simulated: no organisation was created)`, "good");
  };
  const changeStatus = async () => {
    if (reason.trim().length < 3) { setReasonError(true); throw new Error("Enter a reason of at least 3 characters. The server refuses a status change without one."); }
    await delay(800);
    const to = current!.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE";
    setTenants((ts) => ts.map((t) => (t.id === current!.id ? { ...t, status: to } : t)));
    record({ actor: "you", actorType: "HUMAN", action: "organisation.status_changed", objectType: "Tenant", objectId: current!.id, prior: current!.status, next: to,
      reason: to === "SUSPENDED" ? `${reason.trim()} (live sessions revoked)` : reason.trim(), tenant: current!.id });
    toast(`${current!.name} is now ${to} (simulated)`, to === "ACTIVE" ? "good" : "warn");
    setReason(""); setReasonError(false);
  };

  const columns: DataColumn<Tenant>[] = [
    { key: "name", header: "Organisation", sortValue: (t) => t.name, render: (t) => <><span className="block font-medium text-fg">{t.name}</span><code className="type-code text-fg-muted">{t.id}</code></> },
    { key: "status", header: "Status", sortValue: (t) => t.status, render: (t) => <StateBadge code={t.status} size="sm" /> },
    { key: "region", header: "Contracting region", render: (t) => t.region },
    { key: "tier", header: "Isolation tier", render: (t) => <code className="type-code">{t.tier}</code> },
    { key: "entities", header: "Entities", align: "right", sortValue: (t) => t.entities.length, render: (t) => <span className="tabular-nums">{t.entities.length}</span> },
    { key: "created", header: "Created", sortValue: (t) => t.created, render: (t) => <span className="whitespace-nowrap tabular-nums">{fmtDate(t.created)}</span> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (t) => (
      <Button size="sm" variant={t.id === selected ? "secondary" : "ghost"} aria-pressed={t.id === selected}
        onClick={() => { setSelected(t.id); setTimeout(() => detail.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0); }}>
        {t.id === selected ? "Viewing" : "View"}<span className="sr-only"> {t.name}</span>
      </Button>
    ) },
  ];

  return (
    <div className="grid gap-stack min-[1680px]:grid-cols-[minmax(0,1fr)_25rem]">
      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0 flex-1 sm:max-w-sm"><Field label="Search organisations">
            <SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Name, ID, entity or country" />
          </Field></div>
          <Button variant="primary" icon={Plus} onClick={() => setRegistering(true)}>Register organisation</Button>
        </div>
        <Tabs id="sa-tenant-status" label="Filter by status" variant="pills" value={status} onChange={setStatus}
          items={["ALL", "ACTIVE", "SUSPENDED"].map((s) => ({ value: s, label: s === "ALL" ? "All" : s, count: count(s) }))} />
        <TabPanel id="sa-tenant-status" value={status} className="">
          <DataTable caption={`Customer organisations (sample): ${shown.length} shown`} columns={columns} rows={shown} rowKey={(t) => t.id} defaultSort={{ key: "created", dir: "desc" }} minWidth="48rem"
            empty={{ title: "No organisations match", description: "Change the search or the status filter.", action: <Button size="sm" onClick={() => { setQ(""); setStatus("ALL"); }}>Clear filters</Button> }} />
        </TabPanel>
        <p className="type-caption text-fg-muted">Every organisation on this cell shares its contracting region, copied from the cell when it is registered; POOLED is the only isolation tier the platform sets.</p>
      </div>

      <div ref={detail} className="scroll-mt-6">
        {current ? (
          <Card title={current.name} description="Registry record" actions={<StateBadge code={current.status} size="sm" />} bodyClassName="p-card space-y-5">
            <DescriptionList columns={1} items={[
              ["Organisation ID", <code key="i" className="type-code">{current.id}</code>], ["Contracting region", current.region],
              ["Isolation tier", <code key="t" className="type-code">{current.tier}</code>], ["Cells", [...new Set(current.entities.map((e) => e.cell))].join(", ")],
              ["Created", fmtDate(current.created)],
            ]} />
            <Section title="Legal entities">
              <ul className="divide-y divide-line rounded-card border border-line">
                {current.entities.map((e) => <li key={e.name} className="px-3 py-2.5"><span className="block type-secondary font-medium text-fg">{e.name}</span>
                  <span className="type-caption text-fg-muted">{e.country}{e.ref ? ` · ${e.ref}` : ""} · {e.cell}</span></li>)}
              </ul>
            </Section>
            <Section title="Organisation Admins">
              <ul className="space-y-1">{current.admins.map((a) => <li key={a.email} className="type-secondary text-fg">{a.name} <span className="type-caption text-fg-muted">{maskEmail(a.email)}</span></li>)}</ul>
            </Section>
            <Section title="Administrative history">
              <Timeline label={`Platform changes to ${current.name}, newest first`} items={audit.filter((a) => a.tenant === current.id && (a.action.startsWith("organisation.") || a.action === "account.bootstrap_created"))
                .slice().reverse().map((a) => ({ id: String(a.seq), title: <code className="type-code">{a.action}</code>, meta: `${a.actor} · ${fmtDate(a.at, true)}`, description: a.reason ?? (a.prior ? `${a.prior} → ${a.next}` : a.next) }))} />
            </Section>
            <Button fullWidth variant={current.status === "ACTIVE" ? "danger" : "primary"} icon={current.status === "ACTIVE" ? PauseCircle : PlayCircle} onClick={() => { setChanging(true); setReason(""); setReasonError(false); }}>
              {current.status === "ACTIVE" ? "Suspend organisation" : "Reactivate organisation"}
            </Button>
            <p className="type-caption text-fg-muted">Sample organisation<Sample />. The Super Admin sees this registry record only, never the organisation&apos;s evidence or assurance.</p>
          </Card>
        ) : <Card><p className="type-secondary text-fg-muted">Choose an organisation to see its record.</p></Card>}
      </div>

      <Modal open={registering} onClose={() => setRegistering(false)} title="Register an organisation" description="Creates the organisation, its first legal entity and its Organisation Admin."
        footer={<>
          <Button variant="secondary" onClick={() => setRegistering(false)}>Cancel</Button>
          <Button variant="primary" icon={Building2} onClick={() => { setTried(true); if (!Object.values(errors).some(Boolean)) { setRegistering(false); setConfirmRegister(true); } }}>Review registration</Button>
        </>}>
        <div className="grid gap-form sm:grid-cols-2">
          <Field label="Organisation legal name" required error={err("name")} className="sm:col-span-2"><TextInput value={form.name} onChange={set("name")} /></Field>
          <Field label="First legal entity" required error={err("entity")}><TextInput value={form.entity} onChange={set("entity")} /></Field>
          <Field label="Country (ISO 3166 alpha-2)" required error={err("country")}><TextInput value={form.country} onChange={set("country")} maxLength={2} placeholder="GB" /></Field>
          <Field label="Registration reference" optional error={err("ref")} className="sm:col-span-2"><TextInput value={form.ref} onChange={set("ref")} /></Field>
          <Field label="Organisation Admin name" required error={err("admin")}><TextInput value={form.admin} onChange={set("admin")} /></Field>
          <Field label="Organisation Admin email" required error={err("email")}><TextInput type="email" value={form.email} onChange={set("email")} /></Field>
        </div>
        <p className="mt-4 type-caption text-fg-muted">The server assigns the organisation ID, cell, contracting region and isolation tier. In the application it also shows the admin&apos;s temporary password once; this demonstration creates none.</p>
      </Modal>
      {confirmRegister && (
        <ConfirmDialog title={`Register ${form.name.trim()}?`} confirmLabel="Register organisation" busyLabel="Registering…" onClose={() => setConfirmRegister(false)} onConfirm={register}>
          <DescriptionList columns={1} items={[["First legal entity", `${form.entity.trim()} (${form.country.trim().toUpperCase()})`], ["Organisation Admin", `${form.admin.trim()} · ${form.email.trim()}`], ["Placed on", `${CELL} · ${REGION} · POOLED`]]} />
          <p>Simulated: no organisation, account or password is created.</p>
        </ConfirmDialog>
      )}
      {changing && current && (
        <ConfirmDialog title={current.status === "ACTIVE" ? `Suspend ${current.name}?` : `Reactivate ${current.name}?`} confirmLabel={current.status === "ACTIVE" ? "Suspend organisation" : "Reactivate organisation"}
          busyLabel="Recording…" tone={current.status === "ACTIVE" ? "danger" : "default"} onClose={() => setChanging(false)} onConfirm={changeStatus}>
          <p>{current.status === "ACTIVE" ? "Every live session is revoked and the organisation's users are refused until it is reactivated." : "The organisation's users can sign in again."} Simulated: no session or status changes.</p>
          <Field label="Reason" required error={reasonError ? "Enter a reason of at least 3 characters." : undefined}>
            <Textarea rows={3} value={reason} onChange={(e) => { setReason(e.target.value); if (e.target.value.trim().length >= 3) setReasonError(false); }} />
          </Field>
        </ConfirmDialog>
      )}
    </div>
  );
}
