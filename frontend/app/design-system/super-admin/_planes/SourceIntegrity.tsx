"use client";

import { useId, useState } from "react";
import { ShieldCheck } from "lucide-react";
import {
  Button, Card, CriticalPanel, DataTable, DescriptionList, StateBadge, TabPanel, Tabs, Timeline, type DataColumn,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";

/* Source integrity. Real checks exist (content release, ledger, evidence, package), but only the content release check
   is reachable without an organisation, and it stores no history and writes no audit event. This proposal does not run
   it: GET /catalog/releases and /catalog/releases/{id}/verify need catalogue read access, which the Zoiko Super Admin
   does not hold, so the server would refuse them and record each refusal. The live check is shown disabled with that
   reason; the history table is sample data showing what a recorded history could be. Tenant ledger and evidence checks
   belong to the customer plane and are deliberately absent. */

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
      availability={{ level: "partial", note: "Release, ledger, evidence and package verifications exist. Only the release check works without an organisation, and it is not served to the Super Admin; no verification history is stored. The live check is disabled here; the history is sample data." }}
      empty={{ title: "No verifications yet", description: "Checks appear here once content releases are published and verified." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const [filter, setFilter] = useState("ALL");
  const [selected, setSelected] = useState("c4");
  const liveHint = useId();
  const rows = HISTORY.filter((h) => filter === "ALL" || h.result === filter);
  const failed = HISTORY.filter((h) => h.result === "FAILED");
  const current = HISTORY.find((h) => h.id === selected);
  const last = HISTORY.reduce((a, h) => (h.at > a ? h.at : a), "");

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
      <Card title="Live check" description="Release verification on this deployment's most recent content release"
        actions={<Button size="sm" variant="primary" icon={ShieldCheck} disabled aria-describedby={liveHint}>Run live check</Button>}>
        <p id={liveHint} className="type-secondary text-fg-secondary">
          Not available to the Super Admin. Release verification (GET /catalog/releases/{"{id}"}/verify) needs catalogue read access, which this role does
          not hold, so the server would refuse it and record the refusal. This design proposal therefore does not run it; the history below is sample data.
        </p>
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
