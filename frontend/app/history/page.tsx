"use client";

import { useId, useState, type ReactNode } from "react";
import { api, describeDetail, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, ErrorBox, Hash, Json, KV, LiveRegion, Notice, PageHeader, Table, useAction } from "@/components/ui";
import { RuleTrace } from "@/components/Trace";

const SIDES = ["left", "right"] as const;
type Side = (typeof SIDES)[number];
const SIDE_NAME: Record<Side, string> = { left: "Left", right: "Right" };
const isSeq = (s: string) => /^\d+$/.test(s);

/* What each type means as /history/compare computes it (rule version IDs, and the fact revision each trace reached),
   so the legend claims no more than the comparison checked. Types not listed are shown as returned. */
const DIFF_MEANING: Record<string, string> = {
  LEGAL_VERSION_CHANGED: "The two sides resolved different published rule versions.",
  KNOWLEDGE_ADDED: "The left side had no known revision of this fact (unknown, or not reached in its trace), and the right side differs.",
  KNOWLEDGE_CORRECTED: "The left side had a known revision of this fact; the right side has a different revision, is unknown, or did not reach it.",
  NO_SEMANTIC_CHANGE: "Same rule version, and the same revision for every fact in both traces.",
};

/** FACT_REF trace nodes by fact key, last occurrence winning, as the comparison service reads them. */
function factNodes(node: any, out: Record<string, any> = {}): Record<string, any> {
  if (!node) return out;
  if (node.node_type === "FACT_REF") out[node.fact] = node;
  for (const c of node.children || []) factNodes(c, out);
  return out;
}

