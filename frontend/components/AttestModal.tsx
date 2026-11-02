"use client";

import { useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Badge, ErrorBox, Modal, Notice, ReasonField, useAction } from "./ui";

/* WF-APP-03 adoption attestation: human-only (INV-01), explicit authority basis, stale-revision protected. */
export function AttestModal({ det, onClose, onDone }: { det: any; onClose: () => void; onDone: () => void }) {
  // No decision is preselected: the attestor must choose ADOPT or REJECT explicitly (ZA-QA-001 §16, no deceptive defaults).
  const [decision, setDecision] = useState<"" | "ADOPT" | "REJECT">("");
  const [basis, setBasis] = useState("");
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [tried, setTried] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const a = useAction();
  const fid = useId();
  // A rejection needs a reason (ZA-APP-001 §09; the backend refuses an empty one). An adoption reason stays optional.
  const reasonText = reason.trim();
  const reasonMissing = decision === "REJECT" && !reasonText;
  const sign = () => {
    if (!decision) return;
    if (reasonMissing) { setTried(true); reasonRef.current?.focus(); return; }
    a.run(async () => {
      await api(`/applicability/determinations/${det.lineage_id}/attest`, { method: "POST", json: { decision, authority_basis: basis, reason: reasonText || null } });
      onDone();
    });
  };
  if (!det) return null;
  return (
    <Modal open onClose={onClose} title="Adoption attestation">
      <ErrorBox error={a.error} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{det.subject}</span> · <span className="mono text-xs">{det.obligation_definition}</span> · <Badge code={det.outcome} /> · <Badge code={det.state} />
      </div>
      <Notice tone="warn">Attestation is a personal, human act recorded with your identity and authority basis. Agents and service identities cannot adopt determinations. UNCERTAIN outcomes cannot be adopted.</Notice>
      <div className="mt-3 grid gap-3">
        <fieldset>
          <legend className="label">Decision (required)</legend>
          <div className="flex gap-4 text-sm">
            {(["ADOPT", "REJECT"] as const).map((d) => (
              <label key={d} className="flex items-center gap-1.5"><input type="radio" required name={`${fid}-decision`} value={d} checked={decision === d} onChange={() => setDecision(d)} />{d}</label>
            ))}
          </div>
        </fieldset>
        <div><label className="label" htmlFor={`${fid}-basis`}>Authority basis (required)</label><input id={`${fid}-basis`} required className="input" placeholder="Enter your applicable authority basis" value={basis} onChange={(e) => setBasis(e.target.value)} /></div>
        <ReasonField id={`${fid}-reason`} label={`Reason${decision === "REJECT" ? " (required)" : decision === "ADOPT" ? " (optional)" : ""}`} required={decision === "REJECT"}
          value={reason} onChange={setReason} showError={tried && reasonMissing} inputRef={reasonRef} />
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          I have reviewed the evaluation trace, its facts and source mappings, and I make this decision personally under the authority stated.</label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className={decision === "REJECT" ? "btn-danger" : "btn-primary"} disabled={!decision || !ack || a.busy || !basis.trim()}
          onClick={sign}>{decision === "ADOPT" ? "Sign adoption" : decision === "REJECT" ? "Sign rejection" : "Sign decision"}</button>
      </div>
    </Modal>
  );
}
