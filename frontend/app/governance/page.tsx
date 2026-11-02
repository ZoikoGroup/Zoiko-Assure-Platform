"use client";

import { type ReactNode, useId, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ConfirmDialog, ErrorBox, ExceptionBadge, Modal, Notice, PageHeader, PermissionNote, ReasonField, Table, TabPanel, Tabs, useAction } from "@/components/ui";
import { CompensationSoDNote } from "@/components/CompensationSoD";

const NEXT: Record<string, string[]> = { OPEN: ["TRIAGED", "CANCELLED"], TRIAGED: ["IN_REMEDIATION", "CANCELLED"], IN_REMEDIATION: ["PENDING_VERIFICATION"], PENDING_VERIFICATION: ["CLOSED", "IN_REMEDIATION"] };

export default function Governance() {
  const [tab, setTab] = useState("Exceptions");
  const tabsId = useId();
  return (
    <>
      <PageHeader spec="WF-STA-04 · ZA-STA-001 §§12-15" title="Exceptions & Issues"
        subtitle="Governance treatments are recorded beside assurance state and never rewrite it. Approval needs an authorised human, and an exception cannot be approved by the person who requested it." />
      <PermissionNote perms={["approve_exception", "approve_compensation"]} />
      <Tabs id={tabsId} tabs={["Exceptions", "Compensations", "Issues", "Risk acceptances"]} value={tab} onChange={setTab} />
      <TabPanel id={tabsId} value={tab}>
        {tab === "Exceptions" && <Exceptions />}
        {tab === "Compensations" && <Compensations />}
        {tab === "Issues" && <Issues />}
        {tab === "Risk acceptances" && <Risks />}
      </TabPanel>
    </>
  );
}

function Exceptions() {
  const { data, error, reload } = useApi<any[]>("/exceptions");
  const { can, why } = useCan();
  const [approveFor, setApproveFor] = useState<any>(null);
  const [pending, setPending] = useState<{ x: any; decision: "REJECT" | "REVOKE" } | null>(null);
  const send = async (id: string, decision: string, reason: string | null) => {
    await api(`/exceptions/${id}/decide`, { method: "POST", json: { decision, reason } }); reload();
  };
  const about = (x: any) => (
    <p>Exception on {x.target_type.toLowerCase()} <span className="mono text-xs">{x.target_id.slice(0, 8)}…</span>, valid {fmtDate(x.valid_from)} → {fmtDate(x.valid_to)}. Requested because: &ldquo;{x.reason}&rdquo;</p>
  );
  return (
    <Card>
      <ErrorBox error={error} />
      <Table head={["Target", "Reason", "Validity", "State", "Requested by", "Approved by", ""]} empty={data?.length === 0}>
        {data?.map((x) => (
          <tr key={x.exception_id}>
            <td className="td">{x.target_type === "OBLIGATION" ? <A href={`/obligations/${x.target_id}`}>obligation {x.target_id.slice(0, 8)}…</A> : <span className="mono text-xs">{x.target_type} {x.target_id.slice(0, 8)}</span>}</td>
            <td className="td text-sm">{x.reason}</td>
            <td className="td text-xs">{fmtDate(x.valid_from)} → {fmtDate(x.valid_to)}</td>
            <td className="td"><ExceptionBadge state={x.state} /></td>
            <td className="td text-xs">{x.requested_by}</td><td className="td text-xs">{x.approved_by ?? "—"}</td>
            <td className="td whitespace-nowrap">
              {x.state === "PENDING_APPROVAL" && <><button className="btn-primary" disabled={!can("approve_exception")} title={why("approve_exception")} onClick={() => setApproveFor(x)}>Approve</button> <button className="btn-ghost" disabled={!can("approve_exception")} title={why("approve_exception")} onClick={() => setPending({ x, decision: "REJECT" })}>Reject</button></>}
              {x.state === "APPROVED" && <button className="btn-danger" disabled={!can("approve_exception")} title={why("approve_exception")} onClick={() => setPending({ x, decision: "REVOKE" })}>Revoke</button>}
            </td>
          </tr>
        ))}
      </Table>
      {approveFor && (
        <ConfirmDialog title="Approve exception request" confirm="Approve exception" busyLabel="Approving…"
          onConfirm={() => send(approveFor.exception_id, "APPROVE", null)} onClose={() => setApproveFor(null)}>
          {about(approveFor)}
          <p>The request moves from PENDING_APPROVAL to APPROVED and the approval is recorded under your identity in the audit trail. An approved exception is a governance treatment shown beside the assurance state; it never changes that state. You cannot approve an exception you requested yourself.</p>
        </ConfirmDialog>
      )}
      {pending && (
        <ReasonDialog title={pending.decision === "REJECT" ? "Reject exception request" : "Revoke approved exception"} label="Reason" required={false}
          confirm={pending.decision === "REJECT" ? "Reject exception" : "Revoke exception"}
          onSubmit={(reason) => send(pending.x.exception_id, pending.decision, reason)} onClose={() => setPending(null)}>
          {about(pending.x)}
          <p>{pending.decision === "REJECT" ? "The request moves from PENDING_APPROVAL to REJECTED." : "The exception moves from APPROVED to REVOKED."} This cannot be undone. Exception decisions never change assurance state. Any reason you give is recorded in the audit trail.</p>
        </ReasonDialog>
      )}
    </Card>
  );
}

