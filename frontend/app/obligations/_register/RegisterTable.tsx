"use client";

import { useMemo } from "react";
import type { ComponentProps } from "react";
import { SearchX } from "lucide-react";
import { A, Badge, Button, DataTable, EmptyState } from "@/components/ui";
import type { Column } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { ASSURANCE_MEANING, LIFECYCLE_MEANING, assessmentRank, keyOf, scheduleLabel, subjectNameOf, timeOf, titleOf } from "./model";
import type { ObligationRecord } from "./types";

type BadgeTone = ComponentProps<typeof Badge>["tone"];

/* The shared status palette colours CLOSED as a success (it is also an issue state) and SUSPENDED as a failure. For an
   obligation both mean "not in force now", which is neither, so this register shows them neutral. Other lifecycle codes
   keep the shared colours, and an unrecognised one keeps its "unrecognised code" style. */
const LIFECYCLE_TONE: Record<string, BadgeTone> = { CLOSED: "neutral", SUSPENDED: "neutral" };

function LifecycleBadge({ code }: { code: string }) {
  const meaning = LIFECYCLE_MEANING[code];
  return <Badge code={code} tone={LIFECYCLE_TONE[code]} title={meaning ? `Lifecycle ${code}: ${meaning}` : undefined} />;
}

function ExceptionsBadge({ n }: { n: number }) {
  return <Badge tone="exception" label={`${n} APPROVED`} title="Approved exceptions are shown beside the assessment and never change it." />;
}

const exceptionsOf = (o: ObligationRecord) => (typeof o.open_exceptions === "number" ? o.open_exceptions : null);
/** Text sort key; a missing value sorts after every name (A→Z), not before it. */
const textKey = (v: string | null) => (v ? v.toLowerCase() : "￿");
const validTo = (o: ObligationRecord) => (o.valid_to ? fmtDate(o.valid_to) : "open-ended");

/** Title (linking to the dossier), key and schedule. Below the widths where they get their own columns, the subject,
 *  lifecycle, validity and approved exceptions are summarised here, so a phone shows every field without scrolling. */
function ObligationCell({ o }: { o: ObligationRecord }) {
  const title = titleOf(o), key = keyOf(o), subject = subjectNameOf(o), exceptions = exceptionsOf(o);
  return (
    <div className="min-w-28 max-w-md sm:min-w-40">
      <A href={`/obligations/${encodeURIComponent(o.lineage_id)}`}>{title ?? (o.definition.missing ? "Untitled obligation (definition not found)" : "Untitled obligation")}</A>
      <div className="mono mt-0.5 break-words text-[11px] text-slate-500">
        {o.definition.missing ? "Catalogue definition not found" : <>{key ?? "No key recorded"} · {scheduleLabel(o.definition.schedule_kind)}</>}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500 xl:hidden">
        <span className="md:hidden">{subject ?? "Subject not found"}</span>
        <span className="sm:hidden"><LifecycleBadge code={o.lifecycle} /></span>
        <span>Valid {fmtDate(o.valid_from)} → {validTo(o)}</span>
        {exceptions != null && exceptions > 0 && <ExceptionsBadge n={exceptions} />}
      </div>
    </div>
  );
}

function AssessmentCell({ o }: { o: ObligationRecord }) {
  const s = o.latest_snapshot;
  if (!s) return <Badge code="NOT_EVALUATED" label="NOT ASSESSED" title="No period snapshot has been frozen for this obligation yet." />;
  const meaning = ASSURANCE_MEANING[s.state];
  return (
    <>
      <Badge code={s.state} title={meaning ? `${s.state}: ${meaning}` : undefined} />
      <div className="mt-1 text-[11px] text-slate-600 sm:whitespace-nowrap">{fmtDate(s.period_from)} → {fmtDate(s.period_to)}</div>
      <div className="mono text-[11px] text-slate-500">{s.perspective}</div>
    </>
  );
}

/** The register's rows (already searched and filtered), sorted in the browser by the column headers. Columns appear as
 *  the width allows: phones show the obligation and its latest assessment, wider screens add the rest. */
export function RegisterTable({ rows, onClear }: { rows: ObligationRecord[]; onClear: () => void }) {
  const columns = useMemo<Column<ObligationRecord>[]>(() => [
    { key: "obligation", header: "Obligation", sortValue: (o) => textKey(titleOf(o) ?? keyOf(o)), render: (o) => <ObligationCell o={o} /> },
    { key: "subject", header: "Subject", className: "hidden md:table-cell", sortValue: (o) => textKey(subjectNameOf(o)),
      render: (o) => (
        <div className="min-w-32">
          <div className="text-slate-800">{subjectNameOf(o) ?? <span className="text-slate-500">Subject not found</span>}</div>
          {o.subject.subject_type && <div className="mono text-[11px] text-slate-500">{o.subject.subject_type}</div>}
        </div>
      ) },
    { key: "lifecycle", header: "Lifecycle", className: "hidden sm:table-cell", sortValue: (o) => o.lifecycle, render: (o) => <LifecycleBadge code={o.lifecycle} /> },
    { key: "valid", header: "Valid", className: "hidden xl:table-cell", sortValue: (o) => timeOf(o.valid_from),
      render: (o) => <><div className="whitespace-nowrap text-xs text-slate-700">{fmtDate(o.valid_from)}</div><div className="whitespace-nowrap text-[11px] text-slate-500">→ {validTo(o)}</div></> },
    { key: "assessment", header: "Latest assessment", sortValue: assessmentRank, render: (o) => <AssessmentCell o={o} /> },
    { key: "exceptions", header: "Exceptions", className: "hidden xl:table-cell", sortValue: (o) => exceptionsOf(o) ?? -1,
      render: (o) => { const n = exceptionsOf(o); return n == null ? "—" : n > 0 ? <ExceptionsBadge n={n} /> : <span className="text-xs text-slate-500">None</span>; } },
  ], []);
  return (
    <DataTable columns={columns} rows={rows} rowKey={(o) => o.lineage_id} defaultSort={{ key: "obligation", dir: "asc" }}
      empty={<EmptyState icon={SearchX} title="No obligations match your search and filters" compact
        description="Change the search or a filter, or clear them to see the whole register."
        action={<Button onClick={onClear}>Clear filters</Button>} />} />
  );
}
