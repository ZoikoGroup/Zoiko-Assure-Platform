"use client";

import Link from "next/link";
import { cloneElement, createContext, isValidElement, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type {
  ButtonHTMLAttributes, DependencyList, HTMLAttributes, InputHTMLAttributes, KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject, SelectHTMLAttributes,
} from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertCircle, ArrowDown, ArrowUp, ArrowUpDown, Building2, CheckCircle2, ChevronRight, CircleDashed, Flag, HelpCircle, Inbox, Info, Loader2,
  Lock, MinusCircle, Search, ShieldAlert, X, XCircle,
} from "lucide-react";
import { useAuth } from "@/lib/actor";
import { ApiError, errText } from "@/lib/api";
import { short } from "@/lib/format";
import { heldByText, permissionText, rolesText } from "@/lib/roles";

/* ================================================================ controlled status
   State semantics are conveyed by icon + text + colour — never colour alone (ZA-GLS-001 §11). */
type Tone = "good" | "warn" | "bad" | "review" | "neutral" | "info" | "exception" | "unknown";
const TONE: Record<Tone, { cls: string; icon: LucideIcon; iconCls: string }> = {
  good: { cls: "bg-emerald-50 text-emerald-800 ring-emerald-200", icon: CheckCircle2, iconCls: "text-emerald-600" },
  warn: { cls: "bg-amber-50 text-amber-800 ring-amber-200", icon: CircleDashed, iconCls: "text-amber-600" },
  bad: { cls: "bg-red-50 text-red-800 ring-red-200", icon: XCircle, iconCls: "text-red-600" },
  review: { cls: "bg-violet-50 text-violet-800 ring-violet-200", icon: Flag, iconCls: "text-violet-600" },
  neutral: { cls: "bg-slate-100 text-slate-700 ring-slate-200", icon: MinusCircle, iconCls: "text-slate-500" },
  info: { cls: "bg-navy-50 text-navy-800 ring-navy-200", icon: Info, iconCls: "text-navy-600" },
  exception: { cls: "bg-brand-50 text-brand-800 ring-brand-200", icon: ShieldAlert, iconCls: "text-brand-600" },
  // A code this interface has no meaning for (ZA-GLS-001 GLS-REQ-015): neutral, dashed and marked, never mapped to a known state.
  unknown: { cls: "bg-white text-slate-700 ring-transparent outline-1 outline-dashed outline-slate-400 -outline-offset-1", icon: HelpCircle, iconCls: "text-slate-500" },
};

const GOOD = ["PROVEN", "OPERATING", "VERIFIED", "IPE_VERIFIED", "CURRENT", "PASS", "ADOPTED", "CERTIFIED", "SATISFIED",
  "PERMIT", "SUCCESS", "HEALTHY", "RESOLVED", "CLOSED", "ACTIVE", "ATTESTED", "PUBLISHED", "GENERAL_AVAILABILITY", "ENCODED", "OK", "PASSED", "KNOWN", "APPROVED"];
const WARN = ["NOT_PROVEN", "NOT_EVALUATED", "UNCERTAIN", "INCONCLUSIVE", "IPE_INCONCLUSIVE", "STALE", "UNKNOWN", "DEGRADED", "PARTIAL",
  "PENDING_APPROVAL", "OPEN", "MISSING", "UNVERIFIED", "TSA_PENDING", "PRIVATE_BETA", "MONITORING_ONLY", "PENDING_VERIFICATION",
  "VALIDATION_FAILED", "UNASSESSED", "PENDING_REVIEW", "PAYLOAD_NOT_AVAILABLE", "UNAVAILABLE", "DRAFT", "PROPOSED", "AI_EXTRACTED", "TRIAGED", "IN_REMEDIATION", "IN_PROGRESS", "PASS_WITH_WARNINGS", "WARN", "NEVER_RUN"];
const BAD = ["FAILING", "FAIL", "FAILED", "IPE_FAILED", "INVALID", "REJECTED", "DENY", "UNSATISFIED", "ERROR", "REVOKED",
  "SUSPENDED", "INTEGRITY_FAILED", "CRITICAL", "INVALIDATED", "MISSED"];
const REVIEW = ["REQUIRES_REVIEW", "CONFLICTING", "IN_REVIEW", "CONFLICT", "REVIEW", "REVIEW_REQUIRED", "SOURCE_CONFLICT"];
const NEUTRAL = ["DOES_NOT_APPLY", "NOT_REQUIRED", "IPE_NOT_REQUIRED", "SUPERSEDED", "ERASED", "NOT_BINDING",
  "WITHDRAWN", "EXPIRED", "CANCELLED", "RETIRED", "DISABLED", "PLANNED", "NOT_COVERED", "DEPRECATED", "RESTRICTED",
  "FALSE"];  // a predicate evaluating false is logic, not a failure
