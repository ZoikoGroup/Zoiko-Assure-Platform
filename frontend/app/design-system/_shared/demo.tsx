"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, Check, RotateCcw } from "lucide-react";
import { Button, Callout, Card, DescriptionList, EmptyState, ErrorState, PlaneBadge, SkeletonText, STATUS_ICON, STATUS_TONE, TabPanel, Tabs, type Plane } from "@/components/ds";
import { useAuth } from "@/lib/actor";
import { permissionText, rolesText, useCan } from "@/lib/roles";

/* Shared scaffolding for the Design Governance prototypes (High-Assurance Flows, Super Admin Planes). Every prototype is
   a client-side demonstration on sample data: nothing here calls the backend. The frame says so every time, names the
   plane it belongs to, the real screen and backend action it stands in for (or that none exists yet), and whether the
   signed-in role holds the permission the server would check. */

export type AppMapping = {
  screen: string; route?: string; action: string;
  /** Permission code the server checks (security.py); omit when the server checks none today. */
  permission?: string;
  enforced: string[];
};
/** How much of a prototype the application implements today. */
export type Availability = { level: "available" | "partial" | "none"; note: string };
const AVAILABILITY: Record<Availability["level"], { label: string; status: "verified" | "warning" | "unknown" }> = {
  available: { label: "Available in the application", status: "verified" }, partial: { label: "Partly available", status: "warning" }, none: { label: "Not built yet", status: "unknown" },
};
type Scenario = "ready" | "loading" | "empty" | "error";

/** A High-Assurance Flow: always a Customer Assurance Workspace prototype. */
export const FlowFrame = (p: Omit<Parameters<typeof DemoFrame>[0], "plane">) => <DemoFrame plane="workspace" {...p} />;

export function DemoFrame({ id, title, intro, app, empty, plane, availability, children }: {
  id: string; title: string; intro: ReactNode; app: AppMapping; empty: { title: string; description: string };
  plane: Plane; availability?: Availability; children: ReactNode;
}) {
  const [scenario, setScenario] = useState<Scenario>("ready");
  const [run, setRun] = useState(0);
  const { can } = useCan();
  const has = app.permission ? can(app.permission) : true;
  const avail = availability && AVAILABILITY[availability.level];
  return (
    <div className="space-y-stack">
      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <PlaneBadge plane={plane} size="sm" />
            {avail && (() => { const Icon = STATUS_ICON[avail.status]; return (
              <span className={`inline-flex items-center gap-1 rounded-chip border px-1.5 type-caption font-semibold ${STATUS_TONE[avail.status]}`}>
                <Icon size={11} strokeWidth={2.5} aria-hidden />{avail.label}
              </span>
            ); })()}
          </div>
          <h2 className="type-card text-fg">{title}</h2>
          <div className="mt-1 max-w-3xl type-secondary text-fg-secondary">{intro}</div>
          <Callout tone="info" title="Demonstration: nothing is sent to the server" className="mt-4">
            Sample data, handled only in your browser. In the application the server checks your permission, validates the request and records
            the outcome. Confirming something here is not an approval, an attestation or a record.
          </Callout>
        </div>
        <Card title="In the application" bodyClassName="p-card">
          <DescriptionList columns={1} items={[
            ["Screen", app.route ? <Link href={app.route} className="font-medium text-accent-fg underline-offset-4 hover:underline">{app.screen}</Link> : app.screen],
            ["Backend action this stands in for", <code key="a" className="type-code text-fg">{app.action}</code>],
            ["Permission the server checks", app.permission ? (
              <span key="p" className="inline-flex flex-wrap items-center gap-x-1.5">
                {permissionText(app.permission)} <code className="type-code text-fg-muted">{app.permission}</code>
                <span className={`inline-flex items-center gap-1 type-caption ${has ? "text-status-verified" : "text-status-warning"}`}>
                  {has ? <Check size={12} aria-hidden /> : <AlertTriangle size={12} aria-hidden />}{has ? "Your role has it" : "Your role does not; the server would refuse"}
                </span>
              </span>
            ) : "None today"],
            ["Server enforces", <ul key="e" className="list-disc space-y-0.5 pl-4">{app.enforced.map((e) => <li key={e}>{e}</li>)}</ul>],
            ...(availability ? [["What exists today", availability.note] as [string, ReactNode]] : []),
          ]} />
        </Card>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs id={`${id}-scenario`} label={`${title}: demonstration scenario`} variant="pills" value={scenario} onChange={(v) => setScenario(v as Scenario)}
          items={[{ value: "ready", label: "Ready" }, { value: "loading", label: "Loading" }, { value: "empty", label: "Empty" }, { value: "error", label: "Load error" }]} />
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => { setScenario("ready"); setRun((r) => r + 1); }}>Restart demo</Button>
      </div>
      <TabPanel id={`${id}-scenario`} value={scenario} className="">
        {scenario === "loading" ? <Card><SkeletonText lines={6} label={`Loading ${title}`} /></Card>
          : scenario === "empty" ? <Card><EmptyState title={empty.title} description={empty.description} /></Card>
          : scenario === "error" ? <Card><ErrorState error={new Error("The server did not respond. (Demonstration of a failed load.)")} title={`${title} could not be loaded`} onRetry={() => setScenario("ready")} /></Card>
          : <div key={run}>{children}</div>}
      </TabPanel>
    </div>
  );
}

/** A labelled group inside a flow card. */
export const Section = ({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) => (
  <section className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="type-label text-fg-muted">{title}</h3>{aside}</div>
    {children}
  </section>
);

/** Marks a value as illustrative sample data. */
export const Sample = () => <span className="ml-1.5 rounded-chip border border-dashed border-line-strong px-1 type-caption text-fg-muted">sample</span>;

/** The signed-in person, from the session (the one real input the demos use). */
export function useYou() {
  const { me } = useAuth();
  return { name: (me?.display_name ?? "You").replace(/\s*\(.*\)/, ""), roles: rolesText(me) || "no roles", authority: me?.authority_basis ?? null };
}

/** Simulated progress for a demo operation, cleared on unmount. `failAt` stops it there with a failure. */
export function useSimulatedProgress() {
  const [value, setValue] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<"ok" | "failed" | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  useEffect(() => () => clearInterval(timer.current), []);
  const start = (failAt?: number, step = 7, ms = 150) => {
    clearInterval(timer.current);
    setOutcome(null);
    setValue(0);
    let p = 0;
    timer.current = setInterval(() => {
      p = Math.min(100, p + step);
      if (failAt != null && p >= failAt) { clearInterval(timer.current); setValue(failAt); setOutcome("failed"); return; }
      setValue(p);
      if (p >= 100) { clearInterval(timer.current); setOutcome("ok"); }
    }, ms);
  };
  const reset = () => { clearInterval(timer.current); setValue(null); setOutcome(null); };
  return { value, outcome, start, reset };
}

export const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
export const now = () => new Date().toISOString();

/** SHA-256 of bytes or text, computed in the browser (Web Crypto). */
export async function sha256(data: ArrayBuffer | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
