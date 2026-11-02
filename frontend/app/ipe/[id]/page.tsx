"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Hash, Json, KV, Notice, PageHeader, Table, useAction } from "@/components/ui";

export default function SourceRunPage() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, reload } = useApi<any>(`/ipe/source-runs/${id}`);
  const { data: pols } = useApi<any[]>("/ipe/policies");
  const [pol, setPol] = useState("");
  const a = useAction();
  const r = d?.source_run;
  const pm = d?.population_manifest;
  const ie = d?.ipe_evaluations?.[0];
  const eq = d?.reconciliation_equation;
  return (
    <>
      <PageHeader spec="WF-IPE-01 Source Run Inspector · WF-IPE-02 Population Reconciliation" title={d ? `${d.connector.name} · run ${id.slice(0, 8)}` : "Source run"}
        subtitle="Request, scope, consistency boundary, paging, population manifest and IPE checks for a single extraction."
        actions={<>
          <select aria-label="IPE policy for re-evaluation" className="input py-1 text-xs" value={pol} onChange={(e) => setPol(e.target.value)}><option value="">Re-evaluate under policy…</option>
            {pols?.map((p) => <option key={p.ipe_policy_version_id} value={p.ipe_policy_version_id}>{p.name}</option>)}</select>
          <button className="btn-primary" disabled={!pol || a.busy} onClick={() => a.run(async () => { await api(`/ipe/source-runs/${id}/evaluate-ipe`, { method: "POST", json: { ipe_policy_version_id: pol } }); reload(); })}>Evaluate</button>
        </>} />
      <ErrorBox error={error || a.error} />
      {d && (
        <div className="space-y-4">
          <Card>
            <KV cols={4} items={[
              ["Overall IPE", <Badge key="o" code={ie?.overall_result} />], ["Completeness", <Badge key="c" code={ie?.completeness_result} />],
              ["Accuracy", <Badge key="a" code={ie?.accuracy_result} />], ["Run outcome", <Badge key="r" code={r.run_outcome} />],
              ["Connector build", <span key="b" className="mono text-xs">{r.connector_build}</span>], ["Certification", <Badge key="ce" code={d.connector.certification} />],
              ["Coverage", `${fmtDate(r.coverage_from, true)} → ${fmtDate(r.coverage_to, true)}`], ["Started / completed", `${fmtDate(r.started_at, true)} / ${fmtDate(r.completed_at, true)}`],
              ["Request fingerprint", <Hash key="f" value={r.request_fingerprint} />], ["Consistency boundary", <span key="cb" className="mono text-xs">{JSON.stringify(r.consistency_boundary)}</span>],
              ["Extraction contract", <span key="x" className="mono text-xs">{d.extraction_contract?.source_object} v{d.extraction_contract?.version}</span>],
              ["Mapping contract", <span key="m" className="mono text-xs">v{d.mapping_contract?.version}</span>],
            ]} />
            {ie?.reason_codes?.length > 0 && <div className="mt-3 flex flex-wrap gap-1">{ie.reason_codes.map((x: string) => <Badge key={x} code="FAIL" label={x} />)}</div>}
          </Card>

          {eq && (
            <Card title="Population reconciliation" subtitle={eq.verified_condition}>
              <div className="flex flex-wrap items-center gap-3 text-center">
                {[["Intended source population", eq.intended_source_population], ["−", null], ["Extracted unique", eq.extracted_unique_population], ["−", null],
                  ["Governed exclusions", eq.governed_exclusions], ["=", null], ["Unresolved gap", eq.unresolved_gap_population]].map(([l, v], i) =>
                  v === null && typeof l === "string" && l.length === 1 ? <span key={i} className="text-lg text-slate-500">{l}</span> : (
                    <div key={i} className={`rounded-lg px-4 py-2 ring-1 ${l === "Unresolved gap" ? (v === 0 ? "bg-emerald-50 ring-emerald-200" : "bg-red-50 ring-red-200") : "bg-navy-50 ring-navy-100"}`}>
                      <div className="text-lg font-semibold text-slate-900">{v ?? "?"}</div><div className="text-[11px] text-slate-600">{l}</div>
                    </div>
                  ))}
              </div>
              {pm && (
                <div className="mt-4">
                  <KV cols={4} items={[["Raw extracted", pm.extracted_raw_count], ["Duplicates", pm.duplicate_count], ["Rejected", pm.rejected_count], ["Terminal state", <TerminalState key="t" state={pm.terminal_state} />],
                    ["Population definition", pm.population_definition], ["Digest algorithm", <span key="da" className="mono text-xs">{pm.digest_algorithm}</span>]]} />
                </div>
              )}
            </Card>
          )}

          <Card title="IPE checks" subtitle={ie ? `Engine ${ie.engine_version} · policy ${d.ipe_policy?.name}` : undefined}>
            <Table head={["Check", "Family", "Dimension", "Result", "Reason", "Detail"]} empty={!ie?.checks?.length}>
              {ie?.checks?.map((c: any) => (
                <tr key={c.check_id}><td className="td mono text-xs">{c.check_id}</td><td className="td text-xs">{c.family}</td><td className="td text-xs">{c.dimension}</td>
                  <td className="td"><Badge code={c.result} /></td><td className="td mono text-[11px]">{c.reason_code ?? "—"}</td><td className="td max-w-md break-all text-[11px] text-slate-600">{c.detail}</td></tr>
              ))}
            </Table>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Scope check">
              <KV cols={1} items={[["Required", <span key="r" className="mono text-xs">{d.scope_check.required.join(", ")}</span>], ["Actual", <span key="a" className="mono text-xs">{d.scope_check.actual.join(", ")}</span>],
                ["Missing", d.scope_check.missing.length ? <Badge key="m" code="FAIL" label={d.scope_check.missing.join(", ")} /> : <Badge key="m" code="PASS" label="none" />]]} />
            </Card>
            <Card title="Digests (labelled by origin)">
              <Notice>Assure-generated digests are <b>not</b> source checksums. They prove that the bytes Assure received are unchanged, not that the source was complete.</Notice>
              {pm && <div className="mt-3 space-y-2 text-xs">
                {Object.entries(d.digest_labels).map(([k, label]) => (
                  <div key={k}><div className="label">{label as string}</div>
                    {k === "source_checksum_refs" ? (pm.source_checksum_refs?.length ? pm.source_checksum_refs.map((x: any, i: number) => <div key={i}><Hash value={x.value} /> <span className="text-slate-500">{x.algorithm}</span></div>) : <span className="text-slate-500">Not provided by source</span>) : <Hash value={pm[k]} />}
                  </div>
                ))}
              </div>}
            </Card>
          </div>

          <Card title="Anomalies">
            <Table head={["Check", "Class", "Affected", "Severity", "Disposition"]} empty={!d.anomalies.length}>
              {d.anomalies.map((x: any) => (
                <tr key={x.anomaly_id}><td className="td mono text-xs">{x.check_id}</td><td className="td mono text-xs">{x.anomaly_class}</td>
                  <td className="td">{x.affected_count} <span className="mono text-[11px] text-slate-500">{(x.affected_keys || []).slice(0, 5).join(", ")}</span></td>
                  <td className="td">{x.severity}</td><td className="td text-xs">{x.disposition_code ?? <A href="/ipe">disposition in queue →</A>}</td></tr>
              ))}
            </Table>
          </Card>
          {d.ipe_evaluations.length > 1 && (
            <Card title="Evaluation history" subtitle="Re-evaluation appends a new IPE evaluation; prior results are retained.">
              <Table head={["Created", "Overall", "Completeness", "Accuracy", "Policy"]}>
                {d.ipe_evaluations.map((x: any) => (
                  <tr key={x.ipe_evaluation_id}><td className="td text-xs">{fmtDate(x.created_at, true)}</td><td className="td"><Badge code={x.overall_result} /></td>
                    <td className="td"><Badge code={x.completeness_result} /></td><td className="td"><Badge code={x.accuracy_result} /></td><td className="td"><Hash value={x.ipe_policy_version_id} n={8} /></td></tr>
                ))}
              </Table>
            </Card>
          )}
          <details><summary className="cursor-pointer text-xs text-navy-700">Raw request parameters & pages</summary><Json value={{ request_parameters: r.request_parameters, pages: r.pages }} /></details>
        </div>
      )}
    </>
  );
}

/* Population manifest terminal state: { terminal_reached, cursor_loop, last_cursor } (engines/ipe.py). */
function TerminalState({ state }: { state: any }) {
  if (!state || typeof state !== "object") return <Badge code={typeof state === "string" ? state : null} />;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {state.terminal_reached
        ? <Badge tone="good" label="TERMINAL REACHED" />
        : <Badge tone="bad" code="IPE_CURSOR_INCOMPLETE" label="TERMINAL NOT REACHED" />}
      {state.cursor_loop ? <Badge tone="bad" label="CURSOR LOOP" /> : <Badge tone="neutral" label="NO CURSOR LOOP" />}
      <span className="mono text-[11px] text-slate-500">last cursor {state.last_cursor ?? "—"}</span>
    </div>
  );
}
