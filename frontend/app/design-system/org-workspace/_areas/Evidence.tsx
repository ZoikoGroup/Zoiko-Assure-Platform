"use client";

import { useMemo, useState } from "react";
import { Lock, ShieldCheck, Upload } from "lucide-react";
import {
  Alert, Button, Card, ConfirmDialog, DataTable, DescriptionList, Field, FileDropzone, ProgressBar, SearchInput, Select, Spinner, StateBadge, TabPanel, Tabs,
  TextInput, useToast, type DataColumn,
} from "@/components/ds";
import { DATA_CLASSES } from "@/lib/codes";
import { fmtDate, fmtRange, short } from "@/lib/format";
import { DemoFrame, Sample, Section, delay, sha256, useSimulatedProgress } from "../../_shared/demo";
import { ENTITIES, EVIDENCE, entityName, type EvidenceItem } from "./data";
import { AreaLink, ClassChip, FlowLink, Proposed, SPLIT, useDetailPanel, useSelection } from "./shared";

/* Evidence management, mirroring Evidence Records (app/evidence → GET /evidence, GET /evidence/{id},
   POST /evidence/{id}/verify, POST /evidence/upload). Each artifact records a classification, entity, coverage and
   validity, a condition (CURRENT, STALE, INVALID, ERASED…) and lifecycle, its origin and provenance class (set by the
   server), its IPE result when it came from a source run, and its ledger entry. Verification checks the stored object,
   its ciphertext digest and plaintext HMAC. An inline preview and the search and filters are proposals. */

const CLASSES = ["CUSTOMER_INTERNAL", "CUSTOMER_CONFIDENTIAL", "RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"];
const CONDITIONS = ["ALL", "CURRENT", "STALE", "INVALID", "ERASED"];
const WITHHELD = ["RESTRICTED_PERSONAL", "LEGAL_PRIVILEGED"];
const MAX_BYTES = 25 * 1024 * 1024;
const CHECKS = ["OBJECT_PRESENT", "CIPHERTEXT_DIGEST", "PLAINTEXT_HMAC"];

export function Evidence({ go }: { go: (id: string) => void }) {
  return (
    <DemoFrame id="ow-evidence" plane="workspace" title="Evidence management"
      intro="The organization's evidence repository: what each artifact is, how it is classified, where it came from, whether its integrity verifies and what it supports."
      app={{ screen: "Evidence Records", route: "/evidence", action: "GET /evidence · POST /evidence/{id}/verify · POST /evidence/upload", permission: "read",
        enforced: ["Payloads are encrypted; only roles allowed to read the classification can download them", "Origin, provenance class and review status are set by the server, never by the uploader", "Uploading needs Submit evidence; files over 25 MB are refused (413)", "Verification appends a ledger event; it never edits the artifact"] }}
      availability={{ level: "partial", note: "The repository, artifact detail with provenance, verification, holds and upload exist. Search, filters and an inline preview are proposals; today an artifact's payload is downloaded." }}
      empty={{ title: "No evidence yet", description: "Upload an artifact or connect a source system to start the repository." }}>
      <Body go={go} />
    </DemoFrame>
  );
}

