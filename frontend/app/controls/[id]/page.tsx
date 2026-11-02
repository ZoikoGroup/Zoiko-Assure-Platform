"use client";

import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, Json, KV, LiveRegion, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";
import { ManualProvenance } from "@/components/EvidenceProvenance";

export default function ControlPage() {
  const { id } = useParams<{ id: string }>();
  const [days, setDays] = useState(30);
  const { data: d, error, reload } = useApi<any>(`/controls/${id}?days=${days}`);
  const [run, setRun] = useState<any>(null);
  const a = useAction();
  const name = useActorNames();
  const { can, why } = useCan();
  const ce = d?.evaluation_preview;
  return (
    <>
      <PageHeader kicker="Control implementation" spec="WF-STA-02 Control Evaluation" title={d?.definition.title ?? "Control"}
        subtitle={d ? `${d.definition.key} · scope ${d.subject.display_name}. Its tests and their evidence decide whether it operated in a window.` : undefined}
        actions={<>
          <select aria-label="Evaluation window" className="input py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 180].map((x) => <option key={x} value={x}>last {x} days</option>)}</select>
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => {
            const now = new Date(); const r = await api(`/controls/${id}/evaluate`, { method: "POST", json: { window_from: new Date(now.getTime() - days * 864e5).toISOString(), window_to: now.toISOString() } });
            reload(); return r;
          })}>Record evaluation</button></>} />
      <PermissionNote perms={["submit_evidence"]} />
      <ErrorBox error={error || a.error} />
      <LiveRegion>{a.result && <div className="mb-4"><Notice tone="good">Immutable ControlEvaluation recorded: {a.result.state}.</Notice></div>}</LiveRegion>
      {d && (
        <div className="space-y-4">
          <Card>
            <KV cols={4} items={[["Objective", d.definition.objective], ["Method", d.implementation.method], ["Owner", name(d.implementation.owner_actor_id)], ["Category", d.definition.category],
              ["Window state", <Badge key="s" code={ce.state} />], ["Precedence rule", <span key="p" className="mono text-xs">{ce.precedence_rule}</span>],
              ["Aggregation", <span key="a" className="mono text-xs">{ce.aggregation_mode}</span>], ["Automation", <span key="u" className="mono text-xs">{ce.automation_requirement}</span>]]} />
            {ce.reason_codes.length > 0 && <div className="mt-3 flex flex-wrap gap-1">{ce.reason_codes.map((r: string) => <Badge key={r} code="WARN" label={r} />)}</div>}
            <p className="mt-3 text-xs text-slate-500">Precedence: FAILING &gt; REQUIRES_REVIEW &gt; NOT_EVALUATED &gt; DEGRADED &gt; OPERATING. External tool status is an input with provenance, never the control state.</p>
          </Card>
          <Card title="Tests in window">
            <Table head={["Test", "Source", "Flags", "Status", "Reasons", "Evidence", ""]}>
              {ce.tests.map((t: any) => {
                const td = d.tests.find((x: any) => x.test_definition_id === t.test_definition_id);
                return (
                  <tr key={t.test_definition_id}>
                    <td className="td font-medium">{t.name}<div className="text-[11px] text-slate-500">every {td?.cadence_days} days</div></td>
                    <td className="td mono text-xs">{td?.source_class}</td>
                    <td className="td text-[11px]">{[t.required && "required", t.primary && "primary", t.ipe_required && "IPE required", t.degrades_on_fail && "degrades"].filter(Boolean).join(" · ")}</td>
                    <td className="td"><Badge code={t.status} /></td>
                    <td className="td mono text-[11px]">{t.reason_codes.join(", ") || "—"}</td>
                    <td className="td">{(t.evidence || []).map((e: any) => <div key={e.evidence_id ?? e}><A href={`/evidence/${e.evidence_id ?? e}`}>{String(e.evidence_id ?? e).slice(0, 8)}…</A> {e.condition && <Badge code={e.condition} />} <ManualProvenance origin={e.origin_class} /></div>)}</td>
                    <td className="td"><button className="btn-ghost" disabled={!can("submit_evidence")} title={why("submit_evidence")} onClick={() => setRun(td)}>Record result</button></td>
                  </tr>
                );
              })}
            </Table>
          </Card>
          <Card title="Test run history">
            <Table head={["Test", "Occurred", "Window", "Result", "Source", "External status", "Source run"]}>
              {d.tests.flatMap((t: any) => t.runs.map((r: any) => (
                <tr key={r.test_run_id}>
                  <td className="td text-xs">{t.name}</td><td className="td text-xs">{fmtDate(r.occurred_at, true)}</td>
                  <td className="td text-xs">{fmtDate(r.window_from)} → {fmtDate(r.window_to)}</td><td className="td"><Badge code={r.result} /></td>
                  <td className="td mono text-xs">{r.source_system}</td><td className="td text-xs">{r.external_status ?? "—"}</td>
                  <td className="td">{r.source_run_id ? <A href={`/ipe/${r.source_run_id}`}>{r.source_run_id.slice(0, 8)}…</A> : "—"}</td>
                </tr>
              )))}
            </Table>
          </Card>
          <Card title="Recorded control evaluations">
            <Table head={["Window", "Cutoff", "State", "Rule", "Reasons", "Digest"]} empty={!d.evaluations.length}>
              {d.evaluations.map((e: any) => (
                <tr key={e.evaluation_id}><td className="td text-xs">{fmtDate(e.window_from)} → {fmtDate(e.window_to)}</td><td className="td mono text-xs">#{e.record_cutoff_seq}</td>
                  <td className="td"><Badge code={e.state} /></td><td className="td mono text-xs">{e.precedence_rule}</td>
                  <td className="td mono text-[11px]">{e.reason_codes.join(", ") || "—"}</td><td className="td"><Hash value={e.dependency_digest} n={8} /></td></tr>
              ))}
            </Table>
          </Card>
          <details><summary className="cursor-pointer text-xs text-navy-700">Evaluation inputs (raw)</summary><Json value={ce} /></details>
        </div>
      )}
      {run && <RecordRun td={run} onClose={() => setRun(null)} onDone={() => { setRun(null); reload(); }} />}
    </>
  );
}

