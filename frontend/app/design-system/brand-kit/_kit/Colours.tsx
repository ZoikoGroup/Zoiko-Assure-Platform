"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { Button, Callout, Card, DescriptionList, EmptyState, Field, PlaneBadge, SearchInput, TabPanel, Tabs, type Plane } from "@/components/ds";
import { COLOR_GROUPS, STATUSES, statusVars, type CssVar } from "@/lib/design-tokens";
import { Block, CopyButton, ProvenanceChip, nearestToken, ratio, toHex, useArtworkColours, useThemeVars, type ThemeName, type ThemeVals } from "./shared";

/* C. Brand colour palette. Names, utilities and roles come from the Task 1 registry (lib/design-tokens.ts); every
   value is read from the stylesheet in both themes. Nothing here defines or changes a colour. */

type Tok = { group: string; name: string; cssVar: CssVar; utility?: string; role: string; contrastOn?: CssVar[]; min?: number; plane?: Plane };
const SURFACES: CssVar[] = ["--za-surface-app", "--za-surface-card"];

const STATUS_TOKENS: Tok[] = STATUSES.flatMap((s) => {
  const v = statusVars(s.key);
  return [
    { group: "status", name: `${s.label} · text & icon`, cssVar: v.fg, utility: `text-status-${s.key}`, role: `${s.meaning} Text and icons.`, contrastOn: ["--za-surface-card", v.tint] },
    { group: "status", name: `${s.label} · tint`, cssVar: v.tint, utility: `bg-status-${s.key}-tint`, role: "Badge and callout fill" },
    { group: "status", name: `${s.label} · line`, cssVar: v.line, utility: `border-status-${s.key}-line`, role: "Badge and callout border" },
  ];
});
const PLANE_OF: Record<string, Plane> = { "--za-accent": "workspace", "--za-accent-secondary": "platform" };
/** Palette primitives (app/globals.css: navy, brand orange, slate-brand; app/design-tokens.css: gold, azure, midnight, charcoal). */
const PRIMITIVES: Tok[] = [
  ...[50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((n) => ({ group: "primitive", name: `Navy ${n}`, cssVar: `--color-navy-${n}` as CssVar, utility: `bg-navy-${n}`, role: "Logo-family navy (globals.css)" })),
  ...[50, 100, 200, 300, 400, 500, 600, 700, 800, 900].map((n) => ({ group: "primitive", name: `Brand orange ${n}`, cssVar: `--color-brand-${n}` as CssVar, utility: `bg-brand-${n}`, role: "Logo-family orange (globals.css)" })),
  { group: "primitive", name: "Slate brand", cssVar: "--color-slate-brand", utility: "text-slate-brand", role: "Logo-family grey (globals.css)" },
  ...[200, 300, 400, 500, 600].map((n) => ({ group: "primitive", name: `Gold ${n}`, cssVar: `--color-gold-${n}` as CssVar, utility: `bg-gold-${n}`, role: "Dark-theme accent family (design-tokens.css)" })),
  ...[300, 400, 500].map((n) => ({ group: "primitive", name: `Azure ${n}`, cssVar: `--color-azure-${n}` as CssVar, utility: `bg-azure-${n}`, role: "Dark-theme secondary family (design-tokens.css)" })),
  ...[800, 900, 950].map((n) => ({ group: "primitive", name: `Midnight ${n}`, cssVar: `--color-midnight-${n}` as CssVar, utility: `bg-midnight-${n}`, role: "Dark surfaces (design-tokens.css)" })),
  ...[700, 800, 900].map((n) => ({ group: "primitive", name: `Charcoal ${n}`, cssVar: `--color-charcoal-${n}` as CssVar, utility: `bg-charcoal-${n}`, role: "Dark surfaces (design-tokens.css)" })),
];
const SEMANTIC: Tok[] = COLOR_GROUPS.flatMap((g) => g.tokens.map((t) => ({ group: g.id, ...t, plane: PLANE_OF[t.cssVar] })));
/** Each token once: primitives the Task 1 groups already list (dark navy, deep charcoal) stay in their Task 1 group. */
const TOKENS: Tok[] = [...SEMANTIC, ...STATUS_TOKENS, ...PRIMITIVES.filter((p) => !SEMANTIC.some((s) => s.cssVar === p.cssVar))];
const GROUPS = [
  { value: "all", label: "All" }, ...COLOR_GROUPS.map((g) => ({ value: g.id, label: g.title })),
  { value: "status", label: "Status" }, { value: "primitive", label: "Primitives" }, { value: "logo", label: "Logo artwork" },
];
const VARS = [...new Set([...TOKENS.map((t) => t.cssVar), ...SURFACES, "--za-surface-sidebar"])];

export function Colours() {
  const vals = useThemeVars(VARS);
  const [group, setGroup] = useState("all");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<CssVar>("--za-accent");
  const query = q.trim().toLowerCase();
  const shown = useMemo(() => TOKENS.filter((t) => (group === "all" || t.group === group) && (!query || [t.name, t.cssVar, t.utility, t.role,
    vals && toHex(vals.light[t.cssVar]), vals && toHex(vals.dark[t.cssVar])].some((f) => f?.toLowerCase().includes(query)))), [group, query, vals]);
  const current = TOKENS.find((t) => t.cssVar === selected) ?? TOKENS[0];

  return (
    <div className="space-y-section">
      <Callout tone="info" title="Read from the stylesheet, in both themes">
        Every value below is read live from <code className="type-code">app/design-tokens.css</code> and <code className="type-code">app/globals.css</code> through
        hidden light and dark probes, so both themes show side by side whatever theme this page is in. Nothing is copied into this page. Orange leads and
        marks the Customer Assurance Workspace; blue is secondary and marks the Platform Control Plane.
      </Callout>

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 sm:max-w-sm"><Field label="Search colours"><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Name, token, utility or HEX" /></Field></div>
      </div>
      <Tabs id="bk-colour-group" label="Colour group" variant="pills" value={group} onChange={setGroup}
        items={GROUPS.map((g) => ({ ...g, count: g.value === "logo" ? undefined : g.value === "all" ? TOKENS.length : TOKENS.filter((t) => t.group === g.value).length }))} />
      <TabPanel id="bk-colour-group" value={group} className="">
        {group === "logo" ? <LogoArtwork vals={vals} /> : (
          <div className="grid gap-stack min-[1680px]:grid-cols-[minmax(0,1fr)_24rem]">
            {shown.length === 0 ? <EmptyState icon={Search} title="No colours match" description="Try a token name such as accent, or a HEX value." action={<Button size="sm" onClick={() => { setQ(""); setGroup("all"); }}>Clear</Button>} /> : (
              <ul className="grid content-start gap-3 sm:grid-cols-2 2xl:grid-cols-3" aria-label="Colour tokens">
                {shown.map((t) => <TokenCard key={t.cssVar} t={t} vals={vals} active={t.cssVar === selected} onInspect={() => setSelected(t.cssVar)} />)}
              </ul>
            )}
            <Inspector t={current} vals={vals} />
          </div>
        )}
      </TabPanel>
    </div>
  );
}

const Swatch = ({ value, theme }: { value?: string; theme: ThemeName }) => {
  const hex = toHex(value);
  return (
    <div className="min-w-0 flex-1">
      <div data-theme={theme} className="rounded-control border border-line bg-surface-card p-1">
        <div className="h-10 rounded-[calc(var(--radius-control)-4px)]" style={{ background: value || undefined }} />
      </div>
      <p className="mt-1 flex items-center justify-between gap-1 type-caption"><span className="text-fg-muted">{theme === "light" ? "Light" : "Dark"}</span>
        <code className="type-code text-fg">{hex ?? (value ? "n/a" : "not emitted")}</code></p>
    </div>
  );
};

function TokenCard({ t, vals, active, onInspect }: { t: Tok; vals: ThemeVals | null; active: boolean; onInspect: () => void }) {
  return (
    <li className={`flex flex-col gap-3 rounded-card border bg-surface-card/80 p-4 ${active ? "border-accent" : "border-line"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="type-secondary font-semibold text-fg">{t.name}</p>
          <p className="flex items-center gap-1 type-code text-fg-muted"><span className="truncate">{t.cssVar}</span><CopyButton text={`var(${t.cssVar})`} label={`token ${t.cssVar}`} /></p>
        </div>
        {t.plane && <PlaneBadge plane={t.plane} size="sm" />}
      </div>
      <div className="flex gap-2"><Swatch value={vals?.light[t.cssVar]} theme="light" /><Swatch value={vals?.dark[t.cssVar]} theme="dark" /></div>
      <p className="type-caption text-fg-secondary">{t.role}</p>
      <div className="mt-auto flex items-center justify-between gap-2">
        {t.utility ? <code className="type-code text-fg-muted">{t.utility}</code> : <span />}
        <Button size="sm" variant={active ? "secondary" : "ghost"} aria-pressed={active} onClick={onInspect}>{active ? "Inspecting" : "Inspect"}<span className="sr-only"> {t.name}</span></Button>
      </div>
    </li>
  );
}

function Inspector({ t, vals }: { t: Tok; vals: ThemeVals | null }) {
  const row = (theme: ThemeName) => {
    const v = vals?.[theme][t.cssVar];
    const hex = toHex(v);
    const checks = (t.contrastOn ?? []).map((bg) => ({ bg, r: ratio(v, vals?.[theme][bg], vals?.[theme]["--za-surface-card"]) }));
    return { v, hex, checks };
  };
  const min = t.min ?? 4.5;
  return (
    <Card title="Token inspector" description={t.name} className="h-fit min-[1680px]:sticky min-[1680px]:top-6" bodyClassName="p-card space-y-4">
      <div className="flex gap-2"><Swatch value={vals?.light[t.cssVar]} theme="light" /><Swatch value={vals?.dark[t.cssVar]} theme="dark" /></div>
      <DescriptionList columns={1} items={[
        ["Token", <span key="t" className="flex items-center gap-1.5"><code className="type-code">{t.cssVar}</code><CopyButton text={`var(${t.cssVar})`} label="token" /></span>],
        ...(t.utility ? [["Utility", <span key="u" className="flex items-center gap-1.5"><code className="type-code">{t.utility}</code><CopyButton text={t.utility} label="utility" /></span>] as [string, ReactNode]] : []),
        ...(["light", "dark"] as ThemeName[]).map((th) => {
          const r = row(th);
          return [`${th === "light" ? "Light" : "Dark"} value`, (
            <span key={th} className="space-y-1">
              <span className="flex items-center gap-1.5"><code className="type-code">{r.hex ?? r.v ?? "—"}</code>{r.hex && <CopyButton text={r.hex} label={`${th} HEX of ${t.cssVar}`} />}</span>
              {r.v && r.hex && r.v.toUpperCase() !== r.hex && <span className="block type-caption text-fg-muted">Computed: <code className="type-code">{r.v}</code></span>}
              {r.checks.map((c) => c.r !== null && (
                <span key={c.bg} className={`block type-caption font-semibold ${c.r >= min ? "text-status-verified" : "text-status-critical"}`}>
                  {c.r.toFixed(2)}:1 on <code className="type-code font-normal">{c.bg}</code> {c.r >= min ? `passes ${min}:1` : `below ${min}:1`}
                </span>
              ))}
            </span>
          )] as [string, ReactNode];
        }),
        ["Role", t.role],
        ...(t.plane ? [["Authority plane", <PlaneBadge key="p" plane={t.plane} size="sm" />] as [string, ReactNode]] : []),
      ]} />
      <p className="type-caption text-fg-muted">Ratios use WCAG 2.2: 4.5:1 for text, 3:1 for the focus ring and other non-text marks (the Task 1 standard).</p>
    </Card>
  );
}

/* The logo files' own colours, measured in the browser, beside the nearest palette primitive. */
function LogoArtwork({ vals }: { vals: ThemeVals | null }) {
  const word = useArtworkColours("/logo.png");
  const mark = useArtworkColours("/logo-mark.jpg");
  const candidates = vals ? Object.fromEntries(PRIMITIVES.map((p) => [p.cssVar, vals.light[p.cssVar]]).filter(([, v]) => v)) : {};
  const rows = (label: string, file: string, s: ReturnType<typeof useArtworkColours>) => (
    <Block title={`${label} · ${file}`}>
      {s.error ? <p className="type-secondary text-status-critical">The file could not be measured.</p> : !s.colours ? <p className="type-secondary text-fg-muted">Measuring…</p> : (
        <ul className="grid gap-3 sm:grid-cols-3">
          {s.colours.map((c, i) => {
            const n = nearestToken(c.rgb, candidates);
            return (
              <li key={i} className="rounded-card border border-line bg-surface-card/80 p-3">
                <div className="h-10 rounded-control border border-line" style={{ backgroundColor: c.hex }} />
                <p className="mt-2 flex items-center justify-between gap-1"><code className="type-code text-fg">{c.hex}</code><CopyButton text={c.hex} label={`artwork colour ${c.hex}`} /></p>
                <p className="type-caption text-fg-muted">{Math.round(c.share * 100)}% of the artwork</p>
                {n && <p className="type-caption text-fg-secondary">Nearest token <code className="type-code">{n.token}</code> ({n.hex}), {n.delta === 0 ? "exact" : `differs by up to ${n.delta} per channel`}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </Block>
  );
  return (
    <div className="space-y-stack">
      <Callout tone="info" title="Measured from the files, not taken from a brand specification">
        No brand colour specification exists in the repository. These are the dominant colours of each file, measured in your browser. The palette tokens are close to
        them but are not exact copies. The mark is a JPEG, so its colours vary slightly with compression and differ a little from the wordmark&apos;s.
      </Callout>
      {rows("Wordmark", "logo.png", word)}
      {rows("Symbol mark", "logo-mark.jpg", mark)}
      <ProvenanceChip kind="observed">A brand-owner colour specification would settle the exact values</ProvenanceChip>
    </div>
  );
}
