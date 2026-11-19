"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { Bell, Building, ChevronDown, Loader2, LogOut, Menu, Palette, PanelLeftClose, PanelLeftOpen, Search, ShieldCheck, UserCog } from "lucide-react";
import { api } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { roleLabel, rolesText, workContext } from "@/lib/roles";
import SCREEN_ACCESS from "@/lib/screen-access.json";
import { isStandalone, jumpTargets, navFor, orgNav, orgRouteMeta, orgSearchTargets, routeMeta } from "./nav";
import { PlatformFrame } from "./PlatformShell";
import { ThemeToggle } from "./ThemeToggle";
import {
  Breadcrumb, Drawer, Dropdown, DropdownItem, ErrorBox, Field, IconButton, Modal, PageTitleProvider, SearchInput, ToastProvider,
  Tooltip, useAction, useEnterAnimation, usePageTitle, useToast,
} from "./ui";

const initials = (n?: string) => (n ?? "?").replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
const shortName = (n?: string) => (n ?? "").replace(/\s*\(.*\)/, "");

/** Open work for the alerts menu, and the entity count for the organisation sidebar's scope (null: not readable by
 *  this person's roles). */
type OpenWork = { issues: number; facts: number; entities: number | null };

/** Whether the shell's own background reads (record watermark, alert counts, entity scope) will be served to someone
 *  with `permissions`. Generated from the backend guards (lib/screen-access.json `shell`): a read that would be refused
 *  is not made, so an ordinary page view never records a refusal (every refusal is an audit record, IAM-REQ-037). */
const SHELL = SCREEN_ACCESS.shell;
const mayRead = (read: { perms: string[] }, permissions?: readonly string[]) => read.perms.some((p) => permissions?.includes(p));

/* A page that shows synthetic demonstration figures raises the header's "Synthetic demo data" badge while mounted. */
const SyntheticCtx = createContext<(on: boolean) => void>(() => {});
export function useSyntheticDataBadge() {
  const set = useContext(SyntheticCtx);
  useEffect(() => { set(true); return () => set(false); }, [set]);
}

