"use client";

import { useState } from "react";
import { describeDetail, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, LiveRegion, Notice, PageHeader, PermissionNote, Table } from "@/components/ui";
import { AssertModal } from "@/components/AssertModal";

export default function FactRequests() {
  const { data, error, reload } = useApi<any[]>("/fact-requests");
  const [target, setTarget] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="ZA-APP-001 §9 · WF-APP-04" title="Fact Requests"
        subtitle="UNCERTAIN evaluations raise owned, due-dated requests for the material facts that are missing, stale, conflicting or of insufficient provenance." />
      <PermissionNote perms={["manage_facts"]} />
      <ErrorBox error={error} />
      <LiveRegion>{msg && <div className="mb-4"><Notice tone="good">{msg}</Notice></div>}</LiveRegion>
      <Card>
        <Table head={["Subject", "Fact", "Reason", "Owner role", "Due", "Status", "Linked evaluations", ""]} empty={data?.length === 0}>
          {data?.map((r) => {
            return (
              <tr key={r.request_id}>
                <td className="td font-medium">{r.subject}</td>
                <td className="td"><div className="mono text-xs">{r.fact_key}</div><div className="text-[11px] text-slate-500">{r.fact_label}</div></td>
                <td className="td"><Badge code="UNKNOWN" label={r.reason_code} /></td>
                <td className="td mono text-xs">{r.owner_role}</td>
                <td className="td text-xs">{fmtDate(r.due_at)}</td>
                <td className="td"><Badge code={r.status} /></td>
                <td className="td">{r.linked_evaluation_ids.map((e: string) => <div key={e}><A href={`/applicability/${e}`}>{e.slice(0, 8)}…</A></div>)}</td>
                <td className="td">{["OPEN", "IN_PROGRESS"].includes(r.status) && <button className="btn-primary" disabled={!can("manage_facts")} title={why("manage_facts")} onClick={() => setTarget(r)}>Provide fact</button>}</td>
              </tr>
            );
          })}
        </Table>
      </Card>
      <AssertModal subjectId={target?.subject_id} fact={target} onClose={() => setTarget(null)}
        onDone={(r) => {
          setTarget(null);
          const imp: any[] = Array.isArray(r?.impact_reevaluations) ? r.impact_reevaluations : [];
          const done = imp.filter((x) => !x.error).map((x) => `${x.obligation} → ${x.outcome}`);
          const skipped = imp.filter((x) => x.error).map((x) => `${x.obligation}: ${describeDetail(x.error) || "could not be evaluated"}`);
          setMsg(`Fact recorded. Re-evaluated: ${done.join(", ") || "none"}.${skipped.length ? ` Not re-evaluated: ${skipped.join("; ")}` : ""}`);
          reload();
        }} />
    </>
  );
}
