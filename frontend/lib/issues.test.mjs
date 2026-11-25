// lib/issues.ts against the backend it mirrors. No test framework is installed in this frontend, so this uses Node's
// built-in runner, which strips the TypeScript types (Node 22.18 or later). From frontend/:
//     node --test lib/issues.test.mjs
// The backend source is only read: the mirrored transition map, closure rules and request bodies are compared with
// backend/app/engines/governance.py and backend/app/routers/assurance.py, so a change there fails here; the
// remediation-cycle rule is compared through the vectors in backend/tests/remediation_cycle_vectors.json.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ISSUE_FLOW, TERMINAL_ISSUE_STATES, CLOSURE_BY_TYPE, closureKind, issueActions, unavailableReason, remediationStartSeq,
  partitionVerification, assignBody, closeBody, transitionBody, eventTitle, actorLabel, singleFlight,
} from "./issues.ts";

const backend = (p) => readFileSync(new URL(`../../backend/app/${p}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");  // CRLF working copies
const GOV = backend("engines/governance.py");
const ROUTER = backend("routers/assurance.py");

/** The text between the braces that open after `name = {` (nested braces balanced). */
function block(src, name) {
  const start = src.indexOf(`${name} = {`);
  assert.ok(start >= 0, `${name} not found`);
  let i = src.indexOf("{", start), depth = 0;
  const from = i + 1;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    if (src[i] === "}" && --depth === 0) return src.slice(from, i);
  }
  throw new Error(`${name} is not closed`);
}
const strings = (s) => [...s.matchAll(/"(\w+)"/g)].map((m) => m[1]);
const pyFlow = (src) => Object.fromEntries([...block(src, "ISSUE_FLOW").matchAll(/"(\w+)":\s*\{([^}]*)\}/g)].map(([, k, v]) => [k, strings(v).sort()]));
const frozenset = (name) => {
  const m = GOV.match(new RegExp(`^${name} = frozenset\\(\\{([^}]*)\\}\\)`, "m"));
  assert.ok(m, `${name} is no longer a literal frozenset; update this test`);
  return strings(m[1]).sort();
};
/** The fields of a Pydantic request model in routers/assurance.py, and whether each has a default. */
function modelFields(cls) {
  const m = ROUTER.match(new RegExp(`class ${cls}\\(BaseModel\\):([\\s\\S]*?)\\n\\n\\n`));
  assert.ok(m, `${cls} not found`);
  assert.match(m[1], /extra="forbid"/, `${cls} no longer refuses unknown fields`);
  return Object.fromEntries([...m[1].matchAll(/^    (\w+): ([^\n]*)$/gm)].map(([, f, rest]) => [f, /=/.test(rest)]));
}
const sorted = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, [...v].sort()]));
const typesOf = (kind) => Object.keys(CLOSURE_BY_TYPE).filter((t) => CLOSURE_BY_TYPE[t] === kind).sort();

test("transition map equals the backend ISSUE_FLOW (engine and router)", () => {
  assert.deepEqual(sorted(ISSUE_FLOW), pyFlow(GOV));
  assert.deepEqual(sorted(ISSUE_FLOW), pyFlow(ROUTER));
  assert.deepEqual([...TERMINAL_ISSUE_STATES].sort(), frozenset("TERMINAL_ISSUE_STATES"));
});

test("closure kinds equal the backend's issue-type sets", () => {
  assert.deepEqual(typesOf("SNAPSHOT"), frozenset("SNAPSHOT_VERIFIED_ISSUE_TYPES"));
  assert.deepEqual(typesOf("UNVERIFIED"), frozenset("UNVERIFIED_CLOSURE_ISSUE_TYPES"));
  assert.match(GOV, /^EVALUATION_VERIFIED_ISSUE_TYPES = frozenset\(CONTROL_ISSUE_TYPES\.values\(\)\)/m);
  const control = [...block(GOV, "CONTROL_ISSUE_TYPES").matchAll(/"\w+":\s*"(\w+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual(typesOf("EVALUATION"), control);
});

test("every issue type the backend raises has a defined closure here", () => {
  const raised = new Set();
  for (const f of ["engines/applicability.py", "engines/evidence.py", "engines/assurance.py", "engines/governance.py"]) {
    for (const m of backend(f).matchAll(/issue_type="(\w+)"/g)) raised.add(m[1]);
  }
  for (const m of block(backend("engines/assurance.py"), "OBLIGATION_ISSUE_RULES").matchAll(/\("[^"]*",\s*"(\w+)"/g)) raised.add(m[1]);
  for (const m of block(GOV, "CONTROL_ISSUE_TYPES").matchAll(/"\w+":\s*"(\w+)"/g)) raised.add(m[1]);
  assert.ok(raised.size >= 7, `found only ${[...raised]}`);
  for (const t of raised) assert.notEqual(closureKind(t), "NONE", `${t} has no closure kind`);
});

test("request bodies carry exactly the fields the backend models accept", () => {
  const assign = modelFields("IssueAssignIn");
  assert.deepEqual(Object.keys(assignBody("actor-1", " why ")).sort(), Object.keys(assign).sort());
  assert.deepEqual(assignBody("actor-1", "  why  "), { owner_actor_id: "actor-1", reason: "why" });

  const close = modelFields("IssueCloseIn");
  for (const kind of ["SNAPSHOT", "EVALUATION"]) {
    const body = closeBody(kind, "ref-1", " done ");
    for (const k of Object.keys(body)) assert.ok(k in close, `${k} is not an IssueCloseIn field`);
    assert.equal(Object.keys(body).filter((k) => k.startsWith("verification_")).length, 1, "exactly one verification reference");
    assert.equal(body.reason, "done");
  }
  assert.deepEqual(closeBody("SNAPSHOT", "s-1", "r"), { verification_snapshot_id: "s-1", reason: "r" });
  assert.deepEqual(closeBody("EVALUATION", "e-1", "r"), { verification_control_evaluation_id: "e-1", reason: "r" });

  const move = modelFields("IssueMove");
  assert.deepEqual(Object.keys(transitionBody("TRIAGED", "")).sort(), Object.keys(move).sort());
  assert.deepEqual(transitionBody("TRIAGED", "   "), { state: "TRIAGED", reason: null });
  assert.deepEqual(transitionBody("CANCELLED", " dup "), { state: "CANCELLED", reason: "dup" });
});

const issue = (state, issue_type = "OBLIGATION_FAILING", owner_actor_id = null) => ({ state, issue_type, owner_actor_id });
const summary = (i) => issueActions(i).map((a) => `${a.kind}${a.to ? `:${a.to}` : ""}${a.blocked ? "!" : ""}`);

test("actions per state follow the backend rules", () => {
  // Auto-raised issues have no owner: triage is shown but blocked until one is assigned (ISSUE_OWNER_REQUIRED).
  assert.deepEqual(summary(issue("OPEN")), ["assign", "move:TRIAGED!", "cancel:CANCELLED"]);
  assert.match(issueActions(issue("OPEN"))[1].blocked, /ISSUE_OWNER_REQUIRED/);
  assert.deepEqual(summary(issue("OPEN", "OBLIGATION_FAILING", "a")), ["assign", "move:TRIAGED", "cancel:CANCELLED"]);
  assert.equal(issueActions(issue("OPEN", "PROOF_GAP", "a"))[0].label, "Reassign owner");
  assert.deepEqual(summary(issue("TRIAGED", "PROOF_GAP", "a")), ["assign", "move:IN_REMEDIATION", "cancel:CANCELLED"]);
  assert.deepEqual(summary(issue("IN_REMEDIATION", "PROOF_GAP", "a")), ["assign", "move:PENDING_VERIFICATION"]);
  // Verified types close through /close only; never a plain move to CLOSED (VERIFICATION_REQUIRED).
  assert.deepEqual(summary(issue("PENDING_VERIFICATION", "CONTROL_FAILING", "a")), ["assign", "close:CLOSED", "move:IN_REMEDIATION"]);
  assert.equal(issueActions(issue("PENDING_VERIFICATION", "ASSURANCE_REVIEW", "a"))[1].label, "Close with verification");
  assert.equal(issueActions(issue("PENDING_VERIFICATION", "PROOF_GAP", "a"))[2].label, "Return to remediation");
  // Unverified types close through /transition.
  assert.equal(issueActions(issue("PENDING_VERIFICATION", "INTEGRITY_INCIDENT", "a"))[1].label, "Close");
  assert.equal(issueActions(issue("PENDING_VERIFICATION", "INTEGRITY_INCIDENT", "a"))[1].blocked, undefined);
  // Unknown types cannot be closed by either route.
  assert.match(issueActions(issue("PENDING_VERIFICATION", "SOMETHING_NEW", "a"))[1].blocked, /VERIFICATION_NOT_DEFINED/);
  // Terminal issues: nothing (assign answers ISSUE_TERMINAL).
  assert.deepEqual(issueActions(issue("CLOSED")), []);
  assert.deepEqual(issueActions(issue("CANCELLED", "PROOF_GAP", "a")), []);
});

test("no state, type or owner combination offers a move the backend refuses", () => {
  const states = [...Object.keys(ISSUE_FLOW), ...TERMINAL_ISSUE_STATES, "UNKNOWN_STATE"];
  const types = [...Object.keys(CLOSURE_BY_TYPE), "SOMETHING_NEW"];
  for (const s of states) for (const t of types) for (const o of [null, "a"]) {
    for (const a of issueActions(issue(s, t, o))) {
      if (a.kind === "assign") { assert.ok(!TERMINAL_ISSUE_STATES.includes(s)); continue; }
      assert.ok((ISSUE_FLOW[s] ?? []).includes(a.to), `${s} -> ${a.to} offered`);
      if (a.blocked) continue;
      if (a.to === "TRIAGED") assert.ok(o, "triage without owner offered");
      if (a.to === "CLOSED") assert.notEqual(closureKind(t), "NONE");
      if (a.kind === "move") assert.notEqual(a.to, "CLOSED", "a plain move to CLOSED is never offered");
      if (a.kind === "cancel") assert.equal(a.to, "CANCELLED");
    }
  }
});

test("an action re-checked against the re-read issue is withdrawn when the issue moved", () => {
  assert.equal(unavailableReason(issue("OPEN", "PROOF_GAP", "a"), { kind: "move", to: "TRIAGED" }), null);
  assert.match(unavailableReason(issue("TRIAGED", "PROOF_GAP", "a"), { kind: "move", to: "TRIAGED" }), /now TRIAGED/);
  assert.match(unavailableReason(issue("CLOSED"), { kind: "assign" }), /now CLOSED/);
  assert.match(unavailableReason(issue("OPEN"), { kind: "move", to: "TRIAGED" }), /ISSUE_OWNER_REQUIRED/);
});

// The remediation-cycle contract shared with the backend: backend/tests/test_remediation_cycle.py runs the same vectors
// against governance._remediation_started_seq, so the two sides are compared by behaviour, not by the query's text.
const CYCLE = JSON.parse(readFileSync(new URL("../../backend/tests/remediation_cycle_vectors.json", import.meta.url), "utf8"));
const cycleEvents = (rows) => rows.map(([event_type, from_state, to_state, record_seq]) => ({ event_type, from_state, to_state, record_seq }));

test("remediation start is the latest entry into IN_REMEDIATION (shared vectors with governance._remediation_started_seq)", () => {
  assert.ok(CYCLE.vectors.length >= 5, "the shared remediation-cycle vectors are missing");
  for (const v of CYCLE.vectors) {
    assert.equal(remediationStartSeq(cycleEvents(v.events)), v.expected, v.name);
    assert.equal(remediationStartSeq(cycleEvents(v.events).reverse()), v.expected, `${v.name} (any listing order)`);
  }
  // An owner handover during remediation (ASSIGNED, IN_REMEDIATION -> IN_REMEDIATION) keeps the cycle that was entered.
  assert.equal(remediationStartSeq([
    { from_state: "TRIAGED", to_state: "IN_REMEDIATION", record_seq: 5 },
    { from_state: "IN_REMEDIATION", to_state: "IN_REMEDIATION", record_seq: 8 },
  ]), 5);
});

test("verification candidates: right result, recorded remediation, cutoff at or after its start", () => {
  const rows = [
    { id: "a", state: "PROVEN", record_cutoff_seq: 10 },
    { id: "b", state: "PROVEN", record_cutoff_seq: 9 },
    { id: "c", state: "FAILING", record_cutoff_seq: 20 },
    { id: "d", state: "PROVEN", record_cutoff_seq: 8 },
  ];
  const p = partitionVerification(rows, "SNAPSHOT", 9);
  assert.equal(p.required, "PROVEN");
  assert.deepEqual(p.eligible.map((r) => r.id), ["a", "b"]);
  assert.deepEqual(p.excluded.map((x) => x.row.id), ["c", "d"]);
  assert.match(p.excluded[0].reasons[0], /FAILING, not PROVEN/);
  assert.match(p.excluded[1].reasons[0], /before remediation started/);
  const none = partitionVerification(rows, "SNAPSHOT", null);
  assert.equal(none.eligible.length, 0);
  assert.ok(none.excluded.every((x) => x.reasons.some((r) => /no remediation cycle/.test(r))));
  const ev = partitionVerification([{ state: "OPERATING", record_cutoff_seq: 3 }, { state: "DEGRADED", record_cutoff_seq: 4 }], "EVALUATION", 3);
  assert.equal(ev.required, "OPERATING");
  assert.equal(ev.eligible.length, 1);
  assert.match(GOV, /snap\.state != "PROVEN"/);
  assert.match(GOV, /ce\.state != "OPERATING"/);
  assert.match(GOV, /snap\.record_cutoff_seq < started/);
});

test("history wording and actors", () => {
  assert.equal(eventTitle({ event_type: "CREATED", from_state: null, to_state: "OPEN" }), "Raised");
  assert.equal(eventTitle({ event_type: "ASSIGNED", from_state: "OPEN", to_state: "OPEN" }), "Owner assigned");
  assert.equal(eventTitle({ event_type: "IN_REMEDIATION", from_state: "PENDING_VERIFICATION", to_state: "IN_REMEDIATION" }), "Returned to remediation");
  assert.equal(eventTitle({ event_type: "IN_REMEDIATION", from_state: "TRIAGED", to_state: "IN_REMEDIATION" }), "Remediation started");
  assert.equal(eventTitle({ event_type: "SOMETHING", from_state: null, to_state: "X" }), "SOMETHING");
  const name = (id) => `Name of ${id}`;
  assert.equal(actorLabel("system", name), "System");
  assert.equal(actorLabel("system:migration:0008", name), "Migration 0008");
  assert.equal(actorLabel("actor-cco", name), "Name of actor-cco");
});

test("singleFlight drops a second call made while the first is pending", async () => {
  const guard = singleFlight();
  let calls = 0;
  let release;
  const gate = new Promise((r) => { release = r; });
  const first = guard(async () => { calls++; await gate; return "first"; });
  const second = guard(async () => { calls++; return "second"; });   // same tick: dropped
  assert.equal(await second, undefined);
  release();
  assert.equal(await first, "first");
  assert.equal(calls, 1);
  assert.equal(await guard(async () => { calls++; return "third"; }), "third");   // free again once settled
  assert.equal(calls, 2);
  const failing = guard(async () => { throw new Error("refused"); });
  await assert.rejects(failing, /refused/);
  assert.equal(await guard(async () => "after a refusal"), "after a refusal");   // a refusal frees it too
});
