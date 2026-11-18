"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useActorNames } from "@/lib/people";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, CoverageBadge, ErrorBox, ExceptionBadge, Hash, Json, KV, LiveRegion, Modal, Notice, PageHeader, PeriodSelector, PermissionNote, Table, TabPanel, Tabs, useAction, useToast } from "@/components/ui";
import { ExprTrace, SegmentRibbon, TreatmentList } from "@/components/Trace";
import { ManualProvenance } from "@/components/EvidenceProvenance";
import { CompensationSoDNote } from "@/components/CompensationSoD";

export default function Dossier() {
  const { lineage } = useParams<{ lineage: string }>();
  const { data: d, error, reload } = useApi<any>(`/obligations/${lineage}`);
  const name = useActorNames();
  const [tab, setTab] = useState("Overview");
  const tabsId = useId();
  return (
    <>
      <PageHeader kicker="Obligation dossier" spec="WF-STA-01 Obligation Assurance Dossier" title={d?.definition.title ?? "Obligation"}
        subtitle={d ? `For ${d.subject.display_name} · ${d.definition.key}` : undefined} />
      <PermissionNote perms={[["manage_controls", "manage_facts"], ["request_evaluation", "package"], "request_exception"]}
        actions={{ "manage_controls|manage_facts": "Add and fulfil occurrences", "request_evaluation|package": "Freeze period snapshots", request_exception: "Request exceptions and compensating controls" }} />
      <ErrorBox error={error} />
      {d && (
        <>
          <Card className="mb-4">
            <KV cols={4} items={[
              ["Lifecycle", <Badge key="l" code={d.obligation.lifecycle} />], ["Valid", `${fmtDate(d.obligation.valid_from)} → ${d.obligation.valid_to ? fmtDate(d.obligation.valid_to) : "open"}`],
              ["Owner", name(d.obligation.owner_actor_id)], ["Schedule", <span key="s" className="mono">{d.definition.schedule_kind}</span>],
              ["Determination", d.determination ? <span key="d"><Badge code={d.determination.outcome} /> <Badge code={d.determination.state} /> <A href={`/applicability/${d.determination.evaluation_id}`}>trace</A></span> : "—"],
              ["Adopted by", d.attestation ? `${name(d.attestation.actor_id)} · ${fmtDate(d.attestation.signed_at)}` : "—"],
              ["Authority basis", d.attestation?.authority_basis], ["Expression versions", d.expression_versions.length],
              ["Coverage mode (definition)", <CoverageBadge key="cov" mode={d.definition.coverage_mode} showMeaning />],
            ]} />
          </Card>
          <Tabs tabs={["Overview", "Period assurance", "Expression & controls", "Occurrences", "Exceptions & compensation", "Legal basis", "Revisions"]} value={tab} onChange={setTab} id={tabsId} />
          <TabPanel id={tabsId} value={tab}>
          {tab === "Overview" && <Overview d={d} name={name} go={setTab} />}
          {tab === "Period assurance" && <Period d={d} lineage={lineage} reload={reload} />}
          {tab === "Expression & controls" && <Controls d={d} />}
          {tab === "Occurrences" && <Occurrences d={d} lineage={lineage} reload={reload} />}
          {tab === "Exceptions & compensation" && <ExcComp d={d} lineage={lineage} reload={reload} name={name} />}
          {tab === "Legal basis" && (
            <Card title="Provisions">
              <Table head={["Locator", "Duty bearer", "Modality", "Action", "Evidence expectation"]}>
                {d.provisions.map((p: any) => (
                  <tr key={p.provision_id}><td className="td mono text-xs">{p.locator}</td><td className="td text-sm">{p.duty_bearer}</td><td className="td mono text-xs">{p.modality}</td>
                    <td className="td text-sm">{p.action}</td><td className="td text-xs">{p.evidence_expectation ?? "—"}</td></tr>
                ))}
              </Table>
            </Card>
          )}
          {tab === "Revisions" && (
            <Card title="Obligation instance revisions">
              <Table head={["Record", "Lifecycle", "Valid", "Determination revision"]}>
                {d.revisions.map((r: any) => (
                  <tr key={r.revision_id}><td className="td mono text-xs">{r.record_from_seq} → {r.record_to_seq ?? "∞"}</td><td className="td"><Badge code={r.lifecycle} /></td>
                    <td className="td text-xs">{fmtDate(r.valid_from)} → {r.valid_to ? fmtDate(r.valid_to) : "open"}</td><td className="td"><Hash value={r.determination_revision_id} n={8} /></td></tr>
                ))}
              </Table>
            </Card>
          )}
          </TabPanel>
        </>
      )}
    </>
  );
}

