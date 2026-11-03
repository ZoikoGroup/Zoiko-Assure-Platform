"use client";

import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, KV, LiveRegion, LoadingSkeleton, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

export default function Ledger() {
  const { data: d, error, reload } = useApi<any>("/ledger");
  const v = useAction();
  const c = useAction();
  const { can, why } = useCan();
  const canCheckpoint = can("checkpoint") || can("package");
  return (
    <>
      <PageHeader spec="ZA-EVD-001 §§8-10 · WF-EVD-03" title="Ledger"
        subtitle="A per-tenant SHA-256 hash chain, anchored by cell-level Merkle checkpoints signed with Ed25519. RFC 3161 timestamping is reported honestly as pending when no TSA is configured."
        actions={<>
          <button className="btn-primary" disabled={v.busy} onClick={() => v.run(async () => { const r = await api("/ledger/verify", { method: "POST" }); reload(); return r; })}>Verify chain</button>
          <button className="btn-accent" disabled={c.busy || !canCheckpoint} title={canCheckpoint ? undefined : why("checkpoint")} onClick={() => c.run(async () => { const r = await api("/ledger/checkpoint", { method: "POST" }); reload(); return r; })}>Create checkpoint</button>
        </>} />
      <PermissionNote perms={[["checkpoint", "package"]]} />
      <ErrorBox error={error || v.error || c.error} />
      <LiveRegion>{v.result && <div className="mb-4"><Notice tone={v.result.result === "VERIFIED" ? "good" : "warn"}>Chain verification <b>{v.result.result}</b>: {v.result.events_checked} events checked through #{v.result.head_seq}{v.result.first_failing_seq ? `; first failure at #${v.result.first_failing_seq}` : ""}.
        {v.result.result === "FAILED" && " A CRITICAL integrity incident was opened."} This checks the hash links between this tenant&apos;s ledger events; it does not re-check evidence payloads or their condition.</Notice></div>}</LiveRegion>
      <LiveRegion>{c.result && <div className="mb-4"><Notice tone="good">{c.result.message ?? `Checkpoint ${c.result.checkpoint_id?.slice(0, 8)} created with ${c.result.leaf_count} leaves · TSA ${c.result.tsa_status}`}</Notice></div>}</LiveRegion>
      {!d && !error && <Card><LoadingSkeleton rows={4} /></Card>}
      {d && (
        <div className="space-y-4">
          <Card title="Tenant ledger head">
            {d.head ? <KV cols={4} items={[["Head seq", <span key="s" className="mono">#{d.head.current_seq}</span>], ["Head hash", <Hash key="h" value={d.head.current_event_hash} />],
              ["Checkpointed through", <span key="c" className="mono">#{d.head.last_checkpointed_seq}</span>], ["Chain verified through", <span key="v" className="mono">#{d.head.last_verified_seq}</span>],
              ["Signer key", <span key="k" className="mono text-xs">{d.signer_key_id}</span>], ["Updated", fmtDate(d.head.updated_at, true)]]} /> : <p className="text-sm text-slate-500">No ledger events yet.</p>}
          </Card>
          <Card title="Checkpoints">
            <Table head={["Checkpoint", "Window", "Leaves", "Merkle root", "Manifest hash", "TSA", "Includes this tenant", "WORM"]} empty={!d.checkpoints.length}>
              {d.checkpoints.map((x: any) => (
                <tr key={x.checkpoint_id}>
                  <td className="td"><Hash value={x.checkpoint_id} n={8} /><div className="text-[11px] text-slate-500">{fmtDate(x.created_at, true)}</div></td>
                  <td className="td text-xs">{fmtDate(x.window_start, true)} → {fmtDate(x.window_end, true)}</td>
                  <td className="td">{x.leaf_count}</td><td className="td"><Hash value={x.merkle_root} /></td><td className="td"><Hash value={x.manifest_hash} /></td>
                  <td className="td"><Badge code={x.tsa_status} title={x.tsa_note ?? ""} /></td>
                  <td className="td">{x.includes_tenant ? "yes" : "no"}</td>
                  <td className="td mono text-[11px]">{x.worm_object_ref ?? "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
          <Card title="Ledger events (latest first)">
            <Table head={["Seq", "Event", "Evidence", "Actor", "Event hash", "Previous hash", "Recorded"]}>
              {d.events.map((e: any) => (
                <tr key={e.event_id}>
                  <td className="td mono text-xs">#{e.ledger_seq}</td><td className="td"><Badge code={e.event_type} tone="info" /></td>
                  <td className="td"><A href={`/evidence/${e.evidence_id}`}>{e.evidence_id.slice(0, 8)}…</A></td>
                  <td className="td text-xs">{e.actor_id}</td><td className="td"><Hash value={e.event_hash} n={10} /></td><td className="td"><Hash value={e.previous_event_hash} n={10} /></td>
                  <td className="td text-xs">{fmtDate(e.recorded_at, true)}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>
      )}
    </>
  );
}
