"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* The Privileged Access Log is now the Access Log tab of Audit & Reporting (components/nav.ts); this address
   opens it. */
export default function AccessLogAddress() {
  return <LegacyRedirect screen="/admin/access-log" />;
}
