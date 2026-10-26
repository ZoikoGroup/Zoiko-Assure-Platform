"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Notice, PageHeader, Table } from "@/components/ui";
import { AssertModal } from "@/components/AssertModal";

export default function FactRequests() {
  const { data, error, reload } = useApi<any[]>("/fact-requests");
  const [target, setTarget] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <>
      <PageHeader spec="ZA-APP-001 §9 · WF-APP-04" title="Fact Requests"
        subtitle="UNCERTAIN evaluations raise owned, due-dated requests for the material facts that are missing, stale, conflicting or of insufficient provenance." />
      <ErrorBox error={error} />
      {msg && <div className="mb-4"><Notice tone="good">{msg}</Notice></div>}
      <Card>
        <Table head={["Subject", "Fact", "Reason", "Owner role", "Due", "Status", "Linked evaluations", ""]} empty={data?.length === 0}>
          {data?.map((r) => {
            const overdue = r.status === "OPEN" && new Date(r.due_at) < new Date();
            return (
              <tr key={r.request_id}>
                <td className="td font-medium">{r.subject}</td>
                <td className="td"><div className="mono text-xs">{r.fact_key}</div><div className="text-[11px] text-slate-500">{r.fact_label}</div></td>
                <td className="td"><Badge code="UNKNOWN" label={r.reason_code} /></td>
                <td className="td mono text-xs">{r.owner_role}</td>
                <td className="td text-xs">{fmtDate(r.due_at)} {overdue && <Badge code="FAILED" label="OVERDUE" />}</td>
                <td className="td"><Badge code={r.status} /></td>
                <td className="td">{r.linked_evaluation_ids.map((e: string) => <div key={e}><A href={`/applicability/${e}`}>{e.slice(0, 8)}…</A></div>)}</td>
                <td className="td">{["OPEN", "IN_PROGRESS"].includes(r.status) && <button className="btn-primary" onClick={() => setTarget(r)}>Provide fact</button>}</td>
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
          const skipped = imp.filter((x) => x.error).map((x) => `${x.obligation}: ${x.error?.message ?? x.error}`);
          setMsg(`Fact recorded. Re-evaluated: ${done.join(", ") || "none"}.${skipped.length ? ` Not re-evaluated: ${skipped.join("; ")}` : ""}`);
          reload();
        }} />
    </>
  );
}
