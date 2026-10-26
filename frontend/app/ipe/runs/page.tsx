"use client";

import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, PageHeader, Table } from "@/components/ui";

export default function SourceRuns() {
  const runs = useApi<any[]>("/ipe/source-runs");
  return (
    <>
      <PageHeader spec="WF-IPE-01 · ZA-IPE-001" title="Source Runs"
        subtitle="Each connector extraction with its frozen request, coverage window and IPE result. Open a run to inspect scope, paging, the population manifest and every IPE check." />
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
    </>
  );
}
