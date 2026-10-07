"use client";

import Link from "next/link";
import { useState } from "react";
import { errText } from "@/lib/api";
import { short } from "@/lib/format";

/* State semantics are conveyed by text + icon + color (never color alone). */
type Tone = "good" | "warn" | "bad" | "review" | "neutral" | "info" | "exception";
const TONE: Record<Tone, { cls: string; icon: string }> = {
  good: { cls: "bg-emerald-50 text-emerald-800 ring-emerald-200", icon: "✓" },
  warn: { cls: "bg-amber-50 text-amber-800 ring-amber-200", icon: "◐" },
  bad: { cls: "bg-red-50 text-red-800 ring-red-200", icon: "✕" },
  review: { cls: "bg-violet-50 text-violet-800 ring-violet-200", icon: "⚑" },
  neutral: { cls: "bg-slate-100 text-slate-700 ring-slate-200", icon: "–" },
  info: { cls: "bg-navy-50 text-navy-800 ring-navy-200", icon: "○" },
  exception: { cls: "bg-brand-50 text-brand-800 ring-brand-300", icon: "⚠" },
};

const GOOD = ["PROVEN", "OPERATING", "APPLIES", "VERIFIED", "IPE_VERIFIED", "CURRENT", "PASS", "ADOPTED", "CERTIFIED", "SATISFIED",
  "TRUE", "PERMIT", "SUCCESS", "HEALTHY", "RESOLVED", "CLOSED", "ACTIVE", "ATTESTED", "PUBLISHED", "GENERAL_AVAILABILITY", "ENCODED", "OK", "PASSED", "KNOWN", "APPROVED", "PERMIT"];
const WARN = ["NOT_PROVEN", "NOT_EVALUATED", "UNCERTAIN", "INCONCLUSIVE", "IPE_INCONCLUSIVE", "STALE", "UNKNOWN", "DEGRADED", "PARTIAL",
  "PENDING_APPROVAL", "OPEN", "MISSING", "UNVERIFIED", "TSA_PENDING", "PRIVATE_BETA", "MONITORING_ONLY", "PENDING_VERIFICATION",
  "VALIDATION_FAILED", "UNASSESSED", "PENDING_REVIEW", "PAYLOAD_NOT_AVAILABLE", "UNAVAILABLE", "DRAFT", "PROPOSED", "AI_EXTRACTED", "TRIAGED", "IN_REMEDIATION", "IN_PROGRESS", "PASS_WITH_WARNINGS", "WARN", "NEVER_RUN"];
const BAD = ["FAILING", "FAIL", "FAILED", "IPE_FAILED", "INVALID", "REJECTED", "DENY", "UNSATISFIED", "FALSE", "ERROR", "REVOKED",
  "SUSPENDED", "INTEGRITY_FAILED", "CRITICAL", "INVALIDATED", "MISSED"];
const REVIEW = ["REQUIRES_REVIEW", "CONFLICTING", "IN_REVIEW", "CONFLICT", "REVIEW", "REVIEW_REQUIRED", "SOURCE_CONFLICT", "CONFLICT"];
const NEUTRAL = ["DOES_NOT_APPLY", "NOT_REQUIRED", "IPE_NOT_REQUIRED", "SUPERSEDED", "ERASED", "NOT_EVALUATED_SC", "NOT_BINDING",
  "WITHDRAWN", "EXPIRED", "CANCELLED", "RETIRED", "DISABLED", "PLANNED", "NOT_COVERED", "NOT_EVALUATED_SHORT", "DEPRECATED", "RESTRICTED"];

export function toneOf(code?: string | null): Tone {
  if (!code) return "neutral";
  if (GOOD.includes(code)) return "good";
  if (WARN.includes(code)) return "warn";
  if (BAD.includes(code)) return "bad";
  if (REVIEW.includes(code)) return "review";
  if (NEUTRAL.includes(code)) return "neutral";
  return "info";
}

