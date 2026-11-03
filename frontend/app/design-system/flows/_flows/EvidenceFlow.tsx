"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, FileText, RotateCcw, Send, X } from "lucide-react";
import {
  Alert, Button, Callout, Card, ConfirmDialog, DescriptionList, Field, FileDropzone, IconButton, ProgressBar, Select, StateBadge, Stepper,
  Switch, TextInput, Timeline, useToast,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { FlowFrame, Sample, Section, sha256, useSimulatedProgress, useYou } from "../../_shared/demo";

/* Evidence submission, mirroring the Evidence Records upload (frontend/app/evidence/page.tsx → POST /evidence/upload):
   the same fields, the 25 MB limit and the four classifications. The server's own provenance, encryption, digests and
   ledger chaining are described, not performed. */

const MAX_BYTES = 25 * 1024 * 1024;
const CLASSIFICATIONS = ["CUSTOMER_INTERNAL", "CUSTOMER_CONFIDENTIAL", "RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"];
const ENTITIES = [{ value: "nw-nl", label: "Northwind B.V. (NL)" }, { value: "nw-gb", label: "Northwind Ltd (GB)" }];
const STEPS = [{ id: "file", label: "File" }, { id: "details", label: "Details" }, { id: "review", label: "Review & submit" }, { id: "result", label: "Result" }];
const STAGES: [number, string][] = [[0, "Reading the file"], [25, "Encrypting"], [55, "Recording keyed HMAC and ciphertext digest"], [80, "Chaining the ledger event"]];
const size = (b: number) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
const addDays = (d: string, n: number) => { const t = new Date(`${d}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };

export function EvidenceFlow() {
  return (
    <FlowFrame id="evidence" title="Evidence submission"
      intro="Upload an artifact, classify it, describe what it covers, check it, then confirm. The server encrypts it, records its digests and chains it into the evidence ledger; it can later be superseded or invalidated with a reason, never edited."
      app={{ screen: "Evidence Records › Upload", route: "/evidence", action: "POST /evidence/upload (multipart)", permission: "submit_evidence",
        enforced: ["Files over 25 MB are refused (HTTP 413)", "A repeat of the same file with different details is refused (409 IDEMPOTENCY_CONFLICT)", "Origin, provenance class and review status are set by the server"] }}
      empty={{ title: "No evidence requests are waiting", description: "When a control needs an artifact, its request appears here." }}>
      <EvidenceSteps />
    </FlowFrame>
  );
}

function EvidenceSteps() {
  const toast = useToast();
  const you = useYou();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string>();
  const [digest, setDigest] = useState<string>();
  const [form, setForm] = useState({ title: "", entity: "", from: "", to: "", classification: "CUSTOMER_CONFIDENTIAL", validity: "90" });
  const [tried, setTried] = useState<Record<number, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [refuse, setRefuse] = useState(false);
  const progress = useSimulatedProgress();
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => { if (progress.outcome === "ok") toast("Evidence recorded (demo: nothing was stored)", "good"); }, [progress.outcome, toast]);

  const pick = async (files: File[]) => {
    const f = files[0];
    setDigest(undefined);
    setFileError(undefined);
    if (!f) return;
    if (f.size === 0) { setFile(null); setFileError("This file is empty. Choose the file that contains the evidence."); return; }
    if (f.size > MAX_BYTES) { setFile(null); setFileError(`This file is ${size(f.size)}. The limit is 25 MB; the server refuses larger uploads (HTTP 413).`); return; }
    setFile(f);
    try { setDigest(await sha256(await f.arrayBuffer())); } catch { setDigest("unavailable in this browser"); }
  };

  const errors = {
    title: !form.title.trim() ? "Enter a title." : form.title.trim().length > 300 ? "Keep the title to 300 characters or fewer." : undefined,
    entity: !form.entity ? "Choose the legal entity this evidence belongs to." : undefined,
    from: !form.from ? "Enter the date the evidence starts to cover." : undefined,
    to: !form.to ? "Enter the date the coverage ends." : form.from && form.to <= form.from ? "The coverage end must be after its start." : undefined,
    validity: !/^\d+$/.test(form.validity.trim()) ? "Enter a whole number of days." : undefined,
  };
  const show = (k: keyof typeof errors) => (tried[1] ? errors[k] : undefined);
  const validUntil = form.to && !errors.validity ? addDays(form.to, Number(form.validity)) : null;
  const condition = validUntil && validUntil < new Date().toISOString().slice(0, 10) ? "STALE" : "CURRENT";
  const entityLabel = ENTITIES.find((e) => e.value === form.entity)?.label;

  const next = () => {
    setTried((t) => ({ ...t, [step]: true }));
    if (step === 0 && !file) return;
    if (step === 1 && Object.values(errors).some(Boolean)) return;
    setStep(step + 1);
  };
  const submit = async () => { setStep(3); progress.start(refuse ? 63 : undefined); };

  const summary: [string, React.ReactNode][] = [
    ["File", file ? `${file.name} · ${size(file.size)}` : "—"], ["Title", form.title.trim()], ["Legal entity", entityLabel], ["Classification", <code key="c" className="type-code">{form.classification}</code>],
    ["Coverage", form.from && form.to ? `${fmtDate(form.from)} → ${fmtDate(form.to)}` : "—"], ["Valid until", validUntil ? `${fmtDate(validUntil)} (coverage end + ${form.validity} days)` : "—"],
  ];

  return (
    <Card bodyClassName="p-card space-y-6">
      <Stepper steps={STEPS} current={step} label="Evidence submission progress" onStepClick={step < 3 ? setStep : undefined} />

      {step === 0 && (
        <Section title="Choose the evidence file">
          <Field label="Evidence file" required error={fileError ?? (tried[0] && !file ? "Choose a file to continue." : undefined)}
            hint="Any format. The file is read in your browser only, to show its SHA-256; it is not uploaded.">
            <FileDropzone onFiles={pick} formats="Up to 25 MB" />
          </Field>
          {file && (
            <div className="flex items-start gap-3 rounded-card border border-line bg-surface-raised p-4">
              <FileText size={20} aria-hidden className="mt-0.5 shrink-0 text-accent" />
              <div className="min-w-0 flex-1">
                <p className="break-words type-secondary font-semibold text-fg">{file.name}</p>
                <p className="type-caption text-fg-muted">{size(file.size)} · {file.type || "unknown type"} · modified {fmtDate(new Date(file.lastModified).toISOString(), true)}</p>
                <p className="mt-1 break-all type-code text-fg-secondary">SHA-256 {digest ?? "computing…"}</p>
              </div>
              <IconButton label="Remove file" icon={X} size="sm" onClick={() => { setFile(null); setDigest(undefined); }} />
            </div>
          )}
        </Section>
      )}

      {step === 1 && (
        <Section title="Classify and describe">
          <div className="grid gap-form md:grid-cols-2">
            <Field label="Title" required error={show("title")} hint={`${form.title.trim().length} / 300 characters`} className="md:col-span-2">
              <TextInput value={form.title} onChange={(e) => set("title")(e.target.value)} placeholder="e.g. Q3 privileged-access review export" />
            </Field>
            <Field label="Legal entity" required error={show("entity")}>
              <Select value={form.entity} onChange={(e) => set("entity")(e.target.value)} placeholder="Choose an entity" options={ENTITIES} />
            </Field>
            <Field label="Classification" hint="Controls who may see the payload. Defaults to CUSTOMER_CONFIDENTIAL.">
              <Select value={form.classification} onChange={(e) => set("classification")(e.target.value)} options={CLASSIFICATIONS.map((c) => ({ value: c, label: c }))} />
            </Field>
            <Field label="Coverage from" required error={show("from")}><TextInput type="date" value={form.from} onChange={(e) => set("from")(e.target.value)} /></Field>
            <Field label="Coverage to" required error={show("to")} hint="Proposed check: the application does not compare these dates today.">
              <TextInput type="date" value={form.to} onChange={(e) => set("to")(e.target.value)} />
            </Field>
            <Field label="Validity after coverage (days)" error={show("validity")} hint="After this, the server reports the evidence as STALE.">
              <TextInput inputMode="numeric" value={form.validity} onChange={(e) => set("validity")(e.target.value)} />
            </Field>
          </div>
          <p className="type-caption text-fg-muted">Entities shown are samples<Sample /></p>
        </Section>
      )}

      {step === 2 && (
        <div className="grid gap-stack lg:grid-cols-2">
          <Section title="What you are submitting"><DescriptionList items={summary} /></Section>
          <Section title="Provenance the server will record">
            <DescriptionList columns={1} items={[
              ["Origin · method", <code key="o" className="type-code">HUMAN_UPLOAD · FILE_UPLOAD</code>],
              ["Provenance class", <code key="p" className="type-code">P4_MANUAL_UNATTESTED</code>],
              ["Source system", <code key="s" className="break-all type-code">upload:{file?.name}</code>],
              ["Submitted by", `${you.name} (${you.roles})`],
              ["Review status", <StateBadge key="r" code="PENDING_REVIEW" size="sm" />],
              ["Your copy's SHA-256", <code key="h" className="break-all type-code">{digest}</code>],
            ]} />
            <p className="type-caption text-fg-muted">The server encrypts the payload and records a keyed HMAC and a ciphertext digest, not this browser hash.</p>
          </Section>
          <div className="space-y-3 lg:col-span-2">
            <Switch label="Simulate a server refusal" description="The server returns 409 IDEMPOTENCY_CONFLICT, as for a repeat upload with different details." checked={refuse} onChange={setRefuse} />
          </div>
        </div>
      )}

      {step === 3 && progress.value !== null && (
        <div className="space-y-4" aria-live="polite">
          <ProgressBar value={progress.value} label={progress.outcome === "failed" ? "Upload refused" : progress.outcome === "ok" ? "Evidence recorded" : "Encrypting & chaining… (simulated)"}
            tone={progress.outcome === "failed" ? "critical" : progress.outcome === "ok" ? "verified" : "accent"}
            detail={!progress.outcome ? [...STAGES].reverse().find(([at]) => progress.value! >= at)?.[1] : undefined} />
          {progress.outcome === "failed" && (
            <Alert tone="critical" live="assertive" title="The server refused the upload: IDEMPOTENCY_CONFLICT (HTTP 409)">
              A file with this name, size and modification time was already submitted with different details. Nothing new was recorded. (Demonstration.)
            </Alert>
          )}
          {progress.outcome === "ok" && (
            <div className="grid gap-stack lg:grid-cols-2">
              <Callout tone="verified" title="Evidence recorded (demonstration)">
                In the application the artifact now sits in the evidence ledger, awaiting review. Here, nothing was stored.
              </Callout>
              <DescriptionList items={[
                ["Record", <span key="id">EVD-DEMO-0001<Sample /></span>], ["Condition", <StateBadge key="c" code={condition} size="sm" />],
                ["Lifecycle", <StateBadge key="l" code="ACTIVE" size="sm" />], ["Review status", <StateBadge key="r" code="PENDING_REVIEW" size="sm" />],
              ]} />
              <div className="lg:col-span-2">
                <Section title="Provenance">
                  <Timeline label="Evidence provenance" items={[
                    { id: "c", title: "Selected and hashed in the browser", meta: `${you.name} · ${file?.name}`, description: <code className="break-all type-code">SHA-256 {digest}</code>, status: "verified" },
                    { id: "e", title: "Encrypted, digests recorded, MANIFEST_CREATED chained", meta: "Server (simulated)", status: "verified" },
                    { id: "r", title: "Awaiting review", meta: "PENDING_REVIEW", description: "Only CURRENT evidence can support current-period proof.", status: "warning" },
                  ]} />
                </Section>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-4">
        {step > 0 && step < 3 ? <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(step - 1)}>Back</Button> : <span />}
        {step < 2 && <Button variant="primary" iconRight={ArrowRight} onClick={next}>Continue</Button>}
        {step === 2 && <Button variant="primary" icon={Send} onClick={() => setConfirming(true)}>Submit evidence</Button>}
        {step === 3 && progress.outcome === "failed" && (
          <span className="flex gap-2"><Button variant="ghost" icon={ArrowLeft} onClick={() => { progress.reset(); setStep(2); }}>Back to review</Button>
            <Button variant="secondary" icon={RotateCcw} onClick={() => { setRefuse(false); progress.start(); }}>Try again</Button></span>
        )}
        {step === 3 && progress.outcome === "ok" && (
          <Button variant="secondary" icon={RotateCcw} onClick={() => { progress.reset(); setFile(null); setDigest(undefined); setTried({}); setForm({ title: "", entity: "", from: "", to: "", classification: "CUSTOMER_CONFIDENTIAL", validity: "90" }); setStep(0); }}>Submit another</Button>
        )}
      </div>

      {confirming && (
        <ConfirmDialog title="Submit this evidence?" confirmLabel="Submit evidence" busyLabel="Submitting…" onClose={() => setConfirming(false)} onConfirm={submit}>
          <DescriptionList columns={1} items={summary.slice(0, 4)} />
          <p>In the application it is encrypted and chained into the evidence ledger. Afterwards it can only be superseded or invalidated, with a reason.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}
