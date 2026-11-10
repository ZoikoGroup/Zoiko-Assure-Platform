"use client";

import { useParams } from "next/navigation";
import { useId, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, Json, KV, LiveRegion, FieldError, Modal, Notice, PageHeader, PermissionNote, Table, useAction, useToast } from "@/components/ui";
import { ManualProvenance } from "@/components/EvidenceProvenance";

export default function ControlPage() {
  const { id } = useParams<{ id: string }>();
  const [days, setDays] = useState(30);
  const { data: d, error, reload } = useApi<any>(`/controls/${id}?days=${days}`);
  const [run, setRun] = useState<any>(null);
  const a = useAction();
  const name = useActorNames();
  const toast = useToast();
  const { can, why, canAny, whyAny } = useCan();
  const EVALUATE = ["manage_controls", "request_evaluation", "package"];  // POST /controls/{id}/evaluate accepts any of these
  const ce = d?.evaluation_preview;
  return (
    <>
      <PageHeader kicker="Control implementation" spec="WF-STA-02 Control Evaluation" title={d?.definition.title ?? "Control"}
        subtitle={d ? `${d.definition.key} · scope ${d.subject.display_name}` : undefined}
        help="Its tests and their evidence decide whether the control operated in the chosen window. Recording an evaluation stores an immutable ControlEvaluation for that window."
        actions={<>
          <select aria-label="Evaluation window" className="input py-1 text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 180].map((x) => <option key={x} value={x}>last {x} days</option>)}</select>
          <button className="btn-primary" disabled={a.busy || !canAny(EVALUATE)} title={whyAny(EVALUATE)} onClick={() => a.run(async () => {
            const now = new Date(); const r = await api(`/controls/${id}/evaluate`, { method: "POST", json: { window_from: new Date(now.getTime() - days * 864e5).toISOString(), window_to: now.toISOString() } });
            reload(); return r;
          })}>Record evaluation</button></>} />
      <PermissionNote perms={[EVALUATE, "submit_evidence"]} actions={{ [EVALUATE.join("|")]: "Record evaluation", submit_evidence: "Record test result" }} />
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
      {run && <RecordRun td={run} entityId={d?.implementation.entity_id} onClose={() => setRun(null)} onDone={(r) => { setRun(null); toast(`Test result ${r.result} recorded${r.evidence_ids?.length ? ` with ${r.evidence_ids.length} evidence record(s)` : ""}. Record an evaluation to update the control state.`, "good"); reload(); }} />}
    </>
  );
}

/* The rules of POST /test-runs (app/engines/test_results.py): result is one of five codes; SKIPPED needs a reason;
   the window is half-open and non-empty; source provenance is required; evidence must belong to the same legal entity
   as the control being tested. The form checks the same rules first so the person sees them before sending. */
const RESULTS = [
  { value: "PASS", label: "PASS — the test passed" }, { value: "FAIL", label: "FAIL — the test failed" },
  { value: "ERROR", label: "ERROR — the test could not run" }, { value: "INCONCLUSIVE", label: "INCONCLUSIVE — no clear result" },
  { value: "SKIPPED", label: "SKIPPED — not run (reason required)" },
];
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

