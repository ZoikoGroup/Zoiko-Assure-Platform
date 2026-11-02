"use client";

import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, ErrorBox, Hash, KV, LiveRegion, Notice, PageHeader, PermissionNote, Table, TabPanel, Tabs, useAction } from "@/components/ui";
import { AssertModal } from "@/components/AssertModal";

export default function SubjectPage() {
  const { subjectId } = useParams<{ subjectId: string }>();
  const [validAt, setValidAt] = useState("");
  const [knownSeq, setKnownSeq] = useState("");
  const q = new URLSearchParams();
  if (validAt) q.set("valid_at", new Date(validAt).toISOString());
  if (knownSeq) q.set("known_seq", knownSeq);
  const prof = useApi<any>(`/subjects/${subjectId}/facts?${q}`);
  const hist = useApi<any[]>(`/subjects/${subjectId}/facts/history`);
  const evals = useApi<any[]>(`/applicability/evaluations?subject_id=${subjectId}`);
  const [tab, setTab] = useState("Fact profile");
  const [assertFor, setAssertFor] = useState<any>(null);
  const a = useAction();
  const p = prof.data;
  const reloadAll = () => { prof.reload(); hist.reload(); evals.reload(); };
  const { can, why } = useCan();
  const fid = useId();
  const tabsId = useId();
  const name = useActorNames();

  return (
    <>
      <PageHeader kicker="Subject · facts and applicability" spec="WF-APP-01 · ZA-TIM-001" title={p?.subject.display_name ?? "Subject"}
        subtitle="The facts recorded about this subject, and the applicability evaluations and determinations based on them. Valid time (when a fact was true) and record time (when Assure learned it) are kept separately."
        actions={<button className="btn-primary" disabled={a.busy || !can("request_evaluation")} title={why("request_evaluation")} onClick={() => a.run(async () => { const r = await api(`/applicability/evaluate-all?subject_id=${subjectId}`, { method: "POST" }); evals.reload(); return r; })}>Evaluate all obligations</button>} />
      <PermissionNote perms={["request_evaluation", "manage_facts"]} />
      <ErrorBox error={prof.error || a.error} />
      <LiveRegion>
        {Array.isArray(a.result) && (
          <div className="mb-4"><Notice tone="good">Evaluated: {a.result.map((r: any) => `${r.obligation} → ${r.outcome ?? "error"}`).join(" · ")}</Notice></div>
        )}
      </LiveRegion>
      {p && (
        <Card className="mb-4">
          <div className="flex flex-wrap items-end gap-4">
            <KV cols={4} items={[["Subject type", <Badge key="t" code={p.subject.subject_type} tone="info" />], ["Perspective", <Badge key="p" code={p.perspective.perspective} tone="info" />],
              ["Valid at", fmtDate(p.perspective.valid_at, true)], ["Known as of", <span key="k" className="mono">{p.perspective.cell_id}#{p.perspective.known_seq}</span>]]} />
            <div className="ml-auto flex flex-wrap items-end gap-2">
              <div><label className="label" htmlFor={`${fid}-valid`}>Valid at</label><input id={`${fid}-valid`} type="datetime-local" className="input py-1 text-xs" value={validAt} onChange={(e) => setValidAt(e.target.value)} /></div>
              <div><label className="label" htmlFor={`${fid}-seq`}>Known seq</label><input id={`${fid}-seq`} type="number" className="input w-28 py-1 text-xs" value={knownSeq} onChange={(e) => setKnownSeq(e.target.value)} /></div>
              <button className="btn-ghost" onClick={() => { setValidAt(""); setKnownSeq(""); }}>Current</button>
            </div>
          </div>
        </Card>
      )}
      <Tabs id={tabsId} tabs={["Fact profile", "Revision history", "Evaluations"]} value={tab} onChange={setTab} />
      <TabPanel id={tabsId} value={tab}>
      {tab === "Fact profile" && p && (
        <Card>
          {p.conflicts.length > 0 && <div className="mb-3"><Notice tone="warn">{p.conflicts.length} open fact conflict(s): {p.conflicts.map((c: any) => c.fact_definition_id?.slice(0, 8)).join(", ")}</Notice></div>}
          <Table head={["Fact", "Value", "Status", "Provenance", "Valid", "Revision", ""]}>
            {p.facts.map((f: any) => (
              <tr key={f.fact_key}>
                <td className="td"><div className="mono text-xs font-semibold">{f.fact_key}</div><div className="text-[11px] text-slate-500">{f.label} · {f.data_type}{f.sensitivity !== "CUSTOMER_INTERNAL" ? ` · ${f.sensitivity}` : ""}</div></td>
                <td className="td mono text-sm">{f.resolution.known ? JSON.stringify(f.resolution.value) : "—"}</td>
                <td className="td">{f.resolution.known ? <Badge code="KNOWN" tone="good" /> : <Badge code="UNKNOWN" label={f.resolution.reason ?? "UNKNOWN"} />}</td>
                <td className="td mono text-[11px]">{f.resolution.provenance_class ?? "—"}</td>
                <td className="td text-xs">{f.resolution.valid_from ? `${fmtDate(f.resolution.valid_from)} → ${f.resolution.valid_to ? fmtDate(f.resolution.valid_to) : "open"}` : "—"}</td>
                <td className="td"><Hash value={f.resolution.revision_id} n={8} /></td>
                <td className="td"><button className="btn-ghost" disabled={!can("manage_facts")} title={why("manage_facts")} onClick={() => setAssertFor(f)}>Assert / correct</button></td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      {tab === "Revision history" && (
        <Card subtitle="Corrections close the prior record interval and carve valid time; nothing is overwritten.">
          <Table head={["Record seq", "Fact", "Value", "Valid", "Provenance", "Change", "Actor"]} empty={hist.data?.length === 0}>
            {hist.data?.map((r) => (
              <tr key={r.revision_id} className={r.record_to_seq ? "opacity-60" : ""}>
                <td className="td mono text-xs">{r.record_from_seq} → {r.record_to_seq ?? "∞"}</td>
                <td className="td mono text-xs">{r.fact_key}</td>
                <td className="td mono text-xs">{JSON.stringify(r.value)}</td>
                <td className="td text-xs">{fmtDate(r.valid_from)} → {r.valid_to ? fmtDate(r.valid_to) : "open"}</td>
                <td className="td mono text-[11px]">{r.provenance_class}</td>
                <td className="td text-xs"><Badge code={r.change_kind} tone="info" />{r.change_reason && <div className="text-slate-500">{r.change_reason}</div>}</td>
                <td className="td text-xs">{name(r.actor_id)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      {tab === "Evaluations" && (
        <Card>
          <Table head={["Obligation", "Outcome", "Effective at", "Cutoff", "Trigger", "Determination"]} empty={evals.data?.length === 0}>
            {evals.data?.map((e) => (
              <tr key={e.evaluation_id}>
                <td className="td"><A href={`/applicability/${e.evaluation_id}`}>{e.obligation_definition.key}</A></td>
                <td className="td"><Badge code={e.outcome} /></td>
                <td className="td text-xs">{fmtDate(e.effective_at, true)}</td>
                <td className="td mono text-xs">#{e.record_cutoff_seq}</td>
                <td className="td mono text-[11px]">{e.trigger}</td>
                <td className="td">{e.current_determination ? <Badge code={e.current_determination.state} /> : "—"}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
      </TabPanel>
      <AssertModal subjectId={subjectId} fact={assertFor} onClose={() => setAssertFor(null)} onDone={() => { setAssertFor(null); reloadAll(); }} />
    </>
  );
}
