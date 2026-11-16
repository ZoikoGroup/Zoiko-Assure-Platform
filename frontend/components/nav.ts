import type { LucideIcon } from "lucide-react";
import type { Persona } from "@/lib/actor";
import {
  Archive, Bot, Braces, Building2, CalendarRange, ClipboardCheck, Database, FileSearch, FlaskConical, Flag, GitBranch, Globe2,
  HelpCircle, History, Inbox, KeyRound, LayoutDashboard, Library, Link2, Lock, Package, PackageCheck, PlayCircle, Plug,
  Scale, Settings2, ShieldAlert, SlidersHorizontal, Users,
} from "lucide-react";

import SCREEN_ACCESS from "@/lib/screen-access.json";

/** One navigation model for the sidebar, breadcrumbs and "Jump to" search.
 *  `for`: who can use the screen (default: tenant users only). Zoiko content staff and the platform Super Admin have no
 *  standing tenant-data access (INV-14, IAM-REQ-024), so tenant screens are not offered to them.
 *  `perms` (from lib/screen-access.json, generated from the backend's route guards): the screen is offered only to
 *  someone holding one of them organisation- or platform-wide, so the sidebar never shows a screen the server would
 *  refuse on load. The backend enforces every permission and tenant boundary either way; this only avoids showing
 *  screens that would be refused. */
export type NavItem = { href: string; label: string; icon: LucideIcon; for?: Persona[]; perms?: readonly string[] };
const ACCESS: Record<string, { reads: string[]; perms: string[] }> = SCREEN_ACCESS.screens;
const ALL: Persona[] = ["tenant", "content", "platform"];
const CONTENT: Persona[] = ["tenant", "content"];
const PLATFORM: Persona[] = ["platform"];
export type NavGroup = { group: string; items: NavItem[] };

const NAV_ITEMS: NavGroup[] = [
  { group: "Overview", items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard, for: ALL }] },
  // The Super Admin's Action Queue and organisation registry (both read GET /platform/organisations); "/" is the
  // Platform Command Center.
  { group: "Platform administration", items: [
    { href: "/action-queue", label: "Action Queue", icon: ClipboardCheck, for: PLATFORM },
    { href: "/organisations", label: "Tenants & Entities", icon: Building2, for: PLATFORM },
  ] },
  { group: "Regulatory Content", items: [
    { href: "/regulatory", label: "Regulatory Inbox", icon: Inbox, for: CONTENT },
    { href: "/sources", label: "Sources & Provisions", icon: Library, for: CONTENT },
    { href: "/rules", label: "Rule Authoring", icon: Braces, for: CONTENT },
    { href: "/rules/workbench", label: "Rule Test Workbench", icon: FlaskConical, for: CONTENT },
  ] },
  { group: "Applicability", items: [
    { href: "/entities", label: "Entities & Facts", icon: Building2 },
    { href: "/applicability", label: "Applicability Review", icon: Scale },
    { href: "/fact-requests", label: "My Work & Approvals", icon: HelpCircle },
    { href: "/applicability/trace", label: "Applicability Trace", icon: GitBranch },
  ] },
  { group: "Assurance", items: [
    { href: "/obligations", label: "Obligation Dossiers", icon: ClipboardCheck },
    { href: "/controls", label: "Controls & Tests", icon: SlidersHorizontal },
    { href: "/assurance", label: "Assurance Periods", icon: CalendarRange },
    { href: "/governance", label: "Findings & Actions", icon: Flag },
  ] },
  { group: "Evidence", items: [
    { href: "/evidence", label: "Evidence Records", icon: Archive },
    { href: "/ipe", label: "IPE", icon: Database },
    { href: "/ipe/runs", label: "Source Runs", icon: PlayCircle },
  ] },
  { group: "Audit & History", items: [
    { href: "/packages", label: "Audit Package Builder", icon: Package },
    { href: "/history", label: "Historical Reconstruction", icon: History },
    { href: "/ledger", label: "Ledger", icon: Link2 },
  ] },
  { group: "AI & Agents", items: [{ href: "/ai", label: "AI/Agent Asset Dossiers", icon: Bot }] },
  { group: "Integrations", items: [{ href: "/connectors", label: "Connectors", icon: Plug, for: CONTENT }] },
  { group: "Administration & Trust", items: [
    { href: "/admin/users", label: "Users & Roles", icon: Users, for: ALL },
    { href: "/admin/identity", label: "Identity Integrations", icon: KeyRound, for: ALL },
    { href: "/admin/cells", label: "Cells & Residency", icon: Globe2 },
    { href: "/admin/releases", label: "Content Releases", icon: PackageCheck, for: CONTENT },
    { href: "/admin/retention", label: "Retention & Legal Holds", icon: Lock },
    { href: "/admin/access-log", label: "Privileged Access Log", icon: ShieldAlert },
  ] },
];
export const NAV: NavGroup[] = NAV_ITEMS.map((g) => ({ ...g, items: g.items.map((i) => ({ ...i, perms: ACCESS[i.href]?.perms })) }));