/* Recognised codes with no success/failure meaning. An applicability outcome is not a health result (it is shown beside
   the determination state, which carries the workflow colour), nor is a trace predicate evaluating true. The rest are
   backend codes that render with informational styling: content lifecycle steps, test SKIPPED, rule RELEASE_READY,
   package SIGNED, source-run RECONCILING, change class CORRECTION, rights classes and semantic origins. */
const INFO = ["APPLIES", "TRUE", "DISCOVERED", "INGESTED", "CLASSIFIED", "SKIPPED", "RELEASE_READY", "SIGNED", "RECONCILING", "CORRECTION",
  "OFFICIAL_REUSE_PERMITTED", "REFERENCE_ONLY", "AI_PROPOSED", "ZOIKO_ENCODED", "CUSTOMER_ATTESTED"];

export function toneOf(code?: string | null): Tone {
  if (!code) return "neutral";
  if (GOOD.includes(code)) return "good";
  if (WARN.includes(code)) return "warn";
  if (BAD.includes(code)) return "bad";
  if (REVIEW.includes(code)) return "review";
  if (NEUTRAL.includes(code)) return "neutral";
  if (INFO.includes(code)) return "info";
  return "unknown";
}

export function toneIcon(code?: string | null, tone?: Tone) {
  const t = TONE[tone ?? toneOf(code)];
  return { Icon: t.icon, iconCls: t.iconCls };
}

export function StatusPill({ code, tone, label, title }: { code?: string | null; tone?: Tone; label?: string; title?: string }) {
  const resolved = tone ?? toneOf(code);
  const t = TONE[resolved];
  const Icon = t.icon;
  const unknown = resolved === "unknown";
  return (
    <span title={title ?? (unknown ? `Unrecognised code “${code}”: shown as received, because this version of the interface has no meaning for it.` : code ?? "")}
      className={`inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-[5px] px-1.5 text-[11px] font-semibold leading-4 tracking-[0.02em] ring-1 ring-inset ${t.cls}`}>
      <Icon size={12} aria-hidden className={`shrink-0 ${t.iconCls}`} />
      <span>{label ?? code ?? "—"}</span>
      {unknown && <span className="sr-only"> (unrecognised code)</span>}
    </span>
  );
}
/** Back-compatible name used across pages. */
export const Badge = StatusPill;

export function ExceptionBadge({ state, until }: { state: string; until?: string }) {
  return <StatusPill tone="exception" label={`EXCEPTION ${state}${until ? " · until " + until.slice(0, 10) : ""}`} />;
}

/* Content coverage mode (ZA-GLS-001 coverage_mode; meanings from the backend dictionary). Binding applicability needs
   both the obligation definition and its rule version to be ENCODED, so monitoring-only content must stay visible
   (GLS-REQ-018). Shown exactly as recorded: a missing value says so and is never defaulted or inferred. */
const COVERAGE_MEANING: Record<string, string> = {
  ENCODED: "Provision-level content and applicability logic maintained.",
  MONITORING_ONLY: "Change monitoring only; must not produce binding applicability.",
  NOT_COVERED: "No supported coverage commitment.",
  RETIRED: "Previously supported coverage no longer maintained.",
};

export function CoverageBadge({ mode, showMeaning = false }: { mode?: string | null; showMeaning?: boolean }) {
  if (!mode) return <span className="text-xs text-slate-500">Not provided</span>;
  const meaning = COVERAGE_MEANING[mode];
  const badge = <StatusPill code={mode} title={meaning ? `Coverage mode ${mode}: ${meaning}` : undefined} />;
  return showMeaning && meaning ? <span className="inline-flex flex-col items-start gap-0.5">{badge}<span className="text-[11px] text-slate-500">{meaning}</span></span> : badge;
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
      {hint && <p id={htmlFor ? `${htmlFor}-hint` : undefined} className="mt-1.5 text-[12px] leading-4 text-slate-500">{hint}</p>}
    </div>
  );
}

/** Reason for a governed action. A required reason must contain text: after a submit attempt with only blanks the field
 *  is marked invalid and linked to the message. Callers trim the value before sending it and never substitute a default. */
export function ReasonField({ id, label, required, value, onChange, showError, inputRef, rows = 2, className = "" }: {
  id: string; label: string; required: boolean; value: string; onChange: (v: string) => void; showError: boolean;
  inputRef?: RefObject<HTMLTextAreaElement | null>; rows?: number; className?: string;
}) {
  return (
    <div className={className}>
      <label className="label" htmlFor={id}>{label}</label>
      <textarea ref={inputRef} id={id} className="input" rows={rows} value={value} onChange={(e) => onChange(e.target.value)}
        required={required} aria-invalid={showError} aria-describedby={showError ? `${id}-error` : undefined} />
      {showError && <p id={`${id}-error`} className="mt-1.5 text-[12px] leading-5 text-red-700">Enter a reason. Blank or whitespace-only text is not accepted.</p>}
    </div>
  );
}

