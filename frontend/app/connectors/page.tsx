"use client";

import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, KV, LoadingSkeleton, Modal, Notice, PageHeader, PermissionNote, Table, TabPanel, Tabs, useAction } from "@/components/ui";

export default function Connectors() {
  const [tab, setTab] = useState("Connector registry");
  // The outbox holds tenant events; content/platform staff have no standing tenant access (INV-14).
  const tenantUser = !!useAuth().me?.tenant_id;
  const tabsId = useId();
  return (
    <>
      <PageHeader spec="FR-CON-001..004 · INV-11 · WF-EVT-03" title="Connectors"
        subtitle="Inbound source connectors and their certification, and the outbound event outbox. Customer-visible connector status is read only from the certification registry." />
      <PermissionNote perms={["manage_connectors"]} />
      {tenantUser ? (
        <>
          <Tabs id={tabsId} tabs={["Connector registry", "Event outbox"]} value={tab} onChange={setTab} />
          <TabPanel id={tabsId} value={tab}>
            {tab === "Connector registry" && <Registry />}
            {tab === "Event outbox" && <Outbox />}
          </TabPanel>
        </>
      ) : <Registry />}
    </>
  );
}

function Registry() {
  const { data, error, reload } = useApi<any[]>("/connectors");
  const [cert, setCert] = useState<any>(null);
  // The certification registry is shared by every organisation; only Zoiko certification staff may change it.
  const { can, why } = useCan();
  return (
    <>
      <p className="mb-3 text-[13px] text-slate-500">Release availability, certification and runtime health are separate dimensions.</p>
      <ErrorBox error={error} />
      <div className="space-y-4">
        {!data && !error && <Card><LoadingSkeleton rows={4} /></Card>}
        {data?.map((c) => (
          <Card key={c.connector_id} title={c.name} subtitle={`${c.provider} · ${c.source_class} · build ${c.build_version}`}
            actions={<><Badge code={c.release_availability} /><Badge code={c.certification} /><Badge code={c.runtime_health} />
              <button className="btn-ghost" disabled={!can("certify_connectors")} title={why("certify_connectors")} onClick={() => setCert(c)}>Certification</button></>}>
            <div className="grid gap-4 lg:grid-cols-3">
              <div>
                <div className="label">Certification matrix</div>
                <ul className="mt-1 space-y-1">
                  {Object.entries(c.certification_matrix || {}).map(([k, v]) => <li key={k} className="flex justify-between text-xs"><span className="mono">{k}</span><Badge code={v as string} /></li>)}
                  {!Object.keys(c.certification_matrix || {}).length && <li className="text-xs text-slate-500">Not assessed</li>}
                </ul>
              </div>
              <div>
                <KV cols={1} items={[["Delivery model", c.delivery_model], ["Supported objects", (c.supported_objects || []).join(", ")],
                  ["Extraction contracts", c.extraction_contracts.map((x: any) => `${x.source_object} v${x.version}`).join(", ") || "—"],
                  ["Known limitations", (c.known_limitations || []).join("; ") || "—"]]} />
              </div>
              <div>
                <div className="label">Installations</div>
                {(c.installations || []).map((i: any) => (
                  <div key={i.installation_id} className="mt-1 rounded border border-navy-100 p-2 text-xs">
                    <div className="flex items-center justify-between"><span className="mono">{i.entity_id}</span><Badge code={i.freshness} /></div>
                    <div className="mt-1 text-slate-600">Last run {fmtDate(i.last_run_at, true)} · next expected {fmtDate(i.next_expected_run)}</div>
                    <div className="text-slate-600">Last IPE-verified run: {i.last_ipe_verified_run ? <A href={`/ipe/${i.last_ipe_verified_run.source_run_id}`}>{fmtDate(i.last_ipe_verified_run.started_at)}</A> : "none"}</div>
                    {i.scope_shortfall.length > 0 && <div className="mt-1"><Badge code="FAIL" label={`missing scopes: ${i.scope_shortfall.join(", ")}`} /></div>}
                  </div>
                ))}
                {!(c.installations || []).length && <p className="text-xs text-slate-500">Not installed</p>}
              </div>
            </div>
          </Card>
        ))}
      </div>
      {cert && <CertModal c={cert} onClose={() => setCert(null)} onDone={() => { setCert(null); reload(); }} />}
    </>
  );
}

function CertModal({ c, onClose, onDone }: { c: any; onClose: () => void; onDone: () => void }) {
  const [state, setState] = useState(c.certification);
  const [health, setHealth] = useState(c.runtime_health);
  const [reason, setReason] = useState("");
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title={`Certification · ${c.name}`}>
      <Notice>CERTIFIED requires every row of the certification matrix to PASS (auth scope, tenant isolation, pagination snapshot, population proof, schema mapping, transforms, retries/idempotency, IPE anomalies, version compatibility).</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div><label className="label" htmlFor={`${fid}-cert`}>Certification</label><select id={`${fid}-cert`} className="input" value={state} onChange={(e) => setState(e.target.value)}>{["UNASSESSED", "CERTIFIED", "SUSPENDED", "REVOKED"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-health`}>Runtime health</label><select id={`${fid}-health`} className="input" value={health} onChange={(e) => setHealth(e.target.value)}>{["HEALTHY", "DEGRADED", "FAILED", "DISABLED"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-reason`}>Reason (required)</label><input id={`${fid}-reason`} required className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!reason || a.busy} onClick={() => a.run(async () => {
        await api(`/connectors/${c.connector_id}/certification`, { method: "POST", json: { certification: state, runtime_health: health, reason } }); onDone();
      })}>Record transition</button></div>
    </Modal>
  );
}

function Outbox() {
  const { data, error } = useApi<any[]>("/outbox?limit=150");
  return (
    <Card subtitle="Domain events are written in the same transaction as the state change (transactional outbox).">
      <ErrorBox error={error} />
      <Table head={["Seq", "Event", "Aggregate", "Schema / dictionary", "Payload", "Available"]} loading={!data && !error}>
        {data?.map((e) => (
          <tr key={e.event_id} className="align-top">
            <td className="td mono text-xs">#{e.record_seq}</td><td className="td mono text-xs font-semibold">{e.event_type}</td>
            <td className="td text-xs">{e.aggregate_type}<div className="mono text-[11px] text-slate-500">{e.aggregate_id?.slice(0, 13)}</div></td>
            <td className="td mono text-[11px]">{e.schema_version} / {e.dictionary_version}</td>
            <td className="td max-w-md"><pre className="mono whitespace-pre-wrap break-all text-[11px] text-slate-600">{JSON.stringify(e.payload)}</pre></td>
            <td className="td text-xs">{fmtDate(e.available_at, true)}</td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
