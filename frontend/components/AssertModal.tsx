"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox, Modal, useAction } from "./ui";

const PROV = ["P1_SYSTEM_DIRECT", "P2_SYSTEM_DERIVED", "P3_CUSTOMER_ATTESTED", "P4_MANUAL_UNATTESTED", "P5_INFERRED_AI"];

export function AssertModal({ subjectId, fact, onClose, onDone }: { subjectId: string; fact: any; onClose: () => void; onDone: (r: any) => void }) {
  const [value, setValue] = useState("");
  const [vf, setVf] = useState(new Date().toISOString().slice(0, 10));
  const [vt, setVt] = useState("");
  const [prov, setProv] = useState("P3_CUSTOMER_ATTESTED");
  const [kind, setKind] = useState("CREATE");
  const [reason, setReason] = useState("");
  const [src, setSrc] = useState("");
  const a = useAction();
  if (!fact) return null;
  const dt = fact.data_type;
  const parse = () => {
    if (dt === "BOOLEAN") return value === "true";
    if (dt === "INTEGER") return parseInt(value, 10);
    if (dt === "DECIMAL") return parseFloat(value);
    if (dt === "SET") return value.split(",").map((x) => x.trim()).filter(Boolean);
    return value;
  };
  return (
    <Modal open onClose={onClose} title={`Assert ${fact.fact_key}`}>
      <ErrorBox error={a.error} />
      <p className="mb-3 text-xs text-slate-600">{fact.label ?? fact.fact_label} · {dt}{fact.manual_attestation_policy ? ` · manual attestation ${fact.manual_attestation_policy}` : ""}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2"><label className="label">Value</label>
          {dt === "BOOLEAN" ? (
            <select className="input" value={value} onChange={(e) => setValue(e.target.value)}><option value="">Select…</option><option>true</option><option>false</option></select>
          ) : fact.enum_domain ? (
            <select className="input" value={value} onChange={(e) => setValue(e.target.value)}><option value="">Select…</option>{fact.enum_domain.map((x: string) => <option key={x}>{x}</option>)}</select>
          ) : (
            <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder={dt === "SET" ? "comma,separated" : ""} type={dt === "DATE" ? "date" : "text"} />
          )}
        </div>
        <div><label className="label">Valid from</label><input type="date" className="input" value={vf} onChange={(e) => setVf(e.target.value)} /></div>
        <div><label className="label">Valid to (optional)</label><input type="date" className="input" value={vt} onChange={(e) => setVt(e.target.value)} /></div>
        <div><label className="label">Provenance</label><select className="input" value={prov} onChange={(e) => setProv(e.target.value)}>{PROV.map((p) => <option key={p}>{p}</option>)}</select></div>
        <div><label className="label">Change kind</label><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{["CREATE", "PROSPECTIVE_CHANGE", "CORRECTION", "RETROACTIVE_CORRECTION", "WITHDRAWAL", "LATE_ARRIVAL"].map((p) => <option key={p}>{p}</option>)}</select></div>
        <div><label className="label">Source reference</label><input className="input" value={src} onChange={(e) => setSrc(e.target.value)} /></div>
        <div><label className="label">Reason</label><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end">
        <button className="btn-primary" disabled={a.busy || value === ""} onClick={() => a.run(async () => {
          const r = await api(`/subjects/${subjectId}/facts`, { method: "POST", json: {
            fact_key: fact.fact_key, value: parse(), valid_from: new Date(vf).toISOString(), valid_to: vt ? new Date(vt).toISOString() : null,
            provenance_class: prov, change_kind: kind, reason: reason || null, source_ref: src || null } });
          onDone(r);
        })}>Record assertion</button>
      </div>
    </Modal>
  );
}
