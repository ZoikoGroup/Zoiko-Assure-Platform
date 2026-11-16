"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Building2, CircleCheck, CirclePause, Copy, Landmark, Plus, Search } from "lucide-react";
import { api, errText, useApi } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import {
  Alert, Button, DescriptionList, EmptyState, ErrorState, Field, KpiCard, Modal, PLANES, PlaneBadge, SearchInput, Select, Skeleton, TBody, THead,
  Table, Td, TextInput, Th, Tr, buttonClasses,
} from "./ds";
import { STATUS_ICON, STATUS_SURFACE, STATUS_TONE, type StatusKey } from "./ds/Status";
import { CLASSIFICATION, ClassificationBadge, count, organisationItems, type QueueItem } from "./platformQueue";
import { useAction, usePageTitle, useSortedRows } from "./ui";

/* Super Admin: the customer-organisation registry (platform principal, ZA-TEN-001 WF-TEN-01 / ZA-IAM-001). The backend
   returns organisation-registry metadata only; customer evidence, facts and assurance are not reachable from this role
   (INV-14, IAM-REQ-024). The directory is Tenants & Entities (/organisations); one organisation's record is
   app/organisations/[id]/page.tsx, which uses the shared pieces exported here.

   Reads, exactly as before: the whole registry (GET /platform/organisations) for the headline counts, and the filtered
   registry (?status=&q=) only while a filter is applied. The status filter applies at once; the name search is sent
   only when submitted. Sorting is done in the browser on the rows already read, so it never makes a request. */

/* ------------------------------------------------------------------------------------------- the registry's shapes */
/** One legal entity, as GET /platform/organisations returns it (routers/platform.py `_view`). */
export type RegistryEntity = {
  entity_id: string; legal_name: string; country: string; registration_ref: string | null; home_cell_id: string; status: string;
  region: string | null;
};
/** A current Organisation Admin (role TENANT_OWNER). */
export type RegistryAdmin = { actor_id: string; display_name: string; email: string | null };
export type RegistryOrg = {
  tenant_id: string; legal_name: string; status: string; contracting_region: string; isolation_tier: string; created_at: string;
  entities: RegistryEntity[]; cells: string[]; admins: RegistryAdmin[];
};
/** One platform change to an organisation, from its audit trail (routers/platform.py `_detail`). */
export type AdminEvent = {
  action: string; actor_id: string | null; prior_state: string | null; new_state: string | null; reason: string | null;
  recorded_at: string; record_seq: number;
};
/** One organisation's record (GET /platform/organisations/{id}, and every change's response). */
export type RegistryRecord = RegistryOrg & { admin_events: AdminEvent[] };

/* ------------------------------------------------------------------------------------------------- shared pieces */
const PANEL = "rounded-panel border border-line bg-surface-raised/70 p-5 shadow-panel sm:p-6";  // as on the Action Queue

/** The organisation statuses the API uses (ACTIVE, SUSPENDED), in words. Any other code is shown as recorded. */
const ORG_STATUS: Record<string, { label: string; tone: StatusKey }> = {
  ACTIVE: { label: "Active", tone: "verified" },
  SUSPENDED: { label: "Suspended", tone: "critical" },
};
const STATUSES = [{ value: "", label: "Any status" }, { value: "ACTIVE", label: "Active" }, { value: "SUSPENDED", label: "Suspended" }];

