"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Building2, CircleAlert, Eye, UserRound } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { STATUS_TONE } from "./ds/Status";

/* The Platform Action Queue's one implementation (Milestone 3): its reads, rules, order and refresh, shared by the
   Action Queue page (PlatformActionQueue.tsx) and the Platform Command Center (PlatformCommandCenter.tsx), so the two
   can never disagree about what is in the queue. Items are derived in the browser from two existing reads and nothing
   else:

     GET /api/platform/organisations  the organisation registry (manage_organisations, platform human only). Registry
                                      metadata: name, status, legal entities, cells, Organisation Admins. Each read is
                                      recorded by the server as platform.organisation_registry_read.
     GET /api/sessions                the signed-in person's own live sessions (self-service).

   Three rules, each evaluated on the values those reads return:
     1. An ACTIVE organisation with no current Organisation Admin (admins is empty) .......... Action required
     2. A SUSPENDED organisation ............................................................. Review
     3. A live session of your own account that is not the one making the request ........... Review

   Nothing is stored: an item is listed while its condition holds and drops out once it no longer does. There are no
   owners, due dates, priorities or statuses of the queue's own, and no write action: every action is taken on its
   existing page through its existing, audited dialog. Customer evidence, facts, assurance records and an organisation's
   own audit content are never read or shown (INV-14, IAM-REQ-024). The queue never refreshes itself, because every
   registry read is an audit record. */
export const REGISTRY = "GET /api/platform/organisations";
export const SESSIONS = "GET /api/sessions";

export type Org = { tenant_id: string; legal_name: string; status: string; created_at: string; entities: unknown[]; admins: unknown[] };
export type Session = { session_id: string; created_at: string; expires_at: string; user_agent: string | null; current: boolean };

export type Classification = "ACTION_REQUIRED" | "REVIEW";
export type Category = "tenant" | "account";
export type QueueItem = {
  key: string;
  classification: Classification;
  category: Category;
  title: string;
  organisation?: { id: string; name: string };
  why: string;
  rule: string;
  observed: [string, ReactNode][];
  source: string;
  action: { label: string; href: string; hint: string };
};

export const CLASSIFICATION: Record<Classification, { label: string; meaning: string; tone: string; icon: LucideIcon }> = {
  ACTION_REQUIRED: { label: "Action required", tone: STATUS_TONE.warning, icon: CircleAlert,
    meaning: "a gap only the Super Admin can close, with an existing action" },
  REVIEW: { label: "Review", tone: STATUS_TONE.info, icon: Eye,
    meaning: "a deliberate state, or activity on your own account, to check" },
};
export const CATEGORY: Record<Category, { label: string; icon: LucideIcon }> = {
  tenant: { label: "Tenant governance", icon: Building2 },
  account: { label: "Your account", icon: UserRound },
};
const ORDER: Record<Classification, number> = { ACTION_REQUIRED: 0, REVIEW: 1 };

/* ------------------------------------------------------------------------------------------------------------ rules */
export const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function organisationItems(orgs: Org[]): QueueItem[] {
  const out: QueueItem[] = [];
  for (const o of orgs) {
    const facts: [string, ReactNode][] = [
      ["Status", <code key="s" className="type-code">{o.status}</code>],
      ["Organisation Admins", String(o.admins.length)],
      ["Legal entities", String(o.entities.length)],
      ["Organisation registered", fmtDate(o.created_at, true)],
    ];
    const organisation = { id: o.tenant_id, name: o.legal_name };
    const href = `/organisations/${encodeURIComponent(o.tenant_id)}`;
    if (o.status === "ACTIVE" && o.admins.length === 0) {
      out.push({
        key: `no-admin:${o.tenant_id}`, classification: "ACTION_REQUIRED", category: "tenant", organisation,
        title: "Active organisation without an Organisation Admin",
        why: "Its people can sign in, but nobody administers the organisation: only an Organisation Admin can appoint its security "
          + "administrators, and only the Super Admin can appoint an Organisation Admin.",
        rule: "Organisation status is ACTIVE and it has no current Organisation Admin (role TENANT_OWNER).",
        observed: facts, source: REGISTRY,
        action: { label: "Go to organisation record", href,
          hint: "Use Appoint Organisation Admin there. It issues a one-time password and is recorded as organisation.admin_appointed." },
      });
    } else if (o.status === "SUSPENDED") {
      out.push({
        key: `suspended:${o.tenant_id}`, classification: "REVIEW", category: "tenant", organisation,
        title: "Suspended organisation",
        why: "Its people cannot sign in. Check that the suspension is still intended; when and why it was suspended is in the "
          + "organisation record's administrative history.",
        rule: "Organisation status is SUSPENDED.",
        observed: facts, source: REGISTRY,
        action: { label: "Go to organisation record", href,
          hint: "Use Reactivate there. It needs a reason and is recorded as organisation.status_changed." },
      });
    }
  }
  return out;
}

export function sessionItems(sessions: Session[]): QueueItem[] {
  return sessions.filter((s) => !s.current).map((s) => ({
    key: `session:${s.session_id}`, classification: "REVIEW", category: "account",
    title: "Another session is signed in to your account",
    why: "This live session belongs to your Super Admin account but is not the one you are using now. If you don't recognise it, end it.",
    rule: "A live session of your account (not ended, not expired) that is not the session making this request.",
    observed: [
      ["Signed in", fmtDate(s.created_at, true)],
      ["Expires", fmtDate(s.expires_at, true)],
      ["Device", s.user_agent ? <span key="d" className="break-all">{s.user_agent}</span> : "Not recorded"],
    ],
    source: SESSIONS,
    action: { label: "Go to Your sessions", href: "/admin/users",
      hint: "Users & Roles › Your sessions › End session. Ending it is recorded as auth.session_revoked." },
  }));
}

