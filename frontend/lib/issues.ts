/* The issue lifecycle as the backend enforces it (backend/app/engines/governance.py; routes in routers/assurance.py).
   No endpoint publishes the transition map or the closure rules (GET /dictionary describes the states only), so they
   are mirrored here with their source named, and lib/issues.test.mjs checks them against the backend source. They
   decide only what this UI offers: the backend still decides every request, and its refusal is shown as it gave it.
   Pure functions without imports, so the test runs them under Node's built-in test runner. */

/** A row of GET /issues (the assurance_issue projection). */
export type Issue = {
  issue_id: string; title: string; issue_type: string; severity: string; priority: string; state: string;
  source_ref_type: string; source_ref_id: string; owner_actor_id: string | null; due_at: string | null;
  dedupe_key: string; created_at: string;
};

/** A row of GET /issues/{id}/events (governance.event_view), oldest first. */
export type IssueEvent = {
  event_id: string; issue_id: string; event_type: string; from_state: string | null; to_state: string;
  actor_id: string; reason: string | null; owner_actor_id: string | null; record_seq: number; created_at: string;
  provenance?: { kind: string; recorded_by?: string; note?: string };
};

/** governance.ISSUE_FLOW: the states each state may move to. */
export const ISSUE_FLOW: Readonly<Record<string, readonly string[]>> = {
  OPEN: ["TRIAGED", "CANCELLED"],
  TRIAGED: ["IN_REMEDIATION", "CANCELLED"],
  IN_REMEDIATION: ["PENDING_VERIFICATION"],
  PENDING_VERIFICATION: ["CLOSED", "IN_REMEDIATION"],
};

/** governance.TERMINAL_ISSUE_STATES: no transition, assignment or closure is accepted. */
export const TERMINAL_ISSUE_STATES: readonly string[] = ["CLOSED", "CANCELLED"];
export const isTerminal = (state: string) => TERMINAL_ISSUE_STATES.includes(state);

/** How an issue of a given type is closed:
 *  SNAPSHOT    governance.SNAPSHOT_VERIFIED_ISSUE_TYPES: POST /issues/{id}/close with verification_snapshot_id, a new
 *              PROVEN AssuranceSnapshot of the same obligation (closure_problems).
 *  EVALUATION  governance.EVALUATION_VERIFIED_ISSUE_TYPES: POST /issues/{id}/close with
 *              verification_control_evaluation_id, a new OPERATING ControlEvaluation of the same control
 *              (control_closure_problems).
 *  UNVERIFIED  governance.UNVERIFIED_CLOSURE_ISSUE_TYPES: POST /issues/{id}/transition to CLOSED; their closure criteria
 *              are owned outside this workflow.
 *  NONE        any other type: /close answers VERIFICATION_NOT_DEFINED and /transition VERIFICATION_REQUIRED. */
export type ClosureKind = "SNAPSHOT" | "EVALUATION" | "UNVERIFIED" | "NONE";
export const CLOSURE_BY_TYPE: Readonly<Record<string, Exclude<ClosureKind, "NONE">>> = {
  OBLIGATION_FAILING: "SNAPSHOT", PROOF_GAP: "SNAPSHOT", ASSURANCE_REVIEW: "SNAPSHOT",
  CONTROL_FAILING: "EVALUATION", CONTROL_REVIEW: "EVALUATION",
  INTEGRITY_INCIDENT: "UNVERIFIED", DETERMINATION_REVIEW: "UNVERIFIED",
};
export const closureKind = (issueType: string): ClosureKind => CLOSURE_BY_TYPE[issueType] ?? "NONE";

export type IssueActionKind = "assign" | "move" | "cancel" | "close";
export type IssueAction = {
  kind: IssueActionKind;
  /** The target state of a "move", "cancel" or "close". */
  to?: string;
  label: string;
  /** Why the action is shown but cannot be taken: a rule the backend enforces. */
  blocked?: string;
};

const MOVE_LABEL: Record<string, string> = { TRIAGED: "Triage", IN_REMEDIATION: "Start remediation", PENDING_VERIFICATION: "Submit for verification" };

/** The lifecycle actions the backend accepts for `issue` as it is now, in display order. A terminal issue has none
 *  (assign_issue answers ISSUE_TERMINAL and ISSUE_FLOW has no exits). Closure is offered only on the route the issue
 *  type is closed by, and never as a plain move to CLOSED for a type that needs verification. */
export function issueActions(issue: Pick<Issue, "state" | "issue_type" | "owner_actor_id">): IssueAction[] {
  if (isTerminal(issue.state)) return [];
  const out: IssueAction[] = [{ kind: "assign", label: issue.owner_actor_id ? "Reassign owner" : "Assign owner" }];
  for (const to of ISSUE_FLOW[issue.state] ?? []) {
    if (to === "CANCELLED") {
      out.push({ kind: "cancel", to, label: "Cancel issue" });
    } else if (to === "CLOSED") {
      const kind = closureKind(issue.issue_type);
      out.push(kind === "NONE"
        ? { kind: "close", to, label: "Close", blocked: `The server defines no closure for ${issue.issue_type} issues (VERIFICATION_NOT_DEFINED).` }
        : { kind: "close", to, label: kind === "UNVERIFIED" ? "Close" : "Close with verification" });
    } else {
      const label = to === "IN_REMEDIATION" && issue.state === "PENDING_VERIFICATION" ? "Return to remediation" : MOVE_LABEL[to] ?? `Move to ${to}`;
      out.push(to === "TRIAGED" && !issue.owner_actor_id
        ? { kind: "move", to, label, blocked: "Assign an accountable owner first: the server refuses triage without one (ISSUE_OWNER_REQUIRED)." }
        : { kind: "move", to, label });
    }
  }
  return out;
}

