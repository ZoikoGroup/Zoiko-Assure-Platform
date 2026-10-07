"use client";

import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, PageHeader, Table } from "@/components/ui";

export default function Obligations() {
  const { data, error } = useApi<any[]>("/obligations");
  return (
    <>
      <PageHeader spec="ZA-STA-001 · WF-STA-01" title="Obligation dossiers"
        subtitle="Adopted obligation instances with their latest period assurance snapshot. States are evidential (PROVEN / NOT_PROVEN / FAILING / REQUIRES_REVIEW), never ‘compliant’." />
      <ErrorBox error={error} />
      <Card>
        <Table head={["Obligation", "Subject", "Lifecycle", "Valid", "Latest snapshot", "Period", "Exceptions"]} empty={data?.length === 0}>
          {data?.map((o) => (
            <tr key={o.lineage_id}>
              <td className="td"><A href={`/obligations/${o.lineage_id}`}>{o.definition.title}</A><div className="mono text-[11px] text-slate-500">{o.definition.key} · {o.definition.schedule_kind}</div></td>
              <td className="td">{o.subject.display_name}<div className="mono text-[11px] text-slate-500">{o.subject.subject_type}</div></td>
              <td className="td"><Badge code={o.lifecycle} /></td>
              <td className="td text-xs">{fmtDate(o.valid_from)} → {o.valid_to ? fmtDate(o.valid_to) : "open"}</td>
              <td className="td">{o.latest_snapshot ? <Badge code={o.latest_snapshot.state} /> : <Badge code="NOT_EVALUATED" label="NO SNAPSHOT" />}</td>
              <td className="td text-xs">{o.latest_snapshot ? `${fmtDate(o.latest_snapshot.period_from)} → ${fmtDate(o.latest_snapshot.period_to)}` : "—"}</td>
              <td className="td">{o.open_exceptions ? <Badge tone="exception" label={`${o.open_exceptions} APPROVED`} /> : "—"}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}
