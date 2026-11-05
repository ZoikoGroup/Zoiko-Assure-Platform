"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { api, type ApiError } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { Card, EmptyState, ErrorBox, Field, PageHeader, Select, Table } from "@/components/ui";

export default function PrivilegedAccessLog() {
  return (
    <>
      <PageHeader spec="WF-IAM-03/04/08 · IAM-REQ-020/022/024 · IAM §21" title="Privileged Access Log"
        subtitle="Privileged access requests, break-glass and Zoiko support sessions, and the audit trail of security-relevant actions." />
      <div className="space-y-4">
        <div className="card">
          <EmptyState icon={ShieldAlert} title="Privileged access sessions are not yet enabled"
            description="Assure does not yet support just-in-time privileged access (IAM-REQ-020), break-glass access (IAM-REQ-022) or Zoiko support access grants (IAM-REQ-024): they need phishing-resistant step-up authentication (AAL3, IAM-REQ-006) and an approval policy, neither of which exists yet. So there are no privileged session records to show. The audit trail below is recorded today." />
        </div>
        <Audit />
      </div>
    </>
  );
}

type AuditPage = { items: any[]; page: { next_cursor: string | null; has_more: boolean; watermark: { cell_id: string; record_seq: number }; limit: number } };

/* Allowlisted categories (the part of an audit action before its first dot); GET /audit-events filters on it. */
const CATEGORIES = [
  { value: "", label: "All actions" },
  { value: "authorization", label: "Refused requests (authorization)" },
  { value: "auth", label: "Sign-in and sessions (auth)" },
  { value: "role", label: "Role assignments (role)" },
  { value: "organisation", label: "Platform administration of this organisation" },
  { value: "platform", label: "Platform reads of this organisation" },
  { value: "entity", label: "Legal entities (entity)" },
  { value: "evidence", label: "Evidence (evidence)" },
];

/** The organisation's audit trail, newest first, from the cursor-paginated GET /audit-events (ZA-API-001 §04/§11). The
 *  first page pins the record watermark, so "Load more" never mixes in records written after the list was opened. */
function Audit() {
  const { actorId } = useAuth();
  const [category, setCategory] = useState("");
  const [rows, setRows] = useState<any[] | null>(null);
  const [page, setPage] = useState<AuditPage["page"] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (cursor: string | null) => {
    const qs = new URLSearchParams({ limit: "100" });
    if (category) qs.set("category", category);
    if (cursor) qs.set("cursor", cursor);
    setBusy(true);
    setError(null);
    try {
      const r = await api<AuditPage>(`/audit-events?${qs}`);
      setRows((prev) => (cursor && prev ? [...prev, ...r.items] : r.items));
      setPage(r.page);
    } catch (e) {
      setError(e as ApiError);
      if (!cursor) setRows(null);
    } finally {
      setBusy(false);
    }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (actorId) { setRows(null); setPage(null); load(null); } }, [actorId, category]);

  return (
    <Card subtitle={`Privileged and governance actions with prior and new state, newest first${page ? `, as recorded up to ${page.watermark.cell_id} #${page.watermark.record_seq}` : ""}. Downloads are audited too.`}>
      <div className="mb-3 max-w-sm">
        <Field label="Show" htmlFor="audit-category">
          <Select id="audit-category" value={category} onChange={(e) => setCategory(e.target.value)} options={CATEGORIES} />
        </Field>
      </div>
      <ErrorBox error={error} />
      <Table head={["Seq", "Action", "Actor", "Object", "Transition", "Reason", "At"]} empty={rows?.length === 0} loading={!rows && !error}>
        {rows?.map((e) => (
          <tr key={e.id}>
            <td className="td mono text-xs">#{e.record_seq ?? "—"}</td><td className="td mono text-xs">{e.action}</td>
            <td className="td text-xs">{e.actor_id}<div className="text-slate-500">{e.actor_type}</div></td>
            <td className="td text-xs">{e.object_type}<div className="mono text-[11px] text-slate-500">{e.object_id?.slice(0, 13)}</div></td>
            <td className="td text-xs">{e.prior_state ?? "∅"} → {e.new_state ?? "∅"}</td><td className="td text-xs">{e.reason ?? "—"}</td>
            <td className="td text-xs">{fmtDate(e.recorded_at, true)}</td>
          </tr>
        ))}
      </Table>
      {page?.has_more && (
        <div className="mt-3 flex justify-center">
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => load(page.next_cursor)}>{busy ? "Loading…" : "Load more"}</button>
        </div>
      )}
    </Card>
  );
}
