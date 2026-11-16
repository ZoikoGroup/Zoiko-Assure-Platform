"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { AccessLog } from "@/components/audit-reporting/AccessLog";
import { AuditPackages } from "@/components/audit-reporting/AuditPackages";
import { HistoricalReconstruction } from "@/components/audit-reporting/HistoricalReconstruction";
import { Ledger } from "@/components/audit-reporting/Ledger";

/* Audit & Reporting (organisation sidebar, components/nav.ts): audit packages, the evidence ledger, historical
   reconstruction and the access log, one tab each. Their former addresses (/packages, /ledger, /history,
   /admin/access-log) open their tabs. */
const PANELS = { packages: AuditPackages, ledger: Ledger, history: HistoricalReconstruction, "access-log": AccessLog };

export default function AuditAndReporting() {
  return <Suspense fallback={null}><WorkspaceHub href="/audit-reporting" panels={PANELS} /></Suspense>;
}
