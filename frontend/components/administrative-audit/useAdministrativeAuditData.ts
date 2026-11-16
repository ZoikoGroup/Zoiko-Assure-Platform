"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DEMO_ACTORS, DEMO_EVENTS, DEMO_ORGANISATIONS } from "./auditMockData";
import type { AuditData } from "./types";

/* The one place the Administrative Audit screen gets its records.

   Today it returns the static demonstration records in auditMockData.ts and makes NO network request: no audit,
   organisation, ledger, export or verification endpoint is called, and nothing is recorded. When a backend read is
   approved, replace loadDemo() with it and map the response onto types.ts; the components stay unchanged.

   Preview states (no request is made for any of them), chosen on the page or with ?demo-state=:
     loading      the load never finishes
     empty        the audit history holds no records
     unavailable  the audit service answers as unavailable (as an HTTP 503 would)
     denied       the audit service refuses the read (as an HTTP 403 would) */

export const PREVIEW_STATES = ["", "loading", "empty", "unavailable", "denied"] as const;
export type PreviewState = (typeof PREVIEW_STATES)[number];

export function previewFromUrl(): PreviewState {
  if (typeof window === "undefined") return "";
  const v = new URLSearchParams(window.location.search).get("demo-state") ?? "";
  return (PREVIEW_STATES as readonly string[]).includes(v) ? (v as PreviewState) : "";
}

/** A failed load, as the screen distinguishes them. */
export class AuditLoadError extends Error {
  constructor(public kind: "unavailable" | "denied", message: string) { super(message); }
}

const DEMO_PAUSE_MS = 350;

function loadDemo(preview: PreviewState): Promise<AuditData> {
  if (preview === "loading") return new Promise<AuditData>(() => {});  // never settles: the loading state stays
  return new Promise<AuditData>((resolve, reject) => {
    window.setTimeout(() => {
      if (preview === "unavailable") reject(new AuditLoadError("unavailable", "The audit service is unavailable (a preview of HTTP 503). Nothing was requested."));
      else if (preview === "denied") reject(new AuditLoadError("denied", "Reading audit records is not permitted for this role (a preview of HTTP 403). Nothing was requested."));
      else if (preview === "empty") resolve({ events: [], actors: DEMO_ACTORS, organisations: DEMO_ORGANISATIONS });
      else resolve({ events: DEMO_EVENTS, actors: DEMO_ACTORS, organisations: DEMO_ORGANISATIONS });
    }, DEMO_PAUSE_MS);
  });
}

export type AuditLoad = { data: AuditData | null; error: unknown; loading: boolean; loadedAt: string | null; reload: () => void };

/** The records for `preview` (normally ""), loaded on mount, whenever the preview changes and on each reload. A failed
 *  load clears the records, so a preview of an error never shows stale data beside it. */
export function useAdministrativeAuditData(preview: PreviewState): AuditLoad {
  const [data, setData] = useState<AuditData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async (p: PreviewState) => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const next = await loadDemo(p);
      if (id !== seq.current) return;  // superseded, or the page closed
      setData(next);
      setLoadedAt(new Date().toISOString());
    } catch (e) {
      if (id === seq.current) setError(e);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(preview);
    return () => { seq.current++; };
  }, [load, preview]);

  return { data, error, loading, loadedAt, reload: () => void load(preview) };
}
