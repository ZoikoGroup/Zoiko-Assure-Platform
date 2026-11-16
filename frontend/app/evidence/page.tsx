"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { EvidenceRecords } from "@/components/evidence/EvidenceRecords";
import { IpeChecks } from "@/components/evidence/IpeChecks";
import { RetentionAndLegalHolds } from "@/components/evidence/RetentionAndLegalHolds";
import { SourceRuns } from "@/components/evidence/SourceRuns";

/* Evidence (organisation sidebar, components/nav.ts): evidence records, IPE, source runs, and retention and legal holds,
   one tab each. Their former addresses (/ipe, /ipe/runs, /admin/retention) open their tabs. */
const PANELS = { records: EvidenceRecords, ipe: IpeChecks, "source-runs": SourceRuns, retention: RetentionAndLegalHolds };

export default function Evidence() {
  return <Suspense fallback={null}><WorkspaceHub href="/evidence" panels={PANELS} /></Suspense>;
}
