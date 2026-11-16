import {
  AlarmClock, Ban, CheckCircle2, Clock, FilePlus2, Hourglass, LockOpen, Play, ShieldOff, TimerOff, TimerReset, XCircle,
} from "lucide-react";
import type { BadgeMeta } from "@/components/platformDemo";
import type { AccessStatus, ApprovalStatus, JitData, JitRequest, JitRole, JitSession, Organisation } from "./types";

/* The JIT Access vocabulary, the illustrative rules the demonstration uses, and pure derivations (access status,
   remaining time, summary figures, history events). Everything is computed from the records and the fixed
   demonstration clock, so no figure on the page can disagree with the records. */

/* ------------------------------------------------------------------------------------------ illustrative rules */
/** ILLUSTRATIVE values, not business rules: each is pending product confirmation (PENDING_DECISIONS). */
export const ILLUSTRATIVE = {
  maxHours: 8,
  durations: [1, 2, 4, 8],
  extensionHours: [1, 2],
  expiringSoonMinutes: 60,
} as const;

/** Who a decision made on this page is attributed to. */
export const LOCAL_ACTOR = "You (local demonstration)";

export const PENDING_DECISIONS: { question: string; illustrative: string }[] = [
  { question: "Who is authorised to approve JIT requests?",
    illustrative: "Decisions in the records are attributed to a \"Platform security lead\" or \"Duty platform manager\" (both demo). The local demonstration lets you approve or reject as yourself." },
  { question: "Is JIT access platform-only, or can it cover customer-workspace access?",
    illustrative: "Every request here is a Platform Control Plane task about an organisation's registry record or lifecycle, or about platform diagnostics. None reaches into a customer's workspace data." },
  { question: "What is the maximum permitted access duration?",
    illustrative: `Requests offer 1, 2, 4 or 8 hours, with ${ILLUSTRATIVE.maxHours} hours as the ceiling, including extensions.` },
  { question: "Which approval and revocation rules apply?",
    illustrative: `An approver may shorten a request but not lengthen it; a rejection needs a reason; revocation ends access at once; extensions add 1 or 2 hours up to the ceiling; "expiring soon" means within ${ILLUSTRATIVE.expiringSoonMinutes} minutes.` },
];

/* --------------------------------------------------------------------------------------------------- vocabulary */
export const APPROVAL: Record<ApprovalStatus, BadgeMeta & { rank: number }> = {
  PENDING: { rank: 0, label: "Pending approval", tone: "warning", icon: Clock, meaning: "No decision yet: an approver must approve or reject the request." },
  APPROVED: { rank: 1, label: "Approved", tone: "verified", icon: CheckCircle2, meaning: "An approver approved the request (demonstration record)." },
  REJECTED: { rank: 2, label: "Rejected", tone: "critical", icon: XCircle, meaning: "An approver rejected the request; no access was granted." },
};

export const ACCESS: Record<AccessStatus, BadgeMeta & { rank: number }> = {
  EXPIRING_SOON: { rank: 0, label: "Expiring soon", tone: "warning", icon: AlarmClock,
    meaning: `Temporary access ends within ${ILLUSTRATIVE.expiringSoonMinutes} minutes of the demonstration clock.` },
  ACTIVE: { rank: 1, label: "Active", tone: "info", icon: LockOpen, meaning: "Temporary access is in effect at the demonstration clock." },
  AWAITING_APPROVAL: { rank: 2, label: "Awaiting approval", tone: "unknown", icon: Hourglass, meaning: "Access can start only after the request is approved." },
  REVOKED: { rank: 3, label: "Revoked", tone: "critical", icon: ShieldOff, meaning: "Temporary access was ended before its expiry." },
  EXPIRED: { rank: 4, label: "Expired", tone: "unknown", icon: TimerOff, meaning: "Temporary access ended at its expiry." },
  NOT_GRANTED: { rank: 5, label: "Not granted", tone: "unknown", icon: Ban, meaning: "The request was rejected, so access never started." },
};

