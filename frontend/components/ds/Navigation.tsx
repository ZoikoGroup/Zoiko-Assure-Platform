"use client";

import Link from "next/link";
import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Check, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useDisclosure, useRovingTabs } from "@/components/ui";
import { buttonClasses, type ButtonVariant } from "./Button";

/* Navigation on the foundation tokens. Tabs use the application kit's roving focus (useRovingTabs) and DropdownMenu its
   disclosure behaviour (useDisclosure): a button showing ordinary links and buttons reached with Tab, closed by Escape,
   an outside click or tabbing away, the same pattern as the application's own menus. */

/* ---------------------------------------------------------------------------------------------------- Tabs */
export type TabItem = { value: string; label: ReactNode; icon?: LucideIcon; count?: number; disabled?: boolean };
const tabId = (id: string, v: string) => `${id}-tab-${v.replace(/[^A-Za-z0-9]+/g, "-")}`;

/** A tab list. Pair with TabPanel using the same `id`; only the selected panel is rendered. */
export function Tabs({ id, label, items, value, onChange, variant = "underline" }:
  { id: string; label: string; items: TabItem[]; value: string; onChange: (v: string) => void; variant?: "underline" | "pills" }) {
  const { refs, onKeyDown } = useRovingTabs(items.map((t) => t.value), onChange, (v) => !!items.find((t) => t.value === v)?.disabled);
  const pills = variant === "pills";
  return (
    <div role="tablist" aria-label={label}
      className={pills ? "inline-flex max-w-full gap-1 overflow-x-auto rounded-control border border-line bg-surface-control p-1" : "no-scrollbar flex gap-1 overflow-x-auto border-b border-line"}>
      {items.map((t, i) => {
        const on = t.value === value;
        const Icon = t.icon;
        return (
          <button key={t.value} ref={(el) => { refs.current[i] = el; }} type="button" role="tab" id={tabId(id, t.value)} aria-selected={on}
            aria-controls={on ? `${id}-panel` : undefined} tabIndex={on ? 0 : -1} disabled={t.disabled} onClick={() => onChange(t.value)} onKeyDown={onKeyDown(i)}
            className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap type-secondary font-medium transition-colors disabled:cursor-not-allowed disabled:text-fg-disabled ${pills
              ? `h-8 rounded-[calc(var(--radius-control)-4px)] px-3 ${on ? "bg-surface-card text-fg shadow-card" : "text-fg-secondary enabled:hover:text-fg"}`
              : `-mb-px border-b-2 px-3 pb-2.5 pt-1.5 ${on ? "border-accent text-fg" : "border-transparent text-fg-secondary enabled:hover:text-fg"}`}`}>
            {Icon && <Icon size={15} aria-hidden />}
            {t.label}
            {t.count != null && <span className="rounded-full bg-fg/10 px-1.5 type-caption tabular-nums text-fg-secondary">{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** The selected tab's content, labelled by its tab and focusable so keyboard users can move into it. */
export function TabPanel({ id, value, children, className = "pt-4" }: { id: string; value: string; children: ReactNode; className?: string }) {
  return <div role="tabpanel" id={`${id}-panel`} aria-labelledby={tabId(id, value)} tabIndex={0} className={className}>{children}</div>;
}

/* ----------------------------------------------------------------------------------------------- Breadcrumbs */
/** Where the page sits. The last item is the current page. With more than three items, phones show the first and the
 *  last two, with an ellipsis between. */
export function Breadcrumbs({ items, className = "" }: { items: { label: string; href?: string }[]; className?: string }) {
  const collapse = items.length > 3;
  return (
    <nav aria-label="Breadcrumb" className={className}>
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1 type-secondary">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          const hidden = collapse && i > 0 && i < items.length - 2;
          return (
            <li key={`${c.label}-${i}`} className={`items-center gap-1.5 ${hidden ? "hidden sm:flex" : "flex"}`}>
              {i > 0 && <span aria-hidden className="text-fg-muted">/</span>}
              {last ? <span aria-current="page" className="font-medium text-fg">{c.label}</span>
                : c.href ? <Link href={c.href} className="rounded text-fg-secondary underline-offset-4 hover:text-fg hover:underline">{c.label}</Link>
                : <span className="text-fg-secondary">{c.label}</span>}
              {collapse && i === 0 && <span aria-hidden className="flex items-center gap-1.5 text-fg-muted sm:hidden"><span>/</span>…</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/* ------------------------------------------------------------------------------------------------ Pagination */
/** Page numbers with the first and last always shown and an ellipsis for gaps. */
export function pageItems(page: number, count: number, siblings = 1): (number | "gap")[] {
  if (count <= 1) return [1];
  const start = Math.max(2, page - siblings), end = Math.min(count - 1, page + siblings);
  return [1, ...(start > 2 ? ["gap" as const] : []), ...Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => start + i), ...(end < count - 1 ? ["gap" as const] : []), count];
}

export function Pagination({ page, pageCount, onPageChange, total, pageSize, label = "Pagination" }:
  { page: number; pageCount: number; onPageChange: (p: number) => void; total?: number; pageSize?: number; label?: string }) {
  const cell = "inline-flex h-9 min-w-9 items-center justify-center rounded-control border px-2 type-secondary font-medium tabular-nums transition-colors disabled:cursor-not-allowed disabled:text-fg-disabled";
  const idle = "border-transparent text-fg-secondary enabled:hover:bg-fg/5 enabled:hover:text-fg";
  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-between gap-3">
      {total != null && pageSize != null && (
        <p className="type-caption text-fg-muted">Showing {Math.min(total, (page - 1) * pageSize + 1)}–{Math.min(total, page * pageSize)} of {total}</p>
      )}
      <ul className="flex items-center gap-1">
        <li><button type="button" className={`${cell} ${idle}`} disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Previous page"><ChevronLeft size={16} aria-hidden /></button></li>
        {pageItems(page, pageCount).map((p, i) => (
          <li key={p === "gap" ? `gap-${i}` : p} className="hidden sm:block">
            {p === "gap" ? <span aria-hidden className="inline-flex h-9 min-w-9 items-center justify-center type-secondary text-fg-muted">…</span> : (
              <button type="button" aria-label={`Page ${p}`} aria-current={p === page ? "page" : undefined} onClick={() => onPageChange(p)}
                className={`${cell} ${p === page ? "border-accent/60 bg-accent/10 text-accent-fg" : idle}`}>{p}</button>
            )}
          </li>
        ))}
        <li className="px-2 type-caption text-fg-secondary sm:hidden" aria-current="page">Page {page} of {pageCount}</li>
        <li><button type="button" className={`${cell} ${idle}`} disabled={page >= pageCount} onClick={() => onPageChange(page + 1)} aria-label="Next page"><ChevronRight size={16} aria-hidden /></button></li>
      </ul>
    </nav>
  );
}

/* --------------------------------------------------------------------------------------------- DropdownMenu */
const MenuContext = createContext<() => void>(() => {});

/** A button that opens a list of actions. Choosing an item closes the menu (MenuItem does this for you). With `icon`
 *  and no visible label, `label` is the button's accessible name. */
export function DropdownMenu({ label, icon: Icon, iconOnly = false, variant = "secondary", align = "left", width = "w-56", children }: {
  label: string; icon?: LucideIcon; iconOnly?: boolean; variant?: ButtonVariant; align?: "left" | "right"; width?: string; children: ReactNode;
}) {
  const { open, setOpen, root, btn, panelId, close } = useDisclosure();
  return (
    <div ref={root} className="relative inline-block">
      <button ref={btn} type="button" aria-expanded={open} aria-controls={open ? panelId : undefined} aria-label={iconOnly ? label : undefined}
        title={iconOnly ? label : undefined} onClick={() => setOpen(!open)} className={buttonClasses({ variant, square: iconOnly })}>
        {Icon && <Icon size={16} aria-hidden />}
        {!iconOnly && <>{label}<ChevronDown size={15} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} /></>}
      </button>
      {open && (
        <div id={panelId} className={`absolute z-40 mt-2 ${align === "right" ? "right-0" : "left-0"} ${width} overflow-hidden rounded-card border border-line bg-surface-modal py-1.5 shadow-modal`}>
          <MenuContext.Provider value={close}>{children}</MenuContext.Provider>
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon: Icon, children, onSelect, href, danger = false, disabled = false, hint }: {
  icon?: LucideIcon; children: ReactNode; onSelect?: () => void; href?: string; danger?: boolean; disabled?: boolean; hint?: ReactNode;
}) {
  const close = useContext(MenuContext);
  const cls = `flex w-full items-center gap-2.5 px-3 py-2 text-left type-secondary transition-colors ${disabled
    ? "cursor-not-allowed text-fg-disabled" : danger ? "text-status-critical hover:bg-status-critical-tint" : "text-fg hover:bg-fg/5"}`;
  const inner = <>{Icon && <Icon size={15} aria-hidden className={danger || disabled ? "" : "text-fg-muted"} />}<span className="flex-1">{children}</span>{hint && <span className="type-caption text-fg-muted">{hint}</span>}</>;
  if (href && !disabled) return <Link href={href} onClick={close} className={cls}>{inner}</Link>;
  return <button type="button" disabled={disabled} onClick={() => { onSelect?.(); close(); }} className={cls}>{inner}</button>;
}

export const MenuSeparator = () => <div role="separator" className="my-1.5 border-t border-line" />;

/* ---------------------------------------------------------------------------------------------------- Stepper */
export type StepItem = { id: string; label: string; description?: string };

/** Progress through a multi-step task. The current step carries aria-current="step"; with `onStepClick`, completed
 *  steps become buttons for going back. Phones show "Step n of m" with the current label. */
export function Stepper({ steps, current, onStepClick, label = "Progress" }:
  { steps: StepItem[]; current: number; onStepClick?: (index: number) => void; label?: string }) {
  return (
    <nav aria-label={label}>
      <p className="type-secondary text-fg sm:hidden">
        <span className="text-fg-muted">Step {Math.min(current + 1, steps.length)} of {steps.length}:</span> <span className="font-semibold">{steps[Math.min(current, steps.length - 1)]?.label}</span>
      </p>
      <ol className="hidden items-start gap-2 sm:flex">
        {steps.map((s, i) => {
          const done = i < current, here = i === current;
          const mark = (
            <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border type-caption font-semibold ${done
              ? "border-accent bg-accent text-on-accent" : here ? "border-accent bg-accent/15 text-accent-fg" : "border-line-strong text-fg-muted"}`}>
              {done ? <Check size={14} strokeWidth={3} aria-hidden /> : i + 1}
            </span>
          );
          const text = (
            <span className="min-w-0 text-left">
              <span className={`block type-secondary font-semibold ${here || done ? "text-fg" : "text-fg-muted"}`}>{s.label}</span>
              {s.description && <span className="block type-caption text-fg-muted">{s.description}</span>}
            </span>
          );
          return (
            <li key={s.id} aria-current={here ? "step" : undefined} className="flex min-w-0 flex-1 items-start gap-2">
              {done && onStepClick ? (
                <button type="button" onClick={() => onStepClick(i)} className="flex min-w-0 items-start gap-2.5 rounded-control hover:opacity-90">
                  {mark}{text}<span className="sr-only"> (completed, go back to this step)</span>
                </button>
              ) : <span className="flex min-w-0 items-start gap-2.5">{mark}{text}{done && <span className="sr-only"> (completed)</span>}</span>}
              {i < steps.length - 1 && <span aria-hidden className={`mt-3.5 hidden h-px min-w-4 flex-1 lg:block ${done ? "bg-accent" : "bg-line-strong"}`} />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
export const MenuLabel = ({ children }: { children: ReactNode }) => <div className="px-3 pb-1 pt-2 type-label text-fg-muted">{children}</div>;