export default function Shell({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <PageTitleProvider>
        <Frame>{children}</Frame>
      </PageTitleProvider>
    </ToastProvider>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { me, ready } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [synthetic, setSynthetic] = useState(false);
  const [work, setWork] = useState<OpenWork | null>(null);
  const { title } = usePageTitle();
  // A new page fades in place (no remount, so nothing about the page's own state changes).
  const mainRef = useRef<HTMLElement>(null);
  useEnterAnimation(mainRef, [path]);

  // Each screen gets its own document title (WCAG 2.4.2): the page heading, with its section for detail records, or the
  // navigation label until the heading is set. The root metadata title stays the fallback. Organisation users' screens
  // are named as their own sidebar names them.
  useEffect(() => {
    const meta = (personaOf(me) === "tenant" ? orgRouteMeta(path) : null) ?? routeMeta(path);
    const page = path === "/login" ? "Sign in" : title ? (meta?.isDetail ? `${title} · ${meta.item.label}` : title) : meta?.item.label;
    document.title = page ? `${page} · Zoiko Assure` : "Zoiko Assure";
  }, [path, title, me]);
  useEffect(() => { try { setCollapsed(localStorage.getItem("za-nav-collapsed") === "1"); } catch {} }, []);
  const toggle = () => { const v = !collapsed; setCollapsed(v); try { localStorage.setItem("za-nav-collapsed", v ? "1" : "0"); } catch {} };
  useEffect(() => setMobile(false), [path]);
  useEffect(() => {
    setWork(null);
    if (!me?.tenant_id || !mayRead(SHELL.attention, me.permissions)) return;
    // Only the two counts (/dashboard/attention), not the full /dashboard aggregation: this runs on every full page
    // load. The organisation sidebar's entity count comes from the organisation record, the same legal entities.
    const entities = mayRead(SHELL.entity_scope, me.permissions) ? api("/org/tenant") : Promise.resolve(null);
    Promise.all([api("/dashboard/attention"), entities])
      .then(([a, t]) => setWork({ issues: a.open_issues, facts: a.open_fact_requests, entities: t ? t.entities.length : null })).catch(() => {});
  }, [me]);

  // Sign-in is drawn for the light theme only (fixed artwork and colours), so it keeps the light palette in either theme.
  if (path === "/login") return <div data-theme="light" className="contents">{children}</div>;
  if (!ready || !me) return <ShellSkeleton narrow={collapsed} />;
  if (me.must_change_password) return <ForcedPasswordChange />;
  if (isStandalone(path)) return <>{children}</>;
  // The platform Super Admin works in its own chrome (components/PlatformShell.tsx), drawn as the Organization Workspace
  // is, with the same collapsible sidebar setting.
  if (personaOf(me) === "platform") {
    return (
      <SyntheticCtx.Provider value={setSynthetic}>
        <PlatformFrame path={path} synthetic={synthetic} collapsed={collapsed} onToggle={toggle}>{children}</PlatformFrame>
      </SyntheticCtx.Provider>
    );
  }

  // Customer-organisation users work in the Organization Workspace chrome (dark sidebar, ORG_NAV); Zoiko staff keep theirs.
  // The organisation sidebar is navy in both themes, so it keeps the palette it was designed with (the light theme's).
  const org = personaOf(me) === "tenant";
  return (
    <SyntheticCtx.Provider value={setSynthetic}>
      <div className={`min-h-screen ${org ? "bg-slate-100 dark:bg-canvas" : ""}`}>
        <a href="#main" className="sr-only z-[80] rounded-md bg-navy-700 px-3 py-2 text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
        {/* In the dark theme the sidebar takes the darkest navy, so it sits with the dark canvas instead of above it. */}
        <aside aria-label="Primary navigation" data-theme={org ? "light" : undefined}
          className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r transition-[width] duration-200 ease-standard lg:flex
            ${org ? "border-navy-950 bg-navy-900 dark:border-white/[0.06] dark:bg-navy-950" : "border-slate-200 bg-white"} ${collapsed ? "w-16" : org ? "w-[240px]" : "w-[232px]"}`}>
          {org ? <OrgSidebar narrow={collapsed} path={path} onToggle={toggle} entities={work?.entities} />
            : <SidebarBody narrow={collapsed} path={path} onToggle={toggle} />}
        </aside>
        <Drawer open={mobile} onClose={() => setMobile(false)} title="Navigation" side="left" width="max-w-[272px]">
          <div data-theme={org ? "light" : undefined} className={`-m-4 flex h-[calc(100vh-49px)] flex-col ${org ? "bg-navy-900 dark:bg-navy-950" : ""}`}>
            {org ? <OrgSidebar narrow={false} path={path} entities={work?.entities} /> : <SidebarBody narrow={false} path={path} showContext />}
          </div>
        </Drawer>
        <div className={`flex min-h-screen flex-col transition-[padding] duration-200 ease-standard ${collapsed ? "lg:pl-16" : org ? "lg:pl-[240px]" : "lg:pl-[232px]"}`}>
          <Header path={path} org={org} synthetic={synthetic} alerts={work} onMenu={() => setMobile(true)} />
          <main ref={mainRef} id="main" key={me.actor_id} className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        </div>
      </div>
    </SyntheticCtx.Provider>
  );
}

/** Shown while a full page load checks the session: the shell's frame (sidebar, header, page area) appears at once in
 *  the same geometry as the real shell instead of a blank screen. It holds no identity, role, organisation or
 *  navigation data; those render only after the server has confirmed the session. */
function ShellSkeleton({ narrow }: { narrow: boolean }) {
  return (
    <div className="min-h-screen">
      <p role="status" className="sr-only">Checking your session…</p>
      <aside aria-hidden className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-slate-200 bg-white lg:flex ${narrow ? "w-16" : "w-[232px]"}`}>
        <div className={`flex h-14 shrink-0 items-center border-b border-slate-200 ${narrow ? "justify-center" : "pl-4 pr-2"}`}><Logo mark={narrow} /></div>
        <div className={`space-y-2.5 py-4 ${narrow ? "px-3" : "px-5"}`}>
          {Array.from({ length: 9 }, (_, i) => <div key={i} className="skeleton h-4" style={{ width: narrow ? "100%" : `${84 - (i % 3) * 16}%` }} />)}
        </div>
      </aside>
      <div aria-hidden className={`flex min-h-screen flex-col ${narrow ? "lg:pl-16" : "lg:pl-[232px]"}`}>
        <div className="h-14 shrink-0 border-b border-slate-200 bg-white/95" />
        <div className="mx-auto w-full max-w-[1440px] flex-1 space-y-3 px-4 py-5 sm:px-6">
          <div className="skeleton h-6 w-64" />
          <div className="skeleton h-3.5 w-[min(36rem,90%)]" />
          <div className="skeleton mt-4 h-40 w-full" />
        </div>
      </div>
    </div>
  );
}

