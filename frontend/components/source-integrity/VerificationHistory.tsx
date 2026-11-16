"use client";

import { useId, useMemo, useState } from "react";
import { ChevronDown, History } from "lucide-react";
import { Button, EmptyState, Modal, TBody, THead, Table, TabPanel, Tabs, Td, Th, Tr } from "@/components/ds";
import { RegistrySection } from "@/components/PlatformAdmin";
import { count } from "@/components/platformQueue";
import { fmtDate } from "@/lib/format";
import { CHECK_RESULT, CHECK_TYPE_LABEL, CheckResultBadge, DemoTag, byNewest, fmtTimeUtc } from "./shared";
import { CHECK_RESULTS, type CheckResult, type RegulatorySource, type VerificationCheck } from "./types";
import { VerificationResult } from "./VerificationResult";

/* Verification history: every demonstration check, newest first, filtered by result. The first page shows STEP checks
   and "Show more" reveals the next STEP. Details opens the check's result in a dialog. */

const STEP = 8;
type Filter = "ALL" | CheckResult;

export function VerificationHistory({ checks, sources, onOpenSource }:
  { checks: VerificationCheck[]; sources: Map<string, RegulatorySource>; onOpenSource: (id: string) => void }) {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [shown, setShown] = useState(STEP);
  const [open, setOpen] = useState<VerificationCheck | null>(null);
  const sorted = useMemo(() => [...checks].sort(byNewest), [checks]);
  const rows = useMemo(() => sorted.filter((c) => filter === "ALL" || c.result === filter), [sorted, filter]);
  const visible = rows.slice(0, shown);
  const more = Math.min(STEP, rows.length - visible.length);
  const name = (c: VerificationCheck) => sources.get(c.sourceId)?.name ?? c.sourceId;
  const choose = (f: string) => { setFilter(f as Filter); setShown(STEP); };
  const openSource = (c: VerificationCheck) => { setOpen(null); onOpenSource(c.sourceId); };

  const tabs = [{ value: "ALL", label: "All", count: sorted.length },
    ...CHECK_RESULTS.map((r) => ({ value: r, label: CHECK_RESULT[r].label, count: sorted.filter((c) => c.result === r).length }))];

  return (
    <RegistrySection title="Verification history"
      aside={<span className="inline-flex items-center gap-2"><DemoTag /><span className="type-caption text-fg-muted">{count(sorted.length, "check")}</span></span>}
      description="Integrity checks recorded in the demonstration data, newest first. Details shows each check's full result.">
      <Tabs id="si-history" label="Filter checks by result" variant="pills" value={filter} onChange={choose} items={tabs} />
      <TabPanel id="si-history" value={filter} className="mt-4 rounded-card">
        {rows.length === 0 ? (
          filter === "ALL"
            ? <EmptyState compact icon={History} title="No checks recorded" description="No check has been recorded for any source yet." />
            : <EmptyState compact icon={History} title={`No ${CHECK_RESULT[filter].label.toLowerCase()} checks`}
              description="No check in the demonstration data has this result." />
        ) : (
          <>
            <ul aria-label="Verification checks" className="grid gap-3 md:grid-cols-2 xl:hidden">
              {visible.map((c) => (
                <li key={c.id}>
                  <article aria-label={`${c.id}, ${name(c)}`} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="break-words type-secondary font-semibold text-fg">{name(c)}</p>
                        <p className="type-code text-fg-muted">{c.id} · v{c.version}</p>
                      </div>
                      <span className="shrink-0"><CheckResultBadge result={c.result} size="sm" /></span>
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
                      <div className="min-w-0"><dt className="type-caption text-fg-muted">Check date</dt>
                        <dd className="type-secondary tabular-nums text-fg"><time dateTime={c.checkedAt}>{fmtDate(c.checkedAt, true)}</time></dd></div>
                      <div className="min-w-0"><dt className="type-caption text-fg-muted">Check type</dt><dd className="type-secondary text-fg">{CHECK_TYPE_LABEL[c.type]}</dd></div>
                      <div className="col-span-2 min-w-0"><dt className="type-caption text-fg-muted">Performed by</dt><dd className="type-secondary text-fg">{c.performedBy}</dd></div>
                    </dl>
                    <div className="mt-auto pt-3">
                      <Button size="sm" variant="secondary" onClick={() => setOpen(c)}>Details<span className="sr-only"> of check {c.id}</span></Button>
                    </div>
                  </article>
                </li>
              ))}
            </ul>
            <div className="hidden xl:block">
              <Table caption={`Verification history, ${filter === "ALL" ? "all results" : `${CHECK_RESULT[filter].label.toLowerCase()} checks`}, newest first (demonstration data).`} minWidth="54rem">
                <THead>
                  <tr>
                    <Th>Check ID</Th><Th>Source</Th><Th>Version</Th><Th>Check date</Th><Th>Check type</Th><Th>Result</Th><Th>Performed by</Th>
                    <Th><span className="sr-only">Details</span></Th>
                  </tr>
                </THead>
                <TBody>
                  {visible.map((c) => (
                    <Tr key={c.id}>
                      <Td className="align-top"><code className="whitespace-nowrap type-code">{c.id}</code></Td>
                      <Td className="align-top"><span className="break-words font-medium">{name(c)}</span></Td>
                      <Td className="align-top"><span className="type-code">{c.version}</span></Td>
                      <Td className="align-top whitespace-nowrap tabular-nums"><time dateTime={c.checkedAt}>{fmtDate(c.checkedAt)}<span className="block type-caption text-fg-muted">{fmtTimeUtc(c.checkedAt)}</span></time></Td>
                      <Td className="align-top">{CHECK_TYPE_LABEL[c.type]}</Td>
                      <Td className="align-top"><CheckResultBadge result={c.result} size="sm" /></Td>
                      <Td className="align-top text-fg-secondary">{c.performedBy}</Td>
                      <Td align="right" className="align-top"><Button size="sm" variant="secondary" onClick={() => setOpen(c)}>Details<span className="sr-only"> of check {c.id}</span></Button></Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <p className="type-caption text-fg-muted" aria-live="polite">Showing {visible.length} of {count(rows.length, "check")}</p>
              <div className="flex gap-2">
                {more > 0 && <Button size="sm" variant="secondary" onClick={() => setShown(shown + STEP)}>Show {more} more</Button>}
                {shown > STEP && <Button size="sm" variant="ghost" onClick={() => setShown(STEP)}>Show fewer</Button>}
              </div>
            </div>
          </>
        )}
      </TabPanel>

      {open && (
        <Modal open onClose={() => setOpen(null)} size="lg" title={`Check ${open.id}`}
          description={`${CHECK_TYPE_LABEL[open.type]} of ${name(open)}, version ${open.version}`}
          footer={<>
            <Button variant="secondary" onClick={() => setOpen(null)}>Close</Button>
            {sources.has(open.sourceId) && <Button variant="primary" onClick={() => openSource(open)}>View source details</Button>}
          </>}>
          <VerificationResult check={open} source={sources.get(open.sourceId)} onOpenSource={() => openSource(open)} />
        </Modal>
      )}
    </RegistrySection>
  );
}

/** One source's checks, newest first, each expanding in place to its full result (in the source details drawer). */
export function SourceCheckList({ checks }: { checks: VerificationCheck[] }) {
  if (checks.length === 0) {
    return <EmptyState compact icon={History} title="No checks recorded" description="This source has not been checked yet." />;
  }
  return <ol aria-label="Checks of this source, newest first" className="space-y-2">{checks.map((c) => <CheckDisclosure key={c.id} check={c} />)}</ol>;
}

function CheckDisclosure({ check }: { check: VerificationCheck }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <li className="rounded-card border border-line bg-surface-card">
      <button type="button" aria-expanded={open} aria-controls={`${id}-result`} onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-3 rounded-card px-3.5 py-3 text-left transition-colors hover:bg-fg/3">
        <span className="min-w-0 flex-1">
          <span className="block type-secondary font-semibold text-fg">{CHECK_TYPE_LABEL[check.type]} · version {check.version}</span>
          <span className="block type-caption text-fg-muted"><span className="type-code">{check.id}</span> · {fmtDate(check.checkedAt, true)}</span>
        </span>
        <CheckResultBadge result={check.result} size="sm" />
        <ChevronDown size={16} aria-hidden className={`mt-0.5 shrink-0 text-fg-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <div id={`${id}-result`} hidden={!open} className="border-t border-line px-3.5 py-4">
        {open && <VerificationResult check={check} />}
      </div>
    </li>
  );
}
