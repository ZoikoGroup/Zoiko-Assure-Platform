"use client";

import { useEffect, useMemo, useState } from "react";
import { PackageCheck, ShieldCheck } from "lucide-react";
import {
  Alert, Button, Card, ConfirmDialog, DataTable, DescriptionList, Field, KpiCard, ProgressBar, Select, StateBadge, Switch, TBody, THead, Table, Td, Th, Tr,
  useToast, type DataColumn,
} from "@/components/ds";
import { fmtDate, fmtRange, short } from "@/lib/format";
import { DemoFrame, Sample, Section, delay, now, sha256, useSimulatedProgress, useYou } from "../../_shared/demo";
import { ENTITIES, EVIDENCE, PACKAGES, entityName, type Obligation, type PackageRec } from "./data";
import { PERIODS, inForce } from "./Overview";
import { FlowLink, Proposed } from "./shared";

/* Assurance reporting, mirroring Assurance Periods (app/assurance → GET /obligations) and the Audit Package Builder
   (app/packages → POST /packages, GET /packages, POST /packages/{id}/verify). Facts surfaced rather than hidden: the
   server builds a package for one entity from each chosen obligation's LATEST frozen snapshot, whatever its period;
   it refuses (409) when an obligation has no snapshot, there is no checkpoint or the ledger does not verify; it signs
   the package (SIGNED) at the current record watermark. Verification checks integrity and structure only, never legal
   compliance. Evidence-completeness figures and pre-build warnings are proposals. */

type Warning = { tone: "critical" | "warning" | "info"; text: string };

export function Reporting() {
  return (
    <DemoFrame id="ow-reporting" plane="workspace" title="Assurance reporting"
      intro="Assemble what an auditor receives for a period: the frozen assessment of each obligation, the evidence behind it, what is missing, and a signed package anyone can verify."
      app={{ screen: "Assurance Periods · Audit Package Builder", route: "/assurance", action: "GET /obligations · POST /packages · POST /packages/{id}/verify", permission: "package",
        enforced: ["Refused (409) when an obligation has no snapshot, there is no checkpoint, or the ledger does not verify", "Uses each obligation's latest frozen snapshot, whatever its period", "Record cutoff is the current watermark at build time", "Verification checks integrity and structure only; it never asserts legal compliance"] }}
      availability={{ level: "partial", note: "Assurance Periods lists each obligation's latest snapshot; the Package Builder builds, lists, downloads and verifies signed packages. Choosing a period here, evidence-completeness figures and pre-build warnings are proposals." }}
      empty={{ title: "No snapshots to report", description: "Freeze a period snapshot for an obligation before building a package." }}>
      <Body />
    </DemoFrame>
  );
}

