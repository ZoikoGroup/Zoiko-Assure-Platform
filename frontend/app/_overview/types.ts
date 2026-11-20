/* Organisation Overview: the response shapes it reads and the small pure helpers its sections share.
   Each type lists only the fields the Overview uses, as the named backend handler returns them. Nothing here computes
   a figure the backend does not record, and nothing here is demonstration data. */

import type { ApiError } from "@/lib/api";

/* ------------------------------------------------------------------------------------------------ response shapes */

export type AssuranceState = "PROVEN" | "NOT_PROVEN" | "FAILING" | "REQUIRES_REVIEW" | "NO_SNAPSHOT";

/** One of the organisation's eight latest outbox events (GET /dashboard `recent_events`). Events record no actor. */
export type RecentEvent = { event_type: string; aggregate_type: string; aggregate_id: string; record_seq: number; at: string };

/** GET /dashboard (backend/app/routers/dashboard.py; read or audit_read). */
export type Dashboard = {
  as_of: { cell_id: string; record_seq: number; valid_at: string };
  denominator_rule: string;
  assurance_totals: Record<AssuranceState, number>;
  by_entity: { entity_id: string; legal_name: string; country: string; home_cell_id: string; denominator: number }[];
  determinations: { PROPOSED: number; IN_REVIEW: number; ADOPTED: number; REJECTED: number; needs_review: number };
  open_fact_requests: number;
  obligation_occurrences: { unfulfilled: number; overdue: number; rule: string };
  evidence_conditions: Record<string, number>;
  open_issues: { total: number; by_severity: Record<string, number> };
  test_runs_by_month: { month: string; PASS: number; FAIL: number; OTHER: number }[];
  obligations: { lineage_id: string; lifecycle: string; state: string | null; period: [string, string] | null }[];
  recent_events: RecentEvent[];
  language_note: string;
};

/** GET /org/tenant (backend/app/routers/core.py; read). `cells` lists every cell of the deployment, not only this
 *  organisation's: an entity's home cell is looked up in it. */
export type OrgRecord = {
  tenant: { tenant_id: string; legal_name: string; contracting_region: string; isolation_tier: string; status: string; created_at: string };
  entities: { entity_id: string; legal_name: string; country: string; home_cell_id: string; status: string }[];
  cells: { cell_id: string; region: string }[];
};

/** GET /controls (backend/app/routers/assurance.py; read or audit_read). `current_state` is a 24-hour convenience view;
 *  its `state` is null when the control's configuration cannot be evaluated (shown as an error, never as a state). */
export type ControlRow = {
  control_implementation_id: string;
  method: string;
  subject: string;
  definition?: { title?: string | null } | null;
  current_state: { state: string | null; configuration_error?: unknown };
};

/** GET /ipe/anomalies (backend/app/routers/integration.py; read or audit_read). */
export type AnomalyRow = { anomaly_id: string; anomaly_class: string; severity: string; disposition_code: string | null; created_at: string };

/** GET /issues (backend/app/routers/assurance.py; read or audit_read). */
export type IssueRow = {
  issue_id: string; title: string; issue_type: string; severity: string; priority: string; state: string;
  due_at: string | null; created_at: string;
};

/** GET /exceptions (backend/app/routers/assurance.py; read or audit_read). */
export type ExceptionRow = {
  exception_id: string; target_type: string; target_id: string; reason: string; state: string; valid_from: string; valid_to: string;
};

/** GET /compensations (backend/app/routers/assurance.py; read or audit_read). */
export type CompensationRow = {
  compensation_id: string; obligation_lineage_id: string; primary_control_key: string; equivalence_rationale: string;
  status: string; valid_from: string; valid_to: string | null;
};

/** GET /fact-requests (backend/app/routers/applicability.py; read only). */
export type FactRequestRow = {
  request_id: string; fact_label: string; subject: string; owner_role: string; due_at: string; status: string; created_at: string;
};

/* ------------------------------------------------------------------------------------------------ reads */

/** One read the Overview makes, as useApi reports it, and whether it was made at all: a read this person's roles are
 *  not served is never made, because every refusal is an audit record. */
export type Read<T> = { requested: boolean; data: T | null; error: ApiError | null; reload: () => void };

export type Reads = {
  dash: Read<Dashboard>;
  org: Read<OrgRecord>;
  controls: Read<ControlRow[]>;
  anomalies: Read<AnomalyRow[]>;
  issues: Read<IssueRow[]>;
  exceptions: Read<ExceptionRow[]>;
  compensations: Read<CompensationRow[]>;
  facts: Read<FactRequestRow[]>;
};

export const toRead = <T,>(requested: boolean, r: { data: T | null; error: ApiError | null; reload: () => void }): Read<T> =>
  ({ requested, data: r.data, error: r.error, reload: r.reload });

/** skipped: not made for this role · loading · denied: refused (403) · failed: any other error · ready. */
export type ReadState = "skipped" | "loading" | "denied" | "failed" | "ready";

export function stateOf(r: Read<unknown>): ReadState {
  if (!r.requested) return "skipped";
  if (r.error) return r.error.status === 403 ? "denied" : "failed";
  return r.data === null ? "loading" : "ready";
}

/* ------------------------------------------------------------------------------------------------ open records */

// Open as GET /dashboard counts them (backend/app/routers/dashboard.py `_open_issues`, `_open_fact_requests`).
const CLOSED_ISSUE = ["CLOSED", "CANCELLED"];
const OPEN_FACT_REQUEST = ["OPEN", "IN_PROGRESS"];

