"use client";

import { useParams } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { codeText, RULE_CHANGE_CLASSES } from "@/lib/codes";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { Badge, Card, ErrorBox, FieldError, Hash, Json, KV, LiveRegion, Notice, PageHeader, PermissionNote, Table, TabPanel, Tabs, useAction } from "@/components/ui";
import { RuleSandbox, VectorReport } from "@/components/RuleTesting";

export default function DraftPage() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, reload } = useApi<any>(`/rules/drafts/${id}`);
  const [tab, setTab] = useState("Author");
  const a = useAction();
  const step = (path: string, json?: unknown) => a.run(async () => { const r = await api(`/rules/drafts/${id}/${path}`, { method: "POST", json }); await reload(); return r; });
  const { can, why } = useCan();
  const canShadow = can("content_author") || can("content_release") || can("content_legal_review");
  const tabsId = useId();

  return (
    <>
      <PageHeader spec="WF-RUL-01..06" title={d ? `Draft · ${d.obligation_definition} r${d.revision}` : "Draft"}
        subtitle="Any edit resets review approvals and test reports. Publication is fail-closed on validation, vectors, shadow and separation of duties." />
      <PermissionNote perms={["content_author", "content_peer_review", "content_legal_review", "content_release"]} />
      <ErrorBox error={error || a.error} />
      {d && (
        <>
          <Card className="mb-4">
            <KV cols={4} items={[
              ["Status", <Badge key="s" code={d.status} />], ["Mode", <span key="m" className="mono">{d.authoring_mode}</span>],
              ["Author", d.author_id], ["Peer reviewer", d.peer_reviewer_id], ["Legal reviewer", d.legal_reviewer_id],
              ["Change class", <span key="c" className="mono">{codeText(d.change_class, RULE_CHANGE_CLASSES)}</span>], ["Validity", `${d.valid_from} → ${d.valid_to ?? "open"}`],
              ["Supersedes", <Hash key="h" value={d.supersedes_rule_version_id} n={8} />],
            ]} />
            <div className="mt-4 flex flex-wrap gap-2 border-t border-navy-100 pt-4">
              <button className="btn-ghost" disabled={a.busy || !can("content_author")} title={why("content_author")} onClick={() => step("validate")}>1 · Validate</button>
              <button className="btn-ghost" disabled={a.busy || !can("content_author")} title={why("content_author")} onClick={() => step("test")}>2 · Run golden vectors</button>
              <button className="btn-ghost" disabled={a.busy || !can("content_author")} title={why("content_author")} onClick={() => step("submit-review")}>3 · Submit for review</button>
              <button className="btn-ghost" disabled={a.busy || !can("content_peer_review")} title={why("content_peer_review")} onClick={() => step("peer-approve")}>4 · Peer approve</button>
              <button className="btn-ghost" disabled={a.busy || !can("content_legal_review")} title={why("content_legal_review")} onClick={() => step("legal-approve")}>5 · Legal approve</button>
              <button className="btn-ghost" disabled={a.busy || !canShadow} title={canShadow ? undefined : why("content_author")} onClick={() => step("shadow")}>6 · Shadow impact</button>
              <Publish onPublish={(label, changelog) => step("publish", { label, changelog })} busy={a.busy} />
            </div>
            {!RULE_CHANGE_CLASSES.includes(d.change_class) && !["PUBLISHED", "RELEASE_READY"].includes(d.status) && (
              <div className="mt-3"><Notice tone="warn">
                The stored change class ({codeText(d.change_class, RULE_CHANGE_CLASSES)}) is not a ZA-RUL-001 §14 class. The publish gate asks for a shadow impact
                report only for EXECUTABLE_OUTCOME, TEMPORAL, CORRECTION and DEPENDENCY, so it would not ask for one here. Choose a listed class on the Author tab and save the draft before publishing.
              </Notice></div>
            )}
            <LiveRegion>{a.result?.impact_reevaluations !== undefined && <div className="mt-3"><Notice tone="good">Published. {a.result.impact_reevaluations} subject evaluation(s) re-run in their home cell.</Notice></div>}</LiveRegion>
          </Card>
          <Tabs id={tabsId} tabs={["Author", "Validation", "Golden vectors", "Sandbox", "Shadow impact", "Semantic diff"]} value={tab} onChange={setTab} />
          <TabPanel id={tabsId} value={tab}>
          {tab === "Author" && <Editor d={d} onSaved={reload} />}
          {tab === "Validation" && <Validation rep={d.validation_report} />}
          {tab === "Golden vectors" && <VectorReport rep={d.vector_report} />}
          {tab === "Sandbox" && <RuleSandbox id={id} />}
          {tab === "Shadow impact" && (d.shadow_report ? <ShadowImpact rep={d.shadow_report} /> : <Notice>No shadow run yet. Shadow requires an IN_REVIEW or VERIFIED candidate.</Notice>)}
          {tab === "Semantic diff" && <Diff draft={d} />}
          </TabPanel>
        </>
      )}
    </>
  );
}

