"use client";

import { useState } from "react";
import { API, ApiError, api, download, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Hash, Json, Notice, PageHeader, Table, useAction } from "@/components/ui";

export default function Packages() {
  const { data, error, reload } = useApi<any[]>("/packages");
  const { data: org } = useApi<any>("/org/tenant");
  const { data: obls } = useApi<any[]>("/obligations");
  const { data: pk } = useApi<any>("/trust/public-key");
  const [f, setF] = useState({ entity_id: "", period_from: "2026-07-01", period_to: "2026-10-01", include_payloads: true });
  const [sel, setSel] = useState<string[]>([]);
  const a = useAction();
  const vr = useAction();
  const [file, setFile] = useState<File | null>(null);
  const entObls = (obls || []).filter((o) => o.entity_id === f.entity_id && o.latest_snapshot);
  const verifyUpload = () => vr.run(async () => {
    const fd = new FormData(); fd.append("file", file!);
    const res = await fetch(`${API}/packages/verify-upload`, { method: "POST", body: fd });
    const body = await res.json();
    if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
    return body;
  });
  return (
    <>
      <PageHeader spec="ZA-EVD-001 §12 · WF-EVD-05/06" title="Audit packages"
        subtitle="Signed, self-describing zip packages with snapshots, evaluations, IPE statements, evidence, ledger proofs and a manifest. Anyone can verify them offline with the open verifier." />
      <ErrorBox error={error} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Build package">
          <ErrorBox error={a.error} />
          <div className="grid gap-3 sm:grid-cols-3">
            <div><label className="label">Legal entity</label><select className="input" value={f.entity_id} onChange={(e) => { setF({ ...f, entity_id: e.target.value }); setSel([]); }}>
              <option value="">Select…</option>{org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
            <div><label className="label">Period from</label><input type="date" className="input" value={f.period_from} onChange={(e) => setF({ ...f, period_from: e.target.value })} /></div>
            <div><label className="label">Period to</label><input type="date" className="input" value={f.period_to} onChange={(e) => setF({ ...f, period_to: e.target.value })} /></div>
          </div>
          <div className="mt-3"><label className="label">Obligations (latest frozen snapshot is included)</label>
            {f.entity_id && !entObls.length && <p className="text-xs text-slate-500">No obligations with snapshots for this entity.</p>}
            {entObls.map((o) => (
              <label key={o.lineage_id} className="flex items-center gap-2 py-1 text-sm">
                <input type="checkbox" checked={sel.includes(o.lineage_id)} onChange={(e) => setSel(e.target.checked ? [...sel, o.lineage_id] : sel.filter((x) => x !== o.lineage_id))} />
                {o.definition.title} <Badge code={o.latest_snapshot.state} />
              </label>
            ))}
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.include_payloads} onChange={(e) => setF({ ...f, include_payloads: e.target.checked })} />Include decrypted evidence payloads</label>
          <div className="mt-4"><button className="btn-primary" disabled={!f.entity_id || !sel.length || a.busy} onClick={() => a.run(async () => {
            await api("/packages", { method: "POST", json: { ...f, period_from: new Date(f.period_from).toISOString(), period_to: new Date(f.period_to).toISOString(), obligation_lineage_ids: sel } }); reload();
          })}>{a.busy ? "Building & signing…" : "Build signed package"}</button></div>
        </Card>
        <Card title="Independent verification" subtitle="Upload any package. It is checked with the same logic as the standalone verifier (backend/verifier/verify_package.py), with no login or tenant secret.">
          <ErrorBox error={vr.error} />
          <input type="file" accept=".zip" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button className="btn-primary mt-2" disabled={!file || vr.busy} onClick={verifyUpload}>Verify upload</button>
          {pk && <details className="mt-3"><summary className="cursor-pointer text-xs text-navy-700">Trust anchor: {pk.signer_key_id}</summary><pre className="mono mt-1 whitespace-pre-wrap break-all text-[10px]">{pk.public_key_pem}</pre></details>}
        </Card>
      </div>
      {vr.result && <div className="mt-4"><VerifyReport r={vr.result} /></div>}
      <Card className="mt-4" title="Packages">
        <Table head={["Package", "Entity", "Period", "Cutoff", "Manifest hash", "Status", "Created", ""]} empty={data?.length === 0}>
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
    <Card title="Verification report" actions={<Badge code={r.result ?? (r.ok ? "VERIFIED" : "FAILED")} />}>
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
