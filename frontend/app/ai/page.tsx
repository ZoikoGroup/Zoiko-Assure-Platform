"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { AI_ASSET_KINDS } from "@/lib/codes";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ConfirmDialog, ErrorBox, FieldError, Json, LiveRegion, LoadingSkeleton, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

const LEVELS = [["A0", "Observe"], ["A1", "Recommend"], ["A2", "Prepare"], ["A3", "Execute (reversible)"]];
/* ZA-ARCH-001 §12.1 asset registry kinds (lib/codes). Stored kinds outside this list are shown as received and marked, never rewritten. */
const kindLabel = (k: string) => (AI_ASSET_KINDS.includes(k) ? k : `${k} (non-canonical kind)`);
/* Column sizes behind POST /ai/assets; longer values fail when stored. */
const MAX_LEN = { display_name: 200, version_label: 48, provider: 120, model: 120, environment: 64 };

export default function AI() {
  const { data, error, reload } = useApi<any[]>("/ai/assets");
  const [reg, setReg] = useState(false);
  const [grant, setGrant] = useState<any>(null);
  const [ver, setVer] = useState<any>(null);
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="WF-DOM-07 · ZA-ARCH-001 §12 · FR-AI-001..006" title="Agent Governance"
        subtitle="Your AI assets, the obligations that apply to them, and the authority granted to agents."
        help="Each asset carries an AgBOM (AI bill of materials). Agent actions are decided by a deterministic policy decision point using scoped authority grants; no model call sits on the decision path."
        actions={<button className="btn-primary" disabled={!can("manage_ai")} title={why("manage_ai")} onClick={() => setReg(true)}>Register asset</button>} />
      <PermissionNote perms={["manage_ai", "grant_authority"]} />
      <ErrorBox error={error} />
      <Notice>Authority levels run A0 Observe → A1 Recommend → A2 Prepare → A3 Execute (reversible). A4 material autonomy is excluded from the initial production scope. Enforcement stays in the customer runtime; Assure returns a decision, its obligation context and a receipt.</Notice>
      <div className="mt-4 space-y-4">
        {!data && !error && <Card><LoadingSkeleton rows={4} /></Card>}
        {data?.map((s) => {
          const v = s.versions[0];
          return (
            <Card key={s.subject_id} title={s.display_name} subtitle={v ? `${kindLabel(v.asset_kind)} · ${v.version_label} · ${v.provider} / ${v.model} · ${v.environment}` : "No version"}
              actions={<><button className="btn-ghost" disabled={!can("manage_ai")} title={why("manage_ai")} onClick={() => setVer(s)}>New version</button><button className="btn-ghost" disabled={!can("grant_authority")} title={why("grant_authority")} onClick={() => setGrant(s)}>Grant authority</button>
                <A href={`/entities/${s.subject_id}`}>Facts →</A></>}>
              {/* Tracks may shrink below their content (minmax(0, 1fr), min-w-0): long identifiers wrap and tables scroll
                  inside their own box, so a phone never scrolls the page sideways. From xl the details and the grants
                  share the width and the policy bundle takes the row below, so both tables have room for their columns. */}
              <div className="grid grid-cols-1 gap-x-6 gap-y-5 xl:grid-cols-2 [&>*]:min-w-0">
                <div>
                  <div className="label">Purpose</div><p className="break-words text-sm">{v?.purpose ?? "—"}</p>
                  <div className="label mt-3">AgBOM</div>
                  {v?.agbom?.length ? <ul className="mt-1 space-y-0.5">{v.agbom.map((c: any, i: number) => <li key={i} className="mono break-all text-[11px]">{typeof c === "string" ? c : `${c.type ?? c.kind ?? "component"}: ${c.name ?? JSON.stringify(c)}${c.version ? `@${c.version}` : ""}`}</li>)}</ul> : <p className="text-xs text-slate-500">Empty</p>}
                  <div className="label mt-3">Applicability</div>
                  {s.evaluations.length > 0 && <AssetApplicability asset={s} />}
                  {s.obligations.length > 0 && <div className="label mt-3">Obligations</div>}
                  {s.obligations.map((o: any) => <div key={o.lineage_id} className="mt-1 flex items-center gap-2 text-xs"><A href={`/obligations/${o.lineage_id}`}>{o.definition}</A><Badge code={o.lifecycle} /></div>)}
                </div>
                <div>
                  <div className="label">Authority grants</div>
                  <Table head={["Action", "Level", "Scope", "Expires", "Status", ""]} empty={!s.grants.length}>
                    {s.grants.map((g: any) => (
                      <tr key={g.grant_id}><td className="td mono text-[11px]">{g.action_class}</td><td className="td"><Badge code="ACTIVE" tone="info" label={g.authority_level} /></td>
                        <td className="td text-[11px]">{g.scope}<div className="text-slate-500">{g.reversibility} · {g.fail_mode}</div></td><td className="td text-[11px]">{fmtDate(g.expires_at)}</td>
                        <td className="td"><Badge code={new Date(g.expires_at) < new Date() && g.status === "ACTIVE" ? "EXPIRED" : g.status} /></td>
                        <td className="td">{g.status === "ACTIVE" && <RevokeBtn grant={g} asset={s.display_name} onDone={reload} />}</td></tr>
                    ))}
                  </Table>
                </div>
                <div className="xl:col-span-2"><PolicyBundle asset={s} /></div>
              </div>
              {s.receipts.length > 0 && (
                <details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">Decision receipts ({s.receipts.length})</summary>
                  <Table head={["Decided", "Action", "Scope", "Decision", "Rationale", "Policy"]}>
                    {s.receipts.map((r: any) => (
                      <tr key={r.receipt_id}><td className="td text-xs">{fmtDate(r.decided_at, true)}</td><td className="td mono text-[11px]">{r.action_class}</td><td className="td text-xs">{r.scope}</td>
                        <td className="td"><Badge code={r.decision} /></td><td className="td text-xs">{r.rationale}</td><td className="td mono text-[11px]">{r.policy_version}</td></tr>
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

/* Applicability outcome and determination are separate (ZA-GLS-001 §11: never show Applies without determination status).
   /ai/assets returns outcomes only, so the determination comes from the subject's evaluations
   (GET /applicability/evaluations?subject_id=…, as on the subject page), matched by evaluation ID and never inferred. */
function AssetApplicability({ asset }: { asset: any }) {
  const evals = useApi<any[]>(`/applicability/evaluations?subject_id=${asset.subject_id}`);
  // undefined: not in the response (still loading, failed, or not returned); null: no determination recorded.
  const determinationOf = new Map<string, any>((evals.data ?? []).map((e) => [e.evaluation_id, e.current_determination]));
  const missing = evals.error ? "Unavailable" : evals.data ? "Not returned" : "Loading…";
  return (
    <>
      <ErrorBox error={evals.error} />
      <Table head={["Obligation", "Outcome", "Determination"]}>
        {asset.evaluations.slice(0, 5).map((e: any) => {
          const det = determinationOf.get(e.evaluation_id);
          return (
            <tr key={e.evaluation_id}>
              <td className="td text-xs"><A href={`/applicability/${e.evaluation_id}`}>{e.obligation}</A></td>
              <td className="td"><Badge code={e.outcome} /></td>
              <td className="td">{det ? <><Badge code={det.state} />{det.evaluation_id !== e.evaluation_id && <div className="text-[11px] text-slate-500">based on another evaluation</div>}</>
                : <span className="text-xs text-slate-500">{det === null ? "None recorded" : missing}</span>}</td>
            </tr>
          );
        })}
      </Table>
    </>
  );
}

function RevokeBtn({ grant, asset, onDone }: { grant: any; asset: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const { can, why } = useCan();
  return (
    <>
      <button className="btn-ghost text-xs" title={why("grant_authority")} disabled={!can("grant_authority")} onClick={() => setOpen(true)}>Revoke</button>
      {open && (
        <ConfirmDialog danger title="Revoke authority grant" confirm="Revoke grant" busyLabel="Revoking…"
          onConfirm={async () => { await api(`/ai/grants/${grant.grant_id}/revoke`, { method: "POST" }); onDone(); }} onClose={() => setOpen(false)}>
          <p>{asset} · <span className="mono text-xs">{grant.action_class}</span> at {grant.authority_level}, scope {grant.scope}, expires {fmtDate(grant.expires_at)}.</p>
          <p>The grant moves to REVOKED. Assure&apos;s policy decisions and newly signed policy bundles use only ACTIVE grants, so they stop relying on it. A policy bundle signed before the revocation stays valid until it expires (up to 15 minutes). A revoked grant cannot be reinstated; restoring this authority needs a new grant.</p>
        </ConfirmDialog>
      )}
    </>
  );
}

function PolicyBundle({ asset }: { asset: any }) {
  const b = useAction();
  return (
    <div>
      <div className="label">Signed policy bundle</div>
      <ErrorBox error={b.error} />
      <button className="btn-ghost" disabled={b.busy} onClick={() => b.run(() => api(`/ai/policy-bundle/${asset.subject_id}`))}>Signed policy bundle</button>
      {b.result && <div className="mt-2"><p className="mb-1 text-[11px] text-slate-500">Valid for 15 minutes, so an Assure outage never becomes an uncontrolled fail-open.</p><Json value={b.result} max={200} /></div>}
    </div>
  );
}

function Register({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: org } = useApi<any>("/org/tenant");
  const [f, setF] = useState({ entity_id: "", display_name: "", asset_kind: "AGENT", version_label: "1.0.0", purpose: "", provider: "", model: "", environment: "PRODUCTION" });
  const [bom, setBom] = useState('[\n  {"type": "model", "name": "", "version": ""},\n  {"type": "tool", "name": ""}\n]');
  const [tried, setTried] = useState(false);
  const a = useAction();
  const fid = useId();
  // The request body types agbom as a list; the kind always comes from the canonical list above.
  const bomIsList = (() => { try { return Array.isArray(JSON.parse(bom)); } catch { return false; } })();
  const errs = {
    display_name: !f.display_name.trim() ? "Enter a display name. Blank or whitespace-only text is not accepted." : "",
    bom: bomIsList ? "" : "The AgBOM must be a JSON array, for example [].",
  };
  const show = (k: keyof typeof errs) => (tried ? errs[k] : "");
  const register = () => {
    const first = (Object.keys(errs) as (keyof typeof errs)[]).find((k) => errs[k]);
    if (first) { setTried(true); document.getElementById(`${fid}-${first}`)?.focus(); return; }
    a.run(async () => {
      await api("/ai/assets", { method: "POST", json: { ...f, display_name: f.display_name.trim(), agbom: JSON.parse(bom) } }); onDone();
    });
  };
  return (
    <Modal open onClose={onClose} title="Register AI asset">
      <ErrorBox error={a.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div><label className="label" htmlFor={`${fid}-entity`}>Legal entity (required)</label><select id={`${fid}-entity`} required className="input" value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}><option value="">Select…</option>
          {org?.entities.map((e: any) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-kind`}>Kind</label><select id={`${fid}-kind`} className="input" value={f.asset_kind} onChange={(e) => setF({ ...f, asset_kind: e.target.value })}>{AI_ASSET_KINDS.map((x) => <option key={x}>{x}</option>)}</select></div>
        {(["display_name", "version_label", "provider", "model", "environment"] as const).map((k) => {
          const msg = k === "display_name" ? show(k) : "";
          return (
            <div key={k}><label className="label" htmlFor={`${fid}-${k}`}>{k.replace("_", " ")}{k === "display_name" && " (required)"}</label>
              <input id={`${fid}-${k}`} required={k === "display_name"} maxLength={MAX_LEN[k]} className="input" aria-invalid={!!msg || undefined} aria-describedby={msg ? `${fid}-${k}-error` : undefined}
                value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
              <FieldError id={`${fid}-${k}`} message={msg} /></div>
          );
        })}
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-purpose`}>Purpose</label><textarea id={`${fid}-purpose`} className="input" rows={2} value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-bom`}>AgBOM (JSON array)</label><textarea id={`${fid}-bom`} className="input mono h-32 text-[11px]" aria-invalid={!!show("bom") || undefined} aria-describedby={show("bom") ? `${fid}-bom-error` : undefined} value={bom} onChange={(e) => setBom(e.target.value)} />
          <FieldError id={`${fid}-bom`} message={show("bom")} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.entity_id || !f.display_name || a.busy} onClick={register}>Register</button></div>
    </Modal>
  );
}

function NewVersion({ asset, onClose, onDone }: { asset: any; onClose: () => void; onDone: () => void }) {
  const v = asset.versions[0] || {};
  const [f, setF] = useState({ version_label: "", purpose: v.purpose ?? "", provider: v.provider ?? "", model: v.model ?? "", environment: v.environment ?? "PRODUCTION" });
  const [bom, setBom] = useState(JSON.stringify(v.agbom ?? [], null, 2));
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title={`New version · ${asset.display_name}`}>
      <Notice>A change to the AgBOM or model emits a reclassification signal. It does not re-run applicability by itself; evaluate the asset again from its Facts page.</Notice>
      <ErrorBox error={a.error} />
      <LiveRegion>{a.result && <div className="mt-2"><Notice tone={a.result.reclassification_required ? "warn" : "good"}>{a.result.reclassification_required ? "Reclassification required. Event emitted." : "No reclassification needed."}</Notice></div>}</LiveRegion>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {(["version_label", "provider", "model", "environment"] as const).map((k) => (
          <div key={k}><label className="label" htmlFor={`${fid}-${k}`}>{k.replace("_", " ")}{k === "version_label" && " (required)"}</label><input id={`${fid}-${k}`} required={k === "version_label"} className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
        ))}
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-purpose`}>Purpose</label><input id={`${fid}-purpose`} className="input" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-bom`}>AgBOM</label><textarea id={`${fid}-bom`} className="input mono h-32 text-[11px]" value={bom} onChange={(e) => setBom(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2">{a.result && <button className="btn-ghost" onClick={onDone}>Done</button>}
        <button className="btn-primary" disabled={!f.version_label || a.busy} onClick={() => a.run(() => api(`/ai/assets/${asset.subject_id}/versions`, { method: "POST", json: { ...f, agbom: JSON.parse(bom) } }))}>Create version</button></div>
    </Modal>
  );
}

function Grant({ asset, onClose, onDone }: { asset: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ action_class: "", authority_level: "A1", scope: "", reversibility: "REVERSIBLE", expires_in_days: 90, fail_mode: "FAIL_CLOSED" });
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title={`Grant authority · ${asset.display_name}`}>
      <Notice>Each grant is a tuple: agent × action class × scope × reversibility × expiry × grantor. Only a human can issue one.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><label className="label" htmlFor={`${fid}-action`}>Action class (required)</label><input id={`${fid}-action`} required className="input" placeholder="e.g. invoice.approve" value={f.action_class} onChange={(e) => setF({ ...f, action_class: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-level`}>Authority level</label><select id={`${fid}-level`} className="input" value={f.authority_level} onChange={(e) => setF({ ...f, authority_level: e.target.value })}>
          {LEVELS.map(([l, n]) => <option key={l} value={l}>{l} · {n}</option>)}<option value="A4">A4 · Material (excluded)</option></select></div>
        <div><label className="label" htmlFor={`${fid}-scope`}>Scope (required)</label><input id={`${fid}-scope`} required className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-rev`}>Reversibility</label><select id={`${fid}-rev`} className="input" value={f.reversibility} onChange={(e) => setF({ ...f, reversibility: e.target.value })}>{["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-exp`}>Expires in (days)</label><input id={`${fid}-exp`} type="number" className="input" value={f.expires_in_days} onChange={(e) => setF({ ...f, expires_in_days: Number(e.target.value) })} /></div>
        <div><label className="label" htmlFor={`${fid}-fail`}>Fail mode</label><select id={`${fid}-fail`} className="input" value={f.fail_mode} onChange={(e) => setF({ ...f, fail_mode: e.target.value })}>{["FAIL_CLOSED", "FAIL_SAFE_DEGRADE"].map((x) => <option key={x}>{x}</option>)}</select></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.action_class || !f.scope || a.busy} onClick={() => a.run(async () => {
        await api("/ai/grants", { method: "POST", json: { agent_subject_id: asset.subject_id, ...f } }); onDone();
      })}>Issue grant</button></div>
    </Modal>
  );
}
