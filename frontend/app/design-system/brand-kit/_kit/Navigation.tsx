"use client";

import type { LucideIcon } from "lucide-react";
import {
  Activity, Archive, Building, CalendarRange, ClipboardCheck, Database, FileCheck2, KeyRound, LayoutDashboard, PenLine, ScrollText, Settings2,
  SlidersHorizontal, UserCheck,
} from "lucide-react";
import { CollapsiblePanel, PlaneBadge, TBody, THead, Table, Td, Th, Tr, type Plane } from "@/components/ds";
import { SECTIONS } from "@/components/DesignGovernance";
import { NAV, orgNav } from "@/components/nav";
import { ICONS } from "./iconRegistry";
import { Block, ProvenanceChip } from "./shared";

/* F. Navigation icons. The organization sidebar (orgNav), the application navigation (NAV) and the Design Governance
   sections are read from their own modules. The Super Admin and Org Workspace area tabs are defined inside their page
   files, which may not export values, so their icons are listed here with the file named; the browser check compares
   them with the rendered tabs. Nothing here changes a mapping. */

const SUPER_ADMIN_AREAS: [string, LucideIcon][] = [
  ["Platform overview", LayoutDashboard], ["Tenant management", Building], ["Service health", Activity], ["Source integrity", FileCheck2],
  ["Tenant metadata", Database], ["JIT access", KeyRound], ["Administrative audit", ScrollText],
];
const ORG_WORKSPACE_AREAS: [string, LucideIcon][] = [
  ["Overview", LayoutDashboard], ["Obligations", ClipboardCheck], ["Controls & tests", SlidersHorizontal], ["Evidence", Archive],
  ["Approvals", UserCheck], ["Decisions", PenLine], ["Reporting", CalendarRange], ["Settings", Settings2],
];

const nameOf = (Icon: LucideIcon) => ICONS.find((i) => i.Icon === Icon)?.name ?? (Icon as { displayName?: string }).displayName ?? "?";
type Row = { label: string; Icon: LucideIcon; plane?: Plane; source: string; href?: string };

const ORG: Row[] = orgNav().flatMap((g) => g.items.map((i) => ({ label: i.label, Icon: i.icon, plane: "workspace" as Plane, source: `Organization sidebar › ${g.group}`, href: i.href })));
const SA: Row[] = SUPER_ADMIN_AREAS.map(([label, Icon]) => ({ label, Icon, plane: "platform" as Plane, source: "Super Admin Planes tabs (super-admin/page.tsx)" }));
const DG: Row[] = SECTIONS.flatMap((g) => g.items.map((i) => ({ label: i.label, Icon: i.icon, source: `Design Governance › ${g.group}`, href: i.href })));
const OW: Row[] = ORG_WORKSPACE_AREAS.map(([label, Icon]) => ({ label, Icon, plane: "workspace" as Plane, source: "Org Workspace tabs (org-workspace/page.tsx)" }));
const APP: Row[] = NAV.flatMap((g) => g.items.map((i) => ({ label: i.label, Icon: i.icon, source: `Application navigation › ${g.group}`, href: i.href })));

/** Same screen, different icon in the two application navigations (NAV and the organization sidebar). */
const SAME_SCREEN_DIFFERENT_ICON = ORG.filter((o) => { const a = APP.find((x) => x.href === o.href); return a && a.Icon !== o.Icon; });
/** One icon standing for several different destinations across every navigation on this page. */
const MULTI = (() => {
  const by = new Map<LucideIcon, Row[]>();
  for (const r of [...APP, ...ORG, ...SA, ...OW, ...DG]) by.set(r.Icon, [...(by.get(r.Icon) ?? []), r]);
  return [...by.entries()].map(([Icon, rows]) => ({ Icon, labels: [...new Set(rows.map((r) => r.label))] })).filter((x) => x.labels.length > 1);
})();

/** Assessed by hand from the mappings above (Observed); each recommendation is a proposal for a later task. */
const FINDINGS: [string, string][] = [
  ["Approvals", "Org Workspace and the Approval flow use UserCheck; the organization sidebar's “My Work & Approvals” uses HelpCircle, because it opens Fact Requests. Proposed: give approvals one icon (UserCheck) once My Work & Approvals has its own screen."],
  ["Settings", "Org Workspace's Settings tab uses Settings2; the organization sidebar's “Org Settings” uses Globe2, because it opens Cells & Residency. Proposed: Settings2 for settings, Globe2 for residency only."],
  ["Super Admin Planes", "Design Governance uses FileText for Super Admin Planes, while the Platform Control Plane badge uses ServerCog. Proposed: ServerCog in both."],
  ["Icons shared with status", "HelpCircle (Fact Requests), Flag (Findings & Actions) and ShieldAlert (Privileged Access Log) also serve as legacy status-pill icons (unknown, review, exception). Status pills always print their code, so the meaning is never lost, but the overlap is worth retiring when the pills move to the design-system set."],
  ["Building and Building2", "Consistent today: Building marks a tenant or entity record, Building2 the organization workspace. Keep the split."],
];

