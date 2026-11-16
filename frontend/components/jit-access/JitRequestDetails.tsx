"use client";

import { useId } from "react";
import type { ReactNode, Ref } from "react";
import { Check, ShieldOff, TimerReset, X } from "lucide-react";
import { Button, DescriptionList, Timeline } from "@/components/ds";
import { DemoTag, Missing } from "@/components/platformDemo";
import { Drawer } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { AccessBadge, ApprovalBadge, LocalTag } from "./badges";
import { DEMO_NOW } from "./jitMockData";
import { ACCESS, APPROVAL, EVENT, addHours, fmtMinutes, grantedHours, hoursLabel, isActive, type JitEvent, type Row } from "./model";
import type { DemoAction } from "./JitDemoActionDialog";

/* One request, read from the records already on screen. The drawer follows the Super Admin detail pattern (Users &
   Roles, Source Integrity): focus moves into it, and Escape or Close returns focus to the button that opened it. The
   demonstration actions sit at the top, so the drawer opens there rather than scrolled down to a lower control. */

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <div className="mb-2 flex flex-wrap items-center gap-2"><h4 id={id} className="type-secondary font-semibold text-fg">{title}</h4>{aside}</div>
      {children}
    </section>
  );
}

/** The demonstration actions for a request. `focusRef` is a stable focus target for after an action removes the button
 *  that started it (for example Approve, once the request is approved). */
function DemoActions({ r, onAction, focusRef }: { r: Row; onAction: (a: DemoAction) => void; focusRef: Ref<HTMLDivElement> }) {
  const pending = r.request.status === "PENDING";
  const sessionId = r.session && isActive(r.access) ? r.session.id : null;
  const active = sessionId !== null;
  const hintId = useId();
  return (
    <div ref={focusRef} tabIndex={-1} role="group" aria-label="Demonstration actions" aria-describedby={hintId}
      className="rounded-card border border-dashed border-accent/50 bg-accent/5 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {pending && <>
          <Button size="sm" variant="primary" icon={Check} onClick={() => onAction({ kind: "approve", requestId: r.request.id })}>Approve (demo)…</Button>
          <Button size="sm" variant="danger" icon={X} onClick={() => onAction({ kind: "reject", requestId: r.request.id })}>Reject (demo)…</Button>
        </>}
        {sessionId && <>
          <Button size="sm" variant="secondary" icon={TimerReset} onClick={() => onAction({ kind: "extend", sessionId })}>Extend (demo)…</Button>
          <Button size="sm" variant="danger" icon={ShieldOff} onClick={() => onAction({ kind: "revoke", sessionId })}>Revoke (demo)…</Button>
        </>}
      </div>
      <p id={hintId} className="mt-2 type-caption text-fg-muted">
        {pending || active
          ? "Local demonstration: these change only what this page shows. No access is granted, ended or extended, and nothing is sent."
          : "No demonstration action applies to this request."}
      </p>
    </div>
  );
}

