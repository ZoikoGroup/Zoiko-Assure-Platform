"use client";

import { useId, useState } from "react";
import { API, api, download, responseError, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { Badge, Card, ErrorBox, Hash, Json, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

export default function Packages() {
  const { data, error, reload } = useApi<any[]>("/packages");
  const { data: org } = useApi<any>("/org/tenant");
  const { data: obls } = useApi<any[]>("/obligations");
  const { data: pk } = useApi<any>("/trust/public-key");
  const [f, setF] = useState({ entity_id: "", period_from: "", period_to: "", include_payloads: true });
  const [sel, setSel] = useState<string[]>([]);
  const a = useAction();
  const vr = useAction();
  const { can, why } = useCan();
  const fid = useId();
  const [file, setFile] = useState<File | null>(null);
  const entObls = (obls || []).filter((o) => o.entity_id === f.entity_id && o.latest_snapshot);
  const verifyUpload = () => vr.run(async () => {
    const fd = new FormData(); fd.append("file", file!);
    const res = await fetch(`${API}/packages/verify-upload`, { method: "POST", body: fd });
    if (!res.ok) throw await responseError(res);
    return res.json();
  });
  return (
    <>
      <PageHeader spec="ZA-EVD-001 §12 · WF-EVD-05/06" title="Audit Package Builder"
        subtitle="Signed, self-describing zip packages with snapshots, evaluations, IPE statements, evidence, ledger proofs and a manifest. Anyone can verify them offline with the open verifier." />
      <PermissionNote perms={["package"]} />
      <ErrorBox error={error} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Build package">
          <ErrorBox error={a.error} />
          <div className="grid gap-3 sm:grid-cols-3">
            <div><label className="label" htmlFor={`${fid}-entity`}>Legal entity (required)</label><select id={`${fid}-entity`} required className="input" value={f.entity_id} onChange={(e) => { setF({ ...f, entity_id: e.target.value }); setSel([]); }}>
              <option value="">Select…</option>{org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
            <div><label className="label" htmlFor={`${fid}-from`}>Period from (required)</label><input id={`${fid}-from`} type="date" required className="input" value={f.period_from} onChange={(e) => setF({ ...f, period_from: e.target.value })} /></div>
            <div><label className="label" htmlFor={`${fid}-to`}>Period to (required)</label><input id={`${fid}-to`} type="date" required className="input" value={f.period_to} onChange={(e) => setF({ ...f, period_to: e.target.value })} /></div>
          </div>
          <fieldset className="mt-3"><legend className="label">Obligations (required; each one&apos;s most recently frozen snapshot is included, whatever period it covers)</legend>
            {f.entity_id && !entObls.length && <p className="text-xs text-slate-500">No obligations with snapshots for this entity.</p>}
            {entObls.map((o) => (
              <label key={o.lineage_id} className="flex items-center gap-2 py-1 text-sm">
                <input type="checkbox" checked={sel.includes(o.lineage_id)} onChange={(e) => setSel(e.target.checked ? [...sel, o.lineage_id] : sel.filter((x) => x !== o.lineage_id))} />
                {o.definition.title} <Badge code={o.latest_snapshot.state} />
                <span className="text-xs text-slate-500">{fmtDate(o.latest_snapshot.period_from)} → {fmtDate(o.latest_snapshot.period_to)} · <span className="mono">{o.latest_snapshot.perspective}</span> · cutoff #{o.latest_snapshot.record_cutoff_seq}</span>
              </label>
            ))}
          </fieldset>
          <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.include_payloads} onChange={(e) => setF({ ...f, include_payloads: e.target.checked })} />Include decrypted evidence payloads</label>
          <div className="mt-4"><button className="btn-primary" disabled={!can("package") || !f.entity_id || !f.period_from || !f.period_to || !sel.length || a.busy} title={why("package")} onClick={() => a.run(async () => {
            await api("/packages", { method: "POST", json: { ...f, period_from: new Date(f.period_from).toISOString(), period_to: new Date(f.period_to).toISOString(), obligation_lineage_ids: sel } }); reload();
          })}>{a.busy ? "Building & signing…" : "Build signed package"}</button></div>
        </Card>
        <Card title="Independent verification" subtitle="Upload any package. It is checked with the same logic as the standalone verifier (backend/verifier/verify_package.py), with no login or tenant secret.">
          <ErrorBox error={vr.error} />
          <input type="file" accept=".zip" aria-label="Package file to verify (.zip)" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button className="btn-primary mt-2" disabled={!file || vr.busy} onClick={verifyUpload}>Verify upload</button>
          {pk && <details className="mt-3"><summary className="cursor-pointer text-xs text-navy-700">Trust anchor: {pk.signer_key_id}</summary><pre className="mono mt-1 whitespace-pre-wrap break-all text-[11px]">{pk.public_key_pem}</pre></details>}
        </Card>
      </div>
      {vr.result && <div className="mt-4"><VerifyReport r={vr.result} /></div>}
      <Card className="mt-4" title="Packages">
        <Table head={["Package", "Entity", "Period", "Cutoff", "Manifest hash", "Status", "Created", ""]} empty={data?.length === 0} loading={!data && !error}>
          {data?.map((p) => <PackageRow key={p.package_id} p={p} />)}
        </Table>
      </Card>
    </>
  );
}

function PackageRow({ p }: { p: any }) {
  const a = useAction();
  return (
    <>
      <tr>
        <td className="td"><Hash value={p.package_id} n={8} /></td><td className="td mono text-xs">{p.entity_id}</td>
        <td className="td text-xs">{fmtDate(p.period_from)} → {fmtDate(p.period_to)}</td><td className="td mono text-xs">#{p.record_cutoff_seq}</td>
        <td className="td"><Hash value={p.manifest_hash} /></td><td className="td"><Badge code={p.status} /></td>
        <td className="td text-xs">{fmtDate(p.created_at, true)}<div className="text-slate-500">{p.created_by}</div></td>
        <td className="td whitespace-nowrap">
          <button className="btn-ghost" onClick={() => a.run(() => download(`/packages/${p.package_id}/download`, `zoiko-assure-package-${p.package_id}.zip`))}>Download</button>
          <button className="btn-ghost" disabled={a.busy} onClick={() => a.run(() => api(`/packages/${p.package_id}/verify`, { method: "POST" }))}>Verify</button>
        </td>
      </tr>
      {(a.result?.steps || a.error) && <tr><td colSpan={8} className="px-3 pb-3"><ErrorBox error={a.error} />{a.result?.steps && <VerifyReport r={a.result} />}</td></tr>}
    </>
  );
}

function VerifyReport({ r }: { r: any }) {
  const steps: any[] = r.steps || [];
  return (
    <Card title="Verification report" actions={r.result ? <Badge code={r.result} /> : <span className="text-xs text-slate-500">No result returned</span>}>
      {r.warnings?.length > 0 && <Notice tone="warn">{r.warnings.join(" · ")}</Notice>}
      {r.reason_codes?.length > 0 && <p className="my-2 mono text-xs text-red-700">{r.reason_codes.join(", ")}</p>}
      {steps.length ? (
        <Table head={["Step", "Result", "Detail"]}>
          {steps.map((s, i) => <tr key={i}><td className="td text-sm">{s.step}</td><td className="td"><Badge code={s.result} /></td><td className="td break-all text-xs">{s.detail ?? ""}</td></tr>)}
        </Table>
      ) : <Json value={r} />}
    </Card>
  );
}
