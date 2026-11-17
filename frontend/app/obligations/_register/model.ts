import type { ObligationRecord } from "./types";

/* The Obligations Register's search, filters, sort keys and summary figures, worked out in the browser over the full
   list GET /obligations returned (the endpoint has no server-side search, filtering or paging). Nothing here derives,
   re-evaluates or changes an assurance state or lifecycle: every value shown is the backend's own. */

/** Filter value for a row with no value of its own: no snapshot yet, or no schedule because the definition is missing. */
export const NONE = "__none__";

/** Assurance states in roll-up precedence, most severe first (ZA-STA-001 STA-ADR-04). */
export const ASSURANCE_STATES = ["FAILING", "REQUIRES_REVIEW", "NOT_PROVEN", "PROVEN"] as const;

/** Meanings as the backend's controlled dictionary records them (backend/app/codes.py). */
export const ASSURANCE_MEANING: Record<string, string> = {
  PROVEN: "Satisfaction expression satisfied for the full asserted period with admissible evidence.",
  NOT_PROVEN: "No decisive failure, but proof is incomplete.",
  FAILING: "A required path is decisively unsatisfied with no valid alternative.",
  REQUIRES_REVIEW: "A governed ambiguity prevents deterministic assurance.",
};
export const LIFECYCLE_MEANING: Record<string, string> = {
  ADOPTED: "Required customer attestation satisfied.",
  PROPOSED: "Evaluation indicates applicability; adoption incomplete.",
  SUSPENDED: "Temporarily inactive under approved condition.",
  SUPERSEDED: "A newer obligation revision replaces it.",
  CLOSED: "Duty ended; history retained.",
};
const LIFECYCLE_ORDER = Object.keys(LIFECYCLE_MEANING);

const SCHEDULE_LABEL: Record<string, string> = { CONTINUOUS: "Continuous", RECURRING: "Recurring", EVENT_TRIGGERED: "Event-triggered" };
/** A schedule kind for people; an unrecognised code is shown as received. */
export const scheduleLabel = (code?: string | null) => (code ? SCHEDULE_LABEL[code] ?? code : "Not recorded");

export const titleOf = (o: ObligationRecord) => o.definition.title?.trim() || null;
export const keyOf = (o: ObligationRecord) => o.definition.key?.trim() || null;
export const subjectNameOf = (o: ObligationRecord) => o.subject.display_name?.trim() || null;
const subjectKey = (o: ObligationRecord) => o.subject.subject_id ?? o.subject_id;
const stateOf = (o: ObligationRecord) => o.latest_snapshot?.state ?? NONE;
const scheduleOf = (o: ObligationRecord) => o.definition.schedule_kind ?? NONE;

/** Sort rank of the latest assessment: most severe first, then any state this interface does not know, then none. */
export function assessmentRank(o: ObligationRecord): number {
  const s = o.latest_snapshot?.state;
  if (!s) return ASSURANCE_STATES.length + 1;
  const i = (ASSURANCE_STATES as readonly string[]).indexOf(s);
  return i === -1 ? ASSURANCE_STATES.length : i;
}

/** A timestamp as a number for sorting (0 when it cannot be read, so such rows sort first ascending). */
export const timeOf = (v?: string | null) => {
  const t = v ? Date.parse(v) : NaN;
  return Number.isFinite(t) ? t : 0;
};

/* ------------------------------------------------------------------------------------------------ search & filters */
export type FilterKey = "lifecycle" | "state" | "schedule" | "subject";
export type Filters = { q: string } & Record<FilterKey, string>;
export const FILTER_KEYS: FilterKey[] = ["lifecycle", "state", "schedule", "subject"];
export const NO_FILTERS: Filters = { q: "", lifecycle: "", state: "", schedule: "", subject: "" };
export const isFiltered = (f: Filters) => !!f.q.trim() || FILTER_KEYS.some((k) => !!f[k]);

const VALUE: Record<FilterKey, (o: ObligationRecord) => string> = { lifecycle: (o) => o.lifecycle, state: stateOf, schedule: scheduleOf, subject: subjectKey };

