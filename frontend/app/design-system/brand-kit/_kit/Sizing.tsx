"use client";

import { useState } from "react";
import { ArrowRight, Download, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { Button, IconButton, StatusBadge, TBody, THead, TabPanel, Table, Tabs, Td, Th, Tr } from "@/components/ds";
import { Block, ProvenanceChip, Verdict } from "./shared";

/* E. Icon sizing and stroke. "Observed" rows describe the code as it is (scanned 4 Nov 2026); the scale is a proposal.
   This task changes no icon size anywhere in the application. */

const SCALE: { px: number; role: string; observed: string; examples: string }[] = [
  { px: 12, role: "Compact metadata", observed: "11–13 px", examples: "Status and state badges (11–12, stroke 2.5), plane badges (12–13), chip icons" },
  { px: 16, role: "Inline, table and control icons", observed: "14–17 px", examples: "Tabs (15), menu items (15–16), buttons (15 small, 17 medium), application sidebar (14–16), Design Governance sidebar (17)" },
  { px: 20, role: "Prominent interface icons", observed: "18–20 px", examples: "Callouts (18), status marks (18), empty states (20)" },
  { px: 24, role: "Feature tiles", observed: "22 px, once", examples: "lucide's default size; rare today" },
  { px: 32, role: "Illustrations", observed: "Not used", examples: "Reserved for empty-state or onboarding art" },
];

export function Sizing() {
  const [stroke, setStroke] = useState("default");
  return (
    <div className="space-y-section">
      <Block title="Size scale" aside={<ProvenanceChip kind="proposed">Proposed · not applied</ProvenanceChip>}>
        <Table caption="Proposed icon size scale and what the code uses today" minWidth="44rem">
          <THead><Tr><Th>Size</Th><Th>Role</Th><Th>Observed today</Th><Th>Where</Th></Tr></THead>
          <TBody>{SCALE.map((s) => (
            <Tr key={s.px}>
              <Td><span className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-control border border-dashed border-line-strong text-fg"><ShieldCheck size={s.px} aria-hidden /></span><code className="type-code">{s.px}px</code></span></Td>
              <Td className="font-medium text-fg">{s.role}</Td><Td><code className="type-code">{s.observed}</code></Td><Td className="text-fg-secondary">{s.examples}</Td>
            </Tr>
          ))}</TBody>
        </Table>
        <p className="type-caption text-fg-muted">
          Today&apos;s code uses nine sizes from 11 to 22 px in 1 px steps. The brief suggests 20 px for navigation and buttons, but the application uses 15–17 px there, so
          the proposal keeps navigation and buttons at 16 px. Moving the code onto five steps is later work; this task changes no size.
        </p>
      </Block>

      <Block title="Stroke consistency" aside={<ProvenanceChip kind="established">lucide default stroke 2</ProvenanceChip>}>
        <Tabs id="bk-stroke" label="Stroke behaviour" variant="pills" value={stroke} onChange={setStroke}
          items={[{ value: "default", label: "Default (scales with size)" }, { value: "absolute", label: "absoluteStrokeWidth (2 px at every size)" }]} />
        <TabPanel id="bk-stroke" value={stroke} className="">
          <div className="flex flex-wrap items-end gap-6 rounded-card border border-line bg-surface-card/80 p-card">
            {[12, 16, 20, 24, 32].map((s) => (
              <figure key={s} className="flex flex-col items-center gap-2 text-fg">
                <ShieldCheck size={s} absoluteStrokeWidth={stroke === "absolute"} aria-hidden />
                <figcaption className="type-caption text-fg-muted">{s}px · {stroke === "absolute" ? "2px" : `${(2 * s / 24).toFixed(2)}px`}</figcaption>
              </figure>
            ))}
          </div>
        </TabPanel>
        <ul className="list-disc space-y-1 pl-5 type-secondary text-fg-secondary">
          <li><ProvenanceChip kind="established">Established</ProvenanceChip> Icons use lucide&apos;s default stroke (2 on a 24-unit grid), so the stroke scales with the icon.</li>
          <li><ProvenanceChip kind="established">Established</ProvenanceChip> Small marks are drawn heavier so they stay legible: badge icons at 2.5 (components/ds/Status.tsx)
            and the ticks inside checkboxes and stepper steps at 3 (components/ds/Form.tsx, Navigation.tsx).</li>
          <li><ProvenanceChip kind="observed">Observed</ProvenanceChip> The sign-in page alone uses lighter or heavier strokes (1.75 three times, 2.25 once), with no stated reason.</li>
          <li><ProvenanceChip kind="proposed" /> Keep the default and the small-mark cases; avoid other stroke widths.</li>
        </ul>
      </Block>

      <Block title="Alignment, padding and icon-to-text spacing">
        <div className="grid gap-stack lg:grid-cols-2">
          <div className="space-y-3 rounded-card border border-line bg-surface-card/80 p-card">
            <Verdict ok>Do</Verdict>
            <p className="flex items-center gap-1 type-caption text-fg-secondary"><ShieldCheck size={12} aria-hidden />12 px icon · 4 px gap · caption</p>
            <p className="flex items-center gap-1.5 type-secondary text-fg"><ShieldCheck size={16} aria-hidden />16 px icon · 6 px gap · secondary text</p>
            <p className="flex items-center gap-2 type-body text-fg"><ShieldCheck size={20} aria-hidden />20 px icon · 8 px gap · body text</p>
            <p className="type-caption text-fg-muted">Centre icons on the text line (<code className="type-code">items-center</code>); the gap grows with the icon.</p>
          </div>
          <div className="space-y-3 rounded-card border border-line bg-surface-card/80 p-card">
            <Verdict ok={false}>Don&apos;t</Verdict>
            <p className="flex items-start gap-0 type-body text-fg"><ShieldCheck size={12} aria-hidden />Icon on the text top, no gap</p>
            <p className="flex items-end gap-4 type-caption text-fg"><ShieldCheck size={24} aria-hidden />Oversized icon on caption text</p>
            <p className="type-caption text-fg-muted">Top- or bottom-aligned icons look misplaced, and an icon far larger than its text overpowers it.</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 rounded-card border border-line bg-surface-card/80 p-card">
          {["h-6 w-6", "h-8 w-8", "h-10 w-10"].map((box, i) => (
            <span key={box} className="flex items-center gap-2 type-caption text-fg-secondary">
              <span className={`flex ${box} items-center justify-center rounded-control border border-line bg-surface-raised text-fg`}><ShieldCheck size={[12, 16, 20][i]} aria-hidden /></span>
              {[12, 16, 20][i]} px in a {[24, 32, 40][i]} px box
            </span>
          ))}
          <span className="type-caption text-fg-muted">Icon boxes keep the icon at half the box, leaving equal padding on every side.</span>
        </div>
      </Block>

      <Block title="Button icon positioning" aside={<ProvenanceChip kind="established">components/ds Button and IconButton</ProvenanceChip>}>
        <div className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-surface-card/80 p-card">
          <Button variant="primary" icon={Plus}>Leading icon: an action</Button>
          <Button iconRight={ArrowRight}>Trailing icon: goes somewhere</Button>
          <Button size="sm" icon={Download}>Small (15 px)</Button>
          <Button icon={RefreshCw} loading>Busy</Button>
          <IconButton icon={Trash2} label="Delete (icon-only button with a label)" />
          <StatusBadge status="verified" size="sm" />
        </div>
        <p className="type-caption text-fg-muted">
          A leading icon names the action; a trailing arrow says the button navigates. Icon-only buttons always carry an accessible name and a tooltip. The busy state
          replaces the icon with a spinner, so the button keeps its width.
        </p>
      </Block>
    </div>
  );
}
