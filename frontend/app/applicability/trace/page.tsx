"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* Applicability Trace is now the Evaluation Trace tab of Decisions & Attestations (components/nav.ts); this
   address opens it. Evaluation records (/applicability/{id}) are unchanged. */
export default function EvaluationTraceAddress() {
  return <LegacyRedirect screen="/applicability/trace" />;
}
