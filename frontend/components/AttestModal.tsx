"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, ErrorBox, Modal, Notice, useAction } from "./ui";

/* WF-APP-03 adoption attestation: human-only (INV-01), explicit authority basis, stale-revision protected. */
export function AttestModal({ det, onClose, onDone }: { det: any; onClose: () => void; onDone: () => void }) {
  const [decision, setDecision] = useState("ADOPT");
  const [basis, setBasis] = useState("Board delegation — Chief Compliance Officer");
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const a = useAction();
  if (!det) return null;
  return (
    <Modal open onClose={onClose} title="Adoption attestation">
      <ErrorBox error={a.error} />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{det.subject}</span> · <span className="mono text-xs">{det.obligation_definition}</span> · <Badge code={det.outcome} /> · <Badge code={det.state} />
      </div>
      <Notice tone="warn">Attestation is a personal, human act recorded with your identity and authority basis. Agents and service identities cannot adopt determinations. UNCERTAIN outcomes cannot be adopted.</Notice>
      <div className="mt-3 grid gap-3">
        <div className="flex gap-4 text-sm">
          {["ADOPT", "REJECT"].map((d) => (
            <label key={d} className="flex items-center gap-1.5"><input type="radio" checked={decision === d} onChange={() => setDecision(d)} />{d}</label>
          ))}
        </div>
        <div><label className="label">Authority basis</label><input className="input" value={basis} onChange={(e) => setBasis(e.target.value)} /></div>
        <div><label className="label">Reason {decision === "REJECT" && "(required)"}</label><textarea className="input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          I have reviewed the evaluation trace, its facts and source mappings, and I make this decision personally under the authority stated.</label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className={decision === "ADOPT" ? "btn-primary" : "btn-danger"} disabled={!ack || a.busy || !basis}
          onClick={() => a.run(async () => {
            await api(`/applicability/determinations/${det.lineage_id}/attest`, { method: "POST", json: { decision, authority_basis: basis, reason: reason || null } });
            onDone();
          })}>{decision === "ADOPT" ? "Sign adoption" : "Sign rejection"}</button>
      </div>
    </Modal>
  );
}