export type EventKind = "REQUESTED" | "APPROVED" | "REJECTED" | "ACTIVATED" | "EXTENDED" | "EXPIRED" | "REVOKED";
export const EVENT: Record<EventKind, BadgeMeta & { order: number }> = {
  REQUESTED: { order: 0, label: "Access requested", tone: "info", icon: FilePlus2, meaning: "A staff member asked for temporary access." },
  APPROVED: { order: 1, label: "Approval granted", tone: "verified", icon: CheckCircle2, meaning: "An approver approved the request." },
  REJECTED: { order: 1, label: "Approval rejected", tone: "critical", icon: XCircle, meaning: "An approver rejected the request." },
  ACTIVATED: { order: 2, label: "Temporary access activated", tone: "info", icon: Play, meaning: "The temporary access started." },
  EXTENDED: { order: 3, label: "Access extended", tone: "warning", icon: TimerReset, meaning: "The expiry was moved later." },
  EXPIRED: { order: 4, label: "Access expired", tone: "unknown", icon: TimerOff, meaning: "The temporary access ended at its expiry." },
  REVOKED: { order: 4, label: "Access revoked", tone: "critical", icon: ShieldOff, meaning: "The temporary access was ended early." },
};
export const EVENT_KINDS = Object.keys(EVENT) as EventKind[];

/* ------------------------------------------------------------------------------------------------------ helpers */
const MIN = 60_000;
export const ms = (iso: string) => new Date(iso).getTime();
export const addHours = (iso: string, h: number) => new Date(ms(iso) + h * 60 * MIN).toISOString();
export const hoursLabel = (h: number) => `${h} hour${h === 1 ? "" : "s"}`;
export const orgName = (o: Organisation) => o?.name ?? "Platform-wide";

/** "50 min", "1 h 30 min", "2 h". */
export function fmtMinutes(m: number): string {
  const h = Math.floor(m / 60), r = m % 60;
  return h === 0 ? `${r} min` : r === 0 ? `${h} h` : `${h} h ${r} min`;
}

/** Hours of access granted so far: the approved hours plus every extension. */
export const grantedHours = (req: JitRequest, s: JitSession) => (req.decision?.approvedHours ?? 0) + s.extensions.reduce((a, e) => a + e.hours, 0);

export function accessOf(req: JitRequest, s: JitSession | null, nowMs: number): AccessStatus {
  if (req.status === "PENDING") return "AWAITING_APPROVAL";
  if (req.status === "REJECTED" || !s) return "NOT_GRANTED";
  if (s.revoked) return "REVOKED";
  const left = ms(s.expiresAt) - nowMs;
  if (left <= 0) return "EXPIRED";
  return left <= ILLUSTRATIVE.expiringSoonMinutes * MIN ? "EXPIRING_SOON" : "ACTIVE";
}

export const isActive = (a: AccessStatus) => a === "ACTIVE" || a === "EXPIRING_SOON";

/** Minutes left at the demonstration clock, or null when the access is not in effect. */
export function remainingMinutes(s: JitSession | null, access: AccessStatus, nowMs: number): number | null {
  return s && isActive(access) ? Math.max(0, Math.round((ms(s.expiresAt) - nowMs) / MIN)) : null;
}

/** One request with everything the screen derives about it. */
export type Row = {
  request: JitRequest; role: JitRole | undefined; session: JitSession | null; access: AccessStatus;
  /** When access ends or ended: the revocation time if revoked, else the expiry. Null when never granted. */
  endsAt: string | null;
  remaining: number | null;
  /** Changed on this page (local demonstration state). */
  local: boolean;
};

