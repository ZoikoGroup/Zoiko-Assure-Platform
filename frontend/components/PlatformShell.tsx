"use client";

import Link, { useLinkStatus } from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowUpFromLine, Bell, Building2, ChevronRight, ClipboardCheck, FileCog, FileText, LayoutGrid, Loader2, LogOut, Menu,
  Palette, Search, Server, ShieldCheck, UserCog,
} from "lucide-react";
import { useAuth } from "@/lib/actor";
import { roleLabel, rolesText, workContext } from "@/lib/roles";
import { jumpTargets, navFor, routeMeta } from "./nav";
import { Logo } from "./Shell";
import { ThemeToggle } from "./ThemeToggle";
import { Drawer, Dropdown, DropdownItem } from "./ui";

/* The Platform Control Plane chrome for the Zoiko Super Admin (persona "platform"): a floating sidebar and a header with
   page search, theme, notifications and the time, over the themed backdrop. It is built only from the foundation
   tokens (app/design-tokens.css), so it follows the one application theme (data-theme on <html>), as Design Governance
   does. Customer-organisation users and content staff keep their own chrome (components/Shell.tsx).

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
    { label: "Regulatory Sources", icon: FileText },
    { label: "Evidence Integrity", icon: ShieldCheck },
    { label: "Agent Controls", icon: UserCog },
  ] },
  { group: "Operations", items: [
    { label: "Services & Cells", icon: Server },
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

// As in Shell.tsx: "Super Admin (Platform)" → "SA" / "Super Admin".
const initials = (n?: string) => (n ?? "?").replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
const shortName = (n?: string) => (n ?? "").replace(/\s*\(.*\)/, "");

/* The sidebar is dark in both themes, so its focus outline uses the accent (the light theme's navy focus colour would
   not show on it). The global :focus-visible rule reads --za-focus. */
const SIDEBAR_FOCUS = "[--za-focus:var(--za-accent)]";
const CONTROL = "rounded-control border border-line bg-surface-control/80 text-fg-secondary transition-colors hover:border-line-strong hover:text-fg";

export function PlatformFrame({ path, synthetic, children }: { path: string; synthetic: boolean; children: ReactNode }) {
  const { me } = useAuth();
  const [mobile, setMobile] = useState(false);
  useEffect(() => setMobile(false), [path]);
  return (
    <div className="min-h-screen bg-backdrop text-fg">
      <a href="#main" className="sr-only z-[80] rounded-control bg-accent px-3 py-2 text-on-accent focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
      <aside aria-label="Primary navigation"
        className={`fixed inset-y-4 left-4 z-30 hidden w-sidebar flex-col overflow-hidden rounded-panel border border-sidebar-line bg-surface-sidebar/85 shadow-panel backdrop-blur-md lg:flex ${SIDEBAR_FOCUS}`}>
        <PlatformSidebar path={path} />
      </aside>
      <Drawer open={mobile} onClose={() => setMobile(false)} title="Navigation" side="left" width="max-w-[280px]">
        <div className={`-m-4 flex h-[calc(100vh-49px)] flex-col bg-surface-sidebar ${SIDEBAR_FOCUS}`}><PlatformSidebar path={path} /></div>
      </Drawer>
      <div className="lg:pl-[calc(var(--spacing-sidebar)+2.5rem)]">
        <div className="mx-auto flex min-h-screen w-full max-w-content flex-col px-4 sm:px-6 lg:pl-0 lg:pr-8">
          <PlatformHeader synthetic={synthetic} onMenu={() => setMobile(true)} />
          <main id="main" key={me?.actor_id} className="flex-1 pb-10 pt-6">{children}</main>
        </div>
      </div>
    </div>
  );
}

function PlatformSidebar({ path }: { path: string }) {
  const { me } = useAuth();
  const active = routeMeta(path)?.item.href;
  const groups = useMemo(() => platformNav(me?.permissions), [me]);
  // Keep the current page's item visible in the scrollable sidebar.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest" }); }, [active]);
  return (
    <>
      <div className="shrink-0 px-sidebar-inset pt-sidebar-inset">
        {/* White in both themes: the navy wordmark needs it. */}
        <Link href="/" aria-label="Zoiko Assure, Command Center" className="inline-flex rounded-card bg-[#fff] px-4 py-3 shadow-card">
          <Logo width={104} onDark={false} />
        </Link>
        <p className="mt-5 type-label text-sidebar-fg">Control plane</p>
      </div>
      <nav ref={navRef} aria-label="Super Admin" className="scroll-thin flex-1 overflow-y-auto px-4 pb-4 pt-6">
        {groups.map((g) => <NavGroup key={g.group} group={g} active={active} />)}
      </nav>
      <div className="shrink-0 border-t border-sidebar-line p-3"><ProfileMenu /></div>
    </>
  );
}

