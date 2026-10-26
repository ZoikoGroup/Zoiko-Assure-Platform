"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Card, ErrorBox, KV, Modal, PageHeader, Table, useAction } from "@/components/ui";

export default function CellsAndResidency() {
  return (
    <>
      <PageHeader spec="WF-TEN-01/02 · TEN-REQ-013" title="Cells & Residency"
        subtitle="Your tenant, its legal entities and the cells that hold their data. Raw entity evidence and customer assurance state stay in the entity's home cell." />
      <Tenancy />
    </>
  );
}

function Tenancy() {
  const { data, error, reload } = useApi<any>("/org/tenant");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ legal_name: "", country: "", registration_ref: "" });
  const a = useAction();
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      {data && (
        <>
          <Card title={data.tenant?.legal_name} subtitle="Tenant" actions={<button className="btn-primary" onClick={() => setOpen(true)}>Register legal entity</button>}>
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
          <p className="text-xs text-slate-600 sm:col-span-2">The home cell is assigned by the server; it cannot be chosen here.</p>
          {(["legal_name", "country", "registration_ref"] as const).map((k) => <div key={k}><label className="label">{k.replace(/_/g, " ")}</label><input className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>)}
        </div>
        <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.legal_name || !f.country || a.busy} onClick={() => a.run(async () => {
          await api("/org/entities", { method: "POST", json: { ...f, registration_ref: f.registration_ref || null } }); setOpen(false); reload();
        })}>Register</button></div>
      </Modal>
    </div>
  );
}
