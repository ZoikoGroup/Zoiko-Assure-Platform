"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Hash, Json, KV, Modal, Notice, PageHeader, Table, Tabs, useAction } from "@/components/ui";

export default function Regulatory() {
  const [tab, setTab] = useState("Change inbox");
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §7 · ZA-DOM-001 §4" title="Regulatory content"
        subtitle="Source ingestion with rights records, governed (non-binding) extraction, human legal verification and signed content releases." />
      <Tabs tabs={["Change inbox", "Catalogue & coverage", "Content releases"]} value={tab} onChange={setTab} />
      {tab === "Change inbox" && <Inbox />}
      {tab === "Catalogue & coverage" && <Catalogue />}
      {tab === "Content releases" && <Releases />}
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
      {rv && <RequirementReview id={rv} onClose={() => setRv(null)} />}
      <RequirementList onOpen={setRv} />
    </div>
  );
}

function RequirementList({ onOpen }: { onOpen: (id: string) => void }) {
  const { data } = useApi<any[]>("/catalog/instruments");
  const reqs = (data || []).flatMap((i) => i.requirements.map((r: any) => ({ ...r, instrument: i.identifier })));
  return (
    <Card title="Requirement versions" subtitle="AI-proposed requirements stay REVIEW_REQUIRED until a qualified human legal reviewer verifies them (INV-09).">
      <Table head={["Requirement", "Instrument", "Origin", "Lifecycle", "Valid from", ""]} empty={reqs.length === 0}>
        {reqs.map((r) => (
          <tr key={r.requirement_version_id}>
            <td className="td"><div className="font-medium">{r.title}</div><div className="mono text-[11px] text-slate-500">{r.requirement_key} · {r.locator}</div></td>
            <td className="td mono text-xs">{r.instrument}</td>
            <td className="td"><Badge code={r.origin} /></td>
            <td className="td"><Badge code={r.lifecycle} /></td>
            <td className="td">{fmtDate(r.valid_from)}</td>
            <td className="td"><button className="btn-ghost" onClick={() => onOpen(r.requirement_version_id)}>Review</button></td>
          </tr>
        ))}
      </Table>
    </Card>
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

function RequirementReview({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, error, reload } = useApi<any>(`/catalog/requirements/${id}`);
  const a = useAction();
  const [edit, setEdit] = useState<any>(null);
  return (
    <Card title={data ? `Review: ${data.title}` : "Requirement"} actions={<>
      {data?.lifecycle === "REVIEW_REQUIRED" && <button className="btn-accent" disabled={a.busy} onClick={() => a.run(async () => { await api(`/catalog/requirements/${id}/verify`, { method: "POST" }); reload(); })}>Verify (legal reviewer)</button>}
      <button className="btn-ghost" onClick={onClose}>Close</button></>}>
      <ErrorBox error={error || a.error} />
      {data && (
        <>
          <KV cols={4} items={[["Key", <span key="k" className="mono">{data.requirement_key}</span>], ["Lifecycle", <Badge key="l" code={data.lifecycle} />],
            ["Origin", <Badge key="o" code={data.origin} />], ["Reviewed by", data.reviewed_by]]} />
          {data.source?.display_note && <div className="mt-3"><Notice tone="warn">{data.source.display_note}</Notice></div>}
          <div className="mt-4">
            <Table head={["Locator", "Duty bearer", "Modality", "Action", "Object", "Origin", "State", ""]} empty={!data.provisions.length}>
              {data.provisions.map((p: any) => (
                <tr key={p.provision_id}>
                  <td className="td mono text-xs">{p.locator}</td>
                  <td className="td">{p.duty_bearer === "TBD_BY_REVIEWER" ? <Badge code="MISSING" label="TBD" /> : p.duty_bearer}</td>
                  <td className="td mono text-xs">{p.modality}</td>
                  <td className="td max-w-xs text-xs">{p.action}</td>
                  <td className="td text-xs">{p.regulated_object}</td>
                  <td className="td"><Badge code={p.semantic_origin} /></td>
                  <td className="td"><Badge code={p.lifecycle} /></td>
                  <td className="td">{!["VERIFIED", "PUBLISHED"].includes(p.lifecycle) && <button className="btn-ghost" onClick={() => setEdit({ ...p })}>Encode</button>}</td>
                </tr>
              ))}
            </Table>
          </div>
        </>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title="Encode provision (human structured meaning)">
        {edit && (
          <>
            <p className="mb-3 rounded bg-navy-50 p-2 text-xs text-navy-800">{edit.normalized_meaning}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {["duty_bearer", "modality", "action", "regulated_object", "timing", "evidence_expectation"].map((k) => (
                <div key={k}><label className="label">{k.replace(/_/g, " ")}</label><input className="input" value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></div>
              ))}
              <div className="sm:col-span-2"><label className="label">Normalized meaning</label><textarea className="input" rows={3} value={edit.normalized_meaning} onChange={(e) => setEdit({ ...edit, normalized_meaning: e.target.value })} /></div>
            </div>
            <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={() => a.run(async () => {
              const { duty_bearer, modality, action, regulated_object, timing, normalized_meaning, evidence_expectation } = edit;
              await api(`/catalog/provisions/${edit.provision_id}`, { method: "PUT", json: { duty_bearer, modality, action, regulated_object, timing, normalized_meaning, evidence_expectation } });
              setEdit(null); reload();
            })}>Save encoding</button></div>
          </>
        )}
      </Modal>
    </Card>
  );
}

function Catalogue() {
  const { data, error } = useApi<any[]>("/catalog/instruments");
  const { data: od } = useApi<any[]>("/catalog/obligation-definitions");
  const { data: fds } = useApi<any[]>("/catalog/fact-definitions");
  const { data: ctl } = useApi<any[]>("/catalog/controls");
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <Card title="Instruments & coverage mode" subtitle="MONITORING_ONLY instruments are tracked for change but carry no executable applicability rules.">
        <Table head={["Instrument", "Jurisdiction", "Coverage", "Requirements", "Obligation definitions"]}>
          {data?.map((i) => (
            <tr key={i.instrument_id}>
              <td className="td"><div className="font-medium">{i.title}</div><div className="mono text-[11px] text-slate-500">{i.identifier}</div></td>
              <td className="td mono">{i.jurisdiction}</td>
              <td className="td"><Badge code={i.coverage_mode} /></td>
              <td className="td">{i.requirements.length}</td>
              <td className="td">{i.obligation_definitions.map((o: any) => <div key={o.obligation_definition_id} className="mono text-xs">{o.key}</div>)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Obligation definitions">
        <Table head={["Key", "Title", "Subject type", "Material", "Schedule", "Current rule"]}>
          {od?.map((o) => (
            <tr key={o.obligation_definition_id}>
              <td className="td mono text-xs">{o.key}</td><td className="td">{o.title}</td>
              <td className="td mono text-xs">{o.subject_type}</td><td className="td">{o.material ? "yes" : "no"}</td>
              <td className="td mono text-xs">{o.schedule_kind}</td>
              <td className="td">{o.current_rule_version ? <span className="mono text-xs">v{o.current_rule_version.version} · <Hash value={o.current_rule_version.ast_hash} n={8} /></span> : <Badge code="NOT_COVERED" />}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Fact definitions">
          <Table head={["Key", "Type", "Attaches to", "Sensitivity", "Manual attestation"]}>
            {fds?.map((f) => (
              <tr key={f.fact_definition_id}>
                <td className="td"><div className="mono text-xs">{f.key}</div><div className="text-[11px] text-slate-500">{f.label}</div></td>
                <td className="td mono text-xs">{f.data_type}</td><td className="td mono text-xs">{f.subject_type}</td>
                <td className="td text-xs">{f.sensitivity}</td><td className="td text-xs">{f.manual_attestation_policy}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Control library & framework mappings" subtitle="One implementation per control/scope; frameworks are mappings, not clones (INV-08).">
          <Table head={["Control", "Category", "Mappings"]}>
            {ctl?.map((c) => (
              <tr key={c.control_definition_id}>
                <td className="td"><div className="mono text-xs">{c.key}</div><div className="text-[11px] text-slate-500">{c.title}</div></td>
                <td className="td text-xs">{c.category}</td>
                <td className="td">{c.framework_mappings.map((m: any) => <div key={m.id} className="text-[11px]"><span className="mono">{m.framework}</span> {m.reference}{m.rights_note && <span className="text-slate-400"> · {m.rights_note}</span>}</div>)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </div>
  );
}

function Releases() {
  const { data, error } = useApi<any[]>("/catalog/releases");
  const a = useAction();
  const [ver, setVer] = useState<Record<string, any>>({});
  return (
    <Card title="Signed content releases" subtitle="Each release manifest is canonicalised, hashed and Ed25519-signed; verification is independent of the database.">
      <ErrorBox error={error || a.error} />
      <Table head={["Label", "Published", "Manifest hash", "Signer", "Impact", "Verification"]} empty={data?.length === 0}>
        {data?.map((r) => (
          <tr key={r.content_release_id} className="align-top">
            <td className="td"><div className="font-medium">{r.label}</div><div className="text-[11px] text-slate-500">{r.changelog}</div>
              {r.correction_of_release_id && <Badge code="CORRECTION" />}</td>
            <td className="td text-xs">{fmtDate(r.published_at, true)}<div className="text-slate-500">by {r.released_by}</div></td>
            <td className="td"><Hash value={r.manifest_hash} /></td>
            <td className="td mono text-xs">{r.signer_key_id}</td>
            <td className="td text-xs">{r.tenant_impacted_evaluations !== undefined ? `${r.tenant_impacted_evaluations} of your evaluations` : "aggregate only"}</td>
            <td className="td">
              {ver[r.content_release_id] ? (
                <div className="flex gap-1"><Badge code={ver[r.content_release_id].manifest_hash_ok ? "PASS" : "FAIL"} label="hash" /><Badge code={ver[r.content_release_id].signature_ok ? "PASS" : "FAIL"} label="signature" /></div>
              ) : (
                <button className="btn-ghost" onClick={() => a.run(async () => setVer({ ...ver, [r.content_release_id]: await api(`/catalog/releases/${r.content_release_id}/verify`) }))}>Verify</button>
              )}
            </td>
          </tr>
        ))}
      </Table>
      {data?.[0] && <details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">Latest manifest</summary><div className="mt-2"><Json value={data[0].manifest} /></div></details>}
    </Card>
  );
}