function Compensations() {
  const { data, error, reload } = useApi<any[]>("/compensations");
  const a = useAction();
  const { can, why } = useCan();
  const [pending, setPending] = useState<{ c: any; decision: "REJECT" | "REVOKE" } | null>(null);
  const name = useActorNames();
  const sodId = useId();
  const send = async (id: string, decision: string) => { await api(`/compensations/${id}/decide`, { method: "POST", json: { decision } }); reload(); };
  const decide = (id: string, decision: string) => a.run(() => send(id, decision));
  const compensating = (c: any) => c.compensating_expression.control_key ?? c.compensating_expression.children?.map((k: any) => k.control_key).join(" AND ");
  return (
    <Card>
      <Notice>Approving a compensation creates a new versioned satisfaction expression with an explicit COMPENSATION branch. The trace then shows “satisfied via approved compensation”.</Notice>
      <div className="mt-3"><CompensationSoDNote id={sodId} stage="approve" /></div>
      <ErrorBox error={error || a.error} />
      <div className="mt-3">
        <Table head={["Obligation", "Primary control", "Compensating expression", "Rationale", "Validity", "Status", "Proposed / approved by", ""]} empty={data?.length === 0}>
          {data?.map((c) => (
            <tr key={c.compensation_id}>
              <td className="td"><A href={`/obligations/${c.obligation_lineage_id}`}>{c.obligation_lineage_id.slice(0, 8)}…</A></td>
              <td className="td mono text-xs">{c.primary_control_key}</td>
              <td className="td mono text-[11px]">{compensating(c)}</td>
              <td className="td max-w-sm text-xs">{c.equivalence_rationale}</td>
              <td className="td text-xs">{fmtDate(c.valid_from)} → {c.valid_to ? fmtDate(c.valid_to) : "open"}</td>
              <td className="td"><Badge code={c.status} /></td>
              <td className="td text-xs"><span className="text-slate-500">not recorded</span> / {c.approved_by ? name(c.approved_by) : "—"}</td>
              <td className="td whitespace-nowrap">
                {c.status === "PENDING_APPROVAL" && <><button className="btn-primary" disabled={!can("approve_compensation")} title={why("approve_compensation")} aria-describedby={sodId} onClick={() => decide(c.compensation_id, "APPROVE")}>Approve</button> <button className="btn-ghost" disabled={!can("approve_compensation")} title={why("approve_compensation")} onClick={() => setPending({ c, decision: "REJECT" })}>Reject</button></>}
                {c.status === "APPROVED" && <button className="btn-danger" disabled={!can("approve_compensation")} title={why("approve_compensation")} onClick={() => setPending({ c, decision: "REVOKE" })}>Revoke</button>}
              </td>
            </tr>
          ))}
        </Table>
      </div>
      {pending && (
        <ConfirmDialog danger title={pending.decision === "REJECT" ? "Reject compensation proposal" : "Revoke approved compensation"}
          confirm={pending.decision === "REJECT" ? "Reject compensation" : "Revoke compensation"} busyLabel={pending.decision === "REJECT" ? "Rejecting…" : "Revoking…"}
          onConfirm={() => send(pending.c.compensation_id, pending.decision)} onClose={() => setPending(null)}>
          <p>Compensation for obligation <span className="mono text-xs">{pending.c.obligation_lineage_id.slice(0, 8)}…</span>: primary control <span className="mono text-xs">{pending.c.primary_control_key}</span>,
            compensating <span className="mono text-xs">{compensating(pending.c)}</span>, valid {fmtDate(pending.c.valid_from)} → {pending.c.valid_to ? fmtDate(pending.c.valid_to) : "open"}.</p>
          <p>{pending.decision === "REJECT"
            ? "The proposal moves from PENDING_APPROVAL to REJECTED. The obligation's satisfaction expression is not changed."
            : "The compensation moves from APPROVED to REVOKED. Later assurance evaluations no longer accept its compensation branch; snapshots already frozen are not changed."}
            {" "}This cannot be undone here; a new compensation would have to be proposed.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}

function Issues() {
  const { data, error, reload } = useApi<any[]>("/issues");
  const [cancelFor, setCancelFor] = useState<any>(null);
  const [moveFor, setMoveFor] = useState<{ i: any; state: string } | null>(null);
  const send = async (id: string, state: string, reason: string | null) => {
    await api(`/issues/${id}/transition`, { method: "POST", json: { state, reason } }); reload();
  };
  const move = (i: any, state: string) => (state === "CANCELLED" ? setCancelFor(i) : setMoveFor({ i, state }));
  return (
    <Card subtitle="Closing an issue never changes assurance state. A new evaluation has to prove the remediation.">
      <ErrorBox error={error} />
      <Table head={["Issue", "Type", "Severity", "Priority", "State", "Source", "Created", "Move to"]} empty={data?.length === 0}>
        {data?.map((i) => (
          <tr key={i.issue_id}>
            <td className="td font-medium">{i.title}</td><td className="td mono text-xs">{i.issue_type}</td>
            <td className="td"><Badge code={i.severity === "CRITICAL" || i.severity === "HIGH" ? "FAIL" : "WARN"} label={i.severity} /></td>
            <td className="td">{i.priority}</td><td className="td"><Badge code={i.state} /></td>
            <td className="td mono text-[11px]">{i.source_ref_type} {i.source_ref_id?.slice(0, 8)}</td>
            <td className="td text-xs">{fmtDate(i.created_at)}</td>
            <td className="td whitespace-nowrap">{(NEXT[i.state] || []).map((s) => <button key={s} className="btn-ghost" onClick={() => move(i, s)}>{s}</button>)}</td>
          </tr>
        ))}
      </Table>
      {moveFor && (
        <ConfirmDialog title={`Move issue to ${moveFor.state}`} confirm={`Move to ${moveFor.state}`} busyLabel="Moving…"
          onConfirm={() => send(moveFor.i.issue_id, moveFor.state, null)} onClose={() => setMoveFor(null)}>
          <p><span className="font-medium">{moveFor.i.title}</span> · <Badge code={moveFor.i.state} /> → <Badge code={moveFor.state} /></p>
          <p>{moveFor.state === "CLOSED"
            ? "Closing is final: a closed issue cannot be moved to another state. Closing does not change assurance state; a new evaluation has to prove the remediation."
            : "Moving an issue does not change assurance state."} The transition is recorded in the audit trail.</p>
        </ConfirmDialog>
      )}
      {cancelFor && (
        <ReasonDialog title="Cancel issue" label="Cancellation reason" required confirm="Cancel issue" dismiss="Keep issue"
          onSubmit={(reason) => send(cancelFor.issue_id, "CANCELLED", reason)} onClose={() => setCancelFor(null)}>
          <p><span className="font-medium">{cancelFor.title}</span> · currently <Badge code={cancelFor.state} /></p>
          <p>The issue moves to CANCELLED. This cannot be undone. Cancelling an issue never changes assurance state. The reason is recorded in the audit trail.</p>
        </ReasonDialog>
      )}
    </Card>
  );
}

/* Collects the reason for a governed decision in place of window.prompt. Cancel, Escape and the close button submit
   nothing; whitespace-only text counts as no reason. */
function ReasonDialog({ title, children, label, required, confirm, dismiss = "Cancel", onSubmit, onClose }: {
  title: string; children: ReactNode; label: string; required: boolean; confirm: string; dismiss?: string;
  onSubmit: (reason: string | null) => Promise<void>; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const a = useAction();
  const id = useId();
  const text = reason.trim();
  const invalid = required && !text;
  const submit = () => {
    setTried(true);
    if (invalid) { field.current?.focus(); return; }
    a.run(async () => { await onSubmit(text || null); onClose(); });
  };
  return (
    <Modal open onClose={onClose} title={title} size="sm">
      <div className="space-y-2 text-[13px] text-slate-700">{children}</div>
      <ErrorBox error={a.error} />
      <ReasonField className="mt-3" id={id} label={`${label} (${required ? "required" : "optional"})`} required={required} rows={3}
        value={reason} onChange={setReason} showError={tried && invalid} inputRef={field} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>{dismiss}</button>
        <button className="btn-danger" disabled={a.busy} onClick={submit}>{a.busy ? "Submitting…" : confirm}</button>
      </div>
    </Modal>
  );
}

function Risks() {
  const { data, error, reload } = useApi<any[]>("/risk-acceptances");
  const [f, setF] = useState({ scope_ref: "", residual_risk_statement: "", valid_from: new Date().toISOString().slice(0, 10), valid_to: "" });
  const a = useAction();
  const { can, why } = useCan();
  const fid = useId();
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Risk acceptances">
        <ErrorBox error={error} />
        <Table head={["Scope", "Residual risk", "Approver", "Validity"]} empty={data?.length === 0}>
          {data?.map((r) => (
            <tr key={r.risk_acceptance_id}><td className="td mono text-xs">{r.scope_ref}</td><td className="td text-sm">{r.residual_risk_statement}</td>
              <td className="td text-xs">{r.approver_id}</td><td className="td text-xs">{fmtDate(r.valid_from)} → {r.valid_to ? fmtDate(r.valid_to) : "open"}</td></tr>
          ))}
        </Table>
      </Card>
      <Card title="Record risk acceptance" subtitle="A human, authorised act. It does not change any assurance state.">
        <ErrorBox error={a.error} />
        <div className="grid gap-3">
          <div><label className="label" htmlFor={`${fid}-scope`}>Scope reference (required)</label><input id={`${fid}-scope`} required className="input" value={f.scope_ref} onChange={(e) => setF({ ...f, scope_ref: e.target.value })} /></div>
          <div><label className="label" htmlFor={`${fid}-risk`}>Residual risk statement (required)</label><textarea id={`${fid}-risk`} required className="input" rows={3} value={f.residual_risk_statement} onChange={(e) => setF({ ...f, residual_risk_statement: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label" htmlFor={`${fid}-from`}>Valid from</label><input id={`${fid}-from`} type="date" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></div>
            <div><label className="label" htmlFor={`${fid}-to`}>Valid to</label><input id={`${fid}-to`} type="date" className="input" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></div>
          </div>
          <button className="btn-primary" disabled={a.busy || !can("approve_exception") || !f.scope_ref || !f.residual_risk_statement} title={why("approve_exception")} onClick={() => a.run(async () => {
            await api("/risk-acceptances", { method: "POST", json: { ...f, valid_from: new Date(f.valid_from).toISOString(), valid_to: f.valid_to ? new Date(f.valid_to).toISOString() : null } });
            reload();
          })}>Accept risk</button>
        </div>
      </Card>
    </div>
  );
}
