"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { KeyRound, RotateCcw } from "lucide-react";
import { Alert, Button, EmptyState, ErrorState, Skeleton, TabPanel, Tabs } from "@/components/ds";
import { RegistryHeading, RegistryScope, RegistrySection } from "@/components/PlatformAdmin";
import { useSyntheticDataBadge } from "@/components/Shell";
import { DemoTag } from "@/components/platformDemo";
import { usePageTitle } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { DEMO_NOW } from "./jitMockData";
import { buildEvents, buildRows, ms, summarise } from "./model";
import { JitApprovals } from "./JitApprovals";
import { JitDemoActionDialog, type AppliedAction, type DemoAction } from "./JitDemoActionDialog";
import { JitHistory } from "./JitHistory";
import { JitPendingDecisions } from "./JitPendingDecisions";
import { JitRequestDetails } from "./JitRequestDetails";
import { JitRequestInventory } from "./JitRequestInventory";
import { JitSessions } from "./JitSessions";
import { JitSummaryCards } from "./JitSummaryCards";
import { useJitAccessData } from "./useJitAccessData";
import { useTabInView } from "./useTabInView";

/* JIT Access, for the Zoiko Super Admin (Platform Control Plane): a demonstration of temporary, justified, approved
   privileged access for platform staff.

   FRONTEND ONLY, ON DEMONSTRATION DATA. No JIT, break-glass or support-access model or endpoint exists. Every record
   comes from jitMockData.ts through useJitAccessData.ts, which makes no network request; approve, reject, revoke and
   extend change only that hook's in-memory copy and are labelled as local demonstration state everywhere. No session,
   role-management, audit or other endpoint is called, and nothing is recorded. */
const TITLE = "JIT Access";
export const DEMO_NOTICE = "Demonstration interface – JIT access is not connected to live backend functionality.";

const TABS = [
  { value: "requests", label: "Requests" },
  { value: "approvals", label: "Approvals" },
  { value: "sessions", label: "Sessions" },
  { value: "history", label: "History" },
];
const TAB_INTRO: Record<string, string> = {
  requests: "Every request, with its approval and access status. Search, filter and sort them, then open a request for its details.",
  approvals: "The approval workflow, as a demonstration. Pending requests are oldest first; Approve and Reject change only this page.",
  sessions: "Temporary access sessions: the access each approved request produced. Extend and Revoke change only this page.",
  history: "Access history: what happened to each request, newest first. Select a request ID to open its details.",
};

