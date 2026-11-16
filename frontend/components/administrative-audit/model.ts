import type { LucideIcon } from "lucide-react";
import {
  Ban, Building2, CheckCircle2, Clock, Database, EyeOff, FileDown, FileLock2, Fingerprint, Hash, KeyRound, Link2Off, ListOrdered,
  LockKeyhole, Server, ShieldAlert, Settings2, UserCog, Users, Workflow,
} from "lucide-react";
import type { BadgeMeta } from "@/components/platformDemo";
import type { AuditData, AuditEventRecord } from "./types";

/* The Administrative Audit vocabulary and pure derivations. Scope comes from the record (tenant_id); category, outcome
   and the review flag are ILLUSTRATIVE groupings computed from the action name, because the audit model stores none of
   them. Every figure on the page is counted from the same records through these functions. */

/* --------------------------------------------------------------------------------------------------------- scope */
export type Scope = "PLATFORM" | "ORGANISATION";
/** Source-backed: an empty tenant_id is a platform-level record; a populated one is tenant-associated. */
export const scopeOf = (e: AuditEventRecord): Scope => (e.tenant_id ? "ORGANISATION" : "PLATFORM");
export const SCOPE: Record<Scope, BadgeMeta> = {
  PLATFORM: { label: "Platform", tone: "info", icon: Server, meaning: "tenant_id is empty: a platform-level record." },
  ORGANISATION: { label: "Organisation", tone: "unknown", icon: Building2, meaning: "tenant_id is set: a record associated with one organisation." },
};

/* ------------------------------------------------------------------------------------------------------ category */
export const CATEGORIES = ["PLATFORM_ADMIN", "PLATFORM_REGISTRY", "PLATFORM_ACCESS", "PLATFORM_SECURITY", "ORG_ADMIN", "ORG_ROLES", "ORG_CONFIG", "ORG_OTHER"] as const;
export type Category = (typeof CATEGORIES)[number];

export const CATEGORY: Record<Category, BadgeMeta & { scope: Scope }> = {
  PLATFORM_ADMIN: { scope: "PLATFORM", label: "Platform administration", tone: "info", icon: Settings2, meaning: "Content, catalogue and connector administration across the platform." },
  PLATFORM_REGISTRY: { scope: "PLATFORM", label: "Organisation registry", tone: "info", icon: Database, meaning: "Reads of the organisation registry by the Super Admin." },
  PLATFORM_ACCESS: { scope: "PLATFORM", label: "Administrative access", tone: "info", icon: KeyRound, meaning: "Sign-ins, sign-outs and sessions of Zoiko platform staff." },
  PLATFORM_SECURITY: { scope: "PLATFORM", label: "Platform security", tone: "warning", icon: ShieldAlert, meaning: "Refused or failed attempts with no organisation: denied endpoints and failed sign-ins." },
  ORG_ADMIN: { scope: "ORGANISATION", label: "Organisation administration", tone: "unknown", icon: Building2, meaning: "Changes to an organisation's record, including those the Super Admin made." },
  ORG_ROLES: { scope: "ORGANISATION", label: "Organisation roles", tone: "unknown", icon: Users, meaning: "Role assignments and authority grants inside an organisation." },
  ORG_CONFIG: { scope: "ORGANISATION", label: "Organisation configuration", tone: "unknown", icon: UserCog, meaning: "Legal entities and legal holds of an organisation." },
  ORG_OTHER: { scope: "ORGANISATION", label: "Other organisation activity", tone: "unknown", icon: Workflow, meaning: "Sign-ins, denials and work records of an organisation's people and agents." },
};

/** Actions recorded when the platform refused or failed an attempt. */
const DENIED_ACTIONS = new Set(["authorization.denied", "auth.login_failed", "auth.login_refused_suspended", "auth.password_change_failed"]);

