"use client";

import { useId } from "react";
import { Download, ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ds";
import { RegistrySection } from "@/components/PlatformAdmin";
import { FINDINGS } from "./model";

/* How the platform's audit records are implemented today, and the export and verification the backend does not
   provide. The findings describe the code; they make no claim about the fictional records on this page, and nothing
   here scores or certifies integrity. */

export function AuditIntegrityPanel() {
  return (
    <RegistrySection title="Audit model and known limitations"
      aside={<span className="inline-flex items-center rounded-chip border border-line bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted">Current implementation</span>}
      description="Findings from a review of the platform's code, describing how its real audit records are written and kept today. They are not claims about the fictional records on this page, which were never written to any audit table.">
      <ul className="grid gap-3 md:grid-cols-2">
        {FINDINGS.map((f) => (
          <li key={f.title} className="flex gap-3 rounded-card border border-line bg-surface-card p-4">
            <f.icon size={17} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <p className="type-secondary font-semibold text-fg">{f.title}</p>
              <p className="mt-0.5 type-caption text-fg-secondary">{f.detail}</p>
              <p className="mt-1 type-caption font-semibold text-fg-muted">Current implementation finding</p>
            </div>
          </li>
        ))}
      </ul>
    </RegistrySection>
  );
}

/** Export and verification, shown for completeness but disabled: the backend provides neither. */
export function AuditExportControls() {
  const exportHint = useId();
  const verifyHint = useId();
  return (
    <RegistrySection title="Export and verification"
      description="Neither is available: the backend has no audit export and no audit verification endpoint, so both controls are disabled.">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-line-strong p-4">
          <Button variant="secondary" icon={Download} disabled aria-describedby={exportHint}>Export audit records</Button>
          <p id={exportHint} className="type-caption text-fg-muted">Not available: live audit export is not implemented. No file is generated and nothing is downloaded.</p>
        </div>
        <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-line-strong p-4">
          <Button variant="secondary" icon={ShieldQuestion} disabled aria-describedby={verifyHint}>Verify audit integrity</Button>
          <p id={verifyHint} className="type-caption text-fg-muted">
            Not available: live audit verification is unavailable. Audit records carry no hash chain or signature, so no cryptographic verification is possible.
          </p>
        </div>
      </div>
    </RegistrySection>
  );
}