/** Whether someone with `permissions` (organisation- or platform-wide, as the session reports them) can use the item.
 *  `permissions` omitted: no permission filter (persona only). */
const permitted = (i: NavItem, permissions?: readonly string[]) =>
  !i.perms || !permissions || i.perms.some((p) => permissions.includes(p));

/** The organisation workspace sidebar (customer-organisation users): four groups holding 13 standard entries and the
 *  optional Agent Governance. A consolidated page (`tabs`, components/WorkspaceHub.tsx) holds former screens as tabs,
 *  and each former address still opens its tab. Rule Authoring and the Rule Test Workbench (Zoiko content-staff tools)
 *  and Identity Integrations (nothing to configure until enterprise sign-in exists) are not offered to organisation
 *  users; they stay in NAV, so content staff and the Super Admin keep them, and their pages are unchanged. The staff
 *  sidebars, breadcrumbs and "Jump to" keep using NAV; organisation users' sidebar, breadcrumbs, document titles, page
 *  search and post-sign-in check use this. Presentation only: the backend authorises every request. */

/** One tab of a consolidated organisation page. `screen`: the NAV screen it shows. That screen's permissions decide
 *  whether the tab is offered, and its own address opens this tab. `aliases`: former names the page search also finds
 *  it by. */
export type OrgTab = { id: string; label: string; screen: string; aliases?: string[] };
/** `aliases`: former sidebar or page names the page search also finds the entry by, so none needs a second entry.
 *  `tabs`: the entry is a consolidated page, offered when any of its tabs is; otherwise it is its own NAV screen. */