/** Why `action` can no longer be taken on `issue` as it is now (it may have changed since the action was offered), or
 *  null when it still can. */
export function unavailableReason(issue: Pick<Issue, "state" | "issue_type" | "owner_actor_id">, action: Pick<IssueAction, "kind" | "to">): string | null {
  const now = issueActions(issue).find((a) => a.kind === action.kind && a.to === action.to);
  if (!now) return `This issue is now ${issue.state}, so this action is no longer available.`;
  return now.blocked ?? null;
}

/** governance._remediation_started_seq: the record sequence of the latest event that entered IN_REMEDIATION from another
 *  state (a migration BASELINE, whose from_state is null, included; an owner reassignment, recorded IN_REMEDIATION ->
 *  IN_REMEDIATION, is not an entry and starts no cycle), or null when none is recorded (the server then answers
 *  REMEDIATION_NOT_RECORDED). A verifying result must have a record cutoff at or after it. The shared contract is
 *  backend/tests/remediation_cycle_vectors.json. */
export function remediationStartSeq(events: readonly Pick<IssueEvent, "from_state" | "to_state" | "record_seq">[]): number | null {
  let start: number | null = null;
  for (const e of events) {
    const entered = e.to_state === "IN_REMEDIATION" && e.from_state !== "IN_REMEDIATION";
    if (entered && (start === null || e.record_seq > start)) start = e.record_seq;
  }
  return start;
}

export type VerificationRow = { state: string; record_cutoff_seq: number };

/** Splits the records a closure could cite into those the server can accept and those it refuses for a reason visible
 *  here: the wrong result (VERIFICATION_NOT_PROVEN / VERIFICATION_NOT_OPERATING), no recorded remediation cycle
 *  (REMEDIATION_NOT_RECORDED) or a record cutoff before remediation started (VERIFICATION_PREDATES_REMEDIATION). The
 *  server also requires a TestRun or evidence recorded after remediation started (RETEST_REQUIRED), which these rows do
 *  not show, so it can still refuse an eligible one. */
export function partitionVerification<T extends VerificationRow>(rows: readonly T[], kind: "SNAPSHOT" | "EVALUATION", startedSeq: number | null) {
  const required = kind === "SNAPSHOT" ? "PROVEN" : "OPERATING";
  const eligible: T[] = [];
  const excluded: { row: T; reasons: string[] }[] = [];
  for (const row of rows) {
    const reasons: string[] = [];
    if (row.state !== required) reasons.push(`${row.state}, not ${required}`);
    if (startedSeq === null) reasons.push("no remediation cycle is recorded for this issue");
    else if (row.record_cutoff_seq < startedSeq) reasons.push(`record cutoff #${row.record_cutoff_seq} is before remediation started (#${startedSeq})`);
    if (reasons.length) excluded.push({ row, reasons });
    else eligible.push(row);
  }
  return { required, eligible, excluded };
}

/** POST /issues/{id}/assign body (IssueAssignIn; unknown fields are refused). */
export const assignBody = (ownerActorId: string, reason: string) => ({ owner_actor_id: ownerActorId, reason: reason.trim() });

/** POST /issues/{id}/close body (IssueCloseIn): exactly one verification reference, of the kind the issue type needs. */
export function closeBody(kind: "SNAPSHOT" | "EVALUATION", verificationId: string, reason: string) {
  return kind === "SNAPSHOT"
    ? { verification_snapshot_id: verificationId, reason: reason.trim() }
    : { verification_control_evaluation_id: verificationId, reason: reason.trim() };
}

/** POST /issues/{id}/transition body (IssueMove). A blank reason is sent as null: the server needs one for CANCELLED
 *  only, and the dialog asks for it there. */
export const transitionBody = (to: string, reason: string) => ({ state: to, reason: reason.trim() || null });

/** What a history row says happened. Transition events are named after the state they reached. */
export function eventTitle(e: Pick<IssueEvent, "event_type" | "from_state" | "to_state">): string {
  switch (e.event_type) {
    case "CREATED": return "Raised";
    case "ASSIGNED": return "Owner assigned";
    case "BASELINE": return "State recorded by migration";
    case "TRIAGED": return "Triaged";
    case "IN_REMEDIATION": return e.from_state === "PENDING_VERIFICATION" ? "Returned to remediation" : "Remediation started";
    case "PENDING_VERIFICATION": return "Submitted for verification";
    case "CLOSED": return "Closed";
    case "CANCELLED": return "Cancelled";
    default: return e.event_type;
  }
}

/** Who an event names: a person (through `name`), the system that raised the issue, or the migration that backfilled it. */
export function actorLabel(actorId: string, name: (id: string) => string): string {
  if (actorId === "system") return "System";
  if (actorId.startsWith("system:migration:")) return `Migration ${actorId.slice("system:migration:".length)}`;
  return name(actorId);
}

/** Runs one call at a time: a call made while another is pending is dropped (resolves to undefined), so a second click
 *  in the same tick, before the button re-renders as disabled, cannot send a governed request twice. */
export function singleFlight() {
  let pending = false;
  return async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (pending) return undefined;
    pending = true;
    try { return await fn(); } finally { pending = false; }
  };
}
