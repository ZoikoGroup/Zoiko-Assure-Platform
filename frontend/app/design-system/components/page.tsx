"use client";

import { useEffect, useId, useState } from "react";
import type { ComponentType, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive, ArrowRight, Bell, CheckCircle2, Compass, Copy, Download, FolderOpen, LayoutPanelTop, Link2, Lock, MessageSquare, MoreHorizontal,
  MousePointerClick, Pencil, RotateCcw, Save, Send, Settings2, Table2, Tags, TextCursorInput, Trash2, Upload,
} from "lucide-react";
import { GovernanceShell, useGovernance } from "@/components/DesignGovernance";
import { usePageTitle } from "@/components/ui";
import {
  Alert, Breadcrumbs, Button, Card, Checkbox, CollapsiblePanel, ConfirmDialog, CriticalPanel, DataTable, DescriptionList, DropdownMenu, EmptyState, ErrorState,
  Field, Fieldset, FileDropzone, IconButton, InfoPanel, KpiCard, MenuItem, MenuLabel, MenuSeparator, Modal, Pagination, Panel, PlaneBadge, PlaneBanner, ProgressBar, RadioGroup, SearchInput, Select,
  Skeleton, SkeletonText, Spinner, StateBadge, StatusBadge, StatusMark, Stepper, Switch, TabPanel, Tabs, TextInput, Textarea, Timeline, Tooltip, WarningPanel, useToast,
  type AlertTone, type DataColumn, type StatusKey,
} from "@/components/ds";
import { ApiError } from "@/lib/api";
import { STATUSES } from "@/lib/design-tokens";
import { fmtDate } from "@/lib/format";

/* Design Governance › Components (Unified Design System, Task 2). Every demo uses the production components from
   @/components/ds. Names, figures and records here are illustrative samples, not organisation data. */
const TITLE = "Design System Components";

export default function ComponentsShowcase() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return <GovernanceShell active="components" searchLabel="Search components"><Showcase /></GovernanceShell>;
}

type DemoDef = { title: string; description?: string; code: string; keywords: string; wide?: boolean; Body: ComponentType };
type Category = { id: string; title: string; icon: LucideIcon; demos: DemoDef[] };

