"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Upload as UploadIcon } from "lucide-react";
import { uploadForm, useApi } from "@/lib/api";
import { fmtBytes, fmtDate } from "@/lib/format";
import { DATA_CLASSES } from "@/lib/codes";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, FieldError, Modal, PageHeader, PermissionNote, SearchInput, Table, useAction, useToast } from "@/components/ui";
import { ConflictTable, type EvidenceConflict } from "@/components/EvidenceConflicts";
import { ManualProvenance } from "@/components/EvidenceProvenance";

/* Plain names for the recorded origin classes. The code itself stays visible beneath, for audit matching. */
const ORIGIN: Record<string, string> = {
  HUMAN_UPLOAD: "Manual upload", ATTESTATION: "Human attestation", SYSTEM_DIRECT: "System extract", EXTERNAL_IMPORT: "Imported from tool",
};
const CONDITIONS = ["CURRENT", "STALE", "INVALID", "CONFLICTING", "ERASED"];

export default function Evidence() {
  const { data, error, reload, loading } = useApi<any[]>("/evidence");
  const conflicts = useApi<EvidenceConflict[]>("/evidence-conflicts");
  const inOpenConflict = new Set((conflicts.data ?? []).filter((c) => c.status === "OPEN").flatMap((c) => c.member_evidence_ids));
  const titles: Record<string, string> = Object.fromEntries((data ?? []).map((e) => [e.evidence_id, e.title]));
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cond, setCond] = useState("ALL");
  const [fresh, setFresh] = useState<string | null>(null);
  const toast = useToast();
  const { can, why } = useCan();
  const canUpload = can("submit_evidence");
  const all = data ?? [];
  const counts = Object.fromEntries(CONDITIONS.map((c) => [c, all.filter((e) => e.condition === c).length]));
  const rows = all.filter((e) => (cond === "ALL" || e.condition === cond) && (!q || `${e.title} ${e.source_system ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const uploadButton = (
    <button className="btn-primary" disabled={!canUpload} title={why("submit_evidence")} onClick={() => setOpen(true)}>
      <UploadIcon size={14} aria-hidden />Upload evidence
    </button>
  );
  return (
    <>
      <PageHeader title="Evidence Records" spec="ZA-EVD-001 · WF-EVD-01"
        subtitle="Files and system extracts that show your controls operated."
        help={<>
          <p>Evidence comes from control test outputs, connector extracts and manual uploads. Each record is encrypted, content-addressed and chained on your organisation&apos;s own ledger, so it cannot be changed after it is recorded.</p>
          <p>Only <b>CURRENT</b> evidence can support current-period proof. STALE evidence is past its validity date; manual uploads carry lower provenance (P4) and cannot on their own satisfy a control that requires automation.</p>
        </>}
        actions={uploadButton} />
      <PermissionNote perms={["submit_evidence"]} actions={{ submit_evidence: "Upload evidence" }} />
      <ErrorBox error={error} />
      <Card bodyClassName="p-0"
        title={<span>Evidence <span className="font-normal text-slate-500">{data ? `· ${rows.length}${rows.length !== all.length ? ` of ${all.length}` : ""}` : ""}</span></span>}
        actions={<>
          <SearchInput aria-label="Search evidence by title or source" className="w-56" inputClassName="py-1 text-xs" placeholder="Search title or source" value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="Filter by condition" className="input w-40 py-1 text-xs" value={cond} onChange={(e) => setCond(e.target.value)}>
            <option value="ALL">All conditions{data ? ` (${all.length})` : ""}</option>
            {CONDITIONS.map((x) => <option key={x} value={x}>{x}{data ? ` (${counts[x]})` : ""}</option>)}
          </select>
        </>}>
        <Table head={["Title", "Source", "Coverage", "Condition", "Lifecycle", "IPE", "Ledger"]} loading={loading && !data}
          empty={!!data && rows.length === 0}
          emptyTitle={all.length ? "No evidence matches these filters" : "No evidence recorded yet"}
          emptyDescription={all.length ? undefined : canUpload ? "Upload a file to record your first piece of evidence." : "Evidence appears here when a connector extracts it or a Control Owner uploads it."}
          emptyAction={!all.length && canUpload ? uploadButton : all.length ? <button className="btn-ghost" onClick={() => { setQ(""); setCond("ALL"); }}>Clear filters</button> : undefined}>
          {rows.map((e) => (
            <tr key={e.evidence_id} className={fresh === e.evidence_id ? "bg-emerald-50/70" : undefined}>
              <td className="td min-w-[13rem] max-w-[22rem]">
                <A href={`/evidence/${e.evidence_id}`}>{e.title}</A>
                <div className="text-[11px] text-slate-500">{e.source_system ?? e.content_type} · {fmtBytes(e.size_bytes)}</div>
                {e.legal_hold && <div className="mt-1"><Badge tone="exception" label="LEGAL HOLD" /></div>}
              </td>
              <td className="td">
                {ORIGIN[e.origin_class] && !["HUMAN_UPLOAD", "ATTESTATION"].includes(e.origin_class) ? <span className="text-xs text-slate-800">{ORIGIN[e.origin_class]}</span> : <ManualProvenance origin={e.origin_class} />}
                <div className="mono text-[10px] text-slate-500" title={`${e.origin_class} · ${e.provenance_class}`}>{e.provenance_class}</div>
              </td>
              <td className="td whitespace-nowrap text-xs">{fmtDate(e.coverage_from)} – {fmtDate(e.coverage_to)}</td>
              <td className="td"><Badge code={e.condition} />{inOpenConflict.has(e.evidence_id) && <div className="mt-0.5 text-[11px] text-violet-700">In open conflict</div>}</td>
              <td className="td"><Badge code={e.lifecycle} /></td>
              <td className="td">{e.ipe_result ? <Badge code={e.ipe_result} /> : <span className="text-xs text-slate-400" title="No IPE check applies to this evidence">—</span>}</td>
              <td className="td mono text-xs">#{e.ledger_seq}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card className="mt-4" title="Evidence conflicts"
        subtitle="Evidence from different sources that materially disagrees.">
        <ErrorBox error={conflicts.error} />
        {conflicts.data ? (conflicts.data.length
          ? <>
            <p className="mb-2 text-xs text-slate-500">Members are never altered. Control evaluations treat members of an OPEN conflict as CONFLICTING until it is resolved (ZA-EVD-001 §13).</p>
            <ConflictTable conflicts={conflicts.data} titles={titles} emptyText="" />
          </>
          : <p className="text-xs text-slate-500">No evidence conflicts have been recorded.</p>)
          : !conflicts.error && <p className="text-xs text-slate-500">Loading conflict records…</p>}
      </Card>
      {open && <Upload onClose={() => setOpen(false)} onDone={(r, title) => {
        setOpen(false);
        setQ(""); setCond("ALL");
        setFresh(r.evidence_id);
        toast(`Evidence “${title}” recorded on the ledger as #${r.ledger_seq}.`, "good");
        reload();
      }} />}
    </>
  );
}

