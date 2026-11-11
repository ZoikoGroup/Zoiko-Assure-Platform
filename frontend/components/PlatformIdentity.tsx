"use client";

import Link from "next/link";
import { useEffect } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowRight, Building2, CircleCheck, CircleOff, FileKey, KeyRound, LockKeyhole, MonitorSmartphone, Network, Smartphone, TicketCheck, UserCog, Users,
} from "lucide-react";
import { Alert } from "./ds";
import { RegistryHeading, RegistryScope, RegistrySection } from "./PlatformAdmin";
import { usePageTitle } from "./ui";

/* Identity Integrations for the Zoiko Super Admin (persona "platform") on /admin/identity; organisation users and content
   staff keep their own view of that route (app/admin/identity/page.tsx).

   Informational only: no request is made and nothing here is configured or measured, so the page shows no counts,
   statuses of its own, timestamps or health. Each statement describes what the backend implements today:
     email and password sign-in, scrypt-hashed with a per-credential salt ............ backend/app/auth.py
     temporary lockout after repeated failed attempts .................................. auth.py (MAX_FAILURES, LOCK_MINUTES)
     server-side sessions, checked on every request, ended by sign-out or revocation ... auth.py, routers/iam.py
     a one-time password for an appointed Organisation Admin, replaced at first sign-in  routers/platform.py, security.py
   No OIDC, SAML, SCIM or MFA exists in the backend; they wait on platform identity and security architecture
   decisions. Evidence connectors (for example the Microsoft Entra ID connector, source class IDENTITY) collect evidence
   and sign nobody in. */

type Capability = { title: string; detail: string; icon: LucideIcon };

const IN_PLACE: Capability[] = [
  { title: "Email and password", icon: KeyRound,
    detail: "People sign in with their email address and a password. Passwords are stored only as salted hashes, never in a form that can be read back. Agents and services cannot sign in this way." },
  { title: "Temporary lockout", icon: LockKeyhole,
    detail: "Repeated failed sign-in attempts lock the account for a short time. A failed attempt gives the same answer whether or not the account exists." },
  { title: "Revocable server-side sessions", icon: MonitorSmartphone,
    detail: "Each sign-in starts a time-limited session held on the server. Signing out, or ending a session, stops it at its next request." },
  { title: "One-time passwords for Organisation Admins", icon: TicketCheck,
    detail: "Creating an organisation or appointing an Organisation Admin issues a temporary password, shown once. The admin must replace it before using anything else." },
];

const NOT_ENABLED: Capability[] = [
  { title: "Single sign-on (OIDC)", icon: Network, detail: "Signing in through an external identity provider using OpenID Connect." },
  { title: "SAML-based authentication", icon: FileKey, detail: "Signing in through an identity provider that uses SAML." },
  { title: "SCIM user provisioning", icon: UserCog, detail: "Creating, updating and removing accounts automatically from an identity provider." },
  { title: "Multi-factor authentication (MFA)", icon: Smartphone, detail: "A second verification step at sign-in, in addition to the password." },
];

const RELATED: { href: string; title: string; detail: string; icon: LucideIcon }[] = [
  { href: "/admin/users", title: "Users & Roles", icon: Users,
    detail: "Your own access and sessions, the Zoiko staff accounts and every organisation's Organisation Admins." },
  { href: "/organisations", title: "Tenants & Entities", icon: Building2,
    detail: "Tenant management: create organisations, change their status and appoint Organisation Admins." },
];

export function PlatformIdentity() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle("Identity Integrations"); return () => setTitle(""); }, [setTitle]);
  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title="Identity Integrations">
        <p>How people sign in to Zoiko Assure today, and the identity capabilities that are not enabled yet. Nothing on this page is configured
          or measured: it describes the platform as it is built.</p>
      </RegistryHeading>
      <RegistryScope>
        <span className="font-semibold text-fg">One sign-in method for everyone who signs in.</span> It applies to Zoiko staff and to the people of
        every customer organisation alike; no organisation has an identity integration of its own.
      </RegistryScope>

      <RegistrySection title="Current sign-in method" aside={<Chip tone="active" />}
        description="What protects sign-in on the platform now.">
        <ul aria-label="Current sign-in method" className="grid gap-3 md:grid-cols-2">
          {IN_PLACE.map((c) => <li key={c.title} className="min-w-0"><CapabilityCard c={c} /></li>)}
        </ul>
      </RegistrySection>

      <RegistrySection title="Not yet available" description="Identity-provider capabilities that the platform does not offer yet.">
        <ul aria-label="Not yet available" className="grid gap-3 md:grid-cols-2">
          {NOT_ENABLED.map((c) => <li key={c.title} className="min-w-0"><CapabilityCard c={c} status={<Chip tone="off" />} /></li>)}
        </ul>
        <p className="mt-4 max-w-3xl type-caption text-fg-muted">
          These capabilities are pending platform-level identity and security architecture decisions. Until those are made there is nothing to
          configure or connect here.
        </p>
      </RegistrySection>

      <Alert tone="info" title="Evidence integrations are not sign-in integrations">
        Evidence integrations, such as Microsoft Entra ID used for evidence collection, read workforce and authentication-method records from an
        organisation&apos;s systems so that its controls can be assessed. They do not sign anyone in to Zoiko Assure and are not identity-provider
        integrations.
      </Alert>

      <RegistrySection title="Related administration">
        <ul aria-label="Related administration" className="grid gap-3 md:grid-cols-2">
          {RELATED.map((r) => (
            <li key={r.href} className="min-w-0">
              <Link href={r.href}
                className="group flex h-full items-start gap-3 rounded-card border border-line bg-surface-card p-4 transition-colors hover:border-line-strong">
                <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control border border-line bg-fg/5 text-fg-secondary">
                  <r.icon size={17} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 type-secondary font-semibold text-accent-fg">
                    {r.title}<ArrowRight size={14} aria-hidden className="shrink-0 transition-transform group-hover:translate-x-0.5" />
                  </span>
                  <span className="mt-0.5 block type-caption text-fg-secondary">{r.detail}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </RegistrySection>
    </div>
  );
}

function CapabilityCard({ c, status }: { c: Capability; status?: ReactNode }) {
  return (
    <article aria-label={c.title} className="flex h-full gap-3 rounded-card border border-line bg-surface-card p-4">
      <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control border border-line bg-fg/5 text-fg-secondary">
        <c.icon size={17} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
          <h3 className="break-words type-secondary font-semibold text-fg">{c.title}</h3>
          {status}
        </div>
        <p className="mt-1 type-caption text-fg-secondary">{c.detail}</p>
      </div>
    </article>
  );
}

/** A plain label for what the platform offers. It is a fact about the build, not a measured or live status. */
function Chip({ tone }: { tone: "active" | "off" }) {
  return tone === "active" ? (
    <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-chip border border-status-verified-line bg-status-verified-tint px-2 py-0.5 type-caption font-semibold text-status-verified">
      <CircleCheck size={12} strokeWidth={2.5} aria-hidden />In use
    </span>
  ) : (
    <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-chip border border-line-strong bg-fg/5 px-2 py-0.5 type-caption font-semibold text-fg-secondary">
      <CircleOff size={12} strokeWidth={2.5} aria-hidden />Not enabled
    </span>
  );
}
