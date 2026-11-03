"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import { FileUp, History, PackageCheck, PenLine, ScanSearch, ServerCog, ShieldAlert, Tags, UserCheck, MessageSquareText } from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { EmptyState, TabPanel, Tabs } from "@/components/ds";
import { usePageTitle } from "@/components/ui";
import { ApprovalFlow } from "./_flows/ApprovalFlow";
import { AttestationFlow } from "./_flows/AttestationFlow";
import { DeterminationFlow } from "./_flows/DeterminationFlow";
import { EvidenceFlow } from "./_flows/EvidenceFlow";
import { IssueFlow } from "./_flows/IssueFlow";
import { PackageFlow } from "./_flows/PackageFlow";

/* Design Governance › High-Assurance Flows (Unified Design System, Task 3). Interactive prototypes, built from
   @/components/ds, of the workflows where a decision must be traceable. Each mirrors the real screen's fields, states
   and rules (cited in its file) but runs on sample data in the browser and sends nothing to the server. */
const TITLE = "High-Assurance Flows";

type Flow = { id: string; label: string; icon: LucideIcon; keywords: string; Body: ComponentType<{ go: (id: string) => void }> };
const FLOWS: Flow[] = [
  { id: "evidence", label: "Evidence submission", icon: FileUp, keywords: "evidence upload file classification metadata validation progress provenance hash", Body: () => <EvidenceFlow /> },
  { id: "attestation", label: "Attestation", icon: PenLine, keywords: "attestation attest adopt reject sign authority basis reason attested declined", Body: () => <AttestationFlow /> },
  { id: "determination", label: "Determination review", icon: ScanSearch, keywords: "determination review challenge conflict uncertain history comments", Body: ({ go }) => <DeterminationFlow onAttest={() => go("attestation")} /> },
  { id: "approval", label: "Approval", icon: UserCheck, keywords: "approval approve reject revoke exception requester approver separation of duties", Body: () => <ApprovalFlow /> },
  { id: "issues", label: "Exceptions & issues", icon: ShieldAlert, keywords: "exception issue severity status owner due transition cancel close resolution", Body: () => <IssueFlow /> },
  { id: "package", label: "Assurance package", icon: PackageCheck, keywords: "assurance package period snapshot completeness warnings generate sign verify", Body: () => <PackageFlow /> },
];

const PRINCIPLES: [LucideIcon, string, string][] = [
  [ServerCog, "The server decides", "Permissions, allowed states and separation of duties are enforced by the server. The interface asks, explains and confirms; it never grants."],
  [MessageSquareText, "Reasons where rules require them", "Rejecting an attestation and cancelling an issue need a reason. Forms say so before you submit, not after a refusal."],
  [History, "Traceable outcomes", "Every result names who acted, under what authority, on which evidence, and keeps an append-only history."],
  [Tags, "Controlled terms", "States appear exactly as recorded (ATTESTED, IN_REVIEW, SIGNED), in the canonical colours, and never as “compliant”."],
];

export default function HighAssuranceFlows() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="flows" searchLabel="Search flows"><Flows /></GovernanceShell>;
}

function Flows() {
  const { query } = useGovernance();
  const [active, setActive] = useState("evidence");
  const q = query.trim().toLowerCase();
  const shown = FLOWS.filter((f) => !q || `${f.label} ${f.keywords}`.toLowerCase().includes(q));
  const current = shown.find((f) => f.id === active) ?? shown[0];

  // The selected flow lives in the URL hash, so each flow can be linked to directly (…/flows#attestation).
  useEffect(() => {
    const read = () => { const h = window.location.hash.slice(1); if (FLOWS.some((f) => f.id === h)) setActive(h); };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const go = (id: string) => { setActive(id); window.history.replaceState(null, "", `#${id}`); document.getElementById("flow-tabs")?.scrollIntoView({ block: "start" }); };

  return (
    <div className="space-y-section">
      <section aria-labelledby="flows-title">
        <h1 id="flows-title" className="type-page text-fg sm:type-display">High-Assurance <span className="text-accent-fg">Flows</span></h1>
        <p className="mt-3 max-w-3xl type-body text-fg-secondary">
          The components applied to decisions that must stand up to audit. Each flow mirrors the application screen it is named after, with its
          fields, states and rules, as an interactive prototype on sample data.
        </p>
      </section>

      <ul className="grid gap-stack sm:grid-cols-2 xl:grid-cols-4">
        {PRINCIPLES.map(([Icon, title, text]) => (
          <li key={title} className="rounded-card border border-line bg-surface-raised/70 p-card">
            <Icon size={18} aria-hidden className="text-accent" />
            <p className="mt-2 type-secondary font-semibold text-fg">{title}</p>
            <p className="mt-1 type-caption text-fg-secondary">{text}</p>
          </li>
        ))}
      </ul>

      <section id="flow-tabs" aria-label="Flows" className="scroll-mt-6 rounded-panel border border-line bg-surface-raised/70 p-panel shadow-panel">
        {shown.length === 0 || !current ? <EmptyState title="No flows match the search" description="Try evidence, attestation, approval or package." /> : (
          <>
            <Tabs id="flows" label="High-assurance flows" value={current.id} onChange={go}
              items={shown.map((f) => ({ value: f.id, label: f.label, icon: f.icon }))} />
            <TabPanel id="flows" value={current.id} className="pt-6"><current.Body go={go} /></TabPanel>
          </>
        )}
      </section>
      <p className="type-caption text-fg-muted">Names, records and figures in these flows are illustrative samples. Your name, roles and authority basis come from your session.</p>
    </div>
  );
}
