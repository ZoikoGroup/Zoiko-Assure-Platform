"use client";

import { useParams } from "next/navigation";
import { useId, useRef, useState } from "react";
import { api, download, useApi } from "@/lib/api";
import { codeText, DATA_CLASS_CODES } from "@/lib/codes";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, KV, LiveRegion, Modal, Notice, PageHeader, PermissionNote, ReasonField, Table, useAction } from "@/components/ui";
import { ConflictTable, type EvidenceConflict } from "@/components/EvidenceConflicts";
import { ManualProvenance } from "@/components/EvidenceProvenance";

export default function EvidenceRecord() {
  const { id } = useParams<{ id: string }>();
  const { data: e, error, reload } = useApi<any>(`/evidence/${id}`);
  const conflicts = useApi<EvidenceConflict[]>("/evidence-conflicts");
  const mine = (conflicts.data ?? []).filter((c) => c.member_evidence_ids.includes(id));
  const openMine = mine.filter((c) => c.status === "OPEN").length;
  // The latest integrity check recorded on the ledger. Its event type is always VERIFIED; the body carries the actual result.
  const lastCheck = [...(e?.history ?? [])].reverse().find((h: any) => h.event_type === "VERIFIED" && h.canonical_event_body?.result);
  const a = useAction();
  const [act, setAct] = useState<string | null>(null);
  const { can, why } = useCan();
  const name = useActorNames();
  return (
    <>
      <PageHeader kicker={<A href="/evidence">← Evidence Records</A>} spec="WF-EVD-01 Evidence Record & Chain Explorer" title={e?.title ?? "Evidence"} subtitle={e ? `${e.origin_class} · ${e.acquisition_method}` : undefined}
        help="Every change to this record is appended to your organisation's evidence ledger; nothing is edited in place. Verifying integrity re-checks the stored payload against its recorded digests."
        actions={e && <>
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { const r = await api(`/evidence/${id}/verify`, { method: "POST" }); reload(); return r; })}>Verify integrity</button>
          {e.payload_available && <button className="btn-ghost" onClick={() => a.run(() => download(`/evidence/${id}/download`, `${e.title}`))}>Download</button>}
          <button className="btn-ghost" disabled={!can("submit_evidence")} title={why("submit_evidence")} onClick={() => setAct("bind")}>Bind</button>
          <button className="btn-ghost" disabled={!can("submit_evidence")} title={why("submit_evidence")} onClick={() => setAct("supersede")}>Supersede</button>
          <button className="btn-ghost" disabled={!can("submit_evidence")} title={why("submit_evidence")} onClick={() => setAct("invalidate")}>Invalidate</button>
          {e.legal_hold ? <button className="btn-ghost" disabled={!can("attest")} title={why("attest")} onClick={() => setAct("release-hold")}>Release hold</button>
            : <button className="btn-ghost" disabled={!can("attest")} title={why("attest")} onClick={() => setAct("hold")}>Legal hold</button>}
          {e.payload_available && <button className="btn-danger" disabled={!can("attest")} title={why("attest")} onClick={() => setAct("erase")}>Erase payload</button>}
        </>} />
      <PermissionNote perms={["submit_evidence", "attest"]} actions={{ submit_evidence: "Bind, supersede or invalidate", attest: "Legal hold and payload erasure" }} />
      <ErrorBox error={error || a.error} />
      <LiveRegion>{a.result?.checks && (
        <div className="mb-4"><Notice tone={a.result.result === "VERIFIED" ? "good" : "warn"}>
          Integrity verification: <b>{a.result.result}</b> — {a.result.checks.map((c: any) => `${c.check}: ${c.result}`).join(" · ")}.{" "}
          {a.result.result === "FAILED" && <>A CRITICAL integrity incident was opened in <A href="/governance">Findings &amp; Actions</A>. </>}
          This only checks that the stored payload still matches its recorded digests. It does not show that the evidence is CURRENT, sufficient or correct for any control.
        </Notice></div>
      )}</LiveRegion>
      {e && (
        <div className="space-y-4">
          <Card>
            <KV cols={4} items={[
              ["Condition", <Badge key="c" code={e.condition} />], ["Lifecycle", <Badge key="l" code={e.lifecycle} />],
              ["Integrity (latest check)", lastCheck
                ? <span key="iv"><Badge code={lastCheck.canonical_event_body.result} /> <span className="text-[11px] text-slate-500">{fmtDate(lastCheck.recorded_at, true)} · ledger #{lastCheck.ledger_seq}</span></span>
                : <span key="iv" className="text-xs text-slate-500">No integrity check recorded</span>],
              ["Provenance", <span key="p" className="block space-y-1"><span className="mono block text-xs">{e.origin_class} · {e.provenance_class}</span><ManualProvenance origin={e.origin_class} note /></span>],
              ["Review", <Badge key="r" code={e.review_status} />],
              ["Coverage", `${fmtDate(e.coverage_from)} → ${fmtDate(e.coverage_to)}`], ["Valid until", fmtDate(e.valid_until)],
              ["Captured", fmtDate(e.captured_at, true)], ["Recorded", fmtDate(e.recorded_at, true)],
              ["Source system", e.source_system], ["Source checksum", e.source_checksum ? <Hash key="sc" value={e.source_checksum} /> : <span key="sc" className="text-xs text-slate-500">not provided by source</span>],
              ["Plaintext HMAC (tenant-keyed)", <Hash key="h" value={e.plaintext_hmac} />], ["Ciphertext digest", <Hash key="cd" value={e.ciphertext_digest} />],
              ["Encryption", <span key="e" className="mono text-xs">{e.encryption_profile}</span>], ["Fingerprint key", <span key="f" className="mono text-xs">{e.fingerprint_key_version}</span>],
              ["Home cell", <span key="hc" className="mono text-xs">{e.home_cell}</span>], ["Classification", <span key="dc" className="mono text-xs">{codeText(e.data_classification, DATA_CLASS_CODES)}</span>],
            ]} />
            {!e.payload_available && <div className="mt-3"><Notice tone="warn">The payload is erased from active storage: its data key was destroyed. Backup copies are retired as those backups expire. Metadata, digests and ledger history remain verifiable.</Notice></div>}
            {e.legal_hold && <div className="mt-3"><Notice tone="warn">A legal hold is active. Erasure and retention expiry are blocked.</Notice></div>}
            {openMine > 0 && <div className="mt-3"><Notice tone="warn">
              This evidence is a member of {openMine} open evidence conflict{openMine > 1 ? "s" : ""} (see Evidence conflicts below). Its own condition above is unchanged; control
              evaluations that rely on it treat it as <Badge code="CONFLICTING" />, which cannot support proof until the conflict is resolved. A conflict means Assure cannot yet
              establish which source is right; on its own it does not mean a control failed.
            </Notice></div>}
          </Card>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2" title="Ledger history" subtitle="Each event hashes the previous one; lifecycle is derived from these events.">
              <Table head={["Seq", "Event", "Actor", "Reason", "Event hash", "Prev hash", "At"]}>
                {e.history.map((h: any) => (
                  <tr key={h.event_id}>
                    <td className="td mono text-xs">#{h.ledger_seq}</td><td className="td"><Badge code={h.event_type} tone="info" />
                      {h.event_type === "VERIFIED" && h.canonical_event_body?.result && <> <Badge code={h.canonical_event_body.result} label={`result ${h.canonical_event_body.result}`} /></>}</td>
                    <td className="td text-xs">{name(h.actor_id)}</td><td className="td text-xs">{h.reason ?? "—"}</td>
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

          <Card title="Evidence conflicts" subtitle="Conflicts that include this evidence (ZA-EVD-001 §13). The evidence itself is never altered.">
            <ErrorBox error={conflicts.error} />
            {conflicts.data ? <ConflictTable conflicts={mine} currentId={id} emptyText="No evidence conflict record names this artifact." />
              : !conflicts.error && <p className="text-xs text-slate-500">Loading conflict records…</p>}
          </Card>

          {e.source_run && (
            <Card title="Source run & population" actions={<A href={`/ipe/${e.source_run.source_run_id}`}>Inspect source run →</A>}>
              <KV cols={4} items={[["IPE result", <Badge key="i" code={e.ipe_result} />], ["Run outcome", <Badge key="o" code={e.source_run.run_outcome} />],
                ["Source total", e.population_manifest?.source_reported_total], ["Unique extracted", e.population_manifest?.unique_identity_count]]} />
            </Card>
          )}
        </div>
      )}
      {act && <ActionModal kind={act} id={id} evidenceTitle={e?.title} onClose={() => setAct(null)} onDone={() => { setAct(null); reload(); }} />}
    </>
  );
}

function ActionModal({ kind, id, evidenceTitle, onClose, onDone }: { kind: string; id: string; evidenceTitle?: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [extra, setExtra] = useState("");
  const [tt, setTt] = useState("OBLIGATION");
  const [tried, setTried] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const a = useAction();
  const fid = useId();
  // Every action except bind requires a reason (the backend's ReasonIn); a blank or whitespace-only one is not sent.
  const reasonRequired = kind !== "bind";
  const reasonText = reason.trim();
  const reasonMissing = reasonRequired && !reasonText;
  const titles: Record<string, string> = { supersede: "Supersede with successor", invalidate: "Invalidate evidence", hold: "Place legal hold", "release-hold": "Release legal hold", erase: "Erase payload (crypto-shred)", bind: "Bind evidence" };
  const submit = () => {
    if (reasonMissing) { setTried(true); reasonRef.current?.focus(); return; }
    a.run(async () => {
      if (kind === "bind") await api(`/evidence/${id}/bindings`, { method: "POST", json: { target_type: tt, target_id: extra, rationale: reasonText || null } });
      else await api(`/evidence/${id}/${kind}`, { method: "POST", json: { reason: reasonText, authority: kind === "hold" ? extra || null : null, successor_evidence_id: kind === "supersede" ? extra : null } });
      onDone();
    });
  };
  return (
    <Modal open onClose={onClose} title={titles[kind]}>
      {kind === "erase" && <p className="mb-3 text-[13px] text-slate-700">You are about to erase the payload of the evidence record{evidenceTitle ? <> <span className="font-medium">&ldquo;{evidenceTitle}&rdquo;</span></> : null}. This cannot be undone: Assure does not restore an erased payload.</p>}
      {kind === "erase" && <Notice tone="warn">Erasure deletes the active payload and destroys its data key, so the payload can no longer be read through Assure. Backup copies are not erased instantly: they expire under the backup retention schedule, and any restore replays this erasure before payload access. Metadata, digests and the ledger stay intact. Not allowed while a legal hold is active.</Notice>}
      {kind === "supersede" && <Notice>Supersession appends an event that points to a new evidence ID. The original bytes are never replaced.</Notice>}
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3">
        {kind === "bind" && <div><label className="label" htmlFor={`${fid}-type`}>Target type</label><select id={`${fid}-type`} className="input" value={tt} onChange={(e) => setTt(e.target.value)}>{["OBLIGATION", "CONTROL_IMPLEMENTATION", "TEST_RUN", "OCCURRENCE"].map((x) => <option key={x}>{x}</option>)}</select></div>}
        {["supersede", "hold", "bind"].includes(kind) && <div><label className="label" htmlFor={`${fid}-extra`}>{kind === "supersede" ? "Successor evidence id" : kind === "hold" ? "Authority" : "Target id"}</label><input id={`${fid}-extra`} className="input" value={extra} onChange={(e) => setExtra(e.target.value)} /></div>}
        <ReasonField id={`${fid}-reason`} label={reasonRequired ? "Reason (required)" : "Rationale"} required={reasonRequired}
          value={reason} onChange={setReason} showError={tried && reasonMissing} inputRef={reasonRef} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        {kind === "erase" && <button type="button" className="btn-ghost" disabled={a.busy} onClick={onClose}>Cancel</button>}
        <button className={kind === "erase" ? "btn-danger" : "btn-primary"} disabled={a.busy} aria-busy={a.busy} onClick={submit}>
          {kind === "erase" ? (a.busy ? "Erasing payload…" : "Erase payload") : "Confirm"}
        </button>
      </div>
    </Modal>
  );
}
