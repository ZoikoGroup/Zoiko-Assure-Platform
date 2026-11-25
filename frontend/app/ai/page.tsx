"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { AI_ASSET_KINDS } from "@/lib/codes";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ConfirmDialog, EmptyState, ErrorBox, FieldError, Hash, Json, KV, LiveRegion, LoadingSkeleton, Modal, Notice, PageHeader, PermissionNote, useAction } from "@/components/ui";

const LEVELS = [["A0", "Observe"], ["A1", "Recommend"], ["A2", "Prepare"], ["A3", "Execute (reversible)"]];
const LEVEL_NAME: Record<string, string> = { ...Object.fromEntries(LEVELS), A4: "Material (excluded)" };
/* ZA-ARCH-001 §12.1 asset registry kinds (lib/codes). Stored kinds outside this list are shown as received and marked, never rewritten. */
const kindLabel = (k: string) => (AI_ASSET_KINDS.includes(k) ? k : `${k} (non-canonical kind)`);
/* Column sizes behind POST /ai/assets; longer values fail when stored. */
const MAX_LEN = { display_name: 200, version_label: 48, provider: 120, model: 120, environment: 64 };
const expired = (g: any) => new Date(g.expires_at) < new Date() && g.status === "ACTIVE";
/* Compact tables. Below md the header hides and each row's cells flow and wrap as a stacked item (with M labels where a
   value needs naming), so no column hides behind a sideways scroll on a phone. */
const T = {
  table: "w-full text-[13px] max-md:block",
  thead: "text-left text-[11px] uppercase tracking-wide text-slate-500 max-md:hidden",
  th: "border-b border-slate-200 pb-1.5 pr-3 font-semibold",
  tbody: "divide-y divide-slate-100 align-top max-md:block",
  tr: "max-md:flex max-md:flex-wrap max-md:items-center max-md:gap-x-2.5 max-md:gap-y-1.5 max-md:py-2.5",
  td: "py-2 pr-3 max-md:p-0",
};
const M = ({ children }: { children: string }) => <span className="mr-1 text-[11px] text-slate-500 md:hidden">{children}</span>;

/* WF-DOM-07 AI Asset Dossier. One card per asset: its current version, AgBOM, applicability with any resulting obligation,
   authority grants and decision receipts. Every value comes from GET /ai/assets (and the subject's evaluations for the
   determination state); nothing is summarised away: earlier evaluations, raw AgBOM fields and the raw signed policy
   bundle stay one click away. */
export default function AI() {
  const { data, error, reload } = useApi<any[]>("/ai/assets");
  const [reg, setReg] = useState(false);
  const [grant, setGrant] = useState<any>(null);
  const [ver, setVer] = useState<any>(null);
  const [bundle, setBundle] = useState<any>(null);
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="WF-DOM-07 · ZA-ARCH-001 §12 · FR-AI-001..006" title="Agent Governance"
        subtitle="Your AI assets, the obligations that apply to them, and the authority granted to agents."
        help={<>
          <p>Each asset carries an AgBOM (AI bill of materials). Agent actions are decided by a deterministic policy decision point using scoped authority grants; no model call sits on the decision path.</p>
          <p className="mt-2">Authority levels run A0 Observe → A1 Recommend → A2 Prepare → A3 Execute (reversible). A4 material autonomy is excluded from the initial production scope. Enforcement stays in the customer runtime; Assure returns a decision, its obligation context and a receipt.</p>
        </>}
        actions={<button className="btn-primary" disabled={!can("manage_ai")} title={why("manage_ai")} onClick={() => setReg(true)}>Register asset</button>} />
      <PermissionNote perms={["manage_ai", "grant_authority"]} />
      <ErrorBox error={error} />
      <div className="space-y-4">
        {!data && !error && <Card><LoadingSkeleton rows={4} /></Card>}
        {data?.length === 0 && <Card><EmptyState title="No AI assets registered" description="An AI Governance Lead registers models, copilots and agents here, with their AgBOM." /></Card>}
        {data?.map((s) => <AssetCard key={s.subject_id} asset={s} reload={reload} onVersion={() => setVer(s)} onGrant={() => setGrant(s)} onBundle={() => setBundle(s)} />)}
      </div>
      {reg && <Register onClose={() => setReg(false)} onDone={() => { setReg(false); reload(); }} />}
      {grant && <Grant asset={grant} onClose={() => setGrant(null)} onDone={() => { setGrant(null); reload(); }} />}
      {ver && <NewVersion asset={ver} onClose={() => setVer(null)} onDone={() => { setVer(null); reload(); }} />}
      {bundle && <PolicyBundle asset={bundle} onClose={() => setBundle(null)} />}
    </>
  );
}

