import type { AuditEventRecord, DemoActor, DemoOrganisation } from "./types";

/* DEMONSTRATION DATA ONLY. Replace this file's use in useAdministrativeAuditData.ts when a backend read is approved.

   Every record is fictional: none was read from, or written to, any audit table, and no person, organisation or
   session here exists. The records use the audit model's real vocabulary so the screen looks like what the platform
   records: the action names, object types, reason formats and state codes written by backend/app/platform.py audit()
   and its callers (for example organisation.status_changed, platform.organisation_registry_read, authorization.denied
   with new_state MISSING_PERMISSION, auth.login_failed with new_state LOCKED on a LoginAttempt).

   - tenant_id is null for platform-level records (registry reads, platform staff sign-ins and denials, content and
     catalogue administration) and the organisation's ID otherwise. As in the real model, the Super Admin's changes TO
     an organisation (organisation.created, organisation.status_changed, ...) carry that organisation's tenant_id.
   - record_seq is cell-wide, so audit records have gaps: other record types take the numbers in between.
   - correlation_id: the current audit writer never sets it, so it is null on almost every record. Two groups carry an
     ILLUSTRATIVE value to show how one ID could link the records of one operation.
   - Identifiers carry "demo"; the one e-mail address is fictional, on the reserved .example domain. It is shown in full
     because the real writer records it in full (redaction is partial). */

export const DEMO_CELL = "cell-demo-eu1";

export const DEMO_ORGANISATIONS: DemoOrganisation[] = [
  { tenant_id: "ten-demo-01", name: "Northwind Traders Ltd" },
  { tenant_id: "ten-demo-02", name: "Contoso Financial plc" },
  { tenant_id: "ten-demo-03", name: "Fabrikam Insurance SE" },
  { tenant_id: "ten-demo-04", name: "Tailspin Lending Inc" },
  { tenant_id: "ten-demo-05", name: "Woodgrove Bank" },
];

export const DEMO_ACTORS: DemoActor[] = [
  { actor_id: "actor-demo-sa01", name: "Super Admin (demo)" },
  { actor_id: "actor-demo-ca02", name: "Content Analyst (demo)" },
  { actor_id: "actor-demo-cr03", name: "Content Release Manager (demo)" },
  { actor_id: "svc-demo-ingest", name: "Ingestion service (demo)" },
  { actor_id: "actor-demo-n11", name: "Northwind Org Admin (demo)" },
  { actor_id: "actor-demo-n12", name: "Northwind issue owner (demo)" },
  { actor_id: "actor-demo-c21", name: "Contoso Org Admin (demo)" },
  { actor_id: "actor-demo-c22", name: "Contoso compliance officer (demo)" },
  { actor_id: "agent-demo-c23", name: "Contoso evidence agent (demo)" },
  { actor_id: "actor-demo-f31", name: "Fabrikam Org Admin (demo)" },
  { actor_id: "actor-demo-t41", name: "Tailspin Org Admin (demo)" },
  { actor_id: "actor-demo-w51", name: "Woodgrove security admin (demo)" },
  { actor_id: "actor-demo-w52", name: "Woodgrove analyst (demo)" },
];

const SA = "actor-demo-sa01";
type Row = [seq: number, at: string, tenant: string | null, actor: string | null, actorType: string | null, action: string,
  objectType: string, objectId: string, prior: string | null, next: string | null, reason: string | null, correlation?: string];