function NavGroup({ group, active }: { group: Group; active?: string }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="mt-3 first:mt-0">
      <div id={id} className="mb-1.5 px-2 type-label text-sidebar-muted">{group.group}</div>
      <ul className="space-y-1">
        {group.items.map(({ label, icon: Icon, href }) => (
          <li key={label}>
            {href ? (
              <Link href={href} aria-current={href === active ? "page" : undefined}
                className={`flex h-10 items-center gap-3 rounded-nav px-3 type-body transition-colors ${href === active
                  ? "bg-linear-to-r from-accent-strong to-accent font-semibold text-on-accent shadow-glow"
                  : "text-sidebar-fg hover:bg-sidebar-hover hover:text-sidebar-strong"}`}>
                <NavIcon icon={Icon} /><span className="truncate">{label}</span>
              </Link>
            ) : (
              <span aria-disabled="true" title="Planned: there is no Super Admin screen for this area yet"
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

/** A link's icon, swapped for a spinner while its navigation is pending (as in the application sidebar). */
function NavIcon({ icon: Icon }: { icon: LucideIcon }) {
  const { pending } = useLinkStatus();
  return pending ? (
    <><Loader2 size={17} aria-hidden className="shrink-0 motion-safe:animate-spin" /><span className="sr-only">Loading</span></>
  ) : <Icon size={17} aria-hidden className="shrink-0" />;
}

/** The sidebar's identity block, which opens the account menu (the same entries as the application header's). */
function ProfileMenu() {
  const { me, logout } = useAuth();
  const c = workContext(me);
  return (
    <Dropdown label={`Account menu, ${shortName(me?.display_name)}`} align="left" menuClassName="bottom-full mb-2 w-full"
      buttonClassName="flex w-full items-center gap-3 rounded-nav px-2 py-2 text-left transition-colors hover:bg-sidebar-hover"
      button={<>
        <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#fff] type-caption font-bold text-midnight-950">{initials(me?.display_name)}</span>
        <span className="min-w-0 flex-1" title={rolesText(me)}>
          <span className="block truncate type-secondary font-medium text-sidebar-strong">{shortName(me?.display_name)}</span>
          <span className="block truncate type-caption text-sidebar-muted">{c.name}</span>
        </span>
        <ChevronRight size={16} aria-hidden className="shrink-0 text-sidebar-muted" />
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

function PlatformHeader({ synthetic, onMenu }: { synthetic: boolean; onMenu: () => void }) {
  const { me } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [mac, setMac] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

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

  // Jumps to the screens this account may open (navigation, not record search), so it says "pages".
  const hits = useMemo(() => (q ? jumpTargets("platform", me?.permissions).filter((i) => `${i.label} ${i.group}`.toLowerCase().includes(q.toLowerCase())).slice(0, 7) : []), [q, me]);
  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && hits[0]) { router.push(hits[0].href); setQ(""); }
    if (e.key === "Escape") setQ("");
  };

  return (
    <header className="flex items-center gap-2 pt-4 sm:gap-3">
      <button type="button" onClick={onMenu} aria-label="Open navigation" title="Open navigation"
        className={`${CONTROL} inline-flex h-10.5 w-10.5 shrink-0 items-center justify-center lg:hidden`}>
        <Menu size={18} aria-hidden />
      </button>

      <div className="relative min-w-0 flex-1 md:w-96 md:flex-none">
        <Search size={16} aria-hidden className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-fg-muted" />
        <input ref={searchRef} type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onSearchKey}
          placeholder="Search pages…" aria-label="Search Super Admin pages" aria-keyshortcuts="/ Control+K Meta+K"
          className={`${CONTROL} h-10.5 w-full pl-10 type-secondary text-fg placeholder:text-fg-muted focus:border-accent ${q ? "pr-3" : "pr-3 sm:pr-14"}`} />
        {!q && <kbd aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 rounded-chip border border-line px-1.5 type-code text-fg-muted sm:block">{mac ? "⌘K" : "Ctrl K"}</kbd>}
        {hits.length > 0 && (
          <ul aria-label="Matching pages" className="absolute left-0 z-40 mt-2 w-full min-w-64 overflow-hidden rounded-card border border-line bg-surface-modal py-1.5 shadow-modal">
            {hits.map(({ href, label, group }) => (
              <li key={href}>
                <Link href={href} onClick={() => setQ("")} className="flex items-center justify-between gap-3 px-3 py-2 type-secondary text-fg hover:bg-fg/5">
                  {label}<span className="type-caption text-fg-muted">{group}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {synthetic && (
        <span title="This page includes synthetic demonstration figures, each marked SYNTHETIC. Figures marked LIVE are read from this deployment."
          className="hidden shrink-0 items-center whitespace-nowrap rounded-full border border-dashed border-line-strong px-2.5 py-1 type-label text-fg-secondary xl:inline-flex">
          Synthetic demo data
        </span>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
        <ThemeToggle className={`${CONTROL} inline-flex h-10.5 w-10.5 items-center justify-center`} iconSize={17} />
        <Dropdown label="Notifications" menuClassName="w-72" buttonClassName={`${CONTROL} relative inline-flex h-10.5 w-10.5 items-center justify-center`}
          button={<Bell size={17} aria-hidden />}>
          <div className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Needs attention</div>
          <p className="px-3 py-3 text-[13px] text-slate-500">Notifications cover customer-organisation work, so Zoiko staff accounts have none.</p>
        </Dropdown>
        <span aria-hidden className="hidden h-8 w-px bg-line sm:block" />
        <NowUtc />
      </div>
    </header>
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
    <time dateTime={now.toISOString()} title={`${now.toUTCString()} (your local time: ${now.toLocaleString()})`} className="hidden leading-tight sm:block">
      <span className="block type-caption text-fg-secondary">{date}</span>
      <span className="block type-secondary font-semibold tabular-nums text-fg">{time} <span className="font-normal text-fg-muted">UTC</span></span>
    </time>
  );
}