/** ILLUSTRATIVE grouping by action prefix, within the record's scope. */
export function categoryOf(e: AuditEventRecord): Category {
  const a = e.action;
  if (!e.tenant_id) {
    if (a.startsWith("platform.organisation_registry")) return "PLATFORM_REGISTRY";
    if (DENIED_ACTIONS.has(a)) return "PLATFORM_SECURITY";
    if (a.startsWith("auth.") || a.startsWith("role.")) return "PLATFORM_ACCESS";
    return "PLATFORM_ADMIN";
  }
  if (a.startsWith("organisation.") || a.startsWith("platform.") || a.startsWith("account.")) return "ORG_ADMIN";
  if (a.startsWith("role.") || a.startsWith("authority_grant.")) return "ORG_ROLES";
  if (a.startsWith("legal_hold.") || a.startsWith("entity.")) return "ORG_CONFIG";
  return "ORG_OTHER";
}

/* ------------------------------------------------------------------------------------------------------- outcome */
export type Outcome = "SUCCEEDED" | "DENIED";
/** DEMONSTRATION-ONLY: the audit model stores no outcome, so it is read from the action name. */
export const outcomeOf = (e: AuditEventRecord): Outcome => (DENIED_ACTIONS.has(e.action) ? "DENIED" : "SUCCEEDED");
export const OUTCOME: Record<Outcome, BadgeMeta> = {
  SUCCEEDED: { label: "Succeeded", tone: "verified", icon: CheckCircle2, meaning: "Demonstration-only: derived from the action name; the audit model stores no outcome." },
  DENIED: { label: "Denied", tone: "critical", icon: Ban, meaning: "Demonstration-only: the action name records a refused or failed attempt; the audit model stores no outcome field." },
};

/** DEMONSTRATION-ONLY review rule: why a record would deserve a person's look, or null. Not a platform rule. */
export function reviewReasonOf(e: AuditEventRecord): string | null {
  if (e.action === "authorization.denied") return "Access to an endpoint was denied.";
  if (e.new_state === "LOCKED") return "An account was locked after failed sign-ins.";
  if (e.action === "organisation.status_changed") return "An organisation's status changed.";
  if (e.action === "legal_hold.released") return "A legal hold was released.";
  if (e.action === "authority_grant.revoked") return "An authority grant was revoked.";
  if (e.action === "connector.certification_changed") return "A connector's certification changed.";
  return null;
}

/* ---------------------------------------------------------------------------------------------------------- rows */
/** One record with its illustrative display data. */
export type Row = {
  e: AuditEventRecord; actorName: string | null; orgName: string | null;
  scope: Scope; category: Category; outcome: Outcome; review: string | null;
};

export function buildRows(d: AuditData): Row[] {
  const actors = new Map(d.actors.map((a) => [a.actor_id, a.name]));
  const orgs = new Map(d.organisations.map((o) => [o.tenant_id, o.name]));
  return d.events.map((e) => ({
    e, actorName: e.actor_id ? actors.get(e.actor_id) ?? null : null, orgName: e.tenant_id ? orgs.get(e.tenant_id) ?? null : null,
    scope: scopeOf(e), category: categoryOf(e), outcome: outcomeOf(e), review: reviewReasonOf(e),
  }));
}

export type Summary = { total: number; platform: number; organisation: number; organisations: number; succeeded: number; denied: number; review: number; seqMin: number; seqMax: number };

export function summarise(rows: Row[]): Summary {
  const n = (f: (r: Row) => boolean) => rows.filter(f).length;
  const seqs = rows.map((r) => r.e.record_seq);
  return {
    total: rows.length, platform: n((r) => r.scope === "PLATFORM"), organisation: n((r) => r.scope === "ORGANISATION"),
    organisations: new Set(rows.flatMap((r) => (r.e.tenant_id ? [r.e.tenant_id] : []))).size,
    succeeded: n((r) => r.outcome === "SUCCEEDED"), denied: n((r) => r.outcome === "DENIED"), review: n((r) => r.review !== null),
    seqMin: seqs.length ? Math.min(...seqs) : 0, seqMax: seqs.length ? Math.max(...seqs) : 0,
  };
}

/* ------------------------------------------------------------------------------------------------------- filters */
export const ACTOR_TYPES = ["HUMAN", "SERVICE", "AGENT"] as const;
/** Sentinel option values: a record with no actor_type, and records with no tenant_id. */
export const NONE = "__none__";

