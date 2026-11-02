"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, ErrorBox, PageHeader, Table } from "@/components/ui";

const OUTCOMES = ["APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"];

export default function ApplicabilityTrace() {
  const evals = useApi<any[]>("/applicability/evaluations");
  const [subject, setSubject] = useState("");
  const [obligation, setObligation] = useState("");
  const [outcome, setOutcome] = useState("");
  const all = evals.data ?? [];
  const subjects = [...new Set(all.map((e) => e.subject.display_name))].sort();
  const obligations = [...new Set(all.map((e) => e.obligation_definition.key))].sort();
  const rows = all.filter((e) => (!subject || e.subject.display_name === subject)
    && (!obligation || e.obligation_definition.key === obligation) && (!outcome || e.outcome === outcome));

  return (
    <>
      <PageHeader spec="WF-DOM-03 · ZA-APP-001 §7" title="Applicability Trace"
        subtitle="Every evaluation is an immutable record bound to a frozen input bundle, rule version and record cutoff. Open one to see each predicate, the exact fact revision it used, and the tri-valued result." />
      <ErrorBox error={evals.error} />
      <Card title={`${rows.length} evaluation(s)`} actions={<>
        <select aria-label="Filter by subject" className="input w-44 py-1 text-xs" value={subject} onChange={(e) => setSubject(e.target.value)}>
          <option value="">All subjects</option>{subjects.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select aria-label="Filter by obligation" className="input w-52 py-1 text-xs" value={obligation} onChange={(e) => setObligation(e.target.value)}>
          <option value="">All obligations</option>{obligations.map((o) => <option key={o}>{o}</option>)}
        </select>
        <select aria-label="Filter by outcome" className="input w-40 py-1 text-xs" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="">All outcomes</option>{OUTCOMES.map((o) => <option key={o}>{o}</option>)}
        </select>
      </>}>
        <Table head={["Subject", "Obligation", "Outcome", "Current determination", "Effective at", "Cutoff", "Rule", "Trigger", "Missing facts"]} empty={evals.data !== null && rows.length === 0}>
          {rows.map((e) => (
            <tr key={e.evaluation_id}>
              <td className="td">{e.subject.display_name}</td>
              <td className="td"><A href={`/applicability/${e.evaluation_id}`}>{e.obligation_definition.key}</A></td>
              <td className="td"><Badge code={e.outcome} /></td>
              <td className="td">{e.current_determination ? <><Badge code={e.current_determination.state} />
                {e.current_determination.evaluation_id !== e.evaluation_id && <div className="text-[11px] text-slate-500">based on another evaluation</div>}</> : "—"}</td>
              <td className="td text-xs">{fmtDate(e.effective_at, true)}</td>
              <td className="td mono text-xs">#{e.record_cutoff_seq}</td>
              <td className="td mono text-xs">v{e.rule_version.version}</td>
              <td className="td mono text-[11px]">{e.trigger}</td>
              <td className="td text-xs">{(e.missing_facts || []).map((m: any) => (typeof m === "string" ? m : `${m.fact_definition ?? m.fact} (${m.reason_code ?? m.reason})`)).join(", ") || "—"}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}
