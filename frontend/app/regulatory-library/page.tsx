"use client";

import { Suspense } from "react";
import { WorkspaceHub } from "@/components/WorkspaceHub";
import { ContentReleases } from "@/components/regulatory-library/ContentReleases";
import { RegulatoryInbox } from "@/components/regulatory-library/RegulatoryInbox";
import { SourcesAndProvisions } from "@/components/regulatory-library/SourcesAndProvisions";

/* Regulatory Library (organisation sidebar, components/nav.ts): the regulatory sources, requirements and provisions, and
   signed content releases Zoiko publishes, one tab each. Read-only for organisation users as before: the authoring
   controls inside stay disabled without content-staff permissions, and the backend refuses them. Content staff keep
   these screens at /regulatory, /sources and /admin/releases. */
const PANELS = { sources: RegulatoryInbox, requirements: SourcesAndProvisions, releases: ContentReleases };

export default function RegulatoryLibrary() {
  return <Suspense fallback={null}><WorkspaceHub href="/regulatory-library" panels={PANELS} /></Suspense>;
}
