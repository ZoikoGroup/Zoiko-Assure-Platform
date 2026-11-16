"use client";

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { Button, DescriptionList } from "@/components/ds";
import { DemoTag, DrawerSection, Missing } from "@/components/platformDemo";
import { Drawer } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { CATEGORY, OUTCOME, SCOPE, type Row } from "./model";
import { ActionCode, CategoryBadge, FieldValue, OutcomeBadge, ScopeBadge } from "./shared";

/* One audit record, read from the records already on screen. The recorded fields are listed under the audit model's
   own names; the display data the model does not hold is kept apart and labelled illustrative. Previous and Next step
   through the list in the order it is shown, and sit at the top so the drawer opens there. */

const FieldName = ({ children }: { children: string }) => <code className="type-code">{children}</code>;

export function AuditEventDetails({ row, position, total, related, onPrev, onNext, onOpen, onClose }: {
  row: Row; position: number; total: number; related: Row[];
  onPrev: (() => void) | null; onNext: (() => void) | null; onOpen: (id: string) => void; onClose: () => void;
}) {
  const e = row.e;
  const prevRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  // Stepping to the first or last event disables the button that was pressed, and a disabled button loses focus. Keep
  // focus on the other step button then, rather than letting it fall back to the page.
  useEffect(() => {
    const a = document.activeElement;
    const lost = !a || a === document.body || (a instanceof HTMLButtonElement && a.disabled);
    if (lost) (onNext ? nextRef : prevRef).current?.focus();
  }, [position, onNext]);
  const fields: [ReactNode, ReactNode][] = [
    [<FieldName key="n">id</FieldName>, <FieldValue key="v" v={e.id} code />],
    [<FieldName key="n">record_seq</FieldName>, <span key="v" className="tabular-nums">{e.record_seq}</span>],
    [<FieldName key="n">recorded_at</FieldName>, <span key="v"><code className="break-all type-code">{e.recorded_at}</code><span className="block type-caption text-fg-muted">{fmtDate(e.recorded_at, true)}</span></span>],
    [<FieldName key="n">tenant_id</FieldName>, e.tenant_id ? <FieldValue key="v" v={e.tenant_id} code />
      : <span key="v"><FieldValue v={null} /><span className="block type-caption text-fg-muted">A platform-level record</span></span>],
    [<FieldName key="n">actor_id</FieldName>, <FieldValue key="v" v={e.actor_id} code />],
    [<FieldName key="n">actor_type</FieldName>, <FieldValue key="v" v={e.actor_type} code />],
    [<FieldName key="n">action</FieldName>, <ActionCode key="v" action={e.action} />],
    [<FieldName key="n">object_type</FieldName>, <FieldValue key="v" v={e.object_type} code />],
    [<FieldName key="n">object_id</FieldName>, <FieldValue key="v" v={e.object_id} code />],
    [<FieldName key="n">prior_state</FieldName>, <FieldValue key="v" v={e.prior_state} code />],
    [<FieldName key="n">new_state</FieldName>, <FieldValue key="v" v={e.new_state} code />],
    [<FieldName key="n">reason</FieldName>, <FieldValue key="v" v={e.reason} />],
    [<FieldName key="n">correlation_id</FieldName>, e.correlation_id
      ? <span key="v"><FieldValue v={e.correlation_id} code /><span className="block type-caption text-fg-muted">Illustrative value: the current audit writer does not set correlation_id.</span></span>
      : <FieldValue key="v" v={null} />],
  ];

  return (
    <Drawer open onClose={onClose} title={`Audit record #${e.record_seq}`} width="max-w-2xl">
      <div className="space-y-6">
        <div className="space-y-3">
          <nav aria-label="Step through audit events" className="flex flex-wrap items-center justify-between gap-2">
            <Button ref={prevRef} size="sm" variant="secondary" icon={ChevronLeft} disabled={!onPrev} onClick={() => onPrev?.()}>Previous</Button>
            <span className="type-caption tabular-nums text-fg-muted">{position ? `Event ${position} of ${total} in the current list` : "Outside the current filters"}</span>
            <Button ref={nextRef} size="sm" variant="secondary" iconRight={ChevronRight} disabled={!onNext} onClick={() => onNext?.()}>Next</Button>
          </nav>
          <div className="flex flex-wrap items-center gap-2">
            <ScopeBadge scope={row.scope} />
            <CategoryBadge category={row.category} />
            <OutcomeBadge outcome={row.outcome} />
            <DemoTag />
          </div>
          <p className="type-secondary text-fg-secondary">
            <span className="font-semibold text-fg">{row.actorName ?? "No actor recorded"}</span> · <ActionCode action={e.action} /> on {e.object_type}{" "}
            <code className="break-all type-code">{e.object_id}</code>
          </p>
          <p role="status" aria-live="polite" className="sr-only">Record {e.record_seq}, {e.action}{position ? `, event ${position} of ${total}` : ""}.</p>
        </div>

        {related.length > 0 && (
          <DrawerSection title="Records with the same correlation_id" aside={<span className="type-caption text-fg-muted">Illustrative</span>}>
            <ul className="space-y-2">
              {related.map((r) => (
                <li key={r.e.id} className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-line px-3 py-2">
                  <span className="min-w-0 type-secondary"><span className="tabular-nums text-fg-muted">#{r.e.record_seq}</span> <ActionCode action={r.e.action} /></span>
                  <Button size="sm" variant="ghost" onClick={() => onOpen(r.e.id)}>Open<span className="sr-only"> record {r.e.record_seq}</span></Button>
                </li>
              ))}
            </ul>
          </DrawerSection>
        )}

        {(e.prior_state !== null || e.new_state !== null) && (
          <DrawerSection title="State change">
            <p className="flex flex-wrap items-center gap-2 type-secondary">
              <span className="rounded-chip border border-line bg-surface-raised px-2 py-0.5"><FieldValue v={e.prior_state} code /></span>
              <ArrowRight size={15} aria-hidden className="text-fg-muted" /><span className="sr-only">changed to</span>
              <span className="rounded-chip border border-line bg-surface-raised px-2 py-0.5"><FieldValue v={e.new_state} code /></span>
            </p>
          </DrawerSection>
        )}

        <DrawerSection title="Reason">
          <p className="type-secondary text-fg"><FieldValue v={e.reason} /></p>
        </DrawerSection>

        <DrawerSection title="Recorded fields" aside={<span className="type-caption text-fg-muted">As named in the audit model (platform_audit_event)</span>}>
          <DescriptionList columns={2} items={fields} />
          <p className="mt-3 type-caption text-fg-muted">
            <span className="italic">null · not recorded</span> means the record holds no value; <span className="italic">empty value</span> means it holds an empty string.
          </p>
        </DrawerSection>

        <DrawerSection title="Illustrative display data" aside={<span className="type-caption text-fg-muted">Not part of the audit model</span>}>
          <DescriptionList columns={2} items={[
            ["Actor display name", row.actorName ?? <Missing key="a">No display name: no actor recorded</Missing>],
            ["Organisation name", e.tenant_id ? row.orgName ?? <Missing key="o">Not known</Missing> : <Missing key="o">None: a platform-level record</Missing>],
            ["Scope", <span key="s" className="grid justify-items-start gap-1"><ScopeBadge scope={row.scope} size="sm" labelled />
              <span className="type-caption text-fg-muted">{SCOPE[row.scope].meaning} Read from tenant_id.</span></span>],
            ["Event category", <span key="c" className="grid justify-items-start gap-1"><CategoryBadge category={row.category} size="sm" labelled />
              <span className="type-caption text-fg-muted">{CATEGORY[row.category].meaning} Grouped by action name.</span></span>],
            ["Outcome (demonstration only)", <span key="u" className="grid justify-items-start gap-1"><OutcomeBadge outcome={row.outcome} size="sm" labelled />
              <span className="type-caption text-fg-muted">{OUTCOME[row.outcome].meaning}</span></span>],
            ["Review (demonstration only)", row.review ?? <Missing key="r">Not flagged by the illustrative review rule</Missing>],
          ]} />
        </DrawerSection>

      </div>
    </Drawer>
  );
}
