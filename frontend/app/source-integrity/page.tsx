"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformSourceIntegrity } from "@/components/source-integrity/SourceIntegrity";
import { Notice, PageHeader } from "@/components/ui";

/* Source Integrity: the Zoiko Super Admin's view of regulatory source authenticity, verification status, version
   changes and integrity-check history (Platform Control Plane). The screen currently shows demonstration data only and
   makes no request. It belongs to the platform control plane, so customer-organisation users and content staff see
   this notice instead and none of the screen is rendered for them. */
export default function SourceIntegrityPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Source Integrity" />
        <Notice>Source Integrity is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformSourceIntegrity />;
}
