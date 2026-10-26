"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Badge, Card, ErrorBox, Hash, Json, KV, Notice, PageHeader, Table, Tabs, useAction } from "@/components/ui";
import { RuleSandbox, VectorReport } from "@/components/RuleTesting";

export default function DraftPage() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, reload } = useApi<any>(`/rules/drafts/${id}`);
  const [tab, setTab] = useState("Author");
  const a = useAction();
  const step = (path: string, json?: unknown) => a.run(async () => { const r = await api(`/rules/drafts/${id}/${path}`, { method: "POST", json }); await reload(); return r; });

  return (
    <>
      <PageHeader spec="WF-RUL-01..06" title={d ? `Draft · ${d.obligation_definition} r${d.revision}` : "Draft"}
        subtitle="Any edit resets review approvals and test reports. Publication is fail-closed on validation, vectors, shadow and separation of duties." />
      <ErrorBox error={error || a.error} />
      {d && (
        <>
          <Card className="mb-4">
            <KV cols={4} items={[
              ["Status", <Badge key="s" code={d.status} />], ["Mode", <span key="m" className="mono">{d.authoring_mode}</span>],
              ["Author", d.author_id], ["Peer reviewer", d.peer_reviewer_id], ["Legal reviewer", d.legal_reviewer_id],
              ["Change class", <span key="c" className="mono">{d.change_class}</span>], ["Validity", `${d.valid_from} → ${d.valid_to ?? "open"}`],
              ["Supersedes", <Hash key="h" value={d.supersedes_rule_version_id} n={8} />],
            ]} />
            <div className="mt-4 flex flex-wrap gap-2 border-t border-navy-100 pt-4">
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("validate")}>1 · Validate</button>
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("test")}>2 · Run golden vectors</button>
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("submit-review")}>3 · Submit for review</button>
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("peer-approve")}>4 · Peer approve</button>
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("legal-approve")}>5 · Legal approve</button>
              <button className="btn-ghost" disabled={a.busy} onClick={() => step("shadow")}>6 · Shadow impact</button>
              <Publish onPublish={(label, changelog) => step("publish", { label, changelog })} busy={a.busy} />
            </div>
            {a.result?.impact_reevaluations !== undefined && <div className="mt-3"><Notice tone="good">Published. {a.result.impact_reevaluations} subject evaluation(s) re-run in their home cell.</Notice></div>}
          </Card>
          <Tabs tabs={["Author", "Validation", "Golden vectors", "Sandbox", "Shadow impact", "Semantic diff"]} value={tab} onChange={setTab} />
          {tab === "Author" && <Editor d={d} onSaved={reload} />}
          {tab === "Validation" && <Validation rep={d.validation_report} />}
          {tab === "Golden vectors" && <VectorReport rep={d.vector_report} />}
          {tab === "Sandbox" && <RuleSandbox id={id} />}
          {tab === "Shadow impact" && (d.shadow_report ? <Card title="Shadow impact (aggregate only)"><Notice>{d.shadow_report.note}</Notice><div className="mt-3"><Json value={d.shadow_report} /></div></Card> : <Notice>No shadow run yet. Shadow requires an IN_REVIEW or VERIFIED candidate.</Notice>)}
          {tab === "Semantic diff" && <Diff draft={d} />}
        </>
      )}
    </>
  );
}

function Publish({ onPublish, busy }: { onPublish: (l: string, c: string) => void; busy: boolean }) {
  const [label, setLabel] = useState("");
  const [cl, setCl] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input className="input w-40 py-1 text-xs" placeholder="Release label" value={label} onChange={(e) => setLabel(e.target.value)} />
      <input className="input w-56 py-1 text-xs" placeholder="Changelog" value={cl} onChange={(e) => setCl(e.target.value)} />
      <button className="btn-accent" disabled={busy || !label || !cl} onClick={() => onPublish(label, cl)}>7 · Sign & publish</button>
    </div>
  );
}