function Publish({ onPublish, busy }: { onPublish: (l: string, c: string) => void; busy: boolean }) {
  const [label, setLabel] = useState("");
  const [cl, setCl] = useState("");
  const { can, why } = useCan();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input aria-label="Release label" required className="input w-40 py-1 text-xs" placeholder="Release label" value={label} onChange={(e) => setLabel(e.target.value)} />
      <input aria-label="Changelog" required className="input w-56 py-1 text-xs" placeholder="Changelog" value={cl} onChange={(e) => setCl(e.target.value)} />
      <button className="btn-accent" disabled={busy || !can("content_release") || !label || !cl} title={why("content_release")} onClick={() => onPublish(label, cl)}>7 · Sign & publish</button>
    </div>
  );
}

/* ZA-RUL-001 §14 change classes. SOURCE_MAPPING and EXECUTABLE_NON_OUTCOME are canonical but not offered yet:
   the publish gate does not apply their shadow policy and would let them publish without a shadow report. */
const CHANGE_CLASSES = ["EXECUTABLE_OUTCOME", "TEMPORAL", "CORRECTION", "DEPENDENCY", "EDITORIAL_METADATA"];

/** Whether the text parses as JSON of the expected shape ("object" also admits null, as the request types do). */
function jsonIs(text: string, shape: "object" | "array"): boolean {
  try {
    const v = JSON.parse(text);
    return shape === "array" ? Array.isArray(v) : v === null || (typeof v === "object" && !Array.isArray(v));
  } catch {
    return false;
  }
}

