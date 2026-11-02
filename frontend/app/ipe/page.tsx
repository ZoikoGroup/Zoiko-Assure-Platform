"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { A, Badge, Card, ErrorBox, Modal, PageHeader, Table, TabPanel, Tabs, useAction } from "@/components/ui";

const DISPOSITIONS = ["SOURCE_DEFECT", "CONNECTOR_DEFECT", "CONFIG_SCOPE", "MAPPING_DEFECT", "ACCEPTED_LIMITATION", "FALSE_POSITIVE"];

export default function IPE() {
  const [tab, setTab] = useState("Anomalies");
  const tabsId = useId();
  return (
    <>
      <PageHeader spec="ZA-IPE-001 · WF-IPE-04" title="Information Produced by the Entity (IPE)"
        subtitle="Data your own systems produce (user lists, transaction populations) must be established as complete and accurate before a control test built on it is accepted. Anomalies from those checks are dispositioned here; policies set the required checks." />
      <Tabs id={tabsId} tabs={["Anomalies", "IPE policies"]} value={tab} onChange={setTab} />
      <TabPanel id={tabsId} value={tab}>
        {tab === "Anomalies" && <Anomalies />}
        {tab === "IPE policies" && <Policies />}
      </TabPanel>
    </>
  );
}

function Anomalies() {
  const { data, error, reload } = useApi<any[]>("/ipe/anomalies");
  const [sel, setSel] = useState<any>(null);
  const [code, setCode] = useState(DISPOSITIONS[0]);
  const [why, setWhy] = useState("");
  const a = useAction();
  const fid = useId();
  return (
    <Card subtitle="A disposition is a governed record. It never edits the original check result.">
      <ErrorBox error={error} />
      <Table head={["Check", "Class", "Dimension", "Affected", "Severity", "Disposition", ""]} empty={data?.length === 0}>
        {data?.map((x) => (
          <tr key={x.anomaly_id}>
            <td className="td mono text-xs">{x.check_id}</td><td className="td mono text-xs">{x.anomaly_class}</td><td className="td text-xs">{x.dimension}</td>
            <td className="td">{x.affected_count}<div className="mono text-[11px] text-slate-500">{(x.affected_keys || []).slice(0, 4).join(", ")}</div></td>
            <td className="td"><Badge code={x.severity === "CRITICAL" || x.severity === "HIGH" ? "FAIL" : "WARN"} label={x.severity} /></td>
            <td className="td text-xs">{x.disposition_code ? <><Badge code="CLOSED" label={x.disposition_code} /><div className="text-slate-500">{x.disposition_rationale}</div></> : "—"}</td>
            <td className="td"><A href={`/ipe/${x.source_run_id}`}>Run</A> {!x.disposition_code && <button className="btn-ghost" onClick={() => setSel(x)}>Disposition</button>}</td>
          </tr>
        ))}
      </Table>
      <Modal open={!!sel} onClose={() => setSel(null)} title="Disposition anomaly">
        <ErrorBox error={a.error} />
        <label className="label" htmlFor={`${fid}-code`}>Code</label><select id={`${fid}-code`} className="input mb-3" value={code} onChange={(e) => setCode(e.target.value)}>{DISPOSITIONS.map((d) => <option key={d}>{d}</option>)}</select>
        <label className="label" htmlFor={`${fid}-why`}>Rationale (required)</label><textarea id={`${fid}-why`} required className="input" rows={3} value={why} onChange={(e) => setWhy(e.target.value)} />
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
