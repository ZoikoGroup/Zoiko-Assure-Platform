"use client";

import { useId } from "react";
import { CheckCircle2, Eye, History, Library, XCircle } from "lucide-react";
import { KpiCard } from "@/components/ds";
import { count } from "@/components/platformQueue";
import { fmtDate } from "@/lib/format";
import { CHECK_RESULT, CHECK_TYPE_LABEL, DemoTag, byNewest, fmtTimeUtc } from "./shared";
import type { RegulatorySource, VerificationCheck } from "./types";

/* The summary figures, each counted from the demonstration records on the page (nothing is read separately). */
export function SourceSummaryCards({ sources, checks }: { sources: RegulatorySource[]; checks: VerificationCheck[] }) {
  const headingId = useId();
  const active = sources.filter((s) => s.status !== "INACTIVE");
  const verified = sources.filter((s) => s.status === "VERIFIED").length;
  const review = sources.filter((s) => s.status === "REVIEW_REQUIRED").length;
  const failed = checks.filter((c) => c.result === "FAILED");
  const failedSources = new Set(failed.map((c) => c.sourceId)).size;
  const latest = [...checks].sort(byNewest)[0];
  const latestSource = latest && sources.find((s) => s.id === latest.sourceId);

  return (
    <section aria-labelledby={headingId}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="type-secondary font-semibold text-fg-secondary">Integrity summary</h2>
        <DemoTag />
        <span className="type-caption text-fg-muted">Counted from the demonstration records below.</span>
      </div>
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <li><KpiCard label="Total sources" icon={Library} value={sources.length}
          hint={`${active.length} active · ${sources.length - active.length} inactive`} /></li>
        <li><KpiCard label="Verified sources" tone="verified" icon={CheckCircle2} value={verified}
          hint={`Of ${count(active.length, "active source")}: the current version passed its latest check`} /></li>
        <li><KpiCard label="Sources requiring review" tone="warning" icon={Eye} value={review}
          hint="A person must review a change before they are relied on" /></li>
        <li><KpiCard label="Failed verification checks" tone="critical" icon={XCircle} value={failed.length}
          hint={`Across ${count(failedSources, "source")} in the check history`} /></li>
        <li className="sm:col-span-2">
          <KpiCard label="Last verification activity" icon={History}
            value={latest ? <time dateTime={latest.checkedAt}>{fmtDate(latest.checkedAt)}</time> : "None"}
            hint={latest
              ? `${fmtTimeUtc(latest.checkedAt)} · ${latest.id} · ${CHECK_TYPE_LABEL[latest.type]} of ${latestSource?.name ?? latest.sourceId} · ${CHECK_RESULT[latest.result].label}`
              : "No check has been recorded"} />
        </li>
      </ul>
    </section>
  );
}
