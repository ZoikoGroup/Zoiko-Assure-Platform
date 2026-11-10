"use client";

import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { codeText, RULE_CHANGE_CLASSES } from "@/lib/codes";
import { A, Badge, Card, EmptyState, ErrorBox, Field, KV, LoadingSkeleton, Modal, Notice, PageHeader, useAction } from "@/components/ui";
import { RuleSandbox, VectorReport, type FixtureVector } from "@/components/RuleTesting";

const FROZEN = ["PUBLISHED", "RELEASE_READY"];

export default function RuleTestWorkbench() {
  const drafts = useApi<any[]>("/rules/drafts");
  // Running and changing golden vectors is content authoring (Zoiko content staff); the backend enforces it too.
  const canAuthor = !!useAuth().me?.permissions?.includes("content_author");
  const [sel, setSel] = useState("");
  const [pending, setPending] = useState<FixtureVector | null>(null);
  const a = useAction();
  const list = drafts.data ?? [];
  const current = list.find((d) => d.draft_id === sel) ?? list.find((d) => !FROZEN.includes(d.status)) ?? list[0];
  const frozen = current && FROZEN.includes(current.status);

  return (
    <>
      <PageHeader spec="WF-RUL-04 · RUL-REQ-015/016" title="Rule Test Workbench"
        subtitle="Test a rule draft against golden vectors and synthetic fixtures."
        help="Runs the exact draft artifact. Customer production facts are never reachable from rule authoring." />
      <ErrorBox error={drafts.error || a.error} />
      {drafts.loading && !drafts.data && <div className="card"><LoadingSkeleton rows={4} /></div>}
      {drafts.data && list.length === 0 && (
        <div className="card"><EmptyState icon={FlaskConical} title="No rule drafts" description={<>Create a draft in <A href="/rules">Rule Authoring</A> to test it here.</>} /></div>
      )}
      {current && (
        <div className="space-y-4">
          <Card title="Draft under test">
            <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <div>
                <label className="label" htmlFor="wb-draft">Rule draft</label>
                <select id="wb-draft" className="input" value={current.draft_id} onChange={(e) => setSel(e.target.value)}>
                  {list.map((d) => <option key={d.draft_id} value={d.draft_id}>{d.obligation_definition} · r{d.revision} · {d.status}</option>)}
                </select>
              </div>
              <A href={`/rules/${current.draft_id}`}>Open in Rule Authoring</A>
            </div>
            <div className="mt-4">
              <KV cols={4} items={[["Status", <Badge key="s" code={current.status} />], ["Mode", <span key="m" className="mono">{current.authoring_mode}</span>],
                ["Validity", `${current.valid_from} → ${current.valid_to ?? "open"}`], ["Golden vectors defined", (current.golden_vectors ?? []).length]]} />
            </div>
          </Card>

          <section aria-labelledby="wb-regression" className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="wb-regression" className="text-sm font-semibold text-slate-900">Regression view</h2>
              <button className="btn-primary" disabled={a.busy || frozen || !canAuthor}
                title={!canAuthor ? "Requires the content authoring permission" : frozen ? "Frozen artifact: create a new draft to change or re-test it" : undefined}
                onClick={() => a.run(async () => { await api(`/rules/drafts/${current.draft_id}/test`, { method: "POST" }); await drafts.reload(); })}>
                Run golden vectors
              </button>
            </div>
            <VectorReport rep={current.vector_report} />
          </section>

          <section aria-label="Synthetic fixture">
            <RuleSandbox key={current.draft_id} id={current.draft_id} compare onSaveVector={frozen || !canAuthor ? undefined : setPending} />
          </section>

          <Notice>
            {!canAuthor ? <>Running golden vectors and saving new ones require the content authoring permission held by Zoiko content staff. You can still run synthetic fixtures against any draft.</>
              : frozen ? <>This draft is frozen, so new golden vectors go on a new draft in <A href="/rules">Rule Authoring</A>.</>
              : <>Saving a golden vector creates a new draft revision. Review approvals and test reports are reset, so the draft is re-validated and re-reviewed; legal review is required for interpretation boundaries, and changing an expected golden result is governed (RUL-REQ-016).</>}
          </Notice>
        </div>
      )}
      {current && pending && <SaveVector draft={current} fixture={pending} onClose={() => setPending(null)}
        onSaved={async () => { setPending(null); await drafts.reload(); }} />}
    </>
  );
}

const CLASS_FOR: Record<string, string> = { APPLIES: "POSITIVE", DOES_NOT_APPLY: "NEGATIVE", UNCERTAIN: "UNKNOWN" };

/** Appends the fixture to the draft's golden vectors through the existing draft update (PUT /rules/drafts/{id}). */
function SaveVector({ draft, fixture, onClose, onSaved }: { draft: any; fixture: FixtureVector; onClose: () => void; onSaved: () => void }) {
  const existing: any[] = draft.golden_vectors ?? [];
  const [vectorId, setVectorId] = useState("");
  const [vectorClass, setVectorClass] = useState(CLASS_FOR[fixture.expected_outcome] ?? "POSITIVE");
  const [rationale, setRationale] = useState("");
  const a = useAction();
  const id = vectorId.trim();
  const duplicate = existing.some((v) => v.vector_id === id);
  const save = () => a.run(async () => {
    const vector = { vector_id: id, vector_class: vectorClass, effective_at: fixture.effective_at, facts: fixture.facts,
      expected_outcome: fixture.expected_outcome, description: rationale.trim() };
    await api(`/rules/drafts/${draft.draft_id}`, { method: "PUT", json: {
      obligation_definition_id: draft.obligation_definition_id, authoring_mode: draft.authoring_mode, ast: draft.ast ?? null,
      decision_table: draft.decision_table ?? null, source_mappings: draft.source_mappings ?? [], golden_vectors: [...existing, vector],
      valid_from: draft.valid_from, valid_to: draft.valid_to ?? null, change_class: draft.change_class,
    } });
    onSaved();
  });
  return (
    <Modal open onClose={onClose} title="Save golden vector">
      <ErrorBox error={a.error} />
      <p className="mb-3 text-[13px] text-slate-600">
        Adds this fixture to <span className="mono">{draft.obligation_definition}</span> r{draft.revision} with expected result <Badge code={fixture.expected_outcome} /> at {fixture.effective_at}.
        The draft becomes a new revision and its approvals and test reports are reset.
      </p>
      {!RULE_CHANGE_CLASSES.includes(draft.change_class) && (
        <div className="mb-3"><Notice tone="warn">
          This draft&rsquo;s change class ({codeText(draft.change_class, RULE_CHANGE_CLASSES)}) is not a ZA-RUL-001 §14 class. Saving the vector keeps it as stored;
          choose a listed class in the draft editor.
        </Notice></div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Vector ID" htmlFor="gv-id" hint={duplicate ? <span className="text-red-700">A vector with this ID already exists.</span> : undefined}>
          <input id="gv-id" className="input mono" value={vectorId} onChange={(e) => setVectorId(e.target.value)} placeholder="e.g. TR-POS-2" />
        </Field>
        <Field label="Vector class" htmlFor="gv-class">
          <select id="gv-class" className="input" value={vectorClass} onChange={(e) => setVectorClass(e.target.value)}>
            {["POSITIVE", "NEGATIVE", "UNKNOWN"].map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <div className="sm:col-span-2">
          <Field label="Rationale" htmlFor="gv-why" hint="Why this case matters; shown in the golden-vector report.">
            <textarea id="gv-why" className="input" rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </Field>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!id || duplicate || !rationale.trim() || a.busy} onClick={save}>Save vector</button>
      </div>
    </Modal>
  );
}
