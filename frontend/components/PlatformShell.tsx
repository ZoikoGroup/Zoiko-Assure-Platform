"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowUpFromLine, Bell, Building2, ChevronDown, ClipboardCheck, Database, FileCheck2, FileCog, FileText, KeyRound, LayoutGrid, LogOut, Menu,
  Palette, PanelLeftClose, PanelLeftOpen, ScrollText, Server, ShieldCheck, UserCog,
} from "lucide-react";
import { useAuth } from "@/lib/actor";
import { roleLabel, rolesText, workContext } from "@/lib/roles";
import { PLANES } from "./ds/Plane";
import { jumpTargets, navFor, routeMeta } from "./nav";
import { Logo, NavIcon, NavPending, ORG_FOCUS, OrgBreadcrumb } from "./Shell";
import { ThemeToggle } from "./ThemeToggle";
import { Drawer, Dropdown, DropdownItem, IconButton, Tooltip, usePageTitle } from "./ui";

/* The chrome for the Zoiko Super Admin (persona "platform"), drawn as the Organization Workspace chrome is
   (components/Shell.tsx), so both belong to one product: the navy sidebar (with the same collapse setting), the white
   header with the breadcrumb, page search, alerts, theme toggle and account menu, over the slate page. The organisation
   chrome's own breadcrumb, navigation icon, pending spinner and focus ring are reused. The frame carries
   data-za-chrome="workspace", which gives the foundation tokens that the Super Admin screens and the design-system
   components are built on the Organization Workspace values (app/design-tokens.css). Customer-organisation users and
   content staff keep their own chrome (components/Shell.tsx).

   The navigation follows the Platform Command Center reference. An area with no Super Admin screen yet is listed as
   planned, not as a link: its nearest existing screen belongs to customer organisations or content staff, and the
   server would refuse the Super Admin there. Platform screens from NAV that the reference does not name (Users & Roles,
   Identity Integrations) stay reachable under Administration, so no destination is lost. */

type Item = { label: string; icon: LucideIcon; href?: string };
type Group = { group: string; items: Item[] };

const PLATFORM_NAV: Group[] = [
  { group: "Overview", items: [
    { label: "Command Center", icon: LayoutGrid, href: "/" },
    { label: "Action Queue", icon: ClipboardCheck, href: "/action-queue" },
  ] },
  { group: "Governance", items: [
    { label: "Tenants & Entities", icon: Building2, href: "/organisations" },
    { label: "Tenant Metadata", icon: Database, href: "/tenant-metadata" },
    { label: "Source Integrity", icon: FileCheck2, href: "/source-integrity" },
    { label: "JIT Access", icon: KeyRound, href: "/jit-access" },
    { label: "Administrative Audit", icon: ScrollText, href: "/administrative-audit" },
    { label: "Regulatory Sources", icon: FileText },
    { label: "Evidence Integrity", icon: ShieldCheck },
    { label: "Agent Controls", icon: UserCog },
  ] },
  { group: "Operations", items: [
    { label: "Service Health", icon: Server, href: "/service-health" },
    { label: "Integrations & Jobs", icon: FileCog },
    { label: "Releases & Policies", icon: ArrowUpFromLine },
  ] },
];

/** The reference groups, then every other platform screen NAV offers this person, under Administration. */
function platformNav(permissions?: readonly string[]): Group[] {
  const listed = new Set(PLATFORM_NAV.flatMap((g) => g.items.flatMap((i) => (i.href ? [i.href] : []))));
  const rest = navFor("platform", permissions).flatMap((g) => g.items).filter((i) => !listed.has(i.href));
  return rest.length ? [...PLATFORM_NAV, { group: "Administration", items: rest.map(({ href, label, icon }) => ({ href, label, icon })) }] : PLATFORM_NAV;
}

type PageTarget = { href: string; label: string; group: string; terms: string };

/** The pages the header search can open: every linked sidebar entry, under the sidebar's own name and group (so the
 *  platform screens that nav.ts does not list are found, and "/" is "Command Center"), then the other pages NAV offers
 *  (for example the design system). A page NAV also names differently still matches that name ("Dashboard" finds the
 *  Command Center). Planned sidebar entries have no page, so they are not offered. */
