"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { AssurancePeriods } from "@/components/obligations/AssurancePeriods";
import { ObligationsRegister } from "./_register/ObligationsRegister";

/* Obligations (organisation sidebar, components/nav.ts): the Obligations Register (./_register, the organisation's
   obligation inventory, each row opening its dossier) and their period assurance, one tab each. The former Assurance
   Periods address (/assurance) opens its tab. */
const PANELS = { current: ObligationsRegister, "assurance-periods": AssurancePeriods };

export default function Obligations() {
  return <Suspense fallback={null}><WorkspaceHub href="/obligations" panels={PANELS} /></Suspense>;
}