/** Field-level validation message in the ReasonField style. The control points at it with
 *  aria-describedby={`${id}-error`} and sets aria-invalid while a message is shown; nothing renders without one. */
export function FieldError({ id, message }: { id: string; message?: string | false | null }) {
  return message ? <p id={`${id}-error`} className="mt-1.5 text-[12px] leading-5 text-red-700">{message}</p> : null;
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
export function PeriodSelector({ from, to, onChange, fromLabel = "Period from", toLabel = "Period to (exclusive)", required = false }:
  { from: string; to: string; onChange: (from: string, to: string) => void; fromLabel?: string; toLabel?: string; required?: boolean }) {
  const id = useId();
  const invalid = !!from && !!to && to <= from;
  const req = required ? " (required)" : "";
  return (
    <fieldset className="contents">
      <legend className="sr-only">Assurance period</legend>
      <Field label={fromLabel + req} htmlFor={`${id}-from`}><input id={`${id}-from`} type="date" required={required} className="input" value={from} onChange={(e) => onChange(e.target.value, to)} /></Field>
      <Field label={toLabel + req} htmlFor={`${id}-to`} hint={invalid ? <span className="text-red-700">The end must be after the start.</span> : undefined}>
        <input id={`${id}-to`} type="date" required={required} className="input" value={to} aria-invalid={invalid} aria-describedby={invalid ? `${id}-to-hint` : undefined} onChange={(e) => onChange(from, e.target.value)} />
      </Field>
    </fieldset>
  );
}

/* ================================================================ layout primitives */
export function Card({ title, actions, children, className = "", subtitle, footer, bodyClassName = "p-4" }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode; footer?: ReactNode; bodyClassName?: string }) {
  // The default body is marked so that a table it holds can run from edge to edge of the card (app/globals.css).
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-slate-200 px-4 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="text-[14px] font-semibold leading-5 text-slate-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[13px] leading-5 text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2 text-[13px]">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName} data-card-body={bodyClassName === "p-4" ? "" : undefined}>{children}</div>
      {footer && <footer className="border-t border-slate-200 px-4 py-2.5 text-[12px] leading-5 text-slate-500">{footer}</footer>}
    </section>
  );
}

/* Summary figures shown together sit in one strip divided by hairlines (MetricGroup) instead of a row of separate cards.
   A MetricCard on its own keeps its card. The strip's columns are set by the caller (grid-cols-*); choose counts that
   fill every row, since an empty cell would show the divider colour. */
const MetricGroupCtx = createContext(false);
export const METRIC_GROUP = "grid gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 shadow-[0_1px_3px_rgba(15,23,42,0.06)]";

export function MetricGroup({ as: Tag = "div", className = "", children, ...rest }:
  HTMLAttributes<HTMLElement> & { as?: "div" | "section" | "ul" }) {
  return (
    <Tag {...rest} className={`${METRIC_GROUP} ${className}`}>
      <MetricGroupCtx.Provider value>{children}</MetricGroupCtx.Provider>
    </Tag>
  );
}