function pageTargets(permissions?: readonly string[]): PageTarget[] {
  const byHref = new Map<string, PageTarget>();
  for (const g of platformNav(permissions)) {
    for (const i of g.items) {
      if (i.href && !byHref.has(i.href)) byHref.set(i.href, { href: i.href, label: i.label, group: g.group, terms: `${i.label} ${g.group}` });
    }
  }
  for (const j of jumpTargets("platform", permissions)) {
    const known = byHref.get(j.href);
    if (known) known.terms += ` ${j.label} ${j.group}`;
    else byHref.set(j.href, { ...j, terms: `${j.label} ${j.group}` });
  }
  return [...byHref.values()];
}

/** The header breadcrumb, as the organisation header builds it: "Zoiko Assure / Super Admin" on the Command Center
 *  (the organisation's reads "Zoiko Assure / Organization"), elsewhere the sidebar's group and entry (a group named like
 *  its only entry is not repeated), and a record's title on its own page. */
function platformCrumbs(path: string, groups: Group[], title: string): { label: string; href?: string }[] {
  if (path === "/") return [{ label: "Zoiko Assure" }, { label: PLANES.platform.who }];
  const meta = routeMeta(path);
  const href = meta?.item.href ?? path;
  const group = groups.find((g) => g.items.some((i) => i.href === href));
  const item = group?.items.find((i) => i.href === href);
  const page = item ? { label: item.label, href } : meta ? { label: meta.item.label, href: meta.item.href } : null;
  return [{ label: "Zoiko Assure", href: "/" }, ...(group && group.group !== page?.label ? [{ label: group.group }] : []),
    ...(page ? [page] : []), ...(meta?.isDetail && title ? [{ label: title }] : [])];
}

// As in Shell.tsx: "Super Admin (Platform)" → "SA" / "Super Admin".
const initials = (n?: string) => (n ?? "?").replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
const shortName = (n?: string) => (n ?? "").replace(/\s*\(.*\)/, "");

// The page search's popup (matching pages, or the note that none match), as the organisation header's.
const POPUP = "absolute right-0 z-40 mt-1 w-72 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg";
const SCOPE_TITLE = "Zoiko platform administration. You belong to no customer organisation and see registry metadata only.";