export function JitRequestDetails({ row, events, onAction, onClose, actionsRef }:
  { row: Row; events: JitEvent[]; onAction: (a: DemoAction) => void; onClose: () => void; actionsRef: Ref<HTMLDivElement> }) {
  const { request: r, session: s } = row;
  const d = r.decision;
  const shortened = !!d?.approvedHours && d.approvedHours < r.requestedHours;
  const timeline = [...events].reverse().map((e) => ({
    id: e.id, status: EVENT[e.kind].tone,
    title: <>{EVENT[e.kind].label}{e.local && <span className="ml-2 align-middle"><LocalTag /></span>}</>,
    meta: `${fmtDate(e.at, true)} · ${e.actor}`, description: e.description,
  }));

  return (
    <Drawer open onClose={onClose} title={`${r.id} · ${r.requester.name}`} width="max-w-2xl">
      <div className="space-y-6">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <ApprovalBadge status={r.status} />
            <AccessBadge status={row.access} />
            <code className="type-code text-fg-secondary">{r.id}</code>
            <DemoTag />
            {row.local && <LocalTag />}
          </div>
          <DemoActions focusRef={actionsRef} r={row} onAction={onAction} />
        </div>

        <Section title="Requester">
          <DescriptionList columns={2} items={[
            ["Name", r.requester.name],
            ["Team", r.requester.team],
            ["Email", <span key="e" className="break-all type-code">{r.requester.email}</span>],
          ]} />
        </Section>

        <Section title="Request">
          <DescriptionList columns={2} items={[
            ["Request ID", <code key="i" className="type-code">{r.id}</code>],
            ["Organisation", r.organisation ? <span key="o">{r.organisation.name}<span className="block type-code text-fg-muted">{r.organisation.id}</span></span>
              : <span key="o">Platform-wide<span className="block type-caption text-fg-muted">No single organisation</span></span>],
            ["Requested role", <span key="r">{row.role?.label ?? r.roleId}<span className="block type-caption text-fg-muted">{row.role?.description} Illustrative role, not a platform permission.</span></span>],
            ["Requested duration", hoursLabel(r.requestedHours)],
            ["Requested at", fmtDate(r.requestedAt, true)],
            ["Requested start", fmtDate(r.requestedStart, true)],
            ["Requested end", fmtDate(addHours(r.requestedStart, r.requestedHours), true)],
          ]} />
        </Section>

        <Section title="Access justification">
          <blockquote className="rounded-card border-l-4 border-line-strong bg-surface-raised px-4 py-3 type-secondary text-fg-secondary">{r.justification}</blockquote>
        </Section>

        <Section title="Approval" aside={d?.local ? <LocalTag /> : undefined}>
          <DescriptionList columns={2} items={[
            ["Approval status", <span key="a" className="grid justify-items-start gap-1"><ApprovalBadge status={r.status} size="sm" labelled />
              <span className="type-caption text-fg-muted">{APPROVAL[r.status].meaning}</span></span>],
            ["Decided by", d ? d.by : <Missing key="b">No decision yet</Missing>],
            ["Decided at", d ? fmtDate(d.at, true) : <Missing key="t">Not decided</Missing>],
            ...(r.status === "APPROVED" ? [["Approved duration", <span key="h">{hoursLabel(d?.approvedHours ?? r.requestedHours)}
              {shortened && <span className="block type-caption text-fg-muted">Shortened from {hoursLabel(r.requestedHours)}</span>}</span>] as [string, ReactNode]] : []),
            ...(r.status === "REJECTED" ? [["Rejection reason", d?.reason ?? <Missing key="x">None recorded</Missing>] as [string, ReactNode]] : []),
          ]} />
        </Section>

        <Section title="Access and expiry" aside={s?.local || s?.revoked?.local || s?.extensions.some((e) => e.local) ? <LocalTag /> : undefined}>
          {!s ? (
            <p className="type-secondary text-fg-secondary">
              <AccessBadge status={row.access} size="sm" /> <span className="ml-1">{r.status === "PENDING" ? "Access can start only after the request is approved." : "The request was rejected, so no access was granted."}</span>
            </p>
          ) : (
            <DescriptionList columns={2} items={[
              ["Access status", <span key="a" className="grid justify-items-start gap-1"><AccessBadge status={row.access} size="sm" labelled />
                <span className="type-caption text-fg-muted">{ACCESS[row.access].meaning}</span></span>],
              ["Session", <code key="s" className="type-code">{s.id}</code>],
              ["Started", fmtDate(s.startedAt, true)],
              ["Expiry", fmtDate(s.expiresAt, true)],
              ["Remaining", row.remaining !== null
                ? <span key="m" className="font-semibold">{fmtMinutes(row.remaining)}<span className="block type-caption font-normal text-fg-muted">At the demonstration clock, {fmtDate(DEMO_NOW, true)}</span></span>
                : <Missing key="m">None: access is not in effect</Missing>],
              ["Total granted", hoursLabel(grantedHours(r, s))],
              ["Extensions", s.extensions.length
                ? <ul key="x" className="space-y-0.5">{s.extensions.map((e, i) => <li key={i}>+{hoursLabel(e.hours)} · {e.by} · {fmtDate(e.at, true)}</li>)}</ul>
                : <Missing key="x">None</Missing>],
              ...(s.revoked ? [["Revoked", <span key="v">{fmtDate(s.revoked.at, true)} · {s.revoked.by}
                <span className="block type-caption text-fg-muted">{s.revoked.reason ?? "No reason recorded."}</span></span>] as [string, ReactNode]] : []),
            ]} />
          )}
        </Section>

        <Section title="Activity history" aside={<span className="type-caption text-fg-muted">Demonstration history, oldest first</span>}>
          <Timeline label={`Activity history of ${r.id}`} items={timeline} />
        </Section>
      </div>
    </Drawer>
  );
}
