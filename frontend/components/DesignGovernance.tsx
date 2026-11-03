"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowLeft, Building2, Component, Download, Eye, FileText, Image as ImageIcon, LayoutTemplate, Moon, Search, Sun } from "lucide-react";
import { COLOR_GROUPS, RADII, SHADOWS, SPACING, STATUSES, TYPE_ROLES, statusVars } from "@/lib/design-tokens";
import { ToastProvider } from "./ds";
import { Logo } from "./Shell";

/* The Design Governance workspace: the Unified Design System's own shell. It is built only from the foundation tokens
   (app/design-tokens.css), so it doubles as their reference implementation. It renders without the application's
   sidebar and header (see STANDALONE in nav.ts), but only after sign-in, like every other screen. */

export type DsTheme = "dark" | "light";
const GovernanceCtx = createContext<{ theme: DsTheme; query: string }>({ theme: "dark", query: "" });
/** The workspace's theme and the header's token search, for the page inside the shell. */
export const useGovernance = () => useContext(GovernanceCtx);

type Section = { id: string; label: string; icon: LucideIcon; href?: string };
/* Every section exists except Icons & Brand Kit, which is planned and not a link yet. */
const SECTIONS: { group: string; items: Section[] }[] = [
  { group: "Design governance", items: [
    { id: "foundations", label: "Foundations", icon: LayoutTemplate, href: "/design-system" },
    { id: "components", label: "Components", icon: Component, href: "/design-system/components" },
    { id: "flows", label: "High-Assurance Flows", icon: Eye, href: "/design-system/flows" },
  ] },
  { group: "Application shells", items: [
    { id: "super-admin", label: "Super Admin Planes", icon: FileText, href: "/design-system/super-admin" },
    { id: "org", label: "Org Workspace", icon: Building2, href: "/design-system/org-workspace" },
  ] },
  { group: "Assets", items: [{ id: "brand", label: "Icons & Brand Kit", icon: ImageIcon }] },
];

const THEME_KEY = "za-ds-theme";
const CONTROL = "h-10 rounded-control border border-line bg-surface-control/80 text-fg-secondary transition-colors hover:border-line-strong hover:text-fg";

