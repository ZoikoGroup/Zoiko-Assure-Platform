"use client";

import { useEffect, useMemo, useState } from "react";
import { SearchX } from "lucide-react";
import { Button, EmptyState, RadioGroup, StatusMark } from "@/components/ds";
import { count } from "@/components/platformQueue";
import { fmtTimeUtc } from "@/components/platformDemo";
import { fmtDate } from "@/lib/format";
import { OUTCOME, type Row } from "./model";
import { ActionCode, CategoryBadge, OutcomeBadge } from "./shared";

/* The filtered records in time order (`rows`, already ordered by the page as `order` says), grouped by UTC day. The
   marker shows the demonstration-only outcome (a tick for succeeded, a warning for denied), always with its words
   beside it. */

const STEP = 15;
type Order = "newest" | "oldest";

export function AuditTimeline({ rows, order, onOrder, onOpen, onReset }:
  { rows: Row[]; order: Order; onOrder: (o: Order) => void; onOpen: (id: string) => void; onReset: () => void }) {
  const [shown, setShown] = useState(STEP);
  useEffect(() => setShown(STEP), [rows]);
  const ordered = rows;
  const visible = ordered.slice(0, shown);
  const days = useMemo(() => {
    const out: { day: string; items: Row[] }[] = [];
    for (const r of visible) {
      const day = r.e.recorded_at.slice(0, 10);
      if (out[out.length - 1]?.day === day) out[out.length - 1].items.push(r); else out.push({ day, items: [r] });
    }
    return out;
  }, [visible]);
  const more = Math.min(STEP, ordered.length - visible.length);

  return (
    <div className="space-y-4">
      <RadioGroup legend="Order" name="audit-timeline-order" orientation="horizontal" value={order} onChange={(v) => onOrder(v as Order)}
        options={[{ value: "newest", label: "Newest first" }, { value: "oldest", label: "Oldest first" }]} />
      {rows.length === 0 ? (
        <div className="rounded-card border border-dashed border-line-strong">
          <EmptyState icon={SearchX} title="No audit events match" description="Nothing to place on the timeline with this search and these filters."
            action={<Button size="sm" variant="secondary" onClick={onReset}>Reset filters</Button>} />
        </div>
      ) : (
        <>
          <ol aria-label={`Audit timeline, ${order === "newest" ? "newest" : "oldest"} first`} className="space-y-5">
            {days.map((d) => (
              <li key={d.day}>
                <h3 className="mb-2 type-label text-fg-muted"><time dateTime={d.day}>{fmtDate(`${d.day}T00:00:00Z`)}</time> · UTC</h3>
                <ol className="space-y-0">
                  {d.items.map((r, i) => (
                    <li key={r.e.id} className="relative flex gap-3 pb-4 last:pb-0">
                      {i < d.items.length - 1 && <span aria-hidden className="absolute bottom-0 left-4 top-9 w-px bg-line-strong" />}
                      <span className="pt-0.5"><StatusMark status={OUTCOME[r.outcome].tone} /></span>
                      <div className="min-w-0 flex-1 rounded-card border border-line bg-surface-card p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="flex min-w-0 flex-wrap items-center gap-2">
                            <time dateTime={r.e.recorded_at} className="type-secondary font-semibold tabular-nums text-fg">{fmtTimeUtc(r.e.recorded_at)}</time>
                            <ActionCode action={r.e.action} />
                          </span>
                          <span className="flex flex-wrap gap-1.5"><CategoryBadge category={r.category} size="sm" /><OutcomeBadge outcome={r.outcome} size="sm" /></span>
                        </div>
                        <p className="mt-1.5 type-caption text-fg-secondary">
                          <span className="font-semibold text-fg-muted">Actor </span>{r.actorName ?? "No actor recorded"}
                          <span className="font-semibold text-fg-muted"> · Object </span>{r.e.object_type} <code className="break-all type-code">{r.e.object_id}</code>
                          <span className="font-semibold text-fg-muted"> · Organisation </span>{r.e.tenant_id ? r.orgName ?? r.e.tenant_id : "Platform-level"}
                        </p>
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                          <span className="type-caption tabular-nums text-fg-muted">Record #{r.e.record_seq}</span>
                          <Button size="sm" variant="ghost" onClick={() => onOpen(r.e.id)}>Details<span className="sr-only"> of record {r.e.record_seq}</span></Button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="type-caption text-fg-muted" aria-live="polite">Showing {visible.length} of {count(ordered.length, "event")}</p>
            <div className="flex gap-2">
              {more > 0 && <Button size="sm" variant="secondary" onClick={() => setShown(shown + STEP)}>Show {more} more</Button>}
              {shown > STEP && <Button size="sm" variant="ghost" onClick={() => setShown(STEP)}>Show fewer</Button>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
