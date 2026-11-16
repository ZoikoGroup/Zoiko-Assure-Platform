import { CircleHelp } from "lucide-react";
import { RegistrySection } from "@/components/PlatformAdmin";
import { PENDING_DECISIONS } from "./model";

/* The product decisions the backend and product review left open. The demonstration uses illustrative values for each,
   and says so; none of them is a business rule. */
export function JitPendingDecisions() {
  return (
    <RegistrySection title="Decisions pending product confirmation"
      aside={<span className="inline-flex items-center gap-1 rounded-chip border border-status-warning-line bg-status-warning-tint px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-status-warning">
        <CircleHelp size={11} aria-hidden />Not decided</span>}
      description="The rules this demonstration shows are illustrative values chosen to make it usable. None is an approved business rule.">
      <ul className="grid gap-3 md:grid-cols-2">
        {PENDING_DECISIONS.map((d) => (
          <li key={d.question} className="flex gap-3 rounded-card border border-dashed border-line-strong p-4">
            <CircleHelp size={17} aria-hidden className="mt-0.5 shrink-0 text-status-warning" />
            <div className="min-w-0">
              <p className="type-secondary font-semibold text-fg">{d.question}</p>
              <p className="mt-1 type-caption text-fg-secondary"><span className="font-semibold text-fg-muted">Illustrative in this demonstration: </span>{d.illustrative}</p>
              <p className="mt-1 type-caption font-semibold text-status-warning">Pending product confirmation</p>
            </div>
          </li>
        ))}
      </ul>
    </RegistrySection>
  );
}
