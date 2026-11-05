"use client";

import { useCallback } from "react";
import { personaOf, useAuth, type Me } from "./actor";

/* Plain-English names for the backend role catalogue (backend/app/security.py ROLE_PERMISSIONS) and the permissions
   they grant. Display only: the backend decides what every role may do. Codes stay visible beside the names so
   people can match them to policies and audit records. */
const ROLES: Record<string, { label: string; about: string }> = {
  CCO: { label: "Chief Compliance Officer", about: "Adopts applicability determinations, requests and approves exceptions, works issues, resolves evidence conflicts, manages facts and builds audit packages." },
  LEGAL_REVIEWER: { label: "Legal Reviewer", about: "Attests and challenges legal determinations and records legal dispositions." },
  REGULATORY_ANALYST: { label: "Regulatory Analyst", about: "Maintains facts, runs applicability evaluations and raises challenges." },
  CONTROL_OWNER: { label: "Control Owner", about: "Implements controls, submits evidence and test results, works issues and requests exceptions." },
  INTERNAL_AUDIT: { label: "Internal Audit", about: "Reads everything, builds audit packages, verifies evidence and the ledger, and re-runs IPE checks." },
  AI_GOVERNANCE_LEAD: { label: "AI Governance Lead", about: "Registers AI assets and grants or revokes agent authority." },
  PLATFORM_ADMIN: { label: "Organisation Platform Admin", about: "Configures this organisation's connector installations and ledger checkpoints. Cannot change connector certification, and is not the Zoiko Super Admin." },
  TENANT_OWNER: { label: "Organisation Admin", about: "Administers this organisation (for example its legal entities) and appoints its security administrators. Cannot attest, approve or change assurance states on its behalf." },
  TENANT_SECURITY_ADMIN: { label: "Security Administrator", about: "Assigns and revokes this organisation's roles, at organisation or legal-entity scope, and reviews effective access. Cannot read or change regulatory records, or assign a role to itself." },
  RISK_APPROVER: { label: "Risk Approver", about: "Approves exceptions, risk acceptances and compensating controls, and reviews evidence conflicts and IPE anomalies." },
  EXTERNAL_AUDITOR: { label: "External Auditor", about: "Read-only access to the audit allowlist (evidence, packages, control and obligation dossiers, reports), with verification of evidence and packages." },
  CONTENT_ANALYST: { label: "Content Analyst (Zoiko)", about: "Ingests regulatory sources and drafts requirements and rules." },
  ENCODING_ENGINEER: { label: "Encoding Engineer (Zoiko)", about: "Encodes rules and peer-reviews other authors' drafts." },
  CONTENT_LEGAL_REVIEWER: { label: "Content Legal Reviewer (Zoiko)", about: "Legally verifies encoded requirements before release." },
  CONTENT_RELEASE_MANAGER: { label: "Content Release Manager (Zoiko)", about: "Releases verified content to customer organisations." },
  SUPER_ADMIN: { label: "Super Admin (Zoiko platform)", about: "Creates and administers customer organisations in the registry. Has no access to any organisation's regulatory data." },
  CONNECTOR_CERTIFICATION_MANAGER: { label: "Connector Certification Manager (Zoiko)", about: "Records connector certification and runtime health in the registry shared by every organisation." },
  SERVICE: { label: "Service account", about: "Automated integration acting under granted authority." },
};

const PERMISSIONS: Record<string, string> = {
  read: "View this organisation's regulatory records",
  audit_read: "View the audit allowlist: evidence, packages, control and obligation dossiers, reports",
  manage_role_assignments: "Assign and revoke this organisation's roles",
  delegate_admin: "Appoint this organisation's security administrators",
  attest: "Sign attestations and legal holds",
  approve_exception: "Approve exceptions and risk acceptances",
  approve_compensation: "Approve compensating controls",
  request_evaluation: "Run applicability evaluations",
  manage_facts: "Assert facts and register subjects",
  challenge: "Challenge determinations",
  legal_disposition: "Record legal dispositions",
  package: "Build audit packages",
  verify: "Verify evidence, packages and the ledger",
  submit_evidence: "Submit evidence and test results",
  manage_controls: "Implement and maintain controls",
  manage_issues: "Move issues through remediation",
  request_exception: "Request exceptions and propose compensating controls",
  assurance_review: "Resolve evidence conflicts and disposition IPE anomalies",
  reevaluate_ipe: "Re-run IPE checks under a source run's own policy",
  request_policy_decision: "Request agent policy decisions",
  manage_ai: "Register AI assets and versions",
  grant_authority: "Grant and revoke agent authority",
  manage_connectors: "Configure this organisation's connector installations",
  certify_connectors: "Certify connectors in the global registry (Zoiko platform)",
  manage_tenant: "Administer the organisation",
  checkpoint: "Checkpoint the evidence ledger",
  content_author: "Author regulatory content and rule drafts",
  content_peer_review: "Peer-review rule drafts",
  content_legal_review: "Legally verify regulatory content",
  content_release: "Release content to organisations",
  manage_organisations: "Create and administer customer organisations (registry only)",
};

/** Every role code the backend catalogue defines, in display order (for filters). */
export const ROLE_CODES = Object.keys(ROLES);
export const roleLabel = (code: string) => ROLES[code]?.label ?? code;
export const roleAbout = (code: string) => ROLES[code]?.about;
export const permissionText = (code: string) => PERMISSIONS[code] ?? code;
export const rolesText = (me: Pick<Me, "roles"> | null) => (me?.roles ?? []).map(roleLabel).join(", ");

/** Where the signed-in person works. A customer organisation, or Zoiko's own staff, who belong to no organisation. */
export function workContext(me: Me | null): { kind: "organisation" | "platform" | "content"; label: string; name: string; id?: string } {
  const persona = personaOf(me);
  if (persona === "tenant") return { kind: "organisation", label: "Organisation", name: me?.tenant_name ?? "", id: me?.tenant_id ?? undefined };
  if (persona === "platform") return { kind: "platform", label: "Zoiko platform", name: "Platform administration" };
  return { kind: "content", label: "Zoiko staff", name: "Content operations" };
}

/** Whether the signed-in person's roles grant a permission, and why an action is unavailable when they don't.
 *  This only shapes the UI; the backend refuses the call either way. */
export function useCan() {
  const { me } = useAuth();
  const can = useCallback((perm: string) => !!me?.permissions?.includes(perm), [me]);
  const why = useCallback((perm: string) => (me?.permissions?.includes(perm) ? undefined
    : `Not available to your role (${rolesText(me) || "none"}): needs “${permissionText(perm)}”`), [me]);
  return { can, why };
}
