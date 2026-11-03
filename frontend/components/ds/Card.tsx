"use client";

import Link from "next/link";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowDown, ArrowUp, ChevronDown } from "lucide-react";
import { Skeleton } from "./Feedback";
import { STATUS_ICON, STATUS_SURFACE, STATUS_TEXT, STATUS_TONE, StatusMark, statusLabel, type StatusKey } from "./Status";

/* Cards and panels on the foundation tokens: rounded-card · p-card · shadow-card for cards, rounded-panel · p-panel ·
   shadow-panel for panels. */

const CARD = "rounded-card border border-line bg-surface-card shadow-card";

/** A standard card: optional header (title, description, actions), body and footer. */
export function Card({ title, description, actions, footer, children, as: Tag = "section", className = "", bodyClassName = "p-card" }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; footer?: ReactNode; children: ReactNode;
  as?: "section" | "article" | "div"; className?: string; bodyClassName?: string;
}) {
  const headingId = useId();
  return (
    <Tag aria-labelledby={title && Tag !== "div" ? headingId : undefined} className={`${CARD} ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-card py-4">
          <div className="min-w-0">
            {title && <h3 id={headingId} className="type-card text-fg">{title}</h3>}
            {description && <p className="mt-0.5 type-caption text-fg-muted">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
      {footer && <footer className="border-t border-line px-card py-3 type-caption text-fg-muted">{footer}</footer>}
    </Tag>
  );
}

export type KpiTone = StatusKey | "neutral";
const KPI_VALUE: Record<KpiTone, string> = { ...STATUS_TEXT, neutral: "text-fg" };
export type KpiDelta = { value: string; direction: "up" | "down"; status: StatusKey; label?: string };

/** A summary figure. `delta` is a change chip whose status (not its direction) says whether the change is good. */
export function KpiCard({ label, value, hint, tone = "neutral", delta, href, loading = false, icon: Icon }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: KpiTone; delta?: KpiDelta; href?: string; loading?: boolean; icon?: LucideIcon;
}) {
  const body = (
    <div className={`${CARD} flex h-full flex-col gap-1.5 p-card ${href ? "transition-colors hover:border-line-strong" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="type-secondary font-semibold text-fg-secondary">{label}</span>
        {Icon && <Icon size={16} aria-hidden className="shrink-0 text-fg-muted" />}
      </div>
      {loading ? <span role="status" aria-label={`Loading ${label}`}><Skeleton className="my-1 h-8 w-20" /></span>
        : <span className={`type-page font-bold tabular-nums ${KPI_VALUE[tone]}`}>{value}</span>}
      {(delta || hint) && (
        <div className="mt-auto flex flex-wrap items-center gap-2">
          {delta && (
            <span className={`inline-flex items-center gap-0.5 rounded-chip border px-1.5 type-caption font-semibold tabular-nums ${STATUS_TONE[delta.status]}`}>
              {delta.direction === "up" ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />}
              <span className="sr-only">{delta.direction === "up" ? "Up" : "Down"} </span>{delta.value}
              {delta.label && <span className="sr-only"> {delta.label}</span>}
            </span>
          )}
          {hint && <span className="type-caption text-fg-muted">{hint}</span>}
        </div>
      )}
    </div>
  );
  return href ? <Link href={href} className="block h-full rounded-card">{body}</Link> : body;
}

export type CalloutTone = Exclude<StatusKey, "unknown">;

/** A panel of guidance on a status tint. Static by default; for messages that appear in response to an action, use
 *  Alert with `live` so they are announced. */
export function Callout({ tone = "info", title, children, actions, className = "" }:
  { tone?: CalloutTone; title?: ReactNode; children?: ReactNode; actions?: ReactNode; className?: string }) {
  const Icon = STATUS_ICON[tone];
  return (
    <div className={`flex gap-3 rounded-card border border-l-4 p-4 ${STATUS_SURFACE[tone]} ${CALLOUT_EDGE[tone]} ${className}`}>
      <Icon size={18} aria-hidden className={`mt-0.5 shrink-0 ${STATUS_TEXT[tone]}`} />
      <div className="min-w-0 flex-1">
        <span className="sr-only">{statusLabel(tone)}: </span>
        {title && <p className="type-secondary font-semibold text-fg">{title}</p>}
        {children && <div className={`type-secondary text-fg-secondary ${title ? "mt-0.5" : ""}`}>{children}</div>}
        {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  );
}
const CALLOUT_EDGE: Record<CalloutTone, string> = {
  info: "border-l-status-info", warning: "border-l-status-warning", critical: "border-l-status-critical", verified: "border-l-status-verified",
};

type PanelProps = Omit<Parameters<typeof Callout>[0], "tone">;
export const InfoPanel = (p: PanelProps) => <Callout tone="info" {...p} />;
export const WarningPanel = (p: PanelProps) => <Callout tone="warning" {...p} />;
export const CriticalPanel = (p: PanelProps) => <Callout tone="critical" {...p} />;

/** A titled panel whose content shows and hides (disclosure pattern: heading › button › region). */
export function CollapsiblePanel({ title, description, meta, children, defaultOpen = false, open: controlled, onOpenChange, disabled = false }: {
  title: ReactNode; description?: ReactNode; meta?: ReactNode; children: ReactNode;
  defaultOpen?: boolean; open?: boolean; onOpenChange?: (open: boolean) => void; disabled?: boolean;
}) {
  const [inner, setInner] = useState(defaultOpen);
  const open = controlled ?? inner;
  const id = useId();
  const toggle = () => { setInner(!open); onOpenChange?.(!open); };
  return (
    <div className={CARD}>
      <h3>
        <button type="button" id={`${id}-button`} aria-expanded={open} aria-controls={`${id}-region`} disabled={disabled} onClick={toggle}
          className="flex w-full items-center gap-3 rounded-card px-card py-4 text-left transition-colors enabled:hover:bg-fg/3 disabled:cursor-not-allowed">
          <span className="min-w-0 flex-1">
            <span className={`block type-card ${disabled ? "text-fg-disabled" : "text-fg"}`}>{title}</span>
            {description && <span className="block type-caption text-fg-muted">{description}</span>}
          </span>
          {meta}
          <ChevronDown size={18} aria-hidden className={`shrink-0 text-fg-muted transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </h3>
      <div id={`${id}-region`} role="region" aria-labelledby={`${id}-button`} hidden={!open} className="border-t border-line px-card py-4 type-secondary text-fg-secondary">
        {children}
      </div>
    </div>
  );
}

/** Term/value pairs for a record summary (semantic dl). `columns` lays them out in up to three columns from sm up. */
export function DescriptionList({ items, columns = 2, className = "" }: { items: [ReactNode, ReactNode][]; columns?: 1 | 2 | 3; className?: string }) {
  const cols = columns === 3 ? "sm:grid-cols-2 lg:grid-cols-3" : columns === 2 ? "sm:grid-cols-2" : "";
  return (
    <dl className={`grid gap-x-6 gap-y-3 ${cols} ${className}`}>
      {items.map(([term, detail], i) => (
        <div key={i} className="min-w-0">
          <dt className="type-caption text-fg-muted">{term}</dt>
          <dd className="mt-0.5 break-words type-secondary text-fg">{detail ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export type TimelineItem = { id: string; title: ReactNode; meta?: ReactNode; description?: ReactNode; status?: StatusKey };

/** An ordered history (provenance, reviews, transitions), oldest or newest first as the caller orders it. */
export function Timeline({ items, label }: { items: TimelineItem[]; label: string }) {
  return (
    <ol aria-label={label} className="grid">
      {items.map((it, i) => (
        <li key={it.id} className="relative flex gap-3 pb-4 last:pb-0">
          {i < items.length - 1 && <span aria-hidden className="absolute bottom-0 left-3 top-7 w-px bg-line-strong" />}
          {it.status ? <StatusMark status={it.status} size="sm" /> : <span aria-hidden className="mt-1.5 ml-2 h-2 w-2 shrink-0 rounded-full bg-fg-muted" />}
          <div className="min-w-0 flex-1 pt-0.5">
            <p className="type-secondary font-medium text-fg">{it.status && <span className="sr-only">{statusLabel(it.status)}: </span>}{it.title}</p>
            {it.meta && <p className="type-caption text-fg-muted">{it.meta}</p>}
            {it.description && <div className="mt-1 type-caption text-fg-secondary">{it.description}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** A large section panel with a section heading and icon, as on the Design Governance pages. No backdrop-filter or
 *  transform: either would trap fixed overlays and menus inside the panel. */
export function Panel({ id, title, icon: Icon, aside, children }: { id?: string; title: string; icon: LucideIcon; aside?: ReactNode; children: ReactNode }) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-6 rounded-panel border border-line bg-surface-raised/70 p-panel shadow-panel">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 id={headingId} className="flex items-center gap-2.5 type-section text-fg"><Icon size={16} aria-hidden className="shrink-0 text-accent" />{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
