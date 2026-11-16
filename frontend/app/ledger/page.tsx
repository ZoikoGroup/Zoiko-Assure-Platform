"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* The Ledger is now a tab of Audit & Reporting (components/nav.ts); this address opens it. */
export default function LedgerAddress() {
  return <LegacyRedirect screen="/ledger" />;
}