export function MetricCard({ label, value, hint, code, tone, href }:
  { label: string; value: ReactNode; hint?: ReactNode; code?: string; tone?: Tone; href?: string }) {
  const grouped = useContext(MetricGroupCtx);
  const show = code || tone;
  const { Icon, iconCls } = toneIcon(code, tone);
  const surface = grouped ? "bg-white" : `card ${href ? "hover:border-slate-300 hover:shadow-[0_2px_8px_rgba(15,23,42,0.08)]" : ""}`;
  const body = (
    <div className={`${surface} flex h-full flex-col px-4 py-3.5 ${href ? `transition-[background-color,border-color,box-shadow] duration-150 ${grouped ? "hover:bg-slate-50" : ""}` : ""}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium leading-5 text-slate-600">{label}</span>
        {show && <Icon size={15} aria-hidden className={iconCls} />}
      </div>
      <div className="mt-1.5 text-[24px] font-semibold leading-8 tracking-[-0.01em] tabular-nums text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-[12px] leading-4 text-slate-500">{hint}</div>}
    </div>
  );
  return href ? <Link href={href} className={`block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 ${grouped ? "focus-visible:ring-inset" : "rounded-xl"}`}>{body}</Link> : body;
}

export function KV({ items, cols = 2 }: { items: [ReactNode, ReactNode][]; cols?: number }) {
  return (
    <dl className={`grid gap-x-6 gap-y-3 ${cols === 3 ? "sm:grid-cols-3" : cols === 4 ? "sm:grid-cols-2 lg:grid-cols-4" : "sm:grid-cols-2"}`}>
      {items.map(([k, v], i) => (
        <div key={i} className="min-w-0">
          <dt className="text-[12px] font-medium leading-4 text-slate-500">{k}</dt>
          <dd className="mt-1 break-words text-[13px] leading-5 text-slate-900">{v ?? "—"}</dd>
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

/* A screen shown as a tab of a consolidated page (components/WorkspaceHub.tsx). The consolidated page owns the heading
   and the document title, so the screen's own PageHeader becomes the tab's introduction: its subtitle, "About <tab>"
   and its actions, without repeating a heading. Null everywhere else. */
const PageSectionCtx = createContext<{ label: string } | null>(null);
export function PageSection({ label, children }: { label: string; children: ReactNode }) {
  return <PageSectionCtx.Provider value={{ label }}>{children}</PageSectionCtx.Provider>;
}

/** `kicker`: what kind of record or workspace the page shows (e.g. "Obligation dossier"), above the title.
 *  `subtitle`: one short sentence saying what the page is for. `help`: the longer explanation (how records behave,
 *  regulatory and control rules), shown on request under "About this page" together with the governing `spec`, so the
 *  working area starts right below the header without losing that context. `documentTitle`: the browser tab's title
 *  when it should say more than `title` (a consolidated page names its open tab). */
export function PageHeader({ title, subtitle, actions, spec, kicker, help, documentTitle }:
  { title: string; subtitle?: string; actions?: ReactNode; spec?: string; kicker?: ReactNode; help?: ReactNode; documentTitle?: string }) {
  const section = useContext(PageSectionCtx);
  const { setTitle } = usePageTitle();
  const page = section ? null : documentTitle ?? title;
  useEffect(() => { if (page === null) return; setTitle(page); return () => setTitle(""); }, [page, setTitle]);
  // Text on the page canvas is slate-600 or darker: slate-500 on the organisation canvas (slate-100) is below 4.5:1.
  if (section) {
    return (subtitle || help || spec || actions) ? (
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {subtitle && <p className="max-w-3xl text-[14px] leading-6 text-slate-600">{subtitle}</p>}
          {(help || spec) && <PageHelp spec={spec} label={`About ${section.label}`}>{help}</PageHelp>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    ) : null;
  }
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {kicker && <div data-za="page-context" className="mb-1.5 text-[12px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-600">{kicker}</div>}
          <h1 className="text-[24px] font-semibold leading-8 tracking-[-0.015em] text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1 max-w-3xl text-[14px] leading-6 text-slate-600">{subtitle}</p>}
          {(help || spec) && <PageHelp spec={spec}>{help}</PageHelp>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2 sm:pt-0.5">{actions}</div>}
      </div>
    </div>
  );
}

/** "About this page": contextual explanation kept one click away instead of in front of the working area. */
export function PageHelp({ children, spec, label = "About this page" }: { children?: ReactNode; spec?: string; label?: string }) {
  return (
    <details className="group mt-2 max-w-3xl text-[13px]">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded text-[13px] font-medium text-navy-700 transition-colors hover:text-navy-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 [&::-webkit-details-marker]:hidden">
        <HelpCircle size={14} aria-hidden />{label}
        <ChevronRight size={13} aria-hidden className="transition-transform duration-150 group-open:rotate-90" />
      </summary>
      <div className="za-pop-in mt-2 space-y-1.5 rounded-lg border border-slate-200 bg-white px-4 py-3 leading-5 text-slate-600 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        {children}
        {spec && <p className="text-[11px] text-slate-500">Specification: <span className="mono">{spec}</span></p>}
      </div>
    </details>
  );
}

/* Tabs and their panel share an `id` (from useId) so the selected tab points at the panel and the panel is labelled by
   the tab. Only the selected tab's panel is rendered, so only that tab carries aria-controls. */
const tabId = (id: string, t: string) => `${id}-tab-${t.replace(/[^A-Za-z0-9]+/g, "-")}`;

/** Roving focus for a tab list (WAI-ARIA tabs): Arrow keys move and select, Home and End jump, disabled tabs are skipped.
 *  Shared by these Tabs and the design-system Tabs (components/ds). */
export function useRovingTabs(tabs: string[], onChange: (t: string) => void, isDisabled?: (t: string) => boolean) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (from: number, step: number) => {
    for (let k = 1; k <= tabs.length; k++) {
      const n = (((from + step * k) % tabs.length) + tabs.length) % tabs.length;
      if (!isDisabled?.(tabs[n])) { onChange(tabs[n]); refs.current[n]?.focus(); return; }
    }
  };
  const keys: Record<string, (i: number) => void> = { ArrowRight: (i) => move(i, 1), ArrowLeft: (i) => move(i, -1), Home: () => move(-1, 1), End: () => move(tabs.length, -1) };
  const onKeyDown = (i: number) => (e: ReactKeyboardEvent) => { const k = keys[e.key]; if (k) { e.preventDefault(); k(i); } };
  return { refs, onKeyDown };
}

/** Underline tabs, shared with the consolidated pages' tabs (components/WorkspaceHub.tsx). `overlap`: the underline
 *  sits on the tab list's own bottom border (a list whose baseline is drawn inside it passes false). */
export const TAB_CLASS = (on: boolean, overlap = true) =>
  `${overlap ? "-mb-px " : ""}inline-flex h-10 items-center whitespace-nowrap border-b-2 text-[14px] font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400 ${
    on ? "border-navy-700 text-navy-800" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"}`;

export function Tabs({ id, tabs, value, onChange }: { id?: string; tabs: string[]; value: string; onChange: (t: string) => void }) {
  const { refs, onKeyDown } = useRovingTabs(tabs, onChange);
  return (
    <div role="tablist" className="mb-5 flex flex-wrap gap-x-6 border-b border-slate-200">
      {tabs.map((t, i) => (
        <button key={t} ref={(el) => { refs.current[i] = el; }} role="tab" type="button" aria-selected={value === t} tabIndex={value === t ? 0 : -1}
          id={id ? tabId(id, t) : undefined} aria-controls={id && value === t ? `${id}-panel` : undefined}
          onClick={() => onChange(t)}
          onKeyDown={onKeyDown(i)}
          className={TAB_CLASS(value === t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

/** A short fade on `ref`'s element when `deps` change after the first render: content replaced in place (another tab,
 *  another page) is acknowledged without remounting anything, so no component state is lost. Opacity only, so the
 *  element never becomes a containing block for fixed-position dialogs inside it. Off for reduced motion. */
export function useEnterAnimation(ref: RefObject<HTMLElement | null>, deps: DependencyList) {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const el = ref.current;
    if (!el?.animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    el.animate([{ opacity: 0.25 }, { opacity: 1 }], { duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** The selected tab's content. Focusable so keyboard users can move from the tab list straight into the panel. */
export function TabPanel({ id, value, children }: { id: string; value: string; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  useEnterAnimation(panel, [value]);
  return <div ref={panel} role="tabpanel" id={`${id}-panel`} aria-labelledby={tabId(id, value)} tabIndex={0}>{children}</div>;
}

/* ================================================================ tables */
/** `loading` shows placeholder rows until the first response arrives, so a list that is still loading is never shown
 *  as an empty one ("Nothing here yet") or as a bare header. */
export function Table({ head, children, empty, loading, emptyTitle = "Nothing here yet", emptyDescription, emptyAction }:
  { head: string[]; children: ReactNode; empty?: boolean; loading?: boolean; emptyTitle?: string; emptyDescription?: ReactNode; emptyAction?: ReactNode }) {
  return (
    <div data-za-table className="scroll-thin overflow-x-auto">
      <table className="min-w-full border-separate border-spacing-0">
        <thead><tr>{head.map((h, i) => <th key={`${h}-${i}`} className="th border-b border-slate-200">{h}</th>)}</tr></thead>
        <tbody className="[&>tr]:transition-colors [&>tr]:duration-150 [&>tr:hover]:bg-slate-50/70 [&>tr>td]:border-b [&>tr>td]:border-slate-100">{children}</tbody>
      </table>
      {loading ? <LoadingSkeleton rows={4} /> : empty && <EmptyState title={emptyTitle} description={emptyDescription} action={emptyAction} compact />}
    </div>
  );
}

export type Column<T> = { key: string; header: string; render: (row: T) => ReactNode; sortValue?: (row: T) => string | number | null | undefined; className?: string };
export type SortState = { key: string; dir: "asc" | "desc" };

/** Client-side sorting by a column's `sortValue`; `toggle` sorts ascending first, then flips. Shared by these tables
 *  and the design-system DataTable (components/ds). */
export function useSortedRows<T>(rows: T[], columns: { key: string; sortValue?: (row: T) => string | number | null | undefined }[], defaultSort?: SortState) {
  const [sort, setSort] = useState<SortState | null>(defaultSort ?? null);
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
  const toggle = (key: string) => setSort((s) => ({ key, dir: s?.key === key && s.dir === "asc" ? "desc" : "asc" }));
  return { sorted, sort, toggle };
}

export function DataTable<T>({ columns, rows, rowKey, empty, defaultSort }:
  { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string; empty?: ReactNode; defaultSort?: SortState }) {
  const { sorted, sort, toggle } = useSortedRows(rows, columns, defaultSort);
  return (
    <div data-za-table className="scroll-thin overflow-x-auto">
      <table className="min-w-full border-separate border-spacing-0">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th key={c.key} scope="col" className={`th border-b border-slate-200 ${c.className ?? ""}`}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}>
                  {c.sortValue ? (
                    <button type="button" className="inline-flex items-center gap-1 rounded uppercase tracking-[inherit] transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
                      onClick={() => toggle(c.key)}>
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
            <tr key={rowKey(r)} className="transition-colors duration-150 hover:bg-slate-50/70">
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
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "px-4 py-8" : "px-6 py-14"}`}>
      <span aria-hidden className={`flex items-center justify-center rounded-full bg-slate-100 text-slate-500 ${compact ? "h-9 w-9" : "h-11 w-11"}`}>
        <Icon size={compact ? 17 : 20} />
      </span>
      <p className="mt-3 text-[14px] font-medium leading-5 text-slate-800">{title}</p>
      {description && <p className="mt-1 max-w-md text-[13px] leading-5 text-slate-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
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
    <div role="alert" className="mb-4 flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-5 text-red-800">
      <AlertCircle size={16} aria-hidden className="mt-0.5 shrink-0 text-red-600" />
      <div className="min-w-0">
        <span className="sr-only">{title}: </span>{errText(error)}
        {error instanceof ApiError && error.reference && (
          <div className="mt-0.5 text-[12px]">{error.reference.label}: <span className="mono select-all break-all">{error.reference.value}</span></div>
        )}
      </div>
    </div>
  );
}
/** Back-compatible name used across pages. */
export function ErrorBox({ error }: { error: unknown }) {
  return <ErrorState error={error} />;
}

/** Polite live region for the result of an action (WCAG 4.1.3). It stays mounted, so a result rendered into it later is
 *  announced once. Static explanatory notices stay outside it so they are not re-announced; errors use ErrorState's alert. */
export function LiveRegion({ children }: { children?: ReactNode }) {
  return <div role="status">{children}</div>;
}

/** Visible reason for buttons disabled by role, so it does not rely on a `title` tooltip that keyboard and touch users
 *  cannot open. Each entry is a permission, or a list of which any one is enough; `actions` names, per entry (keyed by
 *  its permissions joined with "|"), what it blocks on this page. Each line says which roles can do it, so the person
 *  knows whom to ask. Display only; the backend decides. */
export function PermissionNote({ perms, actions, className = "mb-4" }:
  { perms: (string | string[])[]; actions?: Record<string, string>; className?: string }) {
  const { me } = useAuth();
  const missing = perms.map((p) => (Array.isArray(p) ? p : [p])).filter((any) => !any.some((p) => me?.permissions?.includes(p)));
  if (!me || !missing.length) return null;
  return (
    <div role="note" className={`${className} flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-[13px] leading-5 text-slate-700`}>
      <Lock size={15} aria-hidden className="mt-0.5 shrink-0 text-slate-500" />
      <div className="min-w-0">
        <span className="font-medium text-slate-800">Some actions here aren&apos;t available to your role</span> ({rolesText(me) || "none"}).
        <ul className="mt-0.5 space-y-0.5">
          {missing.map((any) => {
            const key = any.join("|");
            const held = heldByText(any);
            return (
              <li key={key}>
                {actions?.[key] ? <span className="font-medium">{actions[key]}: </span> : null}
                needs “{any.map(permissionText).join("” or “")}”{held ? <> — ask {/^[AEIOU]/i.test(held) ? "an" : "a"} {held}</> : <> — done by Zoiko&apos;s platform and content teams, not by an organisation role</>}.
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/** Status colours match the badges: info is navy, warning amber, success emerald (errors are ErrorState's red). */
export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "good" }) {
  const c = tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" : tone === "good" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-navy-100 bg-navy-50 text-navy-950";
  const ic = tone === "warn" ? "text-amber-600" : tone === "good" ? "text-emerald-600" : "text-navy-600";
  const Icon = tone === "warn" ? AlertCircle : tone === "good" ? CheckCircle2 : Info;
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-[13px] leading-5 ${c}`}>
      <Icon size={16} aria-hidden className={`mt-0.5 shrink-0 ${ic}`} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/* ================================================================ small display helpers */
/** Abbreviated digest or identifier. The toggle reports its expanded state; the full value can then be copied, with the
 *  outcome announced through a status message. Used for IDs as well as digests, so its name stays neutral. */
export function Hash({ value, n = 12 }: { value?: string | null; n?: number }) {
  const [full, setFull] = useState(false);
  const [copied, setCopied] = useState<"" | "ok" | "fail">("");
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(""), 4000);
    return () => clearTimeout(t);
  }, [copied]);
  if (!value) return <span>—</span>;
  const shown = full ? value : short(value, n);
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied("ok"); } catch { setCopied("fail"); }
  };
  return (
    <>
      <button type="button" onClick={() => setFull(!full)} aria-expanded={full} aria-label={`${full ? "Full value" : "Abbreviated value"} ${shown}`}
        title="Show full digest (a digest alone does not prove completeness or legal truth)"
        className="mono rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 hover:bg-slate-200">
        {shown}
      </button>
      {full && <>
        <button type="button" onClick={copy} aria-label="Copy full value" className="ml-1 rounded px-1 text-[11px] font-medium text-navy-700 hover:underline">Copy</button>
        <span role="status" className="ml-1 text-[11px] text-slate-500">{copied === "ok" ? "Copied" : copied === "fail" ? "Copy failed. Select the value and copy it manually." : ""}</span>
      </>}
    </>
  );
}

export function Json({ value, max = 380 }: { value: unknown; max?: number }) {
  return <pre className="scroll-thin mono overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-800" style={{ maxHeight: max }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function A({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className="font-medium text-navy-700 underline-offset-2 hover:text-navy-900 hover:underline">{children}</Link>;
}

/** Shown on hover or focus (WCAG 1.4.13): Escape dismisses it without moving focus or the pointer, and the gap to the
 *  bubble is padding, so the pointer can move onto it without it closing. It never takes focus. `bubbleClassName`
 *  replaces the bubble's look (the design-system Tooltip themes it); `describe` points a single child element at the
 *  bubble with aria-describedby, for triggers whose name does not already say what the tooltip says. */
export function Tooltip({ content, children, side = "top", bubbleClassName = "whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[12px] font-medium leading-4 text-white shadow-md", describe = false }:
  { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom"; bubbleClassName?: string; describe?: boolean }) {
  const [open, setOpen] = useState(false);
  const tipId = useId();
  const trigger = describe && isValidElement<{ "aria-describedby"?: string }>(children)
    ? cloneElement(children, { "aria-describedby": [children.props["aria-describedby"], tipId].filter(Boolean).join(" ") })
    : children;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  const pos = side === "right" ? "left-full top-1/2 pl-2 -translate-y-1/2" : side === "bottom" ? "left-1/2 top-full pt-1.5 -translate-x-1/2" : "bottom-full left-1/2 pb-1.5 -translate-x-1/2";
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      {trigger}
      <span role="tooltip" id={tipId} className={`absolute z-50 ${open ? "za-fade-in block" : "hidden"} ${pos}`}>
        <span className={`block ${bubbleClassName}`}>{content}</span>
      </span>
    </span>
  );
}

/* ================================================================ overlays (focus-trapped, Escape closes) */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open overlays in opening order. Only the topmost one handles Escape and the Tab trap, so closing a modal nested in a
// drawer leaves the drawer open (every open overlay listens on `document`).
const openDialogs: object[] = [];

/** Dialog behaviour shared by every modal surface here and in components/ds: initial focus in the body, a Tab trap,
 *  Escape for the topmost overlay only, and focus returned to where it was. The panel needs tabIndex={-1}. */
export function useDialog(open: boolean, onClose: () => void, panel: RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    if (!open) return;
    const token = {};
    openDialogs.push(token);
    const prev = document.activeElement as HTMLElement | null;
    // Start in the content, not on the header's Close button: the first field of a form, or the first button of a
    // confirmation, which is always Cancel, the least destructive choice. Content with nothing focusable focuses the panel.
    const first = panel.current?.querySelector("[data-dialog-body]")?.querySelector<HTMLElement>(FOCUSABLE);
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
    <div className="za-overlay-in fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[10vh] dark:bg-scrim" onMouseDown={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        className={`za-dialog-in w-full ${w} rounded-xl border border-slate-200 bg-white shadow-xl focus:outline-none`} onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 py-3.5 pl-5 pr-3">
          <h3 id={titleId} className="text-[15px] font-semibold leading-6 text-slate-900">{title}</h3>
          <IconButton label="Close" icon={X} onClick={onClose} />
        </header>
        <div className="p-5" data-dialog-body>{children}</div>
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
    <div className="za-overlay-in fixed inset-0 z-50 bg-slate-900/30 dark:bg-scrim" onMouseDown={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onMouseDown={(e) => e.stopPropagation()}
        className={`absolute inset-y-0 ${side === "left" ? "za-drawer-left left-0 border-r" : "za-drawer-right right-0 border-l"} flex w-full ${width} flex-col border-slate-200 bg-white shadow-xl focus:outline-none`}>
        <header className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h3 id={titleId} className="text-[15px] font-semibold leading-5 text-slate-900">{title}</h3>
          <IconButton label="Close" icon={X} onClick={onClose} />
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto p-4" data-dialog-body>{children}</div>
      </div>
    </div>
  );
}

/** Confirmation step for a consequential action. Cancel, Escape, the close button and the backdrop send nothing. While
 *  the request runs, both buttons are disabled and the dialog stays open; an API error is shown in place for a retry. */
export function ConfirmDialog({ title, children, confirm, busyLabel = "Submitting…", danger = false, onConfirm, onClose }: {
  title: string; children: ReactNode; confirm: string; busyLabel?: string; danger?: boolean;
  onConfirm: () => Promise<unknown>; onClose: () => void;
}) {
  const a = useAction();
  const busy = useRef(false);  // blocks a second confirm in the same tick, before `a.busy` disables the button
  const close = () => { if (!busy.current) onClose(); };
  const submit = () => {
    if (busy.current) return;
    busy.current = true;
    a.run(async () => {
      try { await onConfirm(); } finally { busy.current = false; }
      onClose();
    });
  };
  return (
    <Modal open onClose={close} title={title} size="sm">
      <div className="space-y-2 text-[13px] text-slate-700">{children}</div>
      {!!a.error && <div className="mt-3"><ErrorBox error={a.error} /></div>}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-ghost" disabled={a.busy} onClick={close}>Cancel</button>
        <button type="button" className={danger ? "btn-danger" : "btn-primary"} disabled={a.busy} aria-busy={a.busy} onClick={submit}>{a.busy ? busyLabel : confirm}</button>
      </div>
    </Modal>
  );
}

/** Disclosure pattern: a button showing a panel of ordinary links and buttons reached with Tab, so no menu role or
 *  arrow keys. Escape closes and returns focus to the button; tabbing out or clicking outside closes it too. Shared by
 *  Dropdown and the design-system DropdownMenu (components/ds). */
export function useDisclosure() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const close = useCallback(() => { setOpen(false); btn.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", onKey); };
  }, [open, close]);
  return { open, setOpen, root, btn, panelId, close };
}

export function Dropdown({ button, label, children, align = "right", buttonClassName = "", menuClassName = "w-64" }:
  { button: ReactNode; label: string; children: ReactNode | ((close: () => void) => ReactNode); align?: "left" | "right"; buttonClassName?: string; menuClassName?: string }) {
  const { open, setOpen, root, btn, panelId, close } = useDisclosure();
  return (
    <div ref={root} className="relative">
      <button ref={btn} type="button" aria-label={label} aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => setOpen(!open)} className={buttonClassName}>{button}</button>
      {open && (
        <div id={panelId} className={`za-pop-in absolute z-40 mt-2 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg ${align === "right" ? "right-0 origin-top-right" : "left-0 origin-top-left"} ${menuClassName}`}>
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}

export function DropdownItem({ icon: Icon, children, onClick, href, danger }: { icon?: LucideIcon; children: ReactNode; onClick?: () => void; href?: string; danger?: boolean }) {
  const cls = `flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] leading-5 transition-colors duration-100 focus-visible:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400 ${danger ? "text-red-700 hover:bg-red-50" : "text-slate-700 hover:bg-slate-50 hover:text-slate-900"}`;
  const inner = <>{Icon && <Icon size={15} aria-hidden className={danger ? "" : "text-slate-500"} />}{children}</>;
  return href ? <Link href={href} onClick={onClick} className={cls}>{inner}</Link> : <button type="button" onClick={onClick} className={cls}>{inner}</button>;
}

/* ================================================================ toasts */
export type ToastTone = "info" | "good" | "warn" | "bad";
export type ToastItem = { id: number; text: string; tone: ToastTone };
/** What useToast() returns. A provider (this one, or the design-system ToastProvider) supplies `push`, so callers work
 *  under either. */
export const ToastContext = createContext<(text: string, tone?: ToastTone) => void>(() => {});
const ToastCtx = ToastContext;

/** The queue behind a toast viewport: `push` adds a message that leaves after 4.5 s; `dismiss` removes one early. */
export function useToastQueue() {
  const [items, setItems] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: number) => setItems((x) => x.filter((t) => t.id !== id)), []);
  const push = useCallback((text: string, tone: ToastTone = "info") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => dismiss(id), 4500);
  }, [dismiss]);
  return { items, push, dismiss };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const { items, push } = useToastQueue();
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="fixed bottom-4 right-4 z-[70] flex w-80 flex-col gap-2">
        {items.map((t) => {
          const c = t.tone === "good" ? "border-emerald-200" : t.tone === "warn" ? "border-amber-200" : t.tone === "bad" ? "border-red-200" : "border-slate-200";
          const ic = t.tone === "good" ? "text-emerald-600" : t.tone === "warn" ? "text-amber-600" : t.tone === "bad" ? "text-red-600" : "text-navy-600";
          const Icon = t.tone === "good" ? CheckCircle2 : t.tone === "bad" ? XCircle : t.tone === "warn" ? AlertCircle : Info;
          return (
            <div key={t.id} className={`za-toast-in flex items-start gap-2.5 rounded-lg border bg-white px-3.5 py-3 text-[13px] leading-5 text-slate-800 shadow-lg ${c}`}>
              <Icon size={16} aria-hidden className={`mt-0.5 shrink-0 ${ic}`} />{t.text}
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
