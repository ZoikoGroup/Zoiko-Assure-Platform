"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* Historical Reconstruction is now a tab of Audit & Reporting (components/nav.ts); this address opens it. */
export default function HistoryAddress() {
  return <LegacyRedirect screen="/history" />;
}
