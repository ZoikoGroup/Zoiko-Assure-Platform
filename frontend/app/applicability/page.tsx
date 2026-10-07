"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, PageHeader, Table, Tabs } from "@/components/ui";
import { AttestModal } from "@/components/AttestModal";

export default function Applicability() {
  const dets = useApi<any[]>("/applicability/determinations");
  const evals = useApi<any[]>("/applicability/evaluations");
  const [tab, setTab] = useState("Review queue");
  const [att, setAtt] = useState<any>(null);
  const [filter, setFilter] = useState("ALL");
  const evalById: Record<string, any> = Object.fromEntries((evals.data || []).map((e) => [e.evaluation_id, e]));
  const queue = (dets.data || []).filter((d) => filter === "ALL" || d.state === filter || (filter === "FLAGGED" && d.needs_review));
  return (
    <>
      <PageHeader spec="ZA-APP-001 · WF-APP-02/03" title="Applicability review"
        subtitle="Deterministic evaluations produce PROPOSED determinations. Only an authorised human can adopt them; adoption creates the obligation instance." />
      <ErrorBox error={dets.error || evals.error} />
      <Tabs tabs={["Review queue", "All evaluations"]} value={tab} onChange={setTab} />
      {tab === "Review queue" && (
        <Card actions={
          <select className="input py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
            {["ALL", "PROPOSED", "IN_REVIEW", "ADOPTED", "REJECTED", "FLAGGED"].map((x) => <option key={x}>{x}</option>)}
          </select>} title="Determinations (current revision)">
          <Table head={["Subject", "Obligation", "Outcome", "State", "Valid from", "Flags", ""]} empty={queue.length === 0}>
            {queue.map((d) => (
              <tr key={d.lineage_id}>
                <td className="td font-medium">{d.subject}</td>
                <td className="td"><div className="mono text-xs">{d.obligation_definition}</div><div className="text-[11px] text-slate-500">{d.obligation_title}</div></td>
                <td className="td"><Badge code={d.outcome} /></td>
                <td className="td"><Badge code={d.state} /></td>
                <td className="td text-xs">{fmtDate(d.valid_from)}</td>
                <td className="td text-xs">{d.needs_review && <Badge code="REVIEW_REQUIRED" title={d.review_reason ?? ""} />} {d.basis && <span className="text-slate-500">{d.basis}</span>}</td>
                <td className="td whitespace-nowrap">
                  <A href={`/applicability/${d.evaluation_id}`}>Trace</A>
                  {(["PROPOSED", "IN_REVIEW"].includes(d.state) || (d.state === "ADOPTED" && d.needs_review)) && d.outcome !== "UNCERTAIN" && (
                    <button className="btn-ghost ml-2" onClick={() => setAtt(d)}>Attest</button>
                  )}
                  {d.outcome === "UNCERTAIN" && <A href="/fact-requests"> · Resolve facts</A>}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      {tab === "All evaluations" && (
        <Card subtitle="Each evaluation is an immutable record bound to a frozen input bundle, rule version and record cutoff.">
          <Table head={["Subject", "Obligation", "Outcome", "Effective at", "Cutoff", "Rule", "Trigger", "Missing facts"]}>
            {evals.data?.map((e) => (
              <tr key={e.evaluation_id}>
                <td className="td">{e.subject.display_name}</td>
                <td className="td"><A href={`/applicability/${e.evaluation_id}`}>{e.obligation_definition.key}</A></td>
                <td className="td"><Badge code={e.outcome} /></td>
                <td className="td text-xs">{fmtDate(e.effective_at, true)}</td>
                <td className="td mono text-xs">#{e.record_cutoff_seq}</td>
                <td className="td mono text-xs">v{e.rule_version.version}</td>
                <td className="td mono text-[11px]">{e.trigger}</td>
                <td className="td text-xs">{(e.missing_facts || []).map((m: any) => (typeof m === "string" ? m : `${m.fact_definition ?? m.fact} (${m.reason_code ?? m.reason})`)).join(", ") || "—"}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      <AttestModal det={att ? { ...att, outcome: evalById[att.evaluation_id]?.outcome ?? att.outcome } : null} onClose={() => setAtt(null)} onDone={() => { setAtt(null); dets.reload(); evals.reload(); }} />
    </>
  );
}
