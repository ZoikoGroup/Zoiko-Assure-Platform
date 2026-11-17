"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Activity, Building2, CircleOff, Clock, Layers, RefreshCw, Settings2 } from "lucide-react";
import { api, errText } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Alert, Button, EmptyState, ErrorState, Skeleton, StateBadge, TBody, THead, Table, Td, Th, Tr, DescriptionList } from "./ds";
import {
  OrganisationStatus, RegistryHeading, RegistryScope, RegistrySection, SortChip, type RegistryEntity, type RegistryOrg,
} from "./PlatformAdmin";
import { count, fmtUtcSeconds } from "./platformQueue";
import { usePageTitle, useSortedRows } from "./ui";

/* Tenant Metadata: a registry-wide, read-only summary of what the platform records about every customer organisation
   (placement, residency and lifecycle), for the Zoiko Super Admin (Platform Control Plane).

   One source only: the organisation registry, GET /platform/organisations (routers/platform.py `_view`), the read the
   Super Admin role is served (manage_organisations). It is read once when the page opens and once per Refresh or Try
   again, never on a timer and never retried on its own, because each read is recorded in the platform's
   administrative audit trail (platform.organisation_registry_read). No organisation's own record is requested
   (GET /platform/organisations/{id} records a view in that organisation's audit trail), and nothing is changed: editing,
   suspending and appointing admins stay in Tenants & Entities, which each organisation name links to.

   Data minimisation: the registry response also names each organisation's admins; they are dropped on receipt and
   never kept or shown here, and no administrative events are read. Every figure is counted from the registry rows. */
const TITLE = "Tenant Metadata";

/* ----------------------------------------------------------------------------------------------- registry rows */
/** The registry fields this page uses (the shared registry shapes, admins and registration references left out). */
type Entity = Pick<RegistryEntity, "entity_id" | "legal_name" | "country" | "home_cell_id" | "status" | "region">;
type Org = Pick<RegistryOrg, "tenant_id" | "legal_name" | "status" | "contracting_region" | "isolation_tier" | "created_at" | "cells">
  & { entities: Entity[] };

const text = (v: unknown) => (typeof v === "string" ? v.trim() : "");

function toEntity(x: unknown): Entity | null {
  if (!x || typeof x !== "object") return null;
  const e = x as Record<string, unknown>;
  const entity_id = text(e.entity_id);
  if (!entity_id) return null;
  return { entity_id, legal_name: text(e.legal_name), country: text(e.country), home_cell_id: text(e.home_cell_id),
    status: text(e.status), region: text(e.region) || null };
}

/** One registry row, keeping only the fields above. Rows without an organisation ID cannot be linked and are counted
 *  as skipped, never guessed at. */
function toOrg(x: unknown): { org: Org | null; skippedEntities: number } {
  if (!x || typeof x !== "object") return { org: null, skippedEntities: 0 };
  const o = x as Record<string, unknown>;
  const tenant_id = text(o.tenant_id);
  if (!tenant_id) return { org: null, skippedEntities: 0 };
  const raw = Array.isArray(o.entities) ? o.entities : [];
  const entities = raw.map(toEntity).filter((e): e is Entity => e !== null);
  const cells = Array.isArray(o.cells) ? [...new Set(o.cells.map(text).filter(Boolean))] : [];
  return {
    org: { tenant_id, legal_name: text(o.legal_name), status: text(o.status), contracting_region: text(o.contracting_region),
      isolation_tier: text(o.isolation_tier), created_at: text(o.created_at), cells, entities },
    skippedEntities: raw.length - entities.length,
  };
}

type Registry = { orgs: Org[]; skippedOrgs: number; skippedEntities: number };

function toRegistry(body: unknown): Registry {
  if (!Array.isArray(body)) throw new Error("The organisation registry answered in an unexpected format, so nothing can be shown.");
  let skippedOrgs = 0, skippedEntities = 0;
  const orgs: Org[] = [];
  for (const row of body) {
    const r = toOrg(row);
    if (r.org) orgs.push(r.org); else skippedOrgs++;
    skippedEntities += r.skippedEntities;
  }
  return { orgs, skippedOrgs, skippedEntities };
}

