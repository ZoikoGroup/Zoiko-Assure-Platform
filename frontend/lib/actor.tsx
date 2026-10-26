"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { API, ApiError, clearToken, getToken, setToken } from "./api";

export type Me = {
  actor_id: string; display_name: string; actor_type: string; email: string; roles: string[];
  tenant_id: string | null; tenant_name: string; permissions: string[]; authority_basis?: string | null;
  must_change_password?: boolean;
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
  /** Signs in and returns the server-verified account; throws ApiError (status 0 = the server was unreachable). */
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>({
  me: null, ready: false, actorId: "", login: async () => { throw new ApiError(0, "No auth provider"); }, logout: async () => {}, refresh: async () => {},
});

export function ActorProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const path = usePathname();

  const loadSession = useCallback(async () => {
    const t = getToken();
    if (!t) { setMe(null); setReady(true); return; }
    try {
      const r = await fetch(`${API}/auth/session`, { headers: { Authorization: `Bearer ${t}` }, cache: "no-store" });
      if (!r.ok) throw new Error();
      setMe((await r.json()).actor);
    } catch {
      clearToken();
      setMe(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => { loadSession(); }, [loadSession]);

  // any 401 from the API (expired / revoked session) sends the user back to sign-in
  useEffect(() => {
    const onUnauth = () => { clearToken(); setMe(null); };
    window.addEventListener("za-unauthorized", onUnauth);
    return () => window.removeEventListener("za-unauthorized", onUnauth);
  }, []);

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
    return body.actor as Me;
  };

  const logout = async () => {
    const t = getToken();
    try { if (t) await fetch(`${API}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${t}` } }); } catch {}
    clearToken();
    setMe(null);
    router.replace("/login");
  };

  return <Ctx.Provider value={{ me, ready, actorId: me?.actor_id ?? "", login, logout, refresh: loadSession }}>{children}</Ctx.Provider>;
}

export const useActor = () => useContext(Ctx);
export const useAuth = useActor;
