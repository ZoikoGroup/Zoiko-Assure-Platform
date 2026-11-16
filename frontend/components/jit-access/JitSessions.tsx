"use client";

import { useMemo, useState } from "react";
import type { RefObject } from "react";
import { ShieldOff, TimerOff, TimerReset } from "lucide-react";
import { Button, EmptyState, TabPanel, Tabs } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { AccessBadge, LocalTag } from "./badges";
import { ACCESS, fmtMinutes, isActive, orgName, type Row } from "./model";
import type { DemoAction } from "./JitDemoActionDialog";
import { useTabInView } from "./useTabInView";

/* Temporary access sessions (one per approved request), filtered by state. "Active" includes sessions expiring soon,
   as on the summary cards. Extend and Revoke change only the page's demonstration state. */

type View = "ALL" | "ACTIVE" | "EXPIRING_SOON" | "EXPIRED" | "REVOKED";
const VIEWS: { value: View; label: string; pick: (r: Row) => boolean }[] = [
  { value: "ALL", label: "All", pick: () => true },
  { value: "ACTIVE", label: "Active", pick: (r) => isActive(r.access) },
  { value: "EXPIRING_SOON", label: "Expiring soon", pick: (r) => r.access === "EXPIRING_SOON" },
  { value: "EXPIRED", label: "Expired", pick: (r) => r.access === "EXPIRED" },
  { value: "REVOKED", label: "Revoked", pick: (r) => r.access === "REVOKED" },
];

/** In effect first (least time left first), then ended sessions, most recent end first. */
const order = (a: Row, b: Row) => {
  const aa = isActive(a.access), ba = isActive(b.access);
  if (aa !== ba) return aa ? -1 : 1;
  return aa ? (a.remaining ?? 0) - (b.remaining ?? 0) : (b.endsAt ?? "").localeCompare(a.endsAt ?? "");
};

export function JitSessions({ rows, onOpen, onAction, panelRef }:
  { rows: Row[]; onOpen: (id: string) => void; onAction: (a: DemoAction) => void; panelRef: RefObject<HTMLDivElement | null> }) {
  const [view, setView] = useState<View>("ALL");
  useTabInView("jit-sessions", view);
  const sessions = useMemo(() => rows.filter((r) => r.session), [rows]);
  const list = useMemo(() => {
    const pick = VIEWS.find((v) => v.value === view)?.pick ?? (() => true);
    return sessions.filter(pick).sort(order);
  }, [sessions, view]);

  return (
    <div>
      <Tabs id="jit-sessions" label="Filter temporary access sessions" variant="pills" value={view} onChange={(v) => setView(v as View)}
        items={VIEWS.map((v) => ({ value: v.value, label: v.label, count: sessions.filter(v.pick).length }))} />
      <p className="mt-2 type-caption text-fg-muted">Active includes sessions expiring soon. Remaining time is measured at the demonstration clock.</p>
      <div ref={panelRef} tabIndex={-1} className="mt-4 rounded-card">
        <TabPanel id="jit-sessions" value={view} className="rounded-card">
          {list.length === 0 ? (
            <EmptyState compact icon={TimerOff} title="No sessions in this view" description="No demonstration session has this state." />
          ) : (
            <ul aria-label="Temporary access sessions" className="grid gap-4 lg:grid-cols-2">
              {list.map((r) => <li key={r.session?.id}><SessionCard r={r} onOpen={onOpen} onAction={onAction} /></li>)}
            </ul>
          )}
        </TabPanel>
      </div>
    </div>
  );
}

function SessionCard({ r, onOpen, onAction }: { r: Row; onOpen: (id: string) => void; onAction: (a: DemoAction) => void }) {
  const s = r.session;
  if (!s) return null;
  const q = r.request;
  const live = isActive(r.access);
  return (
    <article aria-label={`${s.id}, ${q.requester.name}`} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="min-w-0 flex-[1_1_12rem]">
          <p className="type-code text-fg">{s.id}</p>
          <p className="type-caption text-fg-muted">For request <span className="type-code">{q.id}</span></p>
        </div>
        <div className="flex flex-wrap gap-1.5"><AccessBadge status={r.access} size="sm" />{r.local && <LocalTag />}</div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <div className="min-w-0"><dt className="type-caption text-fg-muted">User</dt><dd className="break-words type-secondary text-fg">{q.requester.name}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Organisation</dt><dd className="type-secondary text-fg">{orgName(q.organisation)}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Granted role</dt><dd className="type-secondary text-fg">{r.role?.label ?? q.roleId}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Start</dt><dd className="type-secondary tabular-nums text-fg">{fmtDate(s.startedAt, true)}</dd></div>
        <div className="min-w-0"><dt className="type-caption text-fg-muted">Expiry</dt><dd className="type-secondary tabular-nums text-fg">{fmtDate(s.expiresAt, true)}</dd></div>
        <div className="min-w-0">
          <dt className="type-caption text-fg-muted">Remaining</dt>
          <dd className="type-secondary tabular-nums text-fg">
            {live && r.remaining !== null ? <span className="font-semibold">{fmtMinutes(r.remaining)}</span>
              : r.access === "REVOKED" && s.revoked ? <span className="text-fg-muted">None: revoked {fmtDate(s.revoked.at, true)}</span>
              : <span className="text-fg-muted">None: {ACCESS[r.access].label.toLowerCase()}</span>}
          </dd>
        </div>
      </dl>
      {s.extensions.length > 0 && (
        <p className="mt-2 type-caption text-fg-secondary">Extended {s.extensions.map((e) => `+${e.hours} h by ${e.by}`).join(", ")}.</p>
      )}
      {s.revoked && <p className="mt-2 type-caption text-fg-secondary">Revoked by {s.revoked.by}{s.revoked.reason ? `: ${s.revoked.reason}` : "."}</p>}
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
        {live && <>
          <Button size="sm" variant="secondary" icon={TimerReset} onClick={() => onAction({ kind: "extend", sessionId: s.id })}>Extend (demo)…<span className="sr-only"> {s.id}</span></Button>
          <Button size="sm" variant="danger" icon={ShieldOff} onClick={() => onAction({ kind: "revoke", sessionId: s.id })}>Revoke (demo)…<span className="sr-only"> {s.id}</span></Button>
        </>}
        <Button size="sm" variant="secondary" onClick={() => onOpen(q.id)}>Request details<span className="sr-only"> of {q.id}</span></Button>
      </div>
    </article>
  );
}
