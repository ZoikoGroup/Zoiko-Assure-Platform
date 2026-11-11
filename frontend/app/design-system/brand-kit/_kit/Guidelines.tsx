"use client";

import { ArrowRight } from "lucide-react";
import { Button, Callout, CollapsiblePanel } from "@/components/ds";
import { ProvenanceChip, type Provenance } from "./shared";

/* I. Brand usage guidelines. Every rule is tagged: Established rules name where the application already does it;
   Proposed rules are recommendations awaiting the brand owner or a later task. No brand guideline document exists in the
   repository, so nothing here is presented as one. */

type Rule = [Provenance, string, string?];
const TOPICS: { id: string; title: string; area?: string; rules: Rule[] }[] = [
  { id: "logo", title: "Logo usage", area: "logos", rules: [
    ["established", "Render the logo through the Logo component, never as an image of the whole file: each file is a 1001 px square with the artwork in the centre.", "components/Shell.tsx"],
    ["established", "Use logo.png on white and light surfaces.", "staff sidebar, sign-in, password change"],
    ["established", "Collapsed sidebars show the symbol mark (logo-mark.jpg) at 32 px.", "components/Shell.tsx"],
    ["derived", "logo-on-dark.png is the dark-surface wordmark. It is in use but was derived during this design work: keep it pending brand-owner approval.", "organization sidebar"],
    ["proposed", "Minimum on-screen size: wordmark 110 px wide (the smallest current use, where ASSURE is about 6 px tall); symbol mark 16 px."],
    ["proposed", "Clear space: at least a quarter of the artwork's height on every side (about 9 px at the 140 px sidebar size). Today's crop leaves about 4 px."],
    ["proposed", "Never stretch, squash, rotate, recolour, filter or add shadows, outlines or glows."],
  ] },
  { id: "backgrounds", title: "Logo backgrounds", area: "backgrounds", rules: [
    ["established", "Primary wordmark on white or light surfaces; dark-surface wordmark on navy and dark surfaces; the symbol mark on any surface.", "components/Shell.tsx"],
    ["proposed", "Avoid the brand orange and mid-tone backgrounds: the orange letters merge into the fill."],
    ["proposed", "Keep each artwork colour at 3:1 or more against its background (logos are exempt from WCAG, so this is a legibility target)."],
  ] },
  { id: "colour", title: "Brand colours", area: "colours", rules: [
    ["established", "Orange leads and marks the Customer Assurance Workspace; blue is secondary and marks the Platform Control Plane. Keep the planes visually separate.", "Foundations, components/ds/Plane.tsx"],
    ["established", "Components use the semantic tokens (--za-*, bg-surface-card, text-fg-secondary and so on), not primitives or HEX values.", "app/design-tokens.css"],
    ["established", "Text and icons on an accent fill use --za-on-accent: white on orange fails contrast in both themes.", "app/design-tokens.css"],
    ["established", "Every text and focus pair meets WCAG 2.2 AA in both themes; Foundations recomputes the ratios from the live values.", "Foundations"],
    ["proposed", "The logo's artwork colours are close to, but not the same as, the palette primitives. If the brand owner specifies exact values, add them as primitives rather than editing the files."],
  ] },
  { id: "sizing", title: "Icon sizing", area: "sizing", rules: [
    ["observed", "Today's code uses sizes from 11 to 22 px in 1 px steps; 14–16 px is the most common."],
    ["proposed", "Adopt five steps: 12 px metadata, 16 px inline, tables, buttons and navigation, 20 px prominent icons, 24 px feature tiles, 32 px illustrations."],
    ["proposed", "Centre icons on the text line and widen the gap as the icon grows: 4 px at 12, 6 px at 16, 8 px at 20."],
  ] },
  { id: "stroke", title: "Icon stroke", area: "sizing", rules: [
    ["established", "Use lucide's default stroke (2 on its 24-unit grid), which scales with the icon.", "lucide-react"],
    ["established", "Small marks are drawn heavier to stay legible: badge icons 2.5, checkbox and stepper ticks 3.", "components/ds/Status.tsx, Form.tsx, Navigation.tsx"],
    ["proposed", "Avoid any other stroke width; the sign-in page's 1.75 and 2.25 are the exceptions to review."],
  ] },
  { id: "navigation", title: "Navigation icon conventions", area: "navigation", rules: [
    ["established", "Navigation icons sit beside a visible label and are hidden from screen readers.", "components/Shell.tsx, DesignGovernance.tsx"],
    ["established", "A section's sub-entries keep their own screen's label and icon.", "components/nav.ts orgNav"],
    ["established", "The organization sidebar and the application navigation use the same icon for every screen they share.", "computed in Navigation icons"],
    ["proposed", "One concept, one icon: resolve the Approvals, Settings and Super Admin Planes differences listed under Navigation icons."],
  ] },
  { id: "status", title: "Status icon conventions", area: "status", rules: [
    ["established", "Five visual statuses, each with its own icon and word: Verified, Needs Review, Critical, Stale / Unknown, Informational.", "components/ds/Status.tsx"],
    ["established", "Backend codes are printed exactly as recorded beside the visual status, and their meanings never change.", "StateBadge, StatusPill"],
    ["established", "There is deliberately no COMPLIANT state, and no screen claims compliance.", "Controlled Dictionary"],
    ["established", "An unrecognised code is shown neutral and marked, never mapped to a known state.", "components/ui.tsx"],
    ["proposed", "Move the application's status pills onto the design-system icons, and choose a visual family for DECLINED."],
  ] },
  { id: "a11y", title: "Accessibility", area: "accessibility", rules: [
    ["established", "Decorative icons carry aria-hidden; the text beside them carries the meaning.", "components/ds"],
    ["established", "Icon-only controls have an accessible name and a tooltip on hover and focus.", "IconButton, Tooltip"],
    ["established", "Every control shows a visible focus ring in the theme's focus colour, at 3:1 or more.", "app/design-tokens.css"],
    ["established", "Status is never shown by colour or icon alone.", "StatusBadge, StateBadge, StatusPill"],
    ["established", "The logo is announced once, as “Zoiko Assure”.", "Logo component"],
  ] },
];

export function Guidelines({ go }: { go: (id: string) => void }) {
  return (
    <div className="space-y-stack">
      <Callout tone="info" title="Established rules and proposals, kept apart">
        No brand guideline document exists in the repository. <strong>Established</strong> rules name where the application already follows them;
        <strong> Proposed</strong> rules are recommendations for the brand owner or a later task; <strong>Observed</strong> notes describe today&apos;s code.
      </Callout>
      {TOPICS.map((t, i) => (
        <CollapsiblePanel key={t.id} defaultOpen={i === 0} title={t.title}
          meta={<span className="flex gap-1.5"><ProvenanceChip kind="established">{t.rules.filter((r) => r[0] === "established").length} established</ProvenanceChip>
            <ProvenanceChip kind="proposed">{t.rules.filter((r) => r[0] === "proposed").length} proposed</ProvenanceChip></span>}>
          <ul className="space-y-2.5">
            {t.rules.map(([kind, text, source]) => (
              <li key={text} className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
                <span className="shrink-0 sm:w-36"><ProvenanceChip kind={kind} /></span>
                <span className="type-secondary text-fg">{text}{source && <span className="block type-caption text-fg-muted">Source: {source}</span>}</span>
              </li>
            ))}
          </ul>
          {t.area && <Button className="mt-4" size="sm" variant="ghost" iconRight={ArrowRight} onClick={() => go(t.area!)}>See the examples<span className="sr-only"> for {t.title}</span></Button>}
        </CollapsiblePanel>
      ))}
    </div>
  );
}