export function PlatformFrame({ path, synthetic, collapsed = false, onToggle, children }:
  { path: string; synthetic: boolean; collapsed?: boolean; onToggle?: () => void; children: ReactNode }) {
  const { me } = useAuth();
  const [mobile, setMobile] = useState(false);
  useEffect(() => setMobile(false), [path]);
  // The sidebar is navy in both themes, so it keeps the palette it is drawn with (the light theme's), as the
  // organisation sidebar does.
  return (
    <div data-za-chrome="workspace" className="min-h-screen bg-slate-100 dark:bg-canvas">
      <a href="#main" className="sr-only z-[80] rounded-md bg-navy-700 px-3 py-2 text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
      <aside aria-label="Primary navigation" data-theme="light"
        className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-navy-950 bg-navy-900 transition-[width] duration-200 lg:flex ${collapsed ? "w-16" : "w-[220px]"}`}>
        <PlatformSidebar narrow={collapsed} path={path} onToggle={onToggle} />
      </aside>
      <Drawer open={mobile} onClose={() => setMobile(false)} title="Navigation" side="left" width="max-w-[260px]">
        <div data-theme="light" className="-m-4 flex h-[calc(100vh-49px)] flex-col bg-navy-900"><PlatformSidebar narrow={false} path={path} /></div>
      </Drawer>
      <div className={`flex min-h-screen flex-col transition-[padding] duration-200 ${collapsed ? "lg:pl-16" : "lg:pl-[220px]"}`}>
        <PlatformHeader path={path} synthetic={synthetic} onMenu={() => setMobile(true)} />
        <main id="main" key={me?.actor_id} className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-5 sm:px-6">{children}</main>
      </div>
    </div>
  );
}

/** The Super Admin sidebar, laid out as the organisation sidebar: the wordmark and collapse control, the plane it
 *  works in, the grouped navigation (labels; icons with tooltips when collapsed) and, at the foot, the scope it works
 *  in. The current page's entry, or the entry a record page belongs to, is highlighted. */
function PlatformSidebar({ narrow, path, onToggle }: { narrow: boolean; path: string; onToggle?: () => void }) {
  const { me } = useAuth();
  // Platform-only screens that NAV does not list (Service Health) are matched by their exact path.
  const active = routeMeta(path)?.item.href ?? path;
  const groups = useMemo(() => platformNav(me?.permissions), [me]);
  const c = workContext(me);
  // Keep the current page's item visible in the scrollable sidebar.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest" }); }, [active]);
  return (
    <>
      <div className={`shrink-0 ${narrow ? "flex flex-col items-center gap-2 pt-4" : "px-[18px] pt-6"}`}>
        <div className={narrow ? "contents" : "flex items-start justify-between gap-2"}>
          <Link href="/" aria-label="Zoiko Assure, Command Center" className={`rounded ${ORG_FOCUS}`}><Logo mark={narrow} width={110} onDark /></Link>
          {onToggle && (
            <button type="button" onClick={onToggle} aria-label={narrow ? "Expand sidebar" : "Collapse sidebar"} title={narrow ? "Expand sidebar" : "Collapse sidebar"}
              className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-white/10 hover:text-white ${narrow ? "" : "-mr-2 -mt-1"} ${ORG_FOCUS}`}>
              {narrow ? <PanelLeftOpen size={16} aria-hidden /> : <PanelLeftClose size={16} aria-hidden />}
            </button>
          )}
        </div>
        {!narrow && (
          <div className="mt-3">
            <div className="text-[10px] font-semibold uppercase tracking-[0.04em] text-slate-200">{PLANES.platform.label}</div>
            <div className="mt-1.5 text-[9px] font-medium uppercase tracking-[0.08em] text-slate-400">{PLANES.platform.who}</div>
          </div>
        )}
      </div>

      <nav ref={navRef} aria-label="Super Admin" className={`flex-1 overflow-y-auto overflow-x-hidden pb-4 ${narrow ? "no-scrollbar px-3 pt-2" : "scroll-thin px-[11px] pt-3"}`}>
        {groups.map((g, gi) => <NavGroup key={g.group} group={g} active={active} narrow={narrow} first={gi === 0} />)}
      </nav>

      <div className={`shrink-0 border-t border-white/10 ${narrow ? "flex justify-center py-3" : "px-[18px] py-3.5"}`}>
        {narrow ? (
          <span title={SCOPE_TITLE} className="flex h-8 w-8 items-center justify-center rounded-md bg-white/[0.06] text-slate-300">
            <ShieldCheck size={15} aria-hidden /><span className="sr-only">{SCOPE_TITLE}</span>
          </span>
        ) : (
          <div title={SCOPE_TITLE} className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{c.label}</div>
            <div className="mt-1 truncate text-[12px] font-medium text-slate-100">{c.name}</div>
            <div className="mt-0.5 text-[12px] text-slate-300">Registry metadata only</div>
          </div>
        )}
      </div>
    </>
  );
}

function NavGroup({ group, active, narrow, first }: { group: Group; active?: string; narrow: boolean; first: boolean }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={narrow ? undefined : id} aria-label={narrow ? group.group : undefined} className={narrow ? "" : "pt-6 first:pt-3"}>
      {narrow ? (!first && <div className="mx-1 my-2 border-t border-white/10" />) : (
        <div id={id} className="pb-2 text-[11px] font-semibold uppercase leading-4 tracking-widest text-slate-400">{group.group}</div>
      )}
      <ul>
        {group.items.map((item) => <li key={item.label} className="mb-px"><NavEntry item={item} here={!!item.href && item.href === active} narrow={narrow} /></li>)}
      </ul>
    </div>
  );
}

