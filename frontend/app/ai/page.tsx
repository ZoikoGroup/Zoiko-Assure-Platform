"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Json, KV, Modal, Notice, PageHeader, Table, useAction } from "@/components/ui";

const LEVELS = [["A0", "Observe"], ["A1", "Recommend"], ["A2", "Prepare"], ["A3", "Execute (reversible)"]];

export default function AI() {
  const { data, error, reload } = useApi<any[]>("/ai/assets");
  const [reg, setReg] = useState(false);
  const [grant, setGrant] = useState<any>(null);
  const [ver, setVer] = useState<any>(null);
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §12 · FR-AI-001..006" title="AI & agent assets"
        subtitle="AI asset registry with AgBOM, the obligations that apply to each asset, scoped authority grants and a deterministic policy decision point. No model call sits on the decision path."
        actions={<button className="btn-primary" onClick={() => setReg(true)}>Register asset</button>} />
      <ErrorBox error={error} />
      <Notice>Authority levels run A0 Observe → A1 Recommend → A2 Prepare → A3 Execute (reversible). A4 material autonomy is excluded from the initial production scope. Enforcement stays in the customer runtime; Assure returns a decision, its obligation context and a receipt.</Notice>
      <div className="mt-4 space-y-4">
        {data?.map((s) => {
          const v = s.versions[0];
          return (
            <Card key={s.subject_id} title={s.display_name} subtitle={v ? `${v.asset_kind} · ${v.version_label} · ${v.provider} / ${v.model} · ${v.environment}` : "No version"}
              actions={<><button className="btn-ghost" onClick={() => setVer(s)}>New version</button><button className="btn-ghost" onClick={() => setGrant(s)}>Grant authority</button>
                <A href={`/entities/${s.subject_id}`}>Facts →</A></>}>
              <div className="grid gap-4 xl:grid-cols-3">
                <div>
                  <div className="label">Purpose</div><p className="text-sm">{v?.purpose ?? "—"}</p>
                  <div className="label mt-3">AgBOM</div>
                  {v?.agbom?.length ? <ul className="mt-1 space-y-0.5">{v.agbom.map((c: any, i: number) => <li key={i} className="mono text-[11px]">{typeof c === "string" ? c : `${c.type ?? c.kind ?? "component"}: ${c.name ?? JSON.stringify(c)}${c.version ? `@${c.version}` : ""}`}</li>)}</ul> : <p className="text-xs text-slate-500">Empty</p>}
                  <div className="label mt-3">Applicability</div>
                  {s.evaluations.slice(0, 5).map((e: any) => <div key={e.evaluation_id} className="flex items-center gap-2 text-xs"><A href={`/applicability/${e.evaluation_id}`}>{e.obligation}</A><Badge code={e.outcome} /></div>)}
                  {s.obligations.map((o: any) => <div key={o.lineage_id} className="mt-1 flex items-center gap-2 text-xs"><A href={`/obligations/${o.lineage_id}`}>{o.definition}</A><Badge code={o.lifecycle} /></div>)}
                </div>
                <div>
                  <div className="label">Authority grants</div>
                  <Table head={["Action", "Level", "Scope", "Expires", "Status", ""]} empty={!s.grants.length}>
                    {s.grants.map((g: any) => (
                      <tr key={g.grant_id}><td className="td mono text-[11px]">{g.action_class}</td><td className="td"><Badge code="ACTIVE" tone="info" label={g.authority_level} /></td>
                        <td className="td text-[11px]">{g.scope}<div className="text-slate-500">{g.reversibility} · {g.fail_mode}</div></td><td className="td text-[11px]">{fmtDate(g.expires_at)}</td>
                        <td className="td"><Badge code={new Date(g.expires_at) < new Date() && g.status === "ACTIVE" ? "EXPIRED" : g.status} /></td>
                        <td className="td">{g.status === "ACTIVE" && <RevokeBtn id={g.grant_id} onDone={reload} />}</td></tr>
                    ))}
                  </Table>
                </div>
                <PDP asset={s} onDone={reload} />
              </div>
              {s.receipts.length > 0 && (
                <details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">Decision receipts ({s.receipts.length})</summary>
                  <Table head={["Decided", "Action", "Scope", "Decision", "Rationale", "Policy"]}>
                    {s.receipts.map((r: any) => (
                      <tr key={r.receipt_id}><td className="td text-xs">{fmtDate(r.decided_at, true)}</td><td className="td mono text-[11px]">{r.action_class}</td><td className="td text-xs">{r.scope}</td>
                        <td className="td"><Badge code={r.decision} /></td><td className="td text-xs">{r.rationale}</td><td className="td mono text-[10px]">{r.policy_version}</td></tr>
                    ))}
                  </Table>
                </details>
              )}
            </Card>
          );
        })}
      </div>
      {reg && <Register onClose={() => setReg(false)} onDone={() => { setReg(false); reload(); }} />}
      {grant && <Grant asset={grant} onClose={() => setGrant(null)} onDone={() => { setGrant(null); reload(); }} />}
      {ver && <NewVersion asset={ver} onClose={() => setVer(null)} onDone={() => { setVer(null); reload(); }} />}
    </>
  );
}

