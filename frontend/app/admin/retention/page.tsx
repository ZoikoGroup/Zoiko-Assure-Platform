"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* Retention & Legal Holds is now a tab of Evidence (components/nav.ts); this address opens it. */
export default function RetentionAddress() {
  return <LegacyRedirect screen="/admin/retention" />;
}