/** The organisation's open and pending records. A list is undefined while it is not (or cannot be) read, so a missing
 *  list is never mistaken for an empty one. */
export function openRecords(r: Reads) {
  return {
    controlsOff: r.controls.data?.filter((c) => c.current_state.state !== "OPERATING"),
    anomalies: r.anomalies.data?.filter((a) => !a.disposition_code),
    issues: r.issues.data?.filter((i) => !CLOSED_ISSUE.includes(i.state)),
    exceptions: r.exceptions.data?.filter((x) => x.state === "PENDING_APPROVAL"),
    compensations: r.compensations.data?.filter((c) => c.status === "PENDING_APPROVAL"),
    facts: r.facts.data?.filter((f) => OPEN_FACT_REQUEST.includes(f.status)),
  };
}
export type Open = ReturnType<typeof openRecords>;

/** The organisation's legal entities: the organisation record's when it is read, otherwise the dashboard's (the same
 *  entities either way). Undefined while neither is read. */
export const entitiesOf = (r: Reads) => r.org.data?.entities ?? r.dash.data?.by_entity;
/** The jurisdictions (entity countries) those entities are registered in, once each. */
export const countriesOf = (entities?: { country: string }[]) => (entities ? [...new Set(entities.map((e) => e.country))].sort() : []);

/** Evidence conditions other than CURRENT, with records in them. Erasure is a governed act, not a gap. */
export const evidenceGaps = (d: Dashboard) =>
  Object.entries(d.evidence_conditions).filter(([k, v]) => v > 0 && k !== "CURRENT" && k !== "ERASED");

/** The periods the adopted obligations' latest assurance snapshots cover (GET /dashboard `obligations`). Assure keeps a
 *  period per obligation snapshot; there is no organisation-wide reporting period. */
export function snapshotPeriods(d: Dashboard) {
  const adopted = d.obligations.filter((o) => o.lifecycle === "ADOPTED");
  const periods = adopted.flatMap((o) => (o.period ? [o.period] : []));
  const distinct = [...new Map(periods.map((p) => [`${p[0]}|${p[1]}`, p])).values()];
  const earliest = periods.reduce<string | null>((a, p) => (a === null || Date.parse(p[0]) < Date.parse(a) ? p[0] : a), null);
  const latest = periods.reduce<string | null>((a, p) => (a === null || Date.parse(p[1]) > Date.parse(a) ? p[1] : a), null);
  return { adopted: adopted.length, covered: periods.length, distinct, earliest, latest };
}

/* ------------------------------------------------------------------------------------------------ wording */

export const holds = (perms: readonly string[], mine?: readonly string[]) => perms.some((p) => !!mine?.includes(p));
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const list = (pairs: [string, number][]) => pairs.map(([k, v]) => `${v} ${k}`).join(" · ");
export const tally = (codes: string[]): [string, number][] =>
  Object.entries(codes.reduce((m: Record<string, number>, c) => ({ ...m, [c]: (m[c] ?? 0) + 1 }), {}));
export const utcTime = (v: string) => { const t = new Date(v); return isNaN(t.getTime()) ? v : t.toISOString().slice(11, 16); };
/** "a", "a and b", "a, b and c". */
export const joinWords = (words: string[]) => (words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`);
export const sentence = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const isPast = (iso: string | null | undefined, now: number) => {
  if (!iso) return false;
  const t = Date.parse(iso);
  return !isNaN(t) && t <= now;
};

const ACRONYMS: Record<string, string> = { ai: "AI", id: "ID", ipe: "IPE" };
/** A backend code in words: "IN_REMEDIATION" → "in remediation", "evidence.package_signed" → "evidence package signed". */
export const codeWords = (code: string) =>
  code.replace(/[._]+/g, " ").trim().toLowerCase().split(" ").map((w) => ACRONYMS[w] ?? w).join(" ");
/** The recorded event type as a sentence: "exception.requested" → "Exception requested". Words only, nothing added. */
export const describeEvent = (type: string) => sentence(codeWords(type));
/** An aggregate type in words: "EvidenceArtifact" → "Evidence artifact", "IPEEvaluation" → "IPE evaluation". */
export function describeAggregate(type: string) {
  const words = type.replace(/_/g, " ").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2").split(/\s+/).filter(Boolean);
  return words.map((w, i) => (w.length > 1 && w === w.toUpperCase() ? w : i === 0 ? sentence(w.toLowerCase()) : w.toLowerCase())).join(" ");
}
/** "2026-06" → "Jun 2026". */
export function monthLabel(m: string) {
  const [y, mo] = m.split("-").map(Number);
  if (!y || !mo) return m;
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** The Overview's panel surface (the application kit's card: white surface, slate hairline, both themes). */
export const PANEL = "rounded-xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]";
/** A panel's heading. */
export const PANEL_TITLE = "text-[18px] font-semibold leading-7 text-slate-900";
/** Wraps a status pill (or the element holding one) so its code reads as the Overview's reference design shows codes:
 *  monospaced, medium weight, tight tracking. The face is inherited; weight and tracking override the pill's own on its
 *  outer span only. Applied here rather than in the shared kit, whose pills stay sans everywhere else. */
export const CODE = "font-mono [&>span]:font-medium [&>span]:tracking-tight";
/** An inline link in the Overview's accent, with the same hover and keyboard focus everywhere on the page. */
export const LINK = "rounded font-semibold text-teal-700 underline-offset-2 transition-colors duration-150 hover:text-teal-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600";