function RevokeBtn({ id, onDone }: { id: string; onDone: () => void }) {
  const a = useAction();
  return <button className="btn-ghost text-xs" title={a.error ? String(a.error) : ""} disabled={a.busy} onClick={() => a.run(async () => { await api(`/ai/grants/${id}/revoke`, { method: "POST" }); onDone(); })}>Revoke</button>;
}

function PDP({ asset, onDone }: { asset: any; onDone: () => void }) {
  const [req, setReq] = useState({ action_class: asset.grants[0]?.action_class ?? "invoice.approve", scope: asset.grants[0]?.scope ?? "", requested_authority: "A2" });
  const a = useAction();
  const b = useAction();
  return (
    <div>
      <div className="label">Policy decision point (test)</div>
      <ErrorBox error={a.error || b.error} />
      <div className="grid gap-2">
        <input className="input py-1 text-xs" placeholder="action class" value={req.action_class} onChange={(e) => setReq({ ...req, action_class: e.target.value })} />
        <input className="input py-1 text-xs" placeholder="scope" value={req.scope} onChange={(e) => setReq({ ...req, scope: e.target.value })} />
        <select className="input py-1 text-xs" value={req.requested_authority} onChange={(e) => setReq({ ...req, requested_authority: e.target.value })}>{LEVELS.map(([l, n]) => <option key={l} value={l}>{l} · {n}</option>)}</select>
        <div className="flex gap-2">
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { const r = await api("/ai/decisions", { method: "POST", json: { agent_subject_id: asset.subject_id, ...req } }); onDone(); return r; })}>Decide</button>
          <button className="btn-ghost" disabled={b.busy} onClick={() => b.run(() => api(`/ai/policy-bundle/${asset.subject_id}`))}>Signed policy bundle</button>
        </div>
      </div>
      {a.result && <div className="mt-2 rounded border border-navy-100 p-2 text-xs"><Badge code={a.result.decision} /> <span className="ml-1">{a.result.rationale}</span>
        {a.result.obligation_refs?.length > 0 && <div className="mt-1 text-slate-500">Obligation context: {a.result.obligation_refs.length} adopted obligation(s)</div>}</div>}
      {b.result && <div className="mt-2"><p className="mb-1 text-[11px] text-slate-500">Valid for 15 minutes, so an Assure outage never becomes an uncontrolled fail-open.</p><Json value={b.result} max={200} /></div>}
    </div>
  );
}

function Register({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: org } = useApi<any>("/org/tenant");
  const [f, setF] = useState({ entity_id: "", display_name: "", asset_kind: "AGENT", version_label: "1.0.0", purpose: "", provider: "", model: "", environment: "PRODUCTION" });
  const [bom, setBom] = useState('[\n  {"type": "model", "name": "", "version": ""},\n  {"type": "tool", "name": ""}\n]');
  const a = useAction();
  return (
    <Modal open onClose={onClose} title="Register AI asset">
      <ErrorBox error={a.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div><label className="label">Legal entity</label><select className="input" value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}><option value="">Select…</option>
          {org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
        <div><label className="label">Kind</label><select className="input" value={f.asset_kind} onChange={(e) => setF({ ...f, asset_kind: e.target.value })}>{["AGENT", "MODEL", "AI_SYSTEM", "COPILOT"].map((x) => <option key={x}>{x}</option>)}</select></div>
        {(["display_name", "version_label", "provider", "model", "environment"] as const).map((k) => (
          <div key={k}><label className="label">{k.replace("_", " ")}</label><input className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
        ))}
        <div className="sm:col-span-2"><label className="label">Purpose</label><textarea className="input" rows={2} value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label">AgBOM (JSON)</label><textarea className="input mono h-32 text-[11px]" value={bom} onChange={(e) => setBom(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.entity_id || !f.display_name || a.busy} onClick={() => a.run(async () => {
        await api("/ai/assets", { method: "POST", json: { ...f, agbom: JSON.parse(bom) } }); onDone();
      })}>Register</button></div>
    </Modal>
  );
}

