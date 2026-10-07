"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, ExceptionBadge, Notice, PageHeader, Table, Tabs, useAction } from "@/components/ui";

const NEXT: Record<string, string[]> = { OPEN: ["TRIAGED", "CANCELLED"], TRIAGED: ["IN_REMEDIATION", "CANCELLED"], IN_REMEDIATION: ["PENDING_VERIFICATION"], PENDING_VERIFICATION: ["CLOSED", "IN_REMEDIATION"] };

export default function Governance() {
  const [tab, setTab] = useState("Exceptions");
  return (
    <>
      <PageHeader spec="ZA-STA-001 §§12-15" title="Exceptions, compensation, issues & risk"
        subtitle="Governance treatments are recorded beside assurance state and never rewrite it. Approval needs a distinct, authorised human." />
      <Tabs tabs={["Exceptions", "Compensations", "Issues", "Risk acceptances"]} value={tab} onChange={setTab} />
      {tab === "Exceptions" && <Exceptions />}
      {tab === "Compensations" && <Compensations />}
      {tab === "Issues" && <Issues />}
      {tab === "Risk acceptances" && <Risks />}
    </>
  );
}

function Exceptions() {
  const { data, error, reload } = useApi<any[]>("/exceptions");
  const a = useAction();
  const decide = (id: string, decision: string) => a.run(async () => {
    const reason = decision === "APPROVE" ? null : prompt(`Reason to ${decision.toLowerCase()}?`) || null;
    await api(`/exceptions/${id}/decide`, { method: "POST", json: { decision, reason } }); reload();
  });
  return (
    <Card>
      <ErrorBox error={error || a.error} />
      <Table head={["Target", "Reason", "Validity", "State", "Requested by", "Approved by", ""]} empty={data?.length === 0}>
        {data?.map((x) => (
          <tr key={x.exception_id}>
            <td className="td">{x.target_type === "OBLIGATION" ? <A href={`/obligations/${x.target_id}`}>obligation {x.target_id.slice(0, 8)}…</A> : <span className="mono text-xs">{x.target_type} {x.target_id.slice(0, 8)}</span>}</td>
            <td className="td text-sm">{x.reason}</td>
            <td className="td text-xs">{fmtDate(x.valid_from)} → {fmtDate(x.valid_to)}</td>
            <td className="td"><ExceptionBadge state={x.state} /></td>
            <td className="td text-xs">{x.requested_by}</td><td className="td text-xs">{x.approved_by ?? "—"}</td>
            <td className="td whitespace-nowrap">
              {x.state === "PENDING_APPROVAL" && <><button className="btn-primary" onClick={() => decide(x.exception_id, "APPROVE")}>Approve</button> <button className="btn-ghost" onClick={() => decide(x.exception_id, "REJECT")}>Reject</button></>}
              {x.state === "APPROVED" && <button className="btn-danger" onClick={() => decide(x.exception_id, "REVOKE")}>Revoke</button>}
            </td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Compensations() {
  const { data, error, reload } = useApi<any[]>("/compensations");
  const a = useAction();
  const decide = (id: string, decision: string) => a.run(async () => { await api(`/compensations/${id}/decide`, { method: "POST", json: { decision } }); reload(); });
  return (
    <Card>
      <Notice>Approving a compensation creates a new versioned satisfaction expression with an explicit COMPENSATION branch. The trace then shows “satisfied via approved compensation”.</Notice>
      <ErrorBox error={error || a.error} />
      <div className="mt-3">
        <Table head={["Obligation", "Primary control", "Compensating expression", "Rationale", "Validity", "Status", ""]} empty={data?.length === 0}>
          {data?.map((c) => (
            <tr key={c.compensation_id}>
              <td className="td"><A href={`/obligations/${c.obligation_lineage_id}`}>{c.obligation_lineage_id.slice(0, 8)}…</A></td>
              <td className="td mono text-xs">{c.primary_control_key}</td>
              <td className="td mono text-[11px]">{c.compensating_expression.control_key ?? c.compensating_expression.children?.map((k: any) => k.control_key).join(" AND ")}</td>
              <td className="td max-w-sm text-xs">{c.equivalence_rationale}</td>
              <td className="td text-xs">{fmtDate(c.valid_from)} → {c.valid_to ? fmtDate(c.valid_to) : "open"}</td>
              <td className="td"><Badge code={c.status} /></td>
              <td className="td whitespace-nowrap">
                {c.status === "PENDING_APPROVAL" && <><button className="btn-primary" onClick={() => decide(c.compensation_id, "APPROVE")}>Approve</button> <button className="btn-ghost" onClick={() => decide(c.compensation_id, "REJECT")}>Reject</button></>}
                {c.status === "APPROVED" && <button className="btn-danger" onClick={() => decide(c.compensation_id, "REVOKE")}>Revoke</button>}
              </td>
            </tr>
          ))}
        </Table>
      </div>
    </Card>
  );
}

function Issues() {
  const { data, error, reload } = useApi<any[]>("/issues");
  const a = useAction();
  const move = (id: string, state: string) => a.run(async () => {
    const reason = state === "CANCELLED" ? prompt("Cancellation reason?") : null;
    await api(`/issues/${id}/transition`, { method: "POST", json: { state, reason } }); reload();
  });
  return (
    <Card subtitle="Closing an issue never changes assurance state. A new evaluation has to prove the remediation.">
      <ErrorBox error={error || a.error} />
      <Table head={["Issue", "Type", "Severity", "Priority", "State", "Source", "Created", "Move to"]} empty={data?.length === 0}>
        {data?.map((i) => (
          <tr key={i.issue_id}>
            <td className="td font-medium">{i.title}</td><td className="td mono text-xs">{i.issue_type}</td>
            <td className="td"><Badge code={i.severity === "CRITICAL" || i.severity === "HIGH" ? "FAIL" : "WARN"} label={i.severity} /></td>
            <td className="td">{i.priority}</td><td className="td"><Badge code={i.state} /></td>
            <td className="td mono text-[11px]">{i.source_ref_type} {i.source_ref_id?.slice(0, 8)}</td>
            <td className="td text-xs">{fmtDate(i.created_at)}</td>
            <td className="td whitespace-nowrap">{(NEXT[i.state] || []).map((s) => <button key={s} className="btn-ghost" onClick={() => move(i.issue_id, s)}>{s}</button>)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Risks() {
  const { data, error, reload } = useApi<any[]>("/risk-acceptances");
  const [f, setF] = useState({ scope_ref: "", residual_risk_statement: "", valid_from: new Date().toISOString().slice(0, 10), valid_to: "" });
  const a = useAction();
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
          <div><label className="label">Scope reference</label><input className="input" value={f.scope_ref} onChange={(e) => setF({ ...f, scope_ref: e.target.value })} /></div>
          <div><label className="label">Residual risk statement</label><textarea className="input" rows={3} value={f.residual_risk_statement} onChange={(e) => setF({ ...f, residual_risk_statement: e.target.value })} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="label">Valid from</label><input type="date" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></div>
            <div><label className="label">Valid to</label><input type="date" className="input" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></div>
          </div>
          <button className="btn-primary" disabled={a.busy || !f.scope_ref || !f.residual_risk_statement} onClick={() => a.run(async () => {
            await api("/risk-acceptances", { method: "POST", json: { ...f, valid_from: new Date(f.valid_from).toISOString(), valid_to: f.valid_to ? new Date(f.valid_to).toISOString() : null } });
            reload();
          })}>Accept risk</button>
        </div>
      </Card>
    </div>
  );
}
