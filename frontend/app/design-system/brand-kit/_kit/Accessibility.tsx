"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Bell, Download, Info, ShieldCheck, Trash2 } from "lucide-react";
import { Button, IconButton, StatusBadge, TBody, THead, Table, Td, Th, Tooltip, Tr } from "@/components/ds";
import { Logo } from "@/components/Shell";
import { Block, ProvenanceChip, Verdict, ratio, useThemeVars, type ThemeName } from "./shared";

/* H. Accessibility and contrast. Demonstrations use the real components; contrast is computed from the token values in
   both themes; the audit counts the icons actually rendered on this page. */

/** Icon colours checked as non-text graphics (WCAG 2.2 1.4.11, 3:1) on the surfaces they sit on. */
const PAIRS: [string, string, string][] = [
  ["Primary text icons", "--za-fg", "--za-surface-card"], ["Secondary icons", "--za-fg-secondary", "--za-surface-card"], ["Muted icons", "--za-fg-muted", "--za-surface-card"],
  ["Accent icons (workspace)", "--za-accent-fg", "--za-surface-card"], ["Secondary accent icons (platform)", "--za-accent-secondary-fg", "--za-surface-card"],
  ["Sidebar icons", "--za-sidebar-fg", "--za-surface-sidebar"], ["Focus ring", "--za-focus", "--za-surface-app"],
  ["Verified icon on its tint", "--za-status-verified", "--za-status-verified-tint"], ["Needs Review icon on its tint", "--za-status-warning", "--za-status-warning-tint"],
  ["Critical icon on its tint", "--za-status-critical", "--za-status-critical-tint"], ["Stale / Unknown icon on its tint", "--za-status-unknown", "--za-status-unknown-tint"],
  ["Informational icon on its tint", "--za-status-info", "--za-status-info-tint"],
];
const VARS = [...new Set(PAIRS.flatMap(([, f, b]) => [f, b]).concat("--za-surface-card"))];

type Audit = { total: number; decorative: number; named: number; unnamed: number };
/** Counts the lucide icons rendered on the page: hidden from assistive technology, named, or exposed without a name. */
function auditIcons(): Audit {
  const svgs = [...document.querySelectorAll("svg.lucide")];
  let decorative = 0, named = 0, unnamed = 0;
  for (const s of svgs) {
    if (s.closest('[aria-hidden="true"]')) decorative++;
    else if (s.getAttribute("aria-label") || s.getAttribute("aria-labelledby") || s.querySelector("title")) named++;
    else unnamed++;
  }
  return { total: svgs.length, decorative, named, unnamed };
}

