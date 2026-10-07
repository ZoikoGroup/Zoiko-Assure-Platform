"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { API, clearToken, getToken, setToken } from "./api";

export type Me = {
  actor_id: string; display_name: string; actor_type: string; email: string; roles: string[];
  tenant_id: string | null; tenant_name: string; permissions: string[]; authority_basis?: string | null;
};

type AuthCtx = {
  me: Me | null;
  ready: boolean;
  actorId: string; // kept for data hooks: reload when the signed-in identity changes
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>({ me: null, ready: false, actorId: "", login: async () => {}, logout: async () => {} });

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

  const login = async (email: string, password: string) => {
    const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error(body?.detail ?? "Sign-in failed");
    setToken(body.token);
    setMe(body.actor);
  };

  const logout = async () => {
    const t = getToken();
    try { if (t) await fetch(`${API}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${t}` } }); } catch {}
    clearToken();
    setMe(null);
    router.replace("/login");
  };

  return <Ctx.Provider value={{ me, ready, actorId: me?.actor_id ?? "", login, logout }}>{children}</Ctx.Provider>;
}

export const useActor = () => useContext(Ctx);
export const useAuth = useActor;
