"use client";

import { type ReactNode, useId, useMemo, useRef, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import {
  actorLabel, assignBody, closeBody, closureKind, eventTitle, issueActions, partitionVerification, remediationStartSeq,
  singleFlight, transitionBody, unavailableReason, type Issue, type IssueAction, type IssueEvent,
} from "@/lib/issues";
import { A, Badge, Drawer, EmptyState, ErrorBox, FieldError, KV, LoadingSkeleton, Modal, Notice, ReasonField, useAction } from "@/components/ui";

/* The Findings & Actions issue lifecycle (WF-STA-04; ZA-STA-001 §17) against its backend routes:
   POST /issues/{id}/assign, /transition and /close, and GET /issues/{id}/events. What is offered comes from
   lib/issues.ts (the backend's rules, mirrored); every request is still decided by the backend. A dialog stays open
   with its values after a refusal, shows the refusal as given, and asks the page to reload the issue, so nothing on
   screen keeps a state the backend no longer holds. */

type Name = (id?: string | null) => string;

/** What a dialog reports back: the backend's resulting issue on success; on failure, that the issue should be re-read. */
type DialogProps = { issue: Issue; name: Name; onClose: () => void; onDone: (result: Issue, message: string) => void; onFailed: () => void };

export function severityCode(severity: string) {
  return severity === "CRITICAL" || severity === "HIGH" ? "FAIL" : "WARN";
}

/** What verifies closure of an issue of this type, in words. */
export function closureRequirement(issueType: string): string {
  switch (closureKind(issueType)) {
    case "SNAPSHOT": return "Closed only against a new PROVEN assurance snapshot of the same obligation, computed after remediation started and from a test run or evidence recorded after it (ZA-STA-001 §17).";
    case "EVALUATION": return "Closed only against a new OPERATING evaluation of the same control, computed after remediation started and from a test run or evidence recorded after it (ZA-STA-001 §17).";
    case "UNVERIFIED": return `Closed from Pending verification without a verification record here: the closure criteria of ${issueType} issues are owned outside this workflow.`;
    default: return `The server defines no closure for ${issueType} issues.`;
  }
}

/** The actions the backend accepts for the issue now. Buttons a rule blocks stay visible, with the rule written out
 *  below them (a disabled button's tooltip is not reachable by keyboard or touch). */
export function IssueActionButtons({ issue, allowed, deniedWhy, onAction }: {
  issue: Issue; allowed: boolean; deniedWhy?: string; onAction: (a: IssueAction) => void;
}) {
  const actions = issueActions(issue);
  if (!actions.length) return <span className="text-xs text-slate-500">Final: no further actions</span>;
  const rules = allowed ? [...new Set(actions.map((a) => a.blocked).filter((b): b is string => !!b))] : [];
  return (
    <div>
      <div className="flex flex-wrap gap-1">
        {actions.map((a) => (
          <button key={`${a.kind}-${a.to ?? ""}`} type="button" className="btn-ghost" aria-haspopup="dialog"
            disabled={!allowed || !!a.blocked} title={!allowed ? deniedWhy : a.blocked} onClick={() => onAction(a)}>{a.label}</button>
        ))}
      </div>
      {rules.map((r) => <p key={r} className="mt-1 max-w-xs whitespace-normal text-[11px] leading-4 text-slate-500">{r}</p>)}
    </div>
  );
}

/* ------------------------------------------------------------------ detail and history */

/** One issue: its record, the actions available now, and its recorded history. `issue` is the current row from
 *  GET /issues; null when the list no longer holds it (then `listError` says why, if the list failed to load). */
export function IssueDrawer({ issue, listError, version, name, actions, onClose }: {
  issue: Issue | null; listError: unknown; version: number; name: Name; actions: ReactNode; onClose: () => void;
}) {
  const headId = useId();
  return (
    <Drawer open onClose={onClose} title={issue?.title ?? "Issue"}>
      {!issue ? (
        listError ? <ErrorBox error={listError} /> : <Notice tone="warn">This issue is no longer in the organisation&apos;s issue list.</Notice>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge code={issue.state} /><Badge code={severityCode(issue.severity)} label={issue.severity} />
            <span className="mono text-xs text-slate-600">{issue.priority}</span>
          </div>
          <KV cols={2} items={[
            ["Type", <span key="t" className="mono text-xs">{issue.issue_type}</span>],
            ["Owner", issue.owner_actor_id ? name(issue.owner_actor_id) : <span key="o" className="text-slate-500">Not assigned</span>],
            ["Raised from", <span key="s" className="mono text-xs">{issue.source_ref_type} {issue.source_ref_id}</span>],
            ["Created", fmtDate(issue.created_at, true)],
            ["Due", issue.due_at ? fmtDate(issue.due_at, true) : <span key="d" className="text-slate-500">Not set</span>],
            ["Issue ID", <span key="i" className="mono text-xs">{issue.issue_id}</span>],
          ]} />
          <section aria-labelledby={`${headId}-actions`}>
            <h3 id={`${headId}-actions`} className="mb-2 text-[13px] font-semibold text-slate-900">Actions</h3>
            {actions}
            <p className="mt-2 text-xs text-slate-500">{closureRequirement(issue.issue_type)} Closing or moving an issue never changes assurance state.</p>
          </section>
          <section aria-labelledby={`${headId}-history`}>
            <h3 id={`${headId}-history`} className="mb-2 text-[13px] font-semibold text-slate-900">History <span className="font-normal text-slate-500">(oldest first)</span></h3>
            <IssueHistory issueId={issue.issue_id} version={version} name={name} />
          </section>
        </div>
      )}
    </Drawer>
  );
}

/** GET /issues/{id}/events as recorded. A failed read is shown as an error, never as an empty history. */
export function IssueHistory({ issueId, version, name }: { issueId: string; version: number; name: Name }) {
  const { data, error, loading } = useApi<IssueEvent[]>(`/issues/${issueId}/events`, [version]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return loading ? <LoadingSkeleton rows={3} /> : null;
  if (!data.length) return <EmptyState compact title="No events recorded" description="The server holds no recorded events for this issue." />;
  const first = data[0].event_type;
  return (
    <>
      {first !== "CREATED" && first !== "BASELINE" && (
        <p className="mb-2 text-xs text-slate-500">No creation event is recorded for this issue; its history starts at the first recorded change.</p>
      )}
      <ol className="divide-y divide-slate-100 rounded-md border border-slate-200">
        {data.map((e) => (
          <li key={e.event_id} className="px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-1.5 text-[13px] text-slate-900">
              <span className="font-medium">{eventTitle(e)}</span>
              {e.event_type === "ASSIGNED"
                ? <span className="text-slate-700">· owner {name(e.owner_actor_id)}</span>
                : e.from_state && e.from_state !== e.to_state
                  ? <><Badge code={e.from_state} /><span aria-hidden>→</span><span className="sr-only">to</span><Badge code={e.to_state} /></>
                  : <Badge code={e.to_state} />}
            </div>
            <p className="mt-0.5 text-[11px] text-slate-500">
              {actorLabel(e.actor_id, (id) => name(id))} · {fmtDate(e.created_at, true)} · record #{e.record_seq}
            </p>
            {e.reason && <p className="mt-1 whitespace-pre-wrap break-words text-xs text-slate-700">{e.reason}</p>}
            {e.provenance?.kind === "MIGRATION_BASELINE" && <p className="mt-1 text-[11px] text-amber-800">{e.provenance.note}</p>}
          </li>
        ))}
      </ol>
    </>
  );
}

/* ------------------------------------------------------------------ dialogs */

/** Submission state shared by the dialogs: one request at a time (also within a single tick), the refusal kept for
 *  display, and on any refusal the page is asked to re-read the issue while the dialog keeps what was typed. */
function useSubmit(onFailed: () => void) {
  const a = useAction();
  const guard = useRef(singleFlight()).current;
  const submit = (request: () => Promise<void>) => guard(() => a.run(async () => {
    try { await request(); } catch (e) { onFailed(); throw e; }
  }));
  return { busy: a.busy, error: a.error, submit };
}

/** After a 404 or 409 the issue was re-read; say so, and what the backend holds now. */
function Refusal({ error, issue, name }: { error: unknown; issue: Issue; name: Name }) {
  if (!error) return null;
  const reread = error instanceof ApiError && (error.status === 404 || error.status === 409);
  return (
    <div className="mt-3">
      <ErrorBox error={error} />
      {reread && <p className="-mt-1 text-[12px] text-slate-600">The issue was reloaded from the server: it is now <Badge code={issue.state} />, owner {issue.owner_actor_id ? name(issue.owner_actor_id) : "not assigned"}.</p>}
    </div>
  );
}

function Footer({ busy, onClose, confirm, busyLabel, disabled, danger, onSubmit }: {
  busy: boolean; onClose: () => void; confirm: string; busyLabel: string; disabled?: boolean; danger?: boolean; onSubmit: () => void;
}) {
  return (
    <div className="mt-4 flex justify-end gap-2">
      <button type="button" className="btn-ghost" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className={danger ? "btn-danger" : "btn-primary"} disabled={busy || disabled} aria-busy={busy} onClick={onSubmit}>{busy ? busyLabel : confirm}</button>
    </div>
  );
}

const Heading = ({ issue }: { issue: Issue }) => (
  <p><span className="font-medium">{issue.title}</span> · <span className="mono text-xs">{issue.issue_type}</span> · currently <Badge code={issue.state} /></p>
);

type Person = { actor_id: string; display_name: string; email?: string | null; actor_type: string };

/** POST /issues/{id}/assign: an accountable owner from the organisation's people (GET /actors?actor_type=HUMAN; the
 *  backend accepts only a HUMAN principal of the same organisation) and a required reason. */
export function AssignOwnerDialog({ issue, name, onClose, onDone, onFailed }: DialogProps) {
  const people = useApi<Person[]>("/actors?actor_type=HUMAN");
  const [owner, setOwner] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const ownerRef = useRef<HTMLSelectElement>(null);
  const id = useId();
  const s = useSubmit(onFailed);
  const sorted = useMemo(() => [...(people.data ?? [])].sort((x, y) => x.display_name.localeCompare(y.display_name)), [people.data]);
  const gone = unavailableReason(issue, { kind: "assign" });
  const ownerMissing = !owner;
  const reasonMissing = !reason.trim();
  const close = () => { if (!s.busy) onClose(); };
  const send = () => {
    setTried(true);
    if (ownerMissing) { ownerRef.current?.focus(); return; }
    if (reasonMissing) { reasonRef.current?.focus(); return; }
    s.submit(async () => {
      const res = await api<Issue>(`/issues/${issue.issue_id}/assign`, { method: "POST", json: assignBody(owner, reason) });
      onDone(res, `Owner recorded: ${name(res.owner_actor_id)}.`);
    });
  };
  return (
    <Modal open onClose={close} title={issue.owner_actor_id ? "Reassign issue owner" : "Assign issue owner"} size="sm">
      <div className="space-y-2 text-[13px] text-slate-700">
        <Heading issue={issue} />
        <p>Current owner: {issue.owner_actor_id ? name(issue.owner_actor_id) : "none"}.</p>
        <p>The owner is the person accountable for remediating this issue. Only people of this organisation can own an issue, not agents or services. Assigning is recorded in the issue history and the audit trail; it does not change the issue&apos;s state or any assurance state, and sets no due date.</p>
      </div>
      {gone && <div className="mt-3"><Notice tone="warn">{gone}</Notice></div>}
      <div className="mt-3">
        <label className="label" htmlFor={`${id}-owner`}>Owner (required)</label>
        {people.error ? <ErrorBox error={people.error} />
          : !people.data ? <LoadingSkeleton rows={1} />
          : sorted.length === 0 ? <p className="text-[13px] text-slate-500">No people are listed in this organisation&apos;s directory, so there is nobody to assign.</p>
          : (
            <select ref={ownerRef} id={`${id}-owner`} className="input" value={owner} onChange={(e) => setOwner(e.target.value)}
              aria-invalid={tried && ownerMissing} aria-describedby={tried && ownerMissing ? `${id}-owner-error` : undefined}>
              <option value="">Choose a person…</option>
              {sorted.map((p) => (
                <option key={p.actor_id} value={p.actor_id} disabled={p.actor_id === issue.owner_actor_id}>
                  {p.display_name}{p.email ? ` · ${p.email}` : ""}{p.actor_id === issue.owner_actor_id ? " (current owner)" : ""}
                </option>
              ))}
            </select>
          )}
        <FieldError id={`${id}-owner`} message={tried && ownerMissing && sorted.length > 0 && "Choose the person who will own this issue."} />
      </div>
      <ReasonField className="mt-3" id={`${id}-reason`} label="Reason (required)" required rows={3} value={reason} onChange={setReason}
        showError={tried && reasonMissing} inputRef={reasonRef} />
      <Refusal error={s.error} issue={issue} name={name} />
      <Footer busy={s.busy} onClose={close} confirm={issue.owner_actor_id ? "Reassign owner" : "Assign owner"} busyLabel="Assigning…"
        disabled={!!gone || !people.data || sorted.length === 0} onSubmit={send} />
    </Modal>
  );
}

const MOVE_TEXT: Record<string, string> = {
  TRIAGED: "Triage records that ownership, severity and treatment path are established (ZA-GLS-001 §07).",
  PENDING_VERIFICATION: "Records that remediation is done and awaits verification.",
};

/** POST /issues/{id}/transition: a move along the workflow, a cancellation (reason required) or, for an issue type
 *  whose closure is not verified here, a closure. */
export function TransitionDialog({ issue, action, name, onClose, onDone, onFailed }: DialogProps & { action: IssueAction }) {
  const to = action.to!;
  const cancel = action.kind === "cancel";
  const closing = action.kind === "close";
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const s = useSubmit(onFailed);
  const gone = unavailableReason(issue, action);
  const invalid = cancel && !reason.trim();
  const close = () => { if (!s.busy) onClose(); };
  const send = () => {
    setTried(true);
    if (invalid) { field.current?.focus(); return; }
    s.submit(async () => {
      const res = await api<Issue>(`/issues/${issue.issue_id}/transition`, { method: "POST", json: transitionBody(to, reason) });
      onDone(res, `Issue moved to ${res.state}.`);
    });
  };
  const about = cancel
    ? "The issue moves to CANCELLED. This cannot be undone. The reason is recorded in the issue history and the audit trail."
    : closing
      ? `Closing is final: a closed issue cannot be moved again. ${closureRequirement(issue.issue_type)}`
      : to === "IN_REMEDIATION"
        ? issue.state === "PENDING_VERIFICATION"
          ? "Returns the issue to remediation and starts a new remediation cycle: a result computed before this point can no longer verify closure."
          : "Starts the remediation cycle. A result that verifies closure has to be computed after this point."
        : MOVE_TEXT[to] ?? "";
  return (
    <Modal open onClose={close} title={cancel ? "Cancel issue" : closing ? "Close issue" : `${action.label}`} size="sm">
      <div className="space-y-2 text-[13px] text-slate-700">
        <Heading issue={issue} />
        <p><Badge code={issue.state} /> <span aria-hidden>→</span><span className="sr-only">to</span> <Badge code={to} /></p>
        <p>{about}</p>
        {to === "TRIAGED" && issue.owner_actor_id && <p>Owner: {name(issue.owner_actor_id)}.</p>}
        <p>Moving an issue never changes assurance state.</p>
      </div>
      {gone && <div className="mt-3"><Notice tone="warn">{gone}</Notice></div>}
      <ReasonField className="mt-3" id={`${id}-reason`} label={`${cancel ? "Cancellation reason" : "Reason"} (${cancel ? "required" : "optional"})`}
        required={cancel} rows={3} value={reason} onChange={setReason} showError={tried && invalid} inputRef={field} />
      <Refusal error={s.error} issue={issue} name={name} />
      <Footer busy={s.busy} onClose={close} danger={cancel} confirm={cancel ? "Cancel issue" : closing ? "Close issue" : action.label}
        busyLabel={cancel ? "Cancelling…" : closing ? "Closing…" : "Moving…"} disabled={!!gone} onSubmit={send} />
    </Modal>
  );
}

type SnapshotRow = { snapshot_id: string; state: string; record_cutoff_seq: number; period_from: string; period_to: string; perspective: string; created_at: string };
type EvaluationRow = { evaluation_id: string; state: string; record_cutoff_seq: number; window_from: string; window_to: string; created_at: string };

/** POST /issues/{id}/close against the result STA §17 names for the issue's level. Candidates are read from the backend:
 *  the obligation's snapshots (GET /snapshots/{source} → GET /obligations/{lineage}) or the control's stored evaluations
 *  (GET /control-evaluations/{source} → GET /controls/{id}, which lists the 20 most recent). Only those the backend's
 *  visible rules allow are offered; without one, closure is not offered at all. */
export function VerifiedCloseDialog({ issue, version, name, onClose, onDone, onFailed }: DialogProps & { version: number }) {
  const kind = closureKind(issue.issue_type) as "SNAPSHOT" | "EVALUATION";
  const snapshotKind = kind === "SNAPSHOT";
  const events = useApi<IssueEvent[]>(`/issues/${issue.issue_id}/events`, [version]);
  const source = useApi<any>(snapshotKind ? `/snapshots/${issue.source_ref_id}` : `/control-evaluations/${issue.source_ref_id}`);
  const lineage: string | undefined = snapshotKind ? source.data?.obligation_lineage_id : undefined;
  const controlId: string | undefined = snapshotKind ? undefined : source.data?.evaluation?.control_implementation_id;
  const dossier = useApi<{ snapshots: SnapshotRow[] }>(lineage ? `/obligations/${lineage}` : null, [version]);
  const control = useApi<{ evaluations: EvaluationRow[] }>(controlId ? `/controls/${controlId}` : null, [version]);
  const rows: (SnapshotRow | EvaluationRow)[] | undefined = snapshotKind ? dossier.data?.snapshots : control.data?.evaluations;
  const started = events.data ? remediationStartSeq(events.data) : null;
  const split = rows && events.data ? partitionVerification(rows, kind, started) : null;
  const rowId = (r: SnapshotRow | EvaluationRow) => ("snapshot_id" in r ? r.snapshot_id : r.evaluation_id);

  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const s = useSubmit(onFailed);
  const gone = unavailableReason(issue, { kind: "close", to: "CLOSED" });
  const choice = split?.eligible.find((r) => rowId(r) === selected);
  const reasonMissing = !reason.trim();
  const what = snapshotKind ? "snapshot" : "evaluation";
  const close = () => { if (!s.busy) onClose(); };
  const send = () => {
    setTried(true);
    if (!choice) return;
    if (reasonMissing) { field.current?.focus(); return; }
    s.submit(async () => {
      const res = await api<Issue>(`/issues/${issue.issue_id}/close`, { method: "POST", json: closeBody(kind, rowId(choice), reason) });
      onDone(res, `Issue ${res.state.toLowerCase()}, verified by ${what} ${rowId(choice).slice(0, 8)}….`);
    });
  };
  const readError = events.error ?? source.error ?? dossier.error ?? control.error;
  const sourceLink = lineage ? <A href={`/obligations/${lineage}`}>the obligation&apos;s dossier</A> : controlId ? <A href={`/controls/${controlId}`}>the control&apos;s page</A> : null;

  return (
    <Modal open onClose={close} title="Close issue with verification" size="md">
      <div className="space-y-2 text-[13px] text-slate-700">
        <Heading issue={issue} />
        <p>{closureRequirement(issue.issue_type)} Closing is final and changes the issue only: the verifying {what} already holds the updated assurance state.</p>
      </div>
      {gone && <div className="mt-3"><Notice tone="warn">{gone}</Notice></div>}
      <div className="mt-3">
        {readError ? (
          <><p className="mb-1 text-[12px] font-medium text-slate-700">The records closure is verified against could not be read:</p><ErrorBox error={readError} /></>
        ) : !split ? <LoadingSkeleton rows={3} /> : (
          <fieldset aria-describedby={`${id}-scope`}>
            <legend className="label">Verifying {what} (required)</legend>
            <p id={`${id}-scope`} className="mb-2 text-xs text-slate-500">
              {snapshotKind ? "Snapshots of the obligation this issue was raised for" : "Stored evaluations of the control this issue was raised for (the 20 most recent)"};
              {" "}remediation {started === null ? "has no recorded start" : <>started at record #{started}</>}. The server also checks that the {what} uses a test run or evidence recorded after that, and may still refuse it.
            </p>
            {split.eligible.length === 0 ? (
              <Notice tone="warn">No {what} can verify this closure yet, so closure is not available. {snapshotKind
                ? <>Record the retest, then freeze a new snapshot on {sourceLink}; it must be PROVEN.</>
                : <>Record the retest, then evaluate the control again on {sourceLink}; the evaluation must be OPERATING.</>}</Notice>
            ) : (
              <div className="space-y-1.5">
                {split.eligible.map((r) => {
                  const rid = rowId(r);
                  return (
                    <label key={rid} className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-[13px] ${selected === rid ? "border-navy-400 bg-navy-50/40" : "border-slate-200"}`}>
                      <input type="radio" name={`${id}-verification`} value={rid} checked={selected === rid} onChange={() => setSelected(rid)} className="mt-1" />
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-1.5"><Badge code={r.state} /><span className="mono text-[11px] text-slate-600">{rid}</span></span>
                        <span className="mt-0.5 block text-xs text-slate-600">
                          {"period_from" in r ? <>Period {fmtDate(r.period_from)} → {fmtDate(r.period_to)} · {r.perspective}</> : <>Window {fmtDate(r.window_from, true)} → {fmtDate(r.window_to, true)}</>}
                          {" "}· record cutoff #{r.record_cutoff_seq} · {"period_from" in r ? "frozen" : "evaluated"} {fmtDate(r.created_at, true)}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            {split.excluded.length > 0 && (
              <details className="mt-2 text-xs text-slate-600">
                <summary className="cursor-pointer">{split.excluded.length} other {what}(s) cannot verify closure</summary>
                <ul className="mt-1 space-y-0.5 pl-4">
                  {split.excluded.map(({ row, reasons }) => (
                    <li key={rowId(row)} className="list-disc"><span className="mono text-[11px]">{rowId(row).slice(0, 8)}…</span> {fmtDate(row.created_at, true)}: {reasons.join("; ")}</li>
                  ))}
                </ul>
              </details>
            )}
            <FieldError id={`${id}-verification`} message={tried && !choice && split.eligible.length > 0 && `Choose the ${what} that verifies the remediation.`} />
          </fieldset>
        )}
      </div>
      <ReasonField className="mt-3" id={`${id}-reason`} label="Closure reason (required)" required rows={3} value={reason} onChange={setReason}
        showError={tried && reasonMissing} inputRef={field} />
      <Refusal error={s.error} issue={issue} name={name} />
      <Footer busy={s.busy} onClose={close} confirm="Close issue" busyLabel="Closing…" disabled={!!gone || !split || split.eligible.length === 0} onSubmit={send} />
    </Modal>
  );
}
