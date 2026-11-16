"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw, SearchX } from "lucide-react";
import {
  Button, DescriptionList, EmptyState, Field, Pagination, SearchInput, Select, TBody, THead, Table, Td, Th, Tr,
} from "@/components/ds";
import { SortChip } from "@/components/PlatformAdmin";
import { count } from "@/components/platformQueue";
import { Missing, fmtTimeUtc } from "@/components/platformDemo";
import { fmtDate } from "@/lib/format";
import { AccessBadge, ApprovalBadge, LocalTag } from "./badges";
import { ACCESS, APPROVAL, hoursLabel, isActive, orgName, type Row } from "./model";
import { APPROVAL_STATUSES, type ApprovalStatus, type JitRole } from "./types";

/* The request inventory: search, filters, sorting and pagination on the records already loaded. As elsewhere in the
   Super Admin, rows are stacked cards below 1280px and a table from 1280px, both driven by the same sort state. */

const PAGE_SIZE = 8;

type SortKey = "requested" | "requester" | "organisation" | "approval" | "expiry";
type Sort = { key: SortKey; dir: "asc" | "desc" };
const DEFAULT_SORT: Sort = { key: "requested", dir: "desc" };
/** First direction when a column is chosen: newest request first, names A to Z, pending first, soonest expiry first. */
const FIRST_DIR: Record<SortKey, Sort["dir"]> = { requested: "desc", requester: "asc", organisation: "asc", approval: "asc", expiry: "asc" };
const SORTS: { key: SortKey; label: string }[] = [
  { key: "requested", label: "Requested" }, { key: "requester", label: "Requester" }, { key: "organisation", label: "Organisation" },
  { key: "approval", label: "Approval" }, { key: "expiry", label: "Expiry" },
];

/** "ACTIVE" here includes sessions expiring soon, as on the summary cards and the Sessions tab. */
type AccessFilter = "" | "AWAITING_APPROVAL" | "ACTIVE" | "EXPIRING_SOON" | "EXPIRED" | "REVOKED" | "NOT_GRANTED";
const ACCESS_FILTERS: { value: AccessFilter; label: string }[] = [
  { value: "", label: "All access statuses" },
  { value: "AWAITING_APPROVAL", label: ACCESS.AWAITING_APPROVAL.label },
  { value: "ACTIVE", label: "Active (including expiring soon)" },
  { value: "EXPIRING_SOON", label: ACCESS.EXPIRING_SOON.label },
  { value: "EXPIRED", label: ACCESS.EXPIRED.label },
  { value: "REVOKED", label: ACCESS.REVOKED.label },
  { value: "NOT_GRANTED", label: ACCESS.NOT_GRANTED.label },
];

type Filters = { q: string; approval: "" | ApprovalStatus; access: AccessFilter; role: string };
const NO_FILTERS: Filters = { q: "", approval: "", access: "", role: "" };

const byId = (a: Row, b: Row) => a.request.id.localeCompare(b.request.id);

function compare(a: Row, b: Row, sort: Sort): number {
  if (sort.key === "expiry" && (!a.endsAt || !b.endsAt)) {
    // Requests that were never granted have no expiry: they sort last in either direction.
    return a.endsAt ? -1 : b.endsAt ? 1 : byId(a, b);
  }
  const r = sort.key === "requested" ? a.request.requestedAt.localeCompare(b.request.requestedAt)
    : sort.key === "requester" ? a.request.requester.name.localeCompare(b.request.requester.name)
    : sort.key === "organisation" ? orgName(a.request.organisation).localeCompare(orgName(b.request.organisation))
    : sort.key === "approval" ? APPROVAL[a.request.status].rank - APPROVAL[b.request.status].rank
    : (a.endsAt ?? "").localeCompare(b.endsAt ?? "");
  return (sort.dir === "asc" ? r : -r) || byId(a, b);
}

const matchesAccess = (r: Row, f: AccessFilter) => !f || (f === "ACTIVE" ? isActive(r.access) : r.access === f);