function Showcase() {
  const { query } = useGovernance();
  const q = query.trim().toLowerCase();
  const categories = CATEGORIES.map((c) => ({ ...c, demos: c.demos.filter((d) => !q || `${c.title} ${d.title} ${d.keywords} ${d.code}`.toLowerCase().includes(q)) }))
    .filter((c) => c.demos.length > 0);
  const count = categories.reduce((n, c) => n + c.demos.length, 0);
  return (
    <div className="space-y-section">
      <section aria-labelledby="components-title">
        <h1 id="components-title" className="type-page text-fg sm:type-display">Reusable <span className="text-accent-fg">Components</span></h1>
        <p className="mt-3 max-w-3xl type-body text-fg-secondary">
          Production components on the foundation tokens. They follow the active theme, work by keyboard, and share their dialog, menu, tab,
          sorting and toast behaviour with the application kit.
        </p>
        <p className="mt-3 inline-flex flex-wrap items-center gap-2 rounded-control border border-line bg-surface-control/70 px-3 py-2">
          <span className="type-label text-fg-muted">Import</span>
          <code className="type-code text-accent-fg">{`import { Button, Field, DataTable } from "@/components/ds"`}</code>
        </p>
        {q && <p role="status" className="mt-3 type-caption text-fg-muted">{count} component demo{count === 1 ? "" : "s"} match “{query.trim()}”. Press Escape in the search box to clear.</p>}
      </section>

      <nav aria-label="Component categories" className="no-scrollbar -mx-gutter overflow-x-auto px-gutter sm:mx-0 sm:overflow-visible sm:px-0">
        <ul className="flex gap-2 sm:flex-wrap">
          {categories.map((c) => (
            <li key={c.id} className="shrink-0">
              <a href={`#${c.id}`} className="inline-flex h-9 items-center gap-2 rounded-nav border border-line bg-surface-control/70 px-3 type-secondary text-fg-secondary transition-colors hover:border-line-strong hover:text-fg">
                <c.icon size={15} aria-hidden className="text-accent" />{c.title}<span className="type-caption text-fg-muted">{c.demos.length}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {categories.length === 0 ? <EmptyState title="No components match the search" description="Try a component name such as button, table or modal." /> : categories.map((c) => (
        <Panel key={c.id} id={c.id} title={c.title} icon={c.icon}>
          <div className="grid gap-stack lg:grid-cols-2">
            {c.demos.map((d) => <Demo key={d.title} {...d} />)}
          </div>
        </Panel>
      ))}
      <p className="type-caption text-fg-muted">Names, figures and records in these demos are illustrative samples, not records of any organization.</p>
    </div>
  );
}

function Demo({ title, description, code, wide, Body }: DemoDef) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={`flex min-w-0 flex-col rounded-card border border-line bg-surface-card/60 ${wide ? "lg:col-span-2" : ""}`}>
      <header className="border-b border-line px-card py-3.5">
        <h3 id={id} className="type-card text-fg">{title}</h3>
        {description && <p className="mt-0.5 type-caption text-fg-muted">{description}</p>}
      </header>
      <div className="min-w-0 flex-1 p-card"><Body /></div>
      <div className="border-t border-line px-card py-2.5"><code className="break-words type-code text-accent-fg">{code}</code></div>
    </section>
  );
}

const Row = ({ label, children }: { label: string; children: ReactNode }) => (
  <div className="space-y-2">
    <p className="type-label text-fg-muted">{label}</p>
    <div className="flex flex-wrap items-center gap-3">{children}</div>
  </div>
);

/* ------------------------------------------------------------------------------------------------ buttons */
function ButtonVariants() {
  return (
    <div className="flex flex-wrap gap-3">
      <Button variant="primary" icon={CheckCircle2}>Primary action</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="outline">Outline</Button>
      <Button variant="ghost">Tertiary</Button>
      <Button variant="danger" icon={Trash2}>Delete</Button>
    </div>
  );
}

function ButtonSizes() {
  return (
    <div className="space-y-5">
      <Row label="Sizes"><Button size="sm" variant="primary">Small</Button><Button variant="primary">Medium</Button><Button size="lg" variant="primary">Large</Button></Row>
      <Row label="With icons"><Button icon={Upload}>Upload evidence</Button><Button variant="outline" iconRight={ArrowRight}>Continue</Button></Row>
      <Row label="Icon buttons">
        <IconButton label="Edit" icon={Pencil} variant="secondary" />
        <IconButton label="Copy link" icon={Copy} variant="secondary" />
        <IconButton label="More actions" icon={MoreHorizontal} />
        <IconButton label="Delete" icon={Trash2} variant="danger" size="sm" />
      </Row>
    </div>
  );
}

function ButtonStates() {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const save = () => { setSaving(true); setTimeout(() => { setSaving(false); toast("Changes saved (demo)", "good"); }, 1500); };
  return (
    <div className="space-y-5">
      <Row label="Disabled">
        <Button variant="primary" icon={Lock} disabled>Disabled</Button>
        <Button disabled>Secondary</Button>
        <Button variant="outline" disabled>Outline</Button>
        <Button variant="danger" disabled>Delete</Button>
      </Row>
      <Row label="Loading">
        <Button variant="primary" icon={Save} loading={saving} onClick={save}>{saving ? "Saving…" : "Save changes"}</Button>
        <Button loading>Refreshing</Button>
      </Row>
      <Row label="Full width"><Button variant="secondary" fullWidth>Full-width button</Button></Row>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------- form elements */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function TextFieldDemo() {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const error = submitted && !EMAIL.test(email) ? (email ? "Enter an email address like name@company.com." : "Enter the reviewer's email address.") : undefined;
  return (
    <form noValidate className="grid gap-form" onSubmit={(e) => { e.preventDefault(); setSubmitted(true); if (EMAIL.test(email)) toast(`Review requested from ${email} (demo)`, "good"); }}>
      <Field label="Reviewer email" required hint="This person is notified when evidence needs review." error={error}>
        <TextInput type="email" autoComplete="email" placeholder="name@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Reference" optional><TextInput placeholder="e.g. CTL-1012" /></Field>
      <div><Button type="submit" variant="primary" icon={Send}>Request review</Button></div>
    </form>
  );
}

const FRAMEWORKS = ["EU AI Act", "DORA", "NIS2", "SOX", "GDPR", "UK Consumer Duty"];
function SearchDemo() {
  const [q, setQ] = useState("");
  const hits = FRAMEWORKS.filter((f) => f.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="grid gap-3">
      <Field label="Find a framework"><SearchInput placeholder="Search frameworks" value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} /></Field>
      <p className="sr-only" role="status">{hits.length} frameworks shown</p>
      <ul className="flex flex-wrap gap-2">
        {hits.length ? hits.map((f) => <li key={f} className="rounded-chip border border-line bg-surface-control px-2 py-1 type-caption text-fg-secondary">{f}</li>)
          : <li className="type-caption text-fg-muted">No frameworks match.</li>}
      </ul>
    </div>
  );
}

function TextareaDemo() {
  const [v, setV] = useState("");
  const max = 280;
  return (
    <Field label="Reviewer note" hint={`${v.length} / ${max} characters`} error={v.length > max ? `Shorten the note by ${v.length - max} characters.` : undefined}>
      <Textarea placeholder="Explain what the reviewer should check." value={v} onChange={(e) => setV(e.target.value)} />
    </Field>
  );
}

function SelectDemo() {
  const [fw, setFw] = useState("all");
  return (
    <div className="grid gap-form sm:grid-cols-2">
      <Field label="Framework" hint="Filters the evidence list.">
        <Select value={fw} onChange={(e) => setFw(e.target.value)} options={[{ value: "all", label: "All frameworks" }, ...FRAMEWORKS.map((f) => ({ value: f, label: f }))]} />
      </Field>
      <Field label="Jurisdiction" hint="Set by the organization's entity scope." disabled>
        <Select defaultValue="gb" options={[{ value: "gb", label: "United Kingdom" }, { value: "nl", label: "Netherlands" }]} />
      </Field>
    </div>
  );
}

const EVIDENCE = ["Logs", "Screenshots", "Attestations"];
function CheckboxDemo() {
  const [sel, setSel] = useState<string[]>(["Logs"]);
  const all = sel.length === EVIDENCE.length;
  return (
    <Fieldset legend="Evidence types" hint="Choose what this control accepts.">
      <Checkbox label="All evidence types" checked={all} indeterminate={!all && sel.length > 0} onChange={(e) => setSel(e.target.checked ? EVIDENCE : [])} />
      <div className="grid gap-2.5 pl-7">
        {EVIDENCE.map((o) => <Checkbox key={o} label={o} checked={sel.includes(o)} onChange={(e) => setSel((s) => (e.target.checked ? [...s, o] : s.filter((x) => x !== o)))} />)}
      </div>
      <Checkbox label="System-generated reports" description="Not available for manual controls." disabled />
    </Fieldset>
  );
}

function RadioDemo() {
  const [v, setV] = useState("quarterly");
  return (
    <RadioGroup legend="Review cadence" name="cadence-demo" value={v} onChange={setV} options={[
      { value: "quarterly", label: "Quarterly", description: "Recommended for key controls" },
      { value: "semiannual", label: "Semi-annual" },
      { value: "annual", label: "Annual" },
      { value: "continuous", label: "Continuous", description: "Needs a certified connector", disabled: true },
    ]} />
  );
}

function SwitchDemo() {
  const [a, setA] = useState(true);
  const [b, setB] = useState(false);
  return (
    <div className="grid gap-4">
      <Switch label="Email alerts" description="When evidence goes stale." checked={a} onChange={setA} />
      <Switch label="Weekly digest" checked={b} onChange={setB} />
      <Switch label="Agent auto-approval" description="Unavailable: approvals need a named person." checked={false} onChange={() => {}} disabled />
    </div>
  );
}

function DisabledFieldsDemo() {
  return (
    <div className="grid gap-form">
      <Field label="Record ID" hint="Read-only: assigned by the server."><TextInput readOnly value="EVD-0881" /></Field>
      <Field label="Owner" disabled><TextInput defaultValue="Evidence team" /></Field>
      <Field label="Notes" disabled><Textarea rows={2} defaultValue="Locked while the period is frozen." /></Field>
    </div>
  );
}

/* --------------------------------------------------------------------------------------------- status badges */
function StatusCanonical() {
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {STATUSES.map((s) => (
        <li key={s.key} className="flex items-center justify-between gap-3 rounded-control border border-line px-3 py-2">
          <StatusBadge status={s.key} />
          <code className="type-code text-fg-muted">{s.token}</code>
        </li>
      ))}
    </ul>
  );
}

function StatusVariants() {
  return (
    <div className="space-y-5">
      <Row label="Small">{STATUSES.map((s) => <StatusBadge key={s.key} status={s.key} size="sm" />)}</Row>
      <Row label="With a count"><StatusBadge status="warning">3</StatusBadge><StatusBadge status="critical">1</StatusBadge><StatusBadge status="verified">24</StatusBadge></Row>
      <Row label="Marks">{STATUSES.map((s) => <span key={s.key} className="flex items-center gap-2"><StatusMark status={s.key} /><span className="sr-only">{s.label}</span></span>)}</Row>
    </div>
  );
}

const SAMPLE_ITEMS: { name: string; detail: string; status: StatusKey }[] = [
  { name: "Quarterly access review", detail: "Tested 12 Oct 2026", status: "verified" },
  { name: "Vendor SOC 2 report", detail: "Expires in 12 days", status: "warning" },
  { name: "Payroll reconciliation", detail: "Last test failed", status: "critical" },
  { name: "Backup restore test", detail: "No result in this period", status: "unknown" },
];
function StatusInContext() {
  return (
    <ul className="divide-y divide-line rounded-card border border-line">
      {SAMPLE_ITEMS.map((i) => (
        <li key={i.name} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
          <span className="min-w-0"><span className="block type-secondary font-medium text-fg">{i.name}</span><span className="type-caption text-fg-muted">{i.detail}</span></span>
          <StatusBadge status={i.status} size="sm" />
        </li>
      ))}
    </ul>
  );
}

/* --------------------------------------------------------------------------------------------- cards & panels */
function StandardCardDemo() {
  return (
    <Card title="Quarterly access review" description="CTL-2041 · Owner: IT Security"
      actions={<Button size="sm" variant="ghost" iconRight={ArrowRight}>Open</Button>} footer="Sample card · updated 2 Nov 2026">
      <p className="type-secondary text-fg-secondary">Confirms privileged accounts are still needed and approved by their owners.</p>
      <div className="mt-3"><StatusBadge status="warning">2 accounts</StatusBadge></div>
    </Card>
  );
}

function KpiDemo() {
  return (
    <div className="grid gap-stack sm:grid-cols-2">
      <KpiCard label="Controls operating" value="92.4%" tone="verified" delta={{ value: "2.6%", direction: "up", status: "verified", label: "since last period" }} hint="vs last period" />
      <KpiCard label="Integrations degraded" value="02" tone="critical" delta={{ value: "12%", direction: "down", status: "critical", label: "health since last week" }} hint="health" />
      <KpiCard label="Reviews pending" value="08" tone="warning" hint="human sign-off" />
      <KpiCard label="Evidence items" value="—" loading />
    </div>
  );
}

function PanelsDemo() {
  return (
    <div className="grid gap-3">
      <InfoPanel title="Figures are read when you open the page">Each count reflects the records known at that moment, not a period total.</InfoPanel>
      <WarningPanel title="Two controls need re-testing" actions={<Button size="sm">Review controls</Button>}>Their last tests are older than the 90-day policy.</WarningPanel>
      <CriticalPanel title="Ledger verification failed" actions={<Button size="sm" variant="danger">View failure</Button>}>
        The evidence chain could not be verified after record #4182. New attestations are blocked until it is resolved.
      </CriticalPanel>
    </div>
  );
}

function CollapsibleDemo() {
  return (
    <div className="grid gap-3">
      <CollapsiblePanel title="Why this obligation applies" description="3 facts · rule version 2.4" defaultOpen meta={<StatusBadge status="verified" size="sm" />}>
        The entity is a credit institution established in the EU with more than 250 staff, so the rule&apos;s scope conditions are met.
      </CollapsiblePanel>
      <CollapsiblePanel title="Evaluation trace" description="12 steps">Each predicate evaluated, with the fact and version it used.</CollapsiblePanel>
      <CollapsiblePanel title="Archived versions" description="Available after the period closes" disabled>Archived content.</CollapsiblePanel>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ data tables */
type Source = { id: string; framework: string; jurisdiction: string; status: StatusKey; updated: string };
const SOURCES: Source[] = [
  { id: "SRC-101", framework: "EU AI Act · Transparency", jurisdiction: "EU", status: "warning", updated: "2026-10-28" },
  { id: "SRC-102", framework: "DORA · ICT risk management", jurisdiction: "EU", status: "verified", updated: "2026-10-14" },
  { id: "SRC-103", framework: "NIS2 · Incident reporting", jurisdiction: "EU", status: "critical", updated: "2026-10-30" },
  { id: "SRC-104", framework: "UK Consumer Duty · Outcomes", jurisdiction: "UK", status: "verified", updated: "2026-09-22" },
  { id: "SRC-105", framework: "SOX · ITGC", jurisdiction: "US", status: "unknown", updated: "2026-08-03" },
  { id: "SRC-106", framework: "GDPR · Records of processing", jurisdiction: "EU", status: "info", updated: "2026-10-02" },
];
const SEVERITY: Record<StatusKey, number> = { critical: 0, warning: 1, unknown: 2, info: 3, verified: 4 };
const SOURCE_COLUMNS: DataColumn<Source>[] = [
  { key: "framework", header: "Framework", sortValue: (r) => r.framework, render: (r) => <><span className="block font-medium text-fg">{r.framework}</span><span className="type-code text-fg-muted">{r.id}</span></> },
  { key: "jurisdiction", header: "Jurisdiction", sortValue: (r) => r.jurisdiction, render: (r) => r.jurisdiction },
  { key: "status", header: "Status", sortValue: (r) => SEVERITY[r.status], render: (r) => <StatusBadge status={r.status} size="sm" /> },
  { key: "updated", header: "Updated", align: "right", sortValue: (r) => r.updated, render: (r) => <span className="whitespace-nowrap tabular-nums text-fg-secondary">{fmtDate(r.updated)}</span> },
];

function TableDemo() {
  const toast = useToast();
  const [view, setView] = useState("data");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return (
    <div className="space-y-3">
      <Tabs id="table-state" label="Table state" variant="pills" value={view} onChange={(v) => { setView(v); setSelected(new Set()); }}
        items={[{ value: "data", label: "Data" }, { value: "loading", label: "Loading" }, { value: "empty", label: "Empty" }, { value: "error", label: "Error" }]} />
      {selected.size > 0 && (
        <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-control border border-accent/40 bg-accent/10 px-3 py-2">
          <span className="type-secondary font-medium text-fg">{selected.size} selected</span>
          <span className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
            <Button size="sm" variant="primary" icon={Download} onClick={() => toast(`Exported ${selected.size} rows (demo)`, "good")}>Export</Button>
          </span>
        </div>
      )}
      <TabPanel id="table-state" value={view} className="">
        <DataTable caption="Regulatory sources (sample data). Select rows to act on them; sort by any column header." captionVisible
          columns={SOURCE_COLUMNS} rows={view === "data" ? SOURCES : []} rowKey={(r) => r.id} rowLabel={(r) => r.framework}
          loading={view === "loading"} error={view === "error" ? new Error("The catalogue service did not respond. Try again in a moment.") : undefined}
          onRetry={() => { setView("data"); toast("Reloaded (demo)"); }}
          empty={{ title: "No sources match these filters", description: "Clear a filter to see more sources.", action: <Button size="sm" onClick={() => setView("data")}>Clear filters</Button> }}
          selectable selected={selected} onSelectedChange={setSelected} defaultSort={{ key: "updated", dir: "desc" }} />
      </TabPanel>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------- navigation */
const TAB_TEXT: Record<string, string> = {
  overview: "Purpose, owner and the obligations this control satisfies.",
  evidence: "Twelve artifacts collected in the current period.",
  tests: "Test runs and their results, newest first.",
};
function TabsDemo() {
  const [tab, setTab] = useState("overview");
  const [range, setRange] = useState("week");
  return (
    <div className="space-y-6">
      <div>
        <Tabs id="control-tabs" label="Control sections" value={tab} onChange={setTab} items={[
          { value: "overview", label: "Overview" }, { value: "evidence", label: "Evidence", count: 12 }, { value: "tests", label: "Tests" }, { value: "history", label: "History", disabled: true },
        ]} />
        <TabPanel id="control-tabs" value={tab}><p className="type-secondary text-fg-secondary">{TAB_TEXT[tab]}</p></TabPanel>
      </div>
      <div>
        <Tabs id="range-tabs" label="Reporting range" variant="pills" value={range} onChange={setRange}
          items={[{ value: "day", label: "Day" }, { value: "week", label: "Week" }, { value: "quarter", label: "Quarter" }]} />
        <TabPanel id="range-tabs" value={range} className="pt-2"><p className="type-caption text-fg-muted">Showing the last {range}.</p></TabPanel>
      </div>
    </div>
  );
}

function BreadcrumbsDemo() {
  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Zoiko Assure", href: "/" }, { label: "Assurance", href: "#navigation" }, { label: "Controls & Testing", href: "#navigation" }, { label: "CTL-1012" }]} />
      <Breadcrumbs items={[{ label: "Design Governance", href: "/design-system" }, { label: "Components" }]} />
      <p className="type-caption text-fg-muted">On phones, long trails keep the first and last two items.</p>
    </div>
  );
}

function PaginationDemo() {
  const [page, setPage] = useState(3);
  const [small, setSmall] = useState(1);
  return (
    <div className="space-y-5">
      <Pagination page={page} pageCount={12} onPageChange={setPage} total={115} pageSize={10} label="Sample results pages" />
      <Pagination page={small} pageCount={3} onPageChange={setSmall} label="Short sample pages" />
    </div>
  );
}

function DropdownDemo() {
  const toast = useToast();
  return (
    <div className="flex flex-wrap items-start gap-3 pb-2">
      <DropdownMenu label="Actions" icon={Settings2}>
        <MenuLabel>Record</MenuLabel>
        <MenuItem icon={Pencil} onSelect={() => toast("Edit (demo)")}>Edit</MenuItem>
        <MenuItem icon={Copy} onSelect={() => toast("Duplicated (demo)", "good")}>Duplicate</MenuItem>
        <MenuItem icon={Download} onSelect={() => toast("Download started (demo)")}>Download evidence</MenuItem>
        <MenuSeparator />
        <MenuItem icon={Archive} disabled hint="Frozen">Archive</MenuItem>
        <MenuItem icon={Trash2} danger onSelect={() => toast("Deleted (demo)", "bad")}>Delete</MenuItem>
      </DropdownMenu>
      <DropdownMenu label="More actions" icon={MoreHorizontal} iconOnly variant="ghost" align="left">
        <MenuItem icon={Bell} onSelect={() => toast("Subscribed (demo)", "good")}>Subscribe to changes</MenuItem>
        <MenuItem icon={Link2} onSelect={() => toast("Link copied (demo)")}>Copy link</MenuItem>
      </DropdownMenu>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------- feedback & dialogs */
const ALERTS: Record<AlertTone, [string, string]> = {
  info: ["Sync scheduled", "Connectors refresh at 02:00 UTC."],
  verified: ["Attestation recorded", "It was signed and added to the evidence ledger."],
  warning: ["Evidence expires in 7 days", "Upload a newer artifact to keep the control proven."],
  critical: ["Upload rejected", "The file failed its integrity check. Nothing was stored."],
};
function AlertsDemo() {
  const all = Object.keys(ALERTS) as AlertTone[];
  const [shown, setShown] = useState<AlertTone[]>(all);
  return (
    <div className="grid gap-3">
      {shown.map((t) => <Alert key={t} tone={t} title={ALERTS[t][0]} onDismiss={() => setShown((s) => s.filter((x) => x !== t))}>{ALERTS[t][1]}</Alert>)}
      {shown.length < all.length && <div><Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => setShown(all)}>Show dismissed alerts</Button></div>}
    </div>
  );
}

function DialogsDemo() {
  const toast = useToast();
  const [modal, setModal] = useState(false);
  const [confirm, setConfirm] = useState<null | "delete" | "fail">(null);
  const run = (ok: boolean) => new Promise<void>((resolve, reject) => setTimeout(() => (ok ? resolve()
    : reject(new Error("The server refused the attestation because the period is frozen. (Demo error.)"))), 1200));
  return (
    <div className="flex flex-wrap gap-3">
      <Button variant="secondary" icon={MessageSquare} onClick={() => setModal(true)}>Open modal</Button>
      <Button variant="danger" icon={Trash2} onClick={() => setConfirm("delete")}>Delete draft…</Button>
      <Button variant="outline" onClick={() => setConfirm("fail")}>Confirmation that fails</Button>
      <Modal open={modal} onClose={() => setModal(false)} title="Request evidence" description="Ask an owner for a missing artifact."
        footer={<>
          <Button variant="secondary" onClick={() => setModal(false)}>Cancel</Button>
          <Button variant="primary" icon={Send} onClick={() => { setModal(false); toast("Request sent (demo)", "good"); }}>Send request</Button>
        </>}>
        <div className="grid gap-form">
          <Field label="Owner"><Select options={[{ value: "it", label: "IT Security" }, { value: "fin", label: "Finance Operations" }]} /></Field>
          <Field label="Message" optional><Textarea rows={3} placeholder="What is missing, and by when?" /></Field>
        </div>
      </Modal>
      {confirm && (
        <ConfirmDialog title={confirm === "delete" ? "Delete this draft?" : "Submit attestation?"} confirmLabel={confirm === "delete" ? "Delete draft" : "Submit"}
          busyLabel={confirm === "delete" ? "Deleting…" : "Submitting…"} tone={confirm === "delete" ? "danger" : "default"} onClose={() => setConfirm(null)}
          onConfirm={() => run(confirm === "delete").then(() => toast("Draft deleted (demo)", "good"))}>
          <p>{confirm === "delete" ? "The draft and its notes are removed. This can't be undone." : "This demo always fails, so you can see how an error is shown in place."}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function ToastsDemo() {
  const toast = useToast();
  return (
    <div className="flex flex-wrap gap-3">
      <Button onClick={() => toast("Evidence uploaded (demo)", "good")}>Success</Button>
      <Button onClick={() => toast("Two reviews are waiting (demo)", "warn")}>Warning</Button>
      <Button onClick={() => toast("Upload failed: the file is too large (demo)", "bad")}>Error</Button>
      <Button onClick={() => toast("Sync starts in 5 minutes (demo)")}>Information</Button>
    </div>
  );
}

function TooltipDemo() {
  return (
    <div className="flex flex-wrap items-center gap-4">
      <Tooltip content="Copies a link to this record"><IconButton label="Copy link" icon={Link2} variant="secondary" /></Tooltip>
      <Tooltip content="Information Produced by the Entity" side="bottom">
        <button type="button" className="rounded type-secondary font-medium text-fg underline decoration-dotted underline-offset-4">IPE</button>
      </Tooltip>
      <Tooltip content="Opens the evidence upload dialog"><Button icon={Upload}>Upload</Button></Tooltip>
    </div>
  );
}

function LoadingDemo() {
  return (
    <div className="space-y-5">
      <Row label="Spinners"><Spinner size="sm" /><Spinner /><Spinner size="lg" /><Spinner showLabel label="Loading evidence…" /></Row>
      <div className="space-y-2">
        <p className="type-label text-fg-muted">Skeletons</p>
        <div className="flex items-center gap-3"><Skeleton className="h-10 w-10 rounded-full" /><div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-2/3" /><Skeleton className="h-3 w-1/3" /></div></div>
        <SkeletonText lines={3} />
      </div>
    </div>
  );
}

function EmptyErrorDemo() {
  const toast = useToast();
  const error = new ApiError(503, "The evidence service is unavailable.", { label: "Request ID", value: "demo-7f3a91" });
  return (
    <div className="grid gap-stack sm:grid-cols-2">
      <div className="rounded-card border border-dashed border-line">
        <EmptyState icon={FolderOpen} title="No evidence yet" description="Upload an artifact or connect a source." compact
          action={<Button size="sm" variant="primary" icon={Upload}>Upload evidence</Button>} />
      </div>
      <div className="rounded-card border border-line">
        <ErrorState error={error} title="Evidence could not be loaded" onRetry={() => toast("Retrying (demo)")} compact />
      </div>
    </div>
  );
}

function PlaneDemo() {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2"><PlaneBadge plane="platform" withWho /><PlaneBadge plane="workspace" withWho /></div>
      <PlaneBanner plane="platform">Registry metadata only; customer data is never shown on this plane.</PlaneBanner>
      <PlaneBanner plane="workspace">Customer evidence, obligations and approvals for one organization.</PlaneBanner>
    </div>
  );
}

/* ------------------------------------------------------------------------------------- workflow building blocks */
function StateBadgeDemo() {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">{["ATTESTED", "IN_REVIEW", "PENDING_APPROVAL", "REJECTED", "SIGNED", "STALE"].map((c) => <StateBadge key={c} code={c} />)}</div>
      <p className="type-caption text-fg-muted">The code is shown exactly as recorded; its colour family comes from the application&apos;s existing tone mapping.</p>
    </div>
  );
}

function FileDropzoneDemo() {
  const [name, setName] = useState<string>();
  return (
    <Field label="Evidence file" hint={name ? `Selected: ${name} (not uploaded)` : "The file stays in your browser."}>
      <FileDropzone onFiles={(f) => setName(f[0]?.name)} formats="Any format · up to 25 MB" />
    </Field>
  );
}

function StepperDemo() {
  const [step, setStep] = useState(1);
  const steps = [{ id: "a", label: "File" }, { id: "b", label: "Details" }, { id: "c", label: "Review" }, { id: "d", label: "Result" }];
  return (
    <div className="space-y-4">
      <Stepper steps={steps} current={step} onStepClick={setStep} label="Sample progress" />
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</Button>
        <Button size="sm" variant="primary" disabled={step === steps.length - 1} onClick={() => setStep(step + 1)}>Next</Button>
      </div>
    </div>
  );
}

function ProgressDemo() {
  const [v, setV] = useState(40);
  return (
    <div className="space-y-4">
      <ProgressBar value={v} label="Upload (sample)" tone={v === 100 ? "verified" : "accent"} detail="Encrypting" />
      <ProgressBar value={63} label="Refused" tone="critical" showValue={false} />
      <Button size="sm" onClick={() => setV((x) => (x >= 100 ? 0 : Math.min(100, x + 20)))}>Advance</Button>
    </div>
  );
}

function RecordDemo() {
  return (
    <div className="grid gap-stack sm:grid-cols-2">
      <DescriptionList columns={1} items={[["Record", "EVD-0881"], ["Condition", <StateBadge key="c" code="CURRENT" size="sm" />], ["Coverage", "1 Jul → 1 Oct 2026"]]} />
      <Timeline label="Sample history" items={[
        { id: "1", title: "Requested", meta: "28 Oct 2026", status: "info" },
        { id: "2", title: "Approved", meta: "29 Oct 2026", status: "verified" },
        { id: "3", title: "Awaiting verification", meta: "In progress", status: "warning" },
      ]} />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ registry */
const CATEGORIES: Category[] = [
  { id: "buttons", title: "Buttons", icon: MousePointerClick, demos: [
    { title: "Variants", description: "Primary, secondary, outline, tertiary (ghost) and danger.", code: `<Button variant="primary">`, keywords: "primary secondary outline ghost tertiary danger", Body: ButtonVariants },
    { title: "Sizes, icons and icon buttons", code: `<Button size="sm" icon={Upload}> · <IconButton label="Edit" icon={Pencil} />`, keywords: "size small large icon iconbutton", Body: ButtonSizes },
    { title: "Disabled, loading and full width", description: "Loading keeps the variant, shows a spinner and ignores clicks. Try Save changes.", code: `<Button loading> · <Button disabled>`, keywords: "disabled loading busy spinner full width state", wide: true, Body: ButtonStates },
  ] },
  { id: "forms", title: "Form Elements", icon: TextCursorInput, demos: [
    { title: "Text input, labels and validation", description: "Submit empty or with a bad address to see the error.", code: `<Field label="…" hint="…" error={…}><TextInput /></Field>`, keywords: "input text label helper error validation required optional form field", Body: TextFieldDemo },
    { title: "Search input", code: `<SearchInput value={q} onClear={() => setQ("")} />`, keywords: "search filter clear", Body: SearchDemo },
    { title: "Textarea", code: `<Textarea />`, keywords: "textarea multiline note character count", Body: TextareaDemo },
    { title: "Select", code: `<Select options={[…]} />`, keywords: "select dropdown native disabled", Body: SelectDemo },
    { title: "Checkbox", description: "Includes a mixed (indeterminate) parent.", code: `<Checkbox label="…" indeterminate={…} />`, keywords: "checkbox fieldset indeterminate group", Body: CheckboxDemo },
    { title: "Radio buttons", code: `<RadioGroup legend="…" options={[…]} />`, keywords: "radio group option", Body: RadioDemo },
    { title: "Toggle switch", code: `<Switch label="…" checked={on} onChange={setOn} />`, keywords: "switch toggle on off", Body: SwitchDemo },
    { title: "Disabled and read-only", code: `<Field disabled> · <TextInput readOnly />`, keywords: "disabled readonly read-only locked", Body: DisabledFieldsDemo },
    { title: "File dropzone", description: "A native file input with a drop area; works by keyboard.", code: `<FileDropzone onFiles={…} formats="…" />`, keywords: "file upload drop dropzone", Body: FileDropzoneDemo },
  ] },
  { id: "status", title: "Status Badges", icon: Tags, demos: [
    { title: "Canonical statuses", description: "The five statuses from Foundations, always with icon and text.", code: `<StatusBadge status="verified" />`, keywords: "verified needs review warning critical stale unknown informational info badge", Body: StatusCanonical },
    { title: "Sizes, counts and marks", code: `<StatusBadge status="warning">3</StatusBadge> · <StatusMark status="critical" />`, keywords: "badge small count mark", Body: StatusVariants },
    { title: "In context", code: `<StatusBadge status={item.status} size="sm" />`, keywords: "list row badge", Body: StatusInContext },
    { title: "Controlled state codes", description: "For recorded states such as ATTESTED or IN_REVIEW.", code: `<StateBadge code="ATTESTED" />`, keywords: "state code controlled dictionary attested review", Body: StateBadgeDemo },
    { title: "Plane markers", description: "Which authority plane a screen belongs to. Orientation only; the server enforces the boundary.", code: `<PlaneBadge plane="platform" /> · <PlaneBanner plane="workspace">`, keywords: "plane platform workspace super admin organization boundary", wide: true, Body: PlaneDemo },
  ] },
  { id: "cards", title: "Cards and Panels", icon: LayoutPanelTop, demos: [
    { title: "Standard card", code: `<Card title="…" actions={…} footer="…">`, keywords: "card header footer actions", Body: StandardCardDemo },
    { title: "Summary (KPI) cards", code: `<KpiCard label="…" value="92.4%" tone="verified" delta={…} />`, keywords: "kpi summary metric trend delta loading", Body: KpiDemo },
    { title: "Information, warning and critical panels", code: `<InfoPanel> · <WarningPanel> · <CriticalPanel>`, keywords: "callout panel info warning critical alert notice", Body: PanelsDemo },
    { title: "Collapsible panel", code: `<CollapsiblePanel title="…" defaultOpen>`, keywords: "collapsible accordion disclosure expand", Body: CollapsibleDemo },
    { title: "Record summary and history", code: `<DescriptionList items={[…]} /> · <Timeline items={[…]} />`, keywords: "description list key value timeline history provenance", wide: true, Body: RecordDemo },
  ] },
  { id: "tables", title: "Data Tables", icon: Table2, demos: [
    { title: "Data table", description: "Sortable headers, row hover, selection with a bulk action, and loading, empty and error states (switch with the tabs).", code: `<DataTable columns={…} rows={…} selectable loading={…} error={…} />`, keywords: "table header row cell hover selected empty loading error sort select", wide: true, Body: TableDemo },
  ] },
  { id: "navigation", title: "Navigation", icon: Compass, demos: [
    { title: "Tabs", description: "Arrow keys move between tabs; History is disabled.", code: `<Tabs id="…" items={[…]} /><TabPanel id="…" value={…}>`, keywords: "tabs pills segmented keyboard", Body: TabsDemo },
    { title: "Breadcrumbs", code: `<Breadcrumbs items={[…]} />`, keywords: "breadcrumb trail path", Body: BreadcrumbsDemo },
    { title: "Pagination", code: `<Pagination page={p} pageCount={12} onPageChange={setP} />`, keywords: "pagination pages next previous", Body: PaginationDemo },
    { title: "Dropdown menu", description: "Escape, an outside click or choosing an item closes it.", code: `<DropdownMenu label="Actions"><MenuItem …/></DropdownMenu>`, keywords: "dropdown menu actions more overflow", Body: DropdownDemo },
    { title: "Stepper", description: "Completed steps are buttons for going back.", code: `<Stepper steps={[…]} current={i} onStepClick={setI} />`, keywords: "stepper steps wizard progress", wide: true, Body: StepperDemo },
  ] },
  { id: "feedback", title: "Feedback and Dialogs", icon: MessageSquare, demos: [
    { title: "Alerts", code: `<Alert tone="warning" title="…" onDismiss={…} />`, keywords: "alert message dismiss inline", Body: AlertsDemo },
    { title: "Modal and confirmation dialog", description: "Focus is trapped; Escape closes; focus returns to the button.", code: `<Modal open title="…"> · <ConfirmDialog onConfirm={…} />`, keywords: "modal dialog confirm confirmation", Body: DialogsDemo },
    { title: "Toast notifications", code: `const toast = useToast(); toast("Saved", "good")`, keywords: "toast notification snackbar", Body: ToastsDemo },
    { title: "Tooltip", code: `<Tooltip content="…"><IconButton … /></Tooltip>`, keywords: "tooltip hint hover", Body: TooltipDemo },
    { title: "Loading indicators", code: `<Spinner /> · <SkeletonText lines={3} />`, keywords: "loading spinner skeleton progress", Body: LoadingDemo },
    { title: "Progress bar", code: `<ProgressBar value={60} label="Upload" />`, keywords: "progress bar upload percent", Body: ProgressDemo },
    { title: "Empty and error states", code: `<EmptyState title="…" /> · <ErrorState error={e} onRetry={…} />`, keywords: "empty error state retry", Body: EmptyErrorDemo },
  ] },
];
