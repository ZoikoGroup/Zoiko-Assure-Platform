"use client";

import type { ReactNode } from "react";
import { ArrowRight, Braces, FlaskConical, Inbox, Library } from "lucide-react";
import Link from "next/link";
import { personaOf, useAuth } from "@/lib/actor";
import { PlatformCommandCenter } from "@/components/PlatformCommandCenter";
import { Notice, PageHeader } from "@/components/ui";
// The Organisation Overview lives in app/_overview, a private folder Next.js does not route.
import { OrgOverview } from "./_overview/OrgOverview";

export default function Dashboard() {
  const { me } = useAuth();
  const persona = personaOf(me);
  // Three home screens: the platform Super Admin's Platform Command Center (its organisation registry is Tenants &
  // Entities, /organisations), content staff's workspace, and the Organization Command Center.
  if (me && persona === "platform") return <PlatformCommandCenter />;
  if (me && persona !== "tenant") return <ContentOpsHome name={me.display_name} />;
  return <OrgOverview />;
}

function ContentOpsHome({ name }: { name: string }): ReactNode {
  return (
    <>
      <PageHeader kicker="Zoiko content operations" title={`Welcome, ${name.replace(/\s*\(.*\)/, "")}`}
        subtitle="Shared regulatory content and rules that customer organisations receive." />
      <Notice>Zoiko content-operations staff have no standing access to customer data (INV-14).</Notice>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {[{ href: "/regulatory", label: "Regulatory Inbox", hint: "Ingest sources and propose extractions", icon: Inbox },
          { href: "/sources", label: "Sources & Provisions", hint: "Encode and verify requirements", icon: Library },
          { href: "/rules", label: "Rule Authoring", hint: "Validate, review and release", icon: Braces },
          { href: "/rules/workbench", label: "Rule Test Workbench", hint: "Golden vectors and synthetic fixtures", icon: FlaskConical }].map(({ href, label, hint, icon: Icon }) => (
          <Link key={href} href={href} className="card flex items-center gap-3 p-4 transition-colors hover:border-navy-300">
            <Icon size={18} aria-hidden className="text-navy-600" />
            <span><span className="block text-[13px] font-medium text-slate-900">{label}</span><span className="block text-xs text-slate-500">{hint}</span></span>
            <ArrowRight size={14} aria-hidden className="ml-auto text-slate-400" />
          </Link>
        ))}
      </div>
    </>
  );
}