/** Expiry, or why there is none. */
function Expiry({ r }: { r: Row }) {
  if (!r.session) return <Missing>{r.request.status === "PENDING" ? "Not started" : "None"}</Missing>;
  const label = r.access === "REVOKED" ? "Revoked" : r.access === "EXPIRED" ? "Expired" : "Expires";
  return (
    <span className="whitespace-nowrap tabular-nums">
      <span className="block">{label} {fmtDate(r.endsAt)}</span>
      <span className="block type-caption text-fg-muted">{fmtTimeUtc(r.endsAt ?? r.session.expiresAt)}</span>
    </span>
  );
}

export function JitRequestInventory({ rows, roles, onOpen }: { rows: Row[]; roles: JitRole[]; onOpen: (id: string) => void }) {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const [page, setPage] = useState(1);
  const [said, setSaid] = useState("");
  const listTop = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = f.q.trim().toLocaleLowerCase();
    return rows
      .filter((r) => (!q || [r.request.id, r.request.requester.name, orgName(r.request.organisation)].some((v) => v.toLocaleLowerCase().includes(q)))
        && (!f.approval || r.request.status === f.approval) && matchesAccess(r, f.access) && (!f.role || r.request.roleId === f.role))
      .sort((a, b) => compare(a, b, sort));
  }, [rows, f, sort]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const shown = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const filtering = f.q.trim() !== "" || f.approval !== "" || f.access !== "" || f.role !== "";
  const changed = filtering || sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir;
  const summary = filtering ? `${filtered.length} of ${count(rows.length, "request")} match` : count(rows.length, "request");

  // The match count is announced once typing pauses, not on every keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setSaid(filtering ? (filtered.length ? summary : "No requests match") : ""), 600);
    return () => window.clearTimeout(t);
  }, [filtering, filtered.length, summary]);

  const update = (next: Partial<Filters>) => { setF({ ...f, ...next }); setPage(1); };
  const reset = () => { setF(NO_FILTERS); setSort(DEFAULT_SORT); setPage(1); };
  const toggle = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] }));
    setPage(1);
  };
  const dir = (key: SortKey) => (sort.key === key ? sort.dir : null);
  const goTo = (p: number) => {
    setPage(p);
    const el = listTop.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start" });
  };
  const roleName = (r: Row) => r.role?.label ?? r.request.roleId;

  return (
    <div>
      <form role="search" aria-label="Filter JIT requests" onSubmit={(e) => e.preventDefault()}
        className="grid gap-3 md:grid-cols-3 xl:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]">
        <Field label="Search requests" hint="Requester, organisation or request ID" className="md:col-span-3 xl:col-span-1">
          <SearchInput placeholder="e.g. Okoye, Contoso or JIT-DEMO-0118" maxLength={100} value={f.q} autoComplete="off"
            onChange={(e) => update({ q: e.target.value })} onClear={() => update({ q: "" })} />
        </Field>
        <Field label="Approval status">
          <Select value={f.approval} onChange={(e) => update({ approval: e.target.value as Filters["approval"] })}
            options={[{ value: "", label: "All approval statuses" }, ...APPROVAL_STATUSES.map((s) => ({ value: s, label: APPROVAL[s].label }))]} />
        </Field>
        <Field label="Access status">
          <Select value={f.access} onChange={(e) => update({ access: e.target.value as AccessFilter })} options={ACCESS_FILTERS} />
        </Field>
        <Field label="Requested role">
          <Select value={f.role} onChange={(e) => update({ role: e.target.value })}
            options={[{ value: "", label: "All requested roles" }, ...roles.map((r) => ({ value: r.id, label: r.label }))]} />
        </Field>
      </form>

      <div ref={listTop} className="mt-4 flex scroll-mt-6 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="type-caption font-semibold text-fg-secondary">{summary}</p>
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={reset} disabled={!changed}>Reset filters</Button>
      </div>
      <p role="status" aria-live="polite" className="sr-only">{said}</p>

      <div className="mt-3">
        {filtered.length === 0 ? (
          <div className="rounded-card border border-dashed border-line-strong">
            <EmptyState icon={SearchX} title="No requests match" description="No demonstration request matches this search and these filters."
              action={<Button size="sm" variant="secondary" onClick={reset}>Reset filters</Button>} />
          </div>
        ) : (
          <>
            <div role="group" aria-label="Sort requests" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
              <span className="type-caption text-fg-muted">Sort by</span>
              {SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
            </div>
            <ul aria-label="JIT requests" className="grid gap-3 md:grid-cols-2 xl:hidden">
              {shown.map((r) => (
                <li key={r.request.id}>
                  <article aria-label={`${r.request.id}, ${r.request.requester.name}`} className="flex h-full flex-col rounded-card border border-line bg-surface-card p-4 shadow-card">
                    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                      <div className="min-w-0 flex-[1_1_10rem]">
                        <p className="type-code text-fg-muted">{r.request.id}</p>
                        <p className="break-words type-secondary font-semibold text-fg">{r.request.requester.name}</p>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <ApprovalBadge status={r.request.status} size="sm" />
                        <AccessBadge status={r.access} size="sm" />
                        {r.local && <LocalTag />}
                      </div>
                    </div>
                    <DescriptionList columns={2} className="mt-3" items={[
                      ["Organisation", orgName(r.request.organisation)],
                      ["Requested role", roleName(r)],
                      ["Requested duration", hoursLabel(r.request.requestedHours)],
                      ["Requested at", fmtDate(r.request.requestedAt, true)],
                      ["Expiry", <Expiry key="e" r={r} />],
                    ]} />
                    <p className="mt-3 line-clamp-2 type-caption text-fg-secondary"><span className="font-semibold text-fg-muted">Reason: </span>{r.request.justification}</p>
                    <div className="mt-auto pt-3">
                      <Button size="sm" variant="secondary" onClick={() => onOpen(r.request.id)}>View details<span className="sr-only"> of {r.request.id}</span></Button>
                    </div>
                  </article>
                </li>
              ))}
            </ul>
            <div className="hidden xl:block">
              <Table caption="JIT requests (demonstration data). The Request, Requester, Approval and Access · expiry column headings sort the list." minWidth="54rem">
                <THead>
                  <tr>
                    <Th sort={dir("requested")} onSort={() => toggle("requested")}>Request</Th>
                    <Th sort={dir("requester")} onSort={() => toggle("requester")}>Requester</Th>
                    <Th><span className="whitespace-normal">Requested role · reason</span></Th>
                    <Th>Duration</Th>
                    <Th sort={dir("approval")} onSort={() => toggle("approval")}>Approval</Th>
                    <Th sort={dir("expiry")} onSort={() => toggle("expiry")}>Access · expiry</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </tr>
                </THead>
                <TBody>
                  {shown.map((r) => (
                    <Tr key={r.request.id}>
                      <Td className="align-top">
                        <code className="block whitespace-nowrap type-code">{r.request.id}</code>
                        <span className="block whitespace-nowrap type-caption text-fg-muted">{fmtDate(r.request.requestedAt)}, {fmtTimeUtc(r.request.requestedAt)}</span>
                        {r.local && <span className="mt-1 block"><LocalTag /></span>}
                      </Td>
                      <Td className="align-top">
                        <span className="block break-words font-semibold">{r.request.requester.name}</span>
                        <span className="block type-caption text-fg-secondary">{orgName(r.request.organisation)}</span>
                      </Td>
                      <Td className="align-top">
                        <span className="block font-medium">{roleName(r)}</span>
                        <span className="line-clamp-2 type-caption text-fg-secondary">{r.request.justification}</span>
                      </Td>
                      <Td className="align-top whitespace-nowrap">{hoursLabel(r.request.requestedHours)}</Td>
                      <Td className="align-top"><ApprovalBadge status={r.request.status} size="sm" /></Td>
                      <Td className="align-top">
                        <AccessBadge status={r.access} size="sm" />
                        <span className="mt-1 block type-caption text-fg-secondary"><Expiry r={r} /></span>
                      </Td>
                      <Td align="right" className="align-top">
                        <Button size="sm" variant="secondary" onClick={() => onOpen(r.request.id)}>Details<span className="sr-only"> of {r.request.id}</span></Button>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
            {pageCount > 1 && (
              <div className="mt-4">
                <Pagination page={current} pageCount={pageCount} onPageChange={goTo} total={filtered.length} pageSize={PAGE_SIZE} label="JIT request pages" />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