export type OrgNavItem = { href: string; label: string; icon: LucideIcon; aliases?: string[]; tabs?: OrgTab[] };
export type OrgNavGroup = { group: string; items: OrgNavItem[] };
const ORG_NAV: OrgNavGroup[] = [
  { group: "Workspace", items: [
    { href: "/", label: "Overview", icon: LayoutDashboard, aliases: ["Dashboard", "Organization Command Center", "Home"] },
    { href: "/fact-requests", label: "My Work & Approvals", icon: HelpCircle, aliases: ["Fact requests", "Tasks"] },
  ] },
  { group: "Obligations", items: [
    { href: "/obligations", label: "Obligations", icon: ClipboardCheck, tabs: [
      { id: "current", label: "Current Obligations", screen: "/obligations", aliases: ["Obligation Dossiers"] },
      { id: "assurance-periods", label: "Assurance Periods", screen: "/assurance", aliases: ["Assurance Reports", "Period assurance"] },
    ] },
    { href: "/applicability", label: "Decisions & Attestations", icon: Scale, tabs: [
      { id: "decisions", label: "Decisions", screen: "/applicability", aliases: ["Applicability Review", "Determinations", "Attest"] },
      { id: "evaluation-trace", label: "Evaluation Trace", screen: "/applicability/trace", aliases: ["Applicability Trace", "Evaluations"] },
    ] },
    { href: "/entities", label: "Entities & Facts", icon: Building2, aliases: ["Entities & Scope", "Subjects"] },
    { href: "/regulatory-library", label: "Regulatory Library", icon: Library, aliases: ["Change Radar"], tabs: [
      { id: "sources", label: "Regulatory Sources", screen: "/regulatory", aliases: ["Regulatory Inbox"] },
      { id: "requirements", label: "Requirements & Provisions", screen: "/sources", aliases: ["Sources & Provisions", "Catalogue & coverage", "Instruments"] },
      { id: "releases", label: "Content Releases", screen: "/admin/releases" },
    ] },
    // Optional in the navigation proposal: meant for organisations that register AI systems. No organisation-level
    // indicator of that reaches the frontend (GET /org/tenant and /dashboard/attention carry none), and AI systems are
    // registered on this screen, so hiding it until one exists would remove the way to register the first. It is
    // therefore offered to everyone whose roles can open it.
    { href: "/ai", label: "Agent Governance", icon: Bot, aliases: ["AI/Agent Asset Dossiers", "AI assets", "AI systems", "Agents"] },
  ] },
  { group: "Assurance", items: [
    { href: "/controls", label: "Controls & Testing", icon: SlidersHorizontal, aliases: ["Controls & Tests"] },
    { href: "/evidence", label: "Evidence", icon: Archive, aliases: ["Evidence & IPE"], tabs: [
      { id: "records", label: "Evidence Records", screen: "/evidence", aliases: ["Upload evidence", "Evidence conflicts"] },
      { id: "ipe", label: "IPE", screen: "/ipe", aliases: ["IPE anomalies", "IPE policies", "Information Produced by the Entity"] },
      { id: "source-runs", label: "Source Runs", screen: "/ipe/runs" },
      { id: "retention", label: "Retention & Legal Holds", screen: "/admin/retention", aliases: ["Legal holds", "Erased evidence"] },
    ] },
    { href: "/governance", label: "Findings & Actions", icon: Flag, aliases: ["Issues", "Exceptions"] },
    { href: "/audit-reporting", label: "Audit & Reporting", icon: FileSearch, aliases: ["Audit Trail", "Assurance Reports"], tabs: [
      { id: "packages", label: "Audit Packages", screen: "/packages", aliases: ["Audit Package Builder", "Verify package"] },
      { id: "ledger", label: "Ledger", screen: "/ledger", aliases: ["Checkpoints"] },
      { id: "history", label: "Historical Reconstruction", screen: "/history", aliases: ["History"] },
      { id: "access-log", label: "Access Log", screen: "/admin/access-log", aliases: ["Privileged Access Log", "Audit events"] },
    ] },
  ] },
  { group: "Admin", items: [
    { href: "/admin/users", label: "Team & Access", icon: Users, aliases: ["Users & Roles", "Sessions"] },
    { href: "/connectors", label: "Integrations", icon: Plug, aliases: ["Connectors", "Event outbox"] },
    { href: "/admin/cells", label: "Organisation Settings", icon: Settings2, aliases: ["Org Settings", "Cells & Residency", "Legal entities"] },
  ] },
];

const ITEMS = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.group })));

/** The NAV screens a persona (with `permissions`) can open. */
export function openableScreens(persona: Persona, permissions?: readonly string[]): Set<string> {
  return new Set(navFor(persona, permissions).flatMap((g) => g.items.map((i) => i.href)));
}

/** The tabs of `item` offered to someone who can open `screens`, in their order. */
export const offeredTabs = (item: OrgNavItem, screens: Set<string>) => (item.tabs ?? []).filter((t) => screens.has(t.screen));
const isOffered = (item: OrgNavItem, screens: Set<string>) => (item.tabs ? offeredTabs(item, screens).length > 0 : screens.has(item.href));

/** A tab's address: the page itself for the first of the offered `tabs` (what the page opens on), else `?tab=<id>`. */
export const tabHref = (item: OrgNavItem, tab: OrgTab, tabs: OrgTab[]) => (tabs[0] === tab ? item.href : `${item.href}?tab=${tab.id}`);

/** The organisation sidebar entry at `href`. */
export const orgEntry = (href: string) => ORG_NAV.flatMap((g) => g.items).find((i) => i.href === href);

/** The consolidated page and tab a former screen address now opens (for example "/ipe/runs" → Evidence › Source Runs).
 *  The tab is always named, so a tab this person cannot open is reported as such rather than replaced by another. */
