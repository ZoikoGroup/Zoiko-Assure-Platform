"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { ApplicabilityReview } from "@/components/decisions/ApplicabilityReview";
import { ApplicabilityTrace } from "@/components/decisions/ApplicabilityTrace";

/* Decisions & Attestations (organisation sidebar, components/nav.ts): applicability determinations awaiting a human
   decision, attested from the Decisions tab, and every evaluation behind them. Signed attestations are listed on each
   evaluation's trace (/applicability/{id}); the backend offers no list of them. /applicability/trace opens its tab. */
const PANELS = { decisions: ApplicabilityReview, "evaluation-trace": ApplicabilityTrace };

export default function DecisionsAndAttestations() {
  return <Suspense fallback={null}><WorkspaceHub href="/applicability" panels={PANELS} /></Suspense>;
}
