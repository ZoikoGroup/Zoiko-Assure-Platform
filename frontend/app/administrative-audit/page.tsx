"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformAdministrativeAudit } from "@/components/administrative-audit/AdministrativeAudit";
import { Notice, PageHeader } from "@/components/ui";

/* Administrative Audit: the Zoiko Super Admin's demonstration of exploring platform-level and organisation-level
   administrative events (Platform Control Plane). It runs on demonstration data only and makes no request. It belongs
   to the platform control plane, so customer-organisation users and content staff see this notice instead and none of
   the screen is rendered for them. */
export default function AdministrativeAuditPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Administrative Audit" />
        <Notice>Administrative Audit is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformAdministrativeAudit />;
}
