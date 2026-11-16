"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw, SearchX } from "lucide-react";
import {
  Button, DescriptionList, EmptyState, Field, Pagination, SearchInput, Select, TBody, THead, Table, Td, Th, Tr,
} from "@/components/ds";
import { RegistrySection, SortChip } from "@/components/PlatformAdmin";
import { count } from "@/components/platformQueue";
import { fmtDate } from "@/lib/format";
import { DemoTag, INTEGRITY_STATUS, IntegrityStatusBadge, Missing, SOURCE_TYPE_LABEL, fmtTimeUtc, isOutdated } from "./shared";
import { INTEGRITY_STATUSES, SOURCE_TYPES, type IntegrityStatus, type RegulatorySource, type SourceType } from "./types";

/* The source inventory: search, filters, sorting and pagination, all on the records already loaded. As elsewhere in
   the Super Admin, rows are stacked cards below 1280px (where the columns do not fit beside the sidebar) and a table
   from 1280px, both driven by the same sort state. */

const PAGE_SIZE = 8;

type SortKey = "name" | "lastVerified" | "status";
type Sort = { key: SortKey; dir: "asc" | "desc" };
const DEFAULT_SORT: Sort = { key: "name", dir: "asc" };
/** The direction a column sorts in when first chosen: names A to Z, newest verification first, most urgent status first. */
const FIRST_DIR: Record<SortKey, Sort["dir"]> = { name: "asc", lastVerified: "desc", status: "asc" };
const SORTS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Source name" }, { key: "lastVerified", label: "Last verified" }, { key: "status", label: "Status" },
];

type Filters = { q: string; type: "" | SourceType; jurisdiction: string; status: "" | IntegrityStatus };
const NO_FILTERS: Filters = { q: "", type: "", jurisdiction: "", status: "" };

const byName = (a: RegulatorySource, b: RegulatorySource) => a.name.localeCompare(b.name);

function compare(a: RegulatorySource, b: RegulatorySource, sort: Sort): number {
  if (sort.key === "lastVerified" && (!a.lastVerifiedAt || !b.lastVerifiedAt)) {
    // Sources never verified sort last in either direction.
    return a.lastVerifiedAt ? -1 : b.lastVerifiedAt ? 1 : byName(a, b);
  }
  const r = sort.key === "name" ? byName(a, b)
    : sort.key === "status" ? INTEGRITY_STATUS[a.status].rank - INTEGRITY_STATUS[b.status].rank
    : (a.lastVerifiedAt ?? "").localeCompare(b.lastVerifiedAt ?? "");
  return (sort.dir === "asc" ? r : -r) || byName(a, b);
}

function Version({ s }: { s: RegulatorySource }) {
  if (!s.currentVersion) return <Missing>Not recorded</Missing>;
  return (
    <>
      <span className="type-code">{s.currentVersion}</span>
      {isOutdated(s) && <span className="block type-caption font-semibold text-status-warning">Publisher has issued {s.publisherLatestVersion}</span>}
    </>
  );
}

function LastVerified({ s }: { s: RegulatorySource }) {
  if (!s.lastVerifiedAt) return <Missing>Never verified</Missing>;
  return (
    <time dateTime={s.lastVerifiedAt} className="whitespace-nowrap tabular-nums">
      {fmtDate(s.lastVerifiedAt)}<span className="block type-caption text-fg-muted">{fmtTimeUtc(s.lastVerifiedAt)}</span>
    </time>
  );
}

