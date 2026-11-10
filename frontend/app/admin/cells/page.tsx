"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { useCan } from "@/lib/roles";
import { Badge, Card, ErrorBox, KV, LoadingSkeleton, Modal, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

export default function CellsAndResidency() {
  return (
    <>
      <PageHeader spec="WF-TEN-01/02 · TEN-REQ-013" title="Cells & Residency"
        subtitle="Your organisation, its legal entities and where their data is held."
        help="Raw entity evidence and customer assurance state stay in the entity's home cell (TEN-REQ-013)." />
      <PermissionNote perms={["manage_tenant"]} />
      <Tenancy />
    </>
  );
}

function Tenancy() {
  const { data, error, reload } = useApi<any>("/org/tenant");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ legal_name: "", country: "", registration_ref: "" });
  const a = useAction();
  const { can, why } = useCan();
  const fid = useId();
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      {!data && !error && <Card><LoadingSkeleton rows={4} /></Card>}
      {data && (
        <>
          <Card title={data.tenant?.legal_name} subtitle="Tenant" actions={<button className="btn-primary" disabled={!can("manage_tenant")} title={why("manage_tenant")} onClick={() => setOpen(true)}>Register legal entity</button>}>
            <KV cols={4} items={Object.entries(data.tenant || {}).slice(0, 8).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])} />
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Legal entities">
              <Table head={["Entity", "Country", "Registration", "Home cell"]}>
                {data.entities.map((e: any) => <tr key={e.entity_id}><td className="td">{e.legal_name}<div className="mono text-[11px] text-slate-500">{e.entity_id}</div></td><td className="td mono">{e.country}</td><td className="td text-xs">{e.registration_ref ?? "—"}</td><td className="td mono text-xs">{e.home_cell_id}</td></tr>)}
              </Table>
            </Card>
            <Card title="Cells" subtitle="Tenant data is processed only in its home cell.">
              <Table head={["Cell", "Region", "Isolation", "Status"]}>
                {data.cells.map((c: any) => <tr key={c.cell_id}><td className="td mono text-xs">{c.cell_id}</td><td className="td">{c.region}</td><td className="td text-xs">{c.isolation_tier}</td><td className="td"><Badge code={c.status} /></td></tr>)}
              </Table>
            </Card>
          </div>
        </>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Register legal entity">
        <ErrorBox error={a.error} />
        <div className="grid gap-3 sm:grid-cols-2">
          <p className="text-xs text-slate-600 sm:col-span-2">The home cell is assigned by the server; it cannot be chosen here. The registration is recorded in your organisation&apos;s audit trail.</p>
          {([["legal_name", "Legal name", 200], ["country", "Country (ISO 3166 alpha-2, e.g. GB)", 2], ["registration_ref", "Registration reference", 100]] as const).map(([k, label, max]) => {
            const req = k !== "registration_ref";
            return <div key={k}><label className="label" htmlFor={`${fid}-${k}`}>{label}{req && " (required)"}</label><input id={`${fid}-${k}`} required={req} maxLength={max} className={`input ${k === "country" ? "uppercase" : ""}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>;
          })}
        </div>
        <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={f.legal_name.trim().length < 2 || !/^[A-Za-z]{2}$/.test(f.country.trim()) || a.busy} onClick={() => a.run(async () => {
          await api("/org/entities", { method: "POST", json: { legal_name: f.legal_name.trim(), country: f.country.trim().toUpperCase(), registration_ref: f.registration_ref.trim() || null } }); setOpen(false); reload();
        })}>Register</button></div>
      </Modal>
    </div>
  );
}
