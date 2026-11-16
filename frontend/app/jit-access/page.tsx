"use client";

import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformJitAccess } from "@/components/jit-access/JitAccess";
import { Notice, PageHeader } from "@/components/ui";

/* JIT Access: the Zoiko Super Admin's demonstration of temporary, approved privileged access for platform staff
   (Platform Control Plane). It runs on demonstration data only and makes no request. It belongs to the platform
   control plane, so customer-organisation users and content staff see this notice instead and none of the screen is
   rendered for them. */
export default function JitAccessPage() {
  const { me } = useAuth();
  if (!me) return null;
  if (personaOf(me) !== "platform") {
    return (
      <>
        <PageHeader title="JIT Access" />
        <Notice>JIT Access is part of the Zoiko platform control plane and is available to the Zoiko Super Admin only. <Link href="/" className="font-semibold underline">Back to your dashboard</Link>.</Notice>
      </>
    );
  }
  return <PlatformJitAccess />;
}
