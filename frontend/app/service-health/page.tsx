"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformServiceHealth } from "@/components/PlatformServiceHealth";
import { Notice, PageHeader } from "@/components/ui";

/* Service Health: the Zoiko Super Admin's view of this deployment's API, PostgreSQL and Redis (Platform Control Plane),
   from the public health check GET /api/health. The check itself needs no sign-in, but this screen belongs to the
   platform control plane, so customer-organisation users and content staff see this notice instead and make no request. */
export default function ServiceHealthPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Service Health" />
        <Notice>Service Health is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformServiceHealth />;
}