/** Action required first, then Review; within a classification by organisation name (items about your own account
 *  last), then by key, so the order is stable between refreshes. */
export const byQueueOrder = (a: QueueItem, b: QueueItem) => ORDER[a.classification] - ORDER[b.classification]
  || (a.organisation?.name ?? "￿").localeCompare(b.organisation?.name ?? "￿") || a.key.localeCompare(b.key);

/** Every item the loaded sources give, in queue order. A source that did not load contributes nothing. */
export function queueItems(orgs: Org[] | null, sessions: Session[] | null): QueueItem[] {
  return [...(orgs ? organisationItems(orgs) : []), ...(sessions ? sessionItems(sessions) : [])].sort(byQueueOrder);
}

/* ------------------------------------------------------------------------------------------------------------- data */
type Load = { orgs: Org[] | null; sessions: Session[] | null; orgError: unknown; sessionError: unknown };
const NOTHING: Load = { orgs: null, sessions: null, orgError: null, sessionError: null };

/** UTC, to the second: the platform's operating time zone, as the header clock shows. */
export function fmtUtcSeconds(iso: string) {
  return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", timeZone: "UTC" }) + " UTC";
}
export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : "The request did not complete.");

/** The sources that failed on the latest refresh, by name, with the read each one makes. */
export function failedSources(q: Load): { name: string; source: string; error: unknown }[] {
  return [q.orgError && { name: "Organisation registry", source: REGISTRY, error: q.orgError },
    q.sessionError && { name: "Your sessions", source: SESSIONS, error: q.sessionError }].filter(Boolean) as { name: string; source: string; error: unknown }[];
}

export type PlatformQueue = Load & {
  /** The loaded sources' items, in queue order (byQueueOrder). */
  items: QueueItem[];
  /** A read is in flight: opening the page, Refresh or Retry. */
  loading: boolean;
  /** The first read is in flight and nothing has answered yet. */
  firstLoad: boolean;
  /** Both sources loaded on the latest refresh, so `items` is the whole queue. */
  complete: boolean;
  /** When both sources last loaded together (ISO), or null. */
  lastSuccess: string | null;
  /** Polite status text for a live region: refresh started, finished or incomplete. */
  announcement: string;
  refresh: () => Promise<void>;
};

/** Both reads, together, only when asked: on mounting the hook and on Refresh or Retry. Never on a timer. */
export function usePlatformQueue(): PlatformQueue {
  const [data, setData] = useState<Load>(NOTHING);
  const [loading, setLoading] = useState(true);
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const seq = useRef(0);
  const refresh = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    setAnnouncement("Refreshing the Action Queue…");
    const [o, s] = await Promise.allSettled([api<Org[]>("/platform/organisations"), api<Session[]>("/sessions")]);
    if (mine !== seq.current) return;  // a newer refresh has started; its answer wins
    const next: Load = {
      orgs: o.status === "fulfilled" ? o.value : null, orgError: o.status === "rejected" ? o.reason : null,
      sessions: s.status === "fulfilled" ? s.value : null, sessionError: s.status === "rejected" ? s.reason : null,
    };
    setData(next);
    setLoading(false);
    if (next.orgs && next.sessions) {
      const at = new Date().toISOString();
      setLastSuccess(at);
      const n = queueItems(next.orgs, next.sessions).length;
      setAnnouncement(`Action Queue refreshed at ${fmtUtcSeconds(at)}: ${count(n, "item")}.`);
    } else {
      setAnnouncement("The Action Queue could not be fully refreshed. Retry is available.");
    }
  }, []);
  // Once per mount. React's development double-invocation re-runs effects on the same instance, so the ref keeps
  // that from making (and auditing) a second registry read.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void refresh();
  }, [refresh]);
  const items = useMemo(() => queueItems(data.orgs, data.sessions), [data.orgs, data.sessions]);
  const firstLoad = loading && !data.orgs && !data.sessions && !data.orgError && !data.sessionError;
  return { ...data, items, loading, firstLoad, complete: !!(data.orgs && data.sessions), lastSuccess, announcement, refresh };
}

/* ----------------------------------------------------------------------------------------------------------- badges */
/** An item's classification, as a labelled chip (colour is never the only signal). What it means is not a hover title:
 *  each screen that shows the chip says it in visible text (CLASSIFICATION's meanings, or the item's own reason). */
export function ClassificationBadge({ classification }: { classification: Classification }) {
  const c = CLASSIFICATION[classification];
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border px-2 py-0.5 type-caption font-semibold ${c.tone}`}>
      <c.icon size={12} strokeWidth={2.5} aria-hidden className="shrink-0" /><span className="sr-only">Classification: </span>{c.label}
    </span>
  );
}

/** An item's category, as a neutral labelled chip. */
export function CategoryBadge({ category }: { category: Category }) {
  const cat = CATEGORY[category];
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-chip border border-line bg-fg/5 px-2 py-0.5 type-caption font-medium text-fg-secondary">
      <cat.icon size={12} aria-hidden className="shrink-0" /><span className="sr-only">Category: </span>{cat.label}
    </span>
  );
}
