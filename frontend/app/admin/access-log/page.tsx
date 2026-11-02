"use client";

import { ShieldAlert } from "lucide-react";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Card, EmptyState, ErrorBox, PageHeader, Table } from "@/components/ui";

export default function PrivilegedAccessLog() {
  return (
    <>
      <PageHeader spec="WF-IAM-03/04/08 · IAM-REQ-024 · IAM-REQ-030" title="Privileged Access Log"
        subtitle="Privileged access requests, break-glass and Zoiko support sessions, and the audit trail of security-relevant actions." />
      <div className="space-y-4">
        <div className="card">
          <EmptyState icon={ShieldAlert} title="Privileged access sessions are not yet enabled"
            description="Assure does not yet support privileged access requests, break-glass access (IAM-REQ-024) or just-in-time Zoiko support access (IAM-REQ-030), so there are no privileged session records to show. The audit trail below is recorded today." />
        </div>
        <Audit />
      </div>
    </>
  );
}

function Audit() {
  const { data, error } = useApi<any[]>("/audit?limit=200");
  return (
    <Card subtitle="Privileged and governance actions with prior and new state. Downloads are audited too.">
      <ErrorBox error={error} />
      <Table head={["Seq", "Action", "Actor", "Object", "Transition", "Reason", "At"]} empty={data?.length === 0}>
        {data?.map((e) => (
          <tr key={e.id}>
            <td className="td mono text-xs">#{e.record_seq ?? "—"}</td><td className="td mono text-xs">{e.action}</td>
            <td className="td text-xs">{e.actor_id}<div className="text-slate-500">{e.actor_type}</div></td>
            <td className="td text-xs">{e.object_type}<div className="mono text-[11px] text-slate-500">{e.object_id?.slice(0, 13)}</div></td>
            <td className="td text-xs">{e.prior_state ?? "∅"} → {e.new_state ?? "∅"}</td><td className="td text-xs">{e.reason ?? "—"}</td>
            <td className="td text-xs">{fmtDate(e.recorded_at, true)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
