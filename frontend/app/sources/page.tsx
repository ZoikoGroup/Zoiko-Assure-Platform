"use client";

import { personaOf, useAuth } from "@/lib/actor";
import { LegacyRedirect } from "@/components/WorkspaceHub";
import { SourcesAndProvisions } from "@/components/regulatory-library/SourcesAndProvisions";

/* Zoiko content staff keep Sources & Provisions here, as their sidebar names it. For organisation users it is the
   Requirements & Provisions tab of the Regulatory Library (components/nav.ts), which this address opens. */
export default function SourcesPage() {
  const { me } = useAuth();
  return personaOf(me) === "tenant" ? <LegacyRedirect screen="/sources" /> : <SourcesAndProvisions />;
}
