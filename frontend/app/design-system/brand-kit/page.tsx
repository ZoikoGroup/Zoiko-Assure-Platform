"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { Accessibility as A11yIcon, BookOpen, Compass, Image as ImageIcon, LayoutGrid, Palette, Ruler, SquareStack, Tags } from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { Callout, EmptyState, TabPanel, Tabs } from "@/components/ds";
import { usePageTitle } from "@/components/ui";
import { Accessibility } from "./_kit/Accessibility";
import { Backgrounds } from "./_kit/Backgrounds";
import { Colours } from "./_kit/Colours";
import { Guidelines } from "./_kit/Guidelines";
import { Icons } from "./_kit/Icons";
import { Logos } from "./_kit/Logos";
import { Navigation } from "./_kit/Navigation";
import { ProvenanceChip } from "./_kit/shared";
import { Sizing } from "./_kit/Sizing";
import { Status } from "./_kit/Status";

/* Design Governance › Icons & Brand Kit (Unified Design System, Task 6). The reference for Zoiko Assure's logos, colours
   and icons, built from the files and code that exist: the three logo files in frontend/public, the tokens in
   app/design-tokens.css and app/globals.css (read live), and lucide-react, the application's only icon library.
   Proposed variations and rules are labelled as such. Nothing here edits an asset, a token or an icon mapping. */
const TITLE = "Icons & Brand Kit";

type Area = { id: string; label: string; icon: LucideIcon; keywords: string; Body: ComponentType<{ go: (id: string) => void }> };
const AREAS: Area[] = [
  { id: "logos", label: "Logos", icon: ImageIcon, keywords: "logo library backgrounds usage wordmark symbol mark compact horizontal dark light monochrome file minimum size clear space", Body: Logos },
  { id: "backgrounds", label: "Backgrounds", icon: SquareStack, keywords: "logo background white dark brand contrast distortion stretch recolour clear space do don't", Body: Backgrounds },
  { id: "colours", label: "Colours", icon: Palette, keywords: "brand colour color palette token hex primary secondary neutral surface text border focus status light dark", Body: Colours },
  { id: "icons", label: "Icons", icon: LayoutGrid, keywords: "icon library gallery lucide search category copy name size", Body: Icons },
  { id: "sizing", label: "Sizing", icon: Ruler, keywords: "icon size scale stroke alignment padding spacing button position", Body: Sizing },
  { id: "navigation", label: "Navigation", icon: Compass, keywords: "navigation icons sidebar mapping overview obligations evidence super admin design governance consistency", Body: Navigation },
  { id: "status", label: "Status", icon: Tags, keywords: "status verified needs review critical stale unknown informational attested declined in_review approved rejected", Body: Status },
  { id: "accessibility", label: "Accessibility", icon: A11yIcon, keywords: "accessibility label aria hidden screen reader keyboard focus contrast tooltip", Body: Accessibility },
  { id: "guidelines", label: "Guidelines", icon: BookOpen, keywords: "guidelines rules usage established proposed correct incorrect", Body: Guidelines },
];

export default function BrandKit() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="brand" searchLabel="Search brand kit"><Kit /></GovernanceShell>;
}

function Kit() {
  const { query } = useGovernance();
  const [active, setActive] = useState("logos");
  const q = query.trim().toLowerCase();
  const shown = AREAS.filter((a) => !q || `${a.label} ${a.keywords}`.toLowerCase().includes(q));
  const current = shown.find((a) => a.id === active) ?? shown[0];

  // The selected area lives in the URL hash, so each can be linked to directly (…/brand-kit#icons).
  useEffect(() => {
    const read = () => { const h = window.location.hash.slice(1); if (AREAS.some((a) => a.id === h)) setActive(h); };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const go = (id: string) => { setActive(id); window.history.replaceState(null, "", `#${id}`); document.getElementById("bk-areas-section")?.scrollIntoView({ block: "start" }); };

  return (
    <div className="space-y-section">
      <section aria-labelledby="bk-title" className="space-y-4">
        <h1 id="bk-title" className="type-page text-fg sm:type-display">Icons &amp; <span className="text-accent-fg">Brand Kit</span></h1>
        <p className="max-w-3xl type-body text-fg-secondary">
          Zoiko Assure&apos;s logos, colours and icons in one place, taken from the files and code the application actually uses, with the rules that already apply and
          the ones that are only proposed.
        </p>
        <Callout tone="info" title="How to read this kit">
          <span className="flex flex-wrap items-center gap-1.5">
            <ProvenanceChip kind="official" /> supplied with the platform · <ProvenanceChip kind="derived" /> made from an official asset and in use, pending approval ·
            <ProvenanceChip kind="established" /> already in the code · <ProvenanceChip kind="observed" /> measured from today&apos;s code · <ProvenanceChip kind="proposed" /> a recommendation only
          </span>
        </Callout>
      </section>

      <section id="bk-areas-section" aria-label="Brand kit areas" className="scroll-mt-6 rounded-panel border border-line bg-surface-raised/70 p-panel shadow-panel">
        {shown.length === 0 || !current ? <EmptyState title="No areas match the search" description="Try logo, colour, icon or status." /> : (
          <>
            <Tabs id="bk-areas" label="Brand kit areas" value={current.id} onChange={go} items={shown.map((a) => ({ value: a.id, label: a.label, icon: a.icon }))} />
            <TabPanel id="bk-areas" value={current.id} className="pt-6"><current.Body go={go} /></TabPanel>
          </>
        )}
      </section>
      <p className="type-caption text-fg-muted">The logo files are shown unchanged. Misuse and monochrome previews are browser effects on those same files, not new assets.</p>
    </div>
  );
}