export function Logo({ mark = false, width = 140, onDark }: { mark?: boolean; width?: number; onDark?: boolean }) {
  // logo.png is a 1000px square with the wordmark centred; logo-mark.jpg is a 1000px square with the app icon centred.
  // logo-on-dark.png is logo.png with the navy recoloured white, for dark surfaces; same geometry.
  // `width` scales the wordmark crop proportionally (140px is the sidebar size). Without `onDark` the wordmark follows
  // the theme (--za-logo-wordmark, app/theme.css); `onDark` true or false pins one file.
  const s = width / 140;
  const file = onDark === undefined ? "var(--za-logo-wordmark)" : `url(${onDark ? "/logo-on-dark.png" : "/logo.png"})`;
  return mark ? (
    <span role="img" aria-label="Zoiko Assure" className="block h-8 w-8"
      style={{ backgroundImage: "url(/logo-mark.jpg)", backgroundSize: "90px 90px", backgroundPosition: "-29px -29px", backgroundRepeat: "no-repeat" }} />
  ) : (
    <span role="img" aria-label="Zoiko Assure" className="block"
      style={{ width, height: 43 * s, backgroundImage: file, backgroundSize: `${228 * s}px ${228 * s}px`,
        backgroundPosition: `${-44 * s}px ${-91 * s}px`, backgroundRepeat: "no-repeat" }} />
  );
}

/** Where the signed-in person is working, kept visually separate from who they are (the account menu). Zoiko staff
 *  belong to no customer organisation, so they see their own workspace here, never a customer's name. */
function WorkContext() {
  const { me } = useAuth();
  const c = workContext(me);
  const Icon = c.kind === "organisation" ? Building : ShieldCheck;
  const title = c.kind === "organisation" ? `Organisation ${c.name} (${c.id}). Records on every page belong to this organisation.`
    : c.kind === "platform" ? "Zoiko platform administration. You belong to no customer organisation and see registry metadata only."
    : "Zoiko content operations. You belong to no customer organisation and have no access to customer data.";
  return (
    <span data-za="org-context" title={title} className="flex min-w-0 items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1">
      <Icon size={14} aria-hidden className="shrink-0 text-slate-400" />
      <span className="min-w-0 leading-tight">
        <span className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">{c.label}</span>
        <span className="block truncate text-[12px] font-medium text-slate-800">{c.name}</span>
      </span>
    </span>
  );
}

/** A sidebar item's icon, swapped for a spinner while that item's navigation is pending, so the click is acknowledged
 *  at once even when the next page takes a moment (prefetched routes in a production build skip the pending state). */
export function NavIcon({ icon: Icon, active, dark = false, activeClassName, idleClassName }:
  { icon: LucideIcon; active: boolean; dark?: boolean; activeClassName?: string; idleClassName?: string }) {
  const { pending } = useLinkStatus();
  return pending ? (
    <>
      <Loader2 size={16} aria-hidden className={`shrink-0 ${activeClassName ?? (dark ? "text-teal-300" : "text-navy-600")} motion-safe:animate-spin`} />
      <span className="sr-only">Loading</span>
    </>
  ) : <Icon size={16} aria-hidden className={`shrink-0 ${active ? activeClassName ?? (dark ? "text-teal-300" : "text-navy-700") : idleClassName ?? (dark ? "text-slate-400" : "text-slate-500")}`} />;
}

/** The organisation sidebar's labels have no icons, so a pending navigation shows a spinner after the label instead. */
export function NavPending() {
  const { pending } = useLinkStatus();
  return pending ? (
    <>
      <Loader2 size={14} aria-hidden className="ml-auto shrink-0 text-teal-300 motion-safe:animate-spin" />
      <span className="sr-only">Loading</span>
    </>
  ) : null;
}

/** Account menu: the organisation this account belongs to (or, for Zoiko staff, that it belongs to none). */
function AccountContext() {
  const { me } = useAuth();
  const c = workContext(me);
  return (
    <div className="border-b border-slate-200 px-3 py-2.5 text-[12px]">
      <div className="text-[11px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-500">{c.kind === "organisation" ? "Member of organisation" : c.label}</div>
      <div className="mt-0.5 font-medium leading-5 text-slate-800">{c.name}</div>
      {c.kind !== "organisation" && <div className="text-[11px] text-slate-500">Not a member of any customer organisation</div>}
    </div>
  );
}

