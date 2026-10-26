"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, Drawer, ErrorBox, Hash, Modal, Notice, PageHeader, Table, useAction } from "@/components/ui";
import { RequirementReview } from "@/components/RequirementViews";

export default function Regulatory() {
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §7 · ZA-DOM-001 §4" title="Regulatory Inbox"
        subtitle="Incoming regulatory sources with rights records. Extraction is a governed, non-binding proposal until a qualified reviewer encodes and verifies it." />
      <Inbox />
    </>
  );
}

function Inbox() {
  const { data, error, reload } = useApi<any[]>("/catalog/sources");
  const [ingest, setIngest] = useState(false);
  const [extractFor, setExtractFor] = useState<any>(null);
  const [rv, setRv] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Regulatory sources" subtitle="Fingerprinted on ingest. REFERENCE_ONLY rights withhold text but retain locator and fingerprint."
        actions={<button className="btn-primary" onClick={() => setIngest(true)}>Ingest source</button>}>
        <Table head={["Title", "Authority / jurisdiction", "Rights", "Lifecycle", "Fingerprint", "Fetched", ""]} empty={data?.length === 0}>
          {data?.map((s) => (
            <tr key={s.source_id}>
              <td className="td"><div className="font-medium text-navy-900">{s.title}</div><div className="mono text-[11px] text-slate-500">{s.locator}</div></td>
              <td className="td">{s.authority} · <span className="mono">{s.jurisdiction}</span></td>
              <td className="td"><Badge code={s.rights_class} /></td>
              <td className="td"><Badge code={s.lifecycle} /></td>
              <td className="td"><Hash value={s.fingerprint} /></td>
              <td className="td text-xs">{fmtDate(s.fetched_at, true)}</td>
              <td className="td"><button className="btn-ghost" onClick={() => setExtractFor(s)}>Propose extraction</button></td>
            </tr>
          ))}
        </Table>
      </Card>
      <IngestModal open={ingest} onClose={() => setIngest(false)} onDone={() => { setIngest(false); reload(); }} />
      <ExtractModal source={extractFor} onClose={() => setExtractFor(null)} onDone={(id) => { setExtractFor(null); setRv(id); reload(); }} />
      <Drawer open={!!rv} onClose={() => setRv(null)} title="Requirement review" width="max-w-4xl">{rv && <RequirementReview id={rv} />}</Drawer>
    </div>
  );
}

function IngestModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const a = useAction();
  const [f, setF] = useState({
    authority: "European Commission", jurisdiction: "EU", source_type: "OFFICIAL_JOURNAL", title: "", locator: "",
    rights_class: "OFFICIAL_REUSE_PERMITTED", raw_text: "", instrument_identifier: "EU-2024-1689", publication_date: "", effective_date: "",
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = () => a.run(async () => {
    const body: any = { ...f };
    if (!body.publication_date) delete body.publication_date;
    if (!body.effective_date) delete body.effective_date;
    await api("/catalog/sources", { method: "POST", json: body });
    onDone();
  });
  return (
    <Modal open={open} onClose={onClose} title="Ingest regulatory source">
      <ErrorBox error={a.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        {[["authority", "Authority"], ["jurisdiction", "Jurisdiction"], ["source_type", "Source type"], ["instrument_identifier", "Instrument identifier"],
          ["title", "Title"], ["locator", "Locator (URL / ELI)"]].map(([k, l]) => (
          <div key={k}><label className="label">{l}</label><input className="input" value={(f as any)[k]} onChange={set(k)} /></div>
        ))}
        <div><label className="label">Rights class</label>
          <select className="input" value={f.rights_class} onChange={set("rights_class")}>
            {["OFFICIAL_REUSE_PERMITTED", "LICENSED_INTERNAL", "REFERENCE_ONLY"].map((x) => <option key={x}>{x}</option>)}
          </select></div>
        <div><label className="label">Publication date</label><input type="date" className="input" value={f.publication_date} onChange={set("publication_date")} /></div>
        <div className="sm:col-span-2"><label className="label">Effective date</label><input type="date" className="input" value={f.effective_date} onChange={set("effective_date")} /></div>
        <div className="sm:col-span-2"><label className="label">Source text</label><textarea rows={6} className="input" value={f.raw_text} onChange={set("raw_text")} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={a.busy} onClick={submit}>Ingest</button></div>
    </Modal>
  );
}

function ExtractModal({ source, onClose, onDone }: { source: any; onClose: () => void; onDone: (rv: string) => void }) {
  const a = useAction();
  const [f, setF] = useState({ instrument_identifier: "EU-2024-1689", requirement_key: "", requirement_title: "", locator: "", valid_from: "2026-08-02" });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  if (!source) return null;
  return (
    <Modal open onClose={onClose} title={`Governed extraction · ${source.title}`}>
      <Notice tone="warn">The extractor produces <b>non-binding proposals</b> (AI_EXTRACTED). Nothing reaches a release until a qualified reviewer encodes and verifies it.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {[["instrument_identifier", "Instrument identifier"], ["requirement_key", "Requirement key"], ["requirement_title", "Requirement title"], ["locator", "Locator (e.g. Art. 26)"]].map(([k, l]) => (
          <div key={k}><label className="label">{l}</label><input className="input" value={(f as any)[k]} onChange={set(k)} /></div>
        ))}
        <div><label className="label">Valid from</label><input type="date" className="input" value={f.valid_from} onChange={set("valid_from")} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => {
          const r = await api(`/catalog/sources/${source.source_id}/extract`, { method: "POST", json: f });
          onDone(r.requirement_version.requirement_version_id);
        })}>Propose</button>
      </div>
    </Modal>
  );
}
