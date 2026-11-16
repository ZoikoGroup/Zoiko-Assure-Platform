"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DEMO_NOW, DEMO_REQUESTS, DEMO_ROLES, DEMO_SESSIONS } from "./jitMockData";
import { ILLUSTRATIVE, LOCAL_ACTOR, addHours, grantedHours, hoursLabel } from "./model";
import type { JitData } from "./types";

/* The one place the JIT Access screen gets its data, and the one place its demonstration actions change it.

   Today it returns the static demonstration records in jitMockData.ts (after a short pause, so the loading state is
   real) and makes NO network request of any kind. The actions below (approve, reject, revoke, extend) change only this
   hook's in-memory copy of those records: nothing is sent, no access is granted or removed, and every change is lost
   on reload or Reset. Each change is listed in `changes` so the page can label it as local demonstration state.

   When a JIT backend model is approved, replace loadDemo() with its reads and map the responses onto types.ts. The
   actions are demonstration-only and are not a design for the real workflow, which must run on the server.

   Preview states: ?demo-state=loading | empty | error shows that state with the demonstration data. */

const DEMO_PAUSE_MS = 350;
type PreviewState = "loading" | "empty" | "error" | null;

function previewState(): PreviewState {
  if (typeof window === "undefined") return null;
  const v = new URLSearchParams(window.location.search).get("demo-state");
  return v === "loading" || v === "empty" || v === "error" ? v : null;
}

const clone = (d: JitData): JitData => structuredClone(d);

function loadDemo(preview: PreviewState): Promise<JitData> {
  if (preview === "loading") return new Promise<JitData>(() => {});  // never settles: the loading state stays
  return new Promise<JitData>((resolve, reject) => {
    window.setTimeout(() => {
      if (preview === "error") reject(new Error("This is a preview of the error state (?demo-state=error). Nothing was requested."));
      else if (preview === "empty") resolve({ roles: DEMO_ROLES, requests: [], sessions: [] });
      else resolve(clone({ roles: DEMO_ROLES, requests: DEMO_REQUESTS, sessions: DEMO_SESSIONS }));
    }, DEMO_PAUSE_MS);
  });
}

/** A change made on this page, for the "local demonstration changes" panel. */
export type LocalChange = { id: number; text: string };

export type JitActions = {
  approve: (requestId: string, hours: number) => void;
  reject: (requestId: string, reason: string) => void;
  revoke: (sessionId: string, reason: string | null) => void;
  extend: (sessionId: string, hours: number) => void;
  /** Discards every local change and restores the demonstration records. */
  reset: () => void;
};

export type JitLoad = {
  data: JitData | null; error: unknown; loading: boolean; loadedAt: string | null;
  changes: LocalChange[]; actions: JitActions;
  /** Loads the demonstration records again, discarding local changes. */
  reload: () => void;
};

export function useJitAccessData(): JitLoad {
  const [base, setBase] = useState<JitData | null>(null);
  const [data, setData] = useState<JitData | null>(null);
  const [changes, setChanges] = useState<LocalChange[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const seq = useRef(0);
  const changeId = useRef(0);
  const localSession = useRef(0);

  const load = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const next = await loadDemo(previewState());
      if (id !== seq.current) return;  // superseded, or the page closed
      setBase(next);
      setData(clone(next));
      setChanges([]);
      setLoadedAt(new Date().toISOString());
    } catch (e) {
      if (id === seq.current) setError(e);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { seq.current++; };
  }, [load]);

  const log = (text: string) => setChanges((c) => [...c, { id: ++changeId.current, text }]);

  const actions: JitActions = {
    approve: (requestId, hours) => {
      const sessionId = `SES-DEMO-L${String(++localSession.current).padStart(2, "0")}`;
      setData((d) => d && ({
        ...d,
        requests: d.requests.map((r) => (r.id === requestId && r.status === "PENDING"
          ? { ...r, status: "APPROVED", decision: { by: LOCAL_ACTOR, at: DEMO_NOW, approvedHours: hours, local: true } } : r)),
        sessions: [...d.sessions, { id: sessionId, requestId, startedAt: DEMO_NOW, expiresAt: addHours(DEMO_NOW, hours), extensions: [], revoked: null, local: true }],
      }));
      log(`${requestId} approved for ${hoursLabel(hours)}; demonstration session ${sessionId} shown as active. No access was granted.`);
    },
    reject: (requestId, reason) => {
      setData((d) => d && ({
        ...d,
        requests: d.requests.map((r) => (r.id === requestId && r.status === "PENDING"
          ? { ...r, status: "REJECTED", decision: { by: LOCAL_ACTOR, at: DEMO_NOW, reason, local: true } } : r)),
      }));
      log(`${requestId} rejected. No request was changed anywhere else.`);
    },
    revoke: (sessionId, reason) => {
      setData((d) => d && ({
        ...d,
        sessions: d.sessions.map((s) => (s.id === sessionId && !s.revoked ? { ...s, revoked: { by: LOCAL_ACTOR, at: DEMO_NOW, reason, local: true } } : s)),
      }));
      log(`${sessionId} shown as revoked. No real session was changed.`);
    },
    extend: (sessionId, hours) => {
      setData((d) => {
        if (!d) return d;
        return {
          ...d,
          sessions: d.sessions.map((s) => {
            const req = d.requests.find((r) => r.id === s.requestId);
            if (s.id !== sessionId || s.revoked || !req || grantedHours(req, s) + hours > ILLUSTRATIVE.maxHours) return s;
            return { ...s, expiresAt: addHours(s.expiresAt, hours), extensions: [...s.extensions, { hours, by: LOCAL_ACTOR, at: DEMO_NOW, local: true }] };
          }),
        };
      });
      log(`${sessionId} shown as extended by ${hoursLabel(hours)}. No real session was changed.`);
    },
    reset: () => {
      if (base) setData(clone(base));
      setChanges([]);
      localSession.current = 0;
    },
  };

  return { data, error, loading, loadedAt, changes, actions, reload: () => void load() };
}
