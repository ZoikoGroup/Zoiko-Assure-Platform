"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* IPE is now a tab of Evidence (components/nav.ts); this address opens it. Source run records (/ipe/{id}) are
   unchanged. */
export default function IpeAddress() {
  return <LegacyRedirect screen="/ipe" />;
}
