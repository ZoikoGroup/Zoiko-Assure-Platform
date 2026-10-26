"use client";

import { useState } from "react";
import { Building2, Plus } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, EmptyState, ErrorBox, Field, LoadingSkeleton, MetricCard, Modal, Notice, PageHeader, Table, useAction } from "./ui";

/* Super Admin dashboard (platform principal, ZA-TEN-001 / ZA-IAM-001). The backend returns organisation-registry
   metadata only; customer evidence, facts and assurance are not reachable from this role (INV-14, IAM-REQ-030). */

export function SuperAdminDashboard() {
  const orgs = useApi<any[]>("/platform/organisations");
  const [open, setOpen] = useState(false);
  const list = orgs.data ?? [];
  const active = list.filter((o) => o.status === "ACTIVE").length;
  return (
    <>
      <PageHeader kicker="Zoiko platform administration" title="Super Admin Dashboard" spec="WF-TEN-01 · ZA-TEN-001 · ZA-IAM-001"
        subtitle="The customer organisations hosted on this cell: their status, legal entities, placement and Organisation Admins. You are not a member of any of them."
        actions={<button className="btn-primary" onClick={() => setOpen(true)}><Plus size={14} aria-hidden />Create organisation</button>} />
      <Notice>You manage the organisation registry only. Customer evidence, facts and assurance records are not accessible from this role, and every change you make is recorded in that organisation&apos;s audit trail.</Notice>
      <ErrorBox error={orgs.error} />
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Organisations" value={orgs.data ? list.length : "—"} tone="info" />
        <MetricCard label="Active" value={orgs.data ? active : "—"} code="ACTIVE" />
        <MetricCard label="Suspended" value={orgs.data ? list.length - active : "—"} code="SUSPENDED" />
        <MetricCard label="Legal entities" value={orgs.data ? list.reduce((n, o) => n + o.entities.length, 0) : "—"} tone="info" />
      </div>
      <div className="mt-4">
        <Card title="Customer organisations" subtitle="Open an organisation to edit it, change its status or appoint an Organisation Admin.">
          {orgs.loading && !orgs.data ? <LoadingSkeleton rows={4} /> : list.length === 0 ? (
            <EmptyState icon={Building2} title="No organisations yet" description="Create the first customer organisation to get started." />
          ) : (
            <Table head={["Organisation", "Status", "Legal entities", "Cell · tier", "Organisation Admin", "Created"]}>
              {list.map((o) => (
                <tr key={o.tenant_id}>
                  <td className="td"><A href={`/organisations/${o.tenant_id}`}>{o.legal_name}</A><div className="mono text-[11px] text-slate-500">{o.tenant_id}</div></td>
                  <td className="td"><Badge code={o.status} /></td>
                  <td className="td text-[13px]">{o.entities.map((e: any) => <div key={e.entity_id}>{e.legal_name} <span className="mono text-[11px] text-slate-500">{e.country}</span></div>)}</td>
                  <td className="td mono text-[11px]">{o.cells.join(", ") || "—"} · {o.isolation_tier}</td>
                  <td className="td text-[13px]">{o.admins.length ? o.admins.map((a: any) => <div key={a.actor_id}>{a.display_name}<div className="mono text-[11px] text-slate-500">{a.email}</div></div>)
                    : <span className="text-slate-500">None appointed</span>}</td>
                  <td className="td text-xs">{fmtDate(o.created_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
      {open && <CreateOrganisation onClose={() => setOpen(false)} onCreated={() => orgs.reload()} />}
    </>
  );
}

function CreateOrganisation({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [f, setF] = useState({ legal_name: "", entity_name: "", country: "", registration_ref: "", admin_name: "", admin_email: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const a = useAction();
  const created = a.result;
  const ready = f.legal_name.trim().length >= 2 && f.entity_name.trim().length >= 2 && /^[A-Za-z]{2}$/.test(f.country.trim())
    && f.admin_name.trim().length >= 2 && /.+@.+\..+/.test(f.admin_email.trim());
  return (
    <Modal open onClose={onClose} title={created ? "Organisation created" : "Create organisation"}>
      {created ? (
        <>
          <p className="mb-3 text-[13px] text-slate-700"><b>{created.legal_name}</b> is active in {created.cells.join(", ")} ({created.isolation_tier}).</p>
          <OneTimePassword email={created.admins[0]?.email} password={created.initial_admin_password} />
          <div className="mt-4 flex justify-end gap-2">
            <A href={`/organisations/${created.tenant_id}`}>Open organisation</A>
            <button className="btn-primary" onClick={onClose}>Done</button>
          </div>
        </>
      ) : (
        <>
          <ErrorBox error={a.error} />
          <p className="mb-3 text-[13px] text-slate-600">The server assigns the cell and isolation tier. The Organisation Admin receives a one-time temporary password and must change it at first sign-in.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2"><Field label="Organisation legal name" htmlFor="org-name"><input id="org-name" className="input" value={f.legal_name} onChange={set("legal_name")} /></Field></div>
            <Field label="First legal entity" htmlFor="org-entity"><input id="org-entity" className="input" value={f.entity_name} onChange={set("entity_name")} /></Field>
            <Field label="Country (ISO 3166 alpha-2)" htmlFor="org-country"><input id="org-country" className="input uppercase" maxLength={2} value={f.country} onChange={set("country")} placeholder="GB" /></Field>
            <div className="sm:col-span-2"><Field label="Registration reference (optional)" htmlFor="org-reg"><input id="org-reg" className="input" value={f.registration_ref} onChange={set("registration_ref")} /></Field></div>
            <Field label="Organisation Admin name" htmlFor="org-admin-name"><input id="org-admin-name" className="input" value={f.admin_name} onChange={set("admin_name")} /></Field>
            <Field label="Organisation Admin email" htmlFor="org-admin-email"><input id="org-admin-email" type="email" className="input" value={f.admin_email} onChange={set("admin_email")} /></Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={!ready || a.busy} onClick={() => a.run(async () => {
              const r = await api("/platform/organisations", { method: "POST", json: {
                legal_name: f.legal_name.trim(),
                entity: { legal_name: f.entity_name.trim(), country: f.country.trim().toUpperCase(), registration_ref: f.registration_ref.trim() || null },
                admin: { display_name: f.admin_name.trim(), email: f.admin_email.trim() },
              } });
              onCreated();
              return r;
            })}>{a.busy ? "Creating…" : "Create organisation"}</button>
          </div>
        </>
      )}
    </Modal>
  );
}

/** A temporary password returned once by the backend: shown here only, never stored, logged or toasted. */
export function OneTimePassword({ email, password }: { email?: string; password?: string }) {
  const [copied, setCopied] = useState(false);
  if (!password) return null;
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
      <div className="text-[12px] font-medium text-amber-900">One-time temporary password for {email}</div>
      <div className="mt-2 flex items-center gap-2">
        <code className="mono flex-1 select-all break-all rounded bg-white px-2 py-1 text-[13px] text-slate-900 ring-1 ring-amber-200">{password}</code>
        <button type="button" className="btn-ghost" onClick={async () => { try { await navigator.clipboard.writeText(password); setCopied(true); } catch {} }}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <p className="mt-2 text-[12px] text-amber-900">It is shown once and cannot be retrieved later. Hand it over through a secure channel; the admin must replace it at first sign-in.</p>
    </div>
  );
}
