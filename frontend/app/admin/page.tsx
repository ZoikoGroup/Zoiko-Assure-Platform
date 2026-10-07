"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Json, KV, Modal, PageHeader, Table, Tabs, useAction } from "@/components/ui";

export default function Admin() {
  const [tab, setTab] = useState("Identity & access");
  return (
    <>
      <PageHeader spec="ZA-ARCH-001 §§5,14 · ZA-GLS-001" title="Administration & trust"
        subtitle="Tenancy and cells, your effective permissions, the controlled dictionary, the transactional outbox and the audit trail." />
      <Tabs tabs={["Identity & access", "Tenancy & cells", "Controlled dictionary", "Outbox events", "Audit trail"]} value={tab} onChange={setTab} />
      {tab === "Identity & access" && <Me />}
      {tab === "Tenancy & cells" && <Tenancy />}
      {tab === "Controlled dictionary" && <Dictionary />}
      {tab === "Outbox events" && <Outbox />}
      {tab === "Audit trail" && <Audit />}
    </>
  );
}

function Me() {
  const { data, error } = useApi<any>("/me");
  return (
    <Card title="Current identity" subtitle="Signed-in session. Production federates sign-in through OIDC/SAML and provisions users via SCIM.">
      <ErrorBox error={error} />
      {data && (
        <>
          <KV cols={4} items={[["Actor", data.display_name], ["Type", <Badge key="t" code={data.actor_type} tone="info" />], ["Tenant", data.tenant_id ?? "— (content/platform staff: no tenant data)"], ["Authority basis", data.authority_basis]]} />
          <div className="label mt-4">Roles</div><div className="flex flex-wrap gap-1">{data.roles.map((r: string) => <Badge key={r} code={r} tone="info" />)}</div>
          <div className="label mt-4">Effective permissions</div><div className="flex flex-wrap gap-1">{data.permissions.map((p: string) => <span key={p} className="mono rounded bg-navy-50 px-1.5 py-0.5 text-[11px] text-navy-800">{p}</span>)}</div>
        </>
      )}
    </Card>
  );
}

function Tenancy() {
  const { data, error, reload } = useApi<any>("/org/tenant");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ legal_name: "", country: "", registration_ref: "", home_cell_id: "eu-west-cell-a" });
  const a = useAction();
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      {data && (
        <>
          <Card title={data.tenant?.legal_name} subtitle="Tenant" actions={<button className="btn-primary" onClick={() => setOpen(true)}>Register legal entity</button>}>
            <KV cols={4} items={Object.entries(data.tenant || {}).slice(0, 8).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])} />
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Legal entities">
              <Table head={["Entity", "Country", "Registration", "Home cell"]}>
                {data.entities.map((e: any) => <tr key={e.entity_id}><td className="td">{e.legal_name}<div className="mono text-[11px] text-slate-500">{e.entity_id}</div></td><td className="td mono">{e.country}</td><td className="td text-xs">{e.registration_ref ?? "—"}</td><td className="td mono text-xs">{e.home_cell_id}</td></tr>)}
              </Table>
            </Card>
            <Card title="Cells" subtitle="Tenant data is processed only in its home cell.">
              <Table head={["Cell", "Region", "Isolation", "Status"]}>
                {data.cells.map((c: any) => <tr key={c.cell_id}><td className="td mono text-xs">{c.cell_id}</td><td className="td">{c.region}</td><td className="td text-xs">{c.isolation_tier}</td><td className="td"><Badge code={c.status} /></td></tr>)}
              </Table>
            </Card>
          </div>
        </>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Register legal entity">
        <ErrorBox error={a.error} />
        <div className="grid gap-3 sm:grid-cols-2">
          {(["legal_name", "country", "registration_ref", "home_cell_id"] as const).map((k) => <div key={k}><label className="label">{k.replace(/_/g, " ")}</label><input className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>)}
        </div>
        <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!f.legal_name || !f.country || a.busy} onClick={() => a.run(async () => {
          await api("/org/entities", { method: "POST", json: { ...f, registration_ref: f.registration_ref || null } }); setOpen(false); reload();
        })}>Register</button></div>
      </Modal>
    </div>
  );
}

function Dictionary() {
  const { data, error } = useApi<any>("/dictionary");
  if (!data) return <ErrorBox error={error} />;
  const dims = data.dimensions || {};
  return (
    <div className="space-y-4">
      <Card title={`Dictionary ${data.dictionary_version}`} subtitle="Six separate state dimensions plus workflow vocabularies. There is deliberately no COMPLIANT state.">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(dims).map(([dim, vals]) => (
            <div key={dim} className="rounded-lg border border-navy-100 p-3">
              <div className="mono mb-2 text-xs font-semibold text-navy-800">{dim}</div>
              <ul className="space-y-1">
                {Object.entries(vals as Record<string, string>).map(([code, def]) => (
                  <li key={code} className="text-[11px]"><Badge code={code} /> <span className="text-slate-600">{def}</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      {data.reason_codes && <Card title="Reason codes"><Json value={{ reason_codes: data.reason_codes, ipe_reason_codes: data.ipe_reason_codes }} /></Card>}
    </div>
  );
}

function Outbox() {
  const { data, error } = useApi<any[]>("/outbox?limit=150");
  return (
    <Card subtitle="Domain events are written in the same transaction as the state change (transactional outbox).">
      <ErrorBox error={error} />
      <Table head={["Seq", "Event", "Aggregate", "Schema / dictionary", "Payload", "Available"]}>
        {data?.map((e) => (
          <tr key={e.event_id} className="align-top">
            <td className="td mono text-xs">#{e.record_seq}</td><td className="td mono text-xs font-semibold">{e.event_type}</td>
            <td className="td text-xs">{e.aggregate_type}<div className="mono text-[10px] text-slate-500">{e.aggregate_id?.slice(0, 13)}</div></td>
            <td className="td mono text-[10px]">{e.schema_version} / {e.dictionary_version}</td>
            <td className="td max-w-md"><pre className="mono whitespace-pre-wrap break-all text-[10px] text-slate-600">{JSON.stringify(e.payload)}</pre></td>
            <td className="td text-xs">{fmtDate(e.available_at, true)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Audit() {
  const { data, error } = useApi<any[]>("/audit?limit=200");
  return (
    <Card subtitle="Privileged and governance actions with prior and new state. Downloads are audited too.">
      <ErrorBox error={error} />
      <Table head={["Seq", "Action", "Actor", "Object", "Transition", "Reason", "At"]} empty={data?.length === 0}>
        {data?.map((e) => (
          <tr key={e.id}>
            <td className="td mono text-xs">#{e.record_seq ?? "—"}</td><td className="td mono text-xs">{e.action}</td>
            <td className="td text-xs">{e.actor_id}<div className="text-slate-500">{e.actor_type}</div></td>
            <td className="td text-xs">{e.object_type}<div className="mono text-[10px] text-slate-500">{e.object_id?.slice(0, 13)}</div></td>
            <td className="td text-xs">{e.prior_state ?? "∅"} → {e.new_state ?? "∅"}</td><td className="td text-xs">{e.reason ?? "—"}</td>
            <td className="td text-xs">{fmtDate(e.recorded_at, true)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