export function orgTabOf(screen: string): { item: OrgNavItem; tab: OrgTab; href: string } | null {
  for (const item of ORG_NAV.flatMap((g) => g.items)) {
    const tab = item.tabs?.find((t) => t.screen === screen);
    if (tab) return { item, tab, href: `${item.href}?tab=${tab.id}` };
  }
  return null;
}

/** The organisation sidebar, limited to the entries someone with `permissions` can open (every entry without). */
export function orgNav(permissions?: readonly string[]): OrgNavGroup[] {
  const screens = openableScreens("tenant", permissions);
  return ORG_NAV.map((g) => ({ ...g, items: g.items.filter((i) => isOffered(i, screens)) })).filter((g) => g.items.length > 0);
}

type OrgMatch = { group: string; item: OrgNavItem; screen: string | null; isDetail: boolean };

/** The organisation sidebar entry a path belongs to: the entry's own page, a former screen that is now one of its tabs,
 *  or a record page below either (/evidence/{id}, /ipe/{run}, /applicability/{evaluation}, …); the longest match wins.
 *  `screen`: the NAV screen whose permissions apply, or null for a consolidated page's own address when no tab shares
 *  it (any offered tab opens it). */
function orgMatch(path: string): OrgMatch | null {
  let best: (OrgMatch & { len: number }) | null = null;
  for (const g of ORG_NAV) {
    for (const item of g.items) {
      const own = item.tabs?.find((t) => t.screen === item.href);
      const roots: [string, string | null][] = [[item.href, item.tabs ? own?.screen ?? null : item.href],
        ...(item.tabs ?? []).filter((t) => t !== own).map((t): [string, string | null] => [t.screen, t.screen])];
      for (const [root, screen] of roots) {
        const exact = path === root;
        if (!exact && (root === "/" || !path.startsWith(`${root}/`))) continue;
        if (!best || root.length > best.len) best = { group: g.group, item, screen, isDetail: !exact, len: root.length };
      }
    }
  }
  return best && { group: best.group, item: best.item, screen: best.screen, isDetail: best.isDetail };
}

/** Where a path sits in the organisation sidebar (the routeMeta of organisation users): its group, its entry, and
 *  whether it is a record page below that entry. Null for screens the organisation sidebar does not offer. */
export function orgRouteMeta(path: string): RouteMeta | null {
  const m = orgMatch(path);
  return m && { group: m.group, item: { href: m.item.href, label: m.item.label }, isDetail: m.isDetail };
}

export type OrgSearchTarget = { href: string; label: string; group: string; tab?: string; terms: string };

/** What organisation users' page search offers: each sidebar entry under its own name, each tab of a consolidated page
 *  as "<entry> › <tab>" (its first tab is the page itself), then EXTRA. Former names (aliases) match too, so "Change
 *  Radar" finds Regulatory Library and "Ledger" finds Audit & Reporting › Ledger. */
export function orgSearchTargets(permissions?: readonly string[]): OrgSearchTarget[] {
  const screens = openableScreens("tenant", permissions);
  const out = new Map<string, OrgSearchTarget>();
  for (const g of ORG_NAV) {
    for (const item of g.items.filter((i) => isOffered(i, screens))) {
      out.set(item.href, { href: item.href, label: item.label, group: g.group, terms: [item.label, g.group, ...(item.aliases ?? [])].join(" ") });
      const tabs = offeredTabs(item, screens);
      for (const tab of tabs) {
        const href = tabHref(item, tab, tabs);
        const terms = [tab.label, ...(tab.aliases ?? [])].join(" ");
        const known = out.get(href);
        if (known) known.terms += ` ${terms}`;
        else out.set(href, { href, label: item.label, group: g.group, tab: tab.label, terms });
      }
    }
  }
  return [...out.values(), ...EXTRA.map((e) => ({ ...e, terms: `${e.label} ${e.group}` }))];
}

/** Detail routes whose section is not their URL prefix (the source-run and evaluation-trace detail pages). */
const DETAIL_PARENT: [RegExp, string][] = [
  [/^\/applicability\/(?!trace$)[^/]+$/, "/applicability/trace"],
  [/^\/ipe\/(?!runs$)[^/]+$/, "/ipe/runs"],
];