export function PlatformJitAccess() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  useSyntheticDataBadge();  // the header's "Synthetic demo data" badge, while this page is open

  const { data, error, loading, changes, actions, reload } = useJitAccessData();
  const nowMs = ms(DEMO_NOW);
  const rows = useMemo(() => (data ? buildRows(data, nowMs) : []), [data, nowMs]);
  const events = useMemo(() => buildEvents(rows, nowMs), [rows, nowMs]);
  const summary = useMemo(() => summarise(rows), [rows]);
  const [tab, setTab] = useState("requests");
  useTabInView("jit-view", tab);
  const [openId, setOpenId] = useState<string | null>(null);
  const [action, setAction] = useState<DemoAction | null>(null);
  const open = openId ? rows.find((r) => r.request.id === openId) : undefined;
  const firstLoad = loading && !data;

  // Focus after a demonstration action. The dialog returns focus to the button that opened it; when the action removed
  // that button (Approve, once approved), focus goes to a stable place in the same area instead of the page body.
  const opener = useRef<HTMLElement | null>(null);
  const fallback = useRef<HTMLElement | null>(null);
  const drawerActions = useRef<HTMLDivElement>(null);
  const approvalsPanel = useRef<HTMLDivElement>(null);
  const sessionsPanel = useRef<HTMLDivElement>(null);
  const startAction = useCallback((target: RefObject<HTMLElement | null>) => (a: DemoAction) => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    fallback.current = target.current;
    setAction(a);
  }, []);
  const closeAction = () => {
    setAction(null);
    window.setTimeout(() => { if (!opener.current?.isConnected) fallback.current?.focus(); }, 0);
  };
  const apply = (a: AppliedAction) => {
    if (a.kind === "approve") actions.approve(a.requestId, a.hours);
    else if (a.kind === "reject") actions.reject(a.requestId, a.reason);
    else if (a.kind === "revoke") actions.revoke(a.sessionId, a.reason);
    else actions.extend(a.sessionId, a.hours);
  };
  const latest = changes[changes.length - 1];

  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title={TITLE}
        actions={<div className="flex flex-wrap items-center gap-x-4 gap-y-2 xl:justify-end">
          <p className="type-caption text-fg-muted" title="Remaining time, expiry and every count on this page are computed against this fixed time, not the real clock.">
            Demonstration clock (fixed): <time dateTime={DEMO_NOW} className="font-semibold tabular-nums text-fg-secondary">{fmtDate(DEMO_NOW, true)}</time>
          </p>
          <Button variant="secondary" icon={RotateCcw} onClick={actions.reset} disabled={changes.length === 0}>Reset demonstration</Button>
        </div>}>
        <p>Temporary, justified and approved privileged access for Zoiko platform staff: who asked, for which organisation, why, for how long, who
          decided, and when the access ends.</p>
      </RegistryHeading>
      <p role="status" aria-live="polite" className="sr-only">{latest ? `Local demonstration change: ${latest.text}` : ""}</p>

      <RegistryScope>
        <span className="font-semibold text-fg">{DEMO_NOTICE}</span> Every person, organisation, request and session here is fictional. Approving,
        rejecting, revoking or extending changes only this page&apos;s local demonstration state: no access is granted or removed, no request is
        created, and nothing is sent or recorded. Times are measured against a fixed demonstration clock.
      </RegistryScope>

      {changes.length > 0 && (
        <Alert tone="warning" title={`Local demonstration changes (${changes.length})`}>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5">{changes.map((c) => <li key={c.id}>{c.text}</li>)}</ol>
          <p className="mt-2">These exist only on this page and are lost on reload. Records they changed are marked Local demo.</p>
          <Button size="sm" variant="secondary" icon={RotateCcw} onClick={actions.reset} className="mt-2">Reset demonstration</Button>
        </Alert>
      )}

      {firstLoad ? <JitLoading /> : !data ? (
        <RegistrySection title="JIT access records">
          <ErrorState error={error} title="The demonstration data could not be loaded" onRetry={reload} />
        </RegistrySection>
      ) : rows.length === 0 ? (
        <RegistrySection title="JIT access records">
          <EmptyState icon={KeyRound} title="No JIT requests to show"
            description="Requests from platform staff appear here once they are made. (This is the empty state, previewed with demonstration data.)" />
        </RegistrySection>
      ) : (
        <>
          <JitSummaryCards s={summary} local={changes.length > 0} />
          <RegistrySection title="JIT access records" aside={<DemoTag />}
            description="Requests, approvals, temporary sessions and their history, all fictional demonstration records.">
            <Tabs id="jit-view" label="JIT access views" value={tab} onChange={setTab} items={TABS} />
            <TabPanel id="jit-view" value={tab} className="rounded-card pt-4">
              <p className="mb-4 max-w-3xl type-caption text-fg-secondary">{TAB_INTRO[tab]}</p>
              {tab === "requests" && <JitRequestInventory rows={rows} roles={data.roles} onOpen={setOpenId} />}
              {tab === "approvals" && <JitApprovals rows={rows} onOpen={setOpenId} onAction={startAction(approvalsPanel)} panelRef={approvalsPanel} />}
              {tab === "sessions" && <JitSessions rows={rows} onOpen={setOpenId} onAction={startAction(sessionsPanel)} panelRef={sessionsPanel} />}
              {tab === "history" && <JitHistory events={events} onOpen={setOpenId} />}
            </TabPanel>
          </RegistrySection>
        </>
      )}

      <JitPendingDecisions />

      {open && (
        <JitRequestDetails key={open.request.id} row={open} events={events.filter((e) => e.requestId === open.request.id)}
          onAction={startAction(drawerActions)} onClose={() => setOpenId(null)} actionsRef={drawerActions} />
      )}
      {action && <JitDemoActionDialog action={action} rows={rows} onApply={apply} onClose={closeAction} />}
    </div>
  );
}

function JitLoading() {
  return (
    <div role="status" aria-label="Loading the demonstration data" className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="space-y-3 rounded-card border border-line bg-surface-card p-5">
            <Skeleton className="h-4 w-32" /><Skeleton className="h-8 w-16" /><Skeleton className="h-3.5 w-3/4" />
          </div>
        ))}
      </div>
      <div className="rounded-panel border border-line bg-surface-raised/70 p-5 sm:p-6">
        <Skeleton className="h-5 w-56" />
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="mt-4 h-10 w-full" />)}
      </div>
    </div>
  );
}
