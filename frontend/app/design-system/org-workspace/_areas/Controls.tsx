"use client";

import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  Alert, Button, Card, ConfirmDialog, DataTable, DescriptionList, Field, SearchInput, Select, StateBadge, TBody, THead, TabPanel, Table, Tabs, Td, Th, Tr,
  statusOfCode, useToast, type DataColumn,
} from "@/components/ds";
import { fmtDate, fmtRange } from "@/lib/format";
import { DemoFrame, Sample, Section, delay, now } from "../../_shared/demo";
import { CONTROLS, EVIDENCE, OBLIGATIONS, entityName, type Control, type Run } from "./data";
import { AreaLink, Proposed, SPLIT, useDetailPanel, useSelection } from "./shared";

/* Controls & tests, mirroring Controls & Tests (app/controls → GET /controls, GET /controls/{id},
   POST /controls/{id}/evaluate). A control definition has a key, title, objective and category (no separate
   description); its implementation records a free-text method, an owner and the date it applies from. The current
   state is a 24-hour preview recomputed from test runs and evidence. Linked obligations come from each obligation's
   satisfaction expression. Search and filters are proposals: today's list has none. */

const STATES = ["ALL", "OPERATING", "DEGRADED", "FAILING", "NOT_EVALUATED", "REQUIRES_REVIEW"];
const WINDOWS = [7, 30, 90, 180];
const TODAY = "2026-11-03T00:00:00Z";
const linkedTo = (key: string) => OBLIGATIONS.filter((o) => o.controls.includes(key));
const toneOf = (code: string) => { const s = statusOfCode(code); return s === "unknown" ? "info" : s; };

/** A single-test reading of the evaluator's precedence (engines/assurance.py): any FAIL in the window fails the control;
 *  no run means TEST_NOT_RUN; passing runs whose evidence conflicts need review; passing runs must cover the whole
 *  window. The application also applies the control's test policy and evidence admissibility, which this omits. */
function evaluateRuns(runs: Run[], from: string, to: string, conflict: boolean): { state: Control["state"]; reasons: string[] } {
  if (runs.some((r) => r.result === "FAIL")) return { state: "FAILING", reasons: ["TEST_FAIL"] };
  if (!runs.length) return { state: "NOT_EVALUATED", reasons: ["TEST_NOT_RUN"] };
  const passes = runs.filter((r) => r.result === "PASS");
  if (passes.length && conflict) return { state: "REQUIRES_REVIEW", reasons: ["SOURCE_CONFLICT"] };
  let reached = from;
  for (const r of [...passes].sort((a, b) => a.window[0].localeCompare(b.window[0]))) { if (r.window[0] > reached) break; if (r.window[1] > reached) reached = r.window[1]; }
  if (reached >= to) return { state: "OPERATING", reasons: [] };
  const other: Record<string, string> = { ERROR: "TEST_ERROR", INCONCLUSIVE: "TEST_INCONCLUSIVE", SKIPPED: "TEST_NOT_RUN" };
  return { state: "NOT_EVALUATED", reasons: [...new Set(["COVERAGE_GAP", ...runs.filter((r) => other[r.result]).map((r) => other[r.result])])].sort() };
}

export function Controls({ go }: { go: (id: string) => void }) {
  return (
    <DemoFrame id="ow-controls" plane="workspace" title="Controls & tests"
      intro="The controls that prove the organization's obligations: what each is for, who runs it, whether it is operating, and the test runs and evidence behind that state."
      app={{ screen: "Controls & Tests", route: "/controls", action: "GET /controls · GET /controls/{id} · POST /controls/{id}/evaluate", permission: "read",
        enforced: ["Only your organization's controls, runs and evidence are returned", "Test results are PASS, FAIL, ERROR, INCONCLUSIVE or SKIPPED, recorded by the test's source", "Evaluating records a control.evaluated event; it never edits a test run"] }}
      availability={{ level: "partial", note: "The catalogue, control detail, test runs with evidence references and a 7/30/90/180-day evaluation window exist. Search and filters are proposals; controls have an objective rather than a separate description." }}
      empty={{ title: "No controls implemented yet", description: "Implement a control against an obligation's satisfaction expression to see it here." }}>
      <Body go={go} />
    </DemoFrame>
  );
}

