"use client";

import { Notice } from "./ui";

/* Separation-of-duties guidance for compensations (ZA-IAM-001 §10 and §18; audit FA-30). Guidance only. The backend
   records who approved a compensation but not who proposed it, has no materiality setting for it and runs no SoD check
   on the decision, so nothing here can confirm that an approval is independent, and nothing is blocked in the browser. */
export function CompensationSoDNote({ id, stage }: { id?: string; stage: "propose" | "approve" }) {
  return (
    <div id={id}>
      <Notice tone={stage === "approve" ? "warn" : "info"}>
        <p className="font-medium">Separation of duties</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          <li>Designing a compensation, or owning the controls it relies on, is a separate duty from giving its final assurance approval (ZA-IAM-001 §10).</li>
          <li>Where policy marks a compensation material, the final approval should come from an independently authorised assurance or risk reviewer.</li>
          <li>Where dual approval is required, one person must not fill more than one approval role, including through linked accounts.</li>
        </ul>
        <p className="mt-1">
          {stage === "approve"
            ? "Zoiko Assure requires a named human with the “Approve compensating controls” permission and records who approved. It does not record who proposed a compensation, has no materiality setting for it and does not check separation of duties, so this page cannot confirm that an approval is independent. Confirm independence before you approve."
            : "Approval happens in Governance. Zoiko Assure does not record who proposes a compensation and does not check separation of duties, so arrange for someone independent of you and of the control owner to approve it."}
        </p>
      </Notice>
    </div>
  );
}
