"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle, BadgeCheck, Building, Check, EqualNot, FileText, Files, KeyRound, Layers, LayoutGrid, Palette,
  Ruler, ShieldCheck, SlidersHorizontal, Square, SwatchBook, Type, UserCheck,
} from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { Panel, STATUS_ICON, STATUS_TONE } from "@/components/ds";
import { usePageTitle } from "@/components/ui";
import {
  BREAKPOINTS, COLOR_GROUPS, RADII, SHADOWS, SPACING, STATUSES, TYPE_ROLES, contrast, over, parseColor, statusVars,
  type ColorToken, type CssVar,
} from "@/lib/design-tokens";

/* Design Governance › Foundations (Unified Design System, Task 1). Every value shown is read from the rendered
   stylesheet, and every contrast ratio is computed from those values, so this page cannot drift from the tokens. */
const TITLE = "Unified Design System & UI Kit";

export default function DesignSystemFoundations() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="foundations"><Foundations /></GovernanceShell>;
}

type Values = Record<string, string>;
const VARS: string[] = [
  ...COLOR_GROUPS.flatMap((g) => g.tokens.map((t) => t.cssVar)), "--za-surface-app", "--za-surface-card",
  ...STATUSES.flatMap((s) => Object.values(statusVars(s.key))), ...SPACING.map((t) => t.cssVar), ...RADII.map((t) => t.cssVar), ...SHADOWS.map((t) => t.cssVar),
];

function Foundations() {
  const { theme, query } = useGovernance();
  const ref = useRef<HTMLDivElement>(null);
  const [vals, setVals] = useState<Values>({});
  // Re-read after each theme change: data-theme is already on the DOM when this effect runs.
  useEffect(() => {
    if (!ref.current) return;
    const cs = getComputedStyle(ref.current);
    setVals(Object.fromEntries(VARS.map((v) => [v, cs.getPropertyValue(v).trim()])));
  }, [theme]);
  const q = query.trim().toLowerCase();
  const hit = (...fields: (string | undefined)[]) => !q || fields.some((f) => f?.toLowerCase().includes(q));

  return (
    <div ref={ref} className="space-y-section">
      <section aria-labelledby="ds-title">
        <h1 id="ds-title" className="type-page text-fg sm:type-display">Unified <span className="text-accent-fg">Design System</span> &amp; UI Kit</h1>
        <p className="mt-3 max-w-3xl type-body text-fg-secondary">
          A single enterprise design language. Shared tokens, shared components, different policy gates. Server-authorized boundaries always prevail over visual navigation.
        </p>
        {q && <p role="status" className="mt-3 type-caption text-fg-muted">Showing tokens matching “{query.trim()}”. Press Escape in the search box to clear.</p>}
      </section>

      <Architecture />
      <StatusVocabulary vals={vals} hit={hit} />
      <Colours vals={vals} hit={hit} theme={theme} />
      <Typography hit={hit} />
      <Spacing vals={vals} hit={hit} />
      <RadiusElevation vals={vals} hit={hit} />
      <Layout />
    </div>
  );
}

type Hit = (...fields: (string | undefined)[]) => boolean;
const NoMatch = () => <p className="rounded-card border border-dashed border-line px-card py-6 text-center type-secondary text-fg-muted">No tokens in this section match the search.</p>;

/* ------------------------------------------------------------------------------------------- architecture */
type PlaneTone = "secondary" | "accent";
const PLANE: Record<PlaneTone, { top: string; chip: string; icon: string }> = {
  secondary: { top: "border-t-accent-secondary", chip: "border-accent-secondary/40 bg-accent-secondary/10 text-accent-secondary-fg", icon: "text-accent-secondary-fg" },
  accent: { top: "border-t-accent", chip: "border-accent/40 bg-accent/10 text-accent-fg", icon: "text-accent-fg" },
};

