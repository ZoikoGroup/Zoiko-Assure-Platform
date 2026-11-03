"use client";

import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import {
  Button, Card, CriticalPanel, DataTable, DescriptionList, EmptyState, ErrorState, Spinner, StateBadge, TabPanel, Tabs, Timeline, type DataColumn,
} from "@/components/ds";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";
import { LiveTag } from "./live";

/* Source integrity. Real checks exist (content release, ledger, evidence, package), but only the content release check
   is reachable without an organisation, and it stores no history and writes no audit event. The opt-in live check
   below runs it for real (read-only GET); the history table is sample data showing what a recorded history could be.
   Tenant ledger and evidence checks belong to the customer plane and are deliberately absent. */

type Check = { id: string; source: string; kind: string; result: "VERIFIED" | "FAILED"; at: string; detail: string; verifier: string; key: string };
const HISTORY: Check[] = [
  { id: "c6", source: "Content release REL-DEMO-0042", kind: "Manifest hash + signature", result: "VERIFIED", at: "2026-11-03T06:00:00Z", detail: "manifest_hash_ok · signature_ok", verifier: "release check", key: "zoiko-content-2026" },
  { id: "c5", source: "Content release REL-DEMO-0041", kind: "Manifest hash + signature", result: "VERIFIED", at: "2026-10-30T06:00:00Z", detail: "manifest_hash_ok · signature_ok", verifier: "release check", key: "zoiko-content-2026" },
  { id: "c4", source: "Content release REL-DEMO-0039", kind: "Manifest hash + signature", result: "FAILED", at: "2026-10-29T11:03:00Z", detail: "manifest_hash_ok · signature did not verify", verifier: "release check", key: "zoiko-content-2026" },
  { id: "c3", source: "Package PKG-DEMO-0007", kind: "Offline package verification (10 steps)", result: "VERIFIED", at: "2026-10-28T15:20:00Z", detail: "PASS_WITH_WARNINGS · HISTORICAL_HMAC_NOT_EXTERNALLY_RECOMPUTED", verifier: "verify_package.py", key: "package signer" },
  { id: "c2", source: "Content release REL-DEMO-0038", kind: "Manifest hash + signature", result: "VERIFIED", at: "2026-10-21T06:00:00Z", detail: "manifest_hash_ok · signature_ok", verifier: "release check", key: "zoiko-content-2026" },
];

export function SourceIntegrity() {
  return (
    <DemoFrame id="sa-integrity" plane="platform" title="Source integrity"
      intro="Whether the content and packages the platform distributes are intact: what was checked, when, with which key, and what failed."
      app={{ screen: "Content Releases (content staff) · no Super Admin screen", route: "/admin/releases", action: "GET /catalog/releases/{id}/verify", enforced: ["Recomputes the manifest hash and checks the signature against the platform key", "Read-only: it changes nothing and records no audit event"] }}
      availability={{ level: "partial", note: "Release, ledger, evidence and package verifications exist. Only the release check works without an organisation; no verification history is stored. The live check below is real; the history is sample data." }}
      empty={{ title: "No verifications yet", description: "Checks appear here once content releases are published and verified." }}>
      <Body />
    </DemoFrame>
  );
}

type Live = { state: "idle" | "loading" | "ok" | "none" | "error"; error?: unknown; release?: { id: string; label: string; published: string }; v?: { manifest_hash_ok: boolean; signature_ok: boolean; signer_key_id: string } };