function RecordRun({ td, entityId, onClose, onDone }: { td: any; entityId?: string; onClose: () => void; onDone: (r: any) => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({ result: "", source_system: "manual", external_status: "", occurred_at: today, window_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10), window_to: today, detail: "" });
  const [sel, setSel] = useState<string[]>([]);
  const [tried, setTried] = useState(false);
  const evidence = useApi<any[]>("/evidence");
  // Only this control's legal entity's evidence can prove it (ZA-TEN-001 §14); erased payloads cannot support a result.
  const usable = (evidence.data ?? []).filter((e) => e.entity_id === entityId && e.condition !== "ERASED");
  const key = useRef(newKey());  // one key per form: a retry or double click returns the same run (API-001 §08)
  const a = useAction();
  const fid = useId();
  const iso = (x: string) => new Date(x).toISOString();
  const errs = {
    result: !f.result ? "Choose a result." : "",
    source: !f.source_system.trim() ? "Enter where the result came from." : "",
    detail: f.result === "SKIPPED" && !f.detail.trim() ? "A SKIPPED result needs a reason." : "",
    window: !f.window_from || !f.window_to ? "Enter the window tested." : f.window_to <= f.window_from ? "The window must end after it starts." : "",
    at: !f.occurred_at ? "Enter when the test ran." : "",
  };
  const show = (k: keyof typeof errs) => (tried ? errs[k] : "");
  const submit = () => {
    if (a.busy) return;
    if (Object.values(errs).some(Boolean)) { setTried(true); return; }
    a.run(async () => {
      const r = await api("/test-runs", { method: "POST", headers: { "Idempotency-Key": key.current }, json: {
        test_definition_id: td.test_definition_id, result: f.result, source_system: f.source_system.trim(), external_status: f.external_status.trim() || null,
        occurred_at: iso(f.occurred_at), window_from: iso(f.window_from), window_to: iso(f.window_to), detail: f.detail.trim() || null, evidence_ids: sel } });
      onDone(r);
    });
  };
  const close = () => { if (!a.busy) onClose(); };
  return (
    <Modal open onClose={close} title={`Record test result · ${td.name}`}>
      <form noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <p className="mb-3 text-[12px] text-slate-600">Results from external tools are recorded as inputs with provenance. They never set the control state directly; the control evaluation does.</p>
        <ErrorBox error={a.error} />
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className="label" htmlFor={`${fid}-result`}>Result <span className="text-red-700">*</span></label>
            <select id={`${fid}-result`} required className="input" aria-invalid={!!show("result") || undefined} value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}>
              <option value="">Select…</option>{RESULTS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}</select>
            <FieldError id={`${fid}-result`} message={show("result")} /></div>
          <div><label className="label" htmlFor={`${fid}-source`}>Source system <span className="text-red-700">*</span></label>
            <input id={`${fid}-source`} maxLength={80} className="input" aria-invalid={!!show("source") || undefined} value={f.source_system} onChange={(e) => setF({ ...f, source_system: e.target.value })} />
            <FieldError id={`${fid}-source`} message={show("source")} /></div>
          <div><label className="label" htmlFor={`${fid}-wf`}>Window from <span className="text-red-700">*</span></label><input id={`${fid}-wf`} type="date" className="input" value={f.window_from} onChange={(e) => setF({ ...f, window_from: e.target.value })} /></div>
          <div><label className="label" htmlFor={`${fid}-wt`}>Window to <span className="text-red-700">*</span></label><input id={`${fid}-wt`} type="date" className="input" aria-invalid={!!show("window") || undefined} value={f.window_to} onChange={(e) => setF({ ...f, window_to: e.target.value })} />
            <FieldError id={`${fid}-wt`} message={show("window")} /></div>
          <div><label className="label" htmlFor={`${fid}-at`}>Ran on <span className="text-red-700">*</span></label><input id={`${fid}-at`} type="date" className="input" value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} />
            <FieldError id={`${fid}-at`} message={show("at")} /></div>
          <div><label className="label" htmlFor={`${fid}-ext`}>External tool status</label><input id={`${fid}-ext`} maxLength={64} className="input" value={f.external_status} onChange={(e) => setF({ ...f, external_status: e.target.value })} placeholder="Optional, kept as recorded" /></div>
          <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-detail`}>{f.result === "SKIPPED" ? <>Reason <span className="text-red-700">*</span></> : "Detail"}</label>
            <textarea id={`${fid}-detail`} rows={2} className="input" aria-invalid={!!show("detail") || undefined} value={f.detail} onChange={(e) => setF({ ...f, detail: e.target.value })} />
            <FieldError id={`${fid}-detail`} message={show("detail")} /></div>
        </div>
        <fieldset className="mt-3">
          <legend className="label">Supporting evidence {sel.length ? `(${sel.length} selected)` : "(optional)"}</legend>
          <ErrorBox error={evidence.error} />
          <div className="scroll-thin max-h-48 overflow-y-auto rounded border border-slate-200 p-2">
            {!evidence.data && !evidence.error ? <p className="text-xs text-slate-500">Loading evidence…</p>
              : usable.length === 0 ? <p className="text-xs text-slate-500">No evidence recorded for this control&apos;s legal entity yet. <A href="/evidence">Upload evidence</A> first, then return here.</p>
              : usable.map((e) => (
                <label key={e.evidence_id} className="flex items-center gap-2 py-1 text-[13px]">
                  <input type="checkbox" checked={sel.includes(e.evidence_id)} onChange={(x) => setSel(x.target.checked ? [...sel, e.evidence_id] : sel.filter((y) => y !== e.evidence_id))} />
                  <span className="min-w-0 flex-1 truncate">{e.title}</span> <Badge code={e.condition} /> <ManualProvenance origin={e.origin_class} />
                </label>
              ))}
          </div>
          <p className="mt-1 text-[11px] text-slate-500">Only evidence of this control&apos;s legal entity is listed: evidence cannot prove a control in another entity.</p>
        </fieldset>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={a.busy} onClick={close}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={a.busy} aria-busy={a.busy}>{a.busy ? "Recording…" : "Record result"}</button>
        </div>
      </form>
    </Modal>
  );
}