/** The dossier in one view: from the legal requirement, through why it applies and who adopted it, to the controls,
 *  evidence and the latest assurance state. Read-only; every value is this obligation's own backend record. */
function Overview({ d, name, go }: { d: any; name: (id?: string | null) => string; go: (tab: string) => void }) {
  const latest = d.snapshots[0];
  const implemented = d.controls.filter((c: any) => c.implementation).length;
  const missing = d.controls.length - implemented;
  const fulfilled = d.occurrences.filter((o: any) => o.fulfilled_at).length;
  const approved = d.exceptions.filter((x: any) => x.state === "APPROVED").length;
  const openIssues = d.issues.filter((i: any) => !["CLOSED", "CANCELLED"].includes(i.state)).length;
  const tab = (t: string) => <button type="button" className="text-xs text-navy-700 hover:underline" onClick={() => go(t)}>{t} →</button>;
  const steps = [
    { label: "Requirement", about: "What the law requires, and of whom.",
      status: <>{d.provisions.length} provision(s){d.provisions[0] && <> · <span className="mono text-xs">{d.provisions[0].locator}</span> · {d.provisions[0].duty_bearer}</>}</>,
      action: tab("Legal basis") },
    { label: "Applicability", about: "Why it applies: the subject's facts evaluated against the encoded rule.",
      status: d.determination ? <><Badge code={d.determination.outcome} /> for {d.subject.display_name} · determination <Badge code={d.determination.state} /></> : "No determination recorded",
      action: d.determination && <Link href={`/applicability/${d.determination.evaluation_id}`} className="text-xs text-navy-700 hover:underline">Evaluation trace →</Link> },
    { label: "Obligation", about: "The duty your organisation accepted through a named attestation.",
      status: <><Badge code={d.obligation.lifecycle} /> {d.attestation ? <>adopted by {name(d.attestation.actor_id)} on {fmtDate(d.attestation.signed_at)}</> : "not attested"}</>,
      action: tab("Revisions") },
    { label: "Controls", about: "The shared controls its satisfaction expression requires.",
      status: <>{d.controls.length} required · {implemented} implemented{missing > 0 && <> · <Badge code="MISSING" label={`${missing} MISSING`} /></>}</>,
      action: tab("Expression & controls") },
    { label: "Evidence", about: "Each control's tests carry their own evidence (open a control); recurring duties also need evidence per occurrence.",
      status: d.occurrences.length ? <>{d.occurrences.length} occurrence(s) · {fulfilled} with fulfilment recorded</> : "No occurrences scheduled",
      action: tab("Occurrences") },
    { label: "Assurance", about: "The most recently frozen snapshot, computed by the backend for a named period and record cutoff from everything above. Never edited by hand.",
      status: latest ? <><Badge code={latest.state} /> {fmtDate(latest.period_from)} → {fmtDate(latest.period_to)} · <span className="mono text-[11px]">{latest.perspective}</span>
        · record cutoff <span className="mono text-[11px]">#{latest.record_cutoff_seq}</span> · frozen {fmtDate(latest.created_at, true)}
        {latest.reason_codes?.length > 0 && <span className="mono text-[11px] text-amber-700"> · {latest.reason_codes.join(", ")}</span>}</> : <Badge code="NOT_EVALUATED" label="NOT ASSESSED" />,
      action: tab("Period assurance") },
  ];
  return (
    <div className="space-y-4">
      <Card title="From requirement to assurance" subtitle="How this obligation's records connect. Each step depends on the one before it." bodyClassName="">
        <ol className="divide-y divide-slate-100">
          {steps.map((s, i) => (
            <li key={s.label} className="grid gap-1 px-4 py-3 sm:grid-cols-[150px_minmax(0,1fr)_auto] sm:items-start sm:gap-4">
              <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-900">
                <span aria-hidden className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-slate-300 text-[11px] text-slate-500">{i + 1}</span>{s.label}
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-slate-800">{s.status}</div>
                <p className="mt-0.5 text-xs text-slate-500">{s.about}</p>
              </div>
              <div className="sm:pt-0.5">{s.action}</div>
            </li>
          ))}
        </ol>
      </Card>
      {(approved > 0 || openIssues > 0) && (
        <Notice tone="warn">
          {approved > 0 && <>{approved} approved exception(s) apply to this obligation. They are shown beside the assurance state and never change it. </>}
          {openIssues > 0 && <>{openIssues} open issue(s) relate to it: see <A href="/governance">Findings &amp; Actions</A>.</>}
        </Notice>
      )}
    </div>
  );
}