export function Navigation() {
  const standard = [...ORG, ...SA];
  return (
    <div className="space-y-section">
      <Block title="Standard navigation icons" aside={<ProvenanceChip kind="established">Read from components/nav.ts</ProvenanceChip>}>
        <Table caption="Navigation destinations and their icons" minWidth="46rem">
          <THead><Tr><Th>Destination</Th><Th>Icon</Th><Th>Name</Th><Th>Plane</Th><Th>Source</Th></Tr></THead>
          <TBody>
            {standard.map((r) => <NavRow key={`${r.source}-${r.label}`} r={r} />)}
            <Tr><Td colSpan={5} className="bg-surface-raised type-label text-fg-muted">Design Governance</Td></Tr>
            {DG.map((r) => <NavRow key={`${r.source}-${r.label}`} r={r} />)}
          </TBody>
        </Table>
        <p className="type-caption text-fg-muted">
          The organization sidebar groups each section&apos;s other screens beneath it (Change Radar holds Sources, Rule Authoring and the Workbench, for example); those
          sub-entries keep their own icons from the application navigation below.
        </p>
      </Block>

      <CollapsiblePanel title="Org Workspace areas and the full application navigation" description={`${OW.length} area tabs and ${APP.length} application screens, with their icons`}>
        <div className="space-y-stack">
          <Table caption="Org Workspace area tabs" minWidth="36rem"><THead><Tr><Th>Area</Th><Th>Icon</Th><Th>Name</Th><Th>Plane</Th><Th>Source</Th></Tr></THead>
            <TBody>{OW.map((r) => <NavRow key={r.label} r={r} />)}</TBody></Table>
          <Table caption="Application navigation (breadcrumbs, Jump to, staff sidebar)" minWidth="36rem"><THead><Tr><Th>Screen</Th><Th>Icon</Th><Th>Name</Th><Th>Plane</Th><Th>Source</Th></Tr></THead>
            <TBody>{APP.map((r) => <NavRow key={r.href} r={r} />)}</TBody></Table>
        </div>
      </CollapsiblePanel>

      <Block title="Consistency check" aside={<ProvenanceChip kind="observed">Computed from the mappings</ProvenanceChip>}>
        <ul className="space-y-2 type-secondary text-fg-secondary">
          <li><span className="font-semibold text-fg">Same screen, different icon:</span> {SAME_SCREEN_DIFFERENT_ICON.length === 0 ? "none. The organization sidebar and the application navigation agree for every shared screen." :
            SAME_SCREEN_DIFFERENT_ICON.map((r) => r.label).join(", ")}</li>
          <li><span className="font-semibold text-fg">One icon, several destinations ({MULTI.length}):</span>
            <ul className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
              {MULTI.map(({ Icon, labels }) => (
                <li key={nameOf(Icon)} className="flex items-start gap-2 rounded-control border border-line bg-surface-card/70 px-2.5 py-1.5">
                  <Icon size={16} aria-hidden className="mt-0.5 shrink-0 text-fg" />
                  <span><code className="type-code text-fg">{nameOf(Icon)}</code>: {labels.join(" · ")}</span>
                </li>
              ))}
            </ul>
          </li>
        </ul>
      </Block>

      <Block title="Findings" aside={<ProvenanceChip kind="proposed">Recommendations · no mapping changed</ProvenanceChip>}>
        <ul className="space-y-2">{FINDINGS.map(([t, d]) => (
          <li key={t} className="rounded-card border border-line bg-surface-card/80 p-3"><p className="type-secondary font-semibold text-fg">{t}</p><p className="mt-1 type-caption text-fg-secondary">{d}</p></li>
        ))}</ul>
      </Block>
    </div>
  );
}

function NavRow({ r }: { r: Row }) {
  return (
    <Tr>
      <Td className="font-medium text-fg">{r.label}</Td>
      <Td><span className="flex items-center gap-2 text-fg"><r.Icon size={16} aria-hidden /><r.Icon size={20} aria-hidden /></span></Td>
      <Td><code className="type-code">{nameOf(r.Icon)}</code></Td>
      <Td>{r.plane ? <PlaneBadge plane={r.plane} size="sm" /> : <span className="type-caption text-fg-muted">Design Governance</span>}</Td>
      <Td className="text-fg-secondary">{r.source}</Td>
    </Tr>
  );
}
