"use client";

import { useId, useMemo } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button, Field, SearchInput, Select } from "@/components/ui";
import { FILTER_KEYS, SEARCH_MAX, facet, isFiltered, type FacetOption, type FilterKey, type Filters } from "./model";
import type { ObligationRecord } from "./types";

const FILTER_TEXT: Record<FilterKey, { label: string; all: string }> = {
  lifecycle: { label: "Lifecycle", all: "All lifecycles" },
  state: { label: "Latest assessment", all: "All states" },
  schedule: { label: "Schedule", all: "All schedules" },
  subject: { label: "Subject", all: "All subjects" },
};

/** Search and filters over the rows already loaded. Each option shows how many rows it would leave, given the search
 *  and the other filters. `searchId` lets the register return focus to the search after clearing. */
export function RegisterToolbar({ rows, filters, onChange, onClear, searchId, disabled, resultText }: {
  rows: ObligationRecord[]; filters: Filters; onChange: (f: Filters) => void; onClear: () => void;
  searchId: string; disabled: boolean; resultText: ReactNode;
}) {
  const id = useId();
  const facets = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, facet(rows, filters, k)])) as Record<FilterKey, FacetOption[]>,
    [rows, filters]);
  return (
    <div role="search" aria-label="Search and filter obligations" className="border-b border-slate-200 p-4">
      <div className="grid gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4 xl:grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,1fr))] xl:items-end">
        <Field label="Search" htmlFor={searchId} className="min-[360px]:col-span-2 lg:col-span-4 xl:col-span-1">
          <SearchInput id={searchId} placeholder="Obligation title or key" maxLength={SEARCH_MAX} autoComplete="off" spellCheck={false}
            value={filters.q} disabled={disabled} onChange={(e) => onChange({ ...filters, q: e.target.value })} />
        </Field>
        {FILTER_KEYS.map((k) => (
          <Field key={k} label={FILTER_TEXT[k].label} htmlFor={`${id}-${k}`}>
            <Select id={`${id}-${k}`} value={filters[k]} disabled={disabled} onChange={(e) => onChange({ ...filters, [k]: e.target.value })}
              options={[{ value: "", label: FILTER_TEXT[k].all }, ...facets[k].map((o) => ({ value: o.value, label: `${o.label} (${o.count})` }))]} />
          </Field>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p role="status" className="text-[12px] leading-5 text-slate-600">{resultText}</p>
        <Button icon={X} disabled={disabled || !isFiltered(filters)} onClick={onClear}>Clear filters</Button>
      </div>
    </div>
  );
}
