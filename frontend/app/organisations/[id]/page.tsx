"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Field, KV, LoadingSkeleton, Modal, Notice, PageHeader, Table, useAction } from "@/components/ui";
import { OneTimePassword } from "@/components/PlatformAdmin";

/* Super Admin: one customer organisation's registry record (WF-TEN-01). Metadata only; no customer data. */
export default function OrganisationPage() {
  const { id } = useParams<{ id: string }>();
  const org = useApi<any>(`/platform/organisations/${id}`);
  const [dialog, setDialog] = useState<"edit" | "status" | "admin" | null>(null);
  const o = org.data;
  const suspended = o?.status === "SUSPENDED";
  const close = () => setDialog(null);
  const saved = (data: any) => { org.setData(data); };

  return (
    <>
      <PageHeader kicker="Customer organisation · registry record" title={o?.legal_name ?? "Organisation"} spec="WF-TEN-01 · ZA-TEN-001"
        subtitle="Status, placement, legal entities and Organisation Admins, administered by the Zoiko platform. This organisation's regulatory records are not visible here."
        actions={o && <>
          <button className="btn-ghost" onClick={() => setDialog("edit")}>Edit name</button>
          <button className="btn-ghost" onClick={() => setDialog("admin")}>Appoint Organisation Admin</button>
          <button className={suspended ? "btn-primary" : "btn-danger"} onClick={() => setDialog("status")}>{suspended ? "Activate" : "Deactivate"}</button>
        </>} />
      <ErrorBox error={org.error} />
      {org.loading && !o && <div className="card"><LoadingSkeleton rows={5} /></div>}
      {o && (
        <div className="space-y-4">
          {suspended && <Notice tone="warn">This organisation is suspended. Its people cannot sign in and any earlier sessions were ended.</Notice>}
          <Card>
            <KV cols={4} items={[
              ["Status", <Badge key="s" code={o.status} />], ["Organisation ID", <span key="t" className="mono text-xs">{o.tenant_id}</span>],
              ["Contracting region", o.contracting_region], ["Isolation tier", <span key="i" className="mono text-xs">{o.isolation_tier}</span>],
              ["Cells", <span key="c" className="mono text-xs">{o.cells.join(", ") || "—"}</span>], ["Created", fmtDate(o.created_at, true)],
            ]} />
          </Card>
          <Card title="Legal entities" subtitle="Each entity's data stays in its home cell (TEN-REQ-013). Further entities are registered by the organisation itself.">
            <Table head={["Legal entity", "Country", "Registration", "Home cell", "Status"]} empty={o.entities.length === 0}>
              {o.entities.map((e: any) => (
                <tr key={e.entity_id}>
                  <td className="td">{e.legal_name}<div className="mono text-[11px] text-slate-500">{e.entity_id}</div></td>
                  <td className="td mono text-xs">{e.country}</td><td className="td text-xs">{e.registration_ref ?? "—"}</td>
                  <td className="td mono text-xs">{e.home_cell_id}{e.region ? ` · ${e.region}` : ""}</td><td className="td"><Badge code={e.status} /></td>
                </tr>
              ))}
            </Table>
          </Card>
          <Card title="Organisation Admins" subtitle="Role TENANT_OWNER: administers this organisation only, and cannot attest or approve on its behalf.">
            <Table head={["Name", "Email"]} empty={o.admins.length === 0}>
              {o.admins.map((a: any) => <tr key={a.actor_id}><td className="td">{a.display_name}</td><td className="td mono text-xs">{a.email}</td></tr>)}
            </Table>
          </Card>
          <Card title="Administrative history" subtitle="Platform changes to this organisation, as recorded in its audit trail.">
            <Table head={["When", "Change", "Transition", "Detail"]} empty={o.admin_events.length === 0}>
              {o.admin_events.map((e: any) => (
                <tr key={e.record_seq}>
                  <td className="td text-xs">{fmtDate(e.recorded_at, true)}</td><td className="td mono text-xs">{e.action}</td>
                  <td className="td text-xs">{e.prior_state || e.new_state ? `${e.prior_state ?? "∅"} → ${e.new_state ?? "∅"}` : "—"}</td>
                  <td className="td text-xs">{e.reason ?? "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>
      )}
      {o && dialog === "edit" && <EditName org={o} onClose={close} onSaved={saved} />}
      {o && dialog === "status" && <ChangeStatus org={o} onClose={close} onSaved={saved} />}
      {o && dialog === "admin" && <AppointAdmin org={o} onClose={close} onSaved={saved} />}
    </>
  );
}

function EditName({ org, onClose, onSaved }: { org: any; onClose: () => void; onSaved: (o: any) => void }) {
  const [name, setName] = useState(org.legal_name);
  const a = useAction();
  return (
    <Modal open onClose={onClose} title="Edit organisation name" size="sm">
      <ErrorBox error={a.error} />
      <Field label="Organisation legal name" htmlFor="org-edit-name"><input id="org-edit-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={name.trim().length < 2 || name.trim() === org.legal_name || a.busy} onClick={() => a.run(async () => {
          onSaved(await api(`/platform/organisations/${org.tenant_id}`, { method: "PATCH", json: { legal_name: name.trim() } }));
          onClose();
        })}>Save</button>
      </div>
    </Modal>
  );
}

function ChangeStatus({ org, onClose, onSaved }: { org: any; onClose: () => void; onSaved: (o: any) => void }) {
  const target = org.status === "SUSPENDED" ? "ACTIVE" : "SUSPENDED";
  const [reason, setReason] = useState("");
  const a = useAction();
  return (
    <Modal open onClose={onClose} title={target === "SUSPENDED" ? "Deactivate organisation" : "Activate organisation"} size="sm">
      <ErrorBox error={a.error} />
      <p className="mb-3 text-[13px] text-slate-600">
        {target === "SUSPENDED"
          ? <>Deactivating sets <b>{org.legal_name}</b> to SUSPENDED: every active session ends now and its people cannot sign in until it is activated again. No data is deleted.</>
          : <>Activating sets <b>{org.legal_name}</b> to ACTIVE so its people can sign in again.</>}
      </p>
      <Field label="Reason (recorded in the audit trail)" htmlFor="org-status-reason">
        <textarea id="org-status-reason" className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className={target === "SUSPENDED" ? "btn-danger" : "btn-primary"} disabled={reason.trim().length < 3 || a.busy} onClick={() => a.run(async () => {
          onSaved(await api(`/platform/organisations/${org.tenant_id}/status`, { method: "POST", json: { status: target, reason: reason.trim() } }));
          onClose();
        })}>{target === "SUSPENDED" ? "Deactivate" : "Activate"}</button>
      </div>
    </Modal>
  );
}

function AppointAdmin({ org, onClose, onSaved }: { org: any; onClose: () => void; onSaved: (o: any) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const a = useAction();
  const result = a.result;
  return (
    <Modal open onClose={onClose} title="Appoint Organisation Admin">
      {result ? (
        <>
          <OneTimePassword email={email.trim().toLowerCase()} password={result.initial_admin_password} />
          <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div>
        </>
      ) : (
        <>
          <ErrorBox error={a.error} />
          <p className="mb-3 text-[13px] text-slate-600">The new admin gets role TENANT_OWNER in {org.legal_name} only, with a one-time temporary password to replace at first sign-in.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" htmlFor="adm-name"><input id="adm-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Email" htmlFor="adm-email"><input id="adm-email" type="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={name.trim().length < 2 || !/.+@.+\..+/.test(email.trim()) || a.busy} onClick={() => a.run(async () => {
              const r = await api(`/platform/organisations/${org.tenant_id}/admins`, { method: "POST", json: { display_name: name.trim(), email: email.trim() } });
              const { initial_admin_password: _shownOnce, ...record } = r;  // the page keeps the record, not the password
              onSaved(record);
              return r;
            })}>Appoint</button>
          </div>
        </>
      )}
    </Modal>
  );
}
