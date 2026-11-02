"use client";

import { Scale } from "lucide-react";
import { fmtDate } from "@/lib/format";
import { A, Badge, EmptyState, Table } from "./ui";

/* Evidence conflicts (ZA-EVD-001 §13): separate governed records linking evidence that materially disagrees. Member
   artifacts are never mutated, so their own condition is unchanged; control evaluations treat members of an OPEN
   conflict as CONFLICTING until it is resolved. Everything here is shown as GET /evidence-conflicts returns it. */
export type EvidenceConflict = {
  conflict_id: string; member_evidence_ids: string[]; description: string; status: string; disposition: string | null; created_at: string;
};

/** OPEN shares the review styling of the CONFLICTING condition; RESOLVED is neutral history, not an all-clear. */
export function ConflictStatus({ status }: { status: string }) {
  return <Badge code={status} tone={status === "OPEN" ? "review" : status === "RESOLVED" ? "neutral" : undefined} />;
}

export function ConflictTable({ conflicts, titles = {}, currentId, emptyText }: {
  conflicts: EvidenceConflict[]; titles?: Record<string, string>; currentId?: string; emptyText: string;
}) {
  if (!conflicts.length) return <EmptyState compact icon={Scale} title={emptyText} />;
  return (
    <Table head={["Status", "Recorded", "Description", currentId ? "Other evidence in conflict" : "Evidence in conflict", "Disposition"]}>
      {conflicts.map((c) => {
        const members = c.member_evidence_ids.filter((m) => m !== currentId);
        return (
          <tr key={c.conflict_id}>
            <td className="td"><ConflictStatus status={c.status} /></td>
            <td className="td whitespace-nowrap text-xs">{fmtDate(c.created_at, true)}</td>
            <td className="td text-sm">{c.description}</td>
            <td className="td text-xs">
              {members.length ? members.map((m) => <div key={m}><A href={`/evidence/${m}`}>{titles[m] ?? `${m.slice(0, 8)}…`}</A></div>)
                : <span className="text-slate-500">No other evidence recorded</span>}
            </td>
            <td className="td text-xs">{c.disposition ?? <span className="text-slate-500">{c.status === "OPEN" ? "Not resolved" : "—"}</span>}</td>
          </tr>
        );
      })}
    </Table>
  );
}
