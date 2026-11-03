"use client";

import { useState } from "react";
import { ArrowRight, Ban } from "lucide-react";
import {
  Button, Callout, Card, ConfirmDialog, DescriptionList, Field, StateBadge, Textarea, Timeline, useToast, type TimelineItem,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { FlowFrame, Sample, Section, delay, now, useYou } from "../../_shared/demo";

/* Exception and issue management, mirroring Exceptions & Issues (frontend/app/governance/page.tsx →
   POST /issues/{id}/transition). The transition map is the application's own (identical to the backend ISSUE_FLOW);
   cancelling needs a reason, every move is confirmed, and CLOSED is final. Owner and due date are stored by the
   backend but no screen shows or sets them yet, so they appear here as samples. */

const NEXT: Record<string, string[]> = { OPEN: ["TRIAGED", "CANCELLED"], TRIAGED: ["IN_REMEDIATION", "CANCELLED"], IN_REMEDIATION: ["PENDING_VERIFICATION"], PENDING_VERIFICATION: ["CLOSED", "IN_REMEDIATION"] };
const TRACK = ["OPEN", "TRIAGED", "IN_REMEDIATION", "PENDING_VERIFICATION", "CLOSED"];
const VERB: Record<string, string> = { TRIAGED: "Triage", IN_REMEDIATION: "Start remediation", PENDING_VERIFICATION: "Submit for verification", CLOSED: "Close", CANCELLED: "Cancel issue" };

export function IssueFlow() {
  return (
    <FlowFrame id="issue" title="Exceptions and issues"
      intro="Work an issue from OPEN to CLOSED with its exception, evidence and history in view. Only the moves the workflow allows are offered; cancelling needs a reason, and closing is final."
      app={{ screen: "Exceptions & Issues › Issues", route: "/governance", action: "POST /issues/{id}/transition", enforced: ["Only the transitions in the issue workflow are accepted", "Cancelling without a reason is refused (422)", "CLOSED and CANCELLED are final"] }}
      empty={{ title: "No open issues", description: "Issues are raised automatically when an obligation fails or proof is missing." }}>
      <Work />
    </FlowFrame>
  );
}

function Work() {
  const toast = useToast();
  const you = useYou();
  const [state, setState] = useState("OPEN");
  const [target, setTarget] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState(false);
  const [log, setLog] = useState<TimelineItem[]>([
    { id: "open", title: <StateBadge code="OPEN" size="sm" />, meta: "Raised automatically · OBLIGATION_FAILING · 30 Oct 2026", description: "The payroll reconciliation control failed in the October snapshot." },
  ]);
  const final = state === "CLOSED" || state === "CANCELLED";
  const cancelling = target === "CANCELLED";

  const move = async () => {
    if (cancelling && !note.trim()) { setNoteError(true); throw new Error("Enter a cancellation reason. The server refuses a cancellation without one."); }
    await delay(700);
    const to = target!;
    setState(to);
    setLog((l) => [{ id: `${to}-${l.length}`, title: <StateBadge code={to} size="sm" />, meta: `${you.name} · ${fmtDate(now(), true)}`, description: note.trim() || undefined }, ...l]);
    toast(`Issue moved to ${to} (demo: nothing was recorded)`, to === "CLOSED" ? "good" : "info");
    setNote("");
    setNoteError(false);
  };

  return (
    <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Card bodyClassName="p-card space-y-6">
        <Section title="Issue" aside={<span className="flex items-center gap-2"><code className="rounded-chip border border-line px-1.5 type-code text-fg-secondary">HIGH · P1</code><StateBadge code={state} size="sm" /></span>}>
          <DescriptionList items={[
            ["Issue", <span key="i">ISS-DEMO-48 · Payroll reconciliation failing<Sample /></span>], ["Type", <code key="t" className="type-code">OBLIGATION_FAILING</code>],
            ["Severity · priority", "HIGH · P1"], ["Obligation state", <StateBadge key="o" code="FAILING" size="sm" />],
            ["Owner", <span key="w">Finance Operations<Sample /></span>], ["Due", <span key="d">{fmtDate("2026-11-14")}<Sample /></span>],
          ]} />
          <p className="type-caption text-fg-muted">The backend stores an owner and a due date for issues, but no screen shows or sets them yet.</p>
        </Section>

        <Section title="Status">
          <ol aria-label="Issue workflow" className="flex flex-wrap items-center gap-2">
            {TRACK.map((s, i) => {
              const reached = TRACK.indexOf(state) >= i && state !== "CANCELLED";
              return (
                <li key={s} className="flex items-center gap-2">
                  <span aria-current={s === state ? "step" : undefined}
                    className={`rounded-chip border px-2 py-1 type-code ${s === state ? "border-accent bg-accent/15 font-semibold text-accent-fg" : reached ? "border-line-strong text-fg" : "border-line text-fg-muted"}`}>{s}</span>
                  {i < TRACK.length - 1 && <ArrowRight size={13} aria-hidden className="text-fg-muted" />}
                </li>
              );
            })}
          </ol>
          {state === "CANCELLED" && <p className="type-caption text-fg-secondary">Cancelled: left the workflow before remediation.</p>}
        </Section>

        <Section title="Related exception">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-line bg-surface-raised px-4 py-3">
            <span className="min-w-0"><span className="block type-secondary font-medium text-fg">EXC-DEMO-12 · manual reconciliation while the ledger feed is rebuilt</span>
              <span className="type-caption text-fg-muted">{fmtDate("2026-10-15")} → {fmtDate("2026-11-30")}</span></span>
            <StateBadge code="PENDING_APPROVAL" size="sm" />
          </div>
          <p className="type-caption text-fg-muted">An exception sits beside the obligation's state; it does not close the issue or change the state.</p>
        </Section>

        <Section title="Supporting evidence">
          <ul className="divide-y divide-line rounded-card border border-line">
            {[["Failed test run: payroll-to-ledger reconciliation, 29 Oct", "FAIL"], ["Reconciliation report, September", "STALE"], ["Variance analysis worksheet", "CURRENT"]].map(([t, c]) => (
              <li key={t} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3"><span className="type-secondary text-fg">{t}</span><StateBadge code={c} size="sm" /></li>
            ))}
          </ul>
        </Section>
      </Card>

      <div className="space-y-stack">
        <Card title="Next step" bodyClassName="p-card space-y-3">
          {final ? (
            <Callout tone={state === "CLOSED" ? "verified" : "warning"} title={`${state} is final`}>No further transitions are allowed. {state === "CLOSED" ? "A recurrence is raised as a new issue." : ""}</Callout>
          ) : (NEXT[state] ?? []).map((to) => (
            <Button key={to} fullWidth variant={to === "CANCELLED" ? "danger" : to === "CLOSED" ? "primary" : "secondary"} icon={to === "CANCELLED" ? Ban : ArrowRight}
              onClick={() => { setTarget(to); setNote(""); setNoteError(false); }}>
              {VERB[to]} <span className="sr-only">(to {to})</span>
            </Button>
          ))}
        </Card>
        <Card title="Resolution details and history" bodyClassName="p-card"><Timeline label="Issue history, newest first" items={log} /></Card>
      </div>

      {target && (
        <ConfirmDialog title={`${VERB[target]}?`} confirmLabel={VERB[target]} busyLabel="Moving…" tone={cancelling ? "danger" : "default"}
          onClose={() => setTarget(null)} onConfirm={move}>
          <p>ISS-DEMO-48 moves from <code className="type-code">{state}</code> to <code className="type-code">{target}</code>.{target === "CLOSED" ? " Closing is final." : ""}</p>
          <Field label={cancelling ? "Cancellation reason" : target === "PENDING_VERIFICATION" || target === "CLOSED" ? "Resolution details" : "Note"} required={cancelling} optional={!cancelling}
            error={noteError ? "Enter the reason for cancelling." : undefined}>
            <Textarea rows={3} value={note} onChange={(e) => { setNote(e.target.value); if (e.target.value.trim()) setNoteError(false); }} />
          </Field>
        </ConfirmDialog>
      )}
    </div>
  );
}