/** Screens reached from "Jump to" rather than the sidebar (the Design System also from the account menu). */
const EXTRA = [
  { href: "/admin/dictionary", label: "Controlled Dictionary", group: "Administration & Trust" },
  { href: "/design-system", label: "Design System & UI Kit", group: "Design Governance" },
  { href: "/design-system/components", label: "Design System Components", group: "Design Governance" },
  { href: "/design-system/flows", label: "High-Assurance Flows", group: "Design Governance" },
  { href: "/design-system/super-admin", label: "Super Admin Planes", group: "Design Governance" },
  { href: "/design-system/org-workspace", label: "Org Workspace", group: "Design Governance" },
  { href: "/design-system/brand-kit", label: "Icons & Brand Kit", group: "Design Governance" },
];

/** Screens with their own full-page workspace chrome, shown without the application sidebar and header. They still
 *  require a signed-in session (the Shell checks it first); the Design Governance workspace holds no tenant data. */
export const isStandalone = (path: string) => path === "/design-system" || path.startsWith("/design-system/");

/** A customer organisation's registry record is platform administration, not part of the Super Admin's own
 *  dashboard: its breadcrumb reads "Platform administration › Tenants & Entities › <name>" (the list lives on
 *  "/organisations"). */
const PLATFORM_DETAIL = /^\/organisations\/[^/]+$/;

export type RouteMeta = { group: string; item: { href: string; label: string }; isDetail: boolean };

/** Where a path sits in the navigation: its group, its sidebar item, and whether it is a detail page below it. */
export function routeMeta(path: string): RouteMeta | null {
  const extra = EXTRA.find((e) => e.href === path);
  if (extra) return { group: extra.group, item: extra, isDetail: false };
  if (PLATFORM_DETAIL.test(path)) return { group: "Platform administration", item: { href: "/organisations", label: "Tenants & Entities" }, isDetail: true };
  const parent = DETAIL_PARENT.find(([re]) => re.test(path))?.[1];
  const item = parent
    ? ITEMS.find((i) => i.href === parent)
    : ITEMS.filter((i) => i.href === path || (i.href !== "/" && path.startsWith(`${i.href}/`)))
      .sort((a, b) => b.href.length - a.href.length)[0];
  if (!item) return null;
  return { group: item.group, item, isDetail: path !== item.href };
}

/** The navigation a signed-in persona can use, limited to the screens its `permissions` let it open. */
export function navFor(persona: Persona, permissions?: readonly string[]): NavGroup[] {
  return NAV.map((g) => ({ ...g, items: g.items.filter((i) => (i.for ?? ["tenant"]).includes(persona) && permitted(i, permissions)) }))
    .filter((g) => g.items.length > 0);
}

/** Whether a persona (with `permissions`) can use the screen at `path`. After sign-in, a `?next=` page is only followed
 *  when this holds, so each account lands on a screen meant for it (otherwise its own dashboard at "/"). Navigation
 *  only: the backend authorises every request regardless. */
export function canOpen(persona: Persona, path: string, permissions?: readonly string[]): boolean {
  if (PLATFORM_DETAIL.test(path)) return persona === "platform";
  // Organisation users: what their own sidebar offers (a consolidated page's tabs at their former addresses included),
  // not the content-staff tools it leaves out.
  if (persona === "tenant" && !EXTRA.some((e) => e.href === path)) {
    const m = orgMatch(path);
    const screens = openableScreens("tenant", permissions);
    return !!m && (m.screen ? screens.has(m.screen) : isOffered(m.item, screens));
  }
  const meta = routeMeta(path);
  if (!meta) return false;
  return EXTRA.some((e) => e.href === meta.item.href) || navFor(persona, permissions).some((g) => g.items.some((i) => i.href === meta.item.href));
}

/** Everything "Jump to" can reach for this persona (with `permissions`). */
export function jumpTargets(persona: Persona, permissions?: readonly string[]) {
  return [...navFor(persona, permissions).flatMap((g) => g.items.map(({ href, label }) => ({ href, label, group: g.group }))), ...EXTRA];
}