/** The registry, read on mount and on each explicit request. A read that fails keeps the last good rows on screen. */
function useRegistry() {
  const [data, setData] = useState<Registry | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [readAt, setReadAt] = useState<string | null>(null);
  const seq = useRef(0);

  const read = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      // A literal path, so the backend's frontend-contract test (test_sidebar_integration.py) checks this call.
      const registry = toRegistry(await api<unknown>("/platform/organisations"));
      if (id !== seq.current) return;  // superseded, or the page closed
      setData(registry);
      setReadAt(new Date().toISOString());
    } catch (e) {
      if (id === seq.current) setError(e);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void read();
    return () => { seq.current++; };
  }, [read]);

  return { data, error, loading, readAt, read };
}

/* -------------------------------------------------------------------------------------------------- formatting */
const NOT_RECORDED = "Not recorded";

/** A recorded value, or a muted "Not recorded" when the registry holds none. `code` shows identifiers in code type. */
function Value({ v, code = false, nowrap = false }: { v: string | null | undefined; code?: boolean; nowrap?: boolean }) {
  if (!v) return <span className="text-fg-muted">{NOT_RECORDED}</span>;
  return code ? <code className={`type-code ${nowrap ? "whitespace-nowrap" : "break-all"}`}>{v}</code> : <span className="break-words">{v}</span>;
}

/** Counts of each value, most frequent first, then alphabetical. Empty values are counted as "Not recorded". */
function tally(values: string[]): { key: string; n: number }[] {
  const m = new Map<string, number>();
  for (const v of values) m.set(v, (m.get(v) ?? 0) + 1);
  return [...m.entries()].map(([key, n]) => ({ key, n })).sort((a, b) => b.n - a.n || a.key.localeCompare(b.key));
}

const orgHref = (tenantId: string) => `/organisations/${encodeURIComponent(tenantId)}`;