function Body() {
  const toast = useToast();
  const you = useYou();
  const progress = useSimulatedProgress();
  const [periodId, setPeriodId] = useState(PERIODS[0].value);
  const [entity, setEntity] = useState(ENTITIES[1].id);
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  const [packages, setPackages] = useState<PackageRec[]>(PACKAGES);
  const [confirming, setConfirming] = useState(false);
  const [failBuild, setFailBuild] = useState(false);
  const [verified, setVerified] = useState<Record<string, "busy" | "VERIFIED">>({});
  const period = PERIODS.find((p) => p.value === periodId)!;

  const rows = useMemo(() => inForce(period.range).filter((o) => o.entity === entity && o.lifecycle === "ADOPTED"), [period, entity]);
  const selected = chosen ?? new Set(rows.map((o) => o.key));
  const scope = rows.filter((o) => selected.has(o.key));
  const forPeriod = scope.filter((o) => o.period?.[0] === period.range[0]);
  const linked = EVIDENCE.filter((e) => e.obligations.some((k) => selected.has(k)));
  const current = linked.filter((e) => e.condition === "CURRENT");

  const warnings: Warning[] = [
    ...scope.filter((o) => !o.state).map((o) => ({ tone: "critical" as const, text: `${o.key} has no frozen snapshot: the build is refused (409) until one is created.` })),
    ...scope.filter((o) => o.state && o.period?.[0] !== period.range[0]).map((o) => ({ tone: "warning" as const, text: `${o.key}: the latest snapshot covers ${fmtRange(o.period)}, not ${period.label}. The package uses it as it is.` })),
    ...linked.filter((e) => e.condition !== "CURRENT").map((e) => ({ tone: "warning" as const, text: `${e.id} (${e.title}) is ${e.condition}; it is packaged with that condition.` })),
    ...scope.filter((o) => o.state === "FAILING" || o.state === "NOT_PROVEN" || o.state === "REQUIRES_REVIEW").map((o) => ({ tone: "info" as const, text: `${o.key} is ${o.state}. A package reports states as recorded and never changes them.` })),
  ];

  // The build finishes when the simulated progress does.
  useEffect(() => {
    if (progress.outcome !== "ok") return;
    let live = true;
    (async () => {
      const manifest = await sha256(JSON.stringify({ entity, period: period.range, obligations: scope.map((o) => o.key), at: now() }));
      if (!live) return;
      const id = `pkg-${String(13 + packages.length - PACKAGES.length).padStart(4, "0")}`;
      setPackages((ps) => [{ id, entity, period: period.range, cutoff: 4170, obligations: scope.map((o) => o.key), manifest, signer: "za-sign-2026-01", created: now(), createdBy: `${you.name} (you)` }, ...ps]);
      toast(`${id} built and SIGNED (simulated: no package was created)`, "good");
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.outcome]);

  const build = async () => {
    await delay(500);
    const missing = scope.filter((o) => !o.state);
    if (!scope.length) throw new Error("Choose at least one obligation to package.");
    if (missing.length) throw new Error(`Refused (409): No frozen assurance snapshot for obligation ${missing[0].key}; create one first. (Simulated response; the server was not called.)`);
    progress.start(failBuild ? 70 : undefined, 9, 140);
  };
  const verify = async (p: PackageRec) => {
    setVerified((v) => ({ ...v, [p.id]: "busy" }));
    await delay(1000);
    setVerified((v) => ({ ...v, [p.id]: "VERIFIED" }));
    toast(`${p.id}: signature, manifest and contents VERIFIED (simulated)`, "good");
  };

  const columns: DataColumn<Obligation>[] = [
    { key: "title", header: "Obligation", sortValue: (o) => o.title, render: (o) => <><span className="block font-medium text-fg">{o.title}</span><code className="type-code text-fg-muted">{o.key}</code></> },
    { key: "state", header: "Latest snapshot", sortValue: (o) => o.state ?? "", render: (o) => o.state ? <StateBadge code={o.state} size="sm" /> : <span className="type-code text-fg-muted">NO SNAPSHOT</span> },
    { key: "period", header: "Period (end exclusive)", sortValue: (o) => o.period?.[0] ?? "", render: (o) => o.period ? <span className="whitespace-nowrap tabular-nums">{fmtRange(o.period)}{o.period[0] !== period.range[0] && <span className="ml-1.5 type-caption text-status-warning">other period</span>}</span> : "—" },
    { key: "perspective", header: "Perspective", render: (o) => o.perspective ? <code className="type-code">{o.perspective}</code> : "—" },
    { key: "cutoff", header: "Record cutoff", align: "right", sortValue: (o) => o.cutoff ?? 0, render: (o) => o.cutoff ? <code className="type-code">#{o.cutoff}</code> : "—" },
    { key: "exceptions", header: "Exceptions", align: "right", sortValue: (o) => o.exceptions, render: (o) => o.exceptions ? `${o.exceptions} APPROVED` : "—" },
    { key: "frozen", header: "Snapshot frozen", sortValue: (o) => o.frozen ?? "", render: (o) => <span className="whitespace-nowrap">{o.frozen ? fmtDate(o.frozen, true) : "—"}</span> },
  ];

  return (
    <div className="space-y-stack">
      <Card title="Reporting scope" description="Packages are built for one legal entity" actions={<FlowLink flow="package">Full Assurance package flow</FlowLink>}>
        <div className="grid gap-form sm:grid-cols-2 lg:max-w-2xl">
          <Field label={<>Assurance period<Proposed /></>} hint={`${fmtRange(period.range)}, end exclusive.`}>
            <Select value={periodId} onChange={(e) => { setPeriodId(e.target.value); setChosen(null); progress.reset(); }} options={PERIODS.map((p) => ({ value: p.value, label: p.label }))} />
          </Field>
          <Field label="Legal entity"><Select value={entity} onChange={(e) => { setEntity(e.target.value); setChosen(null); progress.reset(); }} options={ENTITIES.map((e) => ({ value: e.id, label: e.name }))} /></Field>
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Obligations in scope" value={scope.length} hint={`of ${rows.length} in force`} />
        <KpiCard label={`Snapshots for ${period.label}`} value={`${forPeriod.length}/${scope.length}`} tone={forPeriod.length === scope.length && scope.length ? "verified" : "warning"} hint="others use their latest" />
        <KpiCard label="Evidence CURRENT" value={linked.length ? `${Math.round((current.length / linked.length) * 100)}%` : "—"} tone={current.length === linked.length ? "verified" : "warning"} hint={`${current.length} of ${linked.length} linked`} />
        <KpiCard label="Validation warnings" value={warnings.length} tone={warnings.some((w) => w.tone === "critical") ? "critical" : warnings.length ? "warning" : "verified"} hint={warnings.some((w) => w.tone === "critical") ? "build would be refused" : "build can proceed"} />
      </div>

      <DataTable caption={`Obligations for ${entityName(entity)} in ${period.label} (sample): choose what to package`} columns={columns} rows={rows} rowKey={(o) => o.key} minWidth="60rem"
        selectable selected={selected} onSelectedChange={(s) => setChosen(new Set(s))} rowLabel={(o) => `${o.key} for the package`} defaultSort={{ key: "title", dir: "asc" }}
        empty={{ title: `No obligations in force for ${entityName(entity)} in ${period.label}`, description: "Choose another period or entity." }} />

      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Evidence completeness" description={<>Evidence linked to the chosen obligations<Proposed /></>}>
          <div className="space-y-4">
            <ProgressBar label="Linked evidence that is CURRENT" tone={current.length === linked.length ? "verified" : "accent"} value={linked.length ? (current.length / linked.length) * 100 : 0} detail={`${current.length} of ${linked.length}`} />
            <ProgressBar label={`Obligations with a snapshot for ${period.label}`} value={scope.length ? (forPeriod.length / scope.length) * 100 : 0} detail={`${forPeriod.length} of ${scope.length}`} />
            <p className="type-caption text-fg-muted">Completeness describes what the package will contain, not whether the obligations are met.</p>
          </div>
        </Card>
        <Card title="Validation warnings" description={<>Checked before building<Proposed /></>}>
          {warnings.length ? <ul className="space-y-2">{warnings.map((w) => <li key={w.text}><Alert tone={w.tone}>{w.text}</Alert></li>)}</ul>
            : <Alert tone="verified">No warnings: every chosen obligation has a snapshot for this period and its evidence is CURRENT.</Alert>}
        </Card>
      </div>

      <Card title="Generate assurance package" description={`${entityName(entity)} · ${period.label} · ${scope.length} obligation${scope.length === 1 ? "" : "s"}`}>
        <div className="space-y-4">
          <Switch checked={failBuild} onChange={setFailBuild} label="Demonstrate a refused build" description="The ledger fails verification during the build, as the server would report it." />
          {progress.value != null && (
            <ProgressBar label={progress.outcome === "failed" ? "Build refused" : progress.outcome === "ok" ? "Signed" : progress.value < 40 ? "Collecting snapshots and evaluations…" : progress.value < 75 ? "Collecting evidence and IPE…" : "Writing the manifest and signing…"}
              value={progress.value} tone={progress.outcome === "failed" ? "critical" : progress.outcome === "ok" ? "verified" : "accent"} />
          )}
          {progress.outcome === "failed" && <Alert tone="critical" live="assertive" title="Refused (409)">The ledger does not verify, so no package was built. Simulated; the server was not called.</Alert>}
          {progress.outcome === "ok" && <Alert tone="verified" live="polite" title="Package SIGNED">Added to the list below. Simulated; no package exists.</Alert>}
          <Button variant="primary" icon={PackageCheck} loading={progress.value != null && progress.outcome == null} onClick={() => setConfirming(true)}>Generate package</Button>
        </div>
      </Card>

      <Section title="Assurance packages">
        <Table caption="Signed assurance packages (sample)" minWidth="52rem">
          <THead><Tr><Th>Package</Th><Th>Entity · period</Th><Th align="right">Cutoff</Th><Th>Status</Th><Th>Manifest</Th><Th align="right"><span className="sr-only">Verify</span></Th></Tr></THead>
          <TBody>{packages.map((p) => (
            <Tr key={p.id}>
              <Td><code className="block type-code">{p.id}</code><span className="type-caption text-fg-muted">{p.createdBy} · {fmtDate(p.created, true)}</span></Td>
              <Td>{entityName(p.entity)}<span className="block type-caption text-fg-muted">{fmtRange(p.period)} · {p.obligations.length} obligations</span></Td>
              <Td align="right"><code className="type-code">#{p.cutoff}</code></Td>
              <Td><span className="flex flex-wrap gap-1"><StateBadge code="SIGNED" size="sm" />{verified[p.id] === "VERIFIED" && <StateBadge code="VERIFIED" size="sm" />}</span></Td>
              <Td><code className="type-code text-fg-muted" title={p.manifest}>{short(p.manifest, 12)}</code></Td>
              <Td align="right"><Button size="sm" variant="ghost" icon={ShieldCheck} loading={verified[p.id] === "busy"} onClick={() => verify(p)}>Verify<span className="sr-only"> {p.id}</span></Button></Td>
            </Tr>
          ))}</TBody>
        </Table>
        <p className="type-caption text-fg-muted">Verification checks the signature, manifest and contents: integrity and structure only, never legal compliance. Sample packages<Sample />.</p>
      </Section>

      {confirming && (
        <ConfirmDialog title="Generate this package?" confirmLabel="Generate package" busyLabel="Checking…" onClose={() => setConfirming(false)} onConfirm={build}>
          <DescriptionList columns={1} items={[["Entity", entityName(entity)], ["Period", `${fmtRange(period.range)} (end exclusive)`], ["Obligations", scope.map((o) => o.key).join(", ") || "None chosen"],
            ["Snapshots used", "Each obligation's latest frozen snapshot"], ["Record cutoff", "The current watermark at build time"]]} />
          {warnings.length > 0 && <p>{warnings.length} validation warning{warnings.length === 1 ? "" : "s"} apply; see the list.</p>}
          <p>Simulated: no package is built or signed and the server is not called.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