export function buildRows(data: JitData, nowMs: number): Row[] {
  const roles = new Map(data.roles.map((r) => [r.id, r]));
  const sessions = new Map(data.sessions.map((s) => [s.requestId, s]));
  return data.requests.map((request) => {
    const session = sessions.get(request.id) ?? null;
    const access = accessOf(request, session, nowMs);
    const local = !!(request.decision?.local || session?.local || session?.revoked?.local || session?.extensions.some((e) => e.local));
    return { request, role: roles.get(request.roleId), session, access, local,
      endsAt: session ? session.revoked?.at ?? session.expiresAt : null, remaining: remainingMinutes(session, access, nowMs) };
  });
}

export type Summary = { total: number; pending: number; active: number; expiringSoon: number; expired: number; revoked: number; rejected: number; approved: number };

export function summarise(rows: Row[]): Summary {
  const n = (f: (r: Row) => boolean) => rows.filter(f).length;
  return {
    total: rows.length,
    pending: n((r) => r.request.status === "PENDING"),
    approved: n((r) => r.request.status === "APPROVED"),
    rejected: n((r) => r.request.status === "REJECTED"),
    active: n((r) => isActive(r.access)),
    expiringSoon: n((r) => r.access === "EXPIRING_SOON"),
    expired: n((r) => r.access === "EXPIRED"),
    revoked: n((r) => r.access === "REVOKED"),
  };
}

/* ------------------------------------------------------------------------------------------------------ history */
export type JitEvent = {
  id: string; at: string; kind: EventKind; actor: string; organisation: Organisation; requestId: string; sessionId: string | null;
  description: string; local: boolean;
};

/** The demonstration history, derived from the records (newest first). It is not a platform audit record. */
export function buildEvents(rows: Row[], nowMs: number): JitEvent[] {
  const out: JitEvent[] = [];
  for (const { request: r, role, session: s } of rows) {
    const base = { organisation: r.organisation, requestId: r.id, sessionId: s?.id ?? null };
    const roleName = role?.label ?? r.roleId;
    out.push({ ...base, id: `${r.id}-requested`, at: r.requestedAt, kind: "REQUESTED", actor: r.requester.name, local: false,
      description: `Requested ${roleName} for ${hoursLabel(r.requestedHours)}.` });
    const d = r.decision;
    if (d && r.status === "APPROVED") {
      const shortened = d.approvedHours !== undefined && d.approvedHours < r.requestedHours;
      out.push({ ...base, id: `${r.id}-approved`, at: d.at, kind: "APPROVED", actor: d.by, local: !!d.local,
        description: `Approved for ${hoursLabel(d.approvedHours ?? r.requestedHours)}${shortened ? ` (requested ${hoursLabel(r.requestedHours)})` : ""}.` });
    }
    if (d && r.status === "REJECTED") {
      out.push({ ...base, id: `${r.id}-rejected`, at: d.at, kind: "REJECTED", actor: d.by, local: !!d.local, description: `Rejected: ${d.reason ?? "no reason recorded"}` });
    }
    if (!s) continue;
    out.push({ ...base, id: `${s.id}-activated`, at: s.startedAt, kind: "ACTIVATED", actor: "System (demo)", local: !!s.local,
      description: `${roleName} became active as ${s.id}.` });
    s.extensions.forEach((e, i) => out.push({ ...base, id: `${s.id}-extended-${i}`, at: e.at, kind: "EXTENDED", actor: e.by, local: !!e.local,
      description: `Extended by ${hoursLabel(e.hours)}.` }));
    if (s.revoked) {
      out.push({ ...base, id: `${s.id}-revoked`, at: s.revoked.at, kind: "REVOKED", actor: s.revoked.by, local: !!s.revoked.local,
        description: s.revoked.reason ? `Revoked before expiry: ${s.revoked.reason}` : "Revoked before expiry." });
    } else if (ms(s.expiresAt) <= nowMs) {
      out.push({ ...base, id: `${s.id}-expired`, at: s.expiresAt, kind: "EXPIRED", actor: "System (demo)", local: false,
        description: "Temporary access ended at its expiry." });
    }
  }
  return out.sort((a, b) => b.at.localeCompare(a.at) || EVENT[b.kind].order - EVENT[a.kind].order);
}
