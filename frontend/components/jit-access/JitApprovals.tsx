"use client";

import { useMemo, useState } from "react";
import type { RefObject } from "react";
import { Check, Inbox, X } from "lucide-react";
import { Button, EmptyState, TabPanel, Tabs } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { ApprovalBadge, LocalTag } from "./badges";
import { DEMO_NOW } from "./jitMockData";
import { APPROVAL, fmtMinutes, hoursLabel, ms, orgName, type Row } from "./model";
import { APPROVAL_STATUSES, type ApprovalStatus } from "./types";
import type { DemoAction } from "./JitDemoActionDialog";
import { useTabInView } from "./useTabInView";

/* The approval workflow, as a demonstration: pending requests (oldest first, as an approver would work through them)
   with local-only Approve and Reject, and the approved and rejected requests with who decided, when and why. */

export function JitApprovals({ rows, onOpen, onAction, panelRef }:
  { rows: Row[]; onOpen: (id: string) => void; onAction: (a: DemoAction) => void; panelRef: RefObject<HTMLDivElement | null> }) {
  const [view, setView] = useState<ApprovalStatus>("PENDING");
  useTabInView("jit-approvals", view);
  const list = useMemo(() => rows.filter((r) => r.request.status === view).sort((a, b) => view === "PENDING"
    ? a.request.requestedAt.localeCompare(b.request.requestedAt)
    : (b.request.decision?.at ?? "").localeCompare(a.request.decision?.at ?? "")), [rows, view]);
  const tabs = APPROVAL_STATUSES.map((s) => ({ value: s, label: APPROVAL[s].label, count: rows.filter((r) => r.request.status === s).length }));

  return (
    <div>
      <Tabs id="jit-approvals" label="Show requests by approval status" variant="pills" value={view} onChange={(v) => setView(v as ApprovalStatus)} items={tabs} />
      <div ref={panelRef} tabIndex={-1} className="mt-4 rounded-card">
        <TabPanel id="jit-approvals" value={view} className="rounded-card">
          {list.length === 0 ? (
            <EmptyState compact icon={Inbox} title={`No ${APPROVAL[view].label.toLowerCase()} requests`}
              description={view === "PENDING" ? "Nothing is waiting for a decision in the demonstration data." : "No request in the demonstration data has this status."} />
          ) : (
            <ul aria-label={`${APPROVAL[view].label} requests`} className="grid gap-4 lg:grid-cols-2">
              {list.map((r) => <li key={r.request.id}><ApprovalCard r={r} onOpen={onOpen} onAction={onAction} /></li>)}
            </ul>
          )}
        </TabPanel>
      </div>
    </div>
  );
}

function ApprovalCard({ r, onOpen, onAction }: { r: Row; onOpen: (id: string) => void; onAction: (a: DemoAction) => void }) {
  const q = r.request;
  const d = q.decision;
  const waited = Math.max(0, Math.round((ms(DEMO_NOW) - ms(q.requestedAt)) / 60_000));
  return (
    <article aria-label={`${q.id}, ${q.requester.name}`} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="min-w-0 flex-[1_1_12rem]">
          <p className="type-code text-fg-muted">{q.id}</p>
          <p className="break-words type-secondary font-semibold text-fg">{q.requester.name}<span className="font-normal text-fg-secondary"> · {q.requester.team}</span></p>
        </div>
        <div className="flex flex-wrap gap-1.5"><ApprovalBadge status={q.status} size="sm" />{r.local && <LocalTag />}</div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Organisation</dt><dd className="type-secondary text-fg">{orgName(q.organisation)}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Requested role</dt><dd className="type-secondary text-fg">{r.role?.label ?? q.roleId}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Requested duration</dt><dd className="type-secondary text-fg">{hoursLabel(q.requestedHours)}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Requested at</dt><dd className="type-secondary tabular-nums text-fg">{fmtDate(q.requestedAt, true)}</dd></div>
      </dl>
      <figure className="mt-3">
        <figcaption className="type-caption text-fg-muted">Justification</figcaption>
        <blockquote className="mt-1 rounded-control border-l-4 border-line-strong bg-surface-raised px-3 py-2 type-secondary text-fg-secondary">{q.justification}</blockquote>
      </figure>

      <div className="mt-3 rounded-control border border-line bg-surface-raised/60 px-3 py-2 type-secondary">
        {q.status === "PENDING" && <p className="text-fg-secondary">Waiting {fmtMinutes(waited)} for a decision, at the demonstration clock.</p>}
        {q.status === "APPROVED" && d && (
          <p className="text-fg-secondary">
            Approved by <span className="font-semibold text-fg">{d.by}</span> on {fmtDate(d.at, true)} for {hoursLabel(d.approvedHours ?? q.requestedHours)}
            {d.approvedHours !== undefined && d.approvedHours < q.requestedHours && <> (requested {hoursLabel(q.requestedHours)})</>}.
          </p>
        )}
        {q.status === "REJECTED" && d && <>
          <p className="text-fg-secondary">Rejected by <span className="font-semibold text-fg">{d.by}</span> on {fmtDate(d.at, true)}.</p>
          <p className="mt-1 text-fg"><span className="font-semibold">Reason: </span>{d.reason ?? "None recorded."}</p>
        </>}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
        {q.status === "PENDING" && <>
          <Button size="sm" variant="primary" icon={Check} onClick={() => onAction({ kind: "approve", requestId: q.id })}>Approve (demo)…<span className="sr-only"> {q.id}</span></Button>
          <Button size="sm" variant="danger" icon={X} onClick={() => onAction({ kind: "reject", requestId: q.id })}>Reject (demo)…<span className="sr-only"> {q.id}</span></Button>
        </>}
        <Button size="sm" variant="secondary" onClick={() => onOpen(q.id)}>Details<span className="sr-only"> of {q.id}</span></Button>
      </div>
    </article>
  );
}
