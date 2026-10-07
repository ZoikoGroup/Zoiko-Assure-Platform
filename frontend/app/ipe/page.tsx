"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Modal, Notice, PageHeader, Table, Tabs, useAction } from "@/components/ui";

const DISPOSITIONS = ["SOURCE_DEFECT", "CONNECTOR_DEFECT", "CONFIG_SCOPE", "MAPPING_DEFECT", "ACCEPTED_LIMITATION", "FALSE_POSITIVE"];

export default function IPE() {
  const [tab, setTab] = useState("Source runs");
  const runs = useApi<any[]>("/ipe/source-runs");
  const [exec, setExec] = useState(false);
  return (
    <>
      <PageHeader spec="ZA-IPE-001" title="Source runs & IPE"
        subtitle="Information Produced by the Entity: proves that a population is complete and accurate before any test result built on it is accepted."
        actions={<button className="btn-primary" onClick={() => setExec(true)}>Execute source run</button>} />
      <Tabs tabs={["Source runs", "Anomalies", "IPE policies"]} value={tab} onChange={setTab} />
      {tab === "Source runs" && (
        <Card>
          <ErrorBox error={runs.error} />
          <Table head={["Run", "Connector", "Coverage", "Run outcome", "Completeness", "Accuracy", "Overall IPE", "Reasons"]} empty={runs.data?.length === 0}>
            {runs.data?.map((r) => (
              <tr key={r.source_run_id}>
                <td className="td"><A href={`/ipe/${r.source_run_id}`}>{r.source_run_id.slice(0, 8)}…</A><div className="text-[11px] text-slate-500">{fmtDate(r.started_at, true)}</div></td>
                <td className="td text-sm">{r.connector}<div className="mono text-[11px] text-slate-500">{r.connector_build}</div></td>
                <td className="td text-xs">{fmtDate(r.coverage_from)} → {fmtDate(r.coverage_to)}</td>
                <td className="td"><Badge code={r.run_outcome} /></td>
                <td className="td"><Badge code={r.ipe?.completeness_result} /></td>
                <td className="td"><Badge code={r.ipe?.accuracy_result} /></td>
                <td className="td"><Badge code={r.ipe?.overall_result} /></td>
                <td className="td mono text-[11px]">{r.ipe?.reason_codes?.join(", ") || "—"}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      {tab === "Anomalies" && <Anomalies />}
      {tab === "IPE policies" && <Policies />}
      {exec && <Execute onClose={() => setExec(false)} onDone={() => { setExec(false); runs.reload(); }} />}
    </>
  );
}

function Anomalies() {
  const { data, error, reload } = useApi<any[]>("/ipe/anomalies");
  const [sel, setSel] = useState<any>(null);
  const [code, setCode] = useState(DISPOSITIONS[0]);
  const [why, setWhy] = useState("");
  const a = useAction();
  return (
    <Card subtitle="A disposition is a governed record. It never edits the original check result.">
      <ErrorBox error={error} />
      <Table head={["Check", "Class", "Dimension", "Affected", "Severity", "Disposition", ""]} empty={data?.length === 0}>
        {data?.map((x) => (
          <tr key={x.anomaly_id}>
            <td className="td mono text-xs">{x.check_id}</td><td className="td mono text-xs">{x.anomaly_class}</td><td className="td text-xs">{x.dimension}</td>
            <td className="td">{x.affected_count}<div className="mono text-[10px] text-slate-500">{(x.affected_keys || []).slice(0, 4).join(", ")}</div></td>
            <td className="td"><Badge code={x.severity === "CRITICAL" || x.severity === "HIGH" ? "FAIL" : "WARN"} label={x.severity} /></td>
            <td className="td text-xs">{x.disposition_code ? <><Badge code="CLOSED" label={x.disposition_code} /><div className="text-slate-500">{x.disposition_rationale}</div></> : "—"}</td>
            <td className="td"><A href={`/ipe/${x.source_run_id}`}>Run</A> {!x.disposition_code && <button className="btn-ghost" onClick={() => setSel(x)}>Disposition</button>}</td>
          </tr>
        ))}
      </Table>
      <Modal open={!!sel} onClose={() => setSel(null)} title="Disposition anomaly">
        <ErrorBox error={a.error} />
        <label className="label">Code</label><select className="input mb-3" value={code} onChange={(e) => setCode(e.target.value)}>{DISPOSITIONS.map((d) => <option key={d}>{d}</option>)}</select>
        <label className="label">Rationale</label><textarea className="input" rows={3} value={why} onChange={(e) => setWhy(e.target.value)} />
        <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!why || a.busy} onClick={() => a.run(async () => {
          await api(`/ipe/anomalies/${sel.anomaly_id}/dispositions`, { method: "POST", json: { disposition_code: code, rationale: why } }); setSel(null); reload();
        })}>Record</button></div>
      </Modal>
    </Card>
  );
}

function Policies() {
  const { data, error } = useApi<any[]>("/ipe/policies");
  return (
    <Card>
      <ErrorBox error={error} />
      <Table head={["Policy", "Purpose", "Completeness checks", "Accuracy checks", "Allowed exclusions", "Partial"]}>
        {data?.map((p) => (
          <tr key={p.ipe_policy_version_id}>
            <td className="td font-medium">{p.name}</td><td className="td text-xs">{p.purpose}</td>
            <td className="td mono text-[11px]">{p.completeness_required ? p.required_completeness_checks.join(", ") : "not required"}</td>
            <td className="td mono text-[11px]">{p.accuracy_required ? p.required_accuracy_checks.join(", ") : "not required"}</td>
            <td className="td mono text-[11px]">{(p.allowed_exclusions || []).join(", ") || "—"}</td>
            <td className="td">{p.allow_partial ? "yes" : "no"}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

const users = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `u${String(i).padStart(3, "0")}`, userPrincipalName: `user${i}@zoiko.example`, mfaState: "Enforced", accountEnabled: true, lastSignIn: "2026-10-01T08:00:00Z", etag: `W/"${i}"` }));
const SAMPLE = {
  coverage_from: "2026-10-01T00:00:00Z", coverage_to: "2026-10-06T00:00:00Z",
  parameters: { scope: { tenant: "zoiko-eu.onmicrosoft.com", accountEnabled: true } },
  consistency_boundary: { snapshot_token: "snap-2026-10-06T00:00Z" },
  source_reported_total: 6, terminal_reached: true,
  pages: [{ page: 1, cursor: "e1", records: users(6).slice(0, 3) }, { page: 2, cursor: "e2", records: users(6).slice(3) }],
};

function Execute({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { data: cons } = useApi<any[]>("/connectors");
  const { data: pols } = useApi<any[]>("/ipe/policies");
  const insts = (cons || []).flatMap((c) => (c.installations || []).map((i: any) => ({ ...i, name: c.name })));
  const [inst, setInst] = useState("");
  const [pol, setPol] = useState("");
  const [td, setTd] = useState("");
  const [payload, setPayload] = useState(JSON.stringify(SAMPLE, null, 2));
  const a = useAction();
  return (
    <Modal open onClose={onClose} title="Execute source run">
      <Notice>The payload is what the source system returned. Assure freezes the request, builds the population manifest, runs the IPE checks and captures the result as evidence.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><label className="label">Installation</label><select className="input" value={inst} onChange={(e) => setInst(e.target.value)}><option value="">Select…</option>
          {insts.map((i) => <option key={i.installation_id} value={i.installation_id}>{i.name} · {i.entity_id}</option>)}</select></div>
        <div><label className="label">IPE policy</label><select className="input" value={pol} onChange={(e) => setPol(e.target.value)}><option value="">Select…</option>
          {pols?.map((p) => <option key={p.ipe_policy_version_id} value={p.ipe_policy_version_id}>{p.name}</option>)}</select></div>
        <div className="sm:col-span-2"><label className="label">Test definition id (optional, records a TestRun)</label><input className="input mono text-xs" value={td} onChange={(e) => setTd(e.target.value)} /></div>
        <div className="sm:col-span-2"><label className="label">Source payload (JSON)</label><textarea className="input mono h-72 text-[11px]" value={payload} onChange={(e) => setPayload(e.target.value)} spellCheck={false} /></div>
      </div>
      {a.result && <div className="mt-3"><Notice tone={a.result.ipe_evaluation.overall_result === "IPE_VERIFIED" ? "good" : "warn"}>IPE: {a.result.ipe_evaluation.overall_result} · evidence {a.result.evidence_id.slice(0, 8)}…</Notice></div>}
      <div className="mt-4 flex justify-end gap-2">
        {a.result && <button className="btn-ghost" onClick={onDone}>Done</button>}
        <button className="btn-primary" disabled={!inst || !pol || a.busy} onClick={() => a.run(() => api("/ipe/source-runs", { method: "POST", json: { installation_id: inst, ipe_policy_version_id: pol, test_definition_id: td || null, payload: JSON.parse(payload) } }))}>Execute</button>
      </div>
    </Modal>
  );
}
