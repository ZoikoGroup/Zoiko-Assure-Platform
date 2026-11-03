"use client";

import { useCallback, useEffect, useState } from "react";
import { DescriptionList, ErrorState, Spinner, StateBadge } from "@/components/ds";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";

/* The two real, public, read-only platform signals this deployment offers (routers/core.py): GET /health (a static
   "ok" from the API process; it does not check the database) and GET /watermark (the cell's latest record sequence).
   Everything else in Super Admin Planes is sample data. */

export type LiveHealth = {
  state: "loading" | "ok" | "error";
  health?: { status: string; cell_id: string; dictionary_version: string };
  watermark?: { cell_id: string; record_seq: number; recorded_at?: string };
  ms?: number; at?: string; error?: unknown;
};

export function useLiveHealth() {
  const [live, setLive] = useState<LiveHealth>({ state: "loading" });
  const run = useCallback(async () => {
    setLive({ state: "loading" });
    const t0 = performance.now();
    try {
      const [health, watermark] = await Promise.all([api("/health"), api("/watermark")]);
      setLive({ state: "ok", health, watermark, ms: Math.round(performance.now() - t0), at: new Date().toISOString() });
    } catch (error) {
      setLive({ state: "error", error, at: new Date().toISOString() });
    }
  }, []);
  useEffect(() => { run(); }, [run]);
  return { live, run };
}

/** Marks a value read from this deployment, as opposed to sample data. */
export const LiveTag = () => (
  <span className="rounded-chip border border-status-verified-line bg-status-verified-tint px-1.5 type-label text-status-verified">Live</span>
);

/** The live probe result, with its limits stated. */
export function LiveHealthBlock({ live, onRetry }: { live: LiveHealth; onRetry: () => void }) {
  if (live.state === "loading") return <Spinner showLabel label="Checking this deployment…" />;
  if (live.state === "error" || !live.health) return <ErrorState error={live.error} title="This deployment's API did not answer" onRetry={onRetry} compact />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><LiveTag /><StateBadge code={live.health.status.toUpperCase()} size="sm" /><span className="type-caption text-fg-muted">answered in {live.ms} ms</span></div>
      <DescriptionList items={[
        ["Cell", <code key="c" className="type-code">{live.health.cell_id}</code>], ["Dictionary version", <code key="d" className="type-code">{live.health.dictionary_version}</code>],
        ["Record watermark", live.watermark ? <code key="w" className="type-code">#{live.watermark.record_seq}</code> : "—"], ["Checked", live.at ? fmtDate(live.at, true) : "—"],
      ]} />
      <p className="type-caption text-fg-muted">From GET /api/health and /api/watermark on this deployment. The health endpoint answers from the API process; it does not check the database or any other service.</p>
    </div>
  );
}
