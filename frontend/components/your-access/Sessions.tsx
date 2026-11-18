"use client";

import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { Info, LogOut } from "lucide-react";
import { api, errText } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { describeUserAgent } from "@/lib/device";
import { fmtDate, fmtDuration } from "@/lib/format";
import { Alert, Button, DescriptionList, EmptyState, ErrorState, Field, Modal, Skeleton, Textarea } from "../ds";
import { RegistrySection } from "../PlatformAdmin";
import { useAction, useToast } from "../ui";

/* Sessions on Users & Roles, shared by your own sessions (Your Access, both views) and a security administrator's view
   of a member's sessions (the principal drawer in app/admin/users/page.tsx). ZA-IAM-001 §05, §23: the server lists only
   sessions the caller may see, ends a session with POST /sessions/{id}/revoke (its own principal, or the organisation's
   security administrator with a reason), audits it as auth.session_revoked, and refuses to end the session making the
   request (409). The session in use is therefore never offered an End button here. */

/** A live session as GET /sessions and GET /principals/{id}/sessions return it: never the token or its hash. */
export type Session = {
  session_id: string; principal_id: string; created_at: string; expires_at: string; user_agent: string | null; current: boolean;
};
export type Loaded<T> = { data: T | null; error: unknown; loading: boolean; reload: () => unknown };

/** The time now, re-read every `ms`, for "time left" (display only: the server enforces expiry). Null until mounted, so
 *  the server-rendered and first client render agree. */
function useNow(ms = 30_000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(timer);
  }, [ms]);
  return now;
}

const timeLeft = (expiresAt: string, now: number) => {
  const left = fmtDuration(new Date(expiresAt).getTime() - now);
  return left === "expired" ? "Expired" : `${left} left`;
};

/** Loading, failure, empty and list states for a set of sessions; `onEnd` is offered for every session but the one
 *  in use. */
export function SessionsBody({ sessions, label, emptyTitle, onEnd, now }:
  { sessions: Loaded<Session[]>; label: string; emptyTitle: string; onEnd: (s: Session) => void; now: number | null }) {
  const rows = sessions.data;
  if (!rows && sessions.error) {
    return <ErrorState compact error={sessions.error} title="The sessions could not be loaded" onRetry={() => { void sessions.reload(); }} />;
  }
  if (!rows) {
    return (
      <div role="status" aria-label={`Loading ${label.toLowerCase()}`} className="space-y-3">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-card border border-line bg-surface-card p-4">
            <Skeleton className="h-4 w-52" />
            <Skeleton className="mt-2 h-3.5 w-3/4" />
          </div>
        ))}
      </div>
    );
  }
  if (rows.length === 0) return <EmptyState compact title={emptyTitle} />;
  return (
    <ul aria-label={label} className="space-y-3">
      {rows.map((s) => {
        const device = describeUserAgent(s.user_agent);
        return (
          <li key={s.session_id} className="flex flex-col gap-3 rounded-card border border-line bg-surface-card p-4 md:flex-row md:items-center md:justify-between">
            <DescriptionList columns={3} className="min-w-0 flex-1" items={[
              ["Signed in", fmtDate(s.created_at, true)],
              ["Expires", <span key="x">{fmtDate(s.expires_at, true)}
                {now !== null && <span className="block type-caption text-fg-muted">{timeLeft(s.expires_at, now)}</span>}</span>],
              ["Device", <span key="d">{device.label}
                {s.user_agent && <span className="mt-0.5 block break-all type-caption text-fg-muted">{s.user_agent}</span>}</span>],
            ]} />
            <div className="shrink-0">
              {s.current
                ? <span className="inline-flex items-center gap-1 rounded-chip border border-status-info-line bg-status-info-tint px-2 py-0.5 type-caption font-semibold text-status-info">
                    <Info size={12} aria-hidden />This session</span>
                : <Button size="sm" variant="secondary" icon={LogOut} onClick={() => onEnd(s)}>End session</Button>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Your own live sessions, with the expiry of the one in use and the time left on it. */
export function YourSessions({ sessions }: { sessions: Loaded<Session[]> }) {
  const { sessionExpiresAt } = useAuth();
  const now = useNow();
  const [target, setTarget] = useState<Session | null>(null);
  const rows = sessions.data;
  const expiresAt = sessionExpiresAt ?? rows?.find((s) => s.current)?.expires_at ?? null;
  return (
    <RegistrySection title="Your sessions" description="Every place you are signed in. End any session you do not recognise; to end this one, sign out."
      aside={rows && <p className="type-caption text-fg-muted">{rows.length === 1 ? "1 live session" : `${rows.length} live sessions`}</p>}>
      {expiresAt && (
        <p className="mb-4 rounded-card border border-line bg-surface-card px-4 py-3 type-secondary text-fg">
          This session expires <span className="font-semibold">{fmtDate(expiresAt, true)}</span>
          {now !== null && <> · <span className="font-semibold">{timeLeft(expiresAt, now)}</span></>}. You will then be asked to sign in again.
        </p>
      )}
      <SessionsBody sessions={sessions} label="Your sessions" emptyTitle="No live sessions" onEnd={setTarget} now={now} />
      {target && <EndSessionDialog session={target} mode="own" onClose={() => setTarget(null)} onDone={() => { void sessions.reload(); }} />}
    </RegistrySection>
  );
}

/** A security administrator's view of a member's live sessions, ending one with a recorded reason. */
export function MemberSessions({ sessions }: { sessions: Loaded<Session[]> }) {
  const now = useNow();
  const [target, setTarget] = useState<Session | null>(null);
  return (
    <>
      <SessionsBody sessions={sessions} label="Active sessions" emptyTitle="No live sessions" onEnd={setTarget} now={now} />
      {target && <EndSessionDialog session={target} mode="admin" onClose={() => setTarget(null)} onDone={() => { void sessions.reload(); }} />}
    </>
  );
}

/** POST /sessions/{id}/revoke. "own": your own session, reason optional. "admin": another person's session, by the
 *  organisation's security administrator, reason required (the server refuses one under 3 characters with 422). */
export function EndSessionDialog({ session, mode, onClose, onDone }:
  { session: Session; mode: "own" | "admin"; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAction();
  const toast = useToast();
  const formId = useId();
  if (session.current) return null;  // the session in use is ended by signing out, never from here
  const trimmed = reason.trim();
  const ready = mode === "own" || trimmed.length >= 3;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (a.busy || !ready) return;
    void a.run(async () => {
      await api(`/sessions/${encodeURIComponent(session.session_id)}/revoke`, { method: "POST", json: trimmed ? { reason: trimmed } : {} });
      toast("Session ended", "good");
      onDone();
      onClose();
    });
  };
  return (
    <Modal open onClose={onClose} title="End session" size="sm" footer={<>
      <Button variant="secondary" onClick={onClose}>Cancel</Button>
      <Button type="submit" form={formId} variant="danger" loading={a.busy} disabled={!ready}>{a.busy ? "Ending…" : "End session"}</Button>
    </>}>
      <form id={formId} onSubmit={submit} className="space-y-4">
        {!!a.error && <Alert tone="critical" live="assertive">{errText(a.error)}</Alert>}
        <p>
          The session signed in {fmtDate(session.created_at, true)} on {describeUserAgent(session.user_agent).label} stops working immediately.
          {mode === "admin" && " The person is not told why; the reason is recorded in the audit trail."}
        </p>
        {mode === "own"
          ? <Field label="Reason" optional><Textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          : <Field label="Reason" required hint="At least 3 characters. Recorded in the audit trail.">
              <Textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
      </form>
    </Modal>
  );
}