function NewVersion({ asset, onClose, onDone }: { asset: any; onClose: () => void; onDone: () => void }) {
  const v = asset.versions[0] || {};
  const [f, setF] = useState({ version_label: "", purpose: v.purpose ?? "", provider: v.provider ?? "", model: v.model ?? "", environment: v.environment ?? "PRODUCTION" });
  const [bom, setBom] = useState(JSON.stringify(v.agbom ?? [], null, 2));
  const a = useAction();
  return (
    <Modal open onClose={onClose} title={`New version · ${asset.display_name}`}>
      <Notice>A change to the AgBOM or model triggers a reclassification signal, which re-evaluates the asset's applicability.</Notice>
      <ErrorBox error={a.error} />
      {a.result && <div className="mt-2"><Notice tone={a.result.reclassification_required ? "warn" : "good"}>{a.result.reclassification_required ? "Reclassification required. Event emitted." : "No reclassification needed."}</Notice></div>}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {(["version_label", "provider", "model", "environment"] as const).map((k) => (
          <div key={k}><label className="label">{k.replace("_", " ")}</label><input className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
        ))}
        <div className="sm:col-span-2"><label className="label">Purpose</label><input className="input" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label">AgBOM</label><textarea className="input mono h-32 text-[11px]" value={bom} onChange={(e) => setBom(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2">{a.result && <button className="btn-ghost" onClick={onDone}>Done</button>}
        <button className="btn-primary" disabled={!f.version_label || a.busy} onClick={() => a.run(() => api(`/ai/assets/${asset.subject_id}/versions`, { method: "POST", json: { ...f, agbom: JSON.parse(bom) } }))}>Create version</button></div>
    </Modal>
  );
}

function Grant({ asset, onClose, onDone }: { asset: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ action_class: "", authority_level: "A1", scope: "", reversibility: "REVERSIBLE", expires_in_days: 90, fail_mode: "FAIL_CLOSED" });
  const a = useAction();
  return (
    <Modal open onClose={onClose} title={`Grant authority · ${asset.display_name}`}>
      <Notice>Each grant is a tuple: agent × action class × scope × reversibility × expiry × grantor. Only a human can issue one.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><label className="label">Action class</label><input className="input" placeholder="e.g. invoice.approve" value={f.action_class} onChange={(e) => setF({ ...f, action_class: e.target.value })} /></div>
        <div><label className="label">Authority level</label><select className="input" value={f.authority_level} onChange={(e) => setF({ ...f, authority_level: e.target.value })}>
          {LEVELS.map(([l, n]) => <option key={l} value={l}>{l} · {n}</option>)}<option value="A4">A4 · Material (excluded)</option></select></div>
        <div><label className="label">Scope</label><input className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} /></div>
        <div><label className="label">Reversibility</label><select className="input" value={f.reversibility} onChange={(e) => setF({ ...f, reversibility: e.target.value })}>{["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label">Expires in (days)</label><input type="number" className="input" value={f.expires_in_days} onChange={(e) => setF({ ...f, expires_in_days: Number(e.target.value) })} /></div>
        <div><label className="label">Fail mode</label><select className="input" value={f.fail_mode} onChange={(e) => setF({ ...f, fail_mode: e.target.value })}>{["FAIL_CLOSED", "FAIL_SAFE_DEGRADE"].map((x) => <option key={x}>{x}</option>)}</select></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.action_class || !f.scope || a.busy} onClick={() => a.run(async () => {
        await api("/ai/grants", { method: "POST", json: { agent_subject_id: asset.subject_id, ...f } }); onDone();
      })}>Issue grant</button></div>
    </Modal>
  );
}
