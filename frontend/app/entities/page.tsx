"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Modal, PageHeader, Table, useAction } from "@/components/ui";

const TYPES = ["BUSINESS_UNIT", "REGULATED_ACTIVITY", "PRODUCT_SERVICE", "ENVIRONMENT", "AI_ASSET", "CONTRACT_THIRD_PARTY"];

export default function Entities() {
  const { data: org } = useApi<any>("/org/tenant");
  const { data, error, reload } = useApi<any[]>("/org/subjects");
  const [open, setOpen] = useState(false);
  const { can, why } = useCan();
  const ents: Record<string, any> = Object.fromEntries((org?.entities || []).map((e: any) => [e.entity_id, e]));
  const names: Record<string, string> = Object.fromEntries((data || []).map((s) => [s.subject_id, s.display_name]));
  const groups = new Map<string, any[]>();
  (data || []).forEach((s) => groups.set(s.entity_id, [...(groups.get(s.entity_id) || []), s]));
  return (
    <>
      <PageHeader spec="ZA-DOM-001 §5 · ZA-APP-001" title="Entities & Facts"
        subtitle={`${org?.tenant.legal_name ?? "Your organisation"}'s legal entities and the subjects within them (business units, activities, products, AI assets) that obligations attach to. Open a subject to see its facts and which requirements apply.`}
        actions={<button className="btn-primary" disabled={!can("manage_facts")} title={why("manage_facts")} onClick={() => setOpen(true)}>Register subject</button>} />
      <ErrorBox error={error} />
      <div className="space-y-4">
        {[...groups.entries()].map(([eid, subs]) => (
          <Card key={eid} title={<>{ents[eid]?.legal_name ?? eid} <span className="text-xs font-normal text-slate-500">· legal entity</span></>}
            subtitle={ents[eid] ? `${ents[eid].country} · data held in ${ents[eid].home_cell_id} · ${subs.length} subject(s)` : undefined}>
            <Table head={["Subject", "Type", "Within", "Active"]}>
              {subs.map((s) => (
                <tr key={s.subject_id}>
                  <td className="td"><A href={`/entities/${s.subject_id}`}>{s.display_name}</A><div className="mono text-[11px] text-slate-500">{s.subject_id}</div></td>
                  <td className="td"><Badge code={s.subject_type} tone="info" /></td>
                  <td className="td text-xs">{s.parent_subject_id ? names[s.parent_subject_id] ?? <span className="mono">{s.parent_subject_id}</span> : "—"}</td>
                  <td className="td">{s.active ? "yes" : "no"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        ))}
      </div>
      <NewSubject open={open} onClose={() => setOpen(false)} entities={org?.entities || []} onDone={() => { setOpen(false); reload(); }} />
    </>
  );
}

function NewSubject({ open, onClose, entities, onDone }: { open: boolean; onClose: () => void; entities: any[]; onDone: () => void }) {
  const [f, setF] = useState({ entity_id: "", subject_type: "PRODUCT_SERVICE", display_name: "" });
  const a = useAction();
  return (
    <Modal open={open} onClose={onClose} title="Register subject">
      <ErrorBox error={a.error} />
      <div className="grid gap-3">
        <div><label className="label">Legal entity</label><select className="input" value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>
          <option value="">Select…</option>{entities.map((e) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
        <div><label className="label">Subject type</label><select className="input" value={f.subject_type} onChange={(e) => setF({ ...f, subject_type: e.target.value })}>
          {TYPES.map((t) => <option key={t}>{t}</option>)}</select></div>
        <div><label className="label">Display name</label><input className="input" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={a.busy || !f.entity_id || !f.display_name}
        onClick={() => a.run(async () => { await api("/org/subjects", { method: "POST", json: f }); onDone(); })}>Register</button></div>
    </Modal>
  );
}