function Body() {
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState("c4");
  const [live, setLive] = useState<Live>({ state: "idle" });
  const rows = HISTORY.filter((h) => filter === "ALL" || h.result === filter);
  const failed = HISTORY.filter((h) => h.result === "FAILED");
  const current = HISTORY.find((h) => h.id === selected);
  const last = HISTORY.reduce((a, h) => (h.at > a ? h.at : a), "");

  const runLive = async () => {
    setLive({ state: "loading" });
    try {
      const releases = await api<{ content_release_id: string; label: string; published_at: string }[]>("/catalog/releases");
      if (!releases.length) { setLive({ state: "none" }); return; }
      const r = releases[0];
      const v = await api<{ manifest_hash_ok: boolean; signature_ok: boolean; signer_key_id: string }>(`/catalog/releases/${encodeURIComponent(r.content_release_id)}/verify`);
      setLive({ state: "ok", release: { id: r.content_release_id, label: r.label, published: r.published_at }, v });
    } catch (error) { setLive({ state: "error", error }); }
  };

  const columns: DataColumn<Check>[] = [
    { key: "source", header: "Source", sortValue: (h) => h.source, render: (h) => <><span className="block font-medium text-fg">{h.source}</span><span className="type-caption text-fg-muted">{h.kind}</span></> },
    { key: "result", header: "Result", sortValue: (h) => h.result, render: (h) => <StateBadge code={h.result} size="sm" /> },
    { key: "at", header: "Verified at", sortValue: (h) => h.at, render: (h) => <span className="whitespace-nowrap tabular-nums text-fg-secondary">{fmtDate(h.at, true)}</span> },
    { key: "open", header: <span className="sr-only">Trace</span>, align: "right", render: (h) => (
      <Button size="sm" variant={h.id === selected ? "secondary" : "ghost"} aria-pressed={h.id === selected} onClick={() => setSelected(h.id)}>Trace<span className="sr-only"> for {h.source}</span></Button>
    ) },
  ];

  return (
    <div className="space-y-stack">
      <Card title="Live check" description="Runs the real release verification on this deployment's most recent content release"
        actions={<Button size="sm" variant="primary" icon={ShieldCheck} loading={live.state === "loading"} onClick={runLive}>{live.state === "ok" ? "Check again" : "Run live check"}</Button>}>
        {live.state === "idle" && <p className="type-secondary text-fg-secondary">Nothing has been checked yet. The check is read-only: it changes nothing and records no audit event.</p>}
        {live.state === "loading" && <Spinner showLabel label="Verifying the latest content release…" />}
        {live.state === "none" && <EmptyState title="No content releases on this deployment" description="Nothing to verify yet." compact />}
        {live.state === "error" && <ErrorState error={live.error} title="The live check could not run" onRetry={runLive} compact />}
        {live.state === "ok" && live.v && live.release && (
          <div className="space-y-3" role="status">
            <div className="flex flex-wrap items-center gap-2"><LiveTag /><StateBadge code={live.v.manifest_hash_ok && live.v.signature_ok ? "VERIFIED" : "FAILED"} /></div>
            <DescriptionList items={[
              ["Release", <span key="r">{live.release.label} <code className="type-code text-fg-muted">{live.release.id}</code></span>], ["Published", fmtDate(live.release.published, true)],
              ["Manifest hash", live.v.manifest_hash_ok ? "Matches" : "Does not match"], ["Signature", live.v.signature_ok ? "Verifies" : "Does not verify"],
              ["Signer key", <code key="k" className="type-code">{live.v.signer_key_id}</code>],
            ]} />
            <p className="type-caption text-fg-muted">Live result, not sample data. Proves the release is unchanged and signed by the platform key; it says nothing about the content&apos;s legal accuracy.</p>
          </div>
        )}
      </Card>

      {failed.length > 0 && (
        <CriticalPanel title={`${failed.length} verification failed (sample)`}>
          {failed.map((f) => `${f.source}: ${f.detail}`).join(" · ")}. A release that fails its check must not be distributed; content operations re-sign or withdraw it.
        </CriticalPanel>
      )}

      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Tabs id="sa-integrity-filter" label="Filter verification results" variant="pills" value={filter} onChange={setFilter}
              items={["ALL", "VERIFIED", "FAILED"].map((v) => ({ value: v, label: v === "ALL" ? "All" : v, count: HISTORY.filter((h) => v === "ALL" || h.result === v).length }))} />
            <span className="type-caption text-fg-muted">Last verification {fmtDate(last, true)}</span>
          </div>
          <TabPanel id="sa-integrity-filter" value={filter} className="">
            <DataTable caption="Verification history (sample data)" captionVisible columns={columns} rows={rows} rowKey={(h) => h.id} defaultSort={{ key: "at", dir: "desc" }} minWidth="34rem"
              empty={{ title: "No results with this outcome" }} />
          </TabPanel>
        </div>
        <Card title="Audit trace" description={current?.source} bodyClassName="p-card space-y-4">
          {current ? <>
            <DescriptionList columns={1} items={[["Result", <StateBadge key="r" code={current.result} size="sm" />], ["Detail", current.detail], ["Verifier", current.verifier], ["Key", <code key="k" className="type-code">{current.key}</code>], ["Checked", fmtDate(current.at, true)]]} />
            <Section title="Steps">
              <Timeline label={`Verification steps for ${current.source}`} items={[
                { id: "1", title: "Manifest read and canonicalised", status: "verified" },
                { id: "2", title: "SHA-256 recomputed and compared", status: "verified" },
                { id: "3", title: current.result === "FAILED" ? "Signature did not verify" : "Signature verified", status: current.result === "FAILED" ? "critical" : "verified", meta: `Key ${current.key}` },
                { id: "4", title: `Result ${current.result}`, meta: fmtDate(current.at, true), status: current.result === "FAILED" ? "critical" : "verified" },
              ]} />
            </Section>
            <p className="type-caption text-fg-muted">Sample trace<Sample />. Real release checks record no audit event today, so this shows what a recorded trace could contain.</p>
          </> : <p className="type-secondary text-fg-muted">Choose a verification.</p>}
        </Card>
      </div>
    </div>
  );
}