function Period({ d, lineage, reload }: { d: any; lineage: string; reload: () => void }) {
  const [snapId, setSnapId] = useState<string | null>(d.snapshots[0]?.snapshot_id ?? null);
  const snap = useApi<any>(snapId ? `/snapshots/${snapId}` : null);
  const [seg, setSeg] = useState(0);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [cutoff, setCutoff] = useState("");
  const a = useAction();
  const rep = useAction();
  const { canAny, whyAny } = useCan();
  const FREEZE = ["request_evaluation", "package"];  // POST /obligations/{lineage}/snapshots accepts any of these
  const [preview, setPreview] = useState<any>(null);
  const fid = useId();
  useEffect(() => setSeg(0), [snapId]);
  const view = preview ?? snap.data;
  const s = view?.segments?.[seg];
  const period = view ? view.period ?? [view.period_from, view.period_to] : null;
  const body = () => ({ period_from: new Date(from).toISOString(), period_to: new Date(to).toISOString(), record_cutoff_seq: cutoff ? Number(cutoff) : null });
  return (
    <div className="space-y-4">
      <Card title="Assess a period" subtitle="Preview is not persisted. Freezing creates an immutable snapshot pinned to a record cutoff, reproducible later.">
        <ErrorBox error={a.error} />
        <div className="flex flex-wrap items-end gap-3">
          <PeriodSelector required from={from} to={to} onChange={(f, t) => { setFrom(f); setTo(t); }} />
          <div><label className="label" htmlFor={`${fid}-cutoff`}>Record cutoff seq (blank = current watermark)</label><input id={`${fid}-cutoff`} type="number" className="input w-36" value={cutoff} onChange={(e) => setCutoff(e.target.value)} /></div>
          <button className="btn-ghost" disabled={a.busy || !from || !to} onClick={() => a.run(async () => { setPreview(await api(`/obligations/${lineage}/preview`, { method: "POST", json: body() })); setSeg(0); })}>Preview</button>
          <button className="btn-primary" disabled={a.busy || !from || !to || !canAny(FREEZE)} title={whyAny(FREEZE)} onClick={() => a.run(async () => {
            const r = await api(`/obligations/${lineage}/snapshots`, { method: "POST", json: body() });
            setPreview(null); await reload(); setSnapId(r.snapshot_id);
          })}>Freeze snapshot</button>
        </div>
      </Card>

      <Card title={preview ? "Preview (not persisted)" : "Snapshot"} actions={<>
        {!preview && (
          <select aria-label="Snapshot" className="input py-1 text-xs" value={snapId ?? ""} onChange={(e) => { setSnapId(e.target.value); setPreview(null); }}>
            {d.snapshots.map((x: any) => <option key={x.snapshot_id} value={x.snapshot_id}>{fmtDate(x.period_from)} → {fmtDate(x.period_to)} · {x.state} · cutoff #{x.record_cutoff_seq} · frozen {fmtDate(x.created_at, true)}</option>)}
          </select>)}
        {preview && <button className="btn-ghost" onClick={() => setPreview(null)}>Back to snapshots</button>}
        {!preview && snapId && <button className="btn-ghost" disabled={rep.busy} onClick={() => rep.run(() => api(`/snapshots/${snapId}/reproduce`))}>Reproduce</button>}
      </>}>
        <ErrorBox error={snap.error || rep.error} />
        <LiveRegion>{rep.result && <div className="mb-3"><Notice tone={rep.result.reproducible ? "good" : "warn"}>
          Rebuilt from record cutoff #{rep.result.record_cutoff_seq}: {rep.result.reproduced_state} (original {rep.result.original_state}) · dependency digest {rep.result.dependency_digest_match ? "matches" : "DIFFERS"} → {rep.result.reproducible ? "reproducible" : "not reproducible"}.
        </Notice></div>}</LiveRegion>
        {!view && <p className="text-sm text-slate-500">No snapshot yet. Preview or freeze one above.</p>}
        {view && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm">Whole-period state</span><Badge code={view.state} />
              <span className="text-xs text-slate-600">{fmtDate(period[0])} → {fmtDate(period[1])} (end exclusive)</span>
              {view.reason_codes?.map((r: string) => <span key={r} className="mono text-[11px] text-amber-700">{r}</span>)}
              <TreatmentList treatments={view.exception_treatments} />
            </div>
            <SegmentRibbon segments={view.segments} excluded={view.excluded_segments || []} period={period} onSelect={setSeg} selected={seg} />
            {view.precedence_explanation && <p className="text-xs text-slate-600">{view.precedence_explanation}</p>}
            <ProofGaps segments={view.segments} excluded={view.excluded_segments} onInspect={setSeg} />
            <KV cols={4} items={[["Perspective", <Badge key="p" code={view.perspective} tone="info" />], ["Record cutoff", <span key="c" className="mono">#{view.record_cutoff_seq}</span>],
              ["Frozen at", preview ? "Not frozen (preview)" : fmtDate(view.created_at, true)], ["Dependency digest", <Hash key="d" value={view.dependency_digest} />], ["Snapshot hash", <Hash key="h" value={view.snapshot_hash} />],
              ["Engine", <span key="e" className="mono text-xs">{view.rollup_engine_version}</span>], ["Segments", view.segments.length]]} />
            <Table head={["Segment", "State", "Expression", "Reasons", "Exceptions", "Coalesced"]}>
              {view.segments.map((x: any, i: number) => (
                <tr key={i} onClick={() => setSeg(i)} className={`cursor-pointer ${seg === i ? "bg-brand-50" : "hover:bg-navy-50"}`}>
                  <td className="td text-xs">{fmtDate(x.segment_range[0])} → {fmtDate(x.segment_range[1])}</td>
                  <td className="td"><Badge code={x.assurance_state} /></td><td className="td"><Badge code={x.expression_result} /></td>
                  <td className="td mono text-[11px]">{x.reason_codes.join(", ") || "—"}</td>
                  <td className="td">{x.exception_treatments.map((t: any) => <ExceptionBadge key={t.exception_id} state={t.state} />)}</td>
                  <td className="td text-xs">{x.coalesced_from ?? 1}</td>
                </tr>
              ))}
            </Table>
            {s && (
              <div className="grid gap-4 xl:grid-cols-3">
                <div className="xl:col-span-2 rounded-lg border border-navy-100 p-3">
                  <div className="mb-2 text-sm font-semibold text-navy-800">Expression trace · {fmtDate(s.segment_range[0])} → {fmtDate(s.segment_range[1])}</div>
                  <ul className="text-sm"><ExprTrace node={s.expression_trace} /></ul>
                </div>
                <div className="rounded-lg border border-navy-100 p-3">
                  <div className="mb-2 text-sm font-semibold text-navy-800">Control evaluations</div>
                  {s.leaf_evaluations.map((l: any) => (
                    <div key={l.control_evaluation_id + l.control_key} className="mb-2 text-xs">
                      <div className="flex items-center gap-2"><span className="mono">{l.control_key}</span><Badge code={l.control_state} /></div>
                      <div className="text-slate-500">rule {l.precedence_rule} {l.reason_codes.join(", ")}</div>
                    </div>
                  ))}
                  {s.exception_treatments.length > 0 && <Notice tone="warn">An approved exception is a governance treatment shown alongside the state. It never turns a gap into PROVEN.</Notice>}
                </div>
              </div>
            )}
            {view.dependency_manifest && <details><summary className="cursor-pointer text-xs text-navy-700">Dependency manifest</summary><Json value={view.dependency_manifest} /></details>}
          </div>
        )}
      </Card>
    </div>
  );
}

/* WF-STA-05 gap detector / WF-STA-01 proof gaps: the segments of this snapshot or preview that are not PROVEN, ranked by
   the roll-up precedence FAILING > REQUIRES_REVIEW > NOT_PROVEN (ZA-STA-001 STA-ADR-04), then by start. Intervals,
   states and reasons are the backend's own segments; only their length is computed here, and nothing is re-evaluated. */
const GAP_RANK: Record<string, number> = { FAILING: 0, REQUIRES_REVIEW: 1, NOT_PROVEN: 2 };

function fmtDuration(from: string, to: string) {
  const mins = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000);
  if (!Number.isFinite(mins) || mins < 0) return "—";
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  return d ? `${d} d${h ? ` ${h} h` : ""}` : h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`;
}

function ProofGaps({ segments, excluded, onInspect }: { segments: any[]; excluded?: any[]; onInspect: (i: number) => void }) {
  const gaps = segments.map((s, i) => ({ s, i })).filter(({ s }) => s.assurance_state !== "PROVEN")
    .sort((x, y) => (GAP_RANK[x.s.assurance_state] ?? 3) - (GAP_RANK[y.s.assurance_state] ?? 3) || String(x.s.segment_range[0]).localeCompare(String(y.s.segment_range[0])));
  return (
    <div className="rounded-lg border border-navy-100 p-3">
      <div className="mb-2 text-sm font-semibold text-navy-800">Proof gaps</div>
      {gaps.length === 0 ? <p className="text-xs text-slate-600">No segment in this period is below PROVEN.</p> : (
        <Table head={["Interval", "Duration", "State", "Reasons", "Exception treatment", ""]}>
          {gaps.map(({ s, i }) => (
            <tr key={i}>
              <td className="td whitespace-nowrap text-xs">{fmtDate(s.segment_range[0], true)} → {fmtDate(s.segment_range[1], true)}</td>
              <td className="td whitespace-nowrap text-xs">{fmtDuration(s.segment_range[0], s.segment_range[1])}</td>
              <td className="td"><Badge code={s.assurance_state} /></td>
              <td className="td mono text-[11px]">{s.reason_codes?.join(", ") || "—"}</td>
              <td className="td">{s.exception_treatments?.length ? s.exception_treatments.map((t: any) => <ExceptionBadge key={t.exception_id} state={t.state} />) : <span className="text-xs text-slate-500">None</span>}</td>
              <td className="td"><button type="button" className="btn-ghost text-xs" onClick={() => onInspect(i)}>Inspect segment</button></td>
            </tr>
          ))}
        </Table>
      )}
      {(excluded ?? []).length > 0 && (
        <p className="mt-2 text-xs text-slate-600">
          Not in assurance scope (obligation not applicable): {excluded!.map((x) => `${fmtDate(x.segment_range[0], true)} → ${fmtDate(x.segment_range[1], true)} (${fmtDuration(x.segment_range[0], x.segment_range[1])})`).join("; ")}.
        </p>
      )}
      <p className="mt-2 text-[11px] text-slate-500">An approved exception is shown beside a gap and never closes it.</p>
    </div>
  );
}

function Controls({ d }: { d: any }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Satisfaction expression versions">
        {d.expression_versions.map((e: any) => (
          <div key={e.expression_version_id} className="mb-3 rounded-lg border border-navy-100 p-3">
            <div className="mb-1 flex items-center gap-2 text-sm"><b>v{e.version}</b><span className="text-xs text-slate-500">from {fmtDate(e.valid_from)}</span><Hash value={e.ast_hash} n={8} /></div>
            {e.rationale && <p className="mb-2 text-xs text-slate-600">{e.rationale}</p>}
            <Json value={e.ast} max={220} />
          </div>
        ))}
      </Card>
      <Card title="Referenced controls (shared implementations)">
        <Table head={["Control", "Implementation", "Method"]}>
          {d.controls.map((c: any) => (
            <tr key={c.control_key}>
              <td className="td"><div className="mono text-xs">{c.control_key}</div><div className="text-[11px] text-slate-500">{c.title}</div></td>
              <td className="td">{c.implementation ? <A href={`/controls/${c.implementation.control_implementation_id}`}>open</A> : <Badge code="MISSING" />}</td>
              <td className="td text-xs">{c.implementation?.method ?? "—"}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function Occurrences({ d, lineage, reload }: { d: any; lineage: string; reload: () => void }) {
  const [wf, setWf] = useState("");
  const [due, setDue] = useState("");
  const [ful, setFul] = useState<any>(null);
  const a = useAction();
  const { can, why } = useCan();
  const canAdd = can("manage_controls") || can("manage_facts");
  const fid = useId();
  return (
    <Card title="Occurrences" subtitle="Each due occurrence must be fulfilled with evidence. Met, late or missed is decided in Period assurance; a missed occurrence is never overwritten.">
      <ErrorBox error={a.error} />
      <Table head={["Window", "Due", "Fulfilled", "Evidence", "Status", ""]} empty={!d.occurrences.length}>
        {d.occurrences.map((o: any) => (
          <tr key={o.occurrence_id}>
            <td className="td text-xs">{fmtDate(o.window_from)} →</td><td className="td text-xs">{fmtDate(o.due_at)}</td>
            <td className="td text-xs">{fmtDate(o.fulfilled_at)}</td>
            <td className="td">{(o.evidence_ids || []).map((e: string) => <div key={e}><A href={`/evidence/${e}`}>{e.slice(0, 8)}…</A></div>)}</td>
            <td className="td text-xs text-slate-600">{o.fulfilled_at ? "Fulfilment recorded" : "No fulfilment recorded"}</td>
            <td className="td">{!o.fulfilled_at && <button className="btn-ghost" disabled={!canAdd} title={canAdd ? undefined : why("manage_controls")} onClick={() => setFul(o)}>Record fulfilment</button>}</td>
          </tr>
        ))}
      </Table>
      <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-navy-100 pt-4">
        <div><label className="label" htmlFor={`${fid}-wf`}>Window from (required)</label><input id={`${fid}-wf`} type="date" required className="input" value={wf} onChange={(e) => setWf(e.target.value)} /></div>
        <div><label className="label" htmlFor={`${fid}-due`}>Due at (required)</label><input id={`${fid}-due`} type="date" required className="input" value={due} onChange={(e) => setDue(e.target.value)} /></div>
        <button className="btn-primary" disabled={a.busy || !canAdd || !wf || !due} title={canAdd ? undefined : why("manage_controls")} onClick={() => a.run(async () => {
          await api(`/obligations/${lineage}/occurrences`, { method: "POST", json: { window_from: new Date(wf).toISOString(), due_at: new Date(due).toISOString() } }); reload();
        })}>Add occurrence</button>
      </div>
      {ful && <Fulfil occ={ful} onClose={() => setFul(null)} onDone={() => { setFul(null); reload(); }} />}
    </Card>
  );
}

function Fulfil({ occ, onClose, onDone }: { occ: any; onClose: () => void; onDone: () => void }) {
  const { data: ev } = useApi<any[]>("/evidence");
  const [at, setAt] = useState(new Date().toISOString().slice(0, 10));
  const [sel, setSel] = useState<string[]>([]);
  const a = useAction();
  const fid = useId();
  return (
    <Modal open onClose={onClose} title={`Fulfil occurrence due ${fmtDate(occ.due_at)}`}>
      <ErrorBox error={a.error} />
      <label className="label" htmlFor={`${fid}-at`}>Fulfilled at</label><input id={`${fid}-at`} type="date" className="input mb-3" value={at} onChange={(e) => setAt(e.target.value)} />
      <fieldset>
        <legend className="label">Evidence (required, select at least one)</legend>
        <div className="max-h-60 overflow-y-auto rounded border border-navy-100 p-2">
          {ev?.map((e) => (
            <label key={e.evidence_id} className="flex items-center gap-2 py-1 text-sm">
              <input type="checkbox" checked={sel.includes(e.evidence_id)} onChange={(x) => setSel(x.target.checked ? [...sel, e.evidence_id] : sel.filter((y) => y !== e.evidence_id))} />
              {e.title} <Badge code={e.condition} /> <ManualProvenance origin={e.origin_class} />
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!sel.length || a.busy} onClick={() => a.run(async () => {
        await api(`/occurrences/${occ.occurrence_id}/fulfil`, { method: "POST", json: { fulfilled_at: new Date(at).toISOString(), evidence_ids: sel } }); onDone();
      })}>Record</button></div>
    </Modal>
  );
}

/** `name`: the dossier's actor-name resolver (useActorNames), passed down so opening this tab does not read the actor
 *  directory a second time. */
function ExcComp({ d, lineage, reload, name }: { d: any; lineage: string; reload: () => void; name: (id?: string | null) => string }) {
  const [exc, setExc] = useState({ reason: "", valid_from: "", valid_to: "" });
  const [comp, setComp] = useState({ primary_control_key: d.controls[0]?.control_key ?? "", compensating: "", rationale: "", valid_from: "" });
  const a = useAction();
  const c = useAction();
  const toast = useToast();
  const { can, why } = useCan();
  const canRequest = can("request_exception");
  const fid = useId();
  // Owner of the primary control's implementation as the dossier resolves it now; shown for reference, never compared.
  const primaryImpl = d.controls.find((c: any) => c.control_key === comp.primary_control_key)?.implementation;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Exceptions" subtitle="Time-bounded governance treatment, separate from assurance state.">
        <ErrorBox error={a.error} />
        <Table head={["Reason", "Validity", "State", "Requested / approved"]} empty={!d.exceptions.length}>
          {d.exceptions.map((x: any) => (
            <tr key={x.exception_id}><td className="td text-sm">{x.reason}</td><td className="td text-xs">{fmtDate(x.valid_from)} → {fmtDate(x.valid_to)}</td>
              <td className="td"><ExceptionBadge state={x.state} /></td><td className="td text-xs">{name(x.requested_by)} / {x.approved_by ? name(x.approved_by) : "—"}</td></tr>
          ))}
        </Table>
        <div className="mt-4 grid gap-2 border-t border-navy-100 pt-4 sm:grid-cols-2">
          <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-exc-reason`}>Request exception — reason (required)</label><input id={`${fid}-exc-reason`} required className="input" value={exc.reason} onChange={(e) => setExc({ ...exc, reason: e.target.value })} /></div>
          <div><label className="label" htmlFor={`${fid}-exc-from`}>From (required)</label><input id={`${fid}-exc-from`} type="date" required className="input" value={exc.valid_from} onChange={(e) => setExc({ ...exc, valid_from: e.target.value })} /></div>
          <div><label className="label" htmlFor={`${fid}-exc-to`}>To (required)</label><input id={`${fid}-exc-to`} type="date" required className="input" value={exc.valid_to} onChange={(e) => setExc({ ...exc, valid_to: e.target.value })} /></div>
          <div className="sm:col-span-2"><button className="btn-primary" disabled={!exc.reason.trim() || !exc.valid_from || !exc.valid_to || a.busy || !canRequest} title={why("request_exception")} onClick={() => a.run(async () => {
            await api("/exceptions", { method: "POST", json: { target_type: "OBLIGATION", target_id: lineage, reason: exc.reason.trim(), valid_from: new Date(exc.valid_from).toISOString(), valid_to: new Date(exc.valid_to).toISOString() } });
            setExc({ reason: "", valid_from: "", valid_to: "" }); toast("Exception requested. It now needs approval by someone other than you.", "good");
            reload();
          })}>{a.busy ? "Requesting…" : "Request exception"}</button> <A href="/governance">Approve in governance →</A></div>
        </div>
      </Card>
      <Card title="Compensating controls" subtitle="Approval creates a new satisfaction-expression version with an explicit COMPENSATION branch — never a manual green flag.">
        <ErrorBox error={c.error} />
        <Table head={["Primary", "Compensating", "Status", "Rationale", "Proposed / approved by"]} empty={!d.compensations.length}>
          {d.compensations.map((c: any) => (
            <tr key={c.compensation_id}><td className="td mono text-xs">{c.primary_control_key}</td>
              <td className="td mono text-xs">{JSON.stringify(c.compensating_expression.control_key ?? c.compensating_expression.children?.map((x: any) => x.control_key))}</td>
              <td className="td"><Badge code={c.status} /></td><td className="td text-xs">{c.equivalence_rationale}</td>
              <td className="td text-xs"><span className="text-slate-500">not recorded</span> / {c.approved_by ? name(c.approved_by) : "—"}</td></tr>
          ))}
        </Table>
        <div className="mt-4 grid gap-2 border-t border-navy-100 pt-4 sm:grid-cols-2">
          <div><label className="label" htmlFor={`${fid}-comp-primary`}>Primary control</label><select id={`${fid}-comp-primary`} className="input" aria-describedby={`${fid}-comp-owner`} value={comp.primary_control_key} onChange={(e) => setComp({ ...comp, primary_control_key: e.target.value })}>
            {d.controls.map((c: any) => <option key={c.control_key}>{c.control_key}</option>)}</select>
            <p id={`${fid}-comp-owner`} className="mt-1 text-[11px] text-slate-500">{primaryImpl
              ? primaryImpl.owner_actor_id ? `Owner of its current implementation: ${name(primaryImpl.owner_actor_id)}` : "Its current implementation has no recorded owner."
              : "No current implementation was found for this control."}</p></div>
          <div><label className="label" htmlFor={`${fid}-comp-keys`}>Compensating keys (comma, required)</label><input id={`${fid}-comp-keys`} required className="input" value={comp.compensating} onChange={(e) => setComp({ ...comp, compensating: e.target.value })} /></div>
          <div className="sm:col-span-2"><label className="label" htmlFor={`${fid}-comp-rationale`}>Equivalence rationale (required, ≥ 20 chars)</label><textarea id={`${fid}-comp-rationale`} required className="input" rows={2} value={comp.rationale} onChange={(e) => setComp({ ...comp, rationale: e.target.value })} /></div>
          <div className="sm:col-span-2"><CompensationSoDNote id={`${fid}-comp-sod`} stage="propose" /></div>
          <div><label className="label" htmlFor={`${fid}-comp-from`}>Valid from (required)</label><input id={`${fid}-comp-from`} type="date" required className="input" value={comp.valid_from} onChange={(e) => setComp({ ...comp, valid_from: e.target.value })} /></div>
          <div className="flex items-end"><button className="btn-primary" aria-describedby={`${fid}-comp-sod`} disabled={!comp.compensating.trim() || !comp.valid_from || c.busy || !canRequest} title={why("request_exception")} onClick={() => c.run(async () => {
            await api("/compensations", { method: "POST", json: { obligation_lineage_id: lineage, primary_control_key: comp.primary_control_key,
              compensating_control_keys: comp.compensating.split(",").map((x) => x.trim()).filter(Boolean), equivalence_rationale: comp.rationale, valid_from: new Date(comp.valid_from).toISOString() } });
            setComp({ ...comp, compensating: "", rationale: "", valid_from: "" }); toast("Compensating control proposed for approval.", "good");
            reload();
          })}>{c.busy ? "Proposing…" : "Propose compensation"}</button></div>
        </div>
      </Card>
    </div>
  );
}