/** One sidebar entry: a link styled as the organisation sidebar's, or a planned area, which is not a link. */
function NavEntry({ item: { label, icon: Icon, href }, here, narrow }: { item: Item; here: boolean; narrow: boolean }) {
  const shape = narrow ? "h-9 w-10 justify-center" : "h-9 gap-2 px-3 text-[14px]";
  if (!href) {
    return (
      <span aria-disabled="true" title={`${narrow ? `${label}. ` : ""}Planned: there is no Super Admin screen for this area yet`}
        className={`flex cursor-not-allowed items-center rounded-md text-slate-400 ${shape}`}>
        {narrow ? <Icon size={16} aria-hidden /> : <span className="truncate">{label}</span>}
        <span className="sr-only">{narrow ? `${label} (planned)` : " (planned)"}</span>
      </span>
    );
  }
  const link = (
    <Link href={href} aria-current={here ? "page" : undefined} aria-label={narrow ? label : undefined}
      className={`flex items-center rounded-md transition-colors ${ORG_FOCUS} ${shape}
        ${here ? "bg-teal-400/15 font-medium text-teal-300" : "text-slate-200 hover:bg-white/[0.06] hover:text-white"}`}>
      {narrow ? <NavIcon icon={Icon} active={here} dark /> : <><span className="truncate">{label}</span><NavPending /></>}
    </Link>
  );
  return narrow ? <Tooltip content={label} side="right">{link}</Tooltip> : link;
}

/** The account menu in the header, as the organisation header's: who is signed in, their roles, and that they belong to
 *  no customer organisation. */
function AccountMenu() {
  const { me, logout } = useAuth();
  const c = workContext(me);
  return (
    <Dropdown label={`Account menu, ${shortName(me?.display_name)}`} menuClassName="w-72"
      buttonClassName="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
      button={<>
        <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
        <span className="hidden text-left 2xl:block" title={rolesText(me)}>
          <span className="block max-w-[160px] truncate text-[12px] font-medium text-slate-900">{shortName(me?.display_name)}</span>
          <span className="block max-w-[160px] truncate text-[11px] text-slate-500">{me?.roles[0] ? roleLabel(me.roles[0]) : ""}</span>
        </span>
        <ChevronDown size={14} aria-hidden className="text-slate-400" />
      </>}>
      {(close) => (
        <>
          <div className="border-b border-slate-100 px-3 py-2.5">
            <div className="text-[13px] font-medium text-slate-900">{me?.display_name}</div>
            <div className="break-all text-[12px] text-slate-500">{me?.email}</div>
            <ul className="mt-1.5 space-y-0.5" aria-label="Your roles">
              {me?.roles.map((r) => <li key={r} className="flex items-center justify-between gap-2 text-[12px] text-slate-700">{roleLabel(r)}<span className="mono text-[11px] text-slate-500">{r}</span></li>)}
            </ul>
          </div>
          <div className="border-b border-slate-100 px-3 py-2 text-[12px]">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">{c.label}</div>
            <div className="font-medium text-slate-800">{c.name}</div>
            <div className="text-[11px] text-slate-500">Not a member of any customer organisation</div>
          </div>
          <DropdownItem icon={UserCog} href="/admin/users" onClick={close}>My access & permissions</DropdownItem>
          <DropdownItem icon={Palette} href="/design-system" onClick={close}>Design system &amp; UI kit</DropdownItem>
          <div className="my-1 border-t border-slate-100" />
          <DropdownItem icon={LogOut} danger onClick={logout}>Sign out</DropdownItem>
        </>
      )}
    </Dropdown>
  );
}

