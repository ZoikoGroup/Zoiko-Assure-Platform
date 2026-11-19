"use client";

import { useState } from "react";
import { describeDetail, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { roleLabel, useCan } from "@/lib/roles";
import Link from "next/link";
import { A, Badge, Card, ErrorBox, LiveRegion, METRIC_GROUP, Notice, PageHeader, PermissionNote, Table } from "@/components/ui";
import { AssertModal } from "@/components/AssertModal";

/* "My Work & Approvals": the decisions waiting on someone in this organisation (determinations to adopt, exception
   requests to decide) and the facts requested to finish applicability decisions. Counts are the records themselves;
   each opens the screen where the decision is made, and says whether the signed-in person's role can make it. */
export default function FactRequests() {
  const { data, error, reload } = useApi<any[]>("/fact-requests");
  const dets = useApi<any[]>("/applicability/determinations");
  const excs = useApi<any[]>("/exceptions");
  const [target, setTarget] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="ZA-APP-001 §9 · WF-APP-04" title="My Work & Approvals"
        subtitle="Decisions waiting for sign-off, and facts needed to finish applicability decisions."
        help="UNCERTAIN evaluations raise owned, due-dated requests for the material facts that are missing, stale, conflicting or of insufficient provenance." />
      <PermissionNote perms={["manage_facts"]} actions={{ manage_facts: "Provide facts" }} />
      <ErrorBox error={error || dets.error || excs.error} />
      <LiveRegion>{msg && <div className="mb-4"><Notice tone="good">{msg}</Notice></div>}</LiveRegion>
      <ul aria-label="Approvals waiting" className={`mb-6 ${METRIC_GROUP} sm:grid-cols-3`}>
        <WorkTile label="Determinations to adopt" href="/applicability" perm="attest" can={can}
          value={dets.data?.filter((d) => ["PROPOSED", "IN_REVIEW"].includes(d.state) && d.outcome !== "UNCERTAIN").length}
          hint="Attest or challenge applicability" failed={!!dets.error} />
        <WorkTile label="Exception requests to decide" href="/governance" perm="approve_exception" can={can}
          value={excs.data?.filter((x) => x.state === "PENDING_APPROVAL").length}
          hint="Approve or reject; not your own requests" failed={!!excs.error} />
        <WorkTile label="Fact requests open" href="#fact-requests" perm="manage_facts" can={can}
          value={data?.filter((r) => ["OPEN", "IN_PROGRESS"].includes(r.status)).length}
          hint="Facts keeping decisions UNCERTAIN" failed={!!error} />
      </ul>
      <Card title={<span id="fact-requests">Fact requests</span>}>
        <Table head={["Subject", "Fact", "Reason", "Owner role", "Due", "Status", "Linked evaluations", ""]} empty={data?.length === 0} loading={!data && !error}
          emptyTitle="No fact requests" emptyDescription="Requests appear when an applicability evaluation is UNCERTAIN because a material fact is missing or stale.">
          {data?.map((r) => {
            return (
              <tr key={r.request_id}>
                <td className="td font-medium">{r.subject}</td>
                <td className="td"><div className="mono text-xs">{r.fact_key}</div><div className="text-[11px] text-slate-500">{r.fact_label}</div></td>
                <td className="td"><Badge code="UNKNOWN" label={r.reason_code} /></td>
                <td className="td text-xs">{roleLabel(r.owner_role)}</td>
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

/** One queue: how many records wait, whether this person's role can act on them, and where they are worked. */
function WorkTile({ label, value, hint, href, perm, can, failed }:
  { label: string; value?: number; hint: string; href: string; perm: string; can: (p: string) => boolean; failed: boolean }) {
  const mine = can(perm);
  return (
    <li className="bg-white">
      <Link href={href} className="flex h-full flex-col px-4 py-3.5 transition-colors duration-150 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400 sm:px-5">
        <span className="text-[13px] font-medium leading-5 text-slate-600">{label}</span>
        <span className="mt-1.5 text-[24px] font-semibold leading-8 tracking-[-0.01em] tabular-nums text-slate-900">{failed ? "—" : value ?? "…"}</span>
        <span className="mt-1 text-[12px] leading-4 text-slate-500">{failed ? "Unavailable" : hint}</span>
        <span className={`mt-2 inline-flex items-center gap-1.5 text-[12px] font-medium leading-4 ${mine ? "text-emerald-700" : "text-slate-500"}`}>
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${mine ? "bg-emerald-500" : "bg-slate-400"}`} />{mine ? "You can act on these" : "View only for your role"}
        </span>
      </Link>
    </li>
  );
}
