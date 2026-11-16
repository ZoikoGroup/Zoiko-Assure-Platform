"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { Badge, Card, Drawer, ErrorBox, Hash, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";
import { RequirementReview } from "@/components/RequirementViews";

export function RegulatoryInbox() {
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §7 · ZA-DOM-001 §4" title="Regulatory Inbox"
        subtitle="Incoming regulatory sources and proposed extractions."
        help="Each source carries a rights record. Extraction is a governed, non-binding proposal until a qualified reviewer encodes and verifies it. Sources are ingested by Zoiko's content team." />
      <PermissionNote perms={["content_author"]} />
      <Inbox />
    </>
  );
}

function Inbox() {
  const { data, error, reload } = useApi<any[]>("/catalog/sources");
  const [ingest, setIngest] = useState(false);
  const [extractFor, setExtractFor] = useState<any>(null);
  const [rv, setRv] = useState<string | null>(null);
  const { can, why } = useCan();
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Regulatory sources" subtitle="Fingerprinted on ingest. REFERENCE_ONLY rights withhold text but retain locator and fingerprint."
        actions={<button className="btn-primary" disabled={!can("content_author")} title={why("content_author")} onClick={() => setIngest(true)}>Ingest source</button>}>
        <Table head={["Title", "Authority / jurisdiction", "Rights", "Lifecycle", "Fingerprint", "Fetched", ""]} empty={data?.length === 0} loading={!data && !error}>
          {data?.map((s) => (
            <tr key={s.source_id}>
              <td className="td"><div className="font-medium text-navy-900">{s.title}</div><div className="mono text-[11px] text-slate-500">{s.locator}</div></td>
              <td className="td">{s.authority} · <span className="mono">{s.jurisdiction}</span></td>
              <td className="td"><Badge code={s.rights_class} /></td>
              <td className="td"><Badge code={s.lifecycle} /></td>
              <td className="td"><Hash value={s.fingerprint} /></td>
              <td className="td text-xs">{fmtDate(s.fetched_at, true)}</td>
              <td className="td"><button className="btn-ghost" disabled={!can("content_author")} title={why("content_author")} onClick={() => setExtractFor(s)}>Propose extraction</button></td>
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
    authority: "", jurisdiction: "", source_type: "OFFICIAL_JOURNAL", title: "", locator: "",
    rights_class: "OFFICIAL_REUSE_PERMITTED", raw_text: "", instrument_identifier: "", publication_date: "", effective_date: "",
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const fid = useId();
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
        {([["authority", "Authority", true], ["jurisdiction", "Jurisdiction", true], ["source_type", "Source type", false], ["instrument_identifier", "Instrument identifier", true],
          ["title", "Title", false], ["locator", "Locator (URL / ELI)", false]] as const).map(([k, l, req]) => (
          <div key={k}><label className="label" htmlFor={`${fid}-${k}`}>{l}{req && " (required)"}</label><input id={`${fid}-${k}`} required={req} className="input" value={f[k]} onChange={set(k)} /></div>
        ))}
        <div><label className="label" htmlFor={`${fid}-rights`}>Rights class</label>
          <select id={`${fid}-rights`} className="input" value={f.rights_class} onChange={set("rights_class")}>
            {["OFFICIAL_REUSE_PERMITTED", "LICENSED_INTERNAL", "REFERENCE_ONLY"].map((x) => <option key={x}>{x}</option>)}
          </select></div>
        <div><label className="label" htmlFor={`${fid}-pub`}>Publication date</label><input id={`${fid}-pub`} type="date" className="input" value={f.publication_date} onChange={set("publication_date")} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-eff`}>Effective date</label><input id={`${fid}-eff`} type="date" className="input" value={f.effective_date} onChange={set("effective_date")} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-text`}>Source text</label><textarea id={`${fid}-text`} rows={6} className="input" value={f.raw_text} onChange={set("raw_text")} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={a.busy || !f.authority.trim() || !f.jurisdiction.trim() || !f.instrument_identifier.trim()} onClick={submit}>Ingest</button></div>
    </Modal>
  );
}

function ExtractModal({ source, onClose, onDone }: { source: any; onClose: () => void; onDone: (rv: string) => void }) {
  const a = useAction();
  const [f, setF] = useState({ instrument_identifier: "", requirement_key: "", requirement_title: "", locator: "", valid_from: "" });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const fid = useId();
  if (!source) return null;
  return (
    <Modal open onClose={onClose} title={`Governed extraction · ${source.title}`}>
      <Notice tone="warn">The extractor produces <b>non-binding proposals</b> (AI_EXTRACTED). Nothing reaches a release until a qualified reviewer encodes and verifies it.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {([["instrument_identifier", "Instrument identifier", true], ["requirement_key", "Requirement key", false], ["requirement_title", "Requirement title", false], ["locator", "Locator (e.g. Art. 26)", false]] as const).map(([k, l, req]) => (
          <div key={k}><label className="label" htmlFor={`${fid}-${k}`}>{l}{req && " (required)"}</label><input id={`${fid}-${k}`} required={req} className="input" value={f[k]} onChange={set(k)} /></div>
        ))}
        <div><label className="label" htmlFor={`${fid}-valid-from`}>Valid from (required)</label><input id={`${fid}-valid-from`} type="date" required className="input" value={f.valid_from} onChange={set("valid_from")} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={a.busy || !f.instrument_identifier.trim() || !f.valid_from} onClick={() => a.run(async () => {
          const r = await api(`/catalog/sources/${source.source_id}/extract`, { method: "POST", json: f });
          onDone(r.requirement_version.requirement_version_id);
        })}>Propose</button>
      </div>
    </Modal>
  );
}
