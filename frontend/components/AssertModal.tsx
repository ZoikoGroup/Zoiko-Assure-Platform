"use client";

import { useId, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox, Modal, useAction } from "./ui";

export function AssertModal({ subjectId, fact, onClose, onDone }: { subjectId: string; fact: any; onClose: () => void; onDone: (r: any) => void }) {
  const [value, setValue] = useState("");
  const [vf, setVf] = useState(new Date().toISOString().slice(0, 10));
  const [vt, setVt] = useState("");
  const [kind, setKind] = useState("CREATE");
  const [reason, setReason] = useState("");
  const [src, setSrc] = useState("");
  const a = useAction();
  const fid = useId();
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
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-value`}>Value (required)</label>
          {dt === "BOOLEAN" ? (
            <select id={`${fid}-value`} required className="input" value={value} onChange={(e) => setValue(e.target.value)}><option value="">Select…</option><option>true</option><option>false</option></select>
          ) : fact.enum_domain ? (
            <select id={`${fid}-value`} required className="input" value={value} onChange={(e) => setValue(e.target.value)}><option value="">Select…</option>{fact.enum_domain.map((x: string) => <option key={x}>{x}</option>)}</select>
          ) : (
            <input id={`${fid}-value`} required className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder={dt === "SET" ? "comma,separated" : ""} type={dt === "DATE" ? "date" : "text"} />
          )}
        </div>
        <div><label className="label" htmlFor={`${fid}-vf`}>Valid from</label><input id={`${fid}-vf`} type="date" className="input" value={vf} onChange={(e) => setVf(e.target.value)} /></div>
        <div><label className="label" htmlFor={`${fid}-vt`}>Valid to (optional)</label><input id={`${fid}-vt`} type="date" className="input" value={vt} onChange={(e) => setVt(e.target.value)} /></div>
        <div><div className="label">Provenance</div><p className="input bg-navy-50 text-xs text-slate-600">Assigned by the server from who is asserting</p></div>
        <div><label className="label" htmlFor={`${fid}-kind`}>Change kind</label><select id={`${fid}-kind`} className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{["CREATE", "PROSPECTIVE_CHANGE", "CORRECTION", "RETROACTIVE_CORRECTION", "WITHDRAWAL", "LATE_ARRIVAL"].map((p) => <option key={p}>{p}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-src`}>Source reference</label><input id={`${fid}-src`} className="input" value={src} onChange={(e) => setSrc(e.target.value)} /></div>
        <div><label className="label" htmlFor={`${fid}-reason`}>Reason</label><input id={`${fid}-reason`} className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end">
        <button className="btn-primary" disabled={a.busy || value === ""} onClick={() => a.run(async () => {
          const r = await api(`/subjects/${subjectId}/facts`, { method: "POST", json: {
            fact_key: fact.fact_key, value: parse(), valid_from: new Date(vf).toISOString(), valid_to: vt ? new Date(vt).toISOString() : null,
            change_kind: kind, reason: reason || null, source_ref: src || null } });
          onDone(r);
        })}>Record assertion</button>
      </div>
    </Modal>
  );
}
