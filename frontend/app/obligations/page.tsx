"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { AssurancePeriods } from "@/components/obligations/AssurancePeriods";
import { ObligationDossiers } from "@/components/obligations/ObligationDossiers";

/* Obligations (organisation sidebar, components/nav.ts): the obligation dossiers and their period assurance, one tab
   each. The former Assurance Periods address (/assurance) opens its tab. */
const PANELS = { current: ObligationDossiers, "assurance-periods": AssurancePeriods };

export default function Obligations() {
  return <Suspense fallback={null}><WorkspaceHub href="/obligations" panels={PANELS} /></Suspense>;
}
