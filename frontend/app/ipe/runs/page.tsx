"use client";

import { LegacyRedirect } from "@/components/WorkspaceHub";

/* Source Runs is now a tab of Evidence (components/nav.ts); this address opens it. */
export default function SourceRunsAddress() {
  return <LegacyRedirect screen="/ipe/runs" />;
}
