"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { SuperAdminDashboard } from "@/components/PlatformAdmin";
import { Notice, PageHeader } from "@/components/ui";

/* Tenants & Entities: the Super Admin's organisation registry (WF-TEN-01 · ZA-TEN-001), moved here from "/" when "/"
   became the Platform Command Center. Same component, same reads and actions. Only the platform Super Admin is served
   GET /platform/organisations, so nobody else's view makes a request the server would refuse (and audit). */
export default function Organisations() {
  const { me } = useAuth();
  if (me && personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Tenants & Entities" />
        <Notice>Tenants &amp; Entities is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <SuperAdminDashboard title="Tenants & Entities" />;
}
