/* The Obligations Register's only read: GET /api/obligations (backend/app/routers/assurance.py `obligations`, guarded
   by DOSSIER_READ = read or audit_read). These types describe what that endpoint already returns; they are not a new
   contract. Fields the register does not show are left out. Dates are ISO 8601 UTC strings. */

/** The catalogue definition the obligation was adopted from. When the backend cannot find it, it sends the ID with
 *  null display fields and `missing: true` instead of failing the whole register. */
export interface ObligationDefinitionSummary {
  key: string | null;
  title: string | null;
  /** CONTINUOUS, RECURRING or EVENT_TRIGGERED in the current catalogue. */
  schedule_kind: string | null;
  obligation_definition_id?: string | null;
  missing?: boolean;
}

/** The subject the obligation binds (a legal entity, business unit, product, AI asset, …). Same `missing` convention. */
export interface ObligationSubject {
  subject_id: string | null;
  display_name: string | null;
  subject_type?: string | null;
  entity_id?: string | null;
  missing?: boolean;
}

/** The most recently frozen period snapshot, without its segments and dependency manifest. */
export interface SnapshotSummary {
  snapshot_id: string;
  /** PROVEN, NOT_PROVEN, FAILING or REQUIRES_REVIEW (ZA-STA-001). */
  state: string;
  period_from: string;
  /** End-exclusive. */
  period_to: string;
  perspective: string;
  record_cutoff_seq: number | null;
  created_at: string;
  reason_codes?: string[] | null;
}

/** One current obligation revision, as listed by GET /obligations. */
export interface ObligationRecord {
  lineage_id: string;
  revision_id: string;
  entity_id: string | null;
  subject_id: string;
  obligation_definition_id: string;
  /** ADOPTED or CLOSED today; the dictionary also defines PROPOSED, SUSPENDED and SUPERSEDED. */
  lifecycle: string;
  valid_from: string;
  valid_to: string | null;
  definition: ObligationDefinitionSummary;
  subject: ObligationSubject;
  latest_snapshot: SnapshotSummary | null;
  /** Exceptions on this obligation recorded in state APPROVED (counted by the backend). */
  open_exceptions: number;
}