export function GovernanceShell({ active, searchLabel = "Search tokens", children }: { active: string; searchLabel?: string; children: ReactNode }) {
  const [theme, setTheme] = useState<DsTheme>("dark");
  const [query, setQuery] = useState("");
  const [mac, setMac] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try { const t = localStorage.getItem(THEME_KEY); if (t === "light" || t === "dark") setTheme(t); } catch {}
    setMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
  }, []);
  // "/" (when not typing) or ⌘K / Ctrl+K moves focus to the token search.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
      const combo = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (!combo && !(e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey)) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const other: DsTheme = theme === "dark" ? "light" : "dark";
  const toggle = () => { setTheme(other); try { localStorage.setItem(THEME_KEY, other); } catch {} };

  return (
    <GovernanceCtx.Provider value={{ theme, query }}>
      <div data-theme={theme} className="min-h-screen bg-backdrop text-fg">
        <a href="#main" className="sr-only z-50 rounded-control bg-accent px-3 py-2 text-on-accent focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
        <aside aria-label="Design governance"
          className="fixed inset-y-4 left-4 z-30 hidden w-sidebar flex-col overflow-hidden rounded-panel border border-sidebar-line bg-surface-sidebar/85 shadow-panel backdrop-blur-md lg:flex">
          <Sidebar active={active} />
        </aside>

        <div className="lg:pl-[calc(var(--spacing-sidebar)+2rem)]">
          <header className="px-gutter pt-5">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
              <span title="This design system is a proposal awaiting design-governance approval."
                className="rounded-chip border border-line-strong bg-surface-control/70 px-2.5 py-1.5 type-label text-fg">Proposed for approval</span>
              <nav aria-label="Breadcrumb">
                <ol className="flex items-center gap-2 type-secondary font-medium uppercase tracking-wide text-fg-secondary">
                  <li><Link href="/" className="rounded hover:text-fg">Zoiko Assure</Link></li>
                  <li aria-hidden className="text-fg-muted">/</li>
                  <li aria-current="page">Design Governance</li>
                </ol>
              </nav>
              <div className="flex w-full items-center gap-3 sm:ml-auto sm:w-auto">
                <div className="relative min-w-0 flex-1 sm:flex-none">
                  <Search size={15} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />
                  <input ref={searchRef} type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Escape") setQuery(""); }}
                    placeholder={`${searchLabel}...`} aria-label={searchLabel} aria-keyshortcuts="/ Control+K Meta+K"
                    className={`${CONTROL} w-full pl-9 pr-12 type-secondary text-fg placeholder:text-fg-muted focus:border-accent focus:outline-none sm:w-64`} />
                  {!query && <kbd aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded-chip border border-line px-1.5 type-code text-fg-muted">{mac ? "⌘K" : "Ctrl K"}</kbd>}
                </div>
                <button type="button" onClick={toggle} aria-label={`Switch to ${other} theme`} title={`Switch to ${other} theme`}
                  className={`${CONTROL} inline-flex w-10 shrink-0 items-center justify-center`}>
                  {theme === "dark" ? <Sun size={17} aria-hidden /> : <Moon size={17} aria-hidden />}
                </button>
                <span aria-hidden className="hidden h-6 w-px bg-line sm:block" />
                <button type="button" onClick={exportTokens} aria-label="Export Library" title="Download every foundation token, in both themes, as JSON"
                  className={`${CONTROL} inline-flex shrink-0 items-center gap-2 px-3 type-secondary font-semibold text-fg sm:px-4`}>
                  <Download size={16} aria-hidden /><span className="hidden sm:inline">Export Library</span>
                </button>
              </div>
            </div>
            <MobileSections active={active} />
          </header>
          <main id="main" className="px-gutter pb-page pt-8">
            <ToastProvider><div className="mx-auto max-w-content">{children}</div></ToastProvider>
          </main>
        </div>
      </div>
    </GovernanceCtx.Provider>
  );
}

function Sidebar({ active }: { active: string }) {
  return (
    <>
      <div className="p-sidebar-inset pb-2">
        <Link href="/" aria-label="Zoiko Assure home" className="inline-flex rounded-card bg-white px-4 py-3 shadow-card">
          <Logo width={124} />
        </Link>
      </div>
      <nav className="scroll-thin flex-1 overflow-y-auto px-4 pb-4">
        {SECTIONS.map((g) => <SectionGroup key={g.group} group={g.group} items={g.items} active={active} />)}
        <p className="mt-5 px-3 type-caption text-sidebar-muted">Icons &amp; Brand Kit is a planned design-system task; the other sections are live.</p>
      </nav>
      <div className="border-t border-sidebar-line px-sidebar-inset py-4">
        <div className="flex items-center gap-3">
          <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white type-caption font-bold text-midnight-950">DS</span>
          <div className="min-w-0">
            <div className="type-secondary font-medium text-sidebar-strong">System v1.0</div>
            <div className="type-code text-sidebar-muted">ZA-DS-FIG-003</div>
          </div>
        </div>
        <Link href="/" className="mt-3 inline-flex items-center gap-1.5 rounded type-caption text-sidebar-fg hover:text-sidebar-strong">
          <ArrowLeft size={13} aria-hidden />Back to workspace
        </Link>
      </div>
    </>
  );
}

