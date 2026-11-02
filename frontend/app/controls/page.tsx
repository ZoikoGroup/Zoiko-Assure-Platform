"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

export default function Controls() {
  const { data, error, reload } = useApi<any[]>("/controls");
  const [open, setOpen] = useState(false);
  const name = useActorNames();
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="ZA-STA-001 §§6-9 · WF-STA-02" title="Controls & Tests"
        subtitle="The controls that satisfy your obligations. Each is implemented once per scope and reused by every obligation that references it; its tests and their evidence show whether it operated."
        actions={<button className="btn-primary" disabled={!can("manage_controls")} title={why("manage_controls")} onClick={() => setOpen(true)}>Implement control</button>} />
      <PermissionNote perms={["manage_controls"]} />
      <ErrorBox error={error} />
      <Notice>The state column is a 24-hour convenience view. Authoritative states are immutable ControlEvaluations pinned to windows and record cutoffs.</Notice>
      <Card className="mt-4">
        <Table head={["Control", "Scope", "Method", "Owner", "Since", "Current state", "Reasons"]} empty={data?.length === 0}>
          {data?.map((c) => (
            <tr key={c.control_implementation_id}>
              <td className="td"><A href={`/controls/${c.control_implementation_id}`}>{c.definition.title}</A><div className="mono text-[11px] text-slate-500">{c.definition.key}</div></td>
              <td className="td text-sm">{c.subject}</td>
              <td className="td max-w-xs text-xs">{c.method}</td>
              <td className="td text-xs">{name(c.owner_actor_id)}</td>
              <td className="td text-xs">{fmtDate(c.valid_from)}</td>
              <td className="td"><Badge code={c.current_state.state} /></td>
              <td className="td mono text-[11px]">{c.current_state.reason_codes.join(", ") || "—"}</td>
            </tr>
          ))}
        </Table>
      </Card>
      {open && <NewControl onClose={() => setOpen(false)} onDone={() => { setOpen(false); reload(); }} />}
    </>
  );
}

function NewControl({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: defs } = useApi<any[]>("/catalog/controls");
  const { data: subs } = useApi<any[]>("/org/subjects");
  const [f, setF] = useState({ control_key: "", subject_id: "", method: "", valid_from: new Date().toISOString().slice(0, 10), aggregation_mode: "ALL_REQUIRED", automation_requirement: "PREFERRED" });
  const [tests, setTests] = useState([{ name: "", source_class: "CONNECTOR", cadence_days: 30, required: true, primary: true, ipe_required: false }]);
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title="Implement control">
      <ErrorBox error={a.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div><label className="label" htmlFor={`${fid}-def`}>Control definition (required)</label><select id={`${fid}-def`} required className="input" value={f.control_key} onChange={(e) => setF({ ...f, control_key: e.target.value })}>
          <option value="">Select…</option>{defs?.map((d) => <option key={d.key} value={d.key}>{d.key}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-scope`}>Scope (subject, required)</label><select id={`${fid}-scope`} required className="input" value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value })}>
          <option value="">Select…</option>{subs?.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.display_name}</option>)}</select></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-method`}>Method (required)</label><input id={`${fid}-method`} required className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-from`}>Valid from</label><input id={`${fid}-from`} type="date" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></div>
        <div><label className="label" htmlFor={`${fid}-agg`}>Aggregation</label><select id={`${fid}-agg`} className="input" value={f.aggregation_mode} onChange={(e) => setF({ ...f, aggregation_mode: e.target.value })}>
          {["ALL_REQUIRED", "K_OF_N_TESTS", "PRIMARY_PLUS_CORROBORATION"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-auto`}>Automation</label><select id={`${fid}-auto`} className="input" value={f.automation_requirement} onChange={(e) => setF({ ...f, automation_requirement: e.target.value })}>
          {["REQUIRED", "PREFERRED", "NOT_REQUIRED"].map((x) => <option key={x}>{x}</option>)}</select></div>
      </div>
      <div className="mt-4 text-sm font-semibold text-navy-800">Tests</div>
      {tests.map((t, i) => (
        <fieldset key={i} className="mt-2 grid gap-2 rounded border border-navy-100 p-2 sm:grid-cols-4">
          <legend className="sr-only">Test {i + 1}</legend>
          <input aria-label="Test name" className="input sm:col-span-2" placeholder="Test name" value={t.name} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <select aria-label="Source class" className="input" value={t.source_class} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, source_class: e.target.value } : x)))}>
            {["CONNECTOR", "EXTERNAL_GRC", "MANUAL"].map((x) => <option key={x}>{x}</option>)}</select>
          <input aria-label="Cadence (days)" type="number" className="input" value={t.cadence_days} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, cadence_days: Number(e.target.value) } : x)))} title="Cadence days" />
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={t.required} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)))} />required</label>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={t.ipe_required} onChange={(e) => setTests(tests.map((x, j) => (j === i ? { ...x, ipe_required: e.target.checked } : x)))} />IPE required</label>
        </fieldset>
      ))}
      <button className="btn-ghost mt-2" onClick={() => setTests([...tests, { name: "", source_class: "MANUAL", cadence_days: 30, required: true, primary: false, ipe_required: false }])}>+ Add test</button>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={a.busy || !f.control_key || !f.subject_id || !f.method}
        onClick={() => a.run(async () => { await api("/controls", { method: "POST", json: { ...f, valid_from: new Date(f.valid_from).toISOString(), tests } }); onDone(); })}>Create</button></div>
    </Modal>
  );
}