const ROWS: Row[] = [
  [4101, "2026-11-03T08:02:11Z", null, SA, "HUMAN", "auth.login", "Actor", SA, null, null, null],
  [4103, "2026-11-03T08:05:40Z", null, SA, "HUMAN", "platform.organisation_registry_read", "OrganisationRegistry", DEMO_CELL, null, null, "4 organisation(s) listed"],
  [4108, "2026-11-03T08:14:02Z", "ten-demo-04", SA, "HUMAN", "organisation.created", "Tenant", "ten-demo-04", null, "ACTIVE", `Organisation Tailspin Lending Inc provisioned in ${DEMO_CELL}`, "corr-demo-1a2b"],
  [4109, "2026-11-03T08:14:02Z", "ten-demo-04", SA, "HUMAN", "organisation.admin_appointed", "Actor", "actor-demo-t41", null, "ORG_ADMIN", "Organisation Admin admin@tailspin.example", "corr-demo-1a2b"],
  [4110, "2026-11-03T08:14:03Z", "ten-demo-04", SA, "HUMAN", "entity.registered", "LegalEntity", "ent-demo-0401", null, "ACTIVE", "Legal entity Tailspin Lending Inc (US) registered", "corr-demo-1a2b"],
  [4122, "2026-11-03T09:30:00Z", null, "actor-demo-ca02", "HUMAN", "auth.login", "Actor", "actor-demo-ca02", null, null, null],
  [4125, "2026-11-03T09:41:18Z", null, "svc-demo-ingest", "SERVICE", "source.ingest", "RegulatorySource", "src-demo-0112", null, "INGESTED", "Ingested version 2026.3"],
  [4131, "2026-11-03T14:22:09Z", "ten-demo-02", "actor-demo-c21", "HUMAN", "role.assigned", "RoleAssignment", "ra-demo-2207", null, "COMPLIANCE_OFFICER@TENANT",
    "admin: COMPLIANCE_OFFICER for actor-demo-c22 at TENANT scope; New compliance lead"],
  [4140, "2026-11-04T07:55:31Z", null, null, null, "auth.login_failed", "LoginAttempt", "att-demo-9f2c1e", null, null, "bad_credentials"],
  [4141, "2026-11-04T07:56:02Z", null, null, null, "auth.login_failed", "LoginAttempt", "att-demo-9f2c1e", null, "LOCKED", "bad_credentials"],
  [4150, "2026-11-04T10:12:44Z", "ten-demo-01", "actor-demo-n11", "HUMAN", "auth.login", "Actor", "actor-demo-n11", null, null, null],
  [4152, "2026-11-04T10:20:05Z", "ten-demo-01", "actor-demo-n11", "HUMAN", "legal_hold.placed", "LegalHold", "lh-demo-0104", null, "ACTIVE", "Regulator request REF-DEMO-77: hold on 2025 evidence"],
  [4163, "2026-11-04T15:03:27Z", null, "actor-demo-cr03", "HUMAN", "content.publish", "ContentRelease", "rel-demo-0042", null, "PUBLISHED", "Content release 2026.11 published"],
  [4171, "2026-11-05T08:31:12Z", "ten-demo-03", "actor-demo-f31", "HUMAN", "authorization.denied", "Endpoint", "/api/platform/organisations", null, "MISSING_PERMISSION", "Missing permission manage_organisations"],
  [4178, "2026-11-05T11:47:55Z", null, SA, "HUMAN", "auth.login", "Actor", SA, null, null, null],
  [4180, "2026-11-05T11:50:02Z", "ten-demo-03", SA, "HUMAN", "platform.organisation_viewed", "Tenant", "ten-demo-03", null, null, null],
  [4181, "2026-11-05T11:58:40Z", "ten-demo-03", SA, "HUMAN", "organisation.status_changed", "Tenant", "ten-demo-03", "ACTIVE", "SUSPENDED", "Contract lapsed pending renewal (12 session(s) revoked)", "corr-demo-5c6d"],
  [4182, "2026-11-05T11:58:40Z", "ten-demo-03", SA, "HUMAN", "auth.session_revoked", "AuthSession", "ses-demo-3101", "ACTIVE", "REVOKED", `Session of actor-demo-f31 revoked by ${SA}`, "corr-demo-5c6d"],
  [4190, "2026-11-06T09:05:16Z", "ten-demo-03", "actor-demo-f31", "HUMAN", "auth.login_refused_suspended", "Actor", "actor-demo-f31", null, null, null],
  [4198, "2026-11-06T13:40:00Z", null, SA, "HUMAN", "platform.organisation_registry_read", "OrganisationRegistry", DEMO_CELL, null, null, "5 organisation(s) listed (filters: status=SUSPENDED)"],
  [4205, "2026-11-06T16:12:31Z", "ten-demo-02", "agent-demo-c23", "AGENT", "evidence.download", "EvidenceArtifact", "evd-demo-2290", null, null, "Downloaded for control test CT-DEMO-14"],
  [4214, "2026-11-07T08:20:44Z", null, "actor-demo-ca02", "HUMAN", "authorization.denied", "Endpoint", "/api/platform/organisations", null, "MISSING_PERMISSION", "Missing permission manage_organisations"],
  [4220, "2026-11-07T10:33:09Z", null, "actor-demo-cr03", "HUMAN", "connector.certification_changed", "ConnectorDefinition", "conn-demo-entra", "CERTIFIED", "UNDER_REVIEW", "Vendor API change detected"],
  [4229, "2026-11-07T14:15:00Z", "ten-demo-05", "actor-demo-w51", "HUMAN", "role.revoked", "RoleAssignment", "ra-demo-5102", "AUDITOR@TENANT", "REVOKED", "Engagement ended"],
  [4230, "2026-11-07T14:16:22Z", "ten-demo-05", "actor-demo-w51", "HUMAN", "authority_grant.revoked", "AuthorityGrant", "ag-demo-5011", "ACTIVE", "REVOKED", "External auditor engagement closed"],
  [4241, "2026-11-08T09:00:05Z", null, SA, "HUMAN", "auth.login", "Actor", SA, null, null, null],
  [4243, "2026-11-08T09:02:30Z", "ten-demo-03", SA, "HUMAN", "organisation.status_changed", "Tenant", "ten-demo-03", "SUSPENDED", "ACTIVE", "Renewal signed; reactivated"],
  [4250, "2026-11-08T12:44:10Z", "ten-demo-01", "actor-demo-n12", "HUMAN", "issue.transition", "Issue", "iss-demo-0177", "OPEN", "IN_REMEDIATION", "Remediation plan accepted"],
  [4256, "2026-11-08T17:20:00Z", null, SA, "HUMAN", "auth.logout", "Actor", SA, null, null, null],
  [4262, "2026-11-09T10:10:10Z", "ten-demo-02", "actor-demo-c22", "HUMAN", "exception.decide", "Exception", "exc-demo-0213", "REQUESTED", "APPROVED", "Compensating control in place until Q1"],
  // An empty string, not null: shows how the screen tells an empty value from a missing one.
  [4270, "2026-11-09T15:45:33Z", "ten-demo-04", "actor-demo-t41", "HUMAN", "auth.password_changed", "Actor", "actor-demo-t41", null, null, ""],
  [4281, "2026-11-10T08:00:12Z", null, "actor-demo-cr03", "HUMAN", "auth.login", "Actor", "actor-demo-cr03", null, null, null],
  [4284, "2026-11-10T08:30:00Z", null, "actor-demo-cr03", "HUMAN", "content.publish", "ContentRelease", "rel-demo-0043", null, "PUBLISHED", "Content release 2026.11.1 published"],
  [4290, "2026-11-10T11:02:47Z", "ten-demo-01", "actor-demo-n11", "HUMAN", "legal_hold.released", "LegalHold", "lh-demo-0104", "ACTIVE", "RELEASED", "Regulator request REF-DEMO-77 closed"],
  [4297, "2026-11-10T13:25:51Z", "ten-demo-05", "actor-demo-w52", "HUMAN", "auth.login_failed", "Actor", "actor-demo-w52", null, null, "bad_credentials"],
  [4305, "2026-11-11T07:45:00Z", null, SA, "HUMAN", "auth.login", "Actor", SA, null, null, null],
  [4306, "2026-11-11T07:46:13Z", null, SA, "HUMAN", "platform.organisation_registry_read", "OrganisationRegistry", DEMO_CELL, null, null, "5 organisation(s) listed"],
  [4312, "2026-11-11T09:20:31Z", "ten-demo-05", SA, "HUMAN", "platform.organisation_viewed", "Tenant", "ten-demo-05", null, null, null],
  [4313, "2026-11-11T09:24:02Z", "ten-demo-05", SA, "HUMAN", "organisation.updated", "Tenant", "ten-demo-05", "Woodgrove Bank", "Woodgrove Bank plc", "Legal name corrected"],
  [4320, "2026-11-11T14:02:18Z", "ten-demo-02", "actor-demo-c21", "HUMAN", "role.assigned", "RoleAssignment", "ra-demo-2215", null, "EVIDENCE_REVIEWER@ENTITY",
    "admin: EVIDENCE_REVIEWER for actor-demo-c24 at ENTITY scope; Quarterly review cover"],
  [4331, "2026-11-12T06:58:04Z", null, SA, "HUMAN", "auth.login", "Actor", SA, null, null, null],
  [4333, "2026-11-12T07:05:44Z", null, SA, "HUMAN", "auth.session_revoked", "AuthSession", "ses-demo-0099", "ACTIVE", "REVOKED", `Session of ${SA} revoked by ${SA}`],
  [4340, "2026-11-12T08:11:29Z", "ten-demo-04", "actor-demo-t41", "HUMAN", "authorization.denied", "Endpoint", "/api/role-assignments", null, "MISSING_PERMISSION", "Missing permission manage_roles"],
  [4345, "2026-11-12T09:30:00Z", null, "svc-demo-ingest", "SERVICE", "source.ingest", "RegulatorySource", "src-demo-0118", null, "INGESTED", "Ingested version 4.2"],
];

export const DEMO_EVENTS: AuditEventRecord[] = ROWS.map(([record_seq, recorded_at, tenant_id, actor_id, actor_type, action, object_type, object_id,
  prior_state, new_state, reason, correlation_id], i) => ({
  id: `evt-demo-${String(i + 1).padStart(4, "0")}`, tenant_id, actor_id, actor_type, action, object_type, object_id,
  prior_state, new_state, reason, correlation_id: correlation_id ?? null, record_seq, recorded_at,
}));
