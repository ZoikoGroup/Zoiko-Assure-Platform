"use client";

import { useState } from "react";
import { API, ApiError, authHeaders, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Hash, Modal, Notice, PageHeader, Table, useAction } from "@/components/ui";

export default function Evidence() {
  const { data, error, reload } = useApi<any[]>("/evidence");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cond, setCond] = useState("ALL");
  const rows = (data || []).filter((e) => (cond === "ALL" || e.condition === cond) && (!q || `${e.title} ${e.source_system ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <>
      <PageHeader spec="ZA-EVD-001 · WF-EVD-01" title="Evidence vault"
        subtitle="Encrypted, content-addressed evidence on a per-tenant hash-chained ledger. Lifecycle and admissibility are derived from append-only events."
        actions={<button className="btn-primary" onClick={() => setOpen(true)}>Upload evidence</button>} />
      <ErrorBox error={error} />
      <Card actions={<>
        <input className="input w-56 py-1 text-xs" placeholder="Search title / source" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input py-1 text-xs" value={cond} onChange={(e) => setCond(e.target.value)}>{["ALL", "CURRENT", "STALE", "INVALID", "ERASED", "CONFLICT"].map((x) => <option key={x}>{x}</option>)}</select>
      </>} title={`${rows.length} artifact(s)`}>
        <Table head={["Title", "Origin / provenance", "Coverage", "Condition", "Lifecycle", "IPE", "Ledger", "Hold"]} empty={rows.length === 0}>
          {rows.map((e) => (
            <tr key={e.evidence_id}>
              <td className="td"><A href={`/evidence/${e.evidence_id}`}>{e.title}</A><div className="mono text-[11px] text-slate-500">{e.source_system ?? e.content_type} · {e.size_bytes} B</div></td>
              <td className="td"><div className="mono text-[11px]">{e.origin_class}</div><div className="mono text-[11px] text-slate-500">{e.provenance_class}</div></td>
              <td className="td text-xs">{fmtDate(e.coverage_from)} → {fmtDate(e.coverage_to)}</td>
              <td className="td"><Badge code={e.condition} /></td>
              <td className="td"><Badge code={e.lifecycle} /></td>
              <td className="td">{e.ipe_result ? <Badge code={e.ipe_result} /> : <span className="text-xs text-slate-400">n/a</span>}</td>
              <td className="td mono text-xs">#{e.ledger_seq}</td>
              <td className="td">{e.legal_hold && <Badge tone="exception" label="LEGAL HOLD" />}</td>
            </tr>
          ))}
        </Table>
      </Card>
      {open && <Upload onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
    </>
  );
}

function Upload({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: org } = useApi<any>("/org/tenant");
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ entity_id: "", title: "", coverage_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10), coverage_to: new Date().toISOString().slice(0, 10), validity_days: "90", data_classification: "CUSTOMER_CONFIDENTIAL" });
  const a = useAction();
  const submit = () => a.run(async () => {
    const fd = new FormData();
    fd.append("file", file!);
    Object.entries(f).forEach(([k, v]) => fd.append(k, k.startsWith("coverage") ? new Date(v).toISOString() : v));
    fd.append("idempotency_key", `${file!.name}:${file!.size}:${file!.lastModified}`);
    const res = await fetch(`${API}/evidence/upload`, { method: "POST", body: fd, headers: authHeaders() });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
    onDone();
  });
  return (
    <Modal open onClose={onClose} title="Upload evidence">
      <Notice tone="warn">Manual uploads carry visibly lower provenance (P4 / HUMAN_UPLOAD). On their own they cannot satisfy controls that require automation.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2"><label className="label">File (≤ 25 MB)</label><input type="file" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
        <div className="sm:col-span-2"><label className="label">Title</label><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div><label className="label">Legal entity</label><select className="input" value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>
          <option value="">Select…</option>{org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
        <div><label className="label">Classification</label><select className="input" value={f.data_classification} onChange={(e) => setF({ ...f, data_classification: e.target.value })}>
          {["CUSTOMER_CONFIDENTIAL", "CUSTOMER_RESTRICTED", "PERSONAL_DATA", "INTERNAL"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label">Coverage from</label><input type="date" className="input" value={f.coverage_from} onChange={(e) => setF({ ...f, coverage_from: e.target.value })} /></div>
        <div><label className="label">Coverage to</label><input type="date" className="input" value={f.coverage_to} onChange={(e) => setF({ ...f, coverage_to: e.target.value })} /></div>
        <div><label className="label">Validity after coverage (days)</label><input type="number" className="input" value={f.validity_days} onChange={(e) => setF({ ...f, validity_days: e.target.value })} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!file || !f.entity_id || !f.title || a.busy} onClick={submit}>{a.busy ? "Encrypting & chaining…" : "Upload"}</button></div>
    </Modal>
  );
}
