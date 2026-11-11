"use client";

import { Callout, STATUS_ICON, StateBadge, StatusBadge, StatusMark, TBody, THead, Table, Td, Th, Tr, statusLabel, statusOfCode } from "@/components/ds";
import { StatusPill, toneIcon, toneOf } from "@/components/ui";
import { STATUSES } from "@/lib/design-tokens";
import { ICONS } from "./iconRegistry";
import { Block, ProvenanceChip } from "./shared";

/* G. Status icons. Two layers, kept apart: the five visual statuses (lib/design-tokens.ts, components/ds/Status.tsx)
   and the backend's state codes, which are shown exactly as recorded. A code's visual family comes from the existing
   mapping (statusOfCode / toneOf); nothing here changes a code, its meaning or its mapping. */

type Tone = ReturnType<typeof toneOf>;
const nameOf = (Icon: unknown) => ICONS.find((i) => i.Icon === Icon)?.name ?? "?";
/** The legacy StatusPill tone that shows the same family on application screens today (lib/design-tokens.ts `current`). */
const LEGACY: Record<string, Tone> = { verified: "good", warning: "warn", critical: "bad", unknown: "unknown", info: "info" };

/** Business codes the brief names, where each appears, and the controlled dictionary's own wording where it has one
 *  (backend/app/codes.py; ATTESTED and DECLINED are attestation decisions, models.py, not a dictionary dimension). */
const CODES: { code: string; where: string; meaning: string }[] = [
  { code: "ATTESTED", where: "Adoption attestation decision", meaning: "Recorded when the attester adopts the determination." },
  { code: "DECLINED", where: "Adoption attestation decision", meaning: "Recorded when the attester rejects the determination (a reason is required)." },
  { code: "IN_REVIEW", where: "Determination state", meaning: "“Named accountable reviewer is assessing.”" },
  { code: "APPROVED", where: "Exception and compensation workflow", meaning: "“Active for exact scope and interval; never changes assurance state.”" },
  { code: "REJECTED", where: "Determination state; exception and compensation workflow", meaning: "Determination: “Customer did not adopt; reason mandatory.” Exception: “Denied.”" },
];

export function Status() {
  return (
    <div className="space-y-section">
      <Callout tone="info" title="Visual status is not a business state">
        The five statuses are a visual vocabulary. Backend codes such as ATTESTED or IN_REVIEW keep their exact names and meanings and are always printed; the status only
        sets the colour and icon beside them. Every status pairs an icon with a word, so colour and shape are never the only signal.
      </Callout>

      <Block title="Visual status vocabulary" aside={<ProvenanceChip kind="established">components/ds/Status.tsx</ProvenanceChip>}>
        <Table caption="Visual statuses with their icons and labels" minWidth="52rem">
          <THead><Tr><Th>Status</Th><Th>Design-system icon</Th><Th>Badge</Th><Th>Meaning</Th><Th>Application screens today</Th></Tr></THead>
          <TBody>{STATUSES.map((s) => {
            const Icon = STATUS_ICON[s.key];
            const legacy = toneIcon(null, LEGACY[s.key]);
            return (
              <Tr key={s.key}>
                <Td><span className="flex items-center gap-2.5"><StatusMark status={s.key} /><span className="font-medium text-fg">{s.label}</span></span></Td>
                <Td><span className="flex items-center gap-2 text-fg"><Icon size={16} aria-hidden /><code className="type-code">{nameOf(Icon)}</code></span></Td>
                <Td><span className="flex flex-wrap gap-1.5"><StatusBadge status={s.key} size="sm" /><StatusBadge status={s.key} /></span></Td>
                <Td className="text-fg-secondary">{s.meaning}</Td>
                <Td><span className="flex items-center gap-2"><span data-theme="light" className="rounded-chip bg-white px-1.5 py-1"><StatusPill tone={LEGACY[s.key]} label={s.label} /></span>
                  <code className="type-code text-fg-muted">{nameOf(legacy.Icon)}</code></span></Td>
              </Tr>
            );
          })}</TBody>
        </Table>
        <p className="type-caption text-fg-muted">
          Application screens still use the original status pills and their icons; the design-system icons apply in Design Governance and the shared components. Moving
          the pills over is a later task.
        </p>
      </Block>

      <Block title="Business state codes" aside={<ProvenanceChip kind="established">Codes as the backend records them</ProvenanceChip>}>
        <Table caption="Backend state codes and the visual family each is shown in" minWidth="56rem">
          <THead><Tr><Th>Code</Th><Th>Where it appears</Th><Th>Meaning</Th><Th>Design-system badge</Th><Th>Application pill today</Th></Tr></THead>
          <TBody>{CODES.map((c) => {
            const family = statusOfCode(c.code);
            const tone = toneOf(c.code);
            return (
              <Tr key={c.code}>
                <Td><code className="type-code font-semibold text-fg">{c.code}</code></Td>
                <Td className="text-fg-secondary">{c.where}</Td>
                <Td className="text-fg-secondary">{c.meaning}</Td>
                <Td><span className="flex flex-col items-start gap-1"><StateBadge code={c.code} size="sm" /><span className="type-caption text-fg-muted">{statusLabel(family)}</span></span></Td>
                <Td><span className="flex flex-col items-start gap-1"><span data-theme="light" className="rounded-chip bg-white px-1.5 py-1"><StatusPill code={c.code} /></span><span className="type-caption text-fg-muted">tone <code className="type-code">{tone}</code></span></span></Td>
              </Tr>
            );
          })}</TBody>
        </Table>
        {statusOfCode("DECLINED") === "unknown" && (
          <Callout tone="warning" title="DECLINED has no visual family yet">
            The interface&apos;s code mapping (components/ui.tsx <code className="type-code">toneOf</code>) does not list DECLINED, so it shows as Stale / Unknown: neutral and
            marked, as the dictionary rule for an unrecognised code requires. The code and its meaning are correct. Choosing its family is a product decision for a later
            task; this kit doesn&apos;t change the mapping.
          </Callout>
        )}
      </Block>
    </div>
  );
}
