/* JIT Access (Platform Control Plane): what the screen displays.

   These shapes describe the screen, NOT an approved backend API contract. No JIT, break-glass or support-access model
   or endpoint exists (the Privileged Access Log states it is not supported), and nothing here names a real permission,
   role code or database field. When a backend model is approved, map its responses onto these types in
   useJitAccessData.ts and leave the components unchanged. Every value the screen shows today comes from
   jitMockData.ts. */

export const APPROVAL_STATUSES = ["PENDING", "APPROVED", "REJECTED"] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];

/** Where a request's access stands, computed against the demonstration clock (model.ts). */
export const ACCESS_STATUSES = ["AWAITING_APPROVAL", "ACTIVE", "EXPIRING_SOON", "EXPIRED", "REVOKED", "NOT_GRANTED"] as const;
export type AccessStatus = (typeof ACCESS_STATUSES)[number];

/** Illustrative role labels for this demonstration. They are not platform roles, role codes or permissions. */
export type JitRole = { id: string; label: string; description: string };

export type Organisation = { id: string; name: string } | null;  // null: platform-wide, no single organisation

export type Requester = { name: string; team: string; email: string };

export type Decision = {
  /** Who decided. "You (local demonstration)" for decisions made on this page. */
  by: string;
  at: string;
  /** Approved only: the hours granted, which may be fewer than requested. */
  approvedHours?: number;
  /** Rejected only. */
  reason?: string;
  /** Made on this page in the browser: local demonstration state, never sent anywhere. */
  local?: boolean;
};

export type JitRequest = {
  id: string;
  requester: Requester;
  organisation: Organisation;
  roleId: string;
  justification: string;
  requestedHours: number;
  requestedAt: string;
  /** When the requester asked the access to start; the requested end is this plus requestedHours. */
  requestedStart: string;
  status: ApprovalStatus;
  decision: Decision | null;
};

export type Extension = { hours: number; by: string; at: string; local?: boolean };

/** The temporary access an approved request produced. */
export type JitSession = {
  id: string;
  requestId: string;
  startedAt: string;
  /** The current expiry, including any extensions. */
  expiresAt: string;
  extensions: Extension[];
  revoked: { by: string; at: string; reason: string | null; local?: boolean } | null;
  /** Created on this page by a local demonstration approval. */
  local?: boolean;
};

export type JitData = { roles: JitRole[]; requests: JitRequest[]; sessions: JitSession[] };