function Editor({ d, onSaved }: { d: any; onSaved: () => void }) {
  const [mode, setMode] = useState(d.authoring_mode);
  const [ast, setAst] = useState("");
  const [dt, setDt] = useState("");
  const [sm, setSm] = useState("");
  const [gv, setGv] = useState("");
  const [vf, setVf] = useState(d.valid_from);
  const [vt, setVt] = useState(d.valid_to ?? "");
  const [cc, setCc] = useState<string>(d.change_class ?? "");
  const [tried, setTried] = useState(false);
  const a = useAction();
  const { can, why } = useCan();
  const fid = useId();
  useEffect(() => {
    setAst(JSON.stringify(d.ast ?? {}, null, 2));
    setDt(JSON.stringify(d.decision_table ?? { table_mode: "ANY_MATCH", columns: [], rows: [] }, null, 2));
    setSm(JSON.stringify(d.source_mappings ?? [], null, 2));
    setGv(JSON.stringify(d.golden_vectors ?? [], null, 2));
  }, [d]);
  // Checks follow the PUT body types (object or null for the AST / table, lists for mappings and vectors, a required
  // valid_from). A newly chosen change class must be a ZA-RUL-001 §14 class: the backend stores any string, and its
  // publish gate asks for shadow impact only for the classes it names. A draft's own stored class is kept as stored (T4).
  const keptNonCanonical = !!cc && cc === d.change_class && !RULE_CHANGE_CLASSES.includes(cc);
  const errs = {
    cc: !cc ? "Choose a change class." : !RULE_CHANGE_CLASSES.includes(cc) && cc !== d.change_class ? `${cc} is not a ZA-RUL-001 §14 change class. Choose one of the listed classes.` : "",
    vf: !vf ? "Enter the date this rule is valid from." : "",
    body: jsonIs(mode === "AST" ? ast : dt, "object") ? "" : `The ${mode === "AST" ? "typed AST" : "decision table"} must be a JSON object.`,
    sm: jsonIs(sm, "array") ? "" : "Source mappings must be a JSON array, for example [].",
    gv: jsonIs(gv, "array") ? "" : "Golden vectors must be a JSON array, for example [].",
  };
  const show = (k: keyof typeof errs) => (tried ? errs[k] : "");
  const invalid = (k: keyof typeof errs) => ({ "aria-invalid": !!show(k) || undefined, "aria-describedby": show(k) ? `${fid}-${k}-error` : undefined });
  const save = () => {
    const first = (Object.keys(errs) as (keyof typeof errs)[]).find((k) => errs[k]);
    if (first) { setTried(true); document.getElementById(`${fid}-${first}`)?.focus(); return; }
    a.run(async () => {
      const body = {
        obligation_definition_id: d.obligation_definition_id, authoring_mode: mode,
        ast: mode === "AST" ? JSON.parse(ast) : null, decision_table: mode === "DECISION_TABLE" ? JSON.parse(dt) : null,
        source_mappings: JSON.parse(sm), golden_vectors: JSON.parse(gv), valid_from: vf, valid_to: vt || null, change_class: cc,
      };
      await api(`/rules/drafts/${d.draft_id}`, { method: "PUT", json: body });
      onSaved();
    });
  };
  const frozen = ["PUBLISHED", "RELEASE_READY"].includes(d.status);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Rule definition" actions={<button className="btn-primary" disabled={frozen || a.busy || !can("content_author")} title={why("content_author")} onClick={save}>Save revision</button>}>
        <ErrorBox error={a.error} />
        {frozen && <Notice tone="warn">Frozen artifact; create a new draft to change it.</Notice>}
        <div className="mb-3 grid gap-3 sm:grid-cols-4">
          <div><label className="label" htmlFor={`${fid}-mode`}>Mode</label><select id={`${fid}-mode`} className="input" value={mode} onChange={(e) => setMode(e.target.value)}><option>AST</option><option>DECISION_TABLE</option></select></div>
          <div><label className="label" htmlFor={`${fid}-vf`}>Valid from (required)</label><input id={`${fid}-vf`} type="date" required className="input" {...invalid("vf")} value={vf} onChange={(e) => setVf(e.target.value)} />
            <FieldError id={`${fid}-vf`} message={show("vf")} /></div>
          <div><label className="label" htmlFor={`${fid}-vt`}>Valid to</label><input id={`${fid}-vt`} type="date" className="input" value={vt} onChange={(e) => setVt(e.target.value)} /></div>
          <div><label className="label" htmlFor={`${fid}-cc`}>Change class (required)</label><select id={`${fid}-cc`} required className="input" {...invalid("cc")} {...(keptNonCanonical ? { "aria-describedby": `${fid}-cc-note` } : {})} value={cc} onChange={(e) => setCc(e.target.value)}>
            {!d.change_class && <option value="">Select…</option>}
            {CHANGE_CLASSES.map((x) => <option key={x}>{x}</option>)}
            {d.change_class && !CHANGE_CLASSES.includes(d.change_class) && (
              <option value={d.change_class}>{d.change_class} ({RULE_CHANGE_CLASSES.includes(d.change_class) ? "stored, not offered here" : "stored, non-canonical"})</option>
            )}</select>
            <FieldError id={`${fid}-cc`} message={show("cc")} />
            {keptNonCanonical && <p id={`${fid}-cc-note`} className="mt-1.5 text-[12px] leading-5 text-amber-800">{codeText(cc, RULE_CHANGE_CLASSES)}: kept as stored when you save. Choose a listed class to replace it.</p>}</div>
        </div>
        <label className="label" htmlFor={`${fid}-body`}>{mode === "AST" ? "Typed AST (JSON)" : "Decision table (ANY_MATCH / SINGLE_MATCH_VALIDATED; FIRST_MATCH prohibited)"}</label>
        <textarea id={`${fid}-body`} className="input mono h-96 text-[11px]" {...invalid("body")} value={mode === "AST" ? ast : dt} onChange={(e) => (mode === "AST" ? setAst(e.target.value) : setDt(e.target.value))} spellCheck={false} />
        <FieldError id={`${fid}-body`} message={show("body")} />
      </Card>
      <div className="space-y-4">
        <Card title="Source mappings"><textarea id={`${fid}-sm`} aria-label="Source mappings (JSON)" className="input mono h-40 text-[11px]" {...invalid("sm")} value={sm} onChange={(e) => setSm(e.target.value)} spellCheck={false} />
          <FieldError id={`${fid}-sm`} message={show("sm")} /></Card>
        <Card title="Golden vectors" subtitle="POSITIVE, NEGATIVE and UNKNOWN classes are mandatory before review.">
          <textarea id={`${fid}-gv`} aria-label="Golden vectors (JSON)" className="input mono h-64 text-[11px]" {...invalid("gv")} value={gv} onChange={(e) => setGv(e.target.value)} spellCheck={false} />
          <FieldError id={`${fid}-gv`} message={show("gv")} />
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
          <DiffNodes title="Changed nodes" rows={a.result.changed.map((c: any) => ({ node: c.after ?? c.before, before: c.before, after: c.after }))} />
          <DiffNodes title="Added nodes" rows={a.result.added.map((n: any) => ({ node: n }))} />
          <DiffNodes title="Removed nodes" rows={a.result.removed.map((n: any) => ({ node: n }))} />
          <details><summary className="cursor-pointer text-xs text-navy-700">Raw diff</summary><Json value={a.result} /></details>
        </div>
      )}
    </Card>
  );
}

