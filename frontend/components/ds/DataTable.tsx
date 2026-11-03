"use client";

import type { ComponentProps, ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useSortedRows, type SortState } from "@/components/ui";
import { EmptyState, ErrorState, Skeleton } from "./Feedback";
import { Checkbox } from "./Form";

/* Tables on the foundation tokens: primitives (Table, THead, TBody, Tr, Th, Td) for custom layouts, and DataTable for
   the common case with sorting, row selection and loading, empty and error states. Sorting is the application kit's
   useSortedRows, so both table families sort identically. Narrow screens scroll the table horizontally. */

type Align = "left" | "right" | "center";
const ALIGN: Record<Align, string> = { left: "text-left", right: "text-right", center: "text-center" };

export function Table({ caption, captionVisible = false, minWidth = "40rem", busy, children, className = "" }:
  { caption: ReactNode; captionVisible?: boolean; minWidth?: string; busy?: boolean; children: ReactNode; className?: string }) {
  // `relative` makes the scroll box the containing block for screen-reader-only (absolutely positioned) cell text, which
  // would otherwise escape it and widen the page whenever the table scrolls.
  return (
    <div className={`scroll-thin relative overflow-x-auto rounded-card border border-line bg-surface-card ${className}`}>
      <table aria-busy={busy || undefined} className="w-full border-collapse text-left" style={{ minWidth }}>
        <caption className={captionVisible ? "px-cell-x py-3 text-left type-caption text-fg-muted" : "sr-only"}>{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export const THead = ({ children }: { children: ReactNode }) => <thead className="bg-surface-raised">{children}</thead>;
export const TBody = ({ children }: { children: ReactNode }) => <tbody>{children}</tbody>;

/** A row. `selected` (set only when rows are selectable) highlights it and marks the leading edge with the accent. */
export function Tr({ selected, className = "", ...rest }: ComponentProps<"tr"> & { selected?: boolean }) {
  return (
    <tr aria-selected={selected} {...rest}
      className={`border-b border-line transition-colors last:border-0 ${selected
        ? "bg-accent/8 hover:bg-accent/12 [&>td:first-child]:shadow-[inset_3px_0_0_var(--za-accent)]"
        : "hover:bg-fg/3"} ${className}`} />
  );
}

/** A header cell. With `onSort` it becomes a sort button and reports aria-sort. */
export function Th({ children, align = "left", sort, onSort, className = "", ...rest }:
  Omit<ComponentProps<"th">, "align"> & { align?: Align; sort?: "asc" | "desc" | null; onSort?: () => void }) {
  return (
    <th scope="col" aria-sort={sort ? (sort === "asc" ? "ascending" : "descending") : undefined} {...rest}
      className={`whitespace-nowrap border-b border-line px-cell-x py-cell-y type-label text-fg-muted ${ALIGN[align]} ${className}`}>
      {onSort ? (
        <button type="button" onClick={onSort} className={`inline-flex items-center gap-1 rounded uppercase hover:text-fg ${sort ? "text-fg" : ""}`}>
          {children}
          {sort === "asc" ? <ArrowUp size={12} aria-hidden /> : sort === "desc" ? <ArrowDown size={12} aria-hidden /> : <ArrowUpDown size={12} aria-hidden className="opacity-50" />}
        </button>
      ) : children}
    </th>
  );
}

export function Td({ align = "left", className = "", ...rest }: Omit<ComponentProps<"td">, "align"> & { align?: Align }) {
  return <td {...rest} className={`px-cell-x py-cell-y align-middle type-secondary text-fg ${ALIGN[align]} ${className}`} />;
}

export type DataColumn<T> = {
  key: string; header: ReactNode; render: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined; align?: Align; className?: string;
};

export type DataTableProps<T> = {
  columns: DataColumn<T>[]; rows: T[]; rowKey: (row: T) => string; caption: string; captionVisible?: boolean;
  loading?: boolean; error?: unknown; onRetry?: () => void;
  empty?: { title: string; description?: ReactNode; action?: ReactNode };
  /** Row selection with checkboxes. `rowLabel` names each row for its checkbox ("Select …"). */
  selectable?: boolean; selected?: Set<string>; onSelectedChange?: (keys: Set<string>) => void; rowLabel?: (row: T) => string;
  defaultSort?: SortState; minWidth?: string;
};

export function DataTable<T>({ columns, rows, rowKey, caption, captionVisible, loading = false, error, onRetry, empty, selectable = false,
  selected = new Set<string>(), onSelectedChange, rowLabel, defaultSort, minWidth }: DataTableProps<T>) {
  const { sorted, sort, toggle } = useSortedRows(rows, columns, defaultSort);
  const span = columns.length + (selectable ? 1 : 0);
  const keys = rows.map(rowKey);
  const all = keys.length > 0 && keys.every((k) => selected.has(k));
  const some = !all && keys.some((k) => selected.has(k));
  const setRow = (k: string, on: boolean) => { const next = new Set(selected); if (on) next.add(k); else next.delete(k); onSelectedChange?.(next); };
  const state = loading ? "loading" : error ? "error" : rows.length === 0 ? "empty" : "rows";

  return (
    <Table caption={caption} captionVisible={captionVisible} minWidth={minWidth} busy={loading}>
      <THead>
        <tr>
          {selectable && (
            <Th className="w-12">
              <Checkbox label="Select all rows" hideLabel checked={all} indeterminate={some} disabled={state !== "rows"}
                onChange={(e) => onSelectedChange?.(e.target.checked ? new Set(keys) : new Set())} />
            </Th>
          )}
          {columns.map((c) => (
            <Th key={c.key} align={c.align} className={c.className} sort={sort?.key === c.key ? sort.dir : null} onSort={c.sortValue ? () => toggle(c.key) : undefined}>
              {c.header}
            </Th>
          ))}
        </tr>
      </THead>
      <TBody>
        {state === "loading" && Array.from({ length: 4 }, (_, i) => (
          <tr key={i} className="border-b border-line last:border-0">
            {Array.from({ length: span }, (_, j) => <td key={j} className="px-cell-x py-3.5"><Skeleton className={`h-3.5 ${j === 0 && selectable ? "w-4.5" : "w-4/5"}`} /></td>)}
          </tr>
        ))}
        {state === "error" && <tr><td colSpan={span}><ErrorState error={error} title="This table could not be loaded" onRetry={onRetry} compact /></td></tr>}
        {state === "empty" && <tr><td colSpan={span}><EmptyState title={empty?.title ?? "Nothing to show"} description={empty?.description} action={empty?.action} compact /></td></tr>}
        {state === "rows" && sorted.map((r) => {
          const k = rowKey(r);
          const on = selected.has(k);
          return (
            <Tr key={k} selected={selectable ? on : undefined}>
              {selectable && <Td><Checkbox label={`Select ${rowLabel?.(r) ?? k}`} hideLabel checked={on} onChange={(e) => setRow(k, e.target.checked)} /></Td>}
              {columns.map((c) => <Td key={c.key} align={c.align} className={c.className}>{c.render(r)}</Td>)}
            </Tr>
          );
        })}
      </TBody>
    </Table>
  );
}
