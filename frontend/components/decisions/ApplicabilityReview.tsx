"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, PageHeader, PermissionNote, Table } from "@/components/ui";
import { AttestModal } from "@/components/AttestModal";

export function ApplicabilityReview() {
  const dets = useApi<any[]>("/applicability/determinations");
  const evals = useApi<any[]>("/applicability/evaluations");
  const [att, setAtt] = useState<any>(null);
  const [filter, setFilter] = useState("ALL");
  const { can, why } = useCan();
  const evalById: Record<string, any> = Object.fromEntries((evals.data || []).map((e) => [e.evaluation_id, e]));
  const queue = (dets.data || []).filter((d) => filter === "ALL" || d.state === filter || (filter === "FLAGGED" && d.needs_review));
  return (
    <>
      <PageHeader spec="ZA-APP-001 · WF-APP-01" title="Applicability Review"
        subtitle="Applicability determinations waiting for a human decision."
        help="Deterministic evaluations produce PROPOSED determinations. Only an authorised human can adopt them; adoption creates the obligation instance." />
      <PermissionNote perms={["attest"]} />
      <ErrorBox error={dets.error || evals.error} />
      <Card actions={
        <select aria-label="Filter by state" className="input w-36 py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {["ALL", "PROPOSED", "IN_REVIEW", "ADOPTED", "REJECTED", "FLAGGED"].map((x) => <option key={x}>{x}</option>)}
        </select>} title="Determinations (current revision)">
        <Table head={["Subject", "Obligation", "Outcome", "State", "Valid from", "Flags", ""]} empty={!!dets.data && queue.length === 0} loading={!dets.data && !dets.error}>
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
                  <button className="btn-ghost ml-2" disabled={!can("attest")} title={why("attest")} onClick={() => setAtt(d)}>Attest</button>
                )}
                {d.outcome === "UNCERTAIN" && <A href="/fact-requests"> · Resolve facts</A>}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
      <AttestModal det={att ? { ...att, outcome: evalById[att.evaluation_id]?.outcome ?? att.outcome } : null} onClose={() => setAtt(null)} onDone={() => { setAtt(null); dets.reload(); evals.reload(); }} />
    </>
  );
}
