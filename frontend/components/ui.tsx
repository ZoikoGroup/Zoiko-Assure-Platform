"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, RefObject, SelectHTMLAttributes } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertCircle, ArrowDown, ArrowUp, ArrowUpDown, Building2, CheckCircle2, ChevronRight, CircleDashed, Flag, Inbox, Info, Loader2,
  MinusCircle, Search, ShieldAlert, X, XCircle,
} from "lucide-react";
import { errText } from "@/lib/api";
import { short } from "@/lib/format";

/* ================================================================ controlled status
   State semantics are conveyed by icon + text + colour — never colour alone (ZA-GLS-001 §11). */
type Tone = "good" | "warn" | "bad" | "review" | "neutral" | "info" | "exception";
const TONE: Record<Tone, { cls: string; icon: LucideIcon; iconCls: string }> = {
  good: { cls: "bg-emerald-50 text-emerald-800 ring-emerald-200", icon: CheckCircle2, iconCls: "text-emerald-600" },
  warn: { cls: "bg-amber-50 text-amber-800 ring-amber-200", icon: CircleDashed, iconCls: "text-amber-600" },
  bad: { cls: "bg-red-50 text-red-800 ring-red-200", icon: XCircle, iconCls: "text-red-600" },
  review: { cls: "bg-violet-50 text-violet-800 ring-violet-200", icon: Flag, iconCls: "text-violet-600" },
  neutral: { cls: "bg-slate-100 text-slate-700 ring-slate-200", icon: MinusCircle, iconCls: "text-slate-500" },
  info: { cls: "bg-navy-50 text-navy-800 ring-navy-200", icon: Info, iconCls: "text-navy-600" },
  exception: { cls: "bg-brand-50 text-brand-800 ring-brand-200", icon: ShieldAlert, iconCls: "text-brand-600" },
};

const GOOD = ["PROVEN", "OPERATING", "APPLIES", "VERIFIED", "IPE_VERIFIED", "CURRENT", "PASS", "ADOPTED", "CERTIFIED", "SATISFIED",
  "TRUE", "PERMIT", "SUCCESS", "HEALTHY", "RESOLVED", "CLOSED", "ACTIVE", "ATTESTED", "PUBLISHED", "GENERAL_AVAILABILITY", "ENCODED", "OK", "PASSED", "KNOWN", "APPROVED"];
const WARN = ["NOT_PROVEN", "NOT_EVALUATED", "UNCERTAIN", "INCONCLUSIVE", "IPE_INCONCLUSIVE", "STALE", "UNKNOWN", "DEGRADED", "PARTIAL",
  "PENDING_APPROVAL", "OPEN", "MISSING", "UNVERIFIED", "TSA_PENDING", "PRIVATE_BETA", "MONITORING_ONLY", "PENDING_VERIFICATION",
  "VALIDATION_FAILED", "UNASSESSED", "PENDING_REVIEW", "PAYLOAD_NOT_AVAILABLE", "UNAVAILABLE", "DRAFT", "PROPOSED", "AI_EXTRACTED", "TRIAGED", "IN_REMEDIATION", "IN_PROGRESS", "PASS_WITH_WARNINGS", "WARN", "NEVER_RUN"];
const BAD = ["FAILING", "FAIL", "FAILED", "IPE_FAILED", "INVALID", "REJECTED", "DENY", "UNSATISFIED", "FALSE", "ERROR", "REVOKED",
  "SUSPENDED", "INTEGRITY_FAILED", "CRITICAL", "INVALIDATED", "MISSED"];
const REVIEW = ["REQUIRES_REVIEW", "CONFLICTING", "IN_REVIEW", "CONFLICT", "REVIEW", "REVIEW_REQUIRED", "SOURCE_CONFLICT"];
const NEUTRAL = ["DOES_NOT_APPLY", "NOT_REQUIRED", "IPE_NOT_REQUIRED", "SUPERSEDED", "ERASED", "NOT_BINDING",
  "WITHDRAWN", "EXPIRED", "CANCELLED", "RETIRED", "DISABLED", "PLANNED", "NOT_COVERED", "DEPRECATED", "RESTRICTED"];