export type Filters = {
  q: string; scope: "" | Scope; category: "" | Category; actorType: string; action: string; tenant: string;
  /** Inclusive UTC calendar days, YYYY-MM-DD. */
  from: string; to: string;
  outcome: "" | Outcome;
};
export const NO_FILTERS: Filters = { q: "", scope: "", category: "", actorType: "", action: "", tenant: "", from: "", to: "", outcome: "" };
export const isFiltering = (f: Filters) => Object.values(f).some((v) => v.trim() !== "");
export const rangeInvalid = (f: Filters) => !!f.from && !!f.to && f.from > f.to;

export function applyFilters(rows: Row[], f: Filters): Row[] {
  if (rangeInvalid(f)) return [];
  const q = f.q.trim().toLocaleLowerCase();
  return rows.filter(({ e, actorName, orgName, scope, category, outcome }) => {
    const day = e.recorded_at.slice(0, 10);
    return (!q || [e.id, e.actor_id, actorName, e.action, e.object_id, orgName, e.tenant_id].some((v) => v?.toLocaleLowerCase().includes(q)))
      && (!f.scope || scope === f.scope) && (!f.category || category === f.category)
      && (!f.actorType || (f.actorType === NONE ? !e.actor_type : e.actor_type === f.actorType))
      && (!f.action || e.action === f.action)
      && (!f.tenant || (f.tenant === NONE ? !e.tenant_id : e.tenant_id === f.tenant))
      && (!f.from || day >= f.from) && (!f.to || day <= f.to)
      && (!f.outcome || outcome === f.outcome);
  });
}

/* ------------------------------------------------------------------------------------------------------- sorting */
export type SortKey = "time" | "seq" | "action";
export type Sort = { key: SortKey; dir: "asc" | "desc" };
export const DEFAULT_SORT: Sort = { key: "time", dir: "desc" };

export function sortRows(rows: Row[], s: Sort): Row[] {
  const v = (r: Row) => (s.key === "time" ? r.e.recorded_at : s.key === "action" ? r.e.action : r.e.record_seq);
  return [...rows].sort((a, b) => {
    const x = v(a), y = v(b);
    const r = x < y ? -1 : x > y ? 1 : 0;
    return (s.dir === "asc" ? r : -r) || (s.dir === "asc" ? a.e.record_seq - b.e.record_seq : b.e.record_seq - a.e.record_seq);
  });
}

/* ----------------------------------------------------------------------------------------- implementation findings */
/** Findings about how the platform's audit records are implemented today. They describe the code, not these records. */
export const FINDINGS: { title: string; detail: string; icon: LucideIcon }[] = [
  { title: "Append-only by design", icon: FileLock2, detail: "Audit events are designed to be append-only: once written, a record is not meant to be changed or removed." },
  { title: "PostgreSQL append-only trigger", icon: Database, detail: "On PostgreSQL a database trigger refuses updates and deletes on the audit table." },
  { title: "Enforced on PostgreSQL only", icon: LockKeyhole, detail: "The trigger has been tested with PostgreSQL. SQLite does not enforce it." },
  { title: "No hash chain or signature", icon: Fingerprint, detail: "Audit records have no hash chain and no cryptographic signature, so tampering cannot be proven or ruled out cryptographically." },
  { title: "Evidence ledger is separate", icon: Link2Off, detail: "The evidence ledger is a separate store with its own chain mechanism. It does not cover these audit records." },
  { title: "Application-server clock", icon: Clock, detail: "recorded_at is taken from the application server's clock, in UTC." },
  { title: "Cell-wide record sequence", icon: ListOrdered, detail: "record_seq is one sequence for the whole cell, shared with other record types, so the audit records alone show gaps between numbers." },
  { title: "Correlation ID not populated", icon: Hash, detail: "The correlation_id field exists but is not populated consistently. In the current code the audit writer does not set it." },
  { title: "No retention or export", icon: FileDown, detail: "Retention rules and audit export are not implemented." },
  { title: "Partial redaction", icon: EyeOff, detail: "Redaction is partial rather than governed by a complete general policy. For example, a registry read records which filters were applied but not the search text, while an Organisation Admin appointment records the admin's e-mail address in full." },
];