function SidebarBody({ narrow, path, onToggle, showContext }: { narrow: boolean; path: string; onToggle?: () => void; showContext?: boolean }) {
  const { me, logout } = useAuth();
  const activeHref = routeMeta(path)?.item.href;
  // Keep the current page's item visible in the scrollable sidebar.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest" }); }, [activeHref]);
  return (
    <>
      <div className={`flex h-14 shrink-0 items-center border-b border-slate-200 ${narrow ? "justify-center" : "justify-between pl-4 pr-2"}`}>
        <Link href="/" aria-label="Dashboard" className="rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400">
          <Logo mark={narrow} />
        </Link>
        {onToggle && !narrow && <IconButton label="Collapse sidebar" icon={PanelLeftClose} onClick={onToggle} />}
      </div>
      {onToggle && narrow && <div className="flex justify-center pt-2"><IconButton label="Expand sidebar" icon={PanelLeftOpen} onClick={onToggle} /></div>}
      {showContext && <div className="flex shrink-0 border-b border-slate-200 px-2.5 py-2 [&>span]:w-full"><WorkContext /></div>}

      <nav ref={navRef} className={`flex-1 overflow-y-auto overflow-x-hidden py-2 ${narrow ? "no-scrollbar px-2" : "scroll-thin px-2.5"}`}>
        {navFor(personaOf(me), me?.permissions).map((g, gi) => {
          const headingId = `nav-group-${gi}`;
          return (
          <div key={g.group} role="group" aria-labelledby={narrow ? undefined : headingId} aria-label={narrow ? g.group : undefined} className="mb-2">
            {narrow ? (gi > 0 && <div className="mx-2 my-2 border-t border-slate-100" />) : (
              <div id={headingId} className="px-2.5 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">{g.group}</div>
            )}
            {g.items.map(({ href, label, icon: Icon }) => {
              const active = activeHref === href;
              const link = (
                <Link href={href} aria-current={active ? "page" : undefined} aria-label={narrow ? label : undefined}
                  className={`relative flex items-center gap-2.5 rounded-md text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400
                    ${narrow ? "h-9 w-10 justify-center" : "h-8 px-2.5"}
                    ${active ? "bg-navy-50 font-medium text-navy-800" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                  {active && !narrow && <span aria-hidden className="absolute left-0 top-1.5 h-5 w-0.5 rounded-full bg-navy-700" />}
                  <NavIcon icon={Icon} active={active} />
                  {!narrow && <span className="truncate">{label}</span>}
                </Link>
              );
              return <div key={href} className="mb-px">{narrow ? <Tooltip content={label} side="right">{link}</Tooltip> : link}</div>;
            })}
          </div>
          );
        })}
      </nav>

      <div className="shrink-0 border-t border-slate-200 p-2.5">
        {narrow ? (
          <div className="flex flex-col items-center gap-1.5">
            <span title={`${me?.display_name ?? ""} · ${rolesText(me)}`} className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
            <IconButton label="Sign out" icon={LogOut} onClick={logout} />
          </div>
        ) : (
          <div className="flex items-center gap-2.5 px-1">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-slate-900">{shortName(me?.display_name)}</div>
              <div title={rolesText(me)} className="truncate text-[11px] text-slate-500">{rolesText(me)}</div>
            </div>
            <IconButton label="Sign out" icon={LogOut} onClick={logout} />
          </div>
        )}
      </div>
    </>
  );
}

export const ORG_FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300";
/** Focus ring on the organisation sidebar's navy: white, as the sidebar's text is (ORG_FOCUS stays the Super Admin's). */
const ORG_NAV_FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/75";

/** The Organization Workspace sidebar: dark navy, grouped as orgNav (components/nav.ts). Every destination is an entry
 *  of its own; screens that were sub-entries are tabs of their consolidated page. The entry a page belongs to (record
 *  pages and former addresses included) is highlighted. Collapsed, every entry is an icon with a tooltip. The footer
 *  names the organisation and how many legal entities the server returned for it. */
function OrgSidebar({ narrow, path, onToggle, entities }: { narrow: boolean; path: string; onToggle?: () => void; entities?: number | null }) {
  const { me } = useAuth();
  const current = orgRouteMeta(path)?.item.href;
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest" }); }, [current]);
  // null: this person's roles do not read the organisation record (e.g. a security administrator or external auditor).
  const unreadable = entities === null || (entities === undefined && !mayRead(SHELL.entity_scope, me?.permissions));
  const scope = unreadable ? "policy checked" : entities === undefined ? null : `${entities} ${entities === 1 ? "entity" : "entities"} • policy checked`;
  const scopeTitle = `Organization ${me?.tenant_name ?? ""}. The server authorizes every request against your roles and this organization's boundary; ${
    unreadable ? "your roles do not include reading its legal entities." : scope ? `it returned ${entities} legal ${entities === 1 ? "entity" : "entities"}.` : "entities are loading."}`;

  // Every entry carries its icon; collapsed, the icon alone with the label as a tooltip. The open entry is a lighter
  // row with white text and a slim brand-orange marker on the sidebar's edge (the brand's controlled accent).
  const link = (href: string, label: string, icon: LucideIcon) => {
    const here = href === current;
    const el = (
      <Link href={href} aria-current={here ? "page" : undefined} aria-label={narrow ? label : undefined}
        className={`group relative flex items-center rounded-md transition-colors duration-150 ${ORG_NAV_FOCUS} ${narrow ? "h-9 w-10 justify-center" : "h-9 gap-3 px-3 text-[14px] leading-5"}
          ${here ? "bg-white/[0.09] font-medium text-white" : "text-slate-300 hover:bg-white/[0.05] hover:text-white"}`}>
        {here && <span aria-hidden className="absolute -left-3 inset-y-1.5 w-[3px] rounded-r-full bg-brand-400" />}
        <NavIcon icon={icon} active={here} dark activeClassName="text-white" idleClassName="text-slate-400 transition-colors duration-150 group-hover:text-slate-200" />
        {!narrow && <span className="za-fade-in truncate">{label}</span>}
      </Link>
    );
    return narrow ? <Tooltip content={label} side="right">{el}</Tooltip> : el;
  };
  const toggleButton = onToggle && (
    <button type="button" onClick={onToggle} aria-label={narrow ? "Expand sidebar" : "Collapse sidebar"} title={narrow ? "Expand sidebar" : "Collapse sidebar"}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors duration-150 hover:bg-white/10 hover:text-white ${ORG_NAV_FOCUS}`}>
      {narrow ? <PanelLeftOpen size={16} aria-hidden /> : <PanelLeftClose size={16} aria-hidden />}
    </button>
  );

  return (
    <>
      {narrow ? (
        <div className="flex shrink-0 flex-col items-center gap-2 pb-1 pt-4">
          <Link href="/" aria-label="Overview" className={`rounded ${ORG_NAV_FOCUS}`}><Logo mark width={110} onDark /></Link>
          {toggleButton}
        </div>
      ) : (
        <div className="shrink-0 px-5 pb-1">
          <div className="flex h-16 items-center justify-between gap-2">
            <Link href="/" aria-label="Overview" className={`rounded ${ORG_NAV_FOCUS}`}><Logo width={110} onDark /></Link>
            <span className="-mr-2">{toggleButton}</span>
          </div>
          <p className="za-fade-in -mt-1 text-[12px] font-medium leading-4 text-slate-400">Organization workspace</p>
        </div>
      )}

      <nav ref={navRef} className={`flex-1 overflow-y-auto overflow-x-hidden px-3 pb-4 ${narrow ? "no-scrollbar pt-2" : "scroll-thin pt-1"}`}>
        {orgNav(me?.permissions).map((g, gi) => {
          const headingId = `org-nav-group-${gi}`;
          return (
            <div key={g.group} role="group" aria-labelledby={narrow ? undefined : headingId} aria-label={narrow ? g.group : undefined} className={narrow ? "" : "pt-5"}>
              {narrow ? (gi > 0 && <div className="mx-1 my-2.5 border-t border-white/10" />) : (
                <div id={headingId} className="za-fade-in px-3 pb-1.5 text-[11px] font-semibold uppercase leading-4 tracking-[0.08em] text-slate-400">{g.group}</div>
              )}
              <div className="space-y-0.5">
                {g.items.map((item) => <div key={item.href}>{link(item.href, item.label, item.icon)}</div>)}
              </div>
            </div>
          );
        })}
      </nav>

      <div className={`shrink-0 border-t border-white/10 ${narrow ? "flex justify-center py-3" : "px-3 py-3"}`}>
        {narrow ? (
          <span title={scopeTitle} className="flex h-9 w-9 items-center justify-center rounded-md bg-white/[0.06] text-slate-300">
            <Building size={15} aria-hidden /><span className="sr-only">{scopeTitle}</span>
          </span>
        ) : (
          <div title={scopeTitle} className="za-fade-in flex min-w-0 items-start gap-3 px-2 py-1">
            <span aria-hidden className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/[0.07] text-slate-300"><Building size={15} /></span>
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-400">Entity scope</div>
              <div className="mt-0.5 break-words text-[13px] font-medium leading-5 text-slate-100">{me?.tenant_name}</div>
              <div className="text-[12px] leading-4 text-slate-400">{scope ?? "Checking entity scope…"}</div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/** Organisation header breadcrumb ("Zoiko Assure / Organization"), also used by the Super Admin header. Below xl only
 *  the current page is shown. */
export function OrgBreadcrumb({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-slate-500">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${c.label}-${i}`} className={`min-w-0 items-center gap-2 ${last ? "flex" : "hidden shrink-0 xl:flex"}`}>
              {i > 0 && <span aria-hidden className={`text-slate-300 ${last ? "hidden xl:inline" : ""}`}>/</span>}
              {last ? <span aria-current="page" className="truncate font-medium text-slate-900">{c.label}</span>
                : c.href ? <Link href={c.href} className="rounded transition-colors hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400">{c.label}</Link>
                : <span>{c.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function Header({ path, org, synthetic, alerts, onMenu }: { path: string; org: boolean; synthetic: boolean; alerts: OpenWork | null; onMenu: () => void }) {
  const { me, logout } = useAuth();
  const router = useRouter();
  const { title } = usePageTitle();
  const [wm, setWm] = useState<{ cell_id: string; record_seq: number } | null>(null);
  const [q, setQ] = useState("");
  const [pw, setPw] = useState(false);
  const [mac, setMac] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  // The record watermark, polled only for someone the server will serve it to (lib/screen-access.json `shell`); for
  // anyone else every poll would be a refusal recorded in the audit trail.
  const watermarkReadable = mayRead(SHELL.watermark, me?.permissions);
  useEffect(() => {
    setWm(null);
    if (!watermarkReadable) return;
    const t = () => api("/watermark").then(setWm).catch(() => setWm(null));
    t();
    const h = setInterval(t, 10000);
    return () => clearInterval(h);
  }, [watermarkReadable]);
  // Organisation header: "/" (when not typing) or ⌘K / Ctrl+K moves focus to the search, unless a dialog is open.
  useEffect(() => {
    if (!org) return;
    setMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
      const combo = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      const slash = e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey;
      const input = searchRef.current;
      if (!(combo || slash) || !input?.offsetParent || document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      input.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [org]);

  const meta = routeMeta(path);
  const crumbs = meta
    ? [...(meta.group !== meta.item.label ? [{ label: meta.group }] : []), { label: meta.item.label, href: meta.item.href },
      ...(meta.isDetail && title ? [{ label: title }] : [])]
    : [{ label: "Zoiko Assure" }];
  // Organisation users' screens are named and grouped as their sidebar shows them (a group named like its only entry is
  // not repeated); a screen that sidebar does not offer keeps its NAV name.
  const om = org ? orgRouteMeta(path) ?? meta : null;
  const orgCrumbs = path === "/" ? [{ label: "Zoiko Assure" }, { label: "Organization" }]
    : [{ label: "Zoiko Assure", href: "/" }, ...(om && om.group !== om.item.label ? [{ label: om.group }] : []),
      ...(om ? [{ label: om.item.label, href: om.item.href }] : []), ...(om?.isDetail && title ? [{ label: title }] : [])];
  // Organisation users search their sidebar's entries and tabs, by current and former names; staff search NAV.
  const hits = useMemo(() => {
    if (!q) return [];
    const targets: { href: string; label: string; group: string; tab?: string; terms: string }[] = org ? orgSearchTargets(me?.permissions)
      : jumpTargets(personaOf(me), me?.permissions).map((i) => ({ ...i, terms: `${i.label} ${i.group}` }));
    return targets.filter((i) => i.terms.toLowerCase().includes(q.toLowerCase())).slice(0, 7);
  }, [q, me, org]);
  const pending = (alerts?.issues ?? 0) + (alerts?.facts ?? 0);
  // Record watermark: always visible ("derived projection watermark is always visible", Vol. 1 NFRs). Phones show it
  // under the breadcrumb, wider screens beside the account controls; the cell is added where space allows.
  const watermark = (placement: string) => wm && (
    <span title={`Data cell ${wm.cell_id}. Views reflect everything recorded up to sequence ${wm.record_seq} (knowledge time).`}
      className={`${placement} shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600`}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500" /><span className="mono"><span className="hidden 2xl:inline">{wm.cell_id} · </span>seq {wm.record_seq}</span>
    </span>
  );

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && hits[0]) { router.push(hits[0].href); setQ(""); }
    if (e.key === "Escape") setQ("");
  };
  const hitList = hits.length > 0 && (
    <ul aria-label="Matching pages" className="za-pop-in absolute right-0 z-40 mt-2 w-80 origin-top-right overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
      {hits.map(({ href, label, group, tab }) => (
        <li key={href}>
          <Link href={href} onClick={() => setQ("")} className="flex items-center justify-between gap-3 px-3 py-2 text-[13px] leading-5 text-slate-700 transition-colors duration-100 hover:bg-slate-50 hover:text-slate-900 focus-visible:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400">
            {tab ? <span className="min-w-0">{label}<span className="text-slate-500"> › {tab}</span></span> : label}<span className="shrink-0 text-[11px] text-slate-500">{group}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
  const alertItems = (close: () => void) => (
    <>
      <div className="border-b border-slate-200 px-3 py-2.5 text-[11px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-500">Needs attention</div>
      {!me?.tenant_id ? <p className="px-3 py-3 text-[13px] text-slate-500">Notifications cover customer-organisation work, so Zoiko staff accounts have none.</p>
        : !alerts ? <p className="px-3 py-3 text-[13px] text-slate-500">No notifications.</p>
        : pending === 0 ? <p className="px-3 py-3 text-[13px] text-slate-500">Nothing open right now.</p> : (
          <>
            {alerts.issues > 0 && <DropdownItem href="/governance" onClick={close}>{alerts.issues} open issue(s)</DropdownItem>}
            {alerts.facts > 0 && <DropdownItem href="/fact-requests" onClick={close}>{alerts.facts} open fact request(s)</DropdownItem>}
          </>
        )}
    </>
  );
  const dot = pending > 0 && <span className={`absolute h-2 w-2 rounded-full bg-brand-500 ring-2 ring-white ${org ? "right-2 top-2" : "right-1 top-1"}`} aria-hidden />;
  const themeToggle = <ThemeToggle className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400" />;
  const accountMenu = (
    <Dropdown label="Account menu" menuClassName="w-72"
      buttonClassName="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 transition-colors duration-150 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 aria-expanded:bg-slate-100"
      button={<>
        <span className={`flex items-center justify-center rounded-full bg-navy-700 font-semibold text-white ${org ? "h-8 w-8 text-[12px]" : "h-7 w-7 text-[11px]"}`}>{initials(me?.display_name)}</span>
        <span className={`hidden text-left ${org ? "2xl:block" : "sm:block"}`}>
          <span className="block max-w-[160px] truncate text-[12px] font-medium text-slate-900">{shortName(me?.display_name)}</span>
          <span className="block max-w-[160px] truncate text-[11px] text-slate-500">{me?.roles[0] ? roleLabel(me.roles[0]) : ""}</span>
        </span>
        <ChevronDown size={14} aria-hidden className="text-slate-400" />
      </>}>
      {(close) => (
        <>
          <div className="border-b border-slate-200 px-3 py-3">
            <div className="text-[13px] font-medium leading-5 text-slate-900">{me?.display_name}</div>
            <div className="text-[12px] leading-4 text-slate-500">{me?.email}</div>
            <ul className="mt-1.5 space-y-0.5" aria-label="Your roles">
              {me?.roles.map((r) => <li key={r} className="flex items-center justify-between gap-2 text-[12px] text-slate-700">{roleLabel(r)}<span className="mono text-[11px] text-slate-500">{r}</span></li>)}
            </ul>
          </div>
          <AccountContext />
          <DropdownItem icon={UserCog} href="/admin/users" onClick={close}>My access & permissions</DropdownItem>
          <DropdownItem icon={Palette} href="/design-system" onClick={close}>Design system &amp; UI kit</DropdownItem>
          <div className="my-1 border-t border-slate-200" />
          <DropdownItem icon={LogOut} danger onClick={logout}>Sign out</DropdownItem>
        </>
      )}
    </Dropdown>
  );

  // Organization Workspace header: breadcrumb and synthetic-data badge, then search, Alerts, the theme toggle and the
  // account menu. The search jumps to the screens this account may open (navigation, not record search), so it says
  // "pages". It does not show the record watermark. Phones put the badge on the second line so the breadcrumb keeps its room.
  if (org) {
    const badge = (placement: string) => synthetic && (
      <span title="This page includes synthetic demonstration figures, each marked SYNTHETIC. Unmarked figures are read from your organization's records."
        className={`${placement} shrink-0 items-center whitespace-nowrap rounded-full border border-slate-200 bg-slate-100 px-2.5 py-[3px] text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-600 md:text-[11px]`}>
        Synthetic<span className="hidden lg:inline">&nbsp;demo data</span>
      </span>
    );
    // Header controls share one size (36px) and one quiet treatment: icon, hover well, focus ring.
    const control = "relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 aria-expanded:bg-slate-100 aria-expanded:text-slate-900";
    const kbdCls = "rounded border border-slate-200 bg-slate-50 px-1.5 font-sans text-[11px] font-medium leading-[18px] text-slate-500";
    return (
      <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4 sm:px-6 lg:px-8">
        <IconButton label="Open navigation" icon={Menu} onClick={onMenu} className="-ml-1 lg:hidden" />
        <div className="min-w-0 flex-1 overflow-x-clip">
          <div className="flex min-w-0 items-center gap-3"><OrgBreadcrumb items={orgCrumbs} />{badge("hidden md:inline-flex")}</div>
          <div className="mt-0.5 flex items-center gap-2 md:hidden">{badge("inline-flex")}</div>
        </div>

        <div className="flex min-w-0 items-center gap-1">
          <div className="relative mr-1 hidden shrink-0 md:block">
            <Search size={15} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input ref={searchRef} type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onSearchKey}
              placeholder="Search authorized pages" aria-label="Search authorized pages" aria-keyshortcuts="/ Control+K Meta+K"
              className={`h-9 w-56 rounded-md border border-line-control bg-white pl-9 pr-3 text-[13px] text-slate-900 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-[border-color,box-shadow] duration-150 placeholder:text-slate-500 hover:border-slate-500 focus:border-navy-500 focus:outline-none focus:ring-2 focus:ring-navy-100 xl:w-72 ${q ? "" : "xl:pr-[5.5rem]"}`} />
            {!q && (
              <span aria-hidden className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 items-center gap-1 xl:flex">
                <kbd className={kbdCls}>/</kbd><kbd className={kbdCls}>{mac ? "⌘K" : "Ctrl K"}</kbd>
              </span>
            )}
            {hitList}
          </div>

          <Dropdown label={pending ? `Alerts, ${pending} open item(s)` : "Alerts"} menuClassName="w-72"
            buttonClassName={control} button={<><Bell size={17} aria-hidden />{dot}</>}>
            {alertItems}
          </Dropdown>

          <ThemeToggle className={control} iconSize={17} />
          <span aria-hidden className="mx-1.5 hidden h-6 w-px bg-slate-200 sm:block" />
          {accountMenu}
        </div>
        {pw && <ChangePassword onClose={() => setPw(false)} />}
      </header>
    );
  }

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
      <IconButton label="Open navigation" icon={Menu} onClick={onMenu} className="lg:hidden" />
      <div className="min-w-0 flex-1 overflow-x-clip"><Breadcrumb items={crumbs} />{watermark("mt-0.5 flex w-fit md:hidden")}</div>

      <div className="flex min-w-0 items-center gap-2">
        <div className="relative hidden shrink-0 md:block">
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to…" aria-label="Jump to page" inputClassName="w-52 py-1"
            onKeyDown={onSearchKey} />
          {hitList}
        </div>

        <div className="hidden min-w-0 max-w-[240px] md:flex"><WorkContext /></div>

        {watermark("hidden md:flex")}
        <Dropdown label={pending ? `Notifications, ${pending} open item(s)` : "Notifications"} menuClassName="w-72"
          buttonClassName="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
          button={<><Bell size={16} aria-hidden />{dot}</>}>
          {alertItems}
        </Dropdown>

        {themeToggle}
        {accountMenu}
      </div>
      {pw && <ChangePassword onClose={() => setPw(false)} />}
    </header>
  );
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const [done, setDone] = useState(false);
  const toast = useToast();
  return (
    <Modal open onClose={onClose} title="Change password" size="sm">
      {done ? <p className="text-[13px] text-emerald-800">Password changed. Your other sessions have been signed out.</p>
        : <PasswordForm onCancel={onClose} onChanged={async () => { toast("Password changed", "good"); setDone(true); }} />}
    </Modal>
  );
}

/** Shown instead of the app while the account still has a temporary password; the backend refuses every other call. */
function ForcedPasswordChange() {
  const { me, refresh, logout } = useAuth();
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex justify-center"><Logo /></div>
        <div className="card p-5">
          <h1 className="text-base font-semibold text-slate-900">Set your own password</h1>
          <p className="mb-4 mt-1 text-[13px] text-slate-600">{shortName(me?.display_name)}, you signed in with a temporary password. Choose a new one to continue.</p>
          <PasswordForm currentLabel="Temporary password" submitLabel="Set password and continue" onChanged={refresh} />
          <button type="button" className="btn-ghost mt-3 w-full justify-center" onClick={logout}>Sign out</button>
        </div>
      </div>
    </main>
  );
}

function PasswordForm({ onChanged, onCancel, submitLabel = "Update password", currentLabel = "Current password" }:
  { onChanged: () => Promise<void>; onCancel?: () => void; submitLabel?: string; currentLabel?: string }) {
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [conf, setConf] = useState("");
  const mismatch = !!conf && conf !== nw;
  const a = useAction();
  return (
    <>
      <ErrorBox error={a.error} />
      <form className="grid gap-3" onSubmit={(e) => {
        e.preventDefault();
        a.run(async () => { const r = await api("/auth/password", { method: "POST", json: { current_password: cur, new_password: nw } }); await onChanged(); return r; });
      }}>
        <Field label={currentLabel} htmlFor="pw-cur"><input id="pw-cur" type="password" autoComplete="current-password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password" htmlFor="pw-new" hint="At least 10 characters, with upper- and lower-case letters and a digit.">
          <input id="pw-new" type="password" autoComplete="new-password" aria-describedby="pw-new-hint" className="input" value={nw} onChange={(e) => setNw(e.target.value)} />
        </Field>
        <Field label="Confirm new password" htmlFor="pw-conf" hint={mismatch ? <span className="text-red-700">Passwords don&apos;t match.</span> : undefined}>
          <input id="pw-conf" type="password" autoComplete="new-password" aria-invalid={mismatch} aria-describedby={mismatch ? "pw-conf-hint" : undefined}
            className="input" value={conf} onChange={(e) => setConf(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          {onCancel && <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>}
          <button className="btn-primary" disabled={!cur || !nw || nw !== conf || a.busy}>{submitLabel}</button>
        </div>
      </form>
    </>
  );
}
