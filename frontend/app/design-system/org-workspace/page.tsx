"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { Archive, CalendarRange, ClipboardCheck, LayoutDashboard, PenLine, Settings2, SlidersHorizontal, UserCheck } from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { EmptyState, PLANES, PlaneBanner, TabPanel, Tabs } from "@/components/ds";
import { usePageTitle } from "@/components/ui";
import { Approvals } from "./_areas/Approvals";
import { Controls } from "./_areas/Controls";
import { Decisions } from "./_areas/Decisions";
import { Evidence } from "./_areas/Evidence";
import { Obligations } from "./_areas/Obligations";
import { Overview } from "./_areas/Overview";
import { Reporting } from "./_areas/Reporting";
import { Settings } from "./_areas/Settings";

/* Design Governance › Org Workspace (Unified Design System, Task 5). Screen patterns for customer-organisation users,
   built from @/components/ds. Each area mirrors what the application's own screens record and allow (cited in its
   file) on sample data, links to the real screen, and reuses the High-Assurance Flows for full workflows. The only
   live read is an opt-in view of your own organisation record in Settings. Nothing here changes any record. */
const TITLE = "Org Workspace";

type Area = { id: string; label: string; icon: LucideIcon; keywords: string; Body: ComponentType<{ go: (id: string) => void }> };
const AREAS: Area[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard, keywords: "organization overview identity status reporting period entity scope assurance coverage pending actions activity", Body: Overview },
  { id: "obligations", label: "Obligations", icon: ClipboardCheck, keywords: "obligations source status effective entities owner search filter dossier", Body: Obligations },
  { id: "controls", label: "Controls & tests", icon: SlidersHorizontal, keywords: "controls tests catalogue operating linked obligations history evidence", Body: Controls },
  { id: "evidence", label: "Evidence", icon: Archive, keywords: "evidence repository classification metadata provenance integrity preview upload validation", Body: Evidence },
  { id: "approvals", label: "Approvals", icon: UserCheck, keywords: "human approvals queue requester approver justification exception compensation risk", Body: Approvals },
  { id: "decisions", label: "Decisions", icon: PenLine, keywords: "decisions attestations determination authority basis history attested declined", Body: Decisions },
  { id: "reporting", label: "Reporting", icon: CalendarRange, keywords: "assurance reporting periods snapshot package completeness coverage warnings report", Body: Reporting },
  { id: "settings", label: "Settings", icon: Settings2, keywords: "organization settings profile entities jurisdictions cells residency preferences configuration", Body: Settings },
];

export default function OrgWorkspace() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="org" searchLabel="Search workspace"><Workspace /></GovernanceShell>;
}

function Workspace() {
  const { query } = useGovernance();
  const [active, setActive] = useState("overview");
  const q = query.trim().toLowerCase();
  const shown = AREAS.filter((a) => !q || `${a.label} ${a.keywords}`.toLowerCase().includes(q));
  const current = shown.find((a) => a.id === active) ?? shown[0];

  // The selected area lives in the URL hash, so each can be linked to directly (…/org-workspace#evidence).
  useEffect(() => {
    const read = () => { const h = window.location.hash.slice(1); if (AREAS.some((a) => a.id === h)) setActive(h); };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const go = (id: string) => { setActive(id); window.history.replaceState(null, "", `#${id}`); document.getElementById("ow-areas-section")?.scrollIntoView({ block: "start" }); };

  return (
    <div className="space-y-section">
      <section aria-labelledby="ow-title" className="space-y-4">
        <h1 id="ow-title" className="type-page text-fg sm:type-display">Org <span className={PLANES.workspace.text}>Workspace</span></h1>
        <p className="max-w-3xl type-body text-fg-secondary">
          Patterns for the people who run assurance inside one organization: what applies, which controls prove it, the evidence behind them,
          the decisions people sign and the reports auditors receive.
        </p>
        <PlaneBanner plane="workspace">
          One organization&apos;s own records. The server scopes every request to your organization and checks your role; Zoiko staff and the
          Super Admin have no standing access to any of it (INV-14). Registry administration lives in Super Admin Planes, never here.
        </PlaneBanner>
      </section>

      <section id="ow-areas-section" aria-label="Org Workspace areas" className="scroll-mt-6 rounded-panel border border-line bg-surface-raised/70 p-panel shadow-panel">
        {shown.length === 0 || !current ? <EmptyState title="No areas match the search" description="Try obligations, evidence, approvals or reporting." /> : (
          <>
            <Tabs id="ow-areas" label="Org Workspace areas" value={current.id} onChange={go} items={shown.map((a) => ({ value: a.id, label: a.label, icon: a.icon }))} />
            <TabPanel id="ow-areas" value={current.id} className="pt-6"><current.Body go={go} /></TabPanel>
          </>
        )}
      </section>
      <p className="type-caption text-fg-muted">Organizations, people and records here are illustrative samples (the fictional Northwind Group). Values marked Live come from your own organization.</p>
    </div>
  );
}
