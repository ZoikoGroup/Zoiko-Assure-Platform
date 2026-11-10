import type { LucideIcon } from "lucide-react";
import type { Persona } from "@/lib/actor";
import {
  Archive, Bot, Braces, Building2, CalendarRange, ClipboardCheck, Database, FlaskConical, Flag, GitBranch, Globe2,
  HelpCircle, History, Inbox, KeyRound, LayoutDashboard, Library, Link2, Lock, Package, PackageCheck, PlayCircle, Plug,
  Scale, ShieldAlert, SlidersHorizontal, Users,
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
export type NavGroup = { group: string; items: NavItem[] };

const NAV_ITEMS: NavGroup[] = [
  { group: "Overview", items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard, for: ALL }] },
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

/** The organisation workspace sidebar (customer-organisation users), grouped as the Organization Command Center design.
 *  Each entry opens an existing NAV screen; `more` lists the section's other NAV screens, shown beneath it while one of
 *  them is open. Every tenant screen in NAV appears here exactly once, so no destination is lost. Presentation only:
 *  breadcrumbs, canOpen and "Jump to" keep using NAV. */
export type OrgNavItem = { href: string; label: string; icon: LucideIcon; more?: string[] };
export type OrgNavGroup = { group: string; items: OrgNavItem[] };
const ORG_NAV: OrgNavGroup[] = [
  { group: "Workspace", items: [
    { href: "/", label: "Overview", icon: LayoutDashboard },
    { href: "/fact-requests", label: "My Work & Approvals", icon: HelpCircle },
  ] },
  { group: "Regulation", items: [
    { href: "/regulatory", label: "Change Radar", icon: Inbox, more: ["/sources", "/rules", "/rules/workbench"] },
    { href: "/obligations", label: "Obligations", icon: ClipboardCheck },
  ] },
  { group: "Assurance", items: [
    { href: "/controls", label: "Controls & Testing", icon: SlidersHorizontal },
    { href: "/evidence", label: "Evidence & IPE", icon: Archive, more: ["/ipe", "/ipe/runs"] },
    { href: "/governance", label: "Findings & Actions", icon: Flag },
    { href: "/assurance", label: "Assurance Reports", icon: CalendarRange, more: ["/packages", "/history"] },
  ] },
  { group: "Governance", items: [
    { href: "/entities", label: "Entities & Scope", icon: Building2 },
    { href: "/applicability", label: "Decisions & Attestations", icon: Scale, more: ["/applicability/trace"] },
    { href: "/ai", label: "Agent Governance", icon: Bot },
  ] },
  { group: "Admin", items: [
    { href: "/admin/users", label: "Team & Access", icon: Users },
    { href: "/connectors", label: "Integrations", icon: Plug, more: ["/admin/identity"] },
    { href: "/ledger", label: "Audit Trail", icon: Link2, more: ["/admin/access-log"] },
    { href: "/admin/cells", label: "Org Settings", icon: Globe2, more: ["/admin/retention", "/admin/releases"] },
  ] },
];

const ITEMS = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.group })));

/** The organisation sidebar, limited to screens the tenant navigation offers to someone with `permissions`. `sub`
 *  resolves `more` to NAV items, so each sub-entry keeps its screen's own label and icon. A section whose own screen is
 *  not permitted but one of whose `more` screens is opens that screen instead, so no permitted destination is lost. */
export function orgNav(permissions?: readonly string[]): { group: string; items: (OrgNavItem & { sub: NavItem[] })[] }[] {
  const allowed = new Set(navFor("tenant", permissions).flatMap((g) => g.items.map((i) => i.href)));
  return ORG_NAV.map((g) => ({
    ...g,
    items: g.items.flatMap((i) => {
      const sub = (i.more ?? []).filter((h) => allowed.has(h)).map((h) => ITEMS.find((n) => n.href === h)!);
      if (allowed.has(i.href)) return [{ ...i, sub }];
      return sub.length ? [{ ...i, href: sub[0].href, sub: sub.slice(1) }] : [];
    }),
  })).filter((g) => g.items.length > 0);
}

/** The organisation sidebar section a path belongs to, for its breadcrumb and active state. */
export function orgSection(path: string): { group: string; item: OrgNavItem } | null {
  const href = routeMeta(path)?.item.href;
  for (const g of ORG_NAV) {
    const item = g.items.find((i) => i.href === href || i.more?.includes(href ?? ""));
    if (item) return { group: g.group, item };
  }
  return null;
}

/** Detail routes whose section is not their URL prefix (the source-run and evaluation-trace detail pages). */
const DETAIL_PARENT: [RegExp, string][] = [
  [/^\/applicability\/(?!trace$)[^/]+$/, "/applicability/trace"],
  [/^\/ipe\/(?!runs$)[^/]+$/, "/ipe/runs"],
];

/** Screens reached from the account menu and "Jump to" rather than the sidebar. */
const EXTRA = [
  { href: "/admin/dictionary", label: "Controlled Dictionary", group: "Administration & Trust" },
  { href: "/design-system", label: "Design System & UI Kit", group: "Design Governance" },
  { href: "/design-system/components", label: "Design System Components", group: "Design Governance" },
  { href: "/design-system/flows", label: "High-Assurance Flows", group: "Design Governance" },
  { href: "/design-system/super-admin", label: "Super Admin Planes", group: "Design Governance" },
  { href: "/design-system/org-workspace", label: "Org Workspace", group: "Design Governance" },
];

/** Screens with their own full-page workspace chrome, shown without the application sidebar and header. They still
 *  require a signed-in session (the Shell checks it first); the Design Governance workspace holds no tenant data. */
export const isStandalone = (path: string) => path === "/design-system" || path.startsWith("/design-system/");

/** A customer organisation's registry record is platform administration, not part of the Super Admin's own
 *  dashboard: its breadcrumb reads "Platform administration › Organisations › <name>" (the list lives on "/"). */
const PLATFORM_DETAIL = /^\/organisations\/[^/]+$/;

export type RouteMeta = { group: string; item: { href: string; label: string }; isDetail: boolean };

/** Where a path sits in the navigation: its group, its sidebar item, and whether it is a detail page below it. */
export function routeMeta(path: string): RouteMeta | null {
  const extra = EXTRA.find((e) => e.href === path);
  if (extra) return { group: extra.group, item: extra, isDetail: false };
  if (PLATFORM_DETAIL.test(path)) return { group: "Platform administration", item: { href: "/", label: "Organisations" }, isDetail: true };
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
  const meta = routeMeta(path);
  if (!meta) return false;
  return EXTRA.some((e) => e.href === meta.item.href) || navFor(persona, permissions).some((g) => g.items.some((i) => i.href === meta.item.href));
}

/** Everything "Jump to" can reach for this persona (with `permissions`). */
export function jumpTargets(persona: Persona, permissions?: readonly string[]) {
  return [...navFor(persona, permissions).flatMap((g) => g.items.map(({ href, label }) => ({ href, label, group: g.group }))), ...EXTRA];
}
