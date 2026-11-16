"use client";

import { personaOf, useAuth } from "@/lib/actor";
import { LegacyRedirect } from "@/components/WorkspaceHub";
import { RegulatoryInbox } from "@/components/regulatory-library/RegulatoryInbox";

/* Zoiko content staff keep the Regulatory Inbox here, as their sidebar names it. For organisation users it is the
   Regulatory Sources tab of the Regulatory Library (components/nav.ts), which this address opens. */
export default function RegulatoryInboxPage() {
  const { me } = useAuth();
  return personaOf(me) === "tenant" ? <LegacyRedirect screen="/regulatory" /> : <RegulatoryInbox />;
}