/** An organisation's status as a labelled chip (icon and text, never colour alone); the recorded code is its title. */
export function OrganisationStatus({ status }: { status: string }) {
  const s = ORG_STATUS[status] ?? { label: status, tone: "unknown" as const };
  const Icon = STATUS_ICON[s.tone];
  return (
    <span title={`Status: ${status}`}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border px-2 py-0.5 type-caption font-semibold ${STATUS_TONE[s.tone]}`}>
      <Icon size={12} strokeWidth={2.5} aria-hidden className="shrink-0" /><span className="sr-only">Status: </span>{s.label}
    </span>
  );
}

/** The organisations the Action Queue lists as Action required, by organisation id. This is the queue's own rule
 *  (organisationItems in components/platformQueue.tsx: an ACTIVE organisation with no current Organisation Admin),
 *  read here rather than restated, so the registry and the queue cannot disagree. */
export function actionRequiredByOrganisation(orgs: RegistryOrg[]): Map<string, QueueItem> {
  const out = new Map<string, QueueItem>();
  for (const item of organisationItems(orgs)) if (item.classification === "ACTION_REQUIRED" && item.organisation) out.set(item.organisation.id, item);
  return out;
}

/** What this role can and cannot reach, as the Action Queue shows it (stacked on phones so the text keeps its width). */
export function RegistryScope({ children }: { children: ReactNode }) {
  return (
    <div className={`flex flex-col gap-2 rounded-card border border-l-4 border-line bg-surface-card/70 px-4 py-3 md:flex-row md:items-start md:gap-4 ${PLANES.platform.edge}`}>
      <span className="shrink-0"><PlaneBadge plane="platform" withWho /></span>
      <p className="min-w-0 flex-1 type-caption text-fg-secondary">{children}</p>
    </div>
  );
}

/** A titled panel in the Platform Command Center and Action Queue style. */
export function RegistrySection({ title, description, aside, children, className = "" }:
  { title: string; description?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={`${PANEL} ${className}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={headingId} className="text-[18px] font-semibold leading-7 text-fg">{title}</h2>
        {aside}
      </div>
      {description && <p className="mt-1 max-w-3xl type-caption text-fg-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** A page heading in the Platform Command Center and Action Queue style. A badge (for example a status chip) sits
 *  beside the heading, outside it, so it is not read as part of the page's name. */
export function RegistryHeading({ kicker, title, badge, children, actions }:
  { kicker: string; title: ReactNode; badge?: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  const H1 = "break-words text-[26px] font-bold leading-tight tracking-tight text-fg sm:text-[32px]";
  return (
    <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="min-w-0">
        <p className="type-label text-fg-muted">{kicker}</p>
        {badge ? (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className={`min-w-0 ${H1}`}>{title}</h1>{badge}
          </div>
        ) : <h1 className={`mt-1 ${H1}`}>{title}</h1>}
        {children && <div className="mt-2 max-w-3xl text-[15px] text-fg-secondary">{children}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/* --------------------------------------------------------------------------------------------------- the directory */
type SortKey = "name" | "status" | "admins" | "created";
const SORTS: { key: SortKey; label: string; sortValue: (o: RegistryOrg) => string | number }[] = [
  { key: "name", label: "Organisation", sortValue: (o) => o.legal_name.toLocaleLowerCase() },
  { key: "status", label: "Status", sortValue: (o) => o.status },
  { key: "admins", label: "Organisation Admins", sortValue: (o) => o.admins.length },
  { key: "created", label: "Created", sortValue: (o) => o.created_at },
];

/** The organisation registry. `title` names the page it is shown on (Tenants & Entities, /organisations). */
export function SuperAdminDashboard({ title = "Super Admin Dashboard" }: { title?: string } = {}) {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(title); return () => setTitle(""); }, [title, setTitle]);
  // The headline counts read the whole registry; the list applies the server-side filters (GET
  // /platform/organisations?status=&q=). The search text is sent only when submitted.
  const orgs = useApi<RegistryOrg[]>("/platform/organisations");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  if (status) qs.set("status", status);
  const query = qs.toString();
  const filtered = useApi<RegistryOrg[]>(query ? `/platform/organisations?${query}` : null);
  const [open, setOpen] = useState(false);
  const list = useMemo(() => orgs.data ?? [], [orgs.data]);
  const shown = useMemo(() => (query ? filtered.data ?? [] : list), [query, filtered.data, list]);
  const active = list.filter((o) => o.status === "ACTIVE").length;
  const flagged = useMemo(() => actionRequiredByOrganisation(shown), [shown]);
  const { sorted, sort, toggle } = useSortedRows(shown, SORTS);
  const dir = (key: SortKey) => (sort?.key === key ? sort.dir : null);
  const clear = () => { setText(""); setQ(""); setStatus(""); };

  const firstLoad = orgs.loading && !orgs.data;
  const loading = firstLoad || (!!query && filtered.loading && !filtered.data);
  const failure = !orgs.data && orgs.error ? { error: orgs.error, retry: orgs.reload }
    : query && !filtered.data && filtered.error ? { error: filtered.error, retry: filtered.reload } : null;

  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title={title}
        actions={<Button variant="primary" icon={Plus} onClick={() => setOpen(true)}>Create organisation</Button>}>
        <p>The customer organisations hosted on this cell: their status, legal entities, placement and Organisation Admins. You are not a member of any of them.</p>
      </RegistryHeading>

      <RegistryScope>
        <span className="font-semibold text-fg">You manage the organisation registry only.</span> Customer evidence, facts and assurance records are not
        accessible from this role, and every change you make is recorded in that organisation&apos;s audit trail.
      </RegistryScope>

      <ul aria-label="Registry summary" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <li><KpiCard label="Organisations" icon={Building2} loading={firstLoad} value={orgs.data ? list.length : "—"} /></li>
        <li><KpiCard label="Active" icon={CircleCheck} tone="verified" loading={firstLoad} value={orgs.data ? active : "—"} /></li>
        <li><KpiCard label="Suspended" icon={CirclePause} tone={orgs.data && list.length - active ? "critical" : "neutral"} loading={firstLoad}
          value={orgs.data ? list.length - active : "—"} /></li>
        <li><KpiCard label="Legal entities" icon={Landmark} loading={firstLoad} value={orgs.data ? list.reduce((n, o) => n + o.entities.length, 0) : "—"} /></li>
      </ul>

      <RegistrySection title="Customer organisations"
        description={<>
          Open an organisation to edit its name, suspend or reactivate it, or appoint an Organisation Admin.
          {flagged.size > 0 && <> <span className="font-semibold text-fg">{CLASSIFICATION.ACTION_REQUIRED.label}</span> marks an active organisation
            without an Organisation Admin, which the Action Queue lists: only the Super Admin can appoint one.</>}
        </>}
        aside={!loading && !failure && list.length > 0 && (
          <p className="type-caption text-fg-muted" aria-live="polite">
            {query ? `${count(shown.length, "organisation")} match, of ${list.length}` : count(list.length, "organisation")}
          </p>
        )}>
        {list.length > 0 && (
          <form role="search" aria-label="Filter organisations" className="flex flex-col gap-3 md:flex-row md:items-center"
            onSubmit={(e) => { e.preventDefault(); setQ(text.trim()); }}>
            <div className="min-w-0 md:flex-1">
              <SearchInput aria-label="Organisation name" placeholder="Search organisation name" maxLength={100} value={text}
                onChange={(e) => setText(e.target.value)} />
            </div>
            <div className="md:w-48"><Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={STATUSES} /></div>
            <div className="flex gap-2">
              <Button type="submit" variant="secondary" icon={Search}>Search</Button>
              {query && <Button variant="ghost" onClick={clear}>Clear</Button>}
            </div>
          </form>
        )}

        <div className={list.length > 0 ? "mt-4" : ""}>
          {loading ? <DirectoryLoading /> : failure ? (
            <ErrorState error={failure.error} title="The organisation registry could not be loaded" onRetry={() => failure.retry()} />
          ) : list.length === 0 ? (
            <EmptyState icon={Building2} title="No organisations yet" description="Create the first customer organisation to get started." />
          ) : shown.length === 0 ? (
            <EmptyState icon={Building2} title="No organisations match" description="Change the name search or the status filter."
              action={<Button size="sm" variant="secondary" onClick={clear}>Clear filters</Button>} />
          ) : (
            <>
              {/* Below 1280px the table's six columns do not fit beside the sidebar without scrolling sideways, so the list
                  is stacked cards there, sorted by the same state as the table's column buttons. */}
              <div role="group" aria-label="Sort organisations" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
                <span className="type-caption text-fg-muted">Sort by</span>
                {SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
              </div>
              <ul aria-label="Customer organisations" className="space-y-3 xl:hidden">
                {sorted.map((o) => <li key={o.tenant_id}><OrganisationCard org={o} flag={flagged.get(o.tenant_id)} /></li>)}
              </ul>
              <div className="hidden xl:block">
                <Table caption="Customer organisations. The Organisation, Status, Organisation Admins and Created column headings sort the list." minWidth="36rem">
                  <THead>
                    <tr>
                      <Th sort={dir("name")} onSort={() => toggle("name")}>Organisation</Th>
                      <Th sort={dir("status")} onSort={() => toggle("status")}>Status</Th>
                      <Th>Legal entities</Th>
                      <Th>Cell · tier</Th>
                      <Th sort={dir("admins")} onSort={() => toggle("admins")}>Organisation Admins</Th>
                      <Th sort={dir("created")} onSort={() => toggle("created")}>Created</Th>
                    </tr>
                  </THead>
                  <TBody>
                    {sorted.map((o) => (
                      <Tr key={o.tenant_id}>
                        <Td className="align-top"><OrganisationLink org={o} /></Td>
                        <Td className="align-top"><OrganisationStatus status={o.status} /></Td>
                        <Td className="align-top"><Entities org={o} /></Td>
                        <Td className="align-top"><Placement org={o} /></Td>
                        <Td className="align-top"><AdminSummary org={o} flag={flagged.get(o.tenant_id)} /></Td>
                        <Td className="align-top whitespace-nowrap">{fmtDate(o.created_at)}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            </>
          )}
        </div>
      </RegistrySection>

      {open && <CreateOrganisation onClose={() => setOpen(false)} onCreated={() => { orgs.reload(); if (query) filtered.reload(); }} />}
    </div>
  );
}

function OrganisationLink({ org }: { org: RegistryOrg }) {
  return (
    <>
      <Link href={`/organisations/${org.tenant_id}`} className="break-words rounded font-semibold text-accent-fg underline-offset-4 hover:underline">{org.legal_name}</Link>
      <div className="break-all type-code text-fg-muted">{org.tenant_id}</div>
    </>
  );
}

/** Cell and isolation tier, each kept whole (a cell id such as eu-west-cell-a must not break at its hyphens). */
function Placement({ org }: { org: RegistryOrg }) {
  return (
    <div className="type-code">
      {(org.cells.length ? org.cells : ["—"]).map((c) => <div key={c} className="whitespace-nowrap">{c}</div>)}
      <div className="whitespace-nowrap text-fg-muted">{org.isolation_tier}</div>
    </div>
  );
}

function Entities({ org }: { org: RegistryOrg }) {
  if (org.entities.length === 0) return <span className="text-fg-muted">—</span>;
  return <ul className="space-y-0.5">{org.entities.map((e) => <li key={e.entity_id} className="break-words">{e.legal_name} <span className="type-code text-fg-muted">{e.country}</span></li>)}</ul>;
}

/** The Organisation Admins in the directory: how many and their names. Their email addresses are shown on the
 *  organisation's own record only. An organisation the Action Queue lists for this carries its classification. */
function AdminSummary({ org, flag }: { org: RegistryOrg; flag?: QueueItem }) {
  if (org.admins.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {flag && <ClassificationBadge classification={flag.classification} />}
        <span className={flag ? "type-caption font-medium text-fg-secondary" : "text-fg-muted"}>None appointed</span>
      </div>
    );
  }
  return (
    <div>
      <p className="type-caption font-semibold tabular-nums text-fg-secondary">{count(org.admins.length, "appointed", "appointed")}</p>
      <ul className="mt-0.5 space-y-0.5">{org.admins.map((a) => <li key={a.actor_id} className="break-words">{a.display_name}</li>)}</ul>
    </div>
  );
}

/** One organisation below 1280px: the table's columns as a stacked card. */
function OrganisationCard({ org, flag }: { org: RegistryOrg; flag?: QueueItem }) {
  return (
    <article aria-label={org.legal_name} className="rounded-card border border-line bg-surface-card p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><OrganisationLink org={org} /></div>
        <span className="shrink-0"><OrganisationStatus status={org.status} /></span>
      </div>
      <DescriptionList columns={2} className="mt-3" items={[
        ["Organisation Admins", <AdminSummary key="a" org={org} flag={flag} />],
        ["Legal entities", <Entities key="e" org={org} />],
        ["Cell · tier", <Placement key="c" org={org} />],
        ["Created", fmtDate(org.created_at)],
      ]} />
    </article>
  );
}

/** A sort button for the stacked list, driving the same sort as the table's column buttons. */
export function SortChip({ label, dir, onSort }: { label: string; dir: "asc" | "desc" | null; onSort: () => void }) {
  const Icon = dir === "asc" ? ArrowUp : dir === "desc" ? ArrowDown : ArrowUpDown;
  return (
    <button type="button" onClick={onSort} aria-pressed={!!dir}
      className={`inline-flex min-h-8 items-center gap-1 rounded-chip border px-2.5 type-caption font-semibold transition-colors ${dir
        ? "border-accent/50 bg-accent/10 text-accent-fg" : "border-line bg-surface-control text-fg-secondary hover:text-fg"}`}>
      {label}<Icon size={12} aria-hidden className={dir ? "" : "opacity-50"} />
      {dir && <span className="sr-only">, sorted {dir === "asc" ? "ascending" : "descending"}</span>}
    </button>
  );
}

function DirectoryLoading() {
  return (
    <div role="status" aria-label="Loading the organisation registry" className="space-y-3">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="rounded-card border border-line bg-surface-card p-4">
          <Skeleton className="h-4 w-56" />
          <Skeleton className="mt-2 h-3.5 w-3/4" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- create organisation */
function CreateOrganisation({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [f, setF] = useState({ legal_name: "", entity_name: "", country: "", registration_ref: "", admin_name: "", admin_email: "" });
  const set = (k: keyof typeof f) => (e: ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const a = useAction();
  const formId = useId();
  const created = a.result as (RegistryRecord & { initial_admin_password?: string }) | null;
  const ready = f.legal_name.trim().length >= 2 && f.entity_name.trim().length >= 2 && /^[A-Za-z]{2}$/.test(f.country.trim())
    && f.admin_name.trim().length >= 2 && /.+@.+\..+/.test(f.admin_email.trim());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready || a.busy) return;
    void a.run(async () => {
      const r = await api("/platform/organisations", { method: "POST", json: {
        legal_name: f.legal_name.trim(),
        entity: { legal_name: f.entity_name.trim(), country: f.country.trim().toUpperCase(), registration_ref: f.registration_ref.trim() || null },
        admin: { display_name: f.admin_name.trim(), email: f.admin_email.trim() },
      } });
      onCreated();
      return r;
    });
  };
  return (
    <Modal open onClose={onClose} title={created ? "Organisation created" : "Create organisation"}
      description={created ? undefined : "The server assigns the cell and isolation tier. The Organisation Admin receives a one-time temporary password and must change it at first sign-in."}
      footer={created ? (
        <>
          <Link href={`/organisations/${created.tenant_id}`} className={buttonClasses({ variant: "secondary" })}>Open organisation</Link>
          <Button variant="primary" onClick={onClose}>Done</Button>
        </>
      ) : (
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={formId} variant="primary" disabled={!ready} loading={a.busy}>{a.busy ? "Creating…" : "Create organisation"}</Button>
        </>
      )}>
      {created ? (
        <>
          <p className="mb-3"><b className="text-fg">{created.legal_name}</b> is active in {created.cells.join(", ")} ({created.isolation_tier}).</p>
          <OneTimePassword email={created.admins[0]?.email ?? undefined} password={created.initial_admin_password} />
        </>
      ) : (
        <form id={formId} onSubmit={submit} className="space-y-4">
          {!!a.error && <Alert tone="critical" live="assertive">{errText(a.error)}</Alert>}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Organisation legal name" required className="sm:col-span-2"><TextInput value={f.legal_name} maxLength={200} onChange={set("legal_name")} /></Field>
            <Field label="First legal entity" required><TextInput value={f.entity_name} maxLength={200} onChange={set("entity_name")} /></Field>
            <Field label="Country (ISO 3166 alpha-2)" required><TextInput className="uppercase" maxLength={2} value={f.country} onChange={set("country")} placeholder="GB" /></Field>
            <Field label="Registration reference" optional className="sm:col-span-2"><TextInput value={f.registration_ref} maxLength={100} onChange={set("registration_ref")} /></Field>
            <Field label="Organisation Admin name" required><TextInput value={f.admin_name} maxLength={120} onChange={set("admin_name")} /></Field>
            <Field label="Organisation Admin email" required><TextInput type="email" value={f.admin_email} onChange={set("admin_email")} /></Field>
          </div>
        </form>
      )}
    </Modal>
  );
}

/** A temporary password returned once by the backend: shown here only, never stored, logged or toasted. */
export function OneTimePassword({ email, password }: { email?: string; password?: string }) {
  const [copied, setCopied] = useState(false);
  if (!password) return null;
  return (
    <div className={`rounded-card border p-3 ${STATUS_SURFACE.warning}`}>
      <p className="type-caption font-semibold text-fg">One-time temporary password for {email}</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="min-w-0 flex-1 select-all break-all rounded-control border border-line bg-surface-card px-2 py-1 type-code text-fg">{password}</code>
        <Button size="sm" variant="secondary" icon={Copy} onClick={async () => { try { await navigator.clipboard.writeText(password); setCopied(true); } catch {} }}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="mt-2 type-caption text-fg-secondary">It is shown once and cannot be retrieved later. Hand it over through a secure channel; the admin must replace it at first sign-in.</p>
    </div>
  );
}