function Body({ go }: { go: (id: string) => void }) {
  const toast = useToast();
  const [controls, setControls] = useState<Control[]>(CONTROLS);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("all");
  const [state, setState] = useState("ALL");
  const [selected, setSelected] = useSelection("controls", CONTROLS[1].key);
  const [windowDays, setWindowDays] = useState(30);
  const [evaluating, setEvaluating] = useState(false);
  const [evaluated, setEvaluated] = useState<{ at: string; state: string; reasons: string[] } | null>(null);
  const { ref, reveal } = useDetailPanel();

  const categories = [...new Set(CONTROLS.map((c) => c.category))].sort();
  const base = useMemo(() => controls.filter((c) => (category === "all" || c.category === category)
    && (!q.trim() || `${c.title} ${c.key} ${c.objective} ${c.owner} ${c.method}`.toLowerCase().includes(q.trim().toLowerCase()))), [controls, q, category]);
  const shown = base.filter((c) => state === "ALL" || c.state === state);
  const current = controls.find((c) => c.key === selected);
  const from = new Date(new Date(TODAY).getTime() - windowDays * 86_400_000).toISOString().slice(0, 10);
  const to = TODAY.slice(0, 10);
  // Runs whose own window overlaps the evaluation window, as the evaluator selects them.
  const runs = (current?.runs ?? []).filter((r) => r.window[1] > from && r.window[0] < to).sort((a, b) => b.at.localeCompare(a.at));

  const evaluate = async () => {
    await delay(900);
    const result = evaluateRuns(runs, from, to, current!.reasons.includes("SOURCE_CONFLICT"));
    setEvaluated({ at: now(), ...result });
    setControls((cs) => cs.map((c) => (c.key === current!.key ? { ...c, state: result.state as Control["state"], reasons: result.reasons } : c)));
    toast(`${current!.title}: ${result.state} for the last ${windowDays} days (simulated: no evaluation was recorded)`, result.state === "OPERATING" ? "good" : result.state === "FAILING" ? "bad" : "warn");
  };

  const columns: DataColumn<Control>[] = [
    { key: "title", header: "Control", sortValue: (c) => c.title, render: (c) => <><span className="block font-medium text-fg">{c.title}</span><code className="type-code text-fg-muted">{c.key}</code> <span className="type-caption text-fg-muted">· {c.category}</span></> },
    { key: "entity", header: "Scope · owner", sortValue: (c) => entityName(c.entity), render: (c) => <><span className="block">{entityName(c.entity)}</span><span className="type-caption text-fg-muted">{c.owner}</span></> },
    { key: "linked", header: "Obligations", align: "right", sortValue: (c) => linkedTo(c.key).length, render: (c) => <span className="tabular-nums">{linkedTo(c.key).length}</span> },
    { key: "state", header: "Current state · reasons", sortValue: (c) => c.state, render: (c) => <span className="flex flex-col items-start gap-1"><StateBadge code={c.state} size="sm" />
      {c.reasons.map((r) => <code key={r} className="type-code text-fg-secondary">{r}</code>)}</span> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (c) => (
      <Button size="sm" variant={c.key === selected ? "secondary" : "ghost"} aria-pressed={c.key === selected} onClick={() => { setSelected(c.key); setEvaluated(null); reveal(); }}>
        {c.key === selected ? "Viewing" : "View"}<span className="sr-only"> {c.title}</span>
      </Button>
    ) },
  ];

  return (
    <div className={SPLIT}>
      <div className="min-w-0 space-y-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <Field label={<>Search controls<Proposed /></>}><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Title, key, objective, method or owner" /></Field>
          <Field label={<>Category<Proposed /></>}><Select value={category} onChange={(e) => setCategory(e.target.value)} options={[{ value: "all", label: "All categories" }, ...categories.map((c) => ({ value: c, label: c }))]} /></Field>
        </div>
        <Tabs id="ow-ctl-state" label="Filter by current state" variant="pills" value={state} onChange={setState}
          items={STATES.map((s) => ({ value: s, label: s === "ALL" ? "All" : s, count: base.filter((c) => s === "ALL" || c.state === s).length }))} />
        <TabPanel id="ow-ctl-state" value={state} className="">
          <DataTable caption={`Control catalogue (sample): ${shown.length} shown`} columns={columns} rows={shown} rowKey={(c) => c.key} defaultSort={{ key: "title", dir: "asc" }} minWidth="44rem"
            empty={{ title: "No controls match", description: "Change the search or the filters.", action: <Button size="sm" onClick={() => { setQ(""); setCategory("all"); setState("ALL"); }}>Clear filters</Button> }} />
        </TabPanel>
        <p className="type-caption text-fg-muted">Current state is a 24-hour preview, recomputed from the latest test runs and evidence; a period&apos;s assessed state is frozen in its snapshot.</p>
      </div>

      <div ref={ref} className="scroll-mt-6">
        {current ? (
          <Card title={current.title} description={<code className="type-code">{current.key}</code>} actions={<StateBadge code={current.state} size="sm" />} bodyClassName="p-card space-y-5">
            <DescriptionList columns={1} items={[
              ["Objective", current.objective], ["Category", current.category], ["Scope", entityName(current.entity)],
              ["Method", current.method], ["Owner", current.owner], ["Implemented since", fmtDate(current.since)],
            ]} />
            <Section title="Linked obligations">
              <ul className="space-y-1.5">{linkedTo(current.key).map((o) => (
                <li key={o.key} className="flex flex-wrap items-center justify-between gap-2"><AreaLink id={o.key} area="obligations" go={go} label={`Open obligation ${o.key}`} />{o.state ? <StateBadge code={o.state} size="sm" /> : <span className="type-code text-fg-muted">NO SNAPSHOT</span>}</li>
              ))}</ul>
            </Section>
            <Section title="Test execution history" aside={
              <div className="w-36"><Field label="Window"><Select value={String(windowDays)} onChange={(e) => { setWindowDays(Number(e.target.value)); setEvaluated(null); }} options={WINDOWS.map((d) => ({ value: String(d), label: `Last ${d} days` }))} /></Field></div>
            }>
              {runs.length ? (
                <Table caption={`Test runs for ${current.title} in the last ${windowDays} days`} minWidth="22rem">
                  <THead><Tr><Th>Run</Th><Th>Result</Th><Th>Window</Th><Th>Evidence</Th></Tr></THead>
                  <TBody>{runs.map((r: Run) => (
                    <Tr key={r.id}>
                      <Td><code className="block type-code">{r.id}</code><span className="type-caption text-fg-muted">{fmtDate(r.at, true)} · {r.source}</span></Td>
                      <Td><StateBadge code={r.result} size="sm" /></Td>
                      <Td className="whitespace-nowrap tabular-nums">{fmtRange(r.window)}</Td>
                      <Td>{r.evidence.length ? <span className="flex flex-col items-start gap-0.5">{r.evidence.map((e) => EVIDENCE.some((x) => x.id === e)
                        ? <AreaLink key={e} id={e} area="evidence" go={go} label={`Open evidence ${e}`} />
                        : <code key={e} title="Not among the sample evidence records" className="type-code text-fg-muted">{e}</code>)}</span> : <span className="text-fg-muted">none</span>}</Td>
                    </Tr>
                  ))}</TBody>
                </Table>
              ) : <p className="type-secondary text-fg-muted">No test run in the last {windowDays} days. Without a run covering the window the control is NOT_EVALUATED (TEST_NOT_RUN).</p>}
            </Section>
            {evaluated && (
              <Alert tone={toneOf(evaluated.state)} live="polite" title={`Evaluated: ${evaluated.state}`}>
                {evaluated.reasons.length ? `Reasons: ${evaluated.reasons.join(", ")}. ` : ""}Simulated at {fmtDate(evaluated.at, true)}; nothing was recorded.
              </Alert>
            )}
            <Button fullWidth icon={RefreshCw} onClick={() => setEvaluating(true)}>Evaluate for the last {windowDays} days</Button>
            <p className="type-caption text-fg-muted">Sample control<Sample />. Evaluating here changes only this page.</p>
          </Card>
        ) : <Card><p className="type-secondary text-fg-muted">Choose a control to see its tests and evidence.</p></Card>}
      </div>

      {evaluating && current && (
        <ConfirmDialog title={`Evaluate ${current.title}?`} confirmLabel="Evaluate" busyLabel="Evaluating…" onClose={() => setEvaluating(false)} onConfirm={evaluate}>
          <p>Recomputes the control&apos;s state from its test runs and evidence for {fmtRange([from, to])}. In the application this records a <code className="type-code">control.evaluated</code> event; test runs are never edited.</p>
          <p>Simulated: no evaluation is recorded and the server is not called.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
