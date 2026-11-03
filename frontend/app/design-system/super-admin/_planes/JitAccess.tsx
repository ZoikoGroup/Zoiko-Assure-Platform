"use client";

import { useEffect, useState } from "react";
import { Check, KeyRound, ShieldOff, X } from "lucide-react";
import {
  Alert, Button, Callout, Card, ConfirmDialog, DescriptionList, Field, Select, StateBadge, Switch, TabPanel, Tabs, Textarea, Timeline, useToast, type TimelineItem,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section, delay, useYou } from "../../_shared/demo";

/* Just-in-time access: a design proposal only. No JIT, break-glass or support-access model, endpoint or screen exists;
   they are deferred (docs/frontend-backend-build-plan.md, ZA-IAM-001). The states reuse the dictionary's approval
   vocabulary (PENDING_APPROVAL, APPROVED, REJECTED, REVOKED, EXPIRED) as a proposal. Nothing here changes access. */

type JitStatus = "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "REVOKED" | "EXPIRED";
type Req = { id: string; requester: string; role: string; scope: string; justification: string; hours: number; status: JitStatus; requested: string; expires?: string; log: TimelineItem[] };
const MIN = 60_000;

const START: Req[] = [
  { id: "JIT-DEMO-07", requester: "Sam Okafor", role: "Zoiko platform support", scope: "Read the registry record and administrative history of ten-demo-03", hours: 2, status: "PENDING_APPROVAL", requested: "2026-11-03T08:40:00Z",
    justification: "The customer reports that reactivation failed. I need the organisation's status history to find the cause.", log: [{ id: "r", title: "Requested", meta: "Sam Okafor · 3 Nov 2026, 08:40 UTC", status: "info" }] },
  { id: "JIT-DEMO-06", requester: "Lee Hart", role: "Zoiko platform support", scope: "Appoint an Organisation Admin for ten-demo-05", hours: 1, status: "EXPIRED", requested: "2026-11-02T14:00:00Z", expires: "2026-11-02T15:05:00Z",
    justification: "Their only Organisation Admin has left; the customer asked for a replacement (ticket ZS-4410).",
    log: [{ id: "r", title: "Requested", meta: "Lee Hart · 2 Nov 2026, 14:00 UTC", status: "info" }, { id: "a", title: "Approved for 1 hour", meta: "Platform security lead · 14:05 UTC", status: "verified" }, { id: "e", title: "Expired", meta: "15:05 UTC · access ended automatically", status: "unknown" }] },
  { id: "JIT-DEMO-05", requester: "Lee Hart", role: "Zoiko platform support", scope: "Change the status of any organisation", hours: 8, status: "REJECTED", requested: "2026-10-30T09:00:00Z",
    justification: "Routine maintenance window.", log: [{ id: "r", title: "Requested", meta: "Lee Hart · 30 Oct 2026, 09:00 UTC", status: "info" }, { id: "x", title: "Rejected", meta: "Platform security lead", description: "Reason: scope too broad; request a single organisation and a concrete task.", status: "critical" }] },
];

export function JitAccess() {
  return (
    <DemoFrame id="sa-jit" plane="platform" title="Just-in-time access"
      intro="Time-boxed, justified, approved elevation for platform staff: who asks, for exactly what, why, for how long, who decided, and when it ends."
      app={{ screen: "Not built · Privileged Access Log states it is not yet supported", action: "None: no endpoint exists", enforced: ["Nothing today: there is no JIT model to enforce", "Any implementation must grant, expire and revoke access on the server, never in the interface"] }}
      availability={{ level: "none", note: "No JIT, break-glass or support-access model, endpoint or screen exists. It is deferred (ZA-IAM-001; IAM-REQ-024 break-glass and IAM-REQ-030 support access are listed as not yet supported). This is a design proposal." }}
      empty={{ title: "No access requests", description: "Requests from platform staff would appear here." }}>
      <Body />
    </DemoFrame>
  );
}

function useNow(ms = 30_000) {
  const [t, setT] = useState(() => Date.now());
  useEffect(() => { const h = setInterval(() => setT(Date.now()), ms); return () => clearInterval(h); }, [ms]);
  return t;
}
const remaining = (iso: string, nowMs: number) => {
  const m = Math.max(0, Math.round((new Date(iso).getTime() - nowMs) / MIN));
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
};

