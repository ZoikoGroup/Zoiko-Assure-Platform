"use client";

import { useEffect, useRef, useState } from "react";
import { SearchX } from "lucide-react";
import { Button, DescriptionList, EmptyState, Pagination, TBody, THead, Table, Td, Th, Tr } from "@/components/ds";
import { SortChip } from "@/components/PlatformAdmin";
import { fmtTimeUtc } from "@/components/platformDemo";
import { fmtDate } from "@/lib/format";
import { CATEGORY, type Row, type Sort, type SortKey } from "./model";
import { ActionCode, FieldValue, OutcomeBadge, ScopeBadge } from "./shared";

/* The audit event inventory: the filtered records in the chosen order, ten to a page. As elsewhere in the Super Admin,
   records are stacked cards below 1280px and a table from 1280px. The table keeps six columns so it fits beside the
   sidebar; reason and correlation_id are on the cards and in the details drawer. */

const PAGE_SIZE = 10;
const SORTS: { key: SortKey; label: string }[] = [
  { key: "time", label: "Recorded at" }, { key: "seq", label: "Record sequence" }, { key: "action", label: "Action" },
];

function Actor({ r }: { r: Row }) {
  return (
    <>
      <span className="block break-words font-medium">{r.actorName ?? <span className="font-normal italic text-fg-muted">No actor recorded</span>}</span>
      <span className="block type-code text-fg-muted">{r.e.actor_id ?? "actor_id null"}</span>
      <span className="block type-caption text-fg-secondary">{r.e.actor_type ?? "actor_type null"}</span>
    </>
  );
}

function Organisation({ r }: { r: Row }) {
  return r.e.tenant_id ? (
    <><span className="block break-words">{r.orgName ?? "Unnamed organisation"}</span><span className="block type-code text-fg-muted">{r.e.tenant_id}</span></>
  ) : <span className="block text-fg-secondary">Platform-level<span className="block type-caption text-fg-muted">tenant_id empty</span></span>;
}

export function AuditInventory({ rows, total, sort, onSort, onOpen, onReset }:
  { rows: Row[]; total: number; sort: Sort; onSort: (k: SortKey) => void; onOpen: (id: string) => void; onReset: () => void }) {
  const [page, setPage] = useState(1);
  const listTop = useRef<HTMLDivElement>(null);
  useEffect(() => setPage(1), [rows]);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const shown = rows.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const dir = (k: SortKey) => (sort.key === k ? sort.dir : null);
  const goTo = (p: number) => {
    setPage(p);
    const el = listTop.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start" });
  };

  if (rows.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-line-strong">
        <EmptyState icon={SearchX} title="No audit events match" description={`None of the ${total} demonstration records matches this search and these filters.`}
          action={<Button size="sm" variant="secondary" onClick={onReset}>Reset filters</Button>} />
      </div>
    );
  }

  return (
    <div ref={listTop} className="scroll-mt-6">
      <div role="group" aria-label="Sort audit events" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
        <span className="type-caption text-fg-muted">Sort by</span>
        {SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => onSort(s.key)} />)}
      </div>
      <ul aria-label="Audit events" className="grid gap-3 md:grid-cols-2 xl:hidden">
        {shown.map((r) => (
          <li key={r.e.id}>
            <article aria-label={`Record ${r.e.record_seq}, ${r.e.action}`} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
              <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                <div className="min-w-0">
                  <p className="type-secondary font-semibold tabular-nums text-fg">#{r.e.record_seq}</p>
                  <p className="type-code text-fg-muted">{r.e.id}</p>
                </div>
                <div className="flex flex-wrap gap-1.5"><ScopeBadge scope={r.scope} size="sm" /><OutcomeBadge outcome={r.outcome} size="sm" /></div>
              </div>
              <p className="mt-2"><ActionCode action={r.e.action} /></p>
              <p className="type-caption text-fg-secondary">{CATEGORY[r.category].label}</p>
              <DescriptionList columns={2} className="mt-3" items={[
                ["Recorded at", <span key="t" className="tabular-nums">{fmtDate(r.e.recorded_at, true)}</span>],
                ["Actor", <span key="a"><Actor r={r} /></span>],
                ["Object", <span key="o">{r.e.object_type}<span className="block break-all type-code text-fg-muted">{r.e.object_id}</span></span>],
                ["Organisation", <span key="g"><Organisation r={r} /></span>],
                ["Correlation ID", <FieldValue key="c" v={r.e.correlation_id} code />],
                ["Reason", <span key="r" className="line-clamp-3"><FieldValue v={r.e.reason} /></span>],
              ]} />
              <div className="mt-auto pt-3">
                <Button size="sm" variant="secondary" onClick={() => onOpen(r.e.id)}>View details<span className="sr-only"> of record {r.e.record_seq}</span></Button>
              </div>
            </article>
          </li>
        ))}
      </ul>
      <div className="hidden xl:block">
        <Table caption="Audit events (demonstration data). The Record, Recorded at and Action column headings sort the list." minWidth="52rem">
          <THead>
            <tr>
              <Th sort={dir("seq")} onSort={() => onSort("seq")}>Record</Th>
              <Th sort={dir("time")} onSort={() => onSort("time")}>Recorded at</Th>
              <Th>Actor</Th>
              <Th sort={dir("action")} onSort={() => onSort("action")}>Action</Th>
              <Th><span className="whitespace-normal">Object · organisation</span></Th>
              <Th><span className="sr-only">Details</span></Th>
            </tr>
          </THead>
          <TBody>
            {shown.map((r) => (
              <Tr key={r.e.id}>
                <Td className="align-top">
                  <span className="block font-semibold tabular-nums">#{r.e.record_seq}</span>
                  <code className="block whitespace-nowrap type-code text-fg-muted">{r.e.id}</code>
                </Td>
                <Td className="align-top whitespace-nowrap tabular-nums">
                  <time dateTime={r.e.recorded_at}>{fmtDate(r.e.recorded_at)}<span className="block type-caption text-fg-muted">{fmtTimeUtc(r.e.recorded_at)}</span></time>
                </Td>
                <Td className="align-top"><Actor r={r} /></Td>
                <Td className="align-top">
                  <ActionCode action={r.e.action} />
                  <span className="block type-caption text-fg-secondary">{CATEGORY[r.category].label}</span>
                  <span className="mt-1 block"><OutcomeBadge outcome={r.outcome} size="sm" /></span>
                </Td>
                <Td className="align-top">
                  <span className="block">{r.e.object_type}</span>
                  <span className="block break-all type-code text-fg-muted">{r.e.object_id}</span>
                  <span className="mt-1 block type-caption"><Organisation r={r} /></span>
                </Td>
                <Td align="right" className="align-top">
                  <Button size="sm" variant="secondary" onClick={() => onOpen(r.e.id)}>Details<span className="sr-only"> of record {r.e.record_seq}</span></Button>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
      {pageCount > 1 && (
        <div className="mt-4">
          <Pagination page={current} pageCount={pageCount} onPageChange={goTo} total={rows.length} pageSize={PAGE_SIZE} label="Audit event pages" />
        </div>
      )}
    </div>
  );
}
