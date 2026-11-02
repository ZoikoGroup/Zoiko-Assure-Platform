"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Card, ErrorBox, Notice, Table, useAction } from "./ui";
import { RuleTrace } from "./Trace";

/* Rule test views shared by the draft page and the Rule Test Workbench (ZA-RUL-001 WF-RUL-04, RUL-REQ-015/016). */

const OUTCOMES = ["APPLIES", "DOES_NOT_APPLY", "UNCERTAIN"];

/** Golden-vector regression report stored on the draft by POST /rules/drafts/{id}/test. */
export function VectorReport({ rep }: { rep: any }) {
  if (!rep) return <Notice>Golden vectors not run yet.</Notice>;
  return (
    <Card title={`Golden vectors · ${rep.passed}/${rep.total} passed`} actions={<Badge code={rep.all_passed ? "PASS" : "FAIL"} />}>
      {rep.missing_mandatory_classes?.length > 0 && <Notice tone="warn">Missing mandatory classes: {rep.missing_mandatory_classes.join(", ")}</Notice>}
      <div className="mt-3">
        <Table head={["Vector", "Expected", "Actual", "Result", "Trace mismatches"]}>
          {rep.results.map((r: any) => (
            <tr key={r.vector_id}>
              <td className="td"><div className="mono text-xs">{r.vector_id}</div><div className="text-[11px] text-slate-500">{r.description}</div></td>
              <td className="td"><Badge code={r.expected} /></td><td className="td">{r.actual ? <Badge code={r.actual} /> : <Badge code={r.error} tone="info" title={r.message ?? r.error} />}</td>
              <td className="td"><Badge code={r.passed ? "PASS" : "FAIL"} /></td>
              <td className="td text-xs">{r.message || r.trace_mismatches?.map((m: any) => `${m.node_id}: ${m.expected}≠${m.got}`).join("; ")}</td>
            </tr>
          ))}
        </Table>
      </div>
    </Card>
  );
}

export type FixtureVector = { facts: Record<string, unknown>; effective_at: string; expected_outcome: string };

/** Runs the exact draft artifact on synthetic facts only (POST /rules/drafts/{id}/sandbox); no customer data access.
 *  With `onSaveVector`, a run with an expected result can be kept as a golden vector (WF-RUL-04 "Save vector"). */
export function RuleSandbox({ id, compare = false, onSaveVector }: { id: string; compare?: boolean; onSaveVector?: (v: FixtureVector) => void }) {
  const [facts, setFacts] = useState('{\n  "entity.established_in_eu": true,\n  "ai.role": "DEPLOYER",\n  "ai.high_risk_basis": {"unknown": "MISSING"}\n}');
  const [at, setAt] = useState("2027-12-02");
  const [expected, setExpected] = useState("");
  const [ran, setRan] = useState<{ facts: Record<string, unknown>; at: string } | null>(null);  // the fixture behind the shown result
  const a = useAction();
  const outcome = a.result?.outcome;
  const run = () => a.run(async () => {
    const parsed = JSON.parse(facts);
    const r = await api(`/rules/drafts/${id}/sandbox`, { method: "POST", json: { facts: parsed, effective_at: at } });
    setRan({ facts: parsed, at });
    return r;
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Synthetic fixture" subtitle="Runs the exact draft artifact on synthetic facts only — no customer data access. Use {&quot;unknown&quot;: REASON} for unknown facts."
        actions={<button className="btn-primary" disabled={a.busy} onClick={run}>Run</button>}>
        <ErrorBox error={a.error} />
        <div className={`mb-3 grid gap-3 ${compare ? "sm:grid-cols-2" : ""}`}>
          <div><label className="label" htmlFor={`sb-at-${id}`}>Effective at</label><input id={`sb-at-${id}`} type="date" className="input" value={at} onChange={(e) => setAt(e.target.value)} /></div>
          {compare && (
            <div><label className="label" htmlFor={`sb-exp-${id}`}>Expected result</label>
              <select id={`sb-exp-${id}`} className="input" value={expected} onChange={(e) => setExpected(e.target.value)}>
                <option value="">No expectation</option>{OUTCOMES.map((o) => <option key={o}>{o}</option>)}
              </select></div>
          )}
        </div>
        <textarea aria-label="Synthetic facts (JSON)" className="input mono h-64 text-[11px]" value={facts} onChange={(e) => setFacts(e.target.value)} spellCheck={false} />
      </Card>
      <Card title="Result & trace" actions={a.result && <>
        <Badge code={outcome} />
        {compare && expected && <Badge tone={outcome === expected ? "good" : "bad"} label={outcome === expected ? "MATCHES EXPECTED" : `EXPECTED ${expected}`} />}
      </>}>
        {a.result ? (
          <>
            <p className="mb-2 text-[11px] text-slate-500">Evaluator <span className="mono">{a.result.evaluator_build}</span> · draft revision r{a.result.draft_revision}</p>
            <ul><RuleTrace node={a.result.trace} /></ul>
            {onSaveVector && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
                <span className="text-[12px] text-slate-500">{expected ? "Keep this fixture and its expected result as a golden vector." : "Choose an expected result to save this fixture as a golden vector."}</span>
                <button className="btn-ghost" disabled={!expected || !ran} onClick={() => ran && onSaveVector({ facts: ran.facts, effective_at: ran.at, expected_outcome: expected })}>Save as golden vector</button>
              </div>
            )}
          </>
        ) : <p className="text-sm text-slate-500">Run the sandbox to see the trace.</p>}
      </Card>
    </div>
  );
}
