"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { DATA_CLASSES } from "@/lib/codes";

/* Small markers shared by the Org Workspace areas. */

/** Marks a field, filter or control the application does not have today: a design proposal, not a current capability. */
export const Proposed = ({ children = "Proposed", className = "ml-1.5" }: { children?: ReactNode; className?: string }) => (
  <span className={`${className} inline-flex rounded-chip border border-dashed border-status-info-line bg-status-info-tint px-1 type-caption font-semibold normal-case tracking-normal text-status-info`}>{children}</span>
);

/** A column header with the Proposed marker beneath it, so a no-wrap header stays narrow. */
export const ProposedHeader = ({ children }: { children: ReactNode }) => (
  <span className="inline-flex flex-col items-start gap-0.5">{children}<Proposed className="" /></span>
);

/** A data classification (ZA-DOM-001 §17.1) as recorded. It is a handling class, not a status, so it has no status colour. */
export const ClassChip = ({ code }: { code: string }) => (
  <span title={DATA_CLASSES[code] ?? code} className="inline-flex whitespace-nowrap rounded-chip border border-line bg-surface-control px-1.5 type-code text-fg-secondary">{code}</span>
);

/** A link to the full High-Assurance Flow that this area summarises. */
export const FlowLink = ({ flow, children }: { flow: string; children: ReactNode }) => (
  <Link href={`/design-system/flows#${flow}`} className="inline-flex items-center gap-1 type-secondary font-medium text-accent-fg underline-offset-4 hover:underline">
    {children}<ArrowUpRight size={14} aria-hidden />
  </Link>
);

/** A list beside its detail panel, side by side only from 1680px so the list keeps room for its columns (as in Super
 *  Admin Planes); narrower, the panel follows the list and choosing a row scrolls to it. */
export const SPLIT = "grid gap-stack min-[1680px]:grid-cols-[minmax(0,1fr)_25rem]";
export function useDetailPanel() {
  const ref = useRef<HTMLDivElement>(null);
  const reveal = () => setTimeout(() => ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0);
  return { ref, reveal };
}

/* Cross-area links: one area asks another to open with a record selected (evidence cited by a test run, the controls
   behind an obligation). The request is kept here until the target area mounts and reads it. */
const requested: Record<string, string> = {};
export const openIn = (go: (area: string) => void, area: string, record: string) => { requested[area] = record; go(area); };

/** The selected record of an area: the one another area asked for, else `fallback`. */
export function useSelection(area: string, fallback: string) {
  const [id, setId] = useState(() => requested[area] ?? fallback);
  useEffect(() => { delete requested[area]; }, [area]);
  return [id, setId] as const;
}

/** A record ID that opens another area of this page with that record selected. */
export const AreaLink = ({ id, area, go, label }: { id: string; area: string; go: (area: string) => void; label?: string }) => (
  <button type="button" onClick={() => openIn(go, area, id)} className="rounded-chip type-code text-accent-fg underline-offset-4 hover:underline" aria-label={label ?? `Open ${id}`}>{id}</button>
);