export function Badge({ code, tone, label, title }: { code?: string | null; tone?: Tone; label?: string; title?: string }) {
  const t = TONE[tone ?? toneOf(code)];
  return (
    <span title={title ?? code ?? ""} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${t.cls}`}>
      <span aria-hidden>{t.icon}</span>
      <span className="mono tracking-tight">{label ?? code ?? "—"}</span>
    </span>
  );
}

export function ExceptionBadge({ state, until }: { state: string; until?: string }) {
  return <Badge tone="exception" label={`EXCEPTION ${state}${until ? " · until " + until.slice(0, 10) : ""}`} />;
}

export function Card({ title, actions, children, className = "", subtitle }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; subtitle?: React.ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-navy-100 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-navy-800">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-brand">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2 text-sm">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions, spec }: { title: string; subtitle?: string; actions?: React.ReactNode; spec?: string }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        {spec && <div className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-brand-600">{spec}</div>}
        <h1 className="text-2xl font-bold tracking-tight text-navy-800">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-brand">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function KV({ items, cols = 2 }: { items: [React.ReactNode, React.ReactNode][]; cols?: number }) {
  return (
    <dl className={`grid gap-x-6 gap-y-3 ${cols === 3 ? "sm:grid-cols-3" : cols === 4 ? "sm:grid-cols-4" : "sm:grid-cols-2"}`}>
      {items.map(([k, v], i) => (
        <div key={i} className="min-w-0">
          <dt className="label">{k}</dt>
          <dd className="break-words text-sm text-navy-900">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Table({ head, children, empty }: { head: string[]; children: React.ReactNode; empty?: boolean }) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="min-w-full divide-y divide-navy-100">
        <thead className="bg-navy-50/60">
          <tr>{head.map((h) => <th key={h} className="th">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-navy-50">{children}</tbody>
      </table>
      {empty && <div className="p-6 text-center text-sm text-slate-brand">Nothing here yet.</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{errText(error)}</div>;
}

export function Notice({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" | "good" }) {
  const c = tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" : tone === "good" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-navy-200 bg-navy-50 text-navy-900";
  return <div className={`rounded-lg border px-3 py-2 text-sm ${c}`}>{children}</div>;
}

export function Loading({ show }: { show: boolean }) {
  if (!show) return null;
  return <div className="p-6 text-sm text-slate-brand">Loading…</div>;
}

export function Hash({ value, n = 12 }: { value?: string | null; n?: number }) {
  const [full, setFull] = useState(false);
  if (!value) return <span>—</span>;
  return (
    <button type="button" onClick={() => setFull(!full)} title="Show full digest (a digest alone does not prove completeness or legal truth)"
      className="mono rounded bg-navy-50 px-1.5 py-0.5 text-[11px] text-navy-700 hover:bg-navy-100">
      {full ? value : short(value, n)}
    </button>
  );
}

export function Json({ value, max = 380 }: { value: unknown; max?: number }) {
  return <pre className="scroll-thin mono overflow-auto rounded-lg bg-navy-950 p-3 text-[11px] leading-relaxed text-navy-100" style={{ maxHeight: max }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function A({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="font-medium text-navy-700 underline-offset-2 hover:text-brand-600 hover:underline">{children}</Link>;
}

export function Stat({ label, value, tone, hint }: { label: string; value: React.ReactNode; tone?: Tone; hint?: string }) {
  const t = tone ? TONE[tone] : null;
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div className="mt-1 flex items-center gap-2 text-2xl font-bold text-navy-800">
        {t && <span aria-hidden className={`rounded-full px-1.5 text-sm ring-1 ring-inset ${t.cls}`}>{t.icon}</span>}
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-slate-brand">{hint}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (t: string) => void }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b border-navy-100">
      {tabs.map((t) => (
        <button key={t} role="tab" aria-selected={value === t} onClick={() => onChange(t)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${value === t ? "border-brand-500 text-navy-800" : "border-transparent text-slate-brand hover:text-navy-700"}`}>
          {t}
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  if (!open) return null;
  return (
    <div role="dialog" aria-modal className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-navy-950/40 p-4 pt-16" onClick={onClose}>
      <div className="card w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-navy-100 px-4 py-3">
          <h3 className="font-semibold text-navy-800">{title}</h3>
          <button className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

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