/* ------------------------------------------------------------------------------------------------------- page */
export function PlatformTenantMetadata() {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  const { data, error, loading, readAt, read } = useRegistry();
  const orgs = useMemo(() => data?.orgs ?? [], [data]);
  const entities = useMemo(() => orgs.flatMap((o) => o.entities.map((e) => ({ ...e, org: o }))), [orgs]);
  const firstLoad = loading && !data;

  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title={TITLE}
        actions={<div className="flex flex-wrap items-center gap-x-4 gap-y-2 xl:justify-end">
          <p className="type-caption text-fg-muted">
            Last read:{" "}
            {readAt
              ? <time dateTime={readAt} className="font-semibold tabular-nums text-fg-secondary">{fmtUtcSeconds(readAt)}</time>
              : <span className="font-semibold text-fg-secondary">{loading ? "reading…" : "not yet"}</span>}
          </p>
          <Button variant="secondary" icon={RefreshCw} loading={loading} onClick={() => void read()}>{loading ? "Reading…" : "Refresh"}</Button>
        </div>}>
        <p>Where every customer organisation and legal entity is placed, and in what lifecycle state, from the organisation registry.</p>
      </RegistryHeading>
      <p role="status" aria-live="polite" className="sr-only">
        {loading ? "" : error ? "The organisation registry could not be read." : data ? `Registry read: ${count(orgs.length, "organisation")}, ${count(entities.length, "legal entity", "legal entities")}.` : ""}
      </p>

      <RegistryScope>
        <span className="font-semibold text-fg">Registry metadata only, read-only.</span> Every read of the organisation registry, including each Refresh,
        is recorded in the platform&apos;s administrative audit trail. This page reads it once when it opens and again only when you ask. Changes to an
        organisation are made in Tenants &amp; Entities.
      </RegistryScope>

      {firstLoad ? <MetadataLoading /> : !data ? (
        <RegistrySection title="Organisation registry">
          <ErrorState error={error} title="The organisation registry could not be read" onRetry={() => void read()} />
        </RegistrySection>
      ) : (
        <div aria-busy={loading || undefined} className="space-y-6">
          {!!error && (
            <Alert tone="critical" live="assertive" title="The registry could not be read again">
              {errText(error)} The figures below are from the last successful read{readAt ? `, ${fmtUtcSeconds(readAt)}` : ""}.
            </Alert>
          )}
          {(data.skippedOrgs > 0 || data.skippedEntities > 0) && (
            <Alert tone="warning" title="Some registry rows could not be shown">
              {[data.skippedOrgs > 0 && `${count(data.skippedOrgs, "organisation")} without an organisation ID`,
                data.skippedEntities > 0 && `${count(data.skippedEntities, "legal entity", "legal entities")} without an entity ID`].filter(Boolean).join(" and ")}{" "}
              {data.skippedOrgs + data.skippedEntities === 1 ? "was" : "were"} left out of the figures and tables.
            </Alert>
          )}
          {orgs.length === 0 ? (
            <RegistrySection title="Organisation registry">
              <EmptyState icon={Building2} title="No organisations registered"
                description={<>The registry holds no customer organisations yet. Organisations are created in <Link href="/organisations" className="font-semibold text-accent-fg hover:underline">Tenants &amp; Entities</Link>.</>} />
            </RegistrySection>
          ) : (
            <>
              <PlacementSummary orgs={orgs} entities={entities} />
              <OrganisationTable orgs={orgs} />
              <EntityTable entities={entities} />
            </>
          )}
        </div>
      )}

      <NotStored />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------- placement summary */
type Row = Entity & { org: Org };

function PlacementSummary({ orgs, entities }: { orgs: Org[]; entities: Row[] }) {
  return (
    <RegistrySection title="Placement summary"
      description={`Counted from the registry: ${count(orgs.length, "organisation")} and ${count(entities.length, "legal entity", "legal entities")}. Placement is assigned by the server when an organisation or entity is registered.`}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Tally title="Organisations by contracting region" rows={tally(orgs.map((o) => o.contracting_region))} unit="organisation" />
        <Tally title="Organisations by isolation tier" rows={tally(orgs.map((o) => o.isolation_tier))} unit="organisation" code />
        <Tally title="Organisations by status" rows={tally(orgs.map((o) => o.status))} unit="organisation"
          render={(k) => (k ? <OrganisationStatus status={k} /> : <span className="text-fg-muted">{NOT_RECORDED}</span>)} />
        <Tally title="Legal entities by home cell" rows={tally(entities.map((e) => e.home_cell_id))} unit="legal entity" units="legal entities" code />
        <Tally title="Legal entities by region" rows={tally(entities.map((e) => e.region ?? ""))} unit="legal entity" units="legal entities" />
        <Tally title="Legal entities by country" rows={tally(entities.map((e) => e.country))} unit="legal entity" units="legal entities" code />
      </div>
    </RegistrySection>
  );
}

function Tally({ title, rows, unit, units, code = false, render }:
  { title: string; rows: { key: string; n: number }[]; unit: string; units?: string; code?: boolean; render?: (key: string) => ReactNode }) {
  return (
    <div className="rounded-card border border-line bg-surface-card p-4 shadow-card">
      <h3 className="type-secondary font-semibold text-fg">{title}</h3>
      {rows.length === 0 ? <p className="mt-2 type-caption text-fg-muted">None recorded.</p> : (
        <ul className="mt-2 divide-y divide-line">
          {rows.map((r) => (
            <li key={r.key || "-"} className="flex items-center justify-between gap-3 py-1.5">
              <span className="min-w-0">{render ? render(r.key) : <Value v={r.key} code={code} />}</span>
              <span className="shrink-0 type-secondary font-semibold tabular-nums text-fg">
                {r.n}<span className="sr-only"> {r.n === 1 ? unit : units ?? `${unit}s`}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------- organisation metadata */
type OrgSortKey = "name" | "status" | "region" | "tier" | "entities" | "created";
const ORG_SORTS: { key: OrgSortKey; label: string; sortValue: (o: Org) => string | number }[] = [
  { key: "name", label: "Organisation", sortValue: (o) => o.legal_name.toLocaleLowerCase() },
  { key: "status", label: "Status", sortValue: (o) => o.status },
  { key: "region", label: "Contracting region", sortValue: (o) => o.contracting_region.toLocaleLowerCase() },
  { key: "tier", label: "Isolation tier", sortValue: (o) => o.isolation_tier },
  { key: "entities", label: "Entities", sortValue: (o) => o.entities.length },
  { key: "created", label: "Created", sortValue: (o) => o.created_at },
];

function OrgName({ o }: { o: Org }) {
  return (
    <Link href={orgHref(o.tenant_id)} title="Open this organisation in Tenants & Entities"
      className="break-words rounded font-semibold text-accent-fg underline-offset-4 hover:underline">
      {o.legal_name || <span className="font-normal text-fg-muted">Unnamed organisation</span>}
    </Link>
  );
}

function Cells({ cells }: { cells: string[] }) {
  if (cells.length === 0) return <span className="text-fg-muted">{NOT_RECORDED}</span>;
  return <div className="type-code">{cells.map((c) => <div key={c} className="whitespace-nowrap">{c}</div>)}</div>;
}

function OrganisationTable({ orgs }: { orgs: Org[] }) {
  const { sorted, sort, toggle } = useSortedRows(orgs, ORG_SORTS);
  const dir = (key: OrgSortKey) => (sort?.key === key ? sort.dir : null);
  return (
    <RegistrySection title="Organisation metadata" aside={<p className="type-caption text-fg-muted">{count(orgs.length, "organisation")}</p>}
      description="Each organisation's registry record. Open an organisation to see its legal entities and history, or to change it.">
      {/* As in Tenants & Entities: below 1280px the columns do not fit beside the sidebar, so the rows are stacked cards
          there, sorted by the same state as the table's column buttons. */}
      <div role="group" aria-label="Sort organisations" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
        <span className="type-caption text-fg-muted">Sort by</span>
        {ORG_SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
      </div>
      <ul aria-label="Organisation metadata" className="space-y-3 xl:hidden">
        {sorted.map((o) => (
          <li key={o.tenant_id}>
            <article aria-label={o.legal_name || o.tenant_id} className="rounded-card border border-line bg-surface-card p-4 shadow-card">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><OrgName o={o} /><div className="break-all type-code text-fg-muted">{o.tenant_id}</div></div>
                <span className="shrink-0">{o.status ? <OrganisationStatus status={o.status} /> : <span className="type-caption text-fg-muted">{NOT_RECORDED}</span>}</span>
              </div>
              <DescriptionList columns={2} className="mt-3" items={[
                ["Contracting region", <Value key="r" v={o.contracting_region} />],
                ["Isolation tier", <Value key="t" v={o.isolation_tier} code nowrap />],
                ["Cells", <Cells key="c" cells={o.cells} />],
                ["Legal entities", <span key="e" className="tabular-nums">{o.entities.length}</span>],
                ["Created", fmtDate(o.created_at)],
              ]} />
            </article>
          </li>
        ))}
      </ul>
      <div className="hidden xl:block">
        <Table caption="Organisation metadata. The Organisation, Status, Contracting region, Isolation tier, Entities and Created column headings sort the list." minWidth="52rem">
          <THead>
            <tr>
              {/* Multi-word headings may wrap, so the table fits beside the sidebar at 1280px without scrolling. */}
              <Th sort={dir("name")} onSort={() => toggle("name")}><span className="whitespace-normal">Organisation · tenant ID</span></Th>
              <Th sort={dir("status")} onSort={() => toggle("status")}>Status</Th>
              <Th sort={dir("region")} onSort={() => toggle("region")}><span className="whitespace-normal">Contracting region</span></Th>
              <Th sort={dir("tier")} onSort={() => toggle("tier")}><span className="whitespace-normal">Isolation tier</span></Th>
              <Th>Cells</Th>
              <Th align="right" sort={dir("entities")} onSort={() => toggle("entities")}>Entities</Th>
              <Th sort={dir("created")} onSort={() => toggle("created")}>Created</Th>
            </tr>
          </THead>
          <TBody>
            {sorted.map((o) => (
              <Tr key={o.tenant_id}>
                <Td className="align-top"><OrgName o={o} /><div className="break-all type-code text-fg-muted">{o.tenant_id}</div></Td>
                <Td className="align-top">{o.status ? <OrganisationStatus status={o.status} /> : <span className="text-fg-muted">{NOT_RECORDED}</span>}</Td>
                <Td className="align-top"><Value v={o.contracting_region} /></Td>
                <Td className="align-top"><Value v={o.isolation_tier} code nowrap /></Td>
                <Td className="align-top"><Cells cells={o.cells} /></Td>
                <Td align="right" className="align-top tabular-nums">{o.entities.length}</Td>
                <Td className="align-top whitespace-nowrap">{fmtDate(o.created_at)}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
    </RegistrySection>
  );
}

/* ------------------------------------------------------------------------------------------ legal entity residency */
type EntitySortKey = "entity" | "organisation" | "country" | "cell" | "region" | "status";
const ENTITY_SORTS: { key: EntitySortKey; label: string; sortValue: (e: Row) => string }[] = [
  { key: "entity", label: "Entity", sortValue: (e) => e.legal_name.toLocaleLowerCase() },
  { key: "organisation", label: "Organisation", sortValue: (e) => e.org.legal_name.toLocaleLowerCase() },
  { key: "country", label: "Country", sortValue: (e) => e.country },
  { key: "cell", label: "Home cell", sortValue: (e) => e.home_cell_id },
  { key: "region", label: "Region", sortValue: (e) => (e.region ?? "").toLocaleLowerCase() },
  { key: "status", label: "Status", sortValue: (e) => e.status },
];

function EntityStatus({ status }: { status: string }) {
  return status ? <StateBadge code={status} size="sm" /> : <span className="type-caption text-fg-muted">{NOT_RECORDED}</span>;
}

function EntityTable({ entities }: { entities: Row[] }) {
  const { sorted, sort, toggle } = useSortedRows(entities, ENTITY_SORTS);
  const dir = (key: EntitySortKey) => (sort?.key === key ? sort.dir : null);
  return (
    <RegistrySection title="Legal entity residency" aside={<p className="type-caption text-fg-muted">{count(entities.length, "legal entity", "legal entities")}</p>}
      description="Where each legal entity's records are held: its home cell and that cell's region. Moving an entity between cells is not supported.">
      {entities.length === 0 ? <EmptyState compact icon={Building2} title="No legal entities recorded" description="The registry lists no legal entities for these organisations." /> : (
        <>
          <div role="group" aria-label="Sort legal entities" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
            <span className="type-caption text-fg-muted">Sort by</span>
            {ENTITY_SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
          </div>
          <ul aria-label="Legal entity residency" className="space-y-3 xl:hidden">
            {sorted.map((e) => (
              <li key={e.entity_id}>
                <article aria-label={e.legal_name || e.entity_id} className="rounded-card border border-line bg-surface-card p-4 shadow-card">
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 break-words type-secondary font-semibold text-fg">{e.legal_name || <span className="font-normal text-fg-muted">Unnamed entity</span>}</p>
                    <span className="shrink-0"><EntityStatus status={e.status} /></span>
                  </div>
                  <DescriptionList columns={2} className="mt-3" items={[
                    ["Organisation", <OrgName key="o" o={e.org} />],
                    ["Country", <Value key="c" v={e.country} code nowrap />],
                    ["Home cell", <Value key="h" v={e.home_cell_id} code nowrap />],
                    ["Region", <Value key="r" v={e.region} />],
                  ]} />
                </article>
              </li>
            ))}
          </ul>
          <div className="hidden xl:block">
            <Table caption="Legal entity residency. Every column heading sorts the list." minWidth="48rem">
              <THead>
                <tr>
                  <Th sort={dir("entity")} onSort={() => toggle("entity")}>Entity</Th>
                  <Th sort={dir("organisation")} onSort={() => toggle("organisation")}>Organisation</Th>
                  <Th sort={dir("country")} onSort={() => toggle("country")}>Country</Th>
                  <Th sort={dir("cell")} onSort={() => toggle("cell")}>Home cell</Th>
                  <Th sort={dir("region")} onSort={() => toggle("region")}>Region</Th>
                  <Th sort={dir("status")} onSort={() => toggle("status")}>Status</Th>
                </tr>
              </THead>
              <TBody>
                {sorted.map((e) => (
                  <Tr key={e.entity_id}>
                    <Td className="align-top"><span className="break-words font-medium">{e.legal_name || <span className="font-normal text-fg-muted">Unnamed entity</span>}</span></Td>
                    <Td className="align-top"><OrgName o={e.org} /></Td>
                    <Td className="align-top"><Value v={e.country} code nowrap /></Td>
                    <Td className="align-top"><Value v={e.home_cell_id} code nowrap /></Td>
                    <Td className="align-top"><Value v={e.region} /></Td>
                    <Td className="align-top"><EntityStatus status={e.status} /></Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        </>
      )}
    </RegistrySection>
  );
}

/* ------------------------------------------------------------------------------------------------ not stored */
const NOT_STORED: { title: string; icon: LucideIcon; text: string }[] = [
  { title: "Tenant settings and configuration", icon: Settings2, text: "The registry holds no per-organisation settings or configuration." },
  { title: "Usage", icon: Activity, text: "No usage, volume or activity figures are recorded for organisations." },
  { title: "Last-modified times", icon: Clock, text: "Organisation records carry a creation date only, not when they last changed." },
  { title: "Content-release versions", icon: Layers, text: "Which content release each organisation is on is not available to this role." },
];

function NotStored() {
  return (
    <RegistrySection title="Not stored"
      aside={<span className="inline-flex items-center gap-1 rounded-chip border border-line bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-muted"><CircleOff size={11} aria-hidden />Not available</span>}
      description="Tenant metadata the current backend does not provide. These are gaps, not values: nothing here is live data or a sample.">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {NOT_STORED.map((n) => (
          <li key={n.title} className="flex gap-3 rounded-card border border-dashed border-line-strong p-4">
            <n.icon size={17} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <p className="type-secondary font-semibold text-fg-secondary">{n.title}</p>
              <p className="mt-0.5 type-caption text-fg-muted">{n.text}</p>
            </div>
          </li>
        ))}
      </ul>
    </RegistrySection>
  );
}

/* --------------------------------------------------------------------------------------------------- loading */
function MetadataLoading() {
  return (
    <div role="status" aria-label="Reading the organisation registry" className="space-y-6">
      <div className="rounded-panel border border-line bg-surface-card p-5 sm:p-6">
        <Skeleton className="h-5 w-48" />
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="space-y-2 rounded-card border border-line bg-surface-card p-4">
              <Skeleton className="h-4 w-40" /><Skeleton className="h-3.5 w-full" /><Skeleton className="h-3.5 w-2/3" />
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-panel border border-line bg-surface-card p-5 sm:p-6">
        <Skeleton className="h-5 w-56" />
        {[0, 1, 2].map((i) => <Skeleton key={i} className="mt-4 h-10 w-full" />)}
      </div>
    </div>
  );
}
