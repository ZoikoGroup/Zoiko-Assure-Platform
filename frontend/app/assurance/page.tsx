"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Badge, Card, DataTable, EntitySelector, ErrorBox, LoadingSkeleton, PageHeader, Select } from "@/components/ui";
import type { Column } from "@/components/ui";

type Obl = any;
const STATE_FILTER = [{ value: "", label: "All states" }, ...["PROVEN", "NOT_PROVEN", "FAILING", "REQUIRES_REVIEW"].map((s) => ({ value: s, label: s })),
  { value: "NONE", label: "Not assessed" }];

/* Period assurance per adopted obligation, read from the backend's latest frozen snapshot.
   The period and perspective are always shown beside the state (ZA-STA-001 STA-REQ-030). */
export default function Assurance() {
  const obls = useApi<Obl[]>("/obligations");
  const org = useApi<any>("/org/tenant");
  const [entity, setEntity] = useState("");
  const [state, setState] = useState("");

  const rows = useMemo(() => (obls.data ?? []).filter((o) => o.lifecycle === "ADOPTED"
    && (!entity || o.entity_id === entity)
    && (!state || (state === "NONE" ? !o.latest_snapshot : o.latest_snapshot?.state === state))), [obls.data, entity, state]);

  const columns: Column<Obl>[] = [
    { key: "obl", header: "Obligation", sortValue: (o) => o.definition.title,
      render: (o) => <><Link href={`/obligations/${o.lineage_id}`} className="font-medium text-slate-900 hover:text-navy-700">{o.definition.title}</Link><div className="mono text-[11px] text-slate-500">{o.definition.key}</div></> },
    { key: "subject", header: "Subject", sortValue: (o) => o.subject.display_name, render: (o) => <span className="text-slate-600">{o.subject.display_name}</span> },
    { key: "state", header: "State", sortValue: (o) => o.latest_snapshot?.state ?? "~",
      render: (o) => o.latest_snapshot ? <Badge code={o.latest_snapshot.state} /> : <Badge code="NOT_EVALUATED" label="NOT ASSESSED" /> },
    { key: "period", header: "Period", sortValue: (o) => o.latest_snapshot?.period_from ?? "",
      render: (o) => o.latest_snapshot ? <span className="whitespace-nowrap text-xs text-slate-600">{fmtDate(o.latest_snapshot.period_from)} → {fmtDate(o.latest_snapshot.period_to)}</span> : "—" },
    { key: "perspective", header: "Perspective", render: (o) => <span className="mono text-[11px] text-slate-600">{o.latest_snapshot?.perspective ?? "—"}</span> },
    { key: "cutoff", header: "Record cutoff", render: (o) => <span className="mono text-[11px] text-slate-600">{o.latest_snapshot?.record_cutoff_seq != null ? `seq ${o.latest_snapshot.record_cutoff_seq}` : o.latest_snapshot ? "current" : "—"}</span> },
    { key: "exc", header: "Exceptions", sortValue: (o) => o.open_exceptions ?? 0,
      render: (o) => o.open_exceptions ? <Badge tone="exception" label={`${o.open_exceptions} APPROVED`} title="Shown beside the state; never changes it" /> : <span className="text-xs text-slate-400">None</span> },
    { key: "snap", header: "Snapshot", render: (o) => o.latest_snapshot ? <span className="text-xs text-slate-500">{fmtDate(o.latest_snapshot.created_at, true)}</span> : "—" },
  ];

  return (
    <>
      <PageHeader spec="WF-STA-05 · WF-TIM-04 · ZA-STA-001" title="Assurance Periods"
        subtitle="Latest period assurance snapshot for each adopted obligation. States are computed by the backend roll-up over frozen inputs; they cannot be edited here."
        actions={<>
          <EntitySelector entities={org.data?.entities ?? []} value={entity} onChange={setEntity} className="w-56" />
          <Select aria-label="Filter by state" options={STATE_FILTER} value={state} onChange={(e) => setState(e.target.value)} className="w-44" />
        </>} />
      <ErrorBox error={obls.error || org.error} />
      <Card bodyClassName="">
        {obls.loading && !obls.data ? <LoadingSkeleton rows={5} /> : (
          <DataTable columns={columns} rows={rows} rowKey={(o) => o.lineage_id} defaultSort={{ key: "obl", dir: "asc" }} />
        )}
      </Card>
      <p className="mt-3 text-[11px] text-slate-500">Open an obligation dossier to generate a new period snapshot, explain a result or reproduce it. Use Historical reconstruction to compare what was known at an earlier record time.</p>
    </>
  );
}