function SectionGroup({ group, items, active }: { group: string; items: Section[]; active: string }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="mt-5 first:mt-3">
      <div id={id} className="mb-2 px-3 type-label text-sidebar-muted">{group}</div>
      <ul className="space-y-1">
        {items.map(({ id: key, label, icon: Icon, href }) => (
          <li key={key}>
            {href ? (
              <Link href={href} aria-current={key === active ? "page" : undefined}
                className={`flex h-10 items-center gap-3 rounded-nav px-3 type-body transition-colors ${key === active
                  ? "bg-linear-to-r from-accent-strong to-accent font-semibold text-on-accent shadow-glow"
                  : "text-sidebar-fg hover:bg-sidebar-hover hover:text-sidebar-strong"}`}>
                <Icon size={17} aria-hidden className="shrink-0" />{label}
              </Link>
            ) : (
              <span aria-disabled="true" title="Planned for a later design-system task"
                className="flex h-10 cursor-not-allowed items-center gap-3 rounded-nav px-3 type-body text-sidebar-muted">
                <Icon size={17} aria-hidden className="shrink-0" /><span className="truncate">{label}</span><span className="sr-only"> (planned)</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Below lg the sections become a scrolling row under the header, in place of the sidebar. */
function MobileSections({ active }: { active: string }) {
  return (
    <nav aria-label="Design governance sections" className="no-scrollbar -mx-gutter mt-4 overflow-x-auto px-gutter lg:hidden">
      <ul className="flex gap-2">
        {SECTIONS.flatMap((g) => g.items).map(({ id, label, icon: Icon, href }) => (
          <li key={id} className="shrink-0">
            {href ? (
              <Link href={href} aria-current={id === active ? "page" : undefined}
                className={`flex h-9 items-center gap-2 rounded-nav px-3 type-secondary ${id === active
                  ? "bg-linear-to-r from-accent-strong to-accent font-semibold text-on-accent shadow-glow"
                  : "border border-line bg-surface-control/70 text-fg-secondary"}`}>
                <Icon size={15} aria-hidden />{label}
              </Link>
            ) : (
              <span aria-disabled="true" title="Planned for a later design-system task"
                className="flex h-9 items-center gap-2 rounded-nav border border-line px-3 type-secondary text-fg-disabled">
                <Icon size={15} aria-hidden />{label}
              </span>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}

/* ------------------------------------------------------------------------------------------------- export */
/** Downloads every foundation token as JSON, with each theme's values read from the stylesheet itself. */
function exportTokens() {
  const read = (theme: DsTheme) => {
    const probe = document.createElement("div");
    probe.dataset.theme = theme;
    probe.hidden = true;
    document.body.appendChild(probe);
    const cs = getComputedStyle(probe);
    const v = (name: string) => cs.getPropertyValue(name).trim();
    const out = {
      color: Object.fromEntries(COLOR_GROUPS.map((g) => [g.id, Object.fromEntries(g.tokens.map((t) => [t.cssVar, v(t.cssVar)]))])),
      status: Object.fromEntries(STATUSES.map((s) => { const sv = statusVars(s.key); return [s.token, { fg: v(sv.fg), tint: v(sv.tint), line: v(sv.line) }]; })),
      shadow: Object.fromEntries(SHADOWS.map((s) => [s.utility, v(s.cssVar)])),
    };
    probe.remove();
    return out;
  };
  const root = getComputedStyle(document.documentElement);
  const typeProbe = document.createElement("span");
  document.body.appendChild(typeProbe);
  const typography = Object.fromEntries(TYPE_ROLES.map((r) => {
    typeProbe.className = r.utility;
    const cs = getComputedStyle(typeProbe);
    return [r.utility, { role: r.name, fontFamily: cs.fontFamily, fontSize: cs.fontSize, lineHeight: cs.lineHeight, fontWeight: cs.fontWeight,
      letterSpacing: cs.letterSpacing, textTransform: cs.textTransform, usage: r.usage }];
  }));
  typeProbe.remove();
  const sizes = (list: typeof SPACING) => Object.fromEntries(list.map((t) => [t.utility, root.getPropertyValue(t.cssVar).trim()]));
  const library = {
    name: "Zoiko Assure Unified Design System", status: "Proposed for approval", version: "1.0", source: "frontend/app/design-tokens.css",
    exportedAt: new Date().toISOString(), themes: { dark: read("dark"), light: read("light") }, typography, spacing: sizes(SPACING), radius: sizes(RADII),
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(library, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "zoiko-assure-design-tokens.json";
  a.click();
  URL.revokeObjectURL(url);
}
