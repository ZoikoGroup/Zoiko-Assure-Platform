"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformTenantMetadata } from "@/components/PlatformTenantMetadata";
import { Notice, PageHeader } from "@/components/ui";

/* Tenant Metadata: a read-only, registry-wide summary of organisation placement, residency and lifecycle for the Zoiko
   Super Admin (Platform Control Plane). Only the platform Super Admin is served GET /platform/organisations, so
   customer-organisation users and content staff see this notice instead and make no request: the server would refuse
   it and record the refusal in their own audit trail. */
export default function TenantMetadataPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Tenant Metadata" />
        <Notice>Tenant Metadata is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformTenantMetadata />;
}