export function toneOf(code?: string | null): Tone {
  if (!code) return "neutral";
  if (GOOD.includes(code)) return "good";
  if (WARN.includes(code)) return "warn";
  if (BAD.includes(code)) return "bad";
  if (REVIEW.includes(code)) return "review";
  if (NEUTRAL.includes(code)) return "neutral";
  return "info";
}

export function toneIcon(code?: string | null, tone?: Tone) {
  const t = TONE[tone ?? toneOf(code)];
  return { Icon: t.icon, iconCls: t.iconCls };
}

export function StatusPill({ code, tone, label, title }: { code?: string | null; tone?: Tone; label?: string; title?: string }) {
  const t = TONE[tone ?? toneOf(code)];
  const Icon = t.icon;
  return (
    <span title={title ?? code ?? ""} className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${t.cls}`}>
      <Icon size={12} aria-hidden className="shrink-0" />
      <span className="mono tracking-tight">{label ?? code ?? "—"}</span>
    </span>
  );
}
/** Back-compatible name used across pages. */
export const Badge = StatusPill;

export function ExceptionBadge({ state, until }: { state: string; until?: string }) {
  return <StatusPill tone="exception" label={`EXCEPTION ${state}${until ? " · until " + until.slice(0, 10) : ""}`} />;
}

/* ================================================================ buttons & form controls */
type Variant = "primary" | "secondary" | "accent" | "danger" | "ghost";
const VARIANT: Record<Variant, string> = {
  primary: "btn-primary", secondary: "btn-ghost", accent: "btn-accent", danger: "btn-danger",
  ghost: "btn text-slate-700 hover:bg-slate-100 hover:text-slate-900",
};

export function Button({ variant = "secondary", icon: Icon, loading, className = "", children, type, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; icon?: LucideIcon; loading?: boolean }) {
  return (
    <button type={type ?? "button"} {...rest} disabled={rest.disabled || loading} className={`${VARIANT[variant]} ${className}`}>
      {loading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : Icon ? <Icon size={14} aria-hidden /> : null}
      {children}
    </button>
  );
}

export function IconButton({ label, icon: Icon, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon: LucideIcon }) {
  return (
    <button type="button" aria-label={label} title={label} {...rest}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 disabled:opacity-50 ${className}`}>
      <Icon size={16} aria-hidden />
    </button>
  );
}