/** Whether a row matches the search (obligation title or key, ignoring case) and every filter but `except`. */
export function matches(o: ObligationRecord, f: Filters, except?: FilterKey): boolean {
  const q = f.q.trim().toLowerCase();
  if (q && !`${o.definition.title ?? ""}\n${o.definition.key ?? ""}`.toLowerCase().includes(q)) return false;
  return FILTER_KEYS.every((k) => k === except || !f[k] || VALUE[k](o) === f[k]);
}

export type FacetOption = { value: string; label: string; count: number };

/** Subject names for the filter. Two subjects with the same name are told apart by their type (or a short ID). */
function subjectLabels(rows: ObligationRecord[]): Map<string, string> {
  const byKey = new Map<string, ObligationRecord>();
  rows.forEach((o) => { if (!byKey.has(subjectKey(o))) byKey.set(subjectKey(o), o); });
  const names = [...byKey.values()].map((o) => subjectNameOf(o) ?? "Subject not found");
  const out = new Map<string, string>();
  [...byKey.entries()].forEach(([key, o], i) => {
    const name = names[i];
    const twin = names.filter((n) => n === name).length > 1;
    out.set(key, twin ? `${name} (${o.subject.subject_type ?? key.slice(0, 8)})` : name);
  });
  return out;
}

const rankIn = (order: readonly string[], v: string) => { const i = order.indexOf(v); return i === -1 ? order.length : i; };
const byLabel = (a: FacetOption, b: FacetOption) => a.label.localeCompare(b.label);
const noneLast = (a: FacetOption, b: FacetOption) => Number(a.value === NONE) - Number(b.value === NONE);

/** The options of one filter: every value present in the register (every known assurance state too, and the selected
 *  value even if a refresh removed it), each counted over the rows that match the search and the other filters. */
export function facet(rows: ObligationRecord[], f: Filters, key: FilterKey): FacetOption[] {
  const scope = rows.filter((o) => matches(o, f, key));
  const values = new Set(rows.map(VALUE[key]));
  if (key === "state") { ASSURANCE_STATES.forEach((s) => values.add(s)); values.add(NONE); }
  if (f[key]) values.add(f[key]);
  const subjects = key === "subject" ? subjectLabels(rows) : null;
  const label = (v: string) =>
    key === "state" ? (v === NONE ? "Not assessed" : v)
    : key === "schedule" ? (v === NONE ? "Not recorded" : scheduleLabel(v))
    : key === "subject" ? subjects?.get(v) ?? "Subject not found"
    : v;
  const opts = [...values].map((value) => ({ value, label: label(value), count: scope.filter((o) => VALUE[key](o) === value).length }));
  if (key === "lifecycle") return opts.sort((a, b) => rankIn(LIFECYCLE_ORDER, a.value) - rankIn(LIFECYCLE_ORDER, b.value) || byLabel(a, b));
  if (key === "state") return opts.sort((a, b) => noneLast(a, b) || rankIn(ASSURANCE_STATES, a.value) - rankIn(ASSURANCE_STATES, b.value) || byLabel(a, b));
  return opts.sort((a, b) => noneLast(a, b) || byLabel(a, b));
}

/* ------------------------------------------------------------------------------------------------ summary figures */
export type RegisterFigures = {
  total: number;
  adopted: number;
  requiresReview: number;
  /** Null when a row did not report its count, so the total would be incomplete. */
  approvedExceptions: number | null;
  obligationsWithExceptions: number;
};

/** Counts over the whole register (not the filtered view), each from a field the backend reported. */
export function figures(rows: ObligationRecord[]): RegisterFigures {
  const counted = rows.every((o) => typeof o.open_exceptions === "number" && Number.isFinite(o.open_exceptions));
  return {
    total: rows.length,
    adopted: rows.filter((o) => o.lifecycle === "ADOPTED").length,
    requiresReview: rows.filter((o) => o.latest_snapshot?.state === "REQUIRES_REVIEW").length,
    approvedExceptions: counted ? rows.reduce((n, o) => n + o.open_exceptions, 0) : null,
    obligationsWithExceptions: rows.filter((o) => (o.open_exceptions ?? 0) > 0).length,
  };
}
