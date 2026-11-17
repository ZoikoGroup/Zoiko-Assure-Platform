"use client";

import { useId, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Badge, ErrorBox, Modal, Notice, ReasonField, useAction } from "./ui";

/** The current determination revision being decided, as GET /applicability/determinations and the
 *  `current_determination` of GET /applicability/evaluations/{id} return it, with the display names callers add. */
export type AttestTarget = {
  lineage_id: string;
  revision_id?: string | null;
  evaluation_id?: string | null;
  state: string;
  outcome: string;
  needs_review?: boolean | null;
  review_reason?: string | null;
  subject?: string | null;
  obligation_definition?: string | null;
};

/* The exact determination revision the attestor opened and the evaluation the decision rests on. Sent with the decision
   so the backend refuses (409 STALE_DETERMINATION_REVISION / STALE_EVALUATION) a decision on anything newer instead of
   applying it (APP §12, §16). Both are taken from the record as received and never guessed; without both, nothing is
   sent. An adopted determination flagged for review is re-decided on the newer evaluation that flagged it
   (backend engines/applicability.attest uses the latest evaluation then), and the record identifies only the
   evaluation it was adopted on, so that evaluation is not known here. */
type Pin = { ok: true; revision_id: string; evaluation_id: string } | { ok: false; reason: "review" | "missing"; missing: string[] };
const present = (v?: string | null): v is string => typeof v === "string" && v.length > 0;

function pinOf(det: AttestTarget): Pin {
  if (det.state === "ADOPTED" && det.needs_review) return { ok: false, reason: "review", missing: ["evaluation_id"] };
  const missing = [present(det.revision_id) ? null : "revision_id", present(det.evaluation_id) ? null : "evaluation_id"].filter((x): x is string => !!x);
  return present(det.revision_id) && present(det.evaluation_id)
    ? { ok: true, revision_id: det.revision_id, evaluation_id: det.evaluation_id }
    : { ok: false, reason: "missing", missing };
}

/* The decisions the backend accepts from each determination state (backend engines/applicability.py ATTESTABLE, pinned
   by backend/tests/test_attestation_api_contract.py): ADOPT from PROPOSED, IN_REVIEW or an adopted determination flagged
   for review; REJECT from IN_REVIEW only, so a PROPOSED determination is challenged into review before it can be
   rejected. The API serves no list of these transitions, so only the allowed ones are offered here; the backend still
   decides every request. */
type Decision = "ADOPT" | "REJECT";
const PERMITS: Record<Decision, (det: AttestTarget) => boolean> = {
  ADOPT: (det) => det.state === "PROPOSED" || det.state === "IN_REVIEW" || (det.state === "ADOPTED" && !!det.needs_review),
  REJECT: (det) => det.state === "IN_REVIEW",
};

const STALE = ["STALE_DETERMINATION_REVISION", "STALE_EVALUATION"];
const isStale = (e: unknown) => e instanceof ApiError && e.status === 409 && STALE.includes((e.detail as { error?: string } | null)?.error ?? "");

/* WF-APP-03 adoption attestation: human-only (INV-01), explicit authority basis, stale-revision protected. */
export function AttestModal({ det, onClose, onDone }: { det: AttestTarget | null; onClose: () => void; onDone: () => void }) {
  // No decision is preselected: the attestor must choose ADOPT or REJECT explicitly (ZA-QA-001 §16, no deceptive defaults).
  const [decision, setDecision] = useState<"" | Decision>("");
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
  const pin = det ? pinOf(det) : null;
  const sign = () => {
    if (!decision || !det || !pin?.ok) return;  // never sent without the revision and evaluation it is for
    if (!PERMITS[decision](det)) return;  // a transition the backend refuses is never offered or sent
    if (reasonMissing) { setTried(true); reasonRef.current?.focus(); return; }
    a.run(async () => {
      await api(`/applicability/determinations/${det.lineage_id}/attest`, { method: "POST", json: {
        decision, authority_basis: basis, reason: reasonText || null, revision_id: pin.revision_id, evaluation_id: pin.evaluation_id } });
      onDone();
    });
  };
  if (!det || !pin) return null;
  const header = (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
      <span className="font-medium">{det.subject}</span> · <span className="mono text-xs">{det.obligation_definition}</span> · <Badge code={det.outcome} /> · <Badge code={det.state} />
    </div>
  );
  if (!pin.ok) {
    return (
      <Modal open onClose={onClose} title="Adoption attestation">
        {header}
        <div role="alert">
          <Notice tone="warn">
            {pin.reason === "review" ? (
              <>This adopted determination is flagged for review because of a newer evaluation. A new decision rests on that
                evaluation, but the determination record identifies only the evaluation it was adopted on, so the decision
                cannot be tied to the evaluation you reviewed. Nothing has been sent.
                {det.review_reason && <span className="mt-1 block text-xs">Review reason: {det.review_reason}</span>}</>
            ) : (
              <>This determination record arrived without its {pin.missing.map((m, i) => (
                <span key={m}>{i > 0 && " and "}<code className="mono text-xs">{m}</code></span>))}, so the decision cannot be tied
                to the exact revision and evaluation you reviewed. Nothing has been sent. Reload the page and try again; if it
                persists, report it.</>
            )}
          </Notice>
        </div>
        <div className="mt-4 flex justify-end"><button type="button" className="btn-ghost" onClick={onClose}>Close</button></div>
      </Modal>
    );
  }
  const offered = (["ADOPT", "REJECT"] as const).filter((d) => PERMITS[d](det));
  const hintId = `${fid}-decision-hint`;
  return (
    <Modal open onClose={onClose} title="Adoption attestation">
      <ErrorBox error={a.error} />
      {isStale(a.error) && (
        <div className="-mt-1 mb-3 flex flex-wrap items-center gap-2 text-[13px] text-slate-700">
          Nothing was signed. Reload to review the current revision before deciding.
          <button type="button" className="btn-ghost" onClick={onDone}>Close and reload</button>
        </div>
      )}
      {header}
      <Notice tone="warn">Attestation is a personal, human act recorded with your identity and authority basis. Agents and service identities cannot adopt determinations. UNCERTAIN outcomes cannot be adopted.</Notice>
      <div className="mt-3 grid gap-3">
        <fieldset aria-describedby={!PERMITS.REJECT(det) ? hintId : undefined}>
          <legend className="label">Decision (required)</legend>
          <div className="flex gap-4 text-sm">
            {offered.map((d) => (
              <label key={d} className="flex items-center gap-1.5"><input type="radio" required name={`${fid}-decision`} value={d} checked={decision === d} onChange={() => setDecision(d)} />{d}</label>
            ))}
          </div>
          {!PERMITS.REJECT(det) && (
            <p id={hintId} className="mt-1 text-[12px] leading-5 text-slate-500">
              {det.state === "PROPOSED"
                ? "A PROPOSED determination can be adopted, not rejected. To decline it, challenge it from its evaluation trace; once it is IN_REVIEW it can be rejected."
                : offered.length ? `Rejection is not available for a determination in state ${det.state}.`
                : `No decision can be signed for a determination in state ${det.state}.`}
            </p>
          )}
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
