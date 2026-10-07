"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { api, download, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, Hash, Json, KV, Modal, Notice, PageHeader, Table, useAction } from "@/components/ui";

export default function EvidenceRecord() {
  const { id } = useParams<{ id: string }>();
  const { data: e, error, reload } = useApi<any>(`/evidence/${id}`);
  const a = useAction();
  const [act, setAct] = useState<string | null>(null);
  return (
    <>
      <PageHeader spec="WF-EVD-01 Evidence Record & Chain Explorer" title={e?.title ?? "Evidence"} subtitle={e ? `${e.origin_class} · ${e.acquisition_method}` : undefined}
        actions={e && <>
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { const r = await api(`/evidence/${id}/verify`, { method: "POST" }); reload(); return r; })}>Verify integrity</button>
          {e.payload_available && <button className="btn-ghost" onClick={() => a.run(() => download(`/evidence/${id}/download`, `${e.title}`))}>Download</button>}
          <button className="btn-ghost" onClick={() => setAct("bind")}>Bind</button>
          <button className="btn-ghost" onClick={() => setAct("supersede")}>Supersede</button>
          <button className="btn-ghost" onClick={() => setAct("invalidate")}>Invalidate</button>
          {e.legal_hold ? <button className="btn-ghost" onClick={() => setAct("release-hold")}>Release hold</button> : <button className="btn-ghost" onClick={() => setAct("hold")}>Legal hold</button>}
          {e.payload_available && <button className="btn-danger" onClick={() => setAct("erase")}>Erase payload</button>}
        </>} />
      <ErrorBox error={error || a.error} />
      {a.result?.checks && (
        <div className="mb-4"><Notice tone={a.result.result === "VERIFIED" ? "good" : "warn"}>
          Verification: <b>{a.result.result}</b> — {a.result.checks.map((c: any) => `${c.check}: ${c.result}`).join(" · ")}
        </Notice></div>
      )}
      {e && (
        <div className="space-y-4">
          <Card>
            <KV cols={4} items={[
              ["Condition", <Badge key="c" code={e.condition} />], ["Lifecycle", <Badge key="l" code={e.lifecycle} />],
              ["Provenance", <span key="p" className="mono text-xs">{e.provenance_class}</span>], ["Review", <Badge key="r" code={e.review_status} />],
              ["Coverage", `${fmtDate(e.coverage_from)} → ${fmtDate(e.coverage_to)}`], ["Valid until", fmtDate(e.valid_until)],
              ["Captured", fmtDate(e.captured_at, true)], ["Recorded", fmtDate(e.recorded_at, true)],
              ["Source system", e.source_system], ["Source checksum", e.source_checksum ? <Hash key="sc" value={e.source_checksum} /> : <span key="sc" className="text-xs text-slate-500">not provided by source</span>],
              ["Plaintext HMAC (tenant-keyed)", <Hash key="h" value={e.plaintext_hmac} />], ["Ciphertext digest", <Hash key="cd" value={e.ciphertext_digest} />],
              ["Encryption", <span key="e" className="mono text-xs">{e.encryption_profile}</span>], ["Fingerprint key", <span key="f" className="mono text-xs">{e.fingerprint_key_version}</span>],
              ["Home cell", <span key="hc" className="mono text-xs">{e.home_cell}</span>], ["Classification", e.data_classification],
            ]} />
            {!e.payload_available && <div className="mt-3"><Notice tone="warn">The payload is erased: its data key was destroyed. Metadata, digests and ledger history remain verifiable.</Notice></div>}
            {e.legal_hold && <div className="mt-3"><Notice tone="warn">A legal hold is active. Erasure and retention expiry are blocked.</Notice></div>}
          </Card>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2" title="Ledger history" subtitle="Each event hashes the previous one. Lifecycle is derived from these events, never edited in place.">
              <Table head={["Seq", "Event", "Actor", "Reason", "Event hash", "Prev hash", "At"]}>
                {e.history.map((h: any) => (
                  <tr key={h.event_id}>
                    <td className="td mono text-xs">#{h.ledger_seq}</td><td className="td"><Badge code={h.event_type} tone="info" /></td>
                    <td className="td text-xs">{h.actor_id}</td><td className="td text-xs">{h.reason ?? "—"}</td>
                    <td className="td"><Hash value={h.event_hash} n={10} /></td><td className="td"><Hash value={h.previous_event_hash} n={10} /></td>
                    <td className="td text-xs">{fmtDate(h.recorded_at, true)}</td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="Anchoring">
              {e.checkpoint ? (
                <KV cols={2} items={[["Checkpoint", <Hash key="c" value={e.checkpoint.checkpoint_id} n={8} />], ["TSA", <Badge key="t" code={e.checkpoint.tsa_status} />],
                  ["Merkle root", <Hash key="m" value={e.checkpoint.merkle_root} />], ["Created", fmtDate(e.checkpoint.created_at, true)]]} />
              ) : <Notice tone="warn">Not checkpointed yet. <A href="/ledger">Create a checkpoint →</A></Notice>}
              <div className="mt-4 text-sm font-semibold text-navy-800">Bindings</div>
              {e.bindings.length ? e.bindings.map((b: any) => <div key={b.binding_id} className="text-xs"><span className="mono">{b.target_type}</span> {b.target_id.slice(0, 12)}… {b.rationale && <span className="text-slate-500">— {b.rationale}</span>}</div>) : <p className="text-xs text-slate-500">None</p>}
              <div className="mt-4 text-sm font-semibold text-navy-800">Legal holds</div>
              {e.holds.length ? e.holds.map((h: any) => <div key={h.hold_id} className="text-xs"><Badge code={h.status === "ACTIVE" ? "WARN" : "CLOSED"} label={h.status} /> {h.authority} — {h.reason}</div>) : <p className="text-xs text-slate-500">None</p>}
            </Card>
          </div>

          {e.source_run && (
            <Card title="Source run & population" actions={<A href={`/ipe/${e.source_run.source_run_id}`}>Inspect source run →</A>}>
              <KV cols={4} items={[["IPE result", <Badge key="i" code={e.ipe_result} />], ["Run outcome", <Badge key="o" code={e.source_run.run_outcome} />],
                ["Source total", e.population_manifest?.source_reported_total], ["Unique extracted", e.population_manifest?.unique_identity_count]]} />
            </Card>
          )}
        </div>
      )}
      {act && <ActionModal kind={act} id={id} onClose={() => setAct(null)} onDone={() => { setAct(null); reload(); }} />}
    </>
  );
}

function ActionModal({ kind, id, onClose, onDone }: { kind: string; id: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [extra, setExtra] = useState("");
  const [tt, setTt] = useState("OBLIGATION");
  const a = useAction();
  const titles: Record<string, string> = { supersede: "Supersede with successor", invalidate: "Invalidate evidence", hold: "Place legal hold", "release-hold": "Release legal hold", erase: "Erase payload (crypto-shred)", bind: "Bind evidence" };
  const submit = () => a.run(async () => {
    if (kind === "bind") await api(`/evidence/${id}/bindings`, { method: "POST", json: { target_type: tt, target_id: extra, rationale: reason || null } });
    else await api(`/evidence/${id}/${kind}`, { method: "POST", json: { reason, authority: kind === "hold" ? extra || null : null, successor_evidence_id: kind === "supersede" ? extra : null } });
    onDone();
  });
  return (
    <Modal open onClose={onClose} title={titles[kind]}>
      {kind === "erase" && <Notice tone="warn">Erasure destroys the data key. The bytes become unrecoverable, while metadata, digests and the ledger stay intact. Not allowed while a legal hold is active.</Notice>}
      {kind === "supersede" && <Notice>Supersession appends an event that points to a new evidence ID. The original bytes are never replaced.</Notice>}
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3">
        {kind === "bind" && <div><label className="label">Target type</label><select className="input" value={tt} onChange={(e) => setTt(e.target.value)}>{["OBLIGATION", "CONTROL_IMPLEMENTATION", "TEST_RUN", "OCCURRENCE"].map((x) => <option key={x}>{x}</option>)}</select></div>}
        {["supersede", "hold", "bind"].includes(kind) && <div><label className="label">{kind === "supersede" ? "Successor evidence id" : kind === "hold" ? "Authority" : "Target id"}</label><input className="input" value={extra} onChange={(e) => setExtra(e.target.value)} /></div>}
        <div><label className="label">{kind === "bind" ? "Rationale" : "Reason"}</label><textarea className="input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className={kind === "erase" ? "btn-danger" : "btn-primary"} disabled={a.busy || (kind !== "bind" && !reason)} onClick={submit}>Confirm</button></div>
    </Modal>
  );
}