export function Input({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={`input ${className}`} />;
}

export function SearchInput({ className = "", inputClassName = "", ...rest }: InputHTMLAttributes<HTMLInputElement> & { inputClassName?: string }) {
  return (
    <div className={`relative ${className}`}>
      <Search size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
      <input type="search" {...rest} className={`input pl-8 ${inputClassName}`} />
    </div>
  );
}

type Opt = string | { value: string; label: string };
export function Select({ options, className = "", ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { options: Opt[] }) {
  return (
    <select {...rest} className={`input pr-8 ${className}`}>
      {options.map((o) => typeof o === "string" ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Field({ label, htmlFor, hint, children, className = "" }: { label: string; htmlFor?: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

/** Legal-entity filter. "" means all entities in the tenant. */
export function EntitySelector({ entities, value, onChange, allLabel = "All legal entities", className = "" }:
  { entities: { entity_id: string; legal_name: string }[]; value: string; onChange: (v: string) => void; allLabel?: string; className?: string }) {
  const id = useId();
  return (
    <div className={`relative ${className}`}>
      <label htmlFor={id} className="sr-only">Legal entity</label>
      <Building2 size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="input pl-8 pr-8">
        <option value="">{allLabel}</option>
        {entities.map((e) => <option key={e.entity_id} value={e.entity_id}>{e.legal_name}</option>)}
      </select>
    </div>
  );
}

/** Half-open period [from, to): the end date is exclusive, matching the assurance period model. */
export function PeriodSelector({ from, to, onChange, fromLabel = "Period from", toLabel = "Period to (exclusive)" }:
  { from: string; to: string; onChange: (from: string, to: string) => void; fromLabel?: string; toLabel?: string }) {
  const id = useId();
  const invalid = !!from && !!to && to <= from;
  return (
    <fieldset className="contents">
      <legend className="sr-only">Assurance period</legend>
      <Field label={fromLabel} htmlFor={`${id}-from`}><input id={`${id}-from`} type="date" className="input" value={from} onChange={(e) => onChange(e.target.value, to)} /></Field>
      <Field label={toLabel} htmlFor={`${id}-to`} hint={invalid ? <span className="text-red-700">The end must be after the start.</span> : undefined}>
        <input id={`${id}-to`} type="date" className="input" value={to} aria-invalid={invalid} onChange={(e) => onChange(from, e.target.value)} />
      </Field>
    </fieldset>
  );
}

/* ================================================================ layout primitives */
export function Card({ title, actions, children, className = "", subtitle, footer, bodyClassName = "p-4" }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode; footer?: ReactNode; bodyClassName?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-semibold text-slate-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2 text-[13px]">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
      {footer && <footer className="border-t border-slate-100 px-4 py-2.5 text-[11px] text-slate-500">{footer}</footer>}
    </section>
  );
}

export function MetricCard({ label, value, hint, code, tone, href }:
  { label: string; value: ReactNode; hint?: ReactNode; code?: string; tone?: Tone; href?: string }) {
  const show = code || tone;
  const { Icon, iconCls } = toneIcon(code, tone);
  const body = (
    <div className={`card flex h-full flex-col p-3.5 ${href ? "transition-colors hover:border-navy-300" : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-600">{label}</span>
        {show && <Icon size={15} aria-hidden className={iconCls} />}
      </div>
      <div className="mt-1 text-[22px] font-semibold leading-7 tabular-nums text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-[11px] leading-4 text-slate-500">{hint}</div>}
    </div>
  );
  return href ? <Link href={href} className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400">{body}</Link> : body;
}

export function KV({ items, cols = 2 }: { items: [ReactNode, ReactNode][]; cols?: number }) {
  return (
    <dl className={`grid gap-x-6 gap-y-3 ${cols === 3 ? "sm:grid-cols-3" : cols === 4 ? "sm:grid-cols-2 lg:grid-cols-4" : "sm:grid-cols-2"}`}>
      {items.map(([k, v], i) => (
        <div key={i} className="min-w-0">
          <dt className="text-[11px] font-medium text-slate-500">{k}</dt>
          <dd className="mt-0.5 break-words text-[13px] text-slate-900">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Breadcrumb({ items }: { items: { label: string; href?: string }[] }) {
  // On phones, unlinked section labels give way so the current page's name stays readable.
  const phoneHidden = (i: number) => i < items.length - 1 && !items[i].href;
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1 text-[13px]">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          const firstShown = items.slice(0, i).every((_, j) => phoneHidden(j));
          return (
            <li key={`${c.label}-${i}`} className={`min-w-0 items-center gap-1 ${last ? "" : "shrink-0"} ${phoneHidden(i) ? "hidden sm:flex" : "flex"}`}>
              {i > 0 && <ChevronRight size={13} aria-hidden className={`shrink-0 text-slate-300 ${firstShown ? "hidden sm:block" : ""}`} />}
              {last ? <span aria-current="page" className="truncate font-medium text-slate-900">{c.label}</span>
                : c.href ? <Link href={c.href} className="text-slate-500 hover:text-navy-700">{c.label}</Link>
                : <span className="text-slate-500">{c.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/* Page title is shared with the header breadcrumb so detail pages show their own name there. */
const PageTitleCtx = createContext<{ title: string; setTitle: (t: string) => void }>({ title: "", setTitle: () => {} });
export function PageTitleProvider({ children }: { children: ReactNode }) {
  const [title, setTitle] = useState("");
  return <PageTitleCtx.Provider value={{ title, setTitle }}>{children}</PageTitleCtx.Provider>;
}
export const usePageTitle = () => useContext(PageTitleCtx);

/** `kicker`: what kind of record or workspace the page shows (e.g. "Obligation dossier"), above the title. */
export function PageHeader({ title, subtitle, actions, spec, kicker }: { title: string; subtitle?: string; actions?: ReactNode; spec?: string; kicker?: ReactNode }) {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(title); return () => setTitle(""); }, [title, setTitle]);
  return (
    <div className="mb-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {kicker && <div data-za="page-context" className="mb-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500">{kicker}</div>}
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
            {spec && <span title="Governing specification" className="mono rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] text-slate-500">{spec}</span>}
          </div>
          {subtitle && <p className="mt-1 max-w-3xl text-[13px] leading-5 text-slate-500">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (t: string) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (i: number) => { const n = (i + tabs.length) % tabs.length; onChange(tabs[n]); refs.current[n]?.focus(); };
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-4 border-b border-slate-200">
      {tabs.map((t, i) => (
        <button key={t} ref={(el) => { refs.current[i] = el; }} role="tab" type="button" aria-selected={value === t} tabIndex={value === t ? 0 : -1}
          onClick={() => onChange(t)}
          onKeyDown={(e) => { if (e.key === "ArrowRight") { e.preventDefault(); move(i + 1); } if (e.key === "ArrowLeft") { e.preventDefault(); move(i - 1); } }}
          className={`-mb-px border-b-2 pb-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 ${value === t ? "border-navy-700 text-navy-800" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
          {t}
        </button>
      ))}
    </div>
  );
}

/* ================================================================ tables */
export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="min-w-full border-separate border-spacing-0">
        <thead><tr>{head.map((h, i) => <th key={`${h}-${i}`} className="th border-b border-slate-200">{h}</th>)}</tr></thead>
        <tbody className="[&>tr:hover]:bg-slate-50/70 [&>tr>td]:border-b [&>tr>td]:border-slate-100">{children}</tbody>
      </table>
      {empty && <EmptyState title="Nothing here yet" compact />}
    </div>
  );
}

export type Column<T> = { key: string; header: string; render: (row: T) => ReactNode; sortValue?: (row: T) => string | number | null | undefined; className?: string };

export function DataTable<T>({ columns, rows, rowKey, empty, defaultSort }:
  { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string; empty?: ReactNode; defaultSort?: { key: string; dir: "asc" | "desc" } }) {
  const [sort, setSort] = useState(defaultSort ?? null);
  const sorted = useMemo(() => {
    const col = sort && columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const v = col.sortValue;
    return [...rows].sort((a, b) => {
      const x = v(a) ?? "", y = v(b) ?? "";
      const r = x < y ? -1 : x > y ? 1 : 0;
      return sort!.dir === "asc" ? r : -r;
    });
  }, [rows, columns, sort]);
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="min-w-full border-separate border-spacing-0">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th key={c.key} scope="col" className={`th border-b border-slate-200 ${c.className ?? ""}`}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}>
                  {c.sortValue ? (
                    <button type="button" className="inline-flex items-center gap-1 uppercase hover:text-slate-800"
                      onClick={() => setSort({ key: c.key, dir: active && sort!.dir === "asc" ? "desc" : "asc" })}>
                      {c.header}
                      {active ? (sort!.dir === "asc" ? <ArrowUp size={11} aria-hidden /> : <ArrowDown size={11} aria-hidden />) : <ArrowUpDown size={11} aria-hidden className="opacity-40" />}
                    </button>
                  ) : c.header}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)} className="hover:bg-slate-50/70">
              {columns.map((c) => <td key={c.key} className={`td border-b border-slate-100 ${c.className ?? ""}`}>{c.render(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (empty ?? <EmptyState title="Nothing here yet" compact />)}
    </div>
  );
}

/* ================================================================ feedback states */
export function EmptyState({ title, description, icon: Icon = Inbox, action, compact }:
  { title: string; description?: ReactNode; icon?: LucideIcon; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "px-4 py-6" : "px-6 py-12"}`}>
      <Icon size={compact ? 18 : 22} aria-hidden className="text-slate-400" />
      <p className="mt-2 text-[13px] font-medium text-slate-700">{title}</p>
      {description && <p className="mt-1 max-w-sm text-xs text-slate-500">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function LoadingSkeleton({ rows = 3, className = "" }: { rows?: number; className?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className={`space-y-2.5 p-4 ${className}`}>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton h-3.5" style={{ width: `${92 - (i % 3) * 14}%` }} />)}
    </div>
  );
}

/** Back-compatible loading indicator used across pages. */
export function Loading({ show }: { show: boolean }) {
  return show ? <LoadingSkeleton /> : null;
}

export function ErrorState({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  if (!error) return null;
  return (
    <div role="alert" className="mb-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-800">
      <AlertCircle size={15} aria-hidden className="mt-0.5 shrink-0 text-red-600" />
      <div><span className="sr-only">{title}: </span>{errText(error)}</div>
    </div>
  );
}
/** Back-compatible name used across pages. */
export function ErrorBox({ error }: { error: unknown }) {
  return <ErrorState error={error} />;
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "good" }) {
  const c = tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" : tone === "good" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-slate-200 bg-slate-50 text-slate-700";
  const Icon = tone === "warn" ? AlertCircle : tone === "good" ? CheckCircle2 : Info;
  return (
    <div className={`flex items-start gap-2 rounded-md border px-3 py-2 text-[13px] ${c}`}>
      <Icon size={15} aria-hidden className="mt-0.5 shrink-0 opacity-80" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/* ================================================================ small display helpers */
export function Hash({ value, n = 12 }: { value?: string | null; n?: number }) {
  const [full, setFull] = useState(false);
  if (!value) return <span>—</span>;
  return (
    <button type="button" onClick={() => setFull(!full)} title="Show full digest (a digest alone does not prove completeness or legal truth)"
      className="mono rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 hover:bg-slate-200">
      {full ? value : short(value, n)}
    </button>
  );
}

export function Json({ value, max = 380 }: { value: unknown; max?: number }) {
  return <pre className="scroll-thin mono overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-800" style={{ maxHeight: max }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function A({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className="font-medium text-navy-700 underline-offset-2 hover:text-navy-900 hover:underline">{children}</Link>;
}

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" }) {
  const pos = side === "right" ? "left-full top-1/2 ml-2 -translate-y-1/2" : side === "bottom" ? "left-1/2 top-full mt-1.5 -translate-x-1/2" : "bottom-full left-1/2 mb-1.5 -translate-x-1/2";
  return (
    <span className="group/tt relative inline-flex">
      {children}
      <span role="tooltip" className={`pointer-events-none absolute z-50 hidden whitespace-nowrap rounded bg-slate-900 px-2 py-1 text-[11px] font-medium text-white shadow group-hover/tt:block group-focus-within/tt:block ${pos}`}>{content}</span>
    </span>
  );
}

/* ================================================================ overlays (focus-trapped, Escape closes) */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open overlays in opening order. Only the topmost one handles Escape and the Tab trap, so closing a modal nested in a
// drawer leaves the drawer open (every open overlay listens on `document`).
const openDialogs: object[] = [];

function useDialog(open: boolean, onClose: () => void, panel: RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    if (!open) return;
    const token = {};
    openDialogs.push(token);
    const prev = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (openDialogs[openDialogs.length - 1] !== token) return;
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab" || !panel.current) return;
      const els = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!els.length) return;
      const [a, z] = [els[0], els[els.length - 1]];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      openDialogs.splice(openDialogs.indexOf(token), 1);
      prev?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

export function Modal({ open, onClose, title, children, size = "md" }: { open: boolean; onClose: () => void; title: string; children: ReactNode; size?: "sm" | "md" | "lg" }) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(open, onClose, panel);
  if (!open) return null;
  const w = size === "sm" ? "max-w-md" : size === "lg" ? "max-w-4xl" : "max-w-2xl";
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[10vh]" onMouseDown={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        className={`w-full ${w} rounded-lg border border-slate-200 bg-white shadow-xl focus:outline-none`} onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <h3 id={titleId} className="text-sm font-semibold text-slate-900">{title}</h3>
          <IconButton label="Close" icon={X} onClick={onClose} />
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children, side = "right", width = "max-w-2xl" }:
  { open: boolean; onClose: () => void; title: string; children: ReactNode; side?: "left" | "right"; width?: string }) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(open, onClose, panel);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/30" onMouseDown={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onMouseDown={(e) => e.stopPropagation()}
        className={`absolute inset-y-0 ${side === "left" ? "left-0 border-r" : "right-0 border-l"} flex w-full ${width} flex-col border-slate-200 bg-white shadow-xl focus:outline-none`}>
        <header className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h3 id={titleId} className="text-sm font-semibold text-slate-900">{title}</h3>
          <IconButton label="Close" icon={X} onClick={onClose} />
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}

export function Dropdown({ button, label, children, align = "right", buttonClassName = "", menuClassName = "w-64" }:
  { button: ReactNode; label: string; children: ReactNode | ((close: () => void) => ReactNode); align?: "left" | "right"; buttonClassName?: string; menuClassName?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { setOpen(false); btn.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open, close]);
  return (
    <div ref={root} className="relative">
      <button ref={btn} type="button" aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} className={buttonClassName}>{button}</button>
      {open && (
        <div role="menu" className={`absolute z-40 mt-1.5 overflow-hidden rounded-md border border-slate-200 bg-white py-1 shadow-lg ${align === "right" ? "right-0" : "left-0"} ${menuClassName}`}>
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}

export function DropdownItem({ icon: Icon, children, onClick, href, danger }: { icon?: LucideIcon; children: ReactNode; onClick?: () => void; href?: string; danger?: boolean }) {
  const cls = `flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] focus-visible:bg-slate-50 focus-visible:outline-none ${danger ? "text-red-700 hover:bg-red-50" : "text-slate-700 hover:bg-slate-50"}`;
  const inner = <>{Icon && <Icon size={15} aria-hidden className={danger ? "" : "text-slate-400"} />}{children}</>;
  return href ? <Link role="menuitem" href={href} onClick={onClick} className={cls}>{inner}</Link> : <button role="menuitem" type="button" onClick={onClick} className={cls}>{inner}</button>;
}

/* ================================================================ toasts */
type ToastTone = "info" | "good" | "warn" | "bad";
const ToastCtx = createContext<(text: string, tone?: ToastTone) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; text: string; tone: ToastTone }[]>([]);
  const push = useCallback((text: string, tone: ToastTone = "info") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-[70] flex w-80 flex-col gap-2">
        {items.map((t) => {
          const c = t.tone === "good" ? "border-emerald-200" : t.tone === "warn" ? "border-amber-200" : t.tone === "bad" ? "border-red-200" : "border-slate-200";
          const Icon = t.tone === "good" ? CheckCircle2 : t.tone === "bad" ? XCircle : t.tone === "warn" ? AlertCircle : Info;
          return (
            <div key={t.id} role="status" className={`flex items-start gap-2 rounded-md border bg-white px-3 py-2.5 text-[13px] text-slate-800 shadow-lg ${c}`}>
              <Icon size={15} aria-hidden className="mt-0.5 shrink-0 text-slate-500" />{t.text}
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ================================================================ async action state */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<any>(null);
  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      setResult(r);
      return r;
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, result, run, setError };
}