export default function History() {
  const { data: subs } = useApi<any[]>("/org/subjects");
  const { data: ods } = useApi<any[]>("/catalog/obligation-definitions");
  const { data: wm, error: wmError } = useApi<any>("/watermark");
  const [f, setF] = useState({ subject_id: "", obligation_definition_id: "", valid_at: "", left_seq: "", right_seq: "" });
  const a = useAction();
  const fid = useId();
  const right = f.right_seq || String(wm?.record_seq ?? "");
  const badLeft = !!f.left_seq && !isSeq(f.left_seq);
  const badRight = !!f.right_seq && !isSeq(f.right_seq);
  const odsForSubject = (ods || []).filter((o) => !f.subject_id || o.subject_type === subs?.find((s) => s.subject_id === f.subject_id)?.subject_type);
  // The result keeps the coordinates it ran with, so editing the form never relabels it.
  const compare = () => a.run(async () => {
    const q = { form: f, right_seq: right, subject: subs?.find((s) => s.subject_id === f.subject_id)?.display_name,
      obligation: ods?.find((o) => o.obligation_definition_id === f.obligation_definition_id)?.key };
    const qs = new URLSearchParams({ subject_id: f.subject_id, obligation_definition_id: f.obligation_definition_id,
      valid_at: new Date(f.valid_at).toISOString(), left_seq: f.left_seq, right_seq: right });
    return { q, data: await api(`/history/compare?${qs}`) };
  });
  const q = a.result?.q;
  const r = a.result?.data;
  const edited = !!q && (Object.keys(f) as (keyof typeof f)[]).some((k) => f[k] !== q.form[k]);
  const failed = r ? SIDES.filter((s) => r[s].error) : [];
  const diffs: any[] = r?.differences ?? [];
  const classified = diffs.filter((d) => d.type !== "NO_SEMANTIC_CHANGE");
  const summary = !r ? "" : failed.length ? `${failed.length === 2 ? "Neither side" : `The ${failed[0]} side`} could not be evaluated.`
    : classified.length ? `${classified.length} classified difference${classified.length === 1 ? "" : "s"}.` : "No semantic change was found.";
  return (
    <>
      <PageHeader spec="ZA-TIM-001 §12 · WF-TIM-02" title="Historical Reconstruction"
        subtitle="Pin one valid time and compare the applicability evaluation at two record cutoffs. The two sides are shown separately, and differences are classified, not blended." />
      <div className="mb-4">
        <Notice>
          Applicability only. Each side is evaluated on request from the published rule version and the fact revisions recorded by its cutoff, and nothing is saved.
          It is not a stored record of an evaluation made at that time, and it does not compare determinations, obligations, evidence or assurance state.
        </Notice>
      </div>
      <Card title="Comparison coordinates">
        <ErrorBox error={a.error} />
        <div className="grid gap-3 sm:grid-cols-5">
          <div><label className="label" htmlFor={`${fid}-subject`}>Subject (required)</label><select id={`${fid}-subject`} required className="input" value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value, obligation_definition_id: "" })}><option value="">Select…</option>
            {subs?.map((s) => <option key={s.subject_id} value={s.subject_id}>{s.display_name}</option>)}</select></div>
          <div><label className="label" htmlFor={`${fid}-od`}>Obligation definition (required)</label><select id={`${fid}-od`} required className="input" value={f.obligation_definition_id} onChange={(e) => setF({ ...f, obligation_definition_id: e.target.value })}><option value="">Select…</option>
            {odsForSubject.map((o) => <option key={o.obligation_definition_id} value={o.obligation_definition_id}>{o.key}</option>)}</select></div>
          <div><label className="label" htmlFor={`${fid}-valid`}>Valid time (required)</label><input id={`${fid}-valid`} type="date" required className="input" value={f.valid_at} onChange={(e) => setF({ ...f, valid_at: e.target.value })} /></div>
          <div><label className="label" htmlFor={`${fid}-left`}>Left record cutoff (required)</label>
            <input id={`${fid}-left`} type="number" min={0} step={1} required className="input" value={f.left_seq} aria-invalid={badLeft || undefined}
              aria-describedby={`${fid}-seq-hint${badLeft ? ` ${fid}-left-err` : ""}`} onChange={(e) => setF({ ...f, left_seq: e.target.value })} />
            {badLeft && <p id={`${fid}-left-err`} className="mt-1 text-xs text-red-700">Enter a whole record sequence number.</p>}</div>
          <div><label className="label" htmlFor={`${fid}-right`}>Right record cutoff{wm ? ` (blank = current #${wm.record_seq})` : wmError ? " (required)" : ""}</label>
            <input id={`${fid}-right`} type="number" min={0} step={1} required={!wm} className="input" value={f.right_seq} aria-invalid={badRight || undefined}
              aria-describedby={`${fid}-seq-hint${badRight ? ` ${fid}-right-err` : ""}`} onChange={(e) => setF({ ...f, right_seq: e.target.value })} />
            {badRight && <p id={`${fid}-right-err`} className="mt-1 text-xs text-red-700">Enter a whole record sequence number.</p>}</div>
        </div>
        <p id={`${fid}-seq-hint`} className="mt-2 text-xs text-slate-500">
          Record cutoffs are record sequence numbers{wm ? ` in cell ${wm.cell_id}` : ""}. Put the earlier cutoff on the left.
          {wm ? " Leave the right cutoff blank to use the current record watermark." : wmError ? " The current record watermark could not be loaded, so enter a right cutoff." : ""}
        </p>
        <button className="btn-primary mt-4" disabled={!f.subject_id || !f.obligation_definition_id || !f.valid_at || !isSeq(f.left_seq) || !isSeq(right) || a.busy}
          onClick={compare}>Compare</button>
      </Card>
      <LiveRegion>{summary && <span className="sr-only">Comparison ready. {summary}</span>}</LiveRegion>
      {r && q && (
        <div className="mt-4 space-y-4">
          {edited && <Notice>The coordinates above have changed since this comparison ran. The result below still shows the coordinates it ran with; select Compare to update it.</Notice>}
          <Card title="Compared" subtitle="Computed on request and not saved.">
            <KV cols={4} items={[
              ["Subject", q.subject ?? <Hash value={q.form.subject_id} n={8} />],
              ["Obligation definition", <span key="o" className="mono text-xs">{q.obligation ?? q.form.obligation_definition_id}</span>],
              ["Valid time", <span key="v">{fmtDate(r.valid_at)} <span className="mono text-[11px] text-slate-500">{String(r.valid_at)}</span></span>],
              ["Record cutoffs", <span key="c" className="mono">Left #{r.left.known_seq} · Right #{r.right.known_seq}</span>],
            ]} />
            {Number(r.left.known_seq) > Number(r.right.known_seq) && (
              <div className="mt-3"><Notice>The left cutoff is later than the right cutoff. Differences are classified from left to right, so read them in that direction.</Notice></div>
            )}
            {Number(r.left.known_seq) === Number(r.right.known_seq) && (
              <div className="mt-3"><Notice>Both cutoffs are the same, so both sides use the same recorded knowledge.</Notice></div>
            )}
          </Card>
          <SideBySide r={r} />
          <Differences r={r} diffs={diffs} classified={classified} />
          <div className="grid gap-4 xl:grid-cols-2">
            {SIDES.map((side) => (
              <Card key={side} title={`${SIDE_NAME[side]} · record cutoff #${r[side].known_seq}`}
                subtitle={`Valid ${fmtDate(r.valid_at)} · ${side === "right" && !q.form.right_seq ? "the current record watermark when compared" : "entered cutoff"}`}
                actions={r[side].outcome && <Badge code={r[side].outcome} />}>
                {r[side].error ? <Notice tone="warn">{describeDetail(r[side].error) || "This side could not be evaluated."}</Notice> : (
                  <>
                    <KV cols={2} items={[["Rule version", <RuleVersionRef key="r" id={r[side].rule_version_id} />], ["Input bundle", <Hash key="b" value={r[side].input_bundle_hash} n={16} />]]} />
                    <ul className="mt-3 text-sm"><RuleTrace node={r[side].trace} /></ul>
                  </>
                )}
              </Card>
            ))}
          </div>
          <details className="card px-4 py-3"><summary className="cursor-pointer text-xs text-navy-700">Raw comparison response</summary><div className="mt-2"><Json value={r} /></div></details>
        </div>
      )}
    </>
  );
}

/** Left and right knowledge state in one table. Only the returned fields are compared; a side that could not be
 *  evaluated makes its rows not comparable rather than different. */
