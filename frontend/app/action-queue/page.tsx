"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformActionQueue } from "@/components/PlatformActionQueue";
import { Notice, PageHeader } from "@/components/ui";

/* Action Queue: the Zoiko Super Admin's platform conditions (Platform Control Plane). Only the platform Super Admin is
   served its reads (GET /platform/organisations), so nobody else's view makes a request the server would refuse (and
   audit): customer-organisation users and content staff see this notice instead. */
export default function ActionQueuePage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="Action Queue" />
        <Notice>The Action Queue is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformActionQueue />;
}
