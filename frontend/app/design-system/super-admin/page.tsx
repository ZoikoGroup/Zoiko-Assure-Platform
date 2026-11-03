"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { Activity, Building, Check, Database, FileCheck2, KeyRound, LayoutDashboard, ScrollText, X } from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { EmptyState, PLANES, PlaneBadge, PlaneBanner, TabPanel, Tabs, type Plane } from "@/components/ds";
import { usePageTitle } from "@/components/ui";
import { AdminAudit } from "./_planes/AdminAudit";
import { JitAccess } from "./_planes/JitAccess";
import { Overview } from "./_planes/Overview";
import { ServiceHealth } from "./_planes/ServiceHealth";
import { SourceIntegrity } from "./_planes/SourceIntegrity";
import { TenantMetadata } from "./_planes/TenantMetadata";
import { Tenants } from "./_planes/Tenants";

/* Design Governance › Super Admin Planes (Unified Design System, Task 4). UI patterns for the Platform Control Plane,
   built from @/components/ds. Each section mirrors what the platform actually implements (cited in its file), says
   plainly what is not built, and runs on sample data except for two read-only live checks (health, release
   verification). Nothing here changes platform state. */
const TITLE = "Super Admin Planes";

type Area = { id: string; label: string; icon: LucideIcon; keywords: string; Body: ComponentType };
const AREAS: Area[] = [
  { id: "overview", label: "Platform overview", icon: LayoutDashboard, keywords: "overview health tenants count alerts activity events", Body: Overview },
  { id: "tenants", label: "Tenant management", icon: Building, keywords: "tenant organisation organization registration status suspend search filter", Body: Tenants },
  { id: "health", label: "Service health", icon: Activity, keywords: "service health availability component incident degraded", Body: ServiceHealth },
  { id: "integrity", label: "Source integrity", icon: FileCheck2, keywords: "source integrity verification release signature hash audit trace", Body: SourceIntegrity },
  { id: "metadata", label: "Tenant metadata", icon: Database, keywords: "tenant metadata identity region isolation tier residency cell lifecycle", Body: TenantMetadata },
  { id: "jit", label: "JIT access", icon: KeyRound, keywords: "jit just in time access request approval expiry break glass", Body: JitAccess },
  { id: "audit", label: "Administrative audit", icon: ScrollText, keywords: "audit activity actor action target result history", Body: AdminAudit },
];

const REACH: Record<Plane, { can: string[]; cannot: string[] }> = {
  platform: {
    can: ["The organisation registry: name, status, cells, contracting region, isolation tier", "Organisation Admin appointments", "Platform health and content-release integrity"],
    cannot: [],
  },
  workspace: {
    can: [],
    cannot: ["Customer evidence, facts and assurance states", "Obligations, controls, tests and approvals", "An organisation's own audit content"],
  },
};

export default function SuperAdminPlanes() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="super-admin" searchLabel="Search Super Admin areas"><Planes /></GovernanceShell>;
}

function Planes() {
  const { query } = useGovernance();
  const [active, setActive] = useState("overview");
  const q = query.trim().toLowerCase();
  const shown = AREAS.filter((a) => !q || `${a.label} ${a.keywords}`.toLowerCase().includes(q));
  const current = shown.find((a) => a.id === active) ?? shown[0];

  useEffect(() => {
    const read = () => { const h = window.location.hash.slice(1); if (AREAS.some((a) => a.id === h)) setActive(h); };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const go = (id: string) => { setActive(id); window.history.replaceState(null, "", `#${id}`); };

  return (
    <div className="space-y-section">
      <section aria-labelledby="sa-title" className="space-y-4">
        <h1 id="sa-title" className="type-page text-fg sm:type-display">Super Admin <span className={PLANES.platform.text}>Planes</span></h1>
        <p className="max-w-3xl type-body text-fg-secondary">
          Patterns for platform-level administration: the organisation registry, platform health, content integrity, privileged access and
          administrative audit. Everything here belongs to the Platform Control Plane, never to a customer&apos;s workspace.
        </p>
        <PlaneBanner plane="platform">
          Registry metadata only. The Super Admin belongs to no organisation and manages organisations as records. The server refuses this role every
          request for customer evidence, facts, assurance or audit content (INV-14); these screens never show it.
        </PlaneBanner>
      </section>

      <section aria-labelledby="sa-separation" className="space-y-3">
        <h2 id="sa-separation" className="type-section text-fg">Plane separation</h2>
        <div className="grid gap-stack md:grid-cols-2">
          {(["platform", "workspace"] as Plane[]).map((p) => (
            <div key={p} className={`rounded-card border border-l-4 border-line bg-surface-card/70 p-card ${PLANES[p].edge}`}>
              <PlaneBadge plane={p} withWho />
              <ul className="mt-3 space-y-1.5">
                {REACH[p].can.map((c) => <li key={c} className="flex gap-2 type-secondary text-fg"><Check size={15} aria-hidden className="mt-0.5 shrink-0 text-status-verified" /><span><span className="sr-only">Reaches: </span>{c}</span></li>)}
                {REACH[p].cannot.map((c) => <li key={c} className="flex gap-2 type-secondary text-fg-secondary"><X size={15} aria-hidden className="mt-0.5 shrink-0 text-status-critical" /><span><span className="sr-only">Never reached from the platform plane: </span>{c}</span></li>)}
              </ul>
              <p className="mt-3 type-caption text-fg-muted">{p === "platform" ? "What the Super Admin reaches." : "Never reached from the Super Admin role. Task 5 covers these screens."}</p>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Super Admin areas" className="rounded-panel border border-line bg-surface-raised/70 p-panel shadow-panel">
        {shown.length === 0 || !current ? <EmptyState title="No areas match the search" description="Try tenants, health, integrity, access or audit." /> : (
          <>
            <Tabs id="sa-areas" label="Super Admin areas" value={current.id} onChange={go} items={shown.map((a) => ({ value: a.id, label: a.label, icon: a.icon }))} />
            <TabPanel id="sa-areas" value={current.id} className="pt-6"><current.Body /></TabPanel>
          </>
        )}
      </section>
      <p className="type-caption text-fg-muted">Organisations, people and records here are illustrative samples. Values marked Live come from this deployment.</p>
    </div>
  );
}
