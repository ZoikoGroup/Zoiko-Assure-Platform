"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DEMO_CHECKS, DEMO_SOURCES } from "./sourceIntegrityMockData";
import type { SourceIntegrityData } from "./types";

/* The one place the Source Integrity screen gets its data.

   Today it returns the static demonstration records in sourceIntegrityMockData.ts and makes no network request of any
   kind. A short pause before they appear keeps the loading state honest to how the screen will behave once real reads
   exist. When the backend serves Source Integrity reads, replace loadDemo() below with those reads, map the responses
   onto the types in types.ts, and leave the components unchanged.

   Preview states: adding ?demo-state=loading, ?demo-state=empty or ?demo-state=error to the page address shows that
   state with the demonstration data, so the screen's loading, empty and error presentations can be reviewed without a
   backend. Nothing is sent in any of them. */

const DEMO_PAUSE_MS = 350;

type PreviewState = "loading" | "empty" | "error" | null;

function previewState(): PreviewState {
  if (typeof window === "undefined") return null;
  const v = new URLSearchParams(window.location.search).get("demo-state");
  return v === "loading" || v === "empty" || v === "error" ? v : null;
}

function loadDemo(preview: PreviewState): Promise<SourceIntegrityData> {
  if (preview === "loading") return new Promise<SourceIntegrityData>(() => {});  // never settles: the loading state stays
  return new Promise<SourceIntegrityData>((resolve, reject) => {
    window.setTimeout(() => {
      if (preview === "error") reject(new Error("This is a preview of the error state (?demo-state=error). Nothing was requested."));
      else if (preview === "empty") resolve({ sources: [], checks: [] });
      else resolve({ sources: DEMO_SOURCES, checks: DEMO_CHECKS });
    }, DEMO_PAUSE_MS);
  });
}

export type SourceIntegrityLoad = {
  data: SourceIntegrityData | null;
  error: unknown;
  loading: boolean;
  /** When the data on screen was loaded (ISO 8601). */
  loadedAt: string | null;
  reload: () => void;
};

/** The screen's data, loaded on mount and again on each explicit reload. A failed reload keeps the last good data. */
export function useSourceIntegrityData(): SourceIntegrityLoad {
  const [data, setData] = useState<SourceIntegrityData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const next = await loadDemo(previewState());
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
    void load();
    return () => { seq.current++; };
  }, [load]);

  return { data, error, loading, loadedAt, reload: () => void load() };
}
