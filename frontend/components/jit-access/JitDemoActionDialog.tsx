"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Alert, Button, DescriptionList, Field, Modal, Select, Textarea } from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DEMO_NOW } from "./jitMockData";
import { ILLUSTRATIVE, addHours, grantedHours, hoursLabel, orgName, type Row } from "./model";

/* Confirmation for a local demonstration action: approve or reject a pending request, revoke or extend an active
   session. Confirming changes only the page's in-memory demonstration records (useJitAccessData): nothing is sent, no
   access is granted or removed and no approval is recorded. Cancel, Escape, Close and the backdrop change nothing. */

export type DemoAction =
  | { kind: "approve" | "reject"; requestId: string }
  | { kind: "revoke" | "extend"; sessionId: string };

export type AppliedAction =
  | { kind: "approve"; requestId: string; hours: number }
  | { kind: "reject"; requestId: string; reason: string }
  | { kind: "revoke"; sessionId: string; reason: string | null }
  | { kind: "extend"; sessionId: string; hours: number };

const TITLE = { approve: "Approve", reject: "Reject", revoke: "Revoke", extend: "Extend" } as const;

export function JitDemoActionDialog({ action, rows, onApply, onClose }:
  { action: DemoAction; rows: Row[]; onApply: (a: AppliedAction) => void; onClose: () => void }) {
  const row = "requestId" in action ? rows.find((r) => r.request.id === action.requestId) : rows.find((r) => r.session?.id === action.sessionId);
  const req = row?.request;
  const session = row?.session ?? null;
  const approveOptions = req ? ILLUSTRATIVE.durations.filter((h) => h <= req.requestedHours && h <= ILLUSTRATIVE.maxHours) : [];
  const extendOptions = req && session ? ILLUSTRATIVE.extensionHours.filter((h) => grantedHours(req, session) + h <= ILLUSTRATIVE.maxHours) : [];
  const [hours, setHours] = useState(String(action.kind === "extend" ? extendOptions[0] ?? "" : approveOptions[approveOptions.length - 1] ?? ""));
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  if (!row || !req) return null;

  const subject = "requestId" in action ? action.requestId : action.sessionId;
  const blocked = action.kind === "extend" && extendOptions.length === 0;
  const confirm = () => {
    if (action.kind === "approve") onApply({ kind: "approve", requestId: req.id, hours: Number(hours) });
    else if (action.kind === "reject") {
      if (!reason.trim()) { setReasonError(true); reasonRef.current?.focus(); return; }
      onApply({ kind: "reject", requestId: req.id, reason: reason.trim() });
    } else if (action.kind === "revoke" && session) onApply({ kind: "revoke", sessionId: session.id, reason: reason.trim() || null });
    else if (action.kind === "extend" && session && !blocked) onApply({ kind: "extend", sessionId: session.id, hours: Number(hours) });
    else return;
    onClose();
  };

  return (
    <Modal open onClose={onClose} title={`${TITLE[action.kind]} ${subject}?`} description="Local demonstration: nothing is sent and no access changes."
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant={action.kind === "reject" || action.kind === "revoke" ? "danger" : "primary"} disabled={blocked} onClick={confirm}>
          {TITLE[action.kind]} in demonstration
        </Button>
      </>}>
      <div className="space-y-4">
        <DescriptionList columns={2} items={[
          ["Requester", req.requester.name],
          ["Organisation", orgName(req.organisation)],
          ["Requested role", row.role?.label ?? req.roleId],
          ["Requested duration", hoursLabel(req.requestedHours)],
          ...(session ? [["Session", <code key="s" className="type-code">{session.id}</code>], ["Current expiry", fmtDate(session.expiresAt, true)]] as [string, ReactNode][] : []),
        ]} />

        {action.kind === "approve" && (
          <Field label="Approve for" hint="Illustrative rule: an approver may shorten the requested duration but not lengthen it.">
            <Select value={hours} onChange={(e) => setHours(e.target.value)} options={approveOptions.map((h) => ({ value: String(h), label: hoursLabel(h) }))} />
          </Field>
        )}
        {action.kind === "extend" && (blocked ? (
          <Alert tone="warning" title="Cannot extend in this demonstration">
            Any extension would take this access past the illustrative {ILLUSTRATIVE.maxHours}-hour maximum.
          </Alert>
        ) : (
          <Field label="Extend by" hint={`Illustrative rule: extensions add 1 or 2 hours, up to ${ILLUSTRATIVE.maxHours} hours in all.`}>
            <Select value={hours} onChange={(e) => setHours(e.target.value)} options={extendOptions.map((h) => ({ value: String(h), label: hoursLabel(h) }))} />
          </Field>
        ))}
        {(action.kind === "reject" || action.kind === "revoke") && (
          <Field label="Reason" required={action.kind === "reject"} optional={action.kind === "revoke"}
            hint={action.kind === "reject" ? "Illustrative rule: a rejection needs a reason." : undefined}
            error={reasonError ? "Enter the reason for rejecting this request." : undefined}>
            <Textarea ref={reasonRef} rows={3} maxLength={300} value={reason}
              onChange={(e) => { setReason(e.target.value); if (e.target.value.trim()) setReasonError(false); }} />
          </Field>
        )}

        {action.kind === "approve" && hours && (
          <p className="type-secondary text-fg-secondary">
            The page would then show this access as active from {fmtDate(DEMO_NOW, true)} until {fmtDate(addHours(DEMO_NOW, Number(hours)), true)}, on the demonstration clock.
          </p>
        )}
        {action.kind === "extend" && !blocked && session && hours && (
          <p className="type-secondary text-fg-secondary">The page would then show the expiry as {fmtDate(addHours(session.expiresAt, Number(hours)), true)}.</p>
        )}

        <Alert tone="warning" title="Local demonstration only">
          {action.kind === "approve" && "This only changes what this page shows. No access is granted, no approval is recorded and no request is sent."}
          {action.kind === "reject" && "This only changes what this page shows. No request is rejected anywhere and nothing is sent."}
          {action.kind === "revoke" && "This only changes what this page shows. No real session is ended and nothing is sent."}
          {action.kind === "extend" && "This only changes what this page shows. No real session is extended and nothing is sent."}
          {" "}The change is lost when the page reloads or the demonstration is reset.
        </Alert>
      </div>
    </Modal>
  );
}