function SideBySide({ r }: { r: any }) {
  const ok = !r.left.error && !r.right.error;
  const facts = (s: any) => (s.missing_facts?.length ? s.missing_facts.join(", ") : "None");
  const rows: [string, (s: any) => unknown, (s: any) => ReactNode][] = [
    ["Outcome", (s) => s.outcome, (s) => (s.outcome ? <Badge code={s.outcome} /> : <span className="text-slate-500">Not returned</span>)],
    ["Rule version", (s) => s.rule_version_id, (s) => <RuleVersionRef id={s.rule_version_id} />],
    ["Material unknown facts", facts, (s) => <span className="mono text-xs">{facts(s)}</span>],
  ];
  return (
    <Card title="Left and right" subtitle="Each side's result at its own record cutoff, for the same valid time.">
      <Table head={["Item", `Left · #${r.left.known_seq}`, `Right · #${r.right.known_seq}`, "Comparison"]}>
        {rows.map(([label, key, show]) => {
          const cmp = !ok || key(r.left) == null || key(r.right) == null ? "Not comparable" : key(r.left) === key(r.right) ? "Same" : "Differs";
          return (
            <tr key={label}>
              <td className="td text-xs font-medium text-slate-600">{label}</td>
              {SIDES.map((side) => <td key={side} className="td">{r[side].error ? <span className="text-xs text-slate-500">Not available</span> : show(r[side])}</td>)}
              <td className={`td text-xs ${cmp === "Differs" ? "font-semibold text-slate-900" : "text-slate-500"}`}>{cmp}</td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}

function Differences({ r, diffs, classified }: { r: any; diffs: any[]; classified: any[] }) {
  const ok = !r.left.error && !r.right.error;
  const facts = { left: factNodes(r.left.trace), right: factNodes(r.right.trace) };
  const types = Array.from(new Set(classified.map((d) => d.type as string)));
  return (
    <Card title="Classified differences" subtitle="As classified by the comparison service. Left and right show the exact revision each side used.">
      {!ok ? <Notice>Differences are classified only when both sides can be evaluated. See the side details below.</Notice>
        : !classified.length ? <Notice>{diffs.some((d) => d.type === "NO_SEMANTIC_CHANGE") ? `No semantic change. ${DIFF_MEANING.NO_SEMANTIC_CHANGE}` : "The comparison returned no differences."}</Notice>
        : (
          <>
            <Table head={["Type", "Changed item", "Left", "Right"]}>
              {classified.map((d, i) => (
                <tr key={i}>
                  <td className="td"><Badge code={d.type} tone={d.type in DIFF_MEANING ? "info" : undefined} /></td>
                  {d.type === "LEGAL_VERSION_CHANGED" ? (
                    <><td className="td text-xs">Rule version</td><td className="td"><RuleVersionRef id={d.from} /></td><td className="td"><RuleVersionRef id={d.to} /></td></>
                  ) : d.fact ? (
                    <><td className="td mono text-xs">{d.fact}</td><td className="td"><FactValue node={facts.left[d.fact]} /></td><td className="td"><FactValue node={facts.right[d.fact]} /></td></>
                  ) : (
                    <><td className="td mono text-xs">{d.object ?? "—"}</td><td className="td mono text-[11px]">{raw(d.left ?? d.from)}</td><td className="td mono text-[11px]">{raw(d.right ?? d.to)}</td></>
                  )}
                </tr>
              ))}
            </Table>
            <dl className="mt-3 space-y-1 text-xs text-slate-600">
              {types.map((t) => (
                <div key={t}><dt className="mono inline font-medium text-slate-700">{t}</dt><dd className="inline">: {DIFF_MEANING[t] ?? "No description in this version of the interface; shown as returned."}</dd></div>
              ))}
            </dl>
          </>
        )}
    </Card>
  );
}

const raw = (v: unknown) => (v == null ? "—" : typeof v === "string" ? v : JSON.stringify(v));

/** One side's value for a fact: the displayed value and revision it used, unknown with its reason, or not reached. */
function FactValue({ node }: { node?: any }) {
  if (!node) return <span className="text-xs text-slate-500">Not reached in this trace</span>;
  if (node.result === "UNKNOWN") {
    return <span className="text-xs text-slate-600">Unknown{node.reason_code && <span className="mono ml-1 text-[11px] text-slate-500">{node.reason_code}</span>}</span>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="mono text-xs text-slate-900">{raw(node.display_value)}</span>
      {node.fact_revision_id && <span className="text-[11px] text-slate-500">rev <Hash value={node.fact_revision_id} n={8} /></span>}
    </span>
  );
}

/** A rule version by ID, with its version number, validity and record sequence when the version can be read. */
function RuleVersionRef({ id }: { id?: string | null }) {
  const { data } = useApi<any>(id ? `/rules/versions/${id}` : null);
  if (!id) return <span className="text-xs text-slate-500">Not available</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {data && <span className="text-xs">v{data.version} · valid {fmtDate(data.valid_from)} → {data.valid_to ? fmtDate(data.valid_to) : "open"} · recorded #{data.record_seq}</span>}
      <Hash value={id} n={8} />
    </span>
  );
}