function Plane({ tone, title, chip, items }: { tone: PlaneTone; title: string; chip: string; items: [string, LucideIcon][] }) {
  const t = PLANE[tone];
  return (
    <article className={`rounded-card border border-t-4 border-line ${t.top} bg-surface-card/80 p-card shadow-card`}>
      <h3 className="type-card text-fg">{title}</h3>
      <span className={`mt-2 inline-flex rounded-chip border px-2.5 py-1 type-secondary font-medium ${t.chip}`}>{chip}</span>
      <ul className="mt-5 grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        {items.map(([label, Icon]) => (
          <li key={label} className="flex items-center gap-3 type-secondary text-fg/90">
            <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-control border border-line bg-surface-raised ${t.icon}`}><Icon size={15} aria-hidden /></span>
            {label}
          </li>
        ))}
      </ul>
    </article>
  );
}

function Architecture() {
  return (
    <Panel title="Architecture / two authority planes" icon={Layers}>
      <div className="grid items-center gap-stack lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <Plane tone="secondary" title="Platform Control Plane" chip="Super Admin • SA-00–SA-12"
          items={[["Service health", BadgeCheck], ["Source integrity", FileText], ["Tenant metadata", Building], ["JIT access", KeyRound]]} />
        <div className="flex items-center justify-center gap-3 py-1 lg:flex-col lg:px-2">
          <span className="flex h-12 w-12 items-center justify-center rounded-full border border-status-critical-line bg-status-critical-tint text-status-critical ring-4 ring-status-critical-tint">
            <EqualNot size={20} aria-hidden />
          </span>
          <span className="type-label text-fg-muted">Boundary</span>
          <span className="sr-only">: the two planes share no data or authority. The server enforces this on every request.</span>
        </div>
        <Plane tone="accent" title="Customer Assurance Workspace" chip="Organization • ORG-00–ORG-06"
          items={[["Obligations", ShieldCheck], ["Controls & Tests", SlidersHorizontal], ["Evidence", Files], ["Human Approvals", UserCheck]]} />
      </div>
      <p className="mt-5 max-w-4xl type-caption text-fg-muted">
        Illustrative. The server enforces each plane on every request (tenant isolation, role permissions, audit). Navigation, including
        this page and either workspace&apos;s sidebar, is presentation only: it never grants or withholds access.
      </p>
    </Panel>
  );
}

/* --------------------------------------------------------------------------------------- status vocabulary */
const STATUS_MARK = STATUS_TONE;  // the design-system status classes (components/ds/Status)

function StatusVocabulary({ vals, hit }: { vals: Values; hit: Hit }) {
  const list = STATUSES.filter((s) => hit(s.label, s.token, s.meaning, s.current));
  return (
    <Panel title="Canonical status vocabulary" icon={SwatchBook}>
      {list.length === 0 ? <NoMatch /> : (
        <ul className="grid gap-stack sm:grid-cols-2 xl:grid-cols-5">
          {list.map((s) => {
            const Icon = STATUS_ICON[s.key];
            const v = statusVars(s.key);
            return (
              <li key={s.key} className="flex flex-col gap-3 rounded-card border border-line bg-surface-card/70 p-4">
                <div className="flex items-center gap-3.5">
                  <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full border ${STATUS_MARK[s.key]}`}><Icon size={18} aria-hidden /></span>
                  <div className="min-w-0">
                    <p className={`type-secondary font-semibold ${s.key === "unknown" ? "text-fg-secondary" : "text-fg"}`}>{s.label}</p>
                    <p className="type-code text-fg-muted">{s.token}</p>
                  </div>
                </div>
                <p className="type-caption text-fg-secondary">{s.meaning}</p>
                <div className="mt-auto flex flex-wrap gap-1.5">
                  <Ratio vals={vals} fg={v.fg} bg="--za-surface-card" label="card" />
                  <Ratio vals={vals} fg={v.fg} bg={v.tint} label="tint" />
                </div>
                <p className="type-caption text-fg-muted">Status pills today: <span className="type-code">{s.current}</span> tone</p>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-5 max-w-4xl type-caption text-fg-muted">
        Visual treatment only: backend status codes and their meanings are unchanged. Existing status pills keep their current tones until they
        are moved onto this vocabulary in a later task.
      </p>
    </Panel>
  );
}

/* ------------------------------------------------------------------------------------------------- colour */
/** Contrast of a foreground token on a background token (a translucent background is composited over the card). */
function ratioOf(vals: Values, fg: CssVar, bg: CssVar): number | null {
  const f = parseColor(vals[fg] ?? ""), b = parseColor(vals[bg] ?? ""), card = parseColor(vals["--za-surface-card"] ?? "");
  if (!f || !b || !card) return null;
  const base = b[3] < 1 ? over(b, card) : b;
  return contrast(f[3] < 1 ? over(f, base) : f, base);
}