function AssetCard({ asset: s, reload, onVersion, onGrant, onBundle }: {
  asset: any; reload: () => void; onVersion: () => void; onGrant: () => void; onBundle: () => void;
}) {
  const { can, why } = useCan();
  const v = s.versions[0];
  const earlier = s.versions.length - 1;
  return (
    <Card title={s.display_name}
      subtitle={v ? <>{kindLabel(v.asset_kind)} · {v.version_label} · {v.provider} / {v.model} · {v.environment}
        {earlier > 0 && <span className="text-slate-400"> · {earlier} earlier version{earlier > 1 ? "s" : ""}</span>}</> : "No version"}
      actions={<>
        <button className="btn-ghost" onClick={onBundle}>Policy bundle</button>
        <button className="btn-ghost" disabled={!can("manage_ai")} title={why("manage_ai")} onClick={onVersion}>New version</button>
        <button className="btn-ghost" disabled={!can("grant_authority")} title={why("grant_authority")} onClick={onGrant}>Grant authority</button>
        <A href={`/entities/${s.subject_id}`}>Facts →</A>
      </>}>
      <div className="space-y-5">
        {/* One row on wide screens (Purpose | AgBOM), stacked below; minmax(0, …) lets long references wrap instead of widening the page. */}
        <dl className="grid gap-x-6 gap-y-2.5 text-[13px] sm:grid-cols-[88px_minmax(0,1fr)] xl:grid-cols-[88px_minmax(0,1fr)_56px_minmax(0,1.3fr)]">
          <dt className="text-[12px] font-medium text-slate-500">Purpose</dt>
          <dd className="break-words text-slate-800">{v?.purpose || "—"}</dd>
          <dt className="text-[12px] font-medium text-slate-500"><abbr title="AI bill of materials" className="no-underline">AgBOM</abbr></dt>
          <dd><AgBom components={v?.agbom ?? []} /></dd>
        </dl>
        <Applicability asset={s} />
        <Grants asset={s} reload={reload} />
        {s.receipts.length > 0 && (
          <details>
            <summary className="cursor-pointer text-[13px] font-medium text-navy-700">Decision receipts ({s.receipts.length})</summary>
            <table className={`${T.table} mt-2`}>
              <thead className={T.thead}><tr>{["Decided", "Action", "Scope", "Decision", "Rationale", "Policy"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
              <tbody className={T.tbody}>
                {s.receipts.map((r: any) => (
                  <tr key={r.receipt_id} className={T.tr}>
                    <td className={`${T.td} whitespace-nowrap max-md:w-full max-md:text-[12px] max-md:text-slate-500`}>{fmtDate(r.decided_at, true)}</td>
                    <td className={T.td}>{r.action_class}</td><td className={T.td}>{r.scope}</td>
                    <td className={T.td}><Badge code={r.decision} /></td>
                    <td className={`${T.td} max-md:w-full`}>{r.rationale}</td>
                    <td className={`${T.td} mono text-[11px]`}><M>Policy</M>{r.policy_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>
    </Card>
  );
}

/* The AgBOM as labelled components instead of JSON. A component is {type|kind, ref|name|id, version?, …}; fields beyond
   those are shown as key=value beside it, and a component with no recognisable field falls back to its JSON, so nothing
   stored is hidden. */
const BOM_KNOWN = ["type", "kind", "ref", "name", "id", "version"];
function AgBom({ components }: { components: any[] }) {
  if (!components.length) return <span className="text-slate-500">Empty</span>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {components.map((c, i) => {
        if (typeof c !== "object" || c === null) {
          return <li key={i} className="mono rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[12px] text-slate-800">{String(c)}</li>;
        }
        const kind = c.type ?? c.kind;
        const ref = c.ref ?? c.name ?? c.id;
        const extra = Object.entries(c).filter(([k]) => !BOM_KNOWN.includes(k));
        return (
          <li key={i} className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[12px]">
            {kind && <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-slate-500">{String(kind).replace(/_/g, " ")}</span>}
            <span className="mono min-w-0 break-all text-slate-800">
              {ref != null ? `${ref}${c.version ? `@${c.version}` : ""}` : extra.length ? "" : JSON.stringify(c)}
              {extra.length > 0 && <span className="text-slate-500">{ref != null ? " " : ""}{extra.map(([k, x]) => `${k}=${typeof x === "string" ? x : JSON.stringify(x)}`).join(" ")}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/* Applicability outcome and determination are separate (ZA-GLS-001 §11: never show Applies without determination status).
   /ai/assets returns outcomes only, so the determination comes from the subject's evaluations
   (GET /applicability/evaluations?subject_id=…, as on the subject page), matched by evaluation ID and never inferred.
   /ai/assets lists every evaluation, newest first: each requirement shows its latest one, with the obligation it led to;
   earlier evaluations stay in the same table behind a toggle. */
function Applicability({ asset }: { asset: any }) {
  const evals = useApi<any[]>(`/applicability/evaluations?subject_id=${asset.subject_id}`);
  const [showOlder, setShowOlder] = useState(false);
  // undefined: not in the response (still loading, failed, or not returned); null: no determination recorded.
  const determinationOf = new Map<string, any>((evals.data ?? []).map((e) => [e.evaluation_id, e.current_determination]));
  const missing = evals.error ? "Unavailable" : evals.data ? "Not returned" : "Loading…";
  const latest: any[] = [];
  const older: any[] = [];
  const seen = new Set<string>();
  for (const e of asset.evaluations) {
    (seen.has(e.obligation) ? older : latest).push(e);
    seen.add(e.obligation);
  }
  const obligationsOf = (key: string) => asset.obligations.filter((o: any) => o.definition === key);
  const unmatched = asset.obligations.filter((o: any) => !seen.has(o.definition));
  const row = (e: any, older = false) => {
    const det = determinationOf.get(e.evaluation_id);
    return (
      <tr key={e.evaluation_id} className={`${T.tr}${older ? " text-slate-500" : ""}`}>
        <td className={`${T.td} max-md:w-full`}><A href={`/applicability/${e.evaluation_id}`}>{e.obligation}</A>
          {older && <span className="ml-1.5 text-[11px] text-slate-500">earlier</span>}</td>
        <td className={T.td}><Badge code={e.outcome} /></td>
        <td className={T.td}><M>Determination</M>{det ? <><Badge code={det.state} />{det.evaluation_id !== e.evaluation_id && <div className="text-[11px] text-slate-500 max-md:ml-1.5 max-md:inline">based on another evaluation</div>}</>
          : <span className="text-[12px] text-slate-500">{det === null ? "None recorded" : missing}</span>}</td>
        <td className={T.td}>{older ? null : <ObligationLinks items={obligationsOf(e.obligation)} />}</td>
      </tr>
    );
  };
  return (
    <section aria-label="Applicability">
      <h3 className="mb-1.5 text-[12px] font-medium text-slate-500">Applicability</h3>
      <ErrorBox error={evals.error} />
      {!latest.length && !unmatched.length ? <p className="text-[13px] text-slate-500">No applicability evaluation recorded.</p> : (
        <table className={T.table}>
          <thead className={T.thead}><tr>{["Requirement", "Outcome", "Determination", "Obligation"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
          <tbody className={T.tbody}>
            {latest.map((e) => row(e))}
            {unmatched.map((o: any) => (
              <tr key={o.lineage_id} className={T.tr}>
                <td className={`${T.td} max-md:w-full`}>{o.definition}</td>
                <td className={`${T.td} text-[12px] text-slate-500`} colSpan={2}>No evaluation listed</td>
                <td className={T.td}><ObligationLinks items={[o]} /></td>
              </tr>
            ))}
            {showOlder && older.map((e) => row(e, true))}
          </tbody>
        </table>
      )}
      {older.length > 0 && (
        <button type="button" className="mt-1.5 text-[12px] font-medium text-navy-700 hover:underline" aria-expanded={showOlder} onClick={() => setShowOlder(!showOlder)}>
          {showOlder ? "Hide" : "Show"} {older.length} earlier evaluation{older.length > 1 ? "s" : ""}
        </button>
      )}
    </section>
  );
}

function ObligationLinks({ items }: { items: any[] }) {
  if (!items.length) return <span className="text-[12px] text-slate-400 max-md:hidden">—</span>;
  return <div className="flex flex-col gap-1">{items.map((o) => (
    <span key={o.lineage_id} className="inline-flex items-center gap-1.5"><M>Obligation</M><Badge code={o.lifecycle} /><A href={`/obligations/${o.lineage_id}`}>Open</A></span>
  ))}</div>;
}

function Grants({ asset: s, reload }: { asset: any; reload: () => void }) {
  return (
    <section aria-label="Authority grants">
      <h3 className="mb-1.5 text-[12px] font-medium text-slate-500">Authority grants</h3>
      {!s.grants.length ? <p className="text-[13px] text-slate-500">No authority granted to this asset.</p> : (
        <table className={T.table}>
          <thead className={T.thead}><tr>{["Action", "Level", "Scope", "Expires", "Status", ""].map((h, i) => <th key={i} className={T.th}>{h}</th>)}</tr></thead>
          <tbody className={T.tbody}>
            {s.grants.map((g: any) => (
              <tr key={g.grant_id} className={T.tr}>
                <td className={`${T.td} text-slate-800 max-md:w-full max-md:font-medium`}>{g.action_class}</td>
                <td className={`${T.td} whitespace-nowrap`}><Badge code="ACTIVE" tone="info" label={g.authority_level} title={LEVEL_NAME[g.authority_level]} />
                  {LEVEL_NAME[g.authority_level] && <span className="ml-1.5 text-[12px] text-slate-500">{LEVEL_NAME[g.authority_level]}</span>}</td>
                <td className={`${T.td} max-md:w-full`}>{g.scope}<div className="text-[11px] text-slate-500">{g.reversibility} · {g.fail_mode}</div></td>
                <td className={`${T.td} whitespace-nowrap`}><M>Expires</M>{fmtDate(g.expires_at)}</td>
                <td className={T.td}><Badge code={expired(g) ? "EXPIRED" : g.status} /></td>
                <td className={`${T.td} text-right max-md:ml-auto`}>{g.status === "ACTIVE" && <RevokeBtn grant={g} asset={s.display_name} onDone={reload} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
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

/* GET /ai/policy-bundle/{asset}: a freshly signed, short-validity bundle of the asset's ACTIVE grants, shown as its parts;
   the signed JSON itself stays available below. Each opening signs a new bundle, as the former button did. */
function PolicyBundle({ asset, onClose }: { asset: any; onClose: () => void }) {
  const b = useAction();
  const sign = () => b.run(() => api(`/ai/policy-bundle/${asset.subject_id}`));
  // Sign once per opening; the ref survives React Strict Mode's development re-run of effects.
  const signed = useRef(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (!signed.current) { signed.current = true; sign(); } }, []);
  const bundle = b.result?.bundle;
  return (
    <Modal open onClose={onClose} title={`Signed policy bundle · ${asset.display_name}`}>
      <ErrorBox error={b.error} />
      {!b.result && !b.error && <LoadingSkeleton rows={3} />}
      {bundle && (
        <div className="space-y-4">
          <p className="text-[12px] text-slate-600">Valid for 15 minutes, so an Assure outage never becomes an uncontrolled fail-open.</p>
          <KV items={[["Issued", fmtDate(bundle.issued_at, true)], ["Valid until", fmtDate(bundle.valid_until, true)],
            ["Policy version", <span key="p" className="mono text-xs">{bundle.policy_version}</span>],
            ["Signer key", <span key="k" className="mono text-xs">{b.result.signer_key_id}</span>],
            ["Signature", <Hash key="s" value={b.result.signature} n={16} />], ["Grants included", String(bundle.grants?.length ?? 0)]]} />
          {bundle.grants?.length > 0 && (
            <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 text-[13px]">
              {bundle.grants.map((g: any) => (
                <li key={g.grant_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <span className="min-w-0">{g.action_class}<span className="text-slate-500"> · {g.scope}</span></span>
                  <span className="flex items-center gap-2 text-[12px] text-slate-600"><Badge code="ACTIVE" tone="info" label={g.authority_level} />{g.fail_mode} · expires {fmtDate(g.expires_at)}</span>
                </li>
              ))}
            </ul>
          )}
          <details><summary className="cursor-pointer text-[12px] text-navy-700">Signed bundle (JSON)</summary><div className="mt-2"><Json value={b.result} max={260} /></div></details>
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" disabled={b.busy} onClick={sign}>{b.busy ? "Signing…" : "Sign again"}</button>
        <button className="btn-primary" onClick={onClose}>Done</button>
      </div>
    </Modal>
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
