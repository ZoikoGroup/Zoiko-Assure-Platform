"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { API, ApiError, clearToken, getToken, responseError, setToken } from "./api";

/** A role assignment in force for the signed-in principal, as the role registry reports it (backend app/iam.py
 *  `assignment_view`): the role at an explicit scope for a validity period, how it was granted, and the permissions it
 *  confers within that scope only. */
export type RoleAssignment = {
  assignment_id: string; tenant_id: string | null; principal_id: string; role_code: string;
  scope_type: "PLATFORM" | "TENANT" | "LEGAL_ENTITY"; scope_id: string | null;
  valid_from: string; valid_to: string | null; status: string; source: string; granted_by: string | null;
  reason: string | null; record_seq: number; recorded_at: string;
  revoked_at: string | null; revoked_by: string | null; revoke_reason: string | null; revoked_record_seq: number | null;
  permissions: string[]; in_force: boolean;
};

/** The signed-in principal from the session check (GET /auth/session). `roles` and `permissions` are those held
 *  organisation- or platform-wide; entity-scoped roles and their permissions appear only in `role_assignments`. */
export type Me = {
  actor_id: string; display_name: string; actor_type: string; email: string | null; roles: string[];
  tenant_id: string | null; tenant_name: string; permissions: string[]; authority_basis: string | null;
  role_assignments: RoleAssignment[]; must_change_password: boolean;
};

/** Who is signed in: a customer organisation user, Zoiko content staff, or the platform Super Admin.
 *  This only shapes navigation; the backend enforces every permission and tenant boundary. */
export type Persona = "tenant" | "content" | "platform";
export function personaOf(me: Me | null): Persona {
  if (me?.tenant_id) return "tenant";
  return me?.permissions?.includes("manage_organisations") ? "platform" : "content";
}

type AuthCtx = {
  me: Me | null;
  ready: boolean;
  actorId: string; // kept for data hooks: reload when the signed-in identity changes
  /** When the server ends this session (its absolute expiry, from sign-in or the session check); null when unknown. */
  sessionExpiresAt: string | null;
  /** Signs in and returns the server-verified account; throws ApiError (status 0 = the server was unreachable). */
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Re-reads the account, roles and permissions from the session check. Only a 401 signs out; any other failure keeps
   *  the current identity and throws ApiError (status 0 = unreachable or timed out). */
  reloadAccess: () => Promise<void>;
};

/** How long a full page load waits for the server to confirm the session before treating the check as failed. */
const SESSION_CHECK_TIMEOUT_MS = 10_000;

const Ctx = createContext<AuthCtx>({
  me: null, ready: false, actorId: "", sessionExpiresAt: null, login: async () => { throw new ApiError(0, "No auth provider"); },
  logout: async () => {}, refresh: async () => {}, reloadAccess: async () => {},
});

/** A session expiry as an unambiguous UTC instant. The backend keeps every time in UTC and reads one without an offset
 *  as UTC (app/platform.py ensure_utc); the session check can send its expiry without one (as SQLite returns it), which a
 *  browser would otherwise read as local time. */
function utcInstant(v: unknown): string | null {
  if (typeof v !== "string" || !v) return null;
  return /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(v) ? v : `${v}Z`;
}

/** GET /auth/session with `token`, given up after SESSION_CHECK_TIMEOUT_MS. Rejects when there is no response. */
async function sessionCheck(token: string): Promise<Response> {
  const timeout = new AbortController();
  const timer = window.setTimeout(() => timeout.abort(), SESSION_CHECK_TIMEOUT_MS);
  try {
    return await fetch(`${API}/auth/session`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: timeout.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

export function ActorProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [sessionExpiresAt, setSessionExpiresAt] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const path = usePathname();

  const signedOut = useCallback(() => { clearToken(); setMe(null); setSessionExpiresAt(null); }, []);

  const loadSession = useCallback(async () => {
    const t = getToken();
    if (!t) { setMe(null); setSessionExpiresAt(null); setReady(true); return; }
    // A server that accepts the connection but never answers would otherwise leave the session check, and the page,
    // pending forever. After the timeout it is treated like any other failed check: no identity, back to sign-in.
    try {
      const r = await sessionCheck(t);
      if (!r.ok) throw new Error();
      const body = await r.json();
      setMe(body.actor);
      setSessionExpiresAt(utcInstant(body.expires_at));
    } catch {
      signedOut();
    } finally {
      setReady(true);
    }
  }, [signedOut]);

  useEffect(() => { loadSession(); }, [loadSession]);

  /* The Your Access Refresh: a temporary network or server failure must not sign anyone out, so unlike the start-up
     check only a 401 (session revoked or expired) takes the normal sign-out path, the same one every API call uses. */
  const reloadAccess = useCallback(async () => {
    const t = getToken();
    if (!t) { window.dispatchEvent(new Event("za-unauthorized")); return; }
    let r: Response;
    try {
      r = await sessionCheck(t);
    } catch {
      throw new ApiError(0, null);
    }
    if (r.status === 401) { window.dispatchEvent(new Event("za-unauthorized")); return; }
    if (!r.ok) throw await responseError(r);
    const body = await r.json();
    setMe(body.actor);
    setSessionExpiresAt(utcInstant(body.expires_at));
  }, []);

  // any 401 from the API (expired / revoked session) sends the user back to sign-in
  useEffect(() => {
    const onUnauth = () => signedOut();
    window.addEventListener("za-unauthorized", onUnauth);
    return () => window.removeEventListener("za-unauthorized", onUnauth);
  }, [signedOut]);

  useEffect(() => {
    if (!ready) return;
    if (!me && path !== "/login") router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [ready, me, path, router]);

  const login = async (email: string, password: string): Promise<Me> => {
    let r: Response;
    try {
      r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
    } catch {
      throw new ApiError(0, null);  // no response at all: network down, server stopped, or CORS refused
    }
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(r.status, body?.detail ?? null);
    setToken(body.token);
    setMe(body.actor);
    setSessionExpiresAt(utcInstant(body.expires_at));
    return body.actor as Me;
  };

  const logout = async () => {
    const t = getToken();
    try { if (t) await fetch(`${API}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${t}` } }); } catch {}
    signedOut();
    router.replace("/login");
  };

  return (
    <Ctx.Provider value={{ me, ready, actorId: me?.actor_id ?? "", sessionExpiresAt, login, logout, refresh: loadSession, reloadAccess }}>
      {children}
    </Ctx.Provider>
  );
}

export const useActor = () => useContext(Ctx);
export const useAuth = useActor;