export function Accessibility() {
  const vals = useThemeVars(VARS);
  const [audit, setAudit] = useState<Audit | null>(null);
  useEffect(() => { const t = setTimeout(() => setAudit(auditIcons()), 300); return () => clearTimeout(t); }, []);

  return (
    <div className="space-y-section">
      <Block title="Labelling icons" aside={<ProvenanceChip kind="established">Pattern used across components/ds</ProvenanceChip>}>
        <div className="grid gap-stack lg:grid-cols-3">
          <Demo title="Decorative: hidden" code={`<ShieldCheck size={16} aria-hidden /> Verified evidence`}>
            <p className="flex items-center gap-1.5 type-secondary text-fg"><ShieldCheck size={16} aria-hidden />Verified evidence</p>
            <p className="type-caption text-fg-muted">The text says it all, so the icon is hidden from screen readers.</p>
          </Demo>
          <Demo title="Icon-only control: named" code={`<IconButton icon={Trash2} label="Delete draft" />`}>
            <div className="flex items-center gap-2"><Tooltip content="Delete draft"><IconButton icon={Trash2} label="Delete draft" /></Tooltip>
              <Tooltip content="Notifications, 2 open items"><IconButton icon={Bell} label="Notifications, 2 open items" /></Tooltip></div>
            <p className="type-caption text-fg-muted">The button carries the name; a tooltip shows it on hover and keyboard focus.</p>
          </Demo>
          <Demo title="Meaningful image: named" code={`<span role="img" aria-label="Zoiko Assure" …/>`}>
            <div className="flex items-center gap-3 rounded-control bg-white p-2" data-theme="light"><Logo width={110} /></div>
            <p className="type-caption text-fg-muted">The logo component is announced once as “Zoiko Assure”.</p>
          </Demo>
          <Demo title="Extra context: screen-reader-only text" code={`<Download aria-hidden /> Export <span className="sr-only">tokens as JSON</span>`}>
            <Button icon={Download}>Export<span className="sr-only"> tokens as JSON</span></Button>
            <p className="type-caption text-fg-muted">Sighted users see “Export”; screen readers hear the full purpose.</p>
          </Demo>
          <Demo title="Status: never icon or colour alone" code={`<StatusBadge status="warning" />`}>
            <div className="flex flex-wrap items-center gap-2">
              <Verdict ok>Do</Verdict><StatusBadge status="warning" size="sm" />
              <Verdict ok={false}>Don&apos;t</Verdict><span aria-hidden className="h-3 w-3 rounded-full bg-status-warning" />
            </div>
            <p className="type-caption text-fg-muted">A lone coloured dot fails people who can&apos;t see colour; the badge adds an icon and a word.</p>
          </Demo>
          <Demo title="Keyboard and focus" code={`:focus-visible → outline var(--za-focus)`}>
            <div className="flex flex-wrap gap-2"><Button size="sm">Tab to me</Button><IconButton size="sm" icon={Info} label="More information" /></div>
            <p className="type-caption text-fg-muted">Press Tab: every control shows the focus ring in this theme&apos;s focus colour, checked to 3:1 below.</p>
          </Demo>
        </div>
      </Block>

      <Block title="Icon contrast, both themes" aside={<ProvenanceChip kind="established">Task 1 standard: 3:1 for non-text</ProvenanceChip>}>
        <Table caption="Contrast of icon colours on their surfaces in the light and dark themes" minWidth="40rem">
          <THead><Tr><Th>Icon colour</Th><Th>Token on surface</Th><Th align="right">Light</Th><Th align="right">Dark</Th></Tr></THead>
          <TBody>{PAIRS.map(([label, fg, bg]) => (
            <Tr key={label}>
              <Td className="font-medium text-fg">{label}</Td>
              <Td><code className="type-code text-fg-secondary">{fg} on {bg}</code></Td>
              {(["light", "dark"] as ThemeName[]).map((t) => {
                const r = vals ? ratio(vals[t][fg], vals[t][bg], vals[t]["--za-surface-card"]) : null;
                return <Td key={t} align="right">{r === null ? "…" : <span data-ratio={r.toFixed(2)} className={`font-semibold tabular-nums ${r >= 3 ? "text-status-verified" : "text-status-critical"}`}>{r.toFixed(2)}:1</span>}</Td>;
              })}
            </Tr>
          ))}</TBody>
        </Table>
        <p className="type-caption text-fg-muted">An icon beside text of the same colour also meets that text&apos;s 4.5:1, which Foundations checks for every text token.</p>
      </Block>

      <Block title="Icons on this page right now" aside={<ProvenanceChip kind="observed">Counted from the rendered page</ProvenanceChip>}>
        {audit ? (
          <ul className="grid gap-3 sm:grid-cols-4" aria-label="Icon accessibility audit">
            <AuditTile label="Icons rendered" value={audit.total} />
            <AuditTile label="Hidden (decorative)" value={audit.decorative} />
            <AuditTile label="Named" value={audit.named} />
            <AuditTile label="Exposed without a name" value={audit.unnamed} bad={audit.unnamed > 0} />
          </ul>
        ) : <p className="type-secondary text-fg-muted">Counting…</p>}
        <p className="type-caption text-fg-muted">An icon exposed without a name would be read as an unlabelled image. The count covers the page shell and this tab.</p>
      </Block>
    </div>
  );
}

function Demo({ title, code, children }: { title: string; code: string; children: ReactNode }) {
  return (
    <figure className="flex flex-col gap-3 rounded-card border border-line bg-surface-card/80 p-card">
      <figcaption className="type-secondary font-semibold text-fg">{title}</figcaption>
      {children}
      <pre className="mt-auto overflow-x-auto rounded-control border border-line bg-surface-control p-2 type-code text-fg-secondary">{code}</pre>
    </figure>
  );
}

const AuditTile = ({ label, value, bad = false }: { label: string; value: number; bad?: boolean }) => (
  <li className="rounded-card border border-line bg-surface-card/80 p-3">
    <p className="type-caption text-fg-muted">{label}</p>
    <p className={`type-page font-bold tabular-nums ${bad ? "text-status-critical" : "text-fg"}`}>{value}</p>
  </li>
);
