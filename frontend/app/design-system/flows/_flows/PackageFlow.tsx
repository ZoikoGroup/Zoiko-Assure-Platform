"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, PackageCheck, RotateCcw } from "lucide-react";
import {
  Alert, Button, Callout, Card, Checkbox, ConfirmDialog, DescriptionList, Field, Fieldset, ProgressBar, Select, StateBadge, Stepper, Switch,
  TextInput, Timeline, statusOfCode, useToast,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { FlowFrame, Sample, Section, sha256, useSimulatedProgress } from "../../_shared/demo";

/* Assurance package, mirroring the Audit Package Builder (frontend/app/packages/page.tsx → POST /packages) and the
   assurance period model. Facts the demo surfaces rather than hides: the server builds from each obligation's LATEST
   frozen snapshot whatever its period, refuses (409) when an obligation has no snapshot, and signs the package (SIGNED).
   Verification is integrity and structure only; it never asserts legal compliance. */

type Obl = { key: string; title: string; state: string | null; period?: [string, string]; cutoff?: number; current: number; total: number; reasons: string[] };
const OBLIGATIONS: Obl[] = [
  { key: "DORA.28.3.REGISTER", title: "ICT third-party register", state: "PROVEN", period: ["2026-07-01", "2026-10-01"], cutoff: 4120, current: 12, total: 12, reasons: [] },
  { key: "NIS2.23.REPORT", title: "Major incident reporting", state: "NOT_PROVEN", period: ["2026-07-01", "2026-10-01"], cutoff: 4120, current: 5, total: 7, reasons: ["STALE_EVIDENCE"] },
  { key: "SOX.ITGC.AR", title: "Privileged-access review", state: "REQUIRES_REVIEW", period: ["2026-04-01", "2026-07-01"], cutoff: 3980, current: 8, total: 9, reasons: ["IPE_INCONCLUSIVE"] },
  { key: "GDPR.30.ROPA", title: "Records of processing", state: null, current: 0, total: 0, reasons: [] },
];
const STEPS = [{ id: "scope", label: "Scope" }, { id: "validate", label: "Validate" }, { id: "generate", label: "Generate" }, { id: "result", label: "Result" }];
const STAGES = ["Collect latest snapshots", "Verify the evidence ledger", "Confirm the latest checkpoint", "Assemble manifest and payloads", "Sign the package"];

export function PackageFlow() {
  return (
    <FlowFrame id="package" title="Assurance package"
      intro="Choose an entity, a reporting period and obligations, check what the package will contain and where it falls short, then generate a signed package an auditor can verify offline."
      app={{ screen: "Audit Package Builder", route: "/packages", action: "POST /packages", permission: "package",
        enforced: ["Refused (409) when an obligation has no snapshot, there is no checkpoint, or the ledger does not verify", "Uses each obligation's latest frozen snapshot, whatever its period", "Record cutoff is the current watermark at build time"] }}
      empty={{ title: "No obligations to package", description: "Adopt determinations and freeze period snapshots before building a package." }}>
      <Build />
    </FlowFrame>
  );
}

function Build() {
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [entity, setEntity] = useState("nw-nl");
  const [from, setFrom] = useState("2026-07-01");
  const [to, setTo] = useState("2026-10-01");
  const [chosen, setChosen] = useState<string[]>(OBLIGATIONS.map((o) => o.key));
  const [payloads, setPayloads] = useState(true);
  const [failLedger, setFailLedger] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [digest, setDigest] = useState<string>();
  const progress = useSimulatedProgress();

  const periodError = from && to && to <= from ? "The period end must be after its start." : undefined;
  const sel = OBLIGATIONS.filter((o) => chosen.includes(o.key));
  const missing = sel.filter((o) => !o.state);
  const offPeriod = sel.filter((o) => o.period && (o.period[0] !== from || o.period[1] !== to));
  const weak = sel.filter((o) => o.state && o.state !== "PROVEN");
  const withReasons = sel.filter((o) => o.reasons.length);
  const current = sel.reduce((n, o) => n + o.current, 0), total = sel.reduce((n, o) => n + o.total, 0);
  const counts = sel.reduce<Record<string, number>>((m, o) => ({ ...m, [o.state ?? "NOT ASSESSED"]: (m[o.state ?? "NOT ASSESSED"] ?? 0) + 1 }), {});
  const stage = progress.value === null ? -1 : Math.min(STAGES.length - 1, Math.floor((progress.value / 100) * STAGES.length));

  useEffect(() => {
    if (progress.outcome !== "ok") return;
    sha256(JSON.stringify({ entity, period: [from, to], obligations: chosen, payloads })).then(setDigest);
    toast("Package signed (demo: nothing was built)", "good");
    setStep(3);
  }, [progress.outcome]); // eslint-disable-line react-hooks/exhaustive-deps

  const generate = async () => { setStep(2); progress.start(failLedger ? 32 : undefined, 5, 140); };

  return (
    <Card bodyClassName="p-card space-y-6">
      <Stepper steps={STEPS} current={step} label="Package progress" onStepClick={step < 2 ? setStep : undefined} />

      {step === 0 && (
        <div className="grid gap-stack lg:grid-cols-2">
          <Section title="Reporting period">
            <div className="grid gap-form sm:grid-cols-2">
              <Field label="Legal entity" className="sm:col-span-2"><Select value={entity} onChange={(e) => setEntity(e.target.value)} options={[{ value: "nw-nl", label: "Northwind B.V. (NL)" }, { value: "nw-gb", label: "Northwind Ltd (GB)" }]} /></Field>
              <Field label="Period from" required><TextInput type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
              <Field label="Period to (exclusive)" required error={periodError} hint="Half-open: the end date is not included."><TextInput type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
            </div>
            <Switch label="Include decrypted evidence payloads" description="Without payloads, offline verification reports PAYLOAD_NOT_INCLUDED." checked={payloads} onChange={setPayloads} />
          </Section>
          <Section title="Obligations and their latest snapshot">
            <Fieldset legend="Include in the package">
              {OBLIGATIONS.map((o) => (
                <Checkbox key={o.key} checked={chosen.includes(o.key)} onChange={(e) => setChosen((c) => (e.target.checked ? [...c, o.key] : c.filter((k) => k !== o.key)))}
                  label={<span className="inline-flex flex-wrap items-center gap-2">{o.title} {o.state ? <StateBadge code={o.state} size="sm" /> : <StateBadge code="NOT ASSESSED" status="unknown" size="sm" />}</span>}
                  description={o.period ? `${o.key} · snapshot ${fmtDate(o.period[0])} → ${fmtDate(o.period[1])} · record cutoff #${o.cutoff}` : `${o.key} · no frozen snapshot`} />
              ))}
            </Fieldset>
            <p className="type-caption text-fg-muted">Obligations and snapshots are samples<Sample /></p>
          </Section>
        </div>
      )}

      {step === 1 && (
        <div className="grid gap-stack lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Section title="Validation">
            <div className="space-y-3">
              {sel.length === 0 && <Alert tone="critical" title="Nothing selected">Choose at least one obligation.</Alert>}
              {missing.map((o) => <Alert key={o.key} tone="critical" title={`${o.key} has no frozen snapshot`}>The server refuses to build (409). Freeze a period snapshot in its dossier, or leave it out.</Alert>)}
              {offPeriod.map((o) => <Alert key={o.key} tone="warning" title={`${o.key} uses a snapshot from another period`}>The package uses its latest snapshot ({fmtDate(o.period![0])} → {fmtDate(o.period![1])}), not {fmtDate(from)} → {fmtDate(to)}.</Alert>)}
              {weak.length > 0 && <Alert tone="warning" title={`${weak.length} obligation${weak.length === 1 ? "" : "s"} not PROVEN`}>{weak.map((o) => `${o.key} (${o.state})`).join(", ")}. The package reports these states as recorded; it never hides or upgrades them.</Alert>}
              {withReasons.length > 0 && <Alert tone="info" title="Snapshot reason codes">{withReasons.map((o) => `${o.key}: ${o.reasons.join(", ")}`).join(" · ")}</Alert>}
              <Alert tone="info" title="Record cutoff">Set by the server to the current watermark when the package is built.</Alert>
            </div>
          </Section>
          <div className="space-y-stack">
            <Section title="Assurance status">
              <ul className="flex flex-wrap gap-2">{Object.entries(counts).map(([s, n]) => <li key={s}><StateBadge code={`${s} · ${n}`} status={s === "NOT ASSESSED" ? "unknown" : statusOfCode(s)} size="sm" /></li>)}</ul>
            </Section>
            <Section title="Evidence completeness">
              <ProgressBar value={total ? (current / total) * 100 : 0} label="CURRENT evidence" tone={total && current === total ? "verified" : "accent"} detail={`${current} of ${total} items in the selected snapshots are CURRENT.`} />
            </Section>
            <Switch label="Simulate a ledger failure" description="The server refuses (409): the evidence ledger does not verify." checked={failLedger} onChange={setFailLedger} />
          </div>
        </div>
      )}

      {step === 2 && progress.value !== null && (
        <div className="space-y-4" aria-live="polite">
          <ProgressBar value={progress.value} label={progress.outcome === "failed" ? "Package not built" : "Generating package (simulated)"} tone={progress.outcome === "failed" ? "critical" : "accent"} />
          <Timeline label="Generation stages" items={STAGES.map((s, i) => ({
            id: s, title: s,
            status: progress.outcome === "failed" && i === stage ? "critical" : i < stage || progress.outcome === "ok" ? "verified" : i === stage ? "info" : undefined,
            meta: progress.outcome === "failed" && i === stage ? "Failed" : i < stage ? "Done" : i === stage && !progress.outcome ? "In progress" : undefined,
          }))} />
          {progress.outcome === "failed" && (
            <Alert tone="critical" live="assertive" title="The server refused to build the package (HTTP 409)">The evidence ledger did not verify, so nothing was signed. Investigate the ledger before trying again. (Demonstration.)</Alert>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="grid gap-stack lg:grid-cols-2">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2"><StateBadge code="SIGNED" status="verified" /><span className="type-secondary text-fg-secondary">PKG-DEMO-0007<Sample /></span></div>
            <DescriptionList columns={1} items={[
              ["Entity · period", `${entity === "nw-nl" ? "Northwind B.V. (NL)" : "Northwind Ltd (GB)"} · ${fmtDate(from)} → ${fmtDate(to)} (exclusive)`],
              ["Obligations", sel.map((o) => o.key).join(", ")], ["Payloads", payloads ? "Included" : "Not included"],
              ["Manifest SHA-256", <code key="d" className="break-all type-code">{digest ?? "computing…"}</code>],
            ]} />
            <p className="type-caption text-fg-muted">This digest is of the demonstration's selection, computed in your browser. A real package's manifest is signed by the server.</p>
          </div>
          <div className="space-y-3">
            <Callout tone="info" title="Verification">
              An auditor can verify the package offline with <code className="type-code">backend/verifier/verify_package.py</code> or the public upload check. The result is PASS,
              PASS_WITH_WARNINGS or FAIL; HISTORICAL_HMAC_NOT_EXTERNALLY_RECOMPUTED is always listed{payloads ? "" : ", and PAYLOAD_NOT_INCLUDED here"}.
            </Callout>
            <Callout tone="warning" title="What the package does not claim">Integrity/structural verification only; this package does not assert legal compliance.</Callout>
          </div>
        </div>
      )}

      <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-4">
        {step === 1 ? <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(0)}>Back</Button> : <span />}
        {step === 0 && <Button variant="primary" iconRight={ArrowRight} disabled={!!periodError || !from || !to} onClick={() => setStep(1)}>Validate</Button>}
        {step === 1 && <Button variant="primary" icon={PackageCheck} disabled={sel.length === 0 || missing.length > 0} onClick={() => setConfirming(true)}>Generate package</Button>}
        {step === 2 && progress.outcome === "failed" && (
          <span className="flex gap-2"><Button variant="ghost" icon={ArrowLeft} onClick={() => { progress.reset(); setStep(1); }}>Back to validation</Button>
            <Button variant="secondary" icon={RotateCcw} onClick={() => { setFailLedger(false); progress.start(undefined, 5, 140); }}>Try again</Button></span>
        )}
        {step === 3 && <Button variant="secondary" icon={RotateCcw} onClick={() => { progress.reset(); setDigest(undefined); setStep(0); }}>Build another</Button>}
      </div>

      {confirming && (
        <ConfirmDialog title="Generate and sign this package?" confirmLabel="Generate package" busyLabel="Starting…" onClose={() => setConfirming(false)} onConfirm={generate}>
          <p>{sel.length} obligation{sel.length === 1 ? "" : "s"}, {fmtDate(from)} → {fmtDate(to)} (exclusive), payloads {payloads ? "included" : "not included"}.</p>
          {(offPeriod.length > 0 || weak.length > 0) && <p>The validation warnings stay visible in the package; it reports states exactly as recorded.</p>}
        </ConfirmDialog>
      )}
    </Card>
  );
}
