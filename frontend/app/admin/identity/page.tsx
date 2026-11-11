"use client";

import { KeyRound } from "lucide-react";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformIdentity } from "@/components/PlatformIdentity";
import { Card, EmptyState, PageHeader, StatusPill } from "@/components/ui";

const CAPABILITIES = [
  { title: "Enterprise sign-in (OIDC)", spec: "IAM-REQ-014",
    detail: "Sign-in through your identity provider using Authorization Code + PKCE, with issuer, audience, state and nonce validation." },
  { title: "User lifecycle provisioning (SCIM)", spec: "ZA-IAM-001 §04",
    detail: "Joiners, movers and leavers synchronised from your identity provider, so offboarding removes access promptly." },
];

export default function IdentityIntegrations() {
  const { me } = useAuth();
  // The Zoiko Super Admin has its own, platform-level view (components/PlatformIdentity.tsx); everyone else keeps this one.
  if (me && personaOf(me) === "platform") return <PlatformIdentity />;
  return (
    <>
      <PageHeader spec="ZA-IAM-001" title="Identity Integrations"
        subtitle="Enterprise single sign-on and workforce lifecycle provisioning for your tenant." />
      <div className="space-y-4">
        <div className="card">
          <EmptyState icon={KeyRound} title="No identity integrations are enabled"
            description="Assure currently signs people in with an email address and password and issues a time-limited session. Identity-provider integration is not yet available, so there is nothing to configure here yet." />
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {CAPABILITIES.map((c) => (
            <Card key={c.title} title={c.title} subtitle={c.spec} actions={<StatusPill tone="neutral" label="NOT ENABLED" />}>
              <p className="text-[13px] text-slate-600">{c.detail}</p>
            </Card>
          ))}
        </div>
      </div>
    </>
  );
}