/* ZA-DOM-001 §17.1 classes an uploaded payload can carry. PUBLIC_REGULATORY (global catalogue replication) and
   SECURITY_SECRET (secret reference only, never a payload) are deliberately not offered. */
const UPLOAD_CLASSIFICATIONS = ["CUSTOMER_INTERNAL", "CUSTOMER_CONFIDENTIAL", "RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"];
const CLASS_LABEL: Record<string, string> = {
  CUSTOMER_INTERNAL: "Internal", CUSTOMER_CONFIDENTIAL: "Confidential", RESTRICTED_PERSONAL: "Restricted personal data", LEGAL_PRIVILEGED: "Legally privileged",
};
/* Limits of POST /evidence/upload: it rejects payloads over 25 MB (413) and stores the title in a 300-character column.
   It accepts any file type (the content type is recorded as sent), so no type filter is applied here. */
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const MAX_TITLE = 300;
const today = () => new Date().toISOString().slice(0, 10);
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

type UploadResult = { evidence_id: string; ledger_seq: number; acknowledged: boolean };

function Upload({ onClose, onDone }: { onClose: () => void; onDone: (r: UploadResult, title: string) => void }) {
  const org = useApi<any>("/org/tenant");
  const entities: any[] = org.data?.entities ?? [];
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ entity_id: "", title: "", coverage_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10), coverage_to: today(), validity_days: "90", data_classification: "CUSTOMER_CONFIDENTIAL" });
  const [tried, setTried] = useState(false);
  const [sent, setSent] = useState<number | null>(null);
  // One key per upload attempt of one file: a retry or a double click is answered with the same record by the server,
  // while choosing another file (or the same file again for a new record) gets a new key.
  const key = useRef(newKey());
  const a = useAction();
  const fid = useId();
  // A single legal entity needs no choice.
  useEffect(() => { if (entities.length === 1 && !f.entity_id) setF((x) => ({ ...x, entity_id: entities[0].entity_id })); }, [entities, f.entity_id]);
  // Checks from the endpoint's form contract: required fields, the size limit and a whole-number validity_days.
  // The ordering of the coverage dates and the sign of validity_days are not checked there, so not here either.
  const errs = {
    file: !file ? "Choose a file." : file.size > MAX_UPLOAD_BYTES ? `This file is ${fmtBytes(file.size)}; the limit is 25 MB.` : file.size === 0 ? "This file is empty." : "",
    title: !f.title.trim() ? "Enter a title." : "",
    entity: !f.entity_id ? "Choose the legal entity this evidence belongs to." : "",
    from: !f.coverage_from ? "Enter the date coverage starts." : "",
    to: !f.coverage_to ? "Enter the date coverage ends." : "",
    validity: !/^-?\d+$/.test(f.validity_days) ? "Enter a whole number of days." : "",
  };
  const show = (k: keyof typeof errs) => (tried || (k === "file" && !!file) ? errs[k] : "");
  const invalid = (k: keyof typeof errs) => ({ "aria-invalid": !!show(k) || undefined, "aria-describedby": show(k) ? `${fid}-${k}-error` : undefined });
  const pick = (next: File | null) => {
    setFile(next);
    key.current = newKey();
    // Suggest a title from the file name when none has been typed.
    if (next && !f.title.trim()) setF((x) => ({ ...x, title: next.name.replace(/\.[^.]+$/, "").slice(0, MAX_TITLE) }));
  };
  const submit = () => {
    if (a.busy) return;
    const first = (Object.keys(errs) as (keyof typeof errs)[]).find((k) => errs[k]);
    if (first) { setTried(true); document.getElementById(`${fid}-${first}`)?.focus(); return; }
    a.run(async () => {
      const fd = new FormData();
      fd.append("file", file!);
      Object.entries({ ...f, title: f.title.trim() }).forEach(([k, v]) => fd.append(k, k.startsWith("coverage") ? new Date(v).toISOString() : v));
      fd.append("idempotency_key", key.current);
      setSent(0);
      try {
        const r = await uploadForm<UploadResult>("/evidence/upload", fd, setSent);
        if (!r?.acknowledged || !r.evidence_id) throw new Error("The server did not acknowledge the upload. Nothing was recorded; try again.");
        onDone(r, f.title.trim());
      } finally {
        setSent(null);
      }
    });
  };
  const close = () => { if (!a.busy) onClose(); };
  return (
    <Modal open onClose={close} title="Upload evidence">
      <form noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <p className="mb-3 text-[12px] leading-5 text-slate-600">
          Manual uploads are recorded with lower provenance (<span className="mono">P4 · manual, unattested</span>). On their own they can&apos;t satisfy a control that requires automated evidence.
        </p>
        <ErrorBox error={a.error || org.error} />
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="label" htmlFor={`${fid}-file`}>File <span className="text-red-700">*</span></label>
            <input id={`${fid}-file`} type="file" required disabled={a.busy} className="input file:mr-3 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1 file:text-xs" {...invalid("file")} onChange={(e) => pick(e.target.files?.[0] ?? null)} />
            <p className="mt-1 text-[11px] text-slate-500">Any file type, up to 25 MB.{file ? ` Selected: ${file.name} (${fmtBytes(file.size)}).` : ""}</p>
            <FieldError id={`${fid}-file`} message={show("file")} />
          </div>
          <div className="sm:col-span-2">
            <label className="label" htmlFor={`${fid}-title`}>Title <span className="text-red-700">*</span></label>
            <input id={`${fid}-title`} required maxLength={MAX_TITLE} disabled={a.busy} className="input" {...invalid("title")} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Quarterly access review sign-off" />
            <FieldError id={`${fid}-title`} message={show("title")} />
          </div>
          <div>
            <label className="label" htmlFor={`${fid}-entity`}>Legal entity <span className="text-red-700">*</span></label>
            <select id={`${fid}-entity`} required disabled={a.busy || !org.data} className="input" {...invalid("entity")} value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>
              <option value="">{org.loading ? "Loading entities…" : entities.length ? "Select…" : "No legal entities available"}</option>
              {entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}
            </select>
            <FieldError id={`${fid}-entity`} message={show("entity")} />
          </div>
          <div>
            <label className="label" htmlFor={`${fid}-class`}>Classification</label>
            <select id={`${fid}-class`} disabled={a.busy} className="input" aria-describedby={`${fid}-class-hint`} value={f.data_classification} onChange={(e) => setF({ ...f, data_classification: e.target.value })}>
              {UPLOAD_CLASSIFICATIONS.map((x) => <option key={x} value={x}>{CLASS_LABEL[x]}</option>)}
            </select>
            <p id={`${fid}-class-hint`} className="mt-1 text-[11px] text-slate-500">{DATA_CLASSES[f.data_classification]}</p>
          </div>
          <div>
            <label className="label" htmlFor={`${fid}-from`}>Covers from <span className="text-red-700">*</span></label>
            <input id={`${fid}-from`} type="date" required disabled={a.busy} className="input" {...invalid("from")} value={f.coverage_from} onChange={(e) => setF({ ...f, coverage_from: e.target.value })} />
            <FieldError id={`${fid}-from`} message={show("from")} />
          </div>
          <div>
            <label className="label" htmlFor={`${fid}-to`}>Covers to <span className="text-red-700">*</span></label>
            <input id={`${fid}-to`} type="date" required disabled={a.busy} className="input" {...invalid("to")} value={f.coverage_to} onChange={(e) => setF({ ...f, coverage_to: e.target.value })} />
            <FieldError id={`${fid}-to`} message={show("to")} />
          </div>
          <div>
            <label className="label" htmlFor={`${fid}-validity`}>Stays current for (days after coverage ends)</label>
            <input id={`${fid}-validity`} type="number" step={1} disabled={a.busy} className="input" {...invalid("validity")} value={f.validity_days} onChange={(e) => setF({ ...f, validity_days: e.target.value })} />
            <FieldError id={`${fid}-validity`} message={show("validity")} />
          </div>
        </div>
        <p className="mt-2 text-[11px] text-slate-500"><span className="text-red-700">*</span> Required</p>
        {sent !== null && (
          <div role="status" className="mt-3">
            <div className="mb-1 text-[12px] text-slate-600">{sent < 1 ? `Uploading… ${Math.round(sent * 100)}%` : "Encrypting and recording on the ledger…"}</div>
            <div className="h-1.5 overflow-hidden rounded bg-slate-100"><div className="h-full bg-navy-600 transition-[width]" style={{ width: `${Math.max(4, Math.round(sent * 100))}%` }} /></div>
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={a.busy} onClick={close}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={a.busy} aria-busy={a.busy}>{a.busy ? "Uploading…" : "Upload evidence"}</button>
        </div>
      </form>
    </Modal>
  );
}
