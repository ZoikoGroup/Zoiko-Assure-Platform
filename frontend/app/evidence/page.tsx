"use client";

import { useId, useState } from "react";
import { API, authHeaders, responseError, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { DATA_CLASSES } from "@/lib/codes";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, FieldError, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";
import { ConflictTable, type EvidenceConflict } from "@/components/EvidenceConflicts";
import { ManualProvenance } from "@/components/EvidenceProvenance";

export default function Evidence() {
  const { data, error, reload } = useApi<any[]>("/evidence");
  const conflicts = useApi<EvidenceConflict[]>("/evidence-conflicts");
  const inOpenConflict = new Set((conflicts.data ?? []).filter((c) => c.status === "OPEN").flatMap((c) => c.member_evidence_ids));
  const titles: Record<string, string> = Object.fromEntries((data ?? []).map((e) => [e.evidence_id, e.title]));
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cond, setCond] = useState("ALL");
  const { can, why } = useCan();
  const rows = (data || []).filter((e) => (cond === "ALL" || e.condition === cond) && (!q || `${e.title} ${e.source_system ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <>
      <PageHeader spec="ZA-EVD-001 · WF-EVD-01" title="Evidence Records"
        subtitle="The records behind your control evaluations: test outputs, connector extracts and uploads. Each is encrypted, content-addressed and chained on your organisation's own ledger; only CURRENT evidence can support current-period proof."
        actions={<button className="btn-primary" disabled={!can("submit_evidence")} title={why("submit_evidence")} onClick={() => setOpen(true)}>Upload evidence</button>} />
      <PermissionNote perms={["submit_evidence"]} />
      <ErrorBox error={error} />
      <Card actions={<>
        <input aria-label="Search evidence by title or source" className="input w-56 py-1 text-xs" placeholder="Search title / source" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Filter by condition" className="input w-36 py-1 text-xs" value={cond} onChange={(e) => setCond(e.target.value)}>{["ALL", "CURRENT", "STALE", "INVALID", "ERASED", "CONFLICTING"].map((x) => <option key={x}>{x}</option>)}</select>
      </>} title={`${data ? rows.length : "…"} artifact(s)`}>
        <Table head={["Title", "Origin / provenance", "Coverage", "Condition", "Lifecycle", "IPE", "Ledger", "Hold"]} empty={!!data && rows.length === 0} loading={!data && !error}>
          {rows.map((e) => (
            <tr key={e.evidence_id}>
              <td className="td"><A href={`/evidence/${e.evidence_id}`}>{e.title}</A><div className="mono text-[11px] text-slate-500">{e.source_system ?? e.content_type} · {e.size_bytes} B</div></td>
              <td className="td"><ManualProvenance origin={e.origin_class} /><div className="mono text-[11px]">{e.origin_class}</div><div className="mono text-[11px] text-slate-500">{e.provenance_class}</div></td>
              <td className="td text-xs">{fmtDate(e.coverage_from)} → {fmtDate(e.coverage_to)}</td>
              <td className="td"><Badge code={e.condition} />{inOpenConflict.has(e.evidence_id) && <div className="mt-0.5 text-[11px] text-violet-700">In open conflict</div>}</td>
              <td className="td"><Badge code={e.lifecycle} /></td>
              <td className="td">{e.ipe_result ? <Badge code={e.ipe_result} /> : <span className="text-xs text-slate-500">n/a</span>}</td>
              <td className="td mono text-xs">#{e.ledger_seq}</td>
              <td className="td">{e.legal_hold && <Badge tone="exception" label="LEGAL HOLD" />}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card className="mt-4" title="Evidence conflicts"
        subtitle="Separate records linking evidence that materially disagrees (ZA-EVD-001 §13). Members are never altered, so their condition above is unchanged; control evaluations treat members of an OPEN conflict as CONFLICTING until it is resolved.">
        <ErrorBox error={conflicts.error} />
        {conflicts.data ? <ConflictTable conflicts={conflicts.data} titles={titles} emptyText="No evidence conflicts have been recorded." />
          : !conflicts.error && <p className="text-xs text-slate-500">Loading conflict records…</p>}
      </Card>
      {open && <Upload onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
    </>
  );
}

/* ZA-DOM-001 §17.1 classes an uploaded payload can carry. PUBLIC_REGULATORY (global catalogue replication) and
   SECURITY_SECRET (secret reference only, never a payload) are deliberately not offered. */
const UPLOAD_CLASSIFICATIONS = ["CUSTOMER_INTERNAL", "CUSTOMER_CONFIDENTIAL", "RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"];
/* Limits of POST /evidence/upload: it rejects payloads over 25 MB (413) and stores the title in a 300-character column. */
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const MAX_TITLE = 300;

function Upload({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: org } = useApi<any>("/org/tenant");
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ entity_id: "", title: "", coverage_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10), coverage_to: new Date().toISOString().slice(0, 10), validity_days: "90", data_classification: "CUSTOMER_CONFIDENTIAL" });
  const [tried, setTried] = useState(false);
  const a = useAction();
  const fid = useId();
  // Checks taken from the endpoint's form contract: required fields, the size limit and an integer validity_days.
  // Ordering of the coverage dates and the sign of validity_days are not checked there, so they are not checked here.
  const errs = {
    file: !file ? "Choose a file." : file.size > MAX_UPLOAD_BYTES ? "This file is larger than 25 MB, the upload limit." : "",
    title: !f.title.trim() ? "Enter a title. Blank or whitespace-only text is not accepted." : "",
    entity: !f.entity_id ? "Choose a legal entity." : "",
    from: !f.coverage_from ? "Enter the date coverage starts." : "",
    to: !f.coverage_to ? "Enter the date coverage ends." : "",
    validity: !/^-?\d+$/.test(f.validity_days) ? "Enter a whole number of days." : "",
  };
  const show = (k: keyof typeof errs) => (tried || (k === "file" && !!file) ? errs[k] : "");
  const invalid = (k: keyof typeof errs) => ({ "aria-invalid": !!show(k) || undefined, "aria-describedby": show(k) ? `${fid}-${k}-error` : undefined });
  const submit = () => {
    const first = (Object.keys(errs) as (keyof typeof errs)[]).find((k) => errs[k]);
    if (first) { setTried(true); document.getElementById(`${fid}-${first}`)?.focus(); return; }
    a.run(async () => {
      const fd = new FormData();
      fd.append("file", file!);
      Object.entries({ ...f, title: f.title.trim() }).forEach(([k, v]) => fd.append(k, k.startsWith("coverage") ? new Date(v).toISOString() : v));
      fd.append("idempotency_key", `${file!.name}:${file!.size}:${file!.lastModified}`);
      const res = await fetch(`${API}/evidence/upload`, { method: "POST", body: fd, headers: authHeaders() });
      if (!res.ok) throw await responseError(res);
      onDone();
    });
  };
  return (
    <Modal open onClose={onClose} title="Upload evidence">
      <Notice tone="warn">Manual uploads carry visibly lower provenance (P4 / HUMAN_UPLOAD). On their own they cannot satisfy controls that require automation.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-file`}>File (≤ 25 MB, required)</label><input id={`${fid}-file`} type="file" required className="input" {...invalid("file")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <FieldError id={`${fid}-file`} message={show("file")} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-title`}>Title (required)</label><input id={`${fid}-title`} required maxLength={MAX_TITLE} className="input" {...invalid("title")} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
          <FieldError id={`${fid}-title`} message={show("title")} /></div>
        <div><label className="label" htmlFor={`${fid}-entity`}>Legal entity (required)</label><select id={`${fid}-entity`} required className="input" {...invalid("entity")} value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>
          <option value="">Select…</option>{org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select>
          <FieldError id={`${fid}-entity`} message={show("entity")} /></div>
        <div><label className="label" htmlFor={`${fid}-class`}>Classification</label><select id={`${fid}-class`} className="input" aria-describedby={`${fid}-class-hint`} value={f.data_classification} onChange={(e) => setF({ ...f, data_classification: e.target.value })}>
          {UPLOAD_CLASSIFICATIONS.map((x) => <option key={x}>{x}</option>)}</select>
          <p id={`${fid}-class-hint`} className="mt-1 text-[11px] text-slate-500">ZA-DOM-001 §17.1 example: {DATA_CLASSES[f.data_classification]}</p></div>
        <div><label className="label" htmlFor={`${fid}-from`}>Coverage from (required)</label><input id={`${fid}-from`} type="date" required className="input" {...invalid("from")} value={f.coverage_from} onChange={(e) => setF({ ...f, coverage_from: e.target.value })} />
          <FieldError id={`${fid}-from`} message={show("from")} /></div>
        <div><label className="label" htmlFor={`${fid}-to`}>Coverage to (required)</label><input id={`${fid}-to`} type="date" required className="input" {...invalid("to")} value={f.coverage_to} onChange={(e) => setF({ ...f, coverage_to: e.target.value })} />
          <FieldError id={`${fid}-to`} message={show("to")} /></div>
        <div><label className="label" htmlFor={`${fid}-validity`}>Validity after coverage (days)</label><input id={`${fid}-validity`} type="number" step={1} className="input" {...invalid("validity")} value={f.validity_days} onChange={(e) => setF({ ...f, validity_days: e.target.value })} />
          <FieldError id={`${fid}-validity`} message={show("validity")} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!file || !f.entity_id || !f.title || a.busy} onClick={submit}>{a.busy ? "Encrypting & chaining…" : "Upload"}</button></div>
    </Modal>
  );
}