function Body() {
  const toast = useToast();
  const you = useYou();
  const nowMs = useNow();
  const [reqs, setReqs] = useState<Req[]>(START);
  const [view, setView] = useState("open");
  const [sel, setSel] = useState(START[0].id);
  const [mine, setMine] = useState(false);
  const [hours, setHours] = useState(String(START[0].hours));
  const [pending, setPending] = useState<"approve" | "reject" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState(false);

  const status = (r: Req): JitStatus => (r.status === "APPROVED" && r.expires && new Date(r.expires).getTime() <= nowMs ? "EXPIRED" : r.status);
  const list = reqs.filter((r) => view === "all" || ["PENDING_APPROVAL", "APPROVED"].includes(status(r)));
  const r = reqs.find((x) => x.id === sel);
  const update = (id: string, f: (x: Req) => Req) => setReqs((rs) => rs.map((x) => (x.id === id ? f(x) : x)));
  const stamp = () => `${you.name} · ${fmtDate(new Date().toISOString(), true)}`;

  const decide = async () => {
    if (pending === "reject" && !reason.trim()) { setReasonError(true); throw new Error("Enter the reason for rejecting this request."); }
    await delay(700);
    if (pending === "approve") {
      const expires = new Date(Date.now() + Number(hours) * 60 * MIN).toISOString();
      update(r!.id, (x) => ({ ...x, status: "APPROVED", expires, log: [...x.log, { id: `a${x.log.length}`, title: `Approved for ${hours} hour${hours === "1" ? "" : "s"}`, meta: stamp(), description: `Expires ${fmtDate(expires, true)}`, status: "verified" }] }));
      toast("Approved (simulated: no access was granted)", "good");
    } else if (pending === "reject") {
      update(r!.id, (x) => ({ ...x, status: "REJECTED", log: [...x.log, { id: `x${x.log.length}`, title: "Rejected", meta: stamp(), description: `Reason: ${reason.trim()}`, status: "critical" }] }));
      toast("Rejected (simulated)", "info");
    } else {
      update(r!.id, (x) => ({ ...x, status: "REVOKED", log: [...x.log, { id: `v${x.log.length}`, title: "Revoked before expiry", meta: stamp(), description: reason.trim() ? `Reason: ${reason.trim()}` : undefined, status: "critical" }] }));
      toast("Revoked (simulated: nothing changed)", "info");
    }
    setReason(""); setReasonError(false);
  };

  return (
    <div className="space-y-stack">
      <Callout tone="warning" title="Design proposal: not built">
        Nothing on this screen grants, extends or removes access, and authorization is unchanged. In a real implementation the server would grant the access,
        end it at expiry and record every step; this interface would only ask and explain.
      </Callout>
      <div className="grid gap-stack xl:grid-cols-[20rem_minmax(0,1fr)]">
        <div className="space-y-3">
          <Tabs id="sa-jit-view" label="Show access requests" variant="pills" value={view} onChange={setView}
            items={[{ value: "open", label: "Open", count: reqs.filter((x) => ["PENDING_APPROVAL", "APPROVED"].includes(status(x))).length }, { value: "all", label: "All", count: reqs.length }]} />
          <TabPanel id="sa-jit-view" value={view} className="">
            <ul className="space-y-2">
              {list.map((x) => (
                <li key={x.id}>
                  <button type="button" aria-pressed={x.id === sel} onClick={() => { setSel(x.id); setHours(String(x.hours)); setMine(false); }}
                    className={`w-full rounded-card border p-3 text-left transition-colors ${x.id === sel ? "border-accent/60 bg-accent/10" : "border-line bg-surface-card/70 hover:border-line-strong"}`}>
                    <span className="flex items-center justify-between gap-2"><code className="type-code text-fg">{x.id}</code><StateBadge code={status(x)} size="sm" /></span>
                    <span className="mt-1 block type-caption text-fg-secondary">{x.requester} · {x.hours} h</span>
                    <span className="block truncate type-caption text-fg-muted">{x.scope}</span>
                  </button>
                </li>
              ))}
              {list.length === 0 && <li className="type-caption text-fg-muted">No open requests.</li>}
            </ul>
          </TabPanel>
        </div>

        {r && (
          <Card title={`Access request ${r.id}`} actions={<StateBadge code={status(r)} size="sm" />} bodyClassName="p-card space-y-5">
            <DescriptionList items={[
              ["Requester", <span key="q">{mine ? `${you.name} (you)` : r.requester}<span className="block type-caption text-fg-muted">{r.role}</span></span>],
              ["Requested", fmtDate(r.requested, true)], ["Requested access scope", r.scope], ["Requested duration", `${r.hours} hour${r.hours === 1 ? "" : "s"}`],
              ["Approval status", <StateBadge key="s" code={status(r)} size="sm" />],
              ["Access expiry", r.expires ? (status(r) === "APPROVED" ? `${fmtDate(r.expires, true)} · ${remaining(r.expires, nowMs)} left` : fmtDate(r.expires, true)) : "Not granted"],
            ]} />
            <Section title="Business justification">
              <blockquote className="rounded-card border-l-4 border-line-strong bg-surface-raised px-4 py-3 type-secondary text-fg-secondary">{r.justification}</blockquote>
            </Section>
            {status(r) === "PENDING_APPROVAL" && (
              <div className="space-y-3">
                <Switch label="Scenario: you are the requester" description="Shows the separation-of-duties rule." checked={mine} onChange={setMine} />
                {mine && <Alert tone="warning" title="Separation of duties">You cannot approve your own access request; another approver must. You can still withdraw it by rejecting.</Alert>}
                <div className="max-w-xs"><Field label="Approve for" hint="An approver may shorten the requested duration, never extend it.">
                  <Select value={hours} onChange={(e) => setHours(e.target.value)} options={[1, 2, 4, 8].filter((h) => h <= r.hours).map((h) => ({ value: String(h), label: `${h} hour${h === 1 ? "" : "s"}` }))} />
                </Field></div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" icon={Check} disabled={mine} onClick={() => setPending("approve")}>Approve</Button>
                  <Button variant="danger" icon={X} onClick={() => { setPending("reject"); setReason(""); setReasonError(false); }}>Reject</Button>
                </div>
              </div>
            )}
            {status(r) === "APPROVED" && <Button variant="danger" icon={ShieldOff} onClick={() => { setPending("revoke"); setReason(""); }}>Revoke now</Button>}
            <Section title="Access history"><Timeline label={`History of ${r.id}`} items={r.log} /></Section>
            <p className="type-caption text-fg-muted">
              Sample requests<Sample />. Every scope here stays in the Platform Control Plane. Access into a customer&apos;s workspace is outside this plane: the Super Admin holds no
              tenant-data access, and any such grant would need the organisation&apos;s own approval. It is not modelled here.
            </p>
          </Card>
        )}
      </div>

      {pending && r && (
        <ConfirmDialog title={pending === "approve" ? `Approve ${r.id} for ${hours} hour${hours === "1" ? "" : "s"}?` : pending === "reject" ? `Reject ${r.id}?` : `Revoke ${r.id} now?`}
          confirmLabel={pending === "approve" ? "Approve" : pending === "reject" ? "Reject" : "Revoke"} busyLabel="Recording…" tone={pending === "approve" ? "default" : "danger"}
          onClose={() => setPending(null)} onConfirm={decide}>
          <p>{r.requester}: {r.scope}.</p>
          {pending === "approve" && <p><KeyRound size={13} aria-hidden className="mr-1 inline" />Would end automatically at {fmtDate(new Date(Date.now() + Number(hours) * 60 * MIN).toISOString(), true)}.</p>}
          {pending !== "approve" && (
            <Field label="Reason" required={pending === "reject"} optional={pending === "revoke"} error={reasonError ? "Enter the reason for rejecting." : undefined}>
              <Textarea rows={3} value={reason} onChange={(e) => { setReason(e.target.value); if (e.target.value.trim()) setReasonError(false); }} />
            </Field>
          )}
          <p>Simulated: no access is granted or removed, and authorization is unchanged.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
