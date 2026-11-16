"use client";

import { useEffect, useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button, Field, Fieldset, SearchInput, Select, TextInput } from "@/components/ds";
import { count } from "@/components/platformQueue";
import { ACTOR_TYPES, CATEGORIES, CATEGORY, NONE, NO_FILTERS, OUTCOME, SCOPE, isFiltering, rangeInvalid, type Filters, type Row } from "./model";

/* The filters shared by the inventory and the timeline. Everything runs on the records already loaded. Dates are UTC
   calendar days, matching recorded_at. */

export function AuditFilters({ rows, matched, filters, onChange }:
  { rows: Row[]; matched: number; filters: Filters; onChange: (f: Filters) => void }) {
  const [said, setSaid] = useState("");
  const set = (next: Partial<Filters>) => onChange({ ...filters, ...next });
  const filtering = isFiltering(filters);
  const badRange = rangeInvalid(filters);
  const summary = filtering ? `${matched} of ${count(rows.length, "event")} match` : count(rows.length, "event");

  const actions = useMemo(() => [...new Set(rows.map((r) => r.e.action))].sort(), [rows]);
  const orgs = useMemo(() => {
    const m = new Map(rows.flatMap((r) => (r.e.tenant_id ? [[r.e.tenant_id, r.orgName ?? r.e.tenant_id] as const] : [])));
    return [...m.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [rows]);
  const days = useMemo(() => rows.map((r) => r.e.recorded_at.slice(0, 10)).sort(), [rows]);

  // The match count is announced once typing pauses, not on every keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setSaid(filtering ? (matched ? summary : "No events match") : ""), 600);
    return () => window.clearTimeout(t);
  }, [filtering, matched, summary]);

  return (
    <div>
      <form role="search" aria-label="Filter audit events" onSubmit={(e) => e.preventDefault()}
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <Field label="Search" hint="Event ID, actor, action, object ID or organisation">
          <SearchInput placeholder="e.g. status_changed or Fabrikam" maxLength={100} value={filters.q} autoComplete="off"
            onChange={(e) => set({ q: e.target.value })} onClear={() => set({ q: "" })} />
        </Field>
        <Field label="Event category">
          <Select value={filters.category} onChange={(e) => set({ category: e.target.value as Filters["category"] })}
            options={[{ value: "", label: "All categories" }, ...CATEGORIES.map((c) => ({ value: c, label: `${SCOPE[CATEGORY[c].scope].label} · ${CATEGORY[c].label}` }))]} />
        </Field>
        <Field label="Action">
          <Select value={filters.action} onChange={(e) => set({ action: e.target.value })}
            options={[{ value: "", label: "All actions" }, ...actions.map((a) => ({ value: a, label: a }))]} />
        </Field>
        <Field label="Organisation">
          <Select value={filters.tenant} onChange={(e) => set({ tenant: e.target.value })}
            options={[{ value: "", label: "All organisations and platform" }, { value: NONE, label: "Platform-level (empty tenant_id)" }, ...orgs]} />
        </Field>
        <Field label="Actor type">
          <Select value={filters.actorType} onChange={(e) => set({ actorType: e.target.value })}
            options={[{ value: "", label: "All actor types" }, ...ACTOR_TYPES.map((t) => ({ value: t, label: t })), { value: NONE, label: "Not recorded (null)" }]} />
        </Field>
        <Field label="Outcome (demonstration only)">
          <Select value={filters.outcome} onChange={(e) => set({ outcome: e.target.value as Filters["outcome"] })}
            options={[{ value: "", label: "All outcomes" }, { value: "SUCCEEDED", label: OUTCOME.SUCCEEDED.label }, { value: "DENIED", label: OUTCOME.DENIED.label }]} />
        </Field>
        <Fieldset legend="Recorded between (UTC dates)" className="min-w-0 sm:col-span-2" error={badRange ? "The From date is after the To date." : undefined}>
          <div className="grid gap-3 min-[420px]:grid-cols-2">
            <Field label="From"><TextInput type="date" value={filters.from} min={days[0]} max={days[days.length - 1]} onChange={(e) => set({ from: e.target.value })} /></Field>
            <Field label="To"><TextInput type="date" value={filters.to} min={days[0]} max={days[days.length - 1]} onChange={(e) => set({ to: e.target.value })} /></Field>
          </div>
        </Fieldset>
      </form>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="type-caption font-semibold text-fg-secondary">{badRange ? "No events: the date range is invalid" : summary}</p>
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => onChange(NO_FILTERS)} disabled={!filtering}>Reset filters</Button>
      </div>
      <p role="status" aria-live="polite" className="sr-only">{said}</p>
    </div>
  );
}
