"use client";

import { personaOf, useAuth } from "@/lib/actor";
import { LegacyRedirect } from "@/components/WorkspaceHub";
import { ContentReleases } from "@/components/regulatory-library/ContentReleases";

/* Zoiko content staff keep Content Releases here, as their sidebar names it. For organisation users it is a tab of
   the Regulatory Library (components/nav.ts), which this address opens. */
export default function ContentReleasesPage() {
  const { me } = useAuth();
  return personaOf(me) === "tenant" ? <LegacyRedirect screen="/admin/releases" /> : <ContentReleases />;
}
