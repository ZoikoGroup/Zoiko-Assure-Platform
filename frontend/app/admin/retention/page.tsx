"use client";

import { Lock } from "lucide-react";
import { useApi } from "@/lib/api";
import { codeText, DATA_CLASS_CODES } from "@/lib/codes";
import { fmtDate } from "@/lib/format";
import { A, Badge, Card, EmptyState, ErrorBox, PageHeader, Table } from "@/components/ui";

export default function RetentionAndLegalHolds() {
  const { data, error } = useApi<any[]>("/evidence");
  const all = data ?? [];
  const held = all.filter((e) => e.legal_hold);
  const erased = all.filter((e) => e.condition === "ERASED" || e.lifecycle === "ERASED");
  return (
    <>
      <PageHeader spec="WF-EVD-04 · WF-PRI-05 · PRI-REQ-023" title="Retention & Legal Holds"
        subtitle="Evidence under legal hold, and evidence whose payload has been erased."
        help="Holds block erasure. Place or release a hold, or erase a payload, from the evidence record; each action is recorded on your organisation's evidence ledger." />
      <ErrorBox error={error} />
      <div className="space-y-4">
        <Card title={`Active legal holds · ${held.length}`}>
          <EvidenceTable rows={held} loaded={data !== null} emptyText="No evidence is under legal hold." />
        </Card>
        <Card title={`Erased evidence · ${erased.length}`} subtitle="The active payload and its data key are destroyed; backup copies are retired as those backups expire. The manifest and ledger history remain.">
          <EvidenceTable rows={erased} loaded={data !== null} emptyText="No evidence payload has been erased." />
        </Card>
        <Card title="Retention policies">
          <EmptyState compact icon={Lock} title="Retention policies are not yet enabled"
            description="Assure does not yet model retention policies (RetentionPolicyVersion, PRI-REQ-018–020), so no retention schedule is applied or shown here. Each evidence record keeps its own validity window." />
        </Card>
      </div>
    </>
  );
}

function EvidenceTable({ rows, loaded, emptyText }: { rows: any[]; loaded: boolean; emptyText: string }) {
  if (loaded && rows.length === 0) return <p className="text-[13px] text-slate-500">{emptyText}</p>;
  return (
    <Table head={["Evidence", "Entity", "Coverage", "Classification", "Condition", "Ledger"]}>
      {rows.map((e) => (
        <tr key={e.evidence_id}>
          <td className="td"><A href={`/evidence/${e.evidence_id}`}>{e.title}</A></td>
          <td className="td mono text-xs">{e.entity_id}</td>
          <td className="td text-xs">{fmtDate(e.coverage_from)} → {fmtDate(e.coverage_to)}</td>
          <td className="td mono text-[11px]">{codeText(e.data_classification, DATA_CLASS_CODES)}</td>
          <td className="td"><Badge code={e.condition} /></td>
          <td className="td mono text-xs">#{e.ledger_seq}</td>
        </tr>
      ))}
    </Table>
  );
}
