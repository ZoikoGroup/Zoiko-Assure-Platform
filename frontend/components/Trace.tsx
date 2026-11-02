"use client";

import { useState } from "react";
import { Badge, ExceptionBadge } from "./ui";
import { fmtDate } from "@/lib/format";

/* In a trace, NOT_EVALUATED marks a node skipped by short-circuit: not needed for the result, so informational here
   rather than the warning it is as a control state. Other node results take their shared tone (TRUE info, FALSE neutral). */
const traceTone = (result?: string) => (result === "NOT_EVALUATED" ? "info" : undefined);

/* Applicability trace tree (ZA-APP-001 §7) — accessible nested list, decisive-path filter. */
export function RuleTrace({ node, depth = 0, decisiveOnly = false }: { node: any; depth?: number; decisiveOnly?: boolean }) {
  const [open, setOpen] = useState(depth < 3);
  if (!node) return null;
  const kids: any[] = node.children || [];
  const label =
    node.node_type === "PREDICATE" ? (
      <>
        <span className="mono font-semibold text-navy-700">{node.operator}</span>
        {kids.map((k: any, i: number) => (
          <span key={i} className="mono text-xs text-slate-600">
            {i === 0 ? "(" : ", "}
            {k.fact ? <span className="text-navy-800">{k.fact}</span> : k.set_version_id ? <span className="text-brand-700">{k.set_version_id}</span> : JSON.stringify(k.display_value)}
            {k.fact && k.display_value !== undefined && <span className="text-emerald-700"> = {JSON.stringify(k.display_value)}</span>}
            {k.fact && k.result === "UNKNOWN" && <span className="text-amber-700"> = ?</span>}
            {i === kids.length - 1 ? ")" : ""}
          </span>
        ))}
      </>
    ) : (
      <span className="mono font-semibold text-navy-700">{node.node_type}{node.row_id ? ` · row ${node.row_id}` : ""}</span>
    );
  const showKids = node.node_type !== "PREDICATE" && kids.length > 0;
  const visibleKids = decisiveOnly ? kids.filter((k) => k.result !== "NOT_EVALUATED") : kids;
  return (
    <li className="my-1">
      <div className="flex flex-wrap items-center gap-2 rounded-md px-2 py-1 hover:bg-navy-50">
        {showKids ? (
          <button type="button" className="w-4 text-xs text-slate-500" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${node.node_type} sub-conditions`}><span aria-hidden>{open ? "▾" : "▸"}</span></button>
        ) : <span className="w-4" />}
        <Badge code={node.result} tone={traceTone(node.result)} />
        {label}
        {node.fact_revision_id && <span className="mono text-[11px] text-slate-500">rev {String(node.fact_revision_id).slice(0, 8)}</span>}
        {node.children?.find?.((c: any) => c.fact_revision_id) && <span className="mono text-[11px] text-slate-500">rev {String(node.children.find((c: any) => c.fact_revision_id).fact_revision_id).slice(0, 8)}</span>}
        {node.reason_code && <span className="mono text-[11px] text-amber-700">{node.reason_code}</span>}
        {node.short_circuit_reason && <span className="text-[11px] italic text-slate-500">short-circuit</span>}
        {node.source_mappings?.length > 0 && <span className="text-[11px] text-brand-700" title={JSON.stringify(node.source_mappings)}>§ {node.source_mappings.map((s: any) => (typeof s === "string" ? s : s.source_mapping_id)).join(", ")}</span>}
        <span className="mono text-[11px] text-slate-500">#{node.node_id}</span>
      </div>
      {showKids && open && (
        <ul className="ml-5 border-l border-navy-100 pl-2">
          {visibleKids.map((k: any) => <RuleTrace key={k.node_id} node={k} depth={depth + 1} decisiveOnly={decisiveOnly} />)}
        </ul>
      )}
    </li>
  );
}

/* Satisfaction-expression trace (ZA-STA-001 §16). */
export function ExprTrace({ node, depth = 0 }: { node: any; depth?: number }) {
  if (!node) return null;
  const kids: any[] = node.children || [];
  return (
    <li className="my-1">
      <div className="flex flex-wrap items-center gap-2 rounded-md px-2 py-1 hover:bg-navy-50">
        <Badge code={node.result} tone={traceTone(node.result)} />
        <span className="mono text-sm font-semibold text-navy-700">{node.node_type}{node.k ? `(${node.k})` : ""}</span>
        {node.control_key && <span className="mono text-sm text-navy-900">{node.control_key}</span>}
        {node.control_state && <Badge code={node.control_state} />}
        {node.reason_codes?.map((r: string) => <span key={r} className="mono text-[11px] text-amber-700">{r}</span>)}
        {node.satisfied_via_compensation && <Badge tone="exception" label="satisfied via approved compensation" />}
        {node.compensation && <span className="text-[11px] text-slate-500">compensation {node.compensation.valid ? "valid" : `invalid (${node.compensation.reason})`}</span>}
        {node.compensation_invalid && <span className="text-[11px] text-red-700">compensation not applicable: {node.compensation_invalid.reason}</span>}
        {node.short_circuit_reason && <span className="text-[11px] italic text-slate-500">{node.short_circuit_reason}</span>}
        {node.occurrences && node.occurrences.map((o: any) => <span key={o.occurrence_id} className="text-[11px] text-slate-600">occurrence due {fmtDate(o.due_at)} → {o.result}{o.reason ? ` (${o.reason})` : ""}</span>)}
        {node.note && <span className="text-[11px] text-slate-500">{node.note}</span>}
      </div>
      {kids.length > 0 && (
        <ul className="ml-5 border-l border-navy-100 pl-2">{kids.map((k, i) => <ExprTrace key={(k.node_id || "") + i} node={k} depth={depth + 1} />)}</ul>
      )}
    </li>
  );
}

/* Segment ribbon (WF-STA-05 / WF-TIM-04): proportional, labeled, with tabular fallback. */
const SEG_CLS: Record<string, string> = {
  PROVEN: "bg-emerald-100 text-emerald-900", NOT_PROVEN: "bg-amber-100 text-amber-900", FAILING: "bg-red-100 text-red-900",
  REQUIRES_REVIEW: "bg-violet-100 text-violet-900", EXCLUDED: "bg-slate-100 text-slate-600",
};
export function SegmentRibbon({ segments, excluded = [], period, onSelect, selected }: { segments: any[]; excluded?: any[]; period: [string, string]; onSelect?: (i: number) => void; selected?: number }) {
  const start = new Date(period[0]).getTime();
  const end = new Date(period[1]).getTime();
  const all = [
    ...segments.map((s, i) => ({ ...s, idx: i, kind: s.assurance_state })),
    ...excluded.map((s) => ({ ...s, idx: -1, kind: "EXCLUDED" })),
  ].sort((a, b) => new Date(a.segment_range[0]).getTime() - new Date(b.segment_range[0]).getTime());
  return (
    <div>
      <div className="flex h-7 w-full overflow-hidden rounded-md ring-1 ring-slate-200" role="group" aria-label="Assurance period segments">
        {all.map((s, i) => {
          const w = ((new Date(s.segment_range[1]).getTime() - new Date(s.segment_range[0]).getTime()) / (end - start)) * 100;
          const name = `${s.kind}: ${fmtDate(s.segment_range[0])} → ${fmtDate(s.segment_range[1])}`;
          return (
            <button key={i} type="button" disabled={s.idx < 0} onClick={() => onSelect?.(s.idx)} aria-label={name} aria-pressed={s.idx >= 0 ? selected === s.idx : undefined}
              title={name}
              style={{ width: `${w}%` }}
              className={`relative h-full border-r border-white text-[10px] font-medium mono ${SEG_CLS[s.kind]} ${selected === s.idx ? "ring-2 ring-inset ring-navy-700" : ""}`}>
              <span className="absolute inset-0 flex items-center justify-center truncate px-1">{w > 12 ? (s.kind === "EXCLUDED" ? "not applicable" : s.kind) : ""}</span>
              {s.exception_treatments?.length > 0 && <span className="absolute right-1 top-0.5 text-[11px] text-brand-700" title="Exception treatment applies (state unchanged)">⚠</span>}
            </button>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-slate-500">
        <span>{fmtDate(period[0])}</span>
        <span>{fmtDate(period[1])}</span>
      </div>
      <table className="sr-only">
        <caption>Segments</caption>
        <tbody>{all.map((s, i) => <tr key={i}><td>{s.segment_range[0]}</td><td>{s.segment_range[1]}</td><td>{s.kind}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

export function TreatmentList({ treatments }: { treatments: any[] }) {
  if (!treatments?.length) return <span className="text-sm text-slate-500">No exception treatment</span>;
  return (
    <div className="flex flex-wrap gap-2">
      {treatments.map((t) => <ExceptionBadge key={t.exception_id} state={t.state} until={t.valid?.[1]} />)}
    </div>
  );
}