function Editor({ d, onSaved }: { d: any; onSaved: () => void }) {
  const [mode, setMode] = useState(d.authoring_mode);
  const [ast, setAst] = useState("");
  const [dt, setDt] = useState("");
  const [sm, setSm] = useState("");
  const [gv, setGv] = useState("");
  const [vf, setVf] = useState(d.valid_from);
  const [vt, setVt] = useState(d.valid_to ?? "");
  const [cc, setCc] = useState(d.change_class);
  const a = useAction();
  useEffect(() => {
    setAst(JSON.stringify(d.ast ?? {}, null, 2));
    setDt(JSON.stringify(d.decision_table ?? { table_mode: "ANY_MATCH", columns: [], rows: [] }, null, 2));
    setSm(JSON.stringify(d.source_mappings ?? [], null, 2));
    setGv(JSON.stringify(d.golden_vectors ?? [], null, 2));
  }, [d]);
  const save = () => a.run(async () => {
    const body = {
      obligation_definition_id: d.obligation_definition_id, authoring_mode: mode,
      ast: mode === "AST" ? JSON.parse(ast) : null, decision_table: mode === "DECISION_TABLE" ? JSON.parse(dt) : null,
      source_mappings: JSON.parse(sm), golden_vectors: JSON.parse(gv), valid_from: vf, valid_to: vt || null, change_class: cc,
    };
    await api(`/rules/drafts/${d.draft_id}`, { method: "PUT", json: body });
    onSaved();
  });
  const frozen = ["PUBLISHED", "RELEASE_READY"].includes(d.status);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Rule definition" actions={<button className="btn-primary" disabled={frozen || a.busy} onClick={save}>Save revision</button>}>
        <ErrorBox error={a.error} />
        {frozen && <Notice tone="warn">Frozen artifact; create a new draft to change it.</Notice>}
        <div className="mb-3 grid gap-3 sm:grid-cols-4">
          <div><label className="label">Mode</label><select className="input" value={mode} onChange={(e) => setMode(e.target.value)}><option>AST</option><option>DECISION_TABLE</option></select></div>
          <div><label className="label">Valid from</label><input type="date" className="input" value={vf} onChange={(e) => setVf(e.target.value)} /></div>
          <div><label className="label">Valid to</label><input type="date" className="input" value={vt} onChange={(e) => setVt(e.target.value)} /></div>
          <div><label className="label">Change class</label><select className="input" value={cc} onChange={(e) => setCc(e.target.value)}>
            {["EXECUTABLE_OUTCOME", "TEMPORAL", "CORRECTION", "DEPENDENCY", "EDITORIAL"].map((x) => <option key={x}>{x}</option>)}</select></div>
        </div>
        <label className="label">{mode === "AST" ? "Typed AST (JSON)" : "Decision table (ANY_MATCH / SINGLE_MATCH_VALIDATED; FIRST_MATCH prohibited)"}</label>
        <textarea className="input mono h-96 text-[11px]" value={mode === "AST" ? ast : dt} onChange={(e) => (mode === "AST" ? setAst(e.target.value) : setDt(e.target.value))} spellCheck={false} />
      </Card>
      <div className="space-y-4">
        <Card title="Source mappings"><textarea className="input mono h-40 text-[11px]" value={sm} onChange={(e) => setSm(e.target.value)} spellCheck={false} /></Card>
        <Card title="Golden vectors" subtitle="POSITIVE, NEGATIVE and UNKNOWN classes are mandatory before review.">
          <textarea className="input mono h-64 text-[11px]" value={gv} onChange={(e) => setGv(e.target.value)} spellCheck={false} />
        </Card>
        {d.compiled_ast && d.authoring_mode === "DECISION_TABLE" && <Card title="Compiled AST"><Json value={d.compiled_ast} /></Card>}
      </div>
    </div>
  );
}

function Validation({ rep }: { rep: any }) {
  if (!rep) return <Notice>Not validated yet.</Notice>;
  return (
    <Card title="Static validation report" actions={<Badge code={rep.ok ? "PASS" : "FAIL"} />}>
      <KV cols={4} items={[["Errors", rep.error_count], ["Warnings", rep.warning_count], ["Nodes", rep.node_count], ["Canonical AST hash", <Hash key="h" value={rep.canonical_ast_hash} />],
        ["Required facts", rep.required_facts?.join(", ")], ["Set refs", rep.set_refs?.join(", ")]]} />
      <div className="mt-4">
        <Table head={["Severity", "Code", "Node", "Message"]} empty={!rep.issues?.length}>
          {rep.issues?.map((i: any, k: number) => (
            <tr key={k}><td className="td"><Badge code={i.severity === "ERROR" ? "FAIL" : "WARN"} label={i.severity} /></td><td className="td mono text-xs">{i.code}</td>
              <td className="td mono text-xs">{i.node_id}</td><td className="td text-sm">{i.message}</td></tr>
          ))}
        </Table>
      </div>
    </Card>
  );
}

function Diff({ draft }: { draft: any }) {
  const a = useAction();
  const target = draft.supersedes_rule_version_id;
  if (!target) return <Notice>No previous published version to diff against.</Notice>;
  return (
    <Card title="Semantic diff by stable node id" actions={<button className="btn-primary" onClick={() => a.run(() => api(`/rules/diff?a=${target}&b=${draft.draft_id}`))}>Compute diff</button>}>
      <ErrorBox error={a.error} />
      {a.result && (
        <div className="space-y-3">
          <KV cols={4} items={[["Nodes added", a.result.added.length], ["Nodes removed", a.result.removed.length], ["Nodes changed", a.result.changed.length],
            ["Source mappings changed", a.result.source_mappings_changed ? "yes" : "no"], ["Facts added", a.result.facts_added.join(", ")], ["Facts removed", a.result.facts_removed.join(", ")]]} />
          <Json value={a.result} />
        </div>
      )}
    </Card>
  );
}
