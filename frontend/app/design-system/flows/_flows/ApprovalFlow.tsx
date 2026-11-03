"use client";

import { useState } from "react";
import { Check, ShieldOff, UserRound, X } from "lucide-react";
import {
  Alert, Button, Callout, Card, ConfirmDialog, DescriptionList, Field, StateBadge, Switch, Textarea, Timeline, useToast, type TimelineItem,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { FlowFrame, Sample, Section, delay, now, useYou } from "../../_shared/demo";

/* Exception approval, mirroring Exceptions & Issues (frontend/app/governance/page.tsx → POST /exceptions/{id}/decide):
   PENDING_APPROVAL → APPROVED or REJECTED, APPROVED → REVOKED. The requester cannot approve their own exception (the
   server refuses with 403; this applies to APPROVE only). Approve is confirmed; reject and revoke take a reason, which
   the application treats as optional today. */

type Decision = "APPROVE" | "REJECT" | "REVOKE";
const RESULT: Record<Decision, string> = { APPROVE: "APPROVED", REJECT: "REJECTED", REVOKE: "REVOKED" };

export function ApprovalFlow() {
  return (
    <FlowFrame id="approval" title="Approval"
      intro="Decide on a time-bounded exception: who asked, why, for which obligation and period, and what supports it. An approved exception is shown beside the obligation's assurance state; it never changes that state."
      app={{ screen: "Exceptions & Issues › Exceptions", route: "/governance", action: "POST /exceptions/{id}/decide", permission: "approve_exception",
        enforced: ["Human accounts only", "The requester cannot approve their own exception (403)", "Only PENDING_APPROVAL can be approved or rejected; only APPROVED can be revoked (409 otherwise)"] }}
      empty={{ title: "No exceptions await approval", description: "Exception requests raised from obligation dossiers appear here." }}>
      <Decide />
    </FlowFrame>
  );
}

function Decide() {
  const toast = useToast();
  const you = useYou();
  const [state, setState] = useState("PENDING_APPROVAL");
  const [mine, setMine] = useState(false);
  const [pending, setPending] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [log, setLog] = useState<TimelineItem[]>([
    { id: "req", title: "Requested", meta: "Jordan Lee, Control Owner · 28 Oct 2026", description: "Reason recorded with the request (required).", status: "info" },
  ]);
  const requester = mine ? `${you.name} (you)` : "Jordan Lee, Control Owner";

  const decide = async () => {
    const d = pending!;
    await delay(800);
    const next = RESULT[d];
    setState(next);
    setLog((l) => [...l, { id: `${d}-${l.length}`, title: next, meta: `${you.name} · ${fmtDate(now(), true)}`, description: reason.trim() ? `Reason: ${reason.trim()}` : "No reason given", status: d === "APPROVE" ? "verified" : "critical" }]);
    toast(`${next} (demo: nothing was recorded)`, d === "APPROVE" ? "good" : "info");
    setReason("");
  };

  return (
    <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Card bodyClassName="p-card space-y-6">
        <Section title="Approval summary" aside={<StateBadge code={state} size="sm" />}>
          <DescriptionList items={[
            ["Exception", <span key="e">EXC-DEMO-31<Sample /></span>], ["Obligation", "Quarterly privileged-access review (SOX.ITGC.AR)"],
            ["Valid", `${fmtDate("2026-11-01")} → ${fmtDate("2026-12-01")} (time-bounded)`], ["Obligation state", <StateBadge key="s" code="NOT_PROVEN" size="sm" />],
          ]} />
        </Section>
        <Section title="Approval reason (from the requester)">
          <blockquote className="rounded-card border-l-4 border-line-strong bg-surface-raised px-4 py-3 type-secondary text-fg-secondary">
            The identity-provider migration delays the Q4 access review by four weeks. Weekly manual checks of privileged accounts continue until it completes.
          </blockquote>
        </Section>
        <Section title="Supporting context">
          <ul className="divide-y divide-line rounded-card border border-line">
            {[["Weekly manual privileged-account checks (4 logs)", "CURRENT"], ["Identity-provider migration plan v2", "CURRENT"], ["Q3 access review export", "STALE"]].map(([t, c]) => (
              <li key={t} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3"><span className="type-secondary text-fg">{t}</span><StateBadge code={c} size="sm" /></li>
            ))}
          </ul>
        </Section>
        <Section title="Decision record"><Timeline label="Exception decision history" items={log} /></Section>
      </Card>

      <div className="space-y-stack">
        <Card title="People" bodyClassName="p-card">
          <DescriptionList columns={1} items={[
            ["Requester", <span key="r" className="inline-flex items-center gap-1.5"><UserRound size={14} aria-hidden className="text-fg-muted" />{requester}</span>],
            ["Approver", <span key="a" className="inline-flex items-center gap-1.5"><UserRound size={14} aria-hidden className="text-fg-muted" />{you.name} (you) · {you.roles}</span>],
          ]} />
          <div className="mt-4"><Switch label="Scenario: you requested this exception" description="Shows the separation-of-duties rule." checked={mine} onChange={setMine} /></div>
        </Card>

        <Card title="Decide" bodyClassName="p-card space-y-3">
          {state === "PENDING_APPROVAL" && mine && (
            <Alert tone="warning" title="Separation of duties">You requested this exception, so you cannot approve it; another approver must. The server refuses with 403. Rejecting is still allowed.</Alert>
          )}
          {state === "PENDING_APPROVAL" && <>
            <Button variant="primary" icon={Check} fullWidth disabled={mine} onClick={() => setPending("APPROVE")}>Approve</Button>
            <Button variant="danger" icon={X} fullWidth onClick={() => setPending("REJECT")}>Reject</Button>
          </>}
          {state === "APPROVED" && <Button variant="danger" icon={ShieldOff} fullWidth onClick={() => setPending("REVOKE")}>Revoke</Button>}
          {(state === "REJECTED" || state === "REVOKED") && <Callout tone={state === "REJECTED" ? "critical" : "warning"} title={`This exception is ${state}`}>No further decision applies. A new request would be a new exception.</Callout>}
          {state === "APPROVED" && <Callout tone="verified" title="Approved (demonstration)">The exception is shown beside the obligation's state for its valid period. The state itself is unchanged.</Callout>}
        </Card>
      </div>

      {pending && (
        <ConfirmDialog title={pending === "APPROVE" ? "Approve this exception?" : pending === "REJECT" ? "Reject this exception?" : "Revoke this exception?"}
          confirmLabel={pending === "APPROVE" ? "Approve" : pending === "REJECT" ? "Reject" : "Revoke"} busyLabel="Recording…" tone={pending === "APPROVE" ? "default" : "danger"}
          onClose={() => { setPending(null); setReason(""); }} onConfirm={decide}>
          <p>EXC-DEMO-31 becomes <code className="type-code">{RESULT[pending]}</code>. Decided by {you.name}.</p>
          {pending !== "APPROVE" && (
            <Field label="Reason" optional hint="Optional in the application today. Making it mandatory would be a business-rule change, not made here.">
              <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
