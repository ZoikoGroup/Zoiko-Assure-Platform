"use client";

import { User } from "lucide-react";

/* Evidence supplied by a person rather than captured from a system (ZA-EVD-001 §12, FR-EVD-003: manual evidence is
   visibly lower provenance). These are exactly the origin classes the backend's control evaluation treats as manual: on
   their own they cannot satisfy a control that requires automation (reason MANUAL_EVIDENCE_INSUFFICIENT). This marks
   provenance only. It says nothing about condition, integrity, classification or lifecycle, and it is read from the
   recorded `origin_class`, never inferred from titles, file names or other fields. */
const MANUAL_ORIGINS = new Map([["HUMAN_UPLOAD", "Manual upload"], ["ATTESTATION", "Human attestation"]]);
const MANUAL_NOTE = "Supplied by a person rather than captured from a system. On its own it cannot satisfy a control that requires automation.";

/** Provenance marker for manually supplied evidence; renders nothing for other origins. `note` adds the visible explanation. */
export function ManualProvenance({ origin, note = false }: { origin?: string | null; note?: boolean }) {
  const label = origin ? MANUAL_ORIGINS.get(origin) : undefined;
  if (!label) return null;
  return (
    <span className="inline-flex flex-col items-start gap-0.5 align-middle">
      <span title={`${label} (${origin}). ${MANUAL_NOTE}`}
        className="inline-flex items-center gap-1 whitespace-nowrap rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300">
        <User size={12} aria-hidden className="shrink-0" />{label}
      </span>
      {note && <span className="text-[11px] text-slate-500">{MANUAL_NOTE}</span>}
    </span>
  );
}
