"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* The Audit Package Builder is now the Audit Packages tab of Audit & Reporting (components/nav.ts); this address
   opens it. */
export default function AuditPackagesAddress() {
  return <LegacyRedirect screen="/packages" />;
}