const BG_LABEL: Record<string, string> = {
  "--za-surface-app": "app", "--za-surface-card": "card", "--za-surface-sidebar": "sidebar", "--za-accent": "primary",
  "--za-accent-strong": "primary strong", "--za-fg": "primary text",
};

function Ratio({ vals, fg, bg, label, min = 4.5 }: { vals: Values; fg: CssVar; bg: CssVar; label?: string; min?: number }) {
  const r = ratioOf(vals, fg, bg);
  if (r === null) return null;
  const ok = r >= min;
  return (
    <span title={`${ok ? "Meets" : "Fails"} WCAG 2.2 AA (${min}:1) on ${label ?? BG_LABEL[bg] ?? bg}`}
      className={`inline-flex items-center gap-1 rounded-chip border px-1.5 type-caption ${ok ? STATUS_MARK.verified : STATUS_MARK.critical}`}>
      {ok ? <Check size={11} aria-hidden /> : <AlertTriangle size={11} aria-hidden />}
      <span className="tabular-nums">{r.toFixed(1)}:1</span> {label ?? BG_LABEL[bg] ?? bg}
      <span className="sr-only">{ok ? ", passes AA" : ", fails AA"}</span>
    </span>
  );
}

function Colours({ vals, hit, theme }: { vals: Values; hit: Hit; theme: string }) {
  const pairs = COLOR_GROUPS.flatMap((g) => g.tokens.flatMap((t) => (t.contrastOn ?? []).map((bg) => ({ r: ratioOf(vals, t.cssVar, bg), min: t.min ?? 4.5 }))))
    .concat(STATUSES.flatMap((s) => { const v = statusVars(s.key); return [ratioOf(vals, v.fg, "--za-surface-card"), ratioOf(vals, v.fg, v.tint)].map((r) => ({ r, min: 4.5 })); }))
    .filter((p): p is { r: number; min: number } => p.r !== null);
  const passed = pairs.filter((p) => p.r >= p.min).length;
  const groups = COLOR_GROUPS.map((g) => ({ ...g, tokens: g.tokens.filter((t) => hit(t.name, t.cssVar, t.utility, t.role, vals[t.cssVar], g.title)) }))
    .filter((g) => g.tokens.length > 0);
  return (
    <Panel title="Colour system" icon={Palette}
      aside={pairs.length > 0 && (
        <span role="status" className={`inline-flex items-center gap-1.5 rounded-chip border px-2 py-1 type-caption ${passed === pairs.length ? STATUS_MARK.verified : STATUS_MARK.critical}`}>
          {passed === pairs.length ? <Check size={12} aria-hidden /> : <AlertTriangle size={12} aria-hidden />}
          {passed} of {pairs.length} contrast pairs meet WCAG AA in the {theme} theme
        </span>
      )}>
      {groups.length === 0 ? <NoMatch /> : (
        <div className="grid gap-stack lg:grid-cols-2">
          {groups.map((g) => (
            <section key={g.id} aria-labelledby={`colour-${g.id}`} className="rounded-card border border-line bg-surface-card/70 p-card">
              <h3 id={`colour-${g.id}`} className="type-card text-fg">{g.title}</h3>
              <p className="mt-0.5 type-caption text-fg-muted">{g.description}</p>
              <ul className="mt-3 divide-y divide-line">{g.tokens.map((t) => <ColourRow key={t.cssVar} t={t} vals={vals} />)}</ul>
            </section>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ColourRow({ t, vals }: { t: ColorToken; vals: Values }) {
  return (
    <li className="flex items-start gap-3 py-3">
      <span aria-hidden className="h-10 w-10 shrink-0 rounded-control border border-line-strong" style={{ background: `var(${t.cssVar})` }} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="type-secondary font-semibold text-fg">{t.name}</span>
          <code className="type-code text-fg-muted">{vals[t.cssVar] || "…"}</code>
        </div>
        <code className="block type-code text-accent-fg">{t.utility}</code>
        <p className="mt-0.5 type-caption text-fg-secondary">{t.role}</p>
        {t.contrastOn && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">{t.contrastOn.map((bg) => <Ratio key={bg} vals={vals} fg={t.cssVar} bg={bg} min={t.min} />)}</div>
        )}
      </div>
    </li>
  );
}

/* --------------------------------------------------------------------------------------------- typography */
function Typography({ hit }: { hit: Hit }) {
  const roles = TYPE_ROLES.filter((r) => hit(r.name, r.utility, r.usage, r.sample));
  return (
    <Panel title="Typography" icon={Type}
      aside={<span className="type-caption text-fg-muted">IBM Plex Sans for interface text · IBM Plex Mono for identifiers</span>}>
      {roles.length === 0 ? <NoMatch /> : <ul className="divide-y divide-line">{roles.map((r) => <TypeRow key={r.utility} {...r} />)}</ul>}
    </Panel>
  );
}

function TypeRow({ name, utility, sample, usage }: (typeof TYPE_ROLES)[number]) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [spec, setSpec] = useState("");
  useLayoutEffect(() => {
    if (!ref.current) return;
    const cs = getComputedStyle(ref.current);
    const family = cs.fontFamily.split(",")[0].replace(/"/g, "");
    setSpec(`${family} · ${parseFloat(cs.fontSize)}/${parseFloat(cs.lineHeight)}px · ${cs.fontWeight}${cs.letterSpacing !== "normal" ? ` · ${cs.letterSpacing}` : ""}${cs.textTransform !== "none" ? ` · ${cs.textTransform}` : ""}`);
  }, []);
  return (
    <li className="grid gap-2 py-4 md:grid-cols-[minmax(0,1fr)_17rem] md:items-center md:gap-6">
      <p ref={ref} className={`${utility} min-w-0 break-words text-fg`}>{sample}</p>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2"><span className="type-secondary font-semibold text-fg">{name}</span><code className="type-code text-accent-fg">{utility}</code></div>
        <p className="type-code text-fg-muted">{spec || "…"}</p>
        <p className="type-caption text-fg-secondary">{usage}</p>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------------------------------------ spacing */
function Spacing({ vals, hit }: { vals: Values; hit: Hit }) {
  const list = SPACING.filter((s) => hit(s.name, s.utility, s.role, s.cssVar, vals[s.cssVar]));
  return (
    <Panel title="Spacing" icon={Ruler} aside={<span className="type-caption text-fg-muted">Fluid values resolve at the current window width</span>}>
      {list.length === 0 ? <NoMatch /> : (
        <ul className="grid gap-x-section gap-y-4 md:grid-cols-2">
          {list.map((s) => <SpaceRow key={s.cssVar} {...s} value={vals[s.cssVar]} />)}
        </ul>
      )}
    </Panel>
  );
}

function SpaceRow({ name, cssVar, utility, role, value }: (typeof SPACING)[number] & { value?: string }) {
  const bar = useRef<HTMLSpanElement>(null);
  const [px, setPx] = useState<number | null>(null);
  useEffect(() => {
    const measure = () => setPx(bar.current ? Math.round(bar.current.getBoundingClientRect().width) : null);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return (
    <li className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="type-secondary font-semibold text-fg">{name} <code className="ml-1 type-code font-normal text-accent-fg">{utility}</code></span>
        <code className="type-code text-fg-muted">{value || "…"}{px !== null && value && !value.endsWith("px") ? ` → ${px}px` : ""}</code>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-line">
        <span ref={bar} className="block h-full max-w-full rounded-full bg-linear-to-r from-accent-strong to-accent" style={{ width: `var(${cssVar})` }} />
      </div>
      <p className="mt-1 type-caption text-fg-secondary">{role}</p>
    </li>
  );
}

/* -------------------------------------------------------------------------------------- radius & elevation */
function RadiusElevation({ vals, hit }: { vals: Values; hit: Hit }) {
  const radii = RADII.filter((r) => hit(r.name, r.utility, r.role, vals[r.cssVar], "radius"));
  const shadows = SHADOWS.filter((s) => hit(s.name, s.utility, s.role, "shadow elevation"));
  return (
    <Panel title="Radius & elevation" icon={Square}>
      {radii.length + shadows.length === 0 ? <NoMatch /> : (
        <div className="space-y-6">
          {radii.length > 0 && (
            <div>
              <h3 className="type-card text-fg">Radius</h3>
              <ul className="mt-3 grid grid-cols-2 gap-stack sm:grid-cols-4 xl:grid-cols-7">
                {radii.map((r) => (
                  <li key={r.utility} className="min-w-0">
                    <span aria-hidden className={`block h-16 border border-line-strong bg-surface-card ${r.utility}`} />
                    <p className="mt-2 type-secondary font-semibold text-fg">{r.name}</p>
                    <code className="block type-code text-accent-fg">{r.utility}</code>
                    <p className="type-caption text-fg-muted">{vals[r.cssVar] || "…"} · {r.role}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {shadows.length > 0 && (
            <div>
              <h3 className="type-card text-fg">Elevation</h3>
              <ul className="mt-3 grid gap-stack sm:grid-cols-2 xl:grid-cols-4">
                {shadows.map((s) => (
                  <li key={s.utility} className="min-w-0">
                    <span aria-hidden className={`block h-20 rounded-card border border-line bg-surface-card ${s.utility}`} />
                    <p className="mt-3 type-secondary font-semibold text-fg">{s.name} <code className="ml-1 type-code font-normal text-accent-fg">{s.utility}</code></p>
                    <p className="type-caption text-fg-muted">{s.role}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------------------------------------- layout */
const LAYOUT_RULES: [string, string, string][] = [
  ["Application sidebar", "w-sidebar · p-sidebar-inset", "16rem, persistent from lg. Below lg it becomes a drawer or a scrolling section row."],
  ["Top navigation", "h-header", "4rem. Breadcrumb and context on the left; search, alerts and account on the right."],
  ["Main content", "max-w-content · px-gutter · py-page", "Up to 90rem wide, centred, with fluid side margins."],
  ["Dashboard grids", "gap-stack · gap-section", "KPI rows 2 → 3 (sm) → 6 (xl). Main and side panels stack, then split 2:1 from xl."],
  ["Content panels", "rounded-panel · p-panel · shadow-panel", "Panels group related cards; cards use rounded-card · p-card · shadow-card."],
];

function Layout() {
  return (
    <Panel title="Layout foundations" icon={LayoutGrid}>
      <div className="grid gap-section xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <figure>
          <div aria-hidden className="flex h-52 gap-2 rounded-card border border-line bg-surface-app p-2.5">
            <div className="flex w-1/4 flex-col gap-1.5 rounded-control bg-surface-sidebar p-2">
              <span className="h-2 w-3/4 rounded-full bg-sidebar-strong/70" />
              {[0, 1, 2, 3].map((i) => <span key={i} className={`h-2 rounded-full ${i === 0 ? "bg-accent" : "bg-sidebar-muted/40"}`} />)}
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <span className="h-5 rounded-control border border-line bg-surface-raised" />
              <div className="grid grid-cols-6 gap-1">{[0, 1, 2, 3, 4, 5].map((i) => <span key={i} className="h-6 rounded border border-line bg-surface-card" />)}</div>
              <div className="grid flex-1 grid-cols-3 gap-1">
                <span className="col-span-2 rounded border border-line bg-surface-card" />
                <span className="flex flex-col gap-1"><span className="flex-1 rounded border border-line bg-surface-card" /><span className="flex-1 rounded border border-line bg-surface-card" /></span>
              </div>
            </div>
          </div>
          <figcaption className="mt-2 type-caption text-fg-muted">Sidebar · header · KPI row · 2:1 panels, as used by the Organization Command Center.</figcaption>
        </figure>
        <div className="min-w-0 space-y-5">
          <dl className="divide-y divide-line">
            {LAYOUT_RULES.map(([k, u, d]) => (
              <div key={k} className="grid gap-1 py-2.5 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
                <dt className="type-secondary font-semibold text-fg">{k}</dt>
                <dd className="min-w-0"><code className="type-code text-accent-fg">{u}</code><p className="type-caption text-fg-secondary">{d}</p></dd>
              </div>
            ))}
          </dl>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[28rem] border-collapse text-left">
              <caption className="pb-2 text-left type-label text-fg-muted">Responsive breakpoints (Tailwind defaults)</caption>
              <thead><tr className="border-b border-line">{["Name", "From", "Layout change"].map((h) => <th key={h} scope="col" className="px-cell-x py-cell-y type-label text-fg-muted">{h}</th>)}</tr></thead>
              <tbody>
                {BREAKPOINTS.map((b) => (
                  <tr key={b.name} className="border-b border-line last:border-0">
                    <td className="px-cell-x py-cell-y type-code text-accent-fg">{b.name}</td>
                    <td className="px-cell-x py-cell-y type-secondary tabular-nums text-fg">{b.min}px</td>
                    <td className="px-cell-x py-cell-y type-secondary text-fg-secondary">{b.rule}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Panel>
  );
}
