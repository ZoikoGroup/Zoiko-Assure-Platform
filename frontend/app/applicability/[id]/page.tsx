"use client";

import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, Json, KV, Modal, Notice, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";
import { RuleTrace } from "@/components/Trace";
import { AttestModal } from "@/components/AttestModal";

export default function EvaluationPage() {
  const { id } = useParams<{ id: string }>();
  const { data: e, error, reload } = useApi<any>(`/applicability/evaluations/${id}`);
  const [decisive, setDecisive] = useState(false);
  const [att, setAtt] = useState(false);
  const [chal, setChal] = useState(false);
  const { can, why } = useCan();
  const det = e?.current_determination;
  const isLatest = det?.evaluation_id === e?.evaluation_id;
  /* Whether this evaluation once backed the determination (a revision in its history names it). Revisions only move to
     evaluations with newer knowledge, so such a record has been superseded: historical. An adopted determination
     flagged for review keeps the evaluation it was adopted on, and no structured field names the evaluation that raised
     the review, so an evaluation that never backed a flagged determination may be that one, a later one or an older
     reconstruction: it is not called historical, and nothing is inferred from the review_reason text. */
  const formerBasis = !!e && (e.determination_history ?? []).some((h: any) => h.evaluation_id === e.evaluation_id);
  const outsideFlagged = !isLatest && !formerBasis && det?.state === "ADOPTED" && !!det?.needs_review;
  return (
    <>
      <PageHeader spec="WF-APP-02 · ZA-APP-001 §7" title={e ? `${e.obligation_definition.key} · ${e.subject.display_name}` : "Evaluation"}
        subtitle={e?.obligation_definition.title}
        actions={det && isLatest && <>
          {(["PROPOSED", "IN_REVIEW"].includes(det.state) || det.needs_review) && e.outcome !== "UNCERTAIN" && <button className="btn-primary" disabled={!can("attest")} title={why("attest")} onClick={() => setAtt(true)}>Attest</button>}
          <button className="btn-ghost" disabled={!can("challenge")} title={why("challenge")} onClick={() => setChal(true)}>Challenge</button></>} />
      <PermissionNote perms={["attest", "challenge"]} />
      <ErrorBox error={error} />
      {e && (
        <div className="space-y-4">
          {!isLatest && det && (outsideFlagged ? (
            <Notice tone="warn">The current determination does not rest on this evaluation: it was adopted on another evaluation and is
              flagged for review{det.review_reason ? <> (&ldquo;{det.review_reason}&rdquo;)</> : null}. The record does not identify, in a
              structured field, which evaluation raised the review, so this evaluation is not marked as that one or as
              historical. It cannot be attested from this page.</Notice>
          ) : <Notice tone="warn">A newer evaluation backs the current determination. This record is historical and cannot be attested.</Notice>)}
          <Card>
            <KV cols={4} items={[
              ["Outcome", <Badge key="o" code={e.outcome} />], ["Determination", det ? <Badge key="d" code={det.state} /> : "—"],
              ["Effective (valid) time", fmtDate(e.effective_at, true)], ["Record cutoff", <span key="c" className="mono">#{e.record_cutoff_seq}</span>],
              ["Rule version", <span key="r" className="mono">v{e.rule_version.version}</span>], ["AST hash", <Hash key="h" value={e.rule_version.ast_hash} />],
              ["Input bundle hash", <Hash key="b" value={e.input_bundle_hash} />], ["Evaluator", <span key="v" className="mono text-xs">{e.evaluator_build}</span>],
              ["Trigger", <span key="t" className="mono text-xs">{e.trigger}</span>], ["Requested by", e.requested_by],
              ["Content release", <Hash key="cr" value={e.rule_version.content_release_id} n={8} />],
              ["Deterministic replay", <Badge key="rp" code={e.replay.matches ? "PASS" : "FAIL"} label={e.replay.matches ? `reproduced ${e.replay.reproduced_outcome}` : "MISMATCH"} />],
            ]} />
          </Card>

          {e.missing_facts?.length > 0 && (
            <Notice tone="warn"><b>Material unknowns:</b> {e.missing_facts.map((m: any) => (typeof m === "string" ? m : `${m.fact_definition ?? m.fact} (${m.reason_code ?? m.reason})`)).join(", ")} — <A href="/fact-requests">resolve via fact requests</A>.</Notice>
          )}

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2" title="Evaluation trace" subtitle="Three-valued logic (TRUE / FALSE / UNKNOWN). Short-circuited branches are NOT_EVALUATED, never FALSE."
              actions={<label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={decisive} onChange={(x) => setDecisive(x.target.checked)} />Decisive path only</label>}>
              <ul className="text-sm"><RuleTrace node={e.trace} decisiveOnly={decisive} /></ul>
            </Card>
            <Card title="Frozen input bundle">
              <Table head={["Fact", "Provenance", "Revision"]}>
                {e.input_bundle.fact_inputs.map((f: any) => (
                  <tr key={f.fact_assertion_revision_id}><td className="td mono text-xs">{f.fact_definition}</td><td className="td mono text-[11px]">{f.provenance_class}</td><td className="td"><Hash value={f.fact_assertion_revision_id} n={8} /></td></tr>
                ))}
                {e.input_bundle.unknown_facts?.map((f: any) => (
                  <tr key={f.fact_definition}><td className="td mono text-xs">{f.fact_definition}</td><td className="td" colSpan={2}><Badge code="UNKNOWN" label={f.reason_code} /></td></tr>
                ))}
              </Table>
              <details className="mt-3"><summary className="cursor-pointer text-xs text-navy-700">Raw bundle</summary><Json value={e.input_bundle} max={260} /></details>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Determination history" subtitle="Revisions are appended; prior revisions remain visible.">
              <Table head={["Record", "Outcome", "State", "Basis / actor"]}>
                {e.determination_history.map((h: any) => (
                  <tr key={h.revision_id} className={h.record_to_seq ? "opacity-60" : ""}>
                    <td className="td mono text-xs">{h.record_from_seq} → {h.record_to_seq ?? "∞"}</td>
                    <td className="td"><Badge code={h.outcome} /></td><td className="td"><Badge code={h.state} /></td>
                    <td className="td text-xs">{h.basis ?? "—"}<div className="text-slate-500">{h.actor_id}</div></td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Card title="Attestations & challenges">
              {e.attestations.map((a: any) => (
                <div key={a.attestation_id} className="mb-3 rounded-lg border border-navy-100 p-3 text-sm">
                  <div className="mb-1 flex items-center gap-2"><AttestationDecision decision={a.decision} /><span className="text-xs text-slate-500">{fmtDate(a.signed_at, true)} · {a.actor_id}</span></div>
                  <p className="text-xs italic text-navy-800">&ldquo;{a.statement}&rdquo;</p>
                </div>
              ))}
              {e.challenges.map((c: any) => (
                <div key={c.challenge_id} className="mb-3 rounded-lg border border-violet-200 bg-violet-50/50 p-3 text-sm">
                  <div className="flex items-center gap-2"><Badge code="REVIEW" label={c.challenge_class} /><Badge code={c.status} /></div>
                  <p className="mt-1">{c.detail}</p><p className="mt-1 text-xs text-slate-600">{c.disposition}</p>
                </div>
              ))}
              {!e.attestations.length && !e.challenges.length && <p className="text-sm text-slate-500">None yet.</p>}
            </Card>
          </div>
        </div>
      )}
      {att && det && <AttestModal det={{ ...det, subject: e.subject.display_name, obligation_definition: e.obligation_definition.key }} onClose={() => setAtt(false)} onDone={() => { setAtt(false); reload(); }} />}
      {chal && det && <Challenge lineage={det.lineage_id} onClose={() => setChal(false)} onDone={() => { setChal(false); reload(); }} />}
    </>
  );
}

/** Attestation codes (ZA-GLS-001 §07). Any other value is shown as received, never coerced to a known decision. */
function AttestationDecision({ decision }: { decision?: string | null }) {
  if (decision === "ATTESTED") return <Badge code="ATTESTED" />;
  if (decision === "DECLINED") return <Badge code="DECLINED" tone="bad" />;
  if (!decision) return <span className="text-xs text-slate-500">Decision not recorded</span>;
  return <><Badge code={decision} tone="neutral" /><span className="text-xs text-slate-500">unrecognised decision code</span></>;
}

function Challenge({ lineage, onClose, onDone }: { lineage: string; onClose: () => void; onDone: () => void }) {
  const [cls, setCls] = useState("FACT_WRONG");
  const [detail, setDetail] = useState("");
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title="Challenge determination">
      <Notice>A challenge creates a governed record. It never rewrites the evaluation.</Notice>
      <ErrorBox error={a.error} />
      <div className="mt-3 grid gap-3">
        <div><label className="label" htmlFor={`${fid}-class`}>Challenge class</label><select id={`${fid}-class`} className="input" value={cls} onChange={(e) => setCls(e.target.value)}>
          {["FACT_WRONG", "FACT_MISSING", "RULE_WRONG", "INTERPRETATION"].map((x) => <option key={x}>{x}</option>)}</select></div>
        <div><label className="label" htmlFor={`${fid}-detail`}>Detail (required)</label><textarea id={`${fid}-detail`} required className="input" rows={3} value={detail} onChange={(e) => setDetail(e.target.value)} /></div>
      </div>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!detail || a.busy} onClick={() => a.run(async () => {
        await api(`/applicability/determinations/${lineage}/challenge`, { method: "POST", json: { challenge_class: cls, detail } }); onDone();
      })}>Submit challenge</button></div>
    </Modal>
  );
}
