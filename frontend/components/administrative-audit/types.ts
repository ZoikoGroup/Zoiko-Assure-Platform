/* Administrative Audit (Platform Control Plane): what the screen displays.

   NOT AN API CONTRACT. No Super Admin endpoint returns audit records across organisations, and none is implied here.

   AuditEventRecord mirrors the columns of the audit model, backend/app/models.py `AuditEvent` (table
   platform_audit_event), using the model's own field names, so the screen shows the fields the platform actually
   records. Everything else (actor display names, organisation names, outcome, category, review flags) is ILLUSTRATIVE
   display data for this demonstration: the model stores none of it. When a backend read is approved, map its response
   onto these types in useAdministrativeAuditData.ts. */

/** One audit record, with the audit model's field names. Nullable where the model's column is nullable. */
export type AuditEventRecord = {
  id: string;
  /** Empty (null) for platform-level records; the organisation's tenant ID for tenant-associated records. */
  tenant_id: string | null;
  actor_id: string | null;
  actor_type: string | null;
  action: string;
  object_type: string;
  object_id: string;
  prior_state: string | null;
  new_state: string | null;
  reason: string | null;
  correlation_id: string | null;
  /** Cell-wide record sequence, shared with other record types, so audit records show gaps. */
  record_seq: number;
  /** Application-server clock, UTC (ISO 8601). */
  recorded_at: string;
};

/** ILLUSTRATIVE: who an actor ID belongs to, for display. Not part of the audit model. */
export type DemoActor = { actor_id: string; name: string };

/** ILLUSTRATIVE: an organisation's name for a tenant ID, for display. Not part of the audit model. */
export type DemoOrganisation = { tenant_id: string; name: string };

export type AuditData = { events: AuditEventRecord[]; actors: DemoActor[]; organisations: DemoOrganisation[] };
