"use client";

import { useState } from "react";
import { Check, Undo2, X } from "lucide-react";
import {
  Button, Callout, Card, ConfirmDialog, DataTable, DescriptionList, Field, StateBadge, TabPanel, Tabs, Textarea, Timeline, useToast, type DataColumn, type TimelineItem,
} from "@/components/ds";
import { fmtDate, fmtRange } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { DemoFrame, Sample, Section, delay, now, useYou } from "../../_shared/demo";
import { COMPENSATIONS, CONTROLS, EXCEPTIONS, OBLIGATIONS, type CompensationReq, type ExceptionReq } from "./data";
import { AreaLink, FlowLink, Proposed, SPLIT, useDetailPanel } from "./shared";

/* Human approvals, mirroring Exceptions & Issues (app/governance → POST /exceptions/{id}/decide,
   POST /compensations/{id}/decide). Exceptions: PENDING_APPROVAL → APPROVED or REJECTED, APPROVED → REVOKED; the
   requester cannot approve their own (403); a reason is optional. Compensations: approval needs Approve compensations
   and creates a new satisfaction-expression version; no proposer is recorded, so no self-approval check applies.
   The request date, requester role and supporting evidence are not recorded on an exception: proposed fields here. */

type Decision = "APPROVE" | "REJECT" | "REVOKE";
const RESULT: Record<Decision, ExceptionReq["state"]> = { APPROVE: "APPROVED", REJECT: "REJECTED", REVOKE: "REVOKED" };
const FILTERS = [{ value: "pending", label: "Awaiting decision" }, { value: "decided", label: "Decided" }, { value: "all", label: "All" }];
const titleOf = (key: string) => OBLIGATIONS.find((o) => o.key === key)?.title ?? CONTROLS.find((c) => c.key === key)?.title ?? key;

export function Approvals({ go }: { go: (id: string) => void }) {
  return (
    <DemoFrame id="ow-approvals" plane="workspace" title="Human approvals"
      intro="The queue of requests that need a person's decision: time-bounded exceptions and compensating controls. Each shows who asked, why, for what and for how long."
      app={{ screen: "Exceptions & Issues", route: "/governance", action: "POST /exceptions/{id}/decide · POST /compensations/{id}/decide", permission: "approve_exception",
        enforced: ["Human accounts only; agents and services cannot decide", "The requester cannot approve their own exception (403)", "Only PENDING_APPROVAL can be approved or rejected; only APPROVED can be revoked (409)", "An approved exception is shown beside the assurance state; it never changes it"] }}
      availability={{ level: "partial", note: "Exceptions and compensations are listed and decided today, with requester and approver shown as account IDs. Request date, requester role and supporting evidence are not recorded on an exception; they are proposed fields here." }}
      empty={{ title: "Nothing awaits approval", description: "Exception and compensation requests raised from obligation dossiers appear here." }}>
      <Body go={go} />
    </DemoFrame>
  );
}

