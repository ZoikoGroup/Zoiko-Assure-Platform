"use client";

import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Card, ErrorBox, KV, Notice, PageHeader, Table, useAction } from "@/components/ui";
import { RuleTrace } from "@/components/Trace";

export default function History() {
  const { data: subs } = useApi<any[]>("/org/subjects");
  const { data: ods } = useApi<any[]>("/catalog/obligation-definitions");
  const { data: wm } = useApi<any>("/watermark");
  const [f, setF] = useState({ subject_id: "", obligation_definition_id: "", valid_at: "2027-12-15", left_seq: "20", right_seq: "" });
  const a = useAction();
  const right = f.right_seq || String(wm?.record_seq ?? "");
  const r = a.result;
  const odsForSubject = (ods || []).filter((o) => !f.subject_id || o.subject_type === subs?.find((s) => s.subject_id === f.subject_id)?.subject_type);
  return (
    <>
      <PageHeader spec="ZA-TIM-001 §12 · WF-TIM-02" title="Historical Reconstruction"
        subtitle="Pin the valid time and compare two record cutoffs: what Assure knew then (AS_KNOWN_THEN) against what it knows now (RECONSTRUCTED_NOW). Differences are classified, not blended." />
      <Card title="Comparison coordinates">
        <ErrorBox error={a.error} />
        <div className="grid gap-3 sm:grid-cols-5">
          <div><label className="label">Subject</label><select className="input" value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value, obligation_definition_id: "" })}><option value="">Select…</option>
            {subs?.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.display_name}</option>)}</select></div>
          <div><label className="label">Obligation definition</label><select className="input" value={f.obligation_definition_id} onChange={(e) => setF({ ...f, obligation_definition_id: e.target.value })}><option value="">Select…</option>
            {odsForSubject.map((o) => <option key={o.obligation_definition_id} value={o.obligation_definition_id}>{o.key}</option>)}</select></div>
          <div><label className="label">Valid time</label><input type="date" className="input" value={f.valid_at} onChange={(e) => setF({ ...f, valid_at: e.target.value })} /></div>
          <div><label className="label">Left cutoff (known then)</label><input type="number" className="input" value={f.left_seq} onChange={(e) => setF({ ...f, left_seq: e.target.value })} /></div>
          <div><label className="label">Right cutoff (blank = now #{wm?.record_seq})</label><input type="number" className="input" value={f.right_seq} onChange={(e) => setF({ ...f, right_seq: e.target.value })} /></div>
        </div>
        <button className="btn-primary mt-4" disabled={!f.subject_id || !f.obligation_definition_id || a.busy} onClick={() => a.run(() => api(
          `/history/compare?subject_id=${f.subject_id}&obligation_definition_id=${f.obligation_definition_id}&valid_at=${new Date(f.valid_at).toISOString()}&left_seq=${f.left_seq}&right_seq=${right}`))}>Compare</button>
      </Card>
      {r && (
        <div className="mt-4 space-y-4">
          <Card title="Classified differences">
            <Table head={["Type", "Subject", "Left", "Right"]}>
              {r.differences.map((d: any, i: number) => (
                <tr key={i}><td className="td"><Badge code={d.type === "NO_SEMANTIC_CHANGE" ? "PASS" : "REVIEW"} label={d.type} /></td><td className="td mono text-xs">{d.fact ?? "rule version"}</td>
                  <td className="td mono text-[11px]">{d.left ?? d.from ?? "—"}</td><td className="td mono text-[11px]">{d.right ?? d.to ?? "—"}</td></tr>
              ))}
            </Table>
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            {(["left", "right"] as const).map((side) => (
              <Card key={side} title={side === "left" ? "As known then" : "Reconstructed now"} subtitle={`Valid ${f.valid_at} · record cutoff #${r[side].known_seq}`}
                actions={r[side].outcome && <Badge code={r[side].outcome} />}>
                {r[side].error ? <Notice tone="warn">{typeof r[side].error === "string" ? r[side].error : JSON.stringify(r[side].error)}</Notice> : (
                  <>
                    <KV cols={2} items={[["Rule version", <span key="r" className="mono text-[11px]">{r[side].rule_version_id?.slice(0, 13)}…</span>], ["Input bundle", <span key="b" className="mono text-[11px]">{r[side].input_bundle_hash?.slice(0, 16)}…</span>]]} />
                    <ul className="mt-3 text-sm"><RuleTrace node={r[side].trace} /></ul>
                  </>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
