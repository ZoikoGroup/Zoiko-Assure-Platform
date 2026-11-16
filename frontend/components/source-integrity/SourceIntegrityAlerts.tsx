"use client";

import { Button, StatusMark, type StatusKey } from "@/components/ds";
import { RegistrySection } from "@/components/PlatformAdmin";
import { count } from "@/components/platformQueue";
import { fmtDate } from "@/lib/format";
import { CHECK_TYPE_LABEL, DemoTag, IntegrityStatusBadge, isOutdated } from "./shared";
import type { RegulatorySource, VerificationCheck } from "./types";

/* Needs attention: the sources a Super Admin would look at first, grouped by why. Every entry comes from the
   demonstration data; selecting one opens the source's details. */

type Group = {
  key: string; title: string; tone: StatusKey; description: string;
  pick: (s: RegulatorySource) => boolean;
  /** One sentence on why this source is listed. */
  reason: (s: RegulatorySource, checks: VerificationCheck[]) => string;
};

const GROUPS: Group[] = [
  {
    key: "failed", title: "Failed demonstration checks", tone: "critical",
    description: "Sources whose latest check failed. They must not be relied on until the failure is resolved.",
    pick: (s) => s.status === "VERIFICATION_FAILED",
    reason: (_s, checks) => {
      const c = checks.find((x) => x.result === "FAILED");
      if (!c) return "The latest check failed.";
      const why = c.checksumOutcome === "MISMATCH" ? "checksum mismatch" : c.signature === "INVALID" ? "signature did not verify" : "check failed";
      return `${c.id} (${CHECK_TYPE_LABEL[c.type].toLowerCase()}) on ${fmtDate(c.checkedAt)}: ${why}.`;
    },
  },
  {
    key: "review", title: "Sources requiring review", tone: "warning",
    description: "A person must review a change before these sources are relied on.",
    pick: (s) => s.status === "REVIEW_REQUIRED",
    reason: (s) => s.statusNote ?? "A change needs review.",
  },
  {
    key: "outdated", title: "Outdated versions", tone: "warning",
    description: "The publisher has issued a newer version than the one the platform holds.",
    pick: isOutdated,
    reason: (s) => `Holds version ${s.currentVersion}; the publisher has issued ${s.publisherLatestVersion}.`,
  },
  {
    key: "pending", title: "Pending verification", tone: "info",
    description: "The current version has not completed a check yet.",
    pick: (s) => s.status === "PENDING_VERIFICATION",
    reason: (s, checks) => {
      const queued = checks.find((c) => c.result === "PENDING" && c.version === s.currentVersion);
      return queued ? `Version ${s.currentVersion} queued on ${fmtDate(queued.checkedAt)} (${queued.id}).` : `Version ${s.currentVersion} has not been checked.`;
    },
  },
];

export function SourceIntegrityAlerts({ sources, checksFor, onOpen }:
  { sources: RegulatorySource[]; checksFor: (id: string) => VerificationCheck[]; onOpen: (id: string) => void }) {
  return (
    <RegistrySection title="Needs attention" aside={<DemoTag />}
      description="Sources grouped by why they need a look. A source can appear in more than one group. Open a source for its details and checks.">
      <ul className="grid gap-4 md:grid-cols-2">
        {GROUPS.map((g) => <AlertGroup key={g.key} group={g} sources={sources.filter(g.pick)} checksFor={checksFor} onOpen={onOpen} />)}
      </ul>
    </RegistrySection>
  );
}

function AlertGroup({ group, sources, checksFor, onOpen }:
  { group: Group; sources: RegulatorySource[]; checksFor: (id: string) => VerificationCheck[]; onOpen: (id: string) => void }) {
  return (
    <li className="flex flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
      <div className="flex items-start gap-3">
        <StatusMark status={sources.length ? group.tone : "verified"} />
        <div className="min-w-0 flex-1">
          <h3 className="type-secondary font-semibold text-fg">
            {group.title}{" "}<span className="ml-1 rounded-full bg-fg/10 px-1.5 type-caption tabular-nums text-fg-secondary">
              {sources.length}<span className="sr-only"> {sources.length === 1 ? "source" : "sources"}</span></span>
          </h3>
          <p className="type-caption text-fg-muted">{group.description}</p>
        </div>
      </div>
      {sources.length === 0 ? (
        <p className="mt-3 type-secondary text-fg-secondary">None in the demonstration data.</p>
      ) : (
        <ul aria-label={`${group.title}: ${count(sources.length, "source")}`} className="mt-3 divide-y divide-line">
          {sources.map((s) => (
            <li key={s.id} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 py-2.5 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-[1_1_14rem]">
                <p className="break-words type-secondary font-medium text-fg">{s.name}</p>
                <p className="mt-0.5 type-caption text-fg-secondary">{group.reason(s, checksFor(s.id))}</p>
                <div className="mt-1.5"><IntegrityStatusBadge status={s.status} size="sm" /></div>
              </div>
              <Button size="sm" variant="secondary" onClick={() => onOpen(s.id)}>View<span className="sr-only"> details of {s.name}</span></Button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