function PlatformHeader({ path, synthetic, onMenu }: { path: string; synthetic: boolean; onMenu: () => void }) {
  const { me } = useAuth();
  const router = useRouter();
  const { title } = usePageTitle();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);   // the results popup, shown while the search has focus and text
  const [active, setActive] = useState(-1);  // the highlighted result (aria-activedescendant); -1 for none
  const [said, setSaid] = useState("");      // the polite result-count announcement
  const [mac, setMac] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  // "/" (when not typing) or ⌘K / Ctrl+K moves focus to the search, unless a dialog is open (as in the organisation header).
  useEffect(() => {
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
  }, []);

  const crumbs = useMemo(() => platformCrumbs(path, platformNav(me?.permissions), title), [path, me, title]);
  // Jumps to the screens this account may open (navigation, not record search), so it says "pages".
  const targets = useMemo(() => pageTargets(me?.permissions), [me]);
  const hits = useMemo(() => (q ? targets.filter((i) => i.terms.toLowerCase().includes(q.toLowerCase())).slice(0, 7) : []), [q, targets]);
  // A combobox with a list popup (WAI-ARIA APG): focus stays in the field and the highlighted result is referenced by
  // aria-activedescendant. The popup shows the matches, or says there are none.
  const shown = open && q !== "";
  const expanded = shown && hits.length > 0;
  const current = expanded && active < hits.length ? active : -1;
  const optionId = (i: number) => `${listId}-option-${i}`;
  const reset = () => { setQ(""); setOpen(false); setActive(-1); };

  // The number of matches is announced once typing pauses, and only when it changes, not on every keystroke.
  useEffect(() => {
    if (!shown) { setSaid(""); return; }
    const t = setTimeout(() => setSaid(hits.length ? `${hits.length} matching page${hits.length === 1 ? "" : "s"}` : "No matching pages"), 600);
    return () => clearTimeout(t);
  }, [shown, hits.length]);

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!hits.length) return;  // nothing to move through: the keys keep their usual caret behaviour
      e.preventDefault();
      const down = e.key === "ArrowDown";
      if (!expanded) { setOpen(true); setActive(e.altKey ? -1 : down ? 0 : hits.length - 1); return; }
      setActive(down ? (current + 1) % hits.length : current <= 0 ? hits.length - 1 : current - 1);
    } else if (e.key === "Enter") {
      // The highlighted result, or else the first match, as before.
      const target = hits[current >= 0 ? current : 0];
      if (target) { e.preventDefault(); reset(); router.push(target.href); }
    } else if (e.key === "Escape") {
      // The first Escape closes the popup and keeps the text; the next clears the text.
      if (shown) { e.preventDefault(); setOpen(false); setActive(-1); }
      else if (q) { e.preventDefault(); setQ(""); }
    }
  };

  // Phones put the badge on the second line so the breadcrumb keeps its room (as in the organisation header).
  const badge = (placement: string) => synthetic && (
    <span title="This page includes synthetic demonstration figures, each marked SYNTHETIC. Figures marked LIVE are read from this deployment."
      className={`${placement} shrink-0 items-center whitespace-nowrap rounded-full border border-slate-200 bg-slate-100 px-2.5 py-[3px] text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-slate-600 md:text-[11px]`}>
      Synthetic<span className="hidden lg:inline">&nbsp;demo data</span>
    </span>
  );

  return (
    <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4 sm:px-6">
      <IconButton label="Open navigation" icon={Menu} onClick={onMenu} className="lg:hidden" />
      <div className="min-w-0 flex-1 overflow-x-clip">
        <div className="flex min-w-0 items-center gap-3"><OrgBreadcrumb items={crumbs} />{badge("hidden md:inline-flex")}</div>
        <div className="mt-0.5 flex items-center gap-2 md:hidden">{badge("inline-flex")}</div>
      </div>

      <div className="flex min-w-0 items-center gap-1 sm:gap-2">
        <div className="relative hidden shrink-0 md:block">
          <input ref={searchRef} type="search" value={q} onKeyDown={onSearchKey}
            onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(-1); }}
            onFocus={() => setOpen(true)} onBlur={() => { setOpen(false); setActive(-1); }}
            role="combobox" aria-autocomplete="list" aria-expanded={expanded} aria-controls={listId}
            aria-activedescendant={current >= 0 ? optionId(current) : undefined} autoComplete="off"
            placeholder="Search pages…" aria-label="Search Super Admin pages" aria-keyshortcuts="/ Control+K Meta+K"
            className={`w-52 rounded-md border border-transparent bg-transparent py-1.5 pl-2.5 pr-2.5 text-[14px] text-slate-900 transition-colors placeholder:text-slate-500 hover:border-slate-200 focus:border-navy-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-navy-100 xl:w-72 ${q ? "" : "xl:pr-16"}`} />
          {!q && <kbd aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 font-sans text-[13px] text-slate-500 xl:block">/ {mac ? "⌘K" : "Ctrl K"}</kbd>}
          {/* Pressing on a result must not blur the field first (which would close the popup before the click lands). */}
          <div id={listId} role="listbox" aria-label="Matching pages" hidden={!expanded} onMouseDown={(e) => e.preventDefault()}
            className={`${POPUP} py-1`}>
            {expanded && hits.map(({ href, label, group }, i) => (
              <Link key={href} href={href} id={optionId(i)} role="option" aria-selected={i === current} tabIndex={-1} onClick={reset}
                className={`flex items-center justify-between gap-2 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50 ${i === current ? "bg-slate-100 text-slate-900 outline-2 -outline-offset-2 outline-focus" : ""}`}>
                {label}<span className="sr-only">, </span><span className="shrink-0 text-[11px] text-slate-500">{group}</span>
              </Link>
            ))}
          </div>
          {shown && hits.length === 0 && (
            <p onMouseDown={(e) => e.preventDefault()} className={`${POPUP} px-3 py-2 text-[13px] text-slate-600`}>
              No pages match <span className="font-semibold text-slate-900">“{q}”</span>.
            </p>
          )}
          <p role="status" aria-live="polite" className="sr-only">{said}</p>
        </div>

        <Dropdown label="Alerts" menuClassName="w-72"
          buttonClassName="relative inline-flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-[14px] font-medium text-slate-800 hover:bg-slate-100 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
          button={<><Bell size={16} aria-hidden className="sm:hidden" /><span className="hidden sm:inline">Alerts</span></>}>
          <NotificationsPanel />
        </Dropdown>
        <NowUtc />
        <ThemeToggle className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400" />
        <AccountMenu />
      </div>
    </header>
  );
}

