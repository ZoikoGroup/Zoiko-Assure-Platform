"use client";

import { useId, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { ArrowDown, ShieldCheck } from "lucide-react";
import { Alert, Button, DescriptionList } from "@/components/ds";
import { Drawer } from "@/components/ui";
import { count } from "@/components/platformQueue";
import { fmtDate } from "@/lib/format";
import { DemoTag, INTEGRITY_STATUS, IntegrityStatusBadge, Missing, SOURCE_TYPE_LABEL, isOutdated } from "./shared";
import type { RegulatorySource, VerificationCheck } from "./types";
import { DemoNotice } from "./VerificationResult";
import { SourceCheckList } from "./VerificationHistory";

/* One source, read-only, from the records already on screen: no further read. The drawer follows the existing Super
   Admin detail pattern (Users & Roles): focus moves into it, Escape and Close return focus to the button that opened it. */

const ALERT_TONE = { critical: "critical", warning: "warning", info: "info", verified: "info", unknown: "info" } as const;

function Section({ title, aside, headingRef, children }:
  { title: string; aside?: ReactNode; headingRef?: RefObject<HTMLHeadingElement | null>; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="scroll-mt-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 id={id} ref={headingRef} tabIndex={headingRef ? -1 : undefined} className="rounded type-secondary font-semibold text-fg">{title}</h4>{aside}
      </div>
      {children}
    </section>
  );
}

export function SourceDetails({ source, checks, onClose }: { source: RegulatorySource; checks: VerificationCheck[]; onClose: () => void }) {
  const status = INTEGRITY_STATUS[source.status];
  const lastCompleted = checks.find((c) => c.result !== "PENDING");
  const coveredOlder = !!lastCompleted && !!source.currentVersion && lastCompleted.version !== source.currentVersion;
  const hintId = useId();
  const historyHeading = useRef<HTMLHeadingElement>(null);
  // The dialog focuses the first control in its body when it opens. This shortcut is that control, so the drawer opens
  // at its top (status and demonstration notice) instead of scrolling down to the first check.
  const toHistory = () => {
    historyHeading.current?.scrollIntoView({ block: "start" });
    historyHeading.current?.focus({ preventScroll: true });
  };

  return (
    <Drawer open onClose={onClose} title={source.name} width="max-w-2xl">
      <div className="space-y-6">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <IntegrityStatusBadge status={source.status} />
            <code className="type-code text-fg-secondary">{source.id}</code>
            <DemoTag />
            <button type="button" onClick={toHistory}
              className="ml-auto inline-flex items-center gap-1 rounded type-caption font-semibold text-accent-fg underline-offset-4 hover:underline">
              Go to verification history ({count(checks.length, "check")})<ArrowDown size={13} aria-hidden />
            </button>
          </div>
          <DemoNotice />
          {source.statusNote && (
            <Alert tone={ALERT_TONE[status.tone]} title={status.label}>{source.statusNote}</Alert>
          )}
        </div>

        <Section title="Source">
          <DescriptionList columns={2} items={[
            ["Source name", source.name],
            ["Publisher", source.publisher],
            ["Source type", SOURCE_TYPE_LABEL[source.type]],
            ["Jurisdiction", source.jurisdiction.name],
            ["Current version", source.currentVersion ? <span key="c" className="type-code">{source.currentVersion}</span> : <Missing key="c">Not recorded</Missing>],
            ["Previous version", source.previousVersion ? <span key="p" className="type-code">{source.previousVersion}</span> : <Missing key="p">None: this is the first version recorded</Missing>],
            ["Publisher's latest version", source.publisherLatestVersion
              ? <span key="l"><span className="type-code">{source.publisherLatestVersion}</span>{isOutdated(source) && <span className="ml-2 type-caption font-semibold text-status-warning">Newer than the version held</span>}</span>
              : <Missing key="l">Not known</Missing>],
            ["Publication date", source.publishedAt ? fmtDate(source.publishedAt) : <Missing key="d">Not recorded</Missing>],
            ["Last verification", source.lastVerifiedAt
              ? <span key="v"><time dateTime={source.lastVerifiedAt} className="tabular-nums">{fmtDate(source.lastVerifiedAt, true)}</time>
                {coveredOlder && lastCompleted && <span className="block type-caption text-fg-muted">Covered version {lastCompleted.version}, not the current version</span>}</span>
              : <Missing key="v">Never verified</Missing>],
            ["Integrity status", <span key="s" className="grid justify-items-start gap-1"><IntegrityStatusBadge status={source.status} size="sm" labelled />
              <span className="type-caption text-fg-muted">{status.meaning}</span></span>],
          ]} />
        </Section>

        <Section title="Description">
          {source.description ? <p className="type-secondary text-fg-secondary">{source.description}</p> : <p className="type-secondary"><Missing>No description recorded.</Missing></p>}
        </Section>

        <Section title="Source reference">
          {source.references.length === 0 ? <p className="type-secondary"><Missing>No reference information recorded.</Missing></p> : (
            <dl className="divide-y divide-line rounded-card border border-line">
              {source.references.map((r) => (
                <div key={r.label} className="grid gap-0.5 px-3 py-2 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-3">
                  <dt className="type-caption text-fg-muted">{r.label}</dt>
                  {/* Shown as text, not a link: demonstration locations use the reserved .example domain. */}
                  <dd className="break-all type-code text-fg">{r.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </Section>

        <Section title="Verification history" headingRef={historyHeading} aside={<span className="type-caption text-fg-muted">{count(checks.length, "check")}</span>}>
          <SourceCheckList checks={checks} />
        </Section>

        <Section title="Run a verification">
          <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-line-strong p-3.5">
            <Button variant="secondary" icon={ShieldCheck} disabled aria-describedby={hintId}>Run verification</Button>
            <p id={hintId} className="type-caption text-fg-muted">
              Not available: live verification is not connected, so no check can be started from this screen. This control is disabled in the demonstration.
            </p>
          </div>
        </Section>
      </div>
    </Drawer>
  );
}