/* WF-RUL-03 semantic diff, laid out from GET /rules/diff: nodes are matched by stable node id. For a changed node, the
   fields that differ are listed with their before and after values; nothing is interpreted as a change in legal meaning. */
const brief = (v: unknown) => { const s = v === undefined ? "—" : JSON.stringify(v); return s.length > 80 ? `${s.slice(0, 79)}…` : s; };
const nodeLabel = (n: any) => [n?.operator, n?.fact, n?.row_id ? `row ${n.row_id}` : null].filter(Boolean).join(" · ") || "—";

function DiffNodes({ title, rows }: { title: string; rows: { node: any; before?: any; after?: any }[] }) {
  if (!rows.length) return null;
  const changedFields = (b: any, a: any) => [...new Set([...Object.keys(b ?? {}), ...Object.keys(a ?? {})])].filter((k) => JSON.stringify(b?.[k]) !== JSON.stringify(a?.[k]));
  return (
    <div>
      <div className="label">{title} ({rows.length})</div>
      <Table head={["Node id", "Type", "Operator / fact", ...(rows[0].before !== undefined ? ["What changed"] : [])]}>
        {rows.map(({ node, before, after }) => (
          <tr key={node?.node_id}>
            <td className="td mono text-xs">{node?.node_id}</td>
            <td className="td mono text-xs">{node?.node_type ?? "—"}</td>
            <td className="td mono text-xs">{nodeLabel(node)}</td>
            {before !== undefined && <td className="td text-xs">{changedFields(before, after).map((k) => (
              <div key={k}><span className="mono">{k}</span>: <span className="mono text-slate-600">{brief(before?.[k])}</span> → <span className="mono">{brief(after?.[k])}</span></div>
            ))}</td>}
          </tr>
        ))}
      </Table>
    </div>
  );
}

/* WF-APP-03 shadow impact review from the draft's stored shadow report: aggregate counts only (FR-APP-006), never subject
   identities. Cell completion and the signed approval decision are not part of the report, so they are not shown. */
function ShadowImpact({ rep }: { rep: any }) {
  const buckets = (Object.entries(rep.transition_buckets ?? {}) as [string, number][]).map(([k, n]) => {
    const [from, to] = k.split("->");
    return { k, from, to, n, kind: from === "NONE" ? "New evaluation" : from === to ? "Unchanged" : "Changed" };
  }).sort((x, y) => Number(x.kind === "Unchanged") - Number(y.kind === "Unchanged") || y.n - x.n || x.k.localeCompare(y.k));
  const toUncertain = buckets.filter((b) => b.to === "UNCERTAIN" && b.from !== "UNCERTAIN").reduce((s, b) => s + b.n, 0);
  const missing = (Object.entries(rep.missing_fact_counts ?? {}) as [string, number][]).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
  return (
    <Card title="Shadow impact (aggregate only)" subtitle={rep.completed_at ? `Completed ${fmtDate(rep.completed_at, true)}` : undefined}>
      {rep.note && <Notice>{rep.note}</Notice>}
      <div className="mt-3 space-y-4">
        <KV cols={4} items={[["Subjects evaluated", rep.subjects_evaluated ?? "—"], ["Would become UNCERTAIN", toUncertain],
          ["Adopted determinations that would differ", rep.adopted_determination_conflicts ?? "—"], ["Execution errors", rep.execution_errors ?? "—"]]} />
        <div>
          <div className="label">Outcome transitions (current evaluation → candidate rule)</div>
          <Table head={["Current outcome", "Candidate outcome", "Subjects", "Change"]} empty={!buckets.length}>
            {buckets.map((b) => (
              <tr key={b.k}>
                <td className="td">{b.from === "NONE" ? <span className="text-xs text-slate-500">No current evaluation</span> : <Badge code={b.from} />}</td>
                <td className="td"><Badge code={b.to} /></td>
                <td className="td tabular-nums">{b.n}</td>
                <td className="td text-xs text-slate-600">{b.kind}</td>
              </tr>
            ))}
          </Table>
        </div>
        {missing.length > 0 && (
          <div>
            <div className="label">Facts that would be missing (subjects that would be UNCERTAIN)</div>
            <Table head={["Fact", "Subjects"]}>
              {missing.map(([f, n]) => <tr key={f}><td className="td mono text-xs">{f}</td><td className="td tabular-nums">{n}</td></tr>)}
            </Table>
          </div>
        )}
        <details><summary className="cursor-pointer text-xs text-navy-700">Raw shadow report</summary><Json value={rep} /></details>
      </div>
    </Card>
  );
}