/** The notifications panel's content. It is not a modal: it takes focus when it opens, so its heading and text are
 *  announced, and Tab and Shift+Tab move on from it as usual (leaving the panel closes it). Escape closes it and returns
 *  focus to its button (the shared Dropdown). */
function NotificationsPanel() {
  const ref = useRef<HTMLDivElement>(null);
  const textId = useId();
  useEffect(() => { ref.current?.focus(); }, []);
  // Named with aria-label: the visible heading is upper-cased by CSS, which would otherwise become the spoken name. The
  // focus indicator is the application's own :focus-visible outline (globals.css), drawn inside the panel so the
  // panel's rounded, overflow-hidden frame does not clip it (an inline offset, because that rule is not layered).
  return (
    <div ref={ref} tabIndex={-1} role="group" aria-label="Needs attention" aria-describedby={textId} style={{ outlineOffset: -2 }}>
      <p aria-hidden className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Needs attention</p>
      <p id={textId} className="px-3 py-3 text-[13px] text-slate-500">Notifications cover customer-organisation work, so Zoiko staff accounts have none.</p>
    </div>
  );
}

/** The current date and time in UTC, the platform's operating time zone. Rendered after mount (the server cannot know
 *  it) and refreshed every 15 seconds. */
function NowUtc() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const h = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(h);
  }, []);
  if (!now) return <span aria-hidden className="hidden w-20 sm:block" />;
  const date = now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const time = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return (
    <time dateTime={now.toISOString()} title={`${now.toUTCString()} (your local time: ${now.toLocaleString()})`} className="hidden px-1 leading-tight sm:block">
      <span className="block text-[11px] text-slate-500">{date}</span>
      <span className="block text-[13px] font-semibold tabular-nums text-slate-800">{time} <span className="font-normal text-slate-500">UTC</span></span>
    </time>
  );
}
