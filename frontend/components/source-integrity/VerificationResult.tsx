import type { ReactNode } from "react";
import { FlaskConical } from "lucide-react";
import { DescriptionList } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import {
  CHECK_TYPE_LABEL, CheckResultBadge, ChecksumOutcomeBadge, DemoTag, Missing, SIGNATURE, SignatureBadge,
} from "./shared";
import type { RegulatorySource, VerificationCheck } from "./types";

/** The visible demonstration notice every verification result carries. */
export const DEMO_NOTICE = "Demonstration data — live verification is not connected.";

export function DemoNotice({ className = "" }: { className?: string }) {
  return (
    <p className={`flex items-start gap-2 rounded-control border border-dashed border-line-strong bg-fg/3 px-3 py-2 type-caption font-semibold text-fg-secondary ${className}`}>
      <FlaskConical size={14} aria-hidden className="mt-px shrink-0 text-fg-muted" />{DEMO_NOTICE}
    </p>
  );
}

/** A checksum shown whole (so it can be compared character by character), or why there is none. */
function Checksum({ value, none }: { value: string | null; none: string }) {
  return value ? <code className="block break-all rounded-control border border-line bg-surface-raised px-2 py-1.5 type-code text-fg">{value}</code> : <Missing>{none}</Missing>;
}

/** One check's result, as recorded in the demonstration data. Nothing here is computed: every outcome is a recorded
 *  value. `source` adds the source's name when the panel is shown away from its source, and `onOpenSource` makes that
 *  name open the source's details. (In a dialog it is then the first control, so the dialog opens at its top.) */
export function VerificationResult({ check, source, onOpenSource }: { check: VerificationCheck; source?: RegulatorySource; onOpenSource?: () => void }) {
  const pending = check.result === "PENDING";
  const sourceName = source && (onOpenSource ? (
    <button key="s" type="button" onClick={onOpenSource}
      className="rounded text-left font-medium text-accent-fg underline decoration-accent-fg/40 underline-offset-4 hover:decoration-accent-fg">
      {source.name}<span className="sr-only">: view source details</span>
    </button>
  ) : <span key="s" className="font-medium">{source.name}</span>);
  const items: [ReactNode, ReactNode][] = [
    ["Result", <CheckResultBadge key="r" result={check.result} labelled />],
    ["Check ID", <span key="i" className="inline-flex flex-wrap items-center gap-2"><code className="type-code">{check.id}</code><DemoTag /></span>],
    ...(sourceName ? [["Source", sourceName] as [ReactNode, ReactNode]] : []),
    ["Source version checked", <span key="v" className="type-code">{check.version}</span>],
    ["Check type", CHECK_TYPE_LABEL[check.type]],
    [pending ? "Queued" : "Verification timestamp",
      <time key="t" dateTime={check.checkedAt} className="tabular-nums">{fmtDate(check.checkedAt, true)}{pending ? " · not completed" : ""}</time>],
    ["Performed by", check.performedBy],
    ["Signature status", <span key="g" className="grid justify-items-start gap-1"><SignatureBadge status={check.signature} labelled />
      <span className="type-caption text-fg-muted">{SIGNATURE[check.signature].meaning}</span></span>],
  ];
  return (
    <div className="space-y-4">
      <DemoNotice />
      <DescriptionList columns={2} items={items} />
      <div className="grid gap-3">
        <div className="min-w-0">
          <p className="type-caption text-fg-muted">Expected checksum ({check.checksumAlgorithm}, as the publisher lists it)</p>
          <div className="mt-1"><Checksum value={check.expectedChecksum} none="The publisher lists no checksum for this version." /></div>
        </div>
        <div className="min-w-0">
          <p className="type-caption text-fg-muted">Observed checksum ({check.checksumAlgorithm}, of the content received)</p>
          <div className="mt-1"><Checksum value={check.observedChecksum} none="Not available: the content has not been read yet." /></div>
        </div>
        <div><ChecksumOutcomeBadge outcome={check.checksumOutcome} /></div>
      </div>
      <div>
        <p className="type-caption text-fg-muted">Explanation</p>
        <p className="mt-0.5 type-secondary text-fg">{check.explanation}</p>
      </div>
    </div>
  );
}
