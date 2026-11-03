/* Sample data for the Super Admin Planes prototypes. Fictional organisations and people; field names and codes are the
   application's own (models.py Tenant / LegalEntity / Cell / AuditEvent, routers/platform.py) so the prototypes never
   imply fields the platform does not have:
   - organisation status is ACTIVE or SUSPENDED only; isolation tier is only ever POOLED;
   - contracting region is copied from the server's cell, so every organisation on this cell shares it;
   - audit events have no result field: an outcome can only be read from the action name. */

export const CELL = "eu-west-cell-a";
export const REGION = "eu-west (UK/EU)";
export const CELLS = [
  { id: "eu-west-cell-a", region: "eu-west (UK/EU)", status: "ACTIVE", tier: "POOLED" },
  { id: "uk-south-cell-b", region: "uk-south", status: "STANDBY", tier: "POOLED" },
];

export type TenantStatus = "ACTIVE" | "SUSPENDED";
export type Tenant = {
  id: string; name: string; status: TenantStatus; region: string; tier: "POOLED"; created: string;
  entities: { name: string; country: string; ref?: string; cell: string }[];
  admins: { name: string; email: string }[];
};
const t = (id: string, name: string, status: TenantStatus, created: string, entities: Tenant["entities"], admins: Tenant["admins"]): Tenant =>
  ({ id, name, status, region: REGION, tier: "POOLED", created, entities, admins });

export const TENANTS: Tenant[] = [
  t("ten-demo-01", "Northwind Group", "ACTIVE", "2026-03-04", [{ name: "Northwind Ltd", country: "GB", ref: "08812345", cell: CELL }, { name: "Northwind B.V.", country: "NL", ref: "77123456", cell: CELL }], [{ name: "Avery Clarke", email: "a.clarke@northwind.example" }]),
  t("ten-demo-02", "Halcyon Bank plc", "ACTIVE", "2026-04-18", [{ name: "Halcyon Bank plc", country: "GB", ref: "02234567", cell: CELL }], [{ name: "Morgan Reyes", email: "m.reyes@halcyon.example" }]),
  t("ten-demo-03", "Brightwater Insurance", "SUSPENDED", "2026-05-09", [{ name: "Brightwater Insurance SE", country: "IE", cell: CELL }, { name: "Brightwater UK Ltd", country: "GB", cell: CELL }], [{ name: "Jordan Patel", email: "j.patel@brightwater.example" }]),
  t("ten-demo-04", "Cobalt Payments", "ACTIVE", "2026-07-22", [{ name: "Cobalt Payments B.V.", country: "NL", ref: "81234567", cell: CELL }], [{ name: "Riley Chen", email: "r.chen@cobalt.example" }]),
  t("ten-demo-05", "Meridian Health Trust", "ACTIVE", "2026-09-01", [{ name: "Meridian Health Trust", country: "GB", cell: CELL }], [{ name: "Casey Morgan", email: "c.morgan@meridian.example" }]),
  t("ten-demo-06", "Oakfield Asset Management", "ACTIVE", "2026-10-27", [{ name: "Oakfield AM Ltd", country: "GB", ref: "13579246", cell: CELL }, { name: "Oakfield AM S.A.", country: "LU", cell: CELL }], [{ name: "Taylor Brooks", email: "t.brooks@oakfield.example" }]),
];

/** Masks an email for display (the local part is personal data the overview does not need). */
export const maskEmail = (e: string) => e.replace(/^(.)[^@]*(@.*)$/, "$1•••$2");

export type AuditRow = {
  seq: number; at: string; actor: string; actorType: "HUMAN" | "SERVICE"; action: string;
  objectType: string; objectId: string; prior?: string; next?: string; reason?: string; tenant: string | null;
};
export const AUDIT: AuditRow[] = [
  { seq: 3902, at: "2026-03-04T09:12:00Z", actor: "super-admin", actorType: "HUMAN", action: "organisation.created", objectType: "Tenant", objectId: "ten-demo-01", next: "ACTIVE", tenant: "ten-demo-01" },
  { seq: 3903, at: "2026-03-04T09:12:00Z", actor: "super-admin", actorType: "HUMAN", action: "account.bootstrap_created", objectType: "Actor", objectId: "actor-demo-11", tenant: "ten-demo-01" },
  { seq: 4118, at: "2026-10-27T13:40:00Z", actor: "super-admin", actorType: "HUMAN", action: "organisation.created", objectType: "Tenant", objectId: "ten-demo-06", next: "ACTIVE", tenant: "ten-demo-06" },
  { seq: 4119, at: "2026-10-27T13:41:00Z", actor: "super-admin", actorType: "HUMAN", action: "organisation.admin_appointed", objectType: "Actor", objectId: "actor-demo-62", tenant: "ten-demo-06" },
  { seq: 4126, at: "2026-10-29T10:05:00Z", actor: "super-admin", actorType: "HUMAN", action: "organisation.updated", objectType: "Tenant", objectId: "ten-demo-06", prior: "Oakfield AM", next: "Oakfield Asset Management", tenant: "ten-demo-06" },
  { seq: 4133, at: "2026-10-31T16:22:00Z", actor: "super-admin", actorType: "HUMAN", action: "organisation.status_changed", objectType: "Tenant", objectId: "ten-demo-03", prior: "ACTIVE", next: "SUSPENDED", reason: "Contract lapsed pending renewal (12 session(s) revoked)", tenant: "ten-demo-03" },
  { seq: 4140, at: "2026-11-02T08:15:00Z", actor: "actor-demo-27", actorType: "HUMAN", action: "auth.login_failed", objectType: "Actor", objectId: "actor-demo-27", tenant: "ten-demo-02" },
  { seq: 4141, at: "2026-11-02T08:16:00Z", actor: "actor-demo-27", actorType: "HUMAN", action: "auth.login", objectType: "Actor", objectId: "actor-demo-27", tenant: "ten-demo-02" },
];
/** The audit model records no result; a refusal or failure shows only in the action name. */
export const outcomeOf = (action: string) => (/_failed$|_refused/.test(action) ? "Refused" : "Recorded");
