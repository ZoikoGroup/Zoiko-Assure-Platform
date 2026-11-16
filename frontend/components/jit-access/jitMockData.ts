import type { JitRequest, JitRole, JitSession, Organisation, Requester } from "./types";

/* DEMONSTRATION DATA ONLY. Replace this file's use in useJitAccessData.ts when a JIT backend model is approved.

   Every record is fictional: no person, organisation, request, approval or session here exists, and nothing was read
   from this deployment. Staff e-mail addresses use the reserved .example domain (RFC 2606); organisations use
   well-known fictitious company names; identifiers carry DEMO. Role names, durations and the approvers shown are
   ILLUSTRATIVE: who may approve, how long access may last and which rules apply are pending product decisions
   (see PENDING_DECISIONS in model.ts). */

/** The fixed demonstration clock. Remaining time, "expiring soon" and expiry are computed against this instant, not
 *  the real clock, so the summary figures always agree with the records. */
export const DEMO_NOW = "2026-11-12T10:00:00Z";

export const DEMO_ROLES: JitRole[] = [
  { id: "demo-registry-reader", label: "Registry Reader", description: "Read one organisation's registry record and administrative history." },
  { id: "demo-lifecycle-operator", label: "Lifecycle Operator", description: "Change one organisation's status, for example suspend or reactivate it." },
  { id: "demo-admin-appointer", label: "Admin Appointer", description: "Appoint a replacement Organisation Admin for one organisation." },
  { id: "demo-support-investigator", label: "Support Investigator", description: "Read platform diagnostics and service records while investigating an incident." },
];

const staff = (name: string, team: string, local: string): Requester => ({ name, team, email: `${local}@staff.example` });
const AMARA = staff("Amara Okoye", "Platform Support (demo)", "a.okoye");
const DANIEL = staff("Daniel Reyes", "Platform Support (demo)", "d.reyes");
const PRIYA = staff("Priya Natarajan", "Platform Operations (demo)", "p.natarajan");
const TOMASZ = staff("Tomasz Nowak", "Platform Support (demo)", "t.nowak");
const HANNAH = staff("Hannah Becker", "Security Operations (demo)", "h.becker");
const KWAME = staff("Kwame Mensah", "Platform Operations (demo)", "k.mensah");
const SOFIA = staff("Sofia Lindqvist", "Platform Support (demo)", "s.lindqvist");
const LIAM = staff("Liam O'Connor", "Security Operations (demo)", "l.oconnor");

const org = (id: string, name: string): Organisation => ({ id, name });
const NORTHWIND = org("ORG-DEMO-01", "Northwind Traders Ltd");
const CONTOSO = org("ORG-DEMO-02", "Contoso Financial plc");
const FABRIKAM = org("ORG-DEMO-03", "Fabrikam Insurance SE");
const TAILSPIN = org("ORG-DEMO-04", "Tailspin Lending Inc");
const WOODGROVE = org("ORG-DEMO-05", "Woodgrove Bank");
const LITWARE = org("ORG-DEMO-06", "Litware Payments Ltd");
const PROSEWARE = org("ORG-DEMO-07", "Proseware Capital LLP");
const ADVENTURE = org("ORG-DEMO-08", "Adventure Works Credit Union");
const PLATFORM_WIDE: Organisation = null;

const SECURITY_LEAD = "Platform security lead (demo)";
const DUTY_MANAGER = "Duty platform manager (demo)";

const req = (r: Omit<JitRequest, "requestedStart"> & { requestedStart?: string }): JitRequest => ({ ...r, requestedStart: r.requestedStart ?? r.requestedAt });

