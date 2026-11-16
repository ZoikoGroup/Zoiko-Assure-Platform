"use client";

import { useMemo, useState } from "react";
import { History } from "lucide-react";
import { Alert, Button, EmptyState, Field, Select, TBody, THead, Table, Td, Th, Tr } from "@/components/ds";
import { count } from "@/components/platformQueue";
import { fmtTimeUtc } from "@/components/platformDemo";
import { fmtDate } from "@/lib/format";
import { EventBadge, LocalTag } from "./badges";
import { EVENT, EVENT_KINDS, orgName, type EventKind, type JitEvent } from "./model";

/* The access history: events derived from the demonstration records, newest first. It is demonstration history, not
   the platform's audit trail: nothing here was read from, or written to, any audit record. */

const STEP = 10;

export function JitHistory({ events, onOpen }: { events: JitEvent[]; onOpen: (id: string) => void }) {
  const [kind, setKind] = useState<"" | EventKind>("");
  const [shown, setShown] = useState(STEP);
  const list = useMemo(() => events.filter((e) => !kind || e.kind === kind), [events, kind]);
  const visible = list.slice(0, shown);
  const more = Math.min(STEP, list.length - visible.length);

  return (
    <div className="space-y-4">
      <Alert tone="info" title="Demonstration history, not audit records">
        These events are derived from the fictional demonstration records on this page. They are not the platform&apos;s audit trail, and no audit
        record was read or written.
      </Alert>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="w-full sm:w-72">
          <Field label="Event type">
            <Select value={kind} onChange={(e) => { setKind(e.target.value as "" | EventKind); setShown(STEP); }}
              options={[{ value: "", label: "All events" }, ...EVENT_KINDS.map((k) => ({ value: k, label: EVENT[k].label }))]} />
          </Field>
        </div>
        <p className="type-caption text-fg-muted" aria-live="polite">Showing {visible.length} of {count(list.length, "event")}</p>
      </div>

      {list.length === 0 ? (
        <EmptyState compact icon={History} title="No events of this type" description="No demonstration event matches this filter." />
      ) : (
        <>
          <ol aria-label="Access history, newest first" className="space-y-2 xl:hidden">
            {visible.map((e) => (
              <li key={e.id} className="rounded-card border border-line bg-surface-card p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex flex-wrap items-center gap-1.5"><EventBadge kind={e.kind} size="sm" />{e.local && <LocalTag />}</span>
                  <time dateTime={e.at} className="type-caption tabular-nums text-fg-muted">{fmtDate(e.at, true)}</time>
                </div>
                <p className="mt-1.5 type-secondary text-fg">{e.description}</p>
                <p className="mt-1 type-caption text-fg-secondary">
                  {e.actor} · {orgName(e.organisation)} · <RequestLink id={e.requestId} onOpen={onOpen} />
                </p>
              </li>
            ))}
          </ol>
          <div className="hidden xl:block">
            <Table caption="Access history, newest first (demonstration data, not audit records)." minWidth="54rem">
              <THead>
                <tr><Th>Event time</Th><Th>Event</Th><Th>Actor</Th><Th>Organisation</Th><Th>Request</Th><Th>Description</Th></tr>
              </THead>
              <TBody>
                {visible.map((e) => (
                  <Tr key={e.id}>
                    <Td className="align-top whitespace-nowrap tabular-nums"><time dateTime={e.at}>{fmtDate(e.at)}<span className="block type-caption text-fg-muted">{fmtTimeUtc(e.at)}</span></time></Td>
                    <Td className="align-top"><span className="flex flex-col items-start gap-1"><EventBadge kind={e.kind} size="sm" labelled />{e.local && <LocalTag />}</span></Td>
                    <Td className="align-top text-fg-secondary">{e.actor}</Td>
                    <Td className="align-top text-fg-secondary">{orgName(e.organisation)}</Td>
                    <Td className="align-top"><RequestLink id={e.requestId} onOpen={onOpen} /></Td>
                    <Td className="align-top text-fg-secondary">{e.description}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {more > 0 && <Button size="sm" variant="secondary" onClick={() => setShown(shown + STEP)}>Show {more} more</Button>}
            {shown > STEP && <Button size="sm" variant="ghost" onClick={() => setShown(STEP)}>Show fewer</Button>}
          </div>
        </>
      )}
    </div>
  );
}

function RequestLink({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onOpen(id)}
      className="whitespace-nowrap rounded type-code text-accent-fg underline decoration-accent-fg/40 underline-offset-4 hover:decoration-accent-fg">
      {id}<span className="sr-only">: open request details</span>
    </button>
  );
}
