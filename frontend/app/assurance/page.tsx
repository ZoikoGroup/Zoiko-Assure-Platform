"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* Assurance Periods is now a tab of Obligations (components/nav.ts); this address opens it. */
export default function AssurancePeriodsAddress() {
  return <LegacyRedirect screen="/assurance" />;
}