export const DEMO_REQUESTS: JitRequest[] = [
  req({ id: "JIT-DEMO-0101", requester: DANIEL, organisation: TAILSPIN, roleId: "demo-support-investigator", requestedHours: 8, requestedAt: "2026-11-06T09:15:00Z",
    justification: "Investigate slow registry responses reported by the customer.",
    status: "REJECTED", decision: { by: SECURITY_LEAD, at: "2026-11-06T10:02:00Z", reason: "The justification names no ticket or incident. Resubmit with the incident reference." } }),
  req({ id: "JIT-DEMO-0102", requester: AMARA, organisation: WOODGROVE, roleId: "demo-admin-appointer", requestedHours: 2, requestedAt: "2026-11-07T14:50:00Z",
    justification: "Appoint a new Organisation Admin; the current one is on extended leave.",
    status: "REJECTED", decision: { by: SECURITY_LEAD, at: "2026-11-07T15:30:00Z", reason: "No customer request is on record. The customer must ask through support first." } }),
  req({ id: "JIT-DEMO-0103", requester: HANNAH, organisation: PLATFORM_WIDE, roleId: "demo-support-investigator", requestedHours: 4, requestedAt: "2026-11-08T08:40:00Z",
    justification: "Incident INC-DEMO-219: elevated sign-in failures across several organisations. I need the service records to find the cause.",
    status: "APPROVED", decision: { by: SECURITY_LEAD, at: "2026-11-08T09:00:00Z", approvedHours: 4 } }),
  req({ id: "JIT-DEMO-0104", requester: SOFIA, organisation: FABRIKAM, roleId: "demo-registry-reader", requestedHours: 2, requestedAt: "2026-11-09T07:45:00Z",
    justification: "Ticket ZS-DEMO-4402: the customer asks why its legal entity shows the wrong country.",
    status: "APPROVED", decision: { by: DUTY_MANAGER, at: "2026-11-09T08:00:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0105", requester: KWAME, organisation: CONTOSO, roleId: "demo-lifecycle-operator", requestedHours: 4, requestedAt: "2026-11-09T12:30:00Z",
    justification: "Ticket ZS-DEMO-4415: reactivate the organisation after its contract renewal was signed.",
    status: "APPROVED", decision: { by: SECURITY_LEAD, at: "2026-11-09T13:00:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0106", requester: LIAM, organisation: PLATFORM_WIDE, roleId: "demo-lifecycle-operator", requestedHours: 8, requestedAt: "2026-11-10T07:20:00Z",
    justification: "Routine maintenance window.",
    status: "REJECTED", decision: { by: SECURITY_LEAD, at: "2026-11-10T08:00:00Z", reason: "Scope too broad: request a single organisation and a concrete task." } }),
  req({ id: "JIT-DEMO-0107", requester: PRIYA, organisation: NORTHWIND, roleId: "demo-registry-reader", requestedHours: 2, requestedAt: "2026-11-10T09:40:00Z",
    justification: "Ticket ZS-DEMO-4431: confirm the organisation's lifecycle history before the customer's audit.",
    status: "APPROVED", decision: { by: DUTY_MANAGER, at: "2026-11-10T10:00:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0108", requester: TOMASZ, organisation: LITWARE, roleId: "demo-lifecycle-operator", requestedHours: 4, requestedAt: "2026-11-11T08:35:00Z",
    justification: "Ticket ZS-DEMO-4450: suspend the organisation at the customer's written request during a security review.",
    status: "APPROVED", decision: { by: SECURITY_LEAD, at: "2026-11-11T09:00:00Z", approvedHours: 4 } }),
  req({ id: "JIT-DEMO-0109", requester: DANIEL, organisation: ADVENTURE, roleId: "demo-admin-appointer", requestedHours: 1, requestedAt: "2026-11-11T13:50:00Z",
    justification: "Ticket ZS-DEMO-4458: their only Organisation Admin has left; the customer asked for a replacement.",
    status: "APPROVED", decision: { by: DUTY_MANAGER, at: "2026-11-11T14:05:00Z", approvedHours: 1 } }),
  req({ id: "JIT-DEMO-0110", requester: HANNAH, organisation: LITWARE, roleId: "demo-registry-reader", requestedHours: 2, requestedAt: "2026-11-11T16:20:00Z",
    justification: "Follow-up to ticket ZS-DEMO-4450: confirm the suspension was recorded correctly.",
    status: "PENDING", decision: null }),
  req({ id: "JIT-DEMO-0111", requester: KWAME, organisation: FABRIKAM, roleId: "demo-lifecycle-operator", requestedHours: 8, requestedAt: "2026-11-11T17:05:00Z",
    requestedStart: "2026-11-12T08:00:00Z",
    justification: "Ticket ZS-DEMO-4462: planned suspension during the customer's contract renewal, starting with their maintenance window.",
    status: "PENDING", decision: null }),
  req({ id: "JIT-DEMO-0112", requester: LIAM, organisation: PLATFORM_WIDE, roleId: "demo-support-investigator", requestedHours: 8, requestedAt: "2026-11-12T07:10:00Z",
    justification: "Incident INC-DEMO-231: intermittent registry timeouts reported by three customers.",
    status: "APPROVED", decision: { by: SECURITY_LEAD, at: "2026-11-12T07:30:00Z", approvedHours: 4 } }),
  req({ id: "JIT-DEMO-0113", requester: SOFIA, organisation: TAILSPIN, roleId: "demo-lifecycle-operator", requestedHours: 4, requestedAt: "2026-11-12T07:55:00Z",
    justification: "Ticket ZS-DEMO-4470: reactivate the organisation now that the overdue invoice is settled.",
    status: "APPROVED", decision: { by: DUTY_MANAGER, at: "2026-11-12T08:15:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0114", requester: PRIYA, organisation: PLATFORM_WIDE, roleId: "demo-support-investigator", requestedHours: 4, requestedAt: "2026-11-12T08:30:00Z",
    justification: "Incident INC-DEMO-231: compare service records with the timeouts Liam O'Connor is investigating.",
    status: "PENDING", decision: null }),
  req({ id: "JIT-DEMO-0115", requester: TOMASZ, organisation: WOODGROVE, roleId: "demo-registry-reader", requestedHours: 2, requestedAt: "2026-11-12T08:40:00Z",
    justification: "Ticket ZS-DEMO-4473: the customer cannot see one of its legal entities.",
    status: "APPROVED", decision: { by: SECURITY_LEAD, at: "2026-11-12T08:50:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0116", requester: DANIEL, organisation: CONTOSO, roleId: "demo-admin-appointer", requestedHours: 1, requestedAt: "2026-11-12T09:12:00Z",
    justification: "Ticket ZS-DEMO-4476: their Organisation Admin is locked out and the customer asked for a second admin.",
    status: "PENDING", decision: null }),
  req({ id: "JIT-DEMO-0117", requester: AMARA, organisation: PROSEWARE, roleId: "demo-admin-appointer", requestedHours: 2, requestedAt: "2026-11-12T09:30:00Z",
    justification: "Ticket ZS-DEMO-4478: appoint the Organisation Admin named in the customer's onboarding form.",
    status: "APPROVED", decision: { by: DUTY_MANAGER, at: "2026-11-12T09:45:00Z", approvedHours: 2 } }),
  req({ id: "JIT-DEMO-0118", requester: AMARA, organisation: NORTHWIND, roleId: "demo-registry-reader", requestedHours: 2, requestedAt: "2026-11-12T09:41:00Z",
    justification: "Ticket ZS-DEMO-4471: the customer reports that reactivation failed on 11 Nov. I need the organisation's status history to find the cause.",
    status: "PENDING", decision: null }),
];

/* One session per approved request. Its expiry is the approval time plus the approved hours, plus any extension. */
export const DEMO_SESSIONS: JitSession[] = [
  { id: "SES-DEMO-0201", requestId: "JIT-DEMO-0103", startedAt: "2026-11-08T09:00:00Z", expiresAt: "2026-11-08T13:00:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0202", requestId: "JIT-DEMO-0104", startedAt: "2026-11-09T08:00:00Z", expiresAt: "2026-11-09T10:00:00Z", extensions: [],
    revoked: { by: "Sofia Lindqvist", at: "2026-11-09T08:25:00Z", reason: "Wrong organisation selected; access ended at the requester's request." } },
  { id: "SES-DEMO-0203", requestId: "JIT-DEMO-0105", startedAt: "2026-11-09T13:00:00Z", expiresAt: "2026-11-09T15:00:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0204", requestId: "JIT-DEMO-0107", startedAt: "2026-11-10T10:00:00Z", expiresAt: "2026-11-10T12:00:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0205", requestId: "JIT-DEMO-0108", startedAt: "2026-11-11T09:00:00Z", expiresAt: "2026-11-11T13:00:00Z", extensions: [],
    revoked: { by: SECURITY_LEAD, at: "2026-11-11T10:10:00Z", reason: "Task completed early; access no longer needed." } },
  { id: "SES-DEMO-0206", requestId: "JIT-DEMO-0109", startedAt: "2026-11-11T14:05:00Z", expiresAt: "2026-11-11T15:05:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0207", requestId: "JIT-DEMO-0112", startedAt: "2026-11-12T07:30:00Z", expiresAt: "2026-11-12T12:30:00Z",
    extensions: [{ hours: 1, by: SECURITY_LEAD, at: "2026-11-12T09:20:00Z" }], revoked: null },
  { id: "SES-DEMO-0208", requestId: "JIT-DEMO-0113", startedAt: "2026-11-12T08:15:00Z", expiresAt: "2026-11-12T10:15:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0209", requestId: "JIT-DEMO-0115", startedAt: "2026-11-12T08:50:00Z", expiresAt: "2026-11-12T10:50:00Z", extensions: [], revoked: null },
  { id: "SES-DEMO-0210", requestId: "JIT-DEMO-0117", startedAt: "2026-11-12T09:45:00Z", expiresAt: "2026-11-12T11:45:00Z", extensions: [], revoked: null },
];