function Body({ go }: { go: (id: string) => void }) {
  const toast = useToast();
  const you = useYou();
  const { can } = useCan();
  const [exceptions, setExceptions] = useState<ExceptionReq[]>(EXCEPTIONS);
  const [comps, setComps] = useState<CompensationReq[]>(COMPENSATIONS);
  const [kind, setKind] = useState("exceptions");
  const [filter, setFilter] = useState("pending");
  const [selected, setSelected] = useState(EXCEPTIONS[0].id);
  const [pending, setPending] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [log, setLog] = useState<Record<string, TimelineItem[]>>({});
  const { ref, reveal } = useDetailPanel();

  const keep = (state: string) => filter === "all" || (filter === "pending" ? state === "PENDING_APPROVAL" : state !== "PENDING_APPROVAL");
  const xs = exceptions.filter((x) => keep(x.state));
  const cs = comps.filter((c) => keep(c.status));
  const exc = exceptions.find((x) => x.id === selected);
  const comp = comps.find((c) => c.id === selected);
  const requester = (x: ExceptionReq) => (x.byYou ? `${you.name} (you)` : x.requestedBy);
  const choose = (id: string) => { setSelected(id); reveal(); };
  const switchKind = (k: string) => { setKind(k); setSelected(k === "exceptions" ? exceptions[0].id : comps[0].id); };

  const decide = async () => {
    await delay(800);
    const d = pending!;
    const note = reason.trim();
    const entry: TimelineItem = { id: `${selected}-${Date.now()}`, title: `${RESULT[d]}`, meta: `${you.name} (you) · ${fmtDate(now(), true)}`, description: note || "No reason given (optional)", status: d === "APPROVE" ? "verified" : "critical" };
    if (exc) setExceptions((all) => all.map((x) => (x.id === exc.id ? { ...x, state: RESULT[d], approvedBy: d === "APPROVE" ? `${you.name} (you)` : x.approvedBy } : x)));
    if (comp) setComps((all) => all.map((c) => (c.id === comp.id ? { ...c, status: RESULT[d], approvedBy: d === "APPROVE" ? `${you.name} (you)` : c.approvedBy } : c)));
    setLog((l) => ({ ...l, [selected]: [...(l[selected] ?? []), entry] }));
    toast(`${selected} ${RESULT[d]} (simulated: no decision was recorded)`, d === "APPROVE" ? "good" : "warn");
    setReason("");
  };

  const excColumns: DataColumn<ExceptionReq>[] = [
    { key: "target", header: "Request", sortValue: (x) => titleOf(x.target), render: (x) => <><span className="block font-medium text-fg">{titleOf(x.target)}</span><code className="type-code text-fg-muted">{x.id} · {x.targetType}</code></> },
    { key: "requester", header: "Requester", sortValue: (x) => x.proposed.requested, render: (x) => <><span className="block">{requester(x)}</span>
      <span className="type-caption text-fg-muted"><span className="whitespace-nowrap tabular-nums">{fmtDate(x.proposed.requested)}</span> <Proposed /></span></> },
    { key: "approver", header: "Approver", sortValue: (x) => x.approvedBy ?? "", render: (x) => x.approvedBy ?? <span className="text-fg-muted">—</span> },
    { key: "state", header: "Status", sortValue: (x) => x.state, render: (x) => <StateBadge code={x.state} size="sm" /> },
    { key: "valid", header: "Applies", sortValue: (x) => x.validFrom, render: (x) => <Span2 from={x.validFrom} to={x.validTo} /> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (x) => <ViewButton on={x.id === selected} label={x.id} onClick={() => choose(x.id)} /> },
  ];
  const compColumns: DataColumn<CompensationReq>[] = [
    { key: "obligation", header: "Compensation", sortValue: (c) => titleOf(c.obligation), render: (c) => <><span className="block font-medium text-fg">{titleOf(c.obligation)}</span><code className="type-code text-fg-muted">{c.id}</code></> },
    { key: "primary", header: "Primary → compensating", render: (c) => <><code className="block type-code">{c.primary}</code>{c.compensating.map((k) => <code key={k} className="block type-code text-fg-muted">→ {k}</code>)}</> },
    { key: "status", header: "Status", sortValue: (c) => c.status, render: (c) => <StateBadge code={c.status} size="sm" /> },
    { key: "valid", header: "Applies", sortValue: (c) => c.validFrom, render: (c) => <Span2 from={c.validFrom} to={c.validTo} /> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (c) => <ViewButton on={c.id === selected} label={c.id} onClick={() => choose(c.id)} /> },
  ];

  const state = exc?.state ?? comp?.status;
  const actions = state === "PENDING_APPROVAL" ? (["APPROVE", "REJECT"] as Decision[]) : state === "APPROVED" ? (["REVOKE"] as Decision[]) : [];
  const ownRequest = !!exc?.byYou;
  // Decisions already in the sample, then the ones made on this page.
  const earlier = (state: string, by?: string): TimelineItem[] => state === "PENDING_APPROVAL" ? []
    : [{ id: "earlier", title: state, meta: by ?? "Only an approval sets the approver; who rejected is in the audit trail", status: state === "APPROVED" ? "verified" : "critical" }];
  const origExc = EXCEPTIONS.find((x) => x.id === selected), origComp = COMPENSATIONS.find((c) => c.id === selected);
  const history: TimelineItem[] = exc && origExc ? [
    { id: "req", title: "Requested", meta: `${requester(exc)} · record #${exc.seq}`, description: "With a reason (required) and a fixed validity window.", status: "info" },
    ...earlier(origExc.state, origExc.approvedBy), ...(log[exc.id] ?? []),
  ] : comp && origComp ? [
    { id: "prop", title: "Proposed", meta: `No proposer recorded · record #${comp.seq}`, description: "With an equivalence rationale (at least 20 characters).", status: "info" },
    ...earlier(origComp.status, origComp.approvedBy), ...(log[comp.id] ?? []),
  ] : [];

  return (
    <div className="space-y-stack">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs id="ow-apr-kind" label="Request type" value={kind} onChange={switchKind}
          items={[{ value: "exceptions", label: "Exceptions", count: exceptions.filter((x) => x.state === "PENDING_APPROVAL").length }, { value: "compensations", label: "Compensations", count: comps.filter((c) => c.status === "PENDING_APPROVAL").length }]} />
        <FlowLink flow="approval">Full Approval flow</FlowLink>
      </div>
      <TabPanel id="ow-apr-kind" value={kind} className="">
        <div className={SPLIT}>
          <div className="min-w-0 space-y-3">
            <Tabs id="ow-apr-filter" label="Filter by decision" variant="pills" value={filter} onChange={setFilter} items={FILTERS.map((f) => ({ ...f, count: (kind === "exceptions" ? exceptions.map((x) => x.state) : comps.map((c) => c.status)).filter((s) => f.value === "all" || (f.value === "pending" ? s === "PENDING_APPROVAL" : s !== "PENDING_APPROVAL")).length }))} />
            <TabPanel id="ow-apr-filter" value={filter} className="">
              {kind === "exceptions"
                ? <DataTable caption={`Exception requests (sample): ${xs.length} shown`} columns={excColumns} rows={xs} rowKey={(x) => x.id} defaultSort={{ key: "requester", dir: "desc" }} minWidth="44rem"
                    empty={{ title: filter === "pending" ? "No exceptions await a decision" : "No exceptions here", description: "Change the filter to see the others." }} />
                : <DataTable caption={`Compensation requests (sample): ${cs.length} shown`} columns={compColumns} rows={cs} rowKey={(c) => c.id} defaultSort={{ key: "valid", dir: "desc" }} minWidth="44rem"
                    empty={{ title: filter === "pending" ? "No compensations await a decision" : "No compensations here", description: "Change the filter to see the others." }} />}
            </TabPanel>
            <p className="type-caption text-fg-muted">The application shows requester and approver as account IDs; names here are samples<Sample />.</p>
          </div>

          <div ref={ref} className="scroll-mt-6">
            {exc || comp ? (
              <Card title={exc ? titleOf(exc.target) : titleOf(comp!.obligation)} description={<code className="type-code">{selected} · {exc ? "exception" : "compensation"}</code>} actions={<StateBadge code={state!} size="sm" />} bodyClassName="p-card space-y-5">
                {exc ? <DescriptionList columns={1} items={[
                  ["Target", <span key="t" className="space-x-1.5"><code className="type-code text-fg-muted">{exc.targetType}</code><AreaLink id={exc.target} area={exc.targetType === "OBLIGATION" ? "obligations" : "controls"} go={go} /></span>],
                  ["Requester", requester(exc)], ["Requester role", <span key="r">{exc.byYou ? you.roles : exc.proposed.role}<Proposed /></span>],
                  ["Approver", exc.approvedBy ?? "Not yet decided"], ["Requested", <span key="d">{fmtDate(exc.proposed.requested)}<Proposed /></span>],
                  ["Applies", `${fmtRange([exc.validFrom, exc.validTo])} (time-bounded)`],
                ]} /> : <DescriptionList columns={1} items={[
                  ["Obligation", <AreaLink key="o" id={comp!.obligation} area="obligations" go={go} />], ["Primary control", <AreaLink key="p" id={comp!.primary} area="controls" go={go} />],
                  ["Compensating controls", <span key="c" className="flex flex-wrap gap-2">{comp!.compensating.map((k) => <AreaLink key={k} id={k} area="controls" go={go} />)}</span>],
                  ["Proposed by", "Not recorded"], ["Approver", comp!.approvedBy ?? "Not yet decided"], ["Applies", fmtRange([comp!.validFrom, comp!.validTo ?? null])],
                ]} />}
                <Section title={exc ? "Justification" : "Equivalence rationale"}><p className="type-secondary text-fg">{exc ? exc.reason : comp!.rationale}</p></Section>
                {exc && <Section title="Supporting evidence" aside={<Proposed />}>
                  {exc.proposed.evidence.length ? <ul className="flex flex-wrap gap-2">{exc.proposed.evidence.map((e) => <li key={e}><AreaLink id={e} area="evidence" go={go} label={`Open evidence ${e}`} /></li>)}</ul>
                    : <p className="type-secondary text-fg-muted">None attached.</p>}
                </Section>}
                <Section title="Decision history"><Timeline label={`Decisions on ${selected}`} items={history} /></Section>
                {ownRequest && state === "PENDING_APPROVAL" && (
                  <Callout tone="warning" title="You requested this exception">You cannot approve your own request; the server refuses it (403). You can still reject it, or ask another approver.</Callout>
                )}
                {comp && !can("approve_compensation") && actions.length > 0 && (
                  <Callout tone="warning" title="Your role cannot decide compensations">Deciding needs Approve compensations; the server would refuse. The buttons stay available so you can see the confirmation.</Callout>
                )}
                {actions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {actions.includes("APPROVE") && <Button variant="primary" icon={Check} disabled={ownRequest} onClick={() => { setPending("APPROVE"); setReason(""); }}>Approve</Button>}
                    {actions.includes("REJECT") && <Button variant="danger" icon={X} onClick={() => { setPending("REJECT"); setReason(""); }}>Reject</Button>}
                    {actions.includes("REVOKE") && <Button variant="danger" icon={Undo2} onClick={() => { setPending("REVOKE"); setReason(""); }}>Revoke</Button>}
                  </div>
                )}
                <p className="type-caption text-fg-muted">Sample request<Sample />. Deciding here records nothing.</p>
              </Card>
            ) : <Card><p className="type-secondary text-fg-muted">Choose a request to see its justification and decide it.</p></Card>}
          </div>
        </div>
      </TabPanel>

      {pending && (
        <ConfirmDialog title={`${pending === "APPROVE" ? "Approve" : pending === "REJECT" ? "Reject" : "Revoke"} ${selected}?`} confirmLabel={pending === "APPROVE" ? "Approve" : pending === "REJECT" ? "Reject" : "Revoke"}
          busyLabel="Recording…" tone={pending === "APPROVE" ? "default" : "danger"} onClose={() => setPending(null)} onConfirm={decide}>
          <p>{pending === "APPROVE"
            ? exc ? `The exception applies from ${fmtDate(exc.validFrom)} to ${fmtDate(exc.validTo)}. It is shown beside the assurance state and never changes it.` : "Approval creates a new version of the obligation's satisfaction expression with this compensation; it is versioned, never a manual pass."
            : pending === "REJECT" ? "The request is closed without effect." : "The approval is withdrawn before it expires."}</p>
          <Field label="Reason" optional hint="Recorded with the decision. The application treats it as optional today.">
            <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <p>Simulated: no decision is recorded and the server is not called.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

/** A validity window on two lines, so the column stays narrow. */
const Span2 = ({ from, to }: { from: string; to?: string }) => (
  <span className="tabular-nums"><span className="block whitespace-nowrap">{fmtDate(from)}</span><span className="block whitespace-nowrap type-caption text-fg-muted">→ {to ? fmtDate(to) : "open"}</span></span>
);

const ViewButton = ({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) => (
  <Button size="sm" variant={on ? "secondary" : "ghost"} aria-pressed={on} onClick={onClick}>{on ? "Viewing" : "View"}<span className="sr-only"> {label}</span></Button>
);