export function SourceInventory({ sources, onOpen }: { sources: RegulatorySource[]; onOpen: (id: string) => void }) {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const [page, setPage] = useState(1);
  const [said, setSaid] = useState("");
  const listTop = useRef<HTMLDivElement>(null);

  const jurisdictions = useMemo(() => {
    const m = new Map(sources.map((s) => [s.jurisdiction.code, s.jurisdiction.name]));
    return [...m.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [sources]);

  const filtered = useMemo(() => {
    const q = f.q.trim().toLocaleLowerCase();
    return sources
      .filter((s) => (!q || s.name.toLocaleLowerCase().includes(q)) && (!f.type || s.type === f.type)
        && (!f.jurisdiction || s.jurisdiction.code === f.jurisdiction) && (!f.status || s.status === f.status))
      .sort((a, b) => compare(a, b, sort));
  }, [sources, f, sort]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const rows = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const filtering = f.q.trim() !== "" || f.type !== "" || f.jurisdiction !== "" || f.status !== "";
  const changed = filtering || sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir;
  const summary = filtering ? `${filtered.length} of ${count(sources.length, "source")} match` : count(sources.length, "source");

  // The match count is announced once typing pauses, not on every keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setSaid(filtering ? (filtered.length ? summary : "No sources match") : ""), 600);
    return () => window.clearTimeout(t);
  }, [filtering, filtered.length, summary]);

  const update = (next: Partial<Filters>) => { setF({ ...f, ...next }); setPage(1); };
  const reset = () => { setF(NO_FILTERS); setSort(DEFAULT_SORT); setPage(1); };
  const toggle = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] }));
    setPage(1);
  };
  const dir = (key: SortKey) => (sort.key === key ? sort.dir : null);
  const goTo = (p: number) => {
    setPage(p);
    // Bring the top of the list back into view when the new page starts above the screen.
    const el = listTop.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start" });
  };

  return (
    <RegistrySection title="Source inventory"
      aside={<span className="inline-flex items-center gap-2"><DemoTag /><span className="type-caption text-fg-muted">{count(sources.length, "source")}</span></span>}
      description="Fictional regulatory sources held as demonstration data. Search, filter and sort them, then open a source for its details and checks.">
      <form role="search" aria-label="Filter sources" onSubmit={(e) => e.preventDefault()}
        className="grid gap-3 md:grid-cols-3 xl:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]">
        <Field label="Search source name" className="md:col-span-3 xl:col-span-1">
          <SearchInput placeholder="Source name" maxLength={100} value={f.q} autoComplete="off"
            onChange={(e) => update({ q: e.target.value })} onClear={() => update({ q: "" })} />
        </Field>
        <Field label="Source type">
          <Select value={f.type} onChange={(e) => update({ type: e.target.value as Filters["type"] })}
            options={[{ value: "", label: "All source types" }, ...SOURCE_TYPES.map((t) => ({ value: t, label: SOURCE_TYPE_LABEL[t] }))]} />
        </Field>
        <Field label="Jurisdiction">
          <Select value={f.jurisdiction} onChange={(e) => update({ jurisdiction: e.target.value })}
            options={[{ value: "", label: "All jurisdictions" }, ...jurisdictions]} />
        </Field>
        <Field label="Integrity status">
          <Select value={f.status} onChange={(e) => update({ status: e.target.value as Filters["status"] })}
            options={[{ value: "", label: "All statuses" }, ...INTEGRITY_STATUSES.map((s) => ({ value: s, label: INTEGRITY_STATUS[s].label }))]} />
        </Field>
      </form>

      <div ref={listTop} className="mt-4 flex scroll-mt-6 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="type-caption font-semibold text-fg-secondary">{summary}</p>
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={reset} disabled={!changed}>Reset filters</Button>
      </div>
      <p role="status" aria-live="polite" className="sr-only">{said}</p>

      <div className="mt-3">
        {filtered.length === 0 ? (
          <div className="rounded-card border border-dashed border-line-strong">
            <EmptyState icon={SearchX} title="No sources match" description="No demonstration source matches this search and these filters."
              action={<Button size="sm" variant="secondary" onClick={reset}>Reset filters</Button>} />
          </div>
        ) : (
          <>
            <div role="group" aria-label="Sort sources" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
              <span className="type-caption text-fg-muted">Sort by</span>
              {SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
            </div>
            <ul aria-label="Sources" className="grid gap-3 md:grid-cols-2 xl:hidden">
              {rows.map((s) => (
                <li key={s.id}>
                  <article aria-label={s.name} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
                    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                      <div className="min-w-0 flex-[1_1_12rem]">
                        <p className="break-words type-secondary font-semibold text-fg">{s.name}</p>
                        <p className="type-code text-fg-muted">{s.id}</p>
                      </div>
                      <IntegrityStatusBadge status={s.status} size="sm" />
                    </div>
                    <DescriptionList columns={2} className="mt-3" items={[
                      ["Publisher", s.publisher],
                      ["Source type", SOURCE_TYPE_LABEL[s.type]],
                      ["Jurisdiction", s.jurisdiction.name],
                      ["Current version", <Version key="v" s={s} />],
                      ["Last verified", <LastVerified key="l" s={s} />],
                    ]} />
                    <div className="mt-auto pt-3">
                      <Button size="sm" variant="secondary" onClick={() => onOpen(s.id)}>View details<span className="sr-only"> of {s.name}</span></Button>
                    </div>
                  </article>
                </li>
              ))}
            </ul>
            <div className="hidden xl:block">
              <Table caption="Source inventory (demonstration data). The Source, Last verified and Integrity status column headings sort the list." minWidth="54rem">
                <THead>
                  <tr>
                    <Th sort={dir("name")} onSort={() => toggle("name")}>Source · publisher</Th>
                    {/* Two-word headings may wrap, so the table fits beside the sidebar at 1280px. */}
                    <Th><span className="whitespace-normal">Source type</span></Th>
                    <Th>Jurisdiction</Th>
                    <Th><span className="whitespace-normal">Current version</span></Th>
                    <Th sort={dir("lastVerified")} onSort={() => toggle("lastVerified")}>Last verified</Th>
                    <Th sort={dir("status")} onSort={() => toggle("status")}>Integrity status</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </tr>
                </THead>
                <TBody>
                  {rows.map((s) => (
                    <Tr key={s.id}>
                      <Td className="align-top">
                        <span className="block break-words font-semibold">{s.name}</span>
                        <span className="block type-caption text-fg-secondary">{s.publisher}</span>
                        <span className="block type-code text-fg-muted">{s.id}</span>
                      </Td>
                      <Td className="align-top text-fg-secondary">{SOURCE_TYPE_LABEL[s.type]}</Td>
                      <Td className="align-top text-fg-secondary">{s.jurisdiction.name}</Td>
                      <Td className="align-top"><Version s={s} /></Td>
                      <Td className="align-top"><LastVerified s={s} /></Td>
                      <Td className="align-top"><IntegrityStatusBadge status={s.status} size="sm" /></Td>
                      <Td align="right" className="align-top">
                        <Button size="sm" variant="secondary" onClick={() => onOpen(s.id)}>Details<span className="sr-only"> of {s.name}</span></Button>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
            {pageCount > 1 && (
              <div className="mt-4">
                <Pagination page={current} pageCount={pageCount} onPageChange={goTo} total={filtered.length} pageSize={PAGE_SIZE} label="Source inventory pages" />
              </div>
            )}
          </>
        )}
      </div>
    </RegistrySection>
  );
}