function Body({ go }: { go: (id: string) => void }) {
  const [items, setItems] = useState<EvidenceItem[]>(EVIDENCE);
  const [q, setQ] = useState("");
  const [cls, setCls] = useState("all");
  const [condition, setCondition] = useState("ALL");
  const [selected, setSelected] = useSelection("evidence", EVIDENCE[0].id);
  const [tab, setTab] = useState("metadata");
  const { ref, reveal } = useDetailPanel();

  const base = useMemo(() => items.filter((e) => (cls === "all" || e.classification === cls)
    && (!q.trim() || `${e.title} ${e.id} ${e.source} ${e.obligations.join(" ")} ${e.controls.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase()))), [items, q, cls]);
  const shown = base.filter((e) => condition === "ALL" || e.condition === condition);
  const current = items.find((e) => e.id === selected);
  const open = (id: string) => { setSelected(id); setTab("metadata"); reveal(); };

  const columns: DataColumn<EvidenceItem>[] = [
    { key: "title", header: "Evidence", sortValue: (e) => e.title, render: (e) => <><span className="block font-medium text-fg">{e.title}{e.hold && <Lock size={12} aria-label="Under legal hold" className="ml-1 inline text-fg-muted" />}</span>
      <code className="type-code text-fg-muted">{e.id}</code> <code className="type-code text-fg-muted">· {e.provenance}</code></> },
    { key: "class", header: "Classification · entity", sortValue: (e) => e.classification, render: (e) => <><ClassChip code={e.classification} /><span className="block type-caption text-fg-muted">{entityName(e.entity)}</span></> },
    { key: "coverage", header: "Coverage", sortValue: (e) => e.coverage[0], render: (e) => <span className="tabular-nums"><span className="block whitespace-nowrap">{fmtDate(e.coverage[0])}</span><span className="block whitespace-nowrap type-caption text-fg-muted">→ {fmtDate(e.coverage[1])}</span></span> },
    { key: "condition", header: "Condition", sortValue: (e) => e.condition, render: (e) => <StateBadge code={e.condition} size="sm" /> },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (e) => (
      <Button size="sm" variant={e.id === selected ? "secondary" : "ghost"} aria-pressed={e.id === selected} onClick={() => open(e.id)}>
        {e.id === selected ? "Viewing" : "View"}<span className="sr-only"> {e.title}</span>
      </Button>
    ) },
  ];

  return (
    <div className="space-y-stack">
      <div className={SPLIT}>
        <div className="min-w-0 space-y-3">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <Field label={<>Search evidence<Proposed /></>}><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Title, ID, source, obligation or control" /></Field>
            <Field label={<>Classification<Proposed /></>}><Select value={cls} onChange={(e) => setCls(e.target.value)} options={[{ value: "all", label: "All classifications" }, ...CLASSES.map((c) => ({ value: c, label: c }))]} /></Field>
          </div>
          <Tabs id="ow-evd-condition" label="Filter by condition" variant="pills" value={condition} onChange={setCondition}
            items={CONDITIONS.map((c) => ({ value: c, label: c === "ALL" ? "All" : c, count: base.filter((e) => c === "ALL" || e.condition === c).length }))} />
          <TabPanel id="ow-evd-condition" value={condition} className="">
            <DataTable caption={`Evidence repository (sample): ${shown.length} shown`} columns={columns} rows={shown} rowKey={(e) => e.id} defaultSort={{ key: "coverage", dir: "desc" }} minWidth="44rem"
              empty={{ title: "No evidence matches", description: "Change the search or the filters.", action: <Button size="sm" onClick={() => { setQ(""); setCls("all"); setCondition("ALL"); }}>Clear filters</Button> }} />
          </TabPanel>
        </div>

        <div ref={ref} className="scroll-mt-6">
          {current ? <Detail key={current.id} item={current} tab={tab} setTab={setTab} go={go}
            onVerified={(r) => setItems((xs) => xs.map((x) => (x.id === current.id ? { ...x, integrity: r } : x)))} />
            : <Card><p className="type-secondary text-fg-muted">Choose an artifact to see its metadata, provenance and links.</p></Card>}
        </div>
      </div>

      <UploadCard existing={items} onUploaded={(e) => { setItems((xs) => [e, ...xs]); setCondition("ALL"); setQ(""); setCls("all"); open(e.id); }} />
    </div>
  );
}

function Detail({ item, tab, setTab, go, onVerified }: { item: EvidenceItem; tab: string; setTab: (t: string) => void; go: (id: string) => void; onVerified: (r: NonNullable<EvidenceItem["integrity"]>) => void }) {
  const toast = useToast();
  const [verifying, setVerifying] = useState(false);
  const [checks, setChecks] = useState<[string, string][] | null>(null);
  const erased = item.condition === "ERASED";

  const verify = async () => {
    setVerifying(true); setChecks(null);
    await delay(1100);
    const result = erased ? "PAYLOAD_NOT_AVAILABLE" : "VERIFIED";
    setChecks(erased ? [["PAYLOAD", "PAYLOAD_ERASED"]] : CHECKS.map((c) => [c, "VERIFIED"]));
    setVerifying(false);
    onVerified(result);
    toast(`${item.id}: ${result} (simulated: no ledger event was appended)`, erased ? "warn" : "good");
  };

  return (
    <Card title={item.title} description={<code className="type-code">{item.id}</code>} actions={<StateBadge code={item.condition} size="sm" />} bodyClassName="p-card space-y-4">
      <Tabs id="ow-evd-detail" label="Evidence detail" value={tab} onChange={setTab}
        items={[{ value: "metadata", label: "Metadata" }, { value: "provenance", label: "Provenance" }, { value: "links", label: "Links", count: item.obligations.length + item.controls.length }, { value: "preview", label: "Preview" }]} />
      <TabPanel id="ow-evd-detail" value={tab} className="space-y-4">
        {tab === "metadata" && <DescriptionList columns={1} items={[
          ["Classification", <span key="c" className="space-y-1"><ClassChip code={item.classification} /><span className="block type-caption text-fg-muted">{DATA_CLASSES[item.classification]}</span></span>],
          ["Entity", entityName(item.entity)], ["Coverage", `${fmtRange(item.coverage)} (end exclusive)`], ["Valid until", fmtDate(item.validUntil)],
          ["Lifecycle", <StateBadge key="l" code={item.lifecycle} size="sm" />], ["Legal hold", item.hold ? "Placed: the payload cannot be erased while it holds" : "None"],
        ]} />}
        {tab === "provenance" && <>
          <DescriptionList columns={1} items={[
            ["Origin", <code key="o" className="type-code">{item.origin}</code>], ["Provenance class", <code key="p" className="type-code">{item.provenance}</code>],
            ["Source", <code key="s" className="type-code break-all">{item.source}</code>],
            ["IPE", item.ipe ? <StateBadge key="i" code={item.ipe} size="sm" /> : <span key="i" className="text-fg-muted">Not applicable: not from a source run</span>],
            ["Ledger entry", <code key="g" className="type-code">#{item.ledger}</code>],
            ["Integrity", item.integrity ? <StateBadge key="v" code={item.integrity} size="sm" /> : <span key="v" className="text-fg-muted">Not verified yet</span>],
          ]} />
          {verifying && <Spinner showLabel label="Verifying the stored object, digest and HMAC…" />}
          {checks && (
            <ul aria-label="Verification checks" className="divide-y divide-line rounded-card border border-line">
              {checks.map(([c, r]) => <li key={c} className="flex items-center justify-between gap-2 px-3 py-2"><code className="type-code">{c}</code><StateBadge code={r} size="sm" /></li>)}
            </ul>
          )}
          <Button fullWidth icon={ShieldCheck} loading={verifying} onClick={verify}>Verify integrity</Button>
        </>}
        {tab === "links" && <>
          <Section title="Obligations">{item.obligations.length ? <ul className="flex flex-wrap gap-2">{item.obligations.map((o) => <li key={o}><AreaLink id={o} area="obligations" go={go} label={`Open obligation ${o}`} /></li>)}</ul> : <p className="type-secondary text-fg-muted">None</p>}</Section>
          <Section title="Controls">{item.controls.length ? <ul className="flex flex-wrap gap-2">{item.controls.map((c) => <li key={c}><AreaLink id={c} area="controls" go={go} label={`Open control ${c}`} /></li>)}</ul> : <p className="type-secondary text-fg-muted">None</p>}</Section>
        </>}
        {tab === "preview" && <>
          <p className="type-label text-fg-muted">Inline preview<Proposed /></p>
          {WITHHELD.includes(item.classification) || erased
            ? <Alert tone="info" title="Preview withheld">{erased ? "The payload was lawfully erased; its manifest and ledger history remain." : `${item.classification} payloads are shown only to people allowed to read that class. ${item.preview}`}</Alert>
            : <pre className="overflow-x-auto whitespace-pre-wrap rounded-card border border-line bg-surface-control p-3 type-code text-fg">{item.preview}</pre>}
        </>}
      </TabPanel>
      <p className="type-caption text-fg-muted">Sample artifact<Sample />. Nothing here reads or changes a stored payload.</p>
    </Card>
  );
}

const BLANK = { title: "", entity: "", classification: "CUSTOMER_CONFIDENTIAL", from: "", to: "" };

function UploadCard({ existing, onUploaded }: { existing: EvidenceItem[]; onUploaded: (e: EvidenceItem) => void }) {
  const toast = useToast();
  const progress = useSimulatedProgress();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [form, setForm] = useState(BLANK);
  const [tried, setTried] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [digest, setDigest] = useState<string | null>(null);

  const errors = {
    title: !form.title.trim() ? "Enter a title." : form.title.trim().length > 300 ? "Keep the title to 300 characters or fewer." : undefined,
    entity: !form.entity ? "Choose the legal entity this evidence is for." : undefined,
    from: !form.from ? "Enter the start of the period this evidence covers." : undefined,
    to: !form.to ? "Enter the end of the period this evidence covers." : form.from && form.to <= form.from ? "The end must be after the start." : undefined,
  };
  const show = (k: keyof typeof errors) => (tried ? errors[k] : undefined);
  const set = (k: keyof typeof BLANK) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const idem = file ? `${file.name}:${file.size}:${file.lastModified}` : "";

  const pick = async (files: File[]) => {
    const f = files[0];
    setDigest(null); progress.reset();
    if (!f) return;
    if (f.size > MAX_BYTES) { setFile(null); setFileError(`This file is ${(f.size / 1048576).toFixed(1)} MB. The limit is 25 MB; the server refuses larger uploads (HTTP 413).`); return; }
    if (f.size === 0) { setFile(null); setFileError("This file is empty. Choose the file that holds the evidence."); return; }
    setFileError(null); setFile(f);
    setDigest(await sha256(await f.arrayBuffer()));
  };
  const review = () => {
    setTried(true);
    if (!file) { setFileError((e) => e ?? "Choose a file to continue."); return; }
    if (!Object.values(errors).some(Boolean)) setConfirming(true);
  };
  const upload = async () => {
    const details = JSON.stringify({ ...form, title: form.title.trim() });
    if (keys[idem] && keys[idem] !== details) throw new Error("Refused (409 IDEMPOTENCY_CONFLICT): this file was already submitted with different details. Simulated response; the server was not called.");
    if (keys[idem]) {
      await delay(400);
      toast("Already submitted with these details: the server would return the existing artifact (simulated)", "info");
      setFile(null); setForm(BLANK); setTried(false); setDigest(null);
      return;
    }
    await delay(300);
    progress.start(undefined, 12, 120);
    await delay(1300);
    const id = `evd-${String(1200 + existing.length).padStart(4, "0")}`;
    setKeys((k) => ({ ...k, [idem]: details }));
    onUploaded({ id, title: form.title.trim(), classification: form.classification as EvidenceItem["classification"], entity: form.entity, coverage: [form.from, form.to],
      validUntil: new Date(new Date(form.to).getTime() + 90 * 86_400_000).toISOString().slice(0, 10), condition: "CURRENT", lifecycle: "ACTIVE",
      origin: "HUMAN_UPLOAD", provenance: "P4_MANUAL_UNATTESTED", source: `upload:${file!.name}`, ipe: null, ledger: 4170 + existing.length,
      obligations: [], controls: [], preview: `${file!.name} (${(file!.size / 1024).toFixed(1)} KB). SHA-256 ${digest}` });
    toast(`${form.title.trim()} added (simulated: nothing was uploaded)`, "good");
    setFile(null); setForm(BLANK); setTried(false); setDigest(null);
  };

  return (
    <Card title="Upload and validate evidence" description="A compact version of the upload; the Evidence submission flow shows every step"
      actions={<FlowLink flow="evidence">Full Evidence submission flow</FlowLink>}>
      <div className="grid gap-form lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Field label="Evidence file" required error={fileError ?? undefined}><FileDropzone onFiles={pick} formats="Any file type, up to 25 MB" /></Field>
          {file && <p className="type-caption text-fg-muted"><span className="font-medium text-fg">{file.name}</span> · {(file.size / 1024).toFixed(1)} KB · SHA-256 {digest ? <code className="type-code">{short(digest, 16)}</code> : "computing…"} (in your browser)</p>}
          {progress.value != null && <ProgressBar label="Uploading (simulated)" value={progress.value} tone={progress.outcome === "ok" ? "verified" : "accent"} />}
        </div>
        <div className="grid gap-form sm:grid-cols-2">
          <Field label="Title" required error={show("title")} className="sm:col-span-2"><TextInput value={form.title} onChange={set("title")} maxLength={320} /></Field>
          <Field label="Legal entity" required error={show("entity")}><Select value={form.entity} onChange={set("entity")} placeholder="Choose an entity" options={ENTITIES.map((e) => ({ value: e.id, label: e.name }))} /></Field>
          <Field label="Classification" hint="Who may see the payload."><Select value={form.classification} onChange={set("classification")} options={CLASSES.map((c) => ({ value: c, label: c }))} /></Field>
          <Field label="Coverage from" required error={show("from")}><TextInput type="date" value={form.from} onChange={set("from")} /></Field>
          <Field label={<>Coverage to<Proposed>Order check proposed</Proposed></>} required error={show("to")}><TextInput type="date" value={form.to} onChange={set("to")} /></Field>
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
            <Button variant="primary" icon={Upload} onClick={review}>Review upload</Button>
            <span className="type-caption text-fg-muted">The server sets origin (HUMAN_UPLOAD) and provenance (P4_MANUAL_UNATTESTED).</span>
          </div>
        </div>
      </div>
      {confirming && file && (
        <ConfirmDialog title="Upload this evidence?" confirmLabel="Upload evidence" busyLabel="Uploading…" onClose={() => setConfirming(false)} onConfirm={upload}>
          <DescriptionList columns={1} items={[["File", `${file.name} · ${(file.size / 1024).toFixed(1)} KB`], ["Title", form.title.trim()], ["Entity", entityName(form.entity)],
            ["Classification", form.classification], ["Coverage", fmtRange([form.from, form.to])]]} />
          <p>Simulated: the file stays in your browser and no artifact or ledger entry is created. Submitting the same file again with different details shows the server&apos;s refusal.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}