function RecordRun({ td, onClose, onDone }: { td: any; onClose: () => void; onDone: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({ result: "", source_system: "manual", external_status: "", occurred_at: today, window_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10), window_to: today, detail: "" });
  const a = useAction();
  const fid = useId();
  const iso = (x: string) => new Date(x).toISOString();
  return (
    <Modal open onClose={onClose} title={`Record test result · ${td.name}`}>
      <Notice>Results from external GRC tools or vendors are inputs with provenance. They never set control state directly.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><label className="label" htmlFor={`${fid}-result`}>Result (required)</label><select id={`${fid}-result`} required className="input" value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}><option value="">Select…</option>{["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-source`}>Source system</label><input id={`${fid}-source`} className="input" value={f.source_system} onChange={(e) => setF({ ...f, source_system: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-at`}>Occurred at</label><input id={`${fid}-at`} type="date" className="input" value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-ext`}>External status</label><input id={`${fid}-ext`} className="input" value={f.external_status} onChange={(e) => setF({ ...f, external_status: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-wf`}>Window from</label><input id={`${fid}-wf`} type="date" className="input" value={f.window_from} onChange={(e) => setF({ ...f, window_from: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-wt`}>Window to</label><input id={`${fid}-wt`} type="date" className="input" value={f.window_to} onChange={(e) => setF({ ...f, window_to: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-detail`}>Detail</label><input id={`${fid}-detail`} className="input" value={f.detail} onChange={(e) => setF({ ...f, detail: e.target.value })} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={a.busy || !f.result} onClick={() => a.run(async () => {
        await api("/test-runs", { method: "POST", json: { test_definition_id: td.test_definition_id, result: f.result, source_system: f.source_system, external_status: f.external_status || null,
          occurred_at: iso(f.occurred_at), window_from: iso(f.window_from), window_to: iso(f.window_to), detail: f.detail || null, evidence_ids: [] } });
        onDone();
      })}>Record</button></div>
    </Modal>
  );
}
