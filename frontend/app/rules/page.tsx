"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { api, useApi } from "@/lib/api";
import { codeText, RULE_CHANGE_CLASSES } from "@/lib/codes";
import { fmtDate } from "@/lib/format";
import { useCan } from "@/lib/roles";
import { A, Badge, Card, CoverageBadge, ErrorBox, Hash, Modal, PageHeader, PermissionNote, Table, useAction } from "@/components/ui";

export default function Rules() {
  const drafts = useApi<any[]>("/rules/drafts");
  const versions = useApi<any[]>("/rules/versions");
  const [open, setOpen] = useState(false);
  const { can, why } = useCan();
  return (
    <>
      <PageHeader spec="ZA-RUL-001 §§10-17" title="Rule Authoring"
        subtitle="Typed rule ASTs and decision tables, static validation, golden vectors, shadow impact, separation-of-duties review and signed release."
        actions={<button className="btn-primary" disabled={!can("content_author")} title={why("content_author")} onClick={() => setOpen(true)}>New draft</button>} />
      <PermissionNote perms={["content_author"]} />
      <ErrorBox error={drafts.error || versions.error} />
      <div className="space-y-5">
        <Card title="Drafts">
          <Table head={["Obligation", "Mode", "Revision", "Status", "Change class", "Author", "Updated"]} empty={drafts.data?.length === 0}>
            {drafts.data?.map((d) => (
              <tr key={d.draft_id}>
                <td className="td"><A href={`/rules/${d.draft_id}`}>{d.obligation_definition}</A></td>
                <td className="td mono text-xs">{d.authoring_mode}</td>
                <td className="td">r{d.revision}</td>
                <td className="td"><Badge code={d.status} /></td>
                <td className="td mono text-xs">{codeText(d.change_class, RULE_CHANGE_CLASSES)}</td>
                <td className="td text-xs">{d.author_id}</td>
                <td className="td text-xs">{fmtDate(d.updated_at, true)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Published rule versions" subtitle="Immutable. Superseded versions remain resolvable for historical reconstruction.">
          <Table head={["Obligation", "Version", "Coverage mode", "Validity", "AST hash", "Schema / operators", "Release", "Superseded by"]}>
            {versions.data?.map((v) => (
              <tr key={v.rule_version_id}>
                <td className="td mono text-xs">{v.obligation_definition}</td>
                <td className="td">v{v.version}</td>
                <td className="td"><CoverageBadge mode={v.coverage_mode} /></td>
                <td className="td text-xs">{fmtDate(v.valid_from)} → {v.valid_to ? fmtDate(v.valid_to) : "open"}</td>
                <td className="td"><Hash value={v.ast_hash} /></td>
                <td className="td mono text-xs">{v.rule_schema_version} / {v.operator_set_version}</td>
                <td className="td"><Hash value={v.content_release_id} n={8} /></td>
                <td className="td">{v.superseded_by ? <Hash value={v.superseded_by} n={8} /> : <Badge code="CURRENT" />}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
      <NewDraft open={open} onClose={() => setOpen(false)} versions={versions.data || []} />
    </>
  );
}

function NewDraft({ open, onClose, versions }: { open: boolean; onClose: () => void; versions: any[] }) {
  const router = useRouter();
  const { data: ods } = useApi<any[]>(open ? "/catalog/obligation-definitions" : null);
  const [odId, setOdId] = useState("");
  const a = useAction();
  const fid = useId();
  const create = () => a.run(async () => {
    const od = ods?.find((o) => o.obligation_definition_id === odId);
    const cur = od?.current_rule_version;
    const full = cur ? versions.find((v) => v.rule_version_id === cur.rule_version_id) : null;
    const d = await api("/rules/drafts", {
      method: "POST", json: {
        obligation_definition_id: odId, authoring_mode: "AST",
        ast: full?.canonical_ast ?? { node_type: "ALL", node_id: "n_root", children: [] },
        source_mappings: full?.source_mappings ?? [], golden_vectors: full?.golden_vectors ?? [],
        valid_from: (full?.valid_from ?? new Date().toISOString()).slice(0, 10), change_class: "EXECUTABLE_OUTCOME",
      },
    });
    router.push(`/rules/${d.draft_id}`);
  });
  return (
    <Modal open={open} onClose={onClose} title="New rule draft">
      <ErrorBox error={a.error} />
      <label className="label" htmlFor={`${fid}-od`}>Obligation definition (required)</label>
      <select id={`${fid}-od`} required aria-describedby={`${fid}-od-hint`} className="input" value={odId} onChange={(e) => setOdId(e.target.value)}>
        <option value="">Select…</option>
        {ods?.map((o) => <option key={o.obligation_definition_id} value={o.obligation_definition_id}>{o.key} — {o.title}</option>)}
      </select>
      <p id={`${fid}-od-hint`} className="mt-2 text-xs text-slate-500">The draft starts from the current published AST (if any) so changes appear as a semantic diff.</p>
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!odId || a.busy} onClick={create}>Create draft</button></div>
    </Modal>
  );
}
