"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, Building2, Info, KeyRound, Search, ShieldCheck, UserRound, Users } from "lucide-react";
import { useApi } from "@/lib/api";
import { useAuth, type Me } from "@/lib/actor";
import { ROLE_CODES, roleAbout, roleLabel } from "@/lib/roles";
import {
  Alert, Button, DescriptionList, EmptyState, ErrorState, KpiCard, PlaneBadge, SearchInput, Select, Skeleton,
  TBody, THead, Table, TabPanel, Tabs, Td, Th, Tr, buttonClasses,
} from "./ds";
import { ClassificationBadge, count } from "./platformQueue";
import {
  OrganisationStatus, RegistryHeading, RegistryScope, RegistrySection, SortChip, actionRequiredByOrganisation, type RegistryOrg,
} from "./PlatformAdmin";
import { Drawer, usePageTitle, useSortedRows } from "./ui";
import { YourAccess } from "./your-access/YourAccess";
import { YourSessions, type Session } from "./your-access/Sessions";

/* Users & Roles for the Zoiko Super Admin (persona "platform") on /admin/users; organisation users and content staff
   keep their own view of that route (app/admin/users/page.tsx).

   Reads, all of them served to this role and nothing else (backend tests/read_authorization_matrix.json, SUPER_ADMIN):
     the signed-in account (from the session check the shell already made; Your Access's Refresh repeats it), GET /sessions,
     GET /actors (this role is served only Zoiko staff accounts, tenant_id null), and GET /platform/organisations.
   Each tab reads its data the first time it is opened and keeps it while the page is open, so switching tabs never
   repeats a request; every registry read is an audit record. The one write is ending one of your own sessions
   (POST /sessions/{id}/revoke). The role registry, role assignments, other people's effective access and sessions
   are organisation-administrator reads: this role is refused them (and every refusal is audited), so none is made. */

type TabKey = "access" | "staff" | "admins" | "roles";
/** Below 640px the four full labels need about 500px, so the tab strip would scroll and hide the last tab: there they
 *  use their short names and no icon (each tab's accessible name is the label it shows). */
const TABS: { value: TabKey; label: string; short: string; icon: LucideIcon }[] = [
  { value: "access", label: "Your Access", short: "Access", icon: UserRound },
  { value: "staff", label: "Zoiko Staff", short: "Staff", icon: Users },
  { value: "admins", label: "Organisation Admins", short: "Admins", icon: Building2 },
  { value: "roles", label: "Roles", short: "Roles", icon: ShieldCheck },
];
const tabItems = TABS.map(({ value, label, short, icon: Icon }) => ({
  value,
  label: <><Icon size={15} aria-hidden className="hidden shrink-0 sm:block" /><span className="sm:hidden">{short}</span><span className="hidden sm:inline">{label}</span></>,
}));

/** A Zoiko staff account from GET /actors. `roles` is the account's cached organisation-/platform-wide roles. */
type StaffRow = {
  actor_id: string; tenant_id: string | null; actor_type: string; display_name: string; email: string | null; roles: string[] | null;
  authority_basis: string | null; tenant_name?: string;
};

/* -------------------------------------------------------------------------------------------------- role catalogue */
/** How each role is granted, as the backend implements it (app/iam.py ASSIGNABILITY, app/routers/platform.py,
 *  app/accounts.py; platform roles "are never granted through any API", iam.py). Read-only facts for display: no
 *  permission mapping is restated here, because none is served to this role. */
type Grant = "provisioned" | "super-admin" | "organisation" | "organisation-revoke";
const GRANT: Record<Grant, { label: string; tone: string }> = {
  provisioned: { label: "Server-provisioned only", tone: "border-line-strong bg-fg/5 text-fg-secondary" },
  "super-admin": { label: "Appointed by the Super Admin", tone: "border-accent-secondary/50 bg-accent-secondary/10 text-accent-secondary-fg" },
  organisation: { label: "Organisation-administered", tone: "border-accent/50 bg-accent/10 text-accent-fg" },
  "organisation-revoke": { label: "Organisation-administered (revoke only)", tone: "border-accent/50 bg-accent/10 text-accent-fg" },
};
const PROVISIONED = "Provisioned by the server (the one-time bootstrap or the demo seed). No API grants it, so it is never assigned from this screen.";
const BUSINESS = "The organisation's Security Administrator, for the whole organisation or one of its legal entities.";
const ROLE_GRANT: Record<string, { plane: "platform" | "organisation"; grant: Grant; assignedBy: string }> = {
  SUPER_ADMIN: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  CONNECTOR_CERTIFICATION_MANAGER: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  CONTENT_ANALYST: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  ENCODING_ENGINEER: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  CONTENT_LEGAL_REVIEWER: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  CONTENT_RELEASE_MANAGER: { plane: "platform", grant: "provisioned", assignedBy: PROVISIONED },
  TENANT_OWNER: { plane: "organisation", grant: "super-admin",
    assignedBy: "The Zoiko Super Admin, when creating an organisation or from its record in Tenants & Entities. No API revokes it yet." },
  TENANT_SECURITY_ADMIN: { plane: "organisation", grant: "organisation", assignedBy: "The organisation's Organisation Admin, for the whole organisation." },
  CCO: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  LEGAL_REVIEWER: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  REGULATORY_ANALYST: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  CONTROL_OWNER: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  INTERNAL_AUDIT: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  AI_GOVERNANCE_LEAD: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  PLATFORM_ADMIN: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  RISK_APPROVER: { plane: "organisation", grant: "organisation", assignedBy: BUSINESS },
  EXTERNAL_AUDITOR: { plane: "organisation", grant: "organisation-revoke",
    assignedBy: "Not assignable through the API. The organisation's Security Administrator can revoke it." },
  SERVICE: { plane: "organisation", grant: "organisation-revoke",
    assignedBy: "Not assignable through the API. The organisation's Security Administrator can revoke it." },
};
/** The six platform roles: the only roles a Zoiko staff account can hold, so the only useful staff filter values. */
const PLATFORM_ROLES = ROLE_CODES.filter((r) => ROLE_GRANT[r]?.plane === "platform");

/* ------------------------------------------------------------------------------------------------------- the page */
export function PlatformUsers() {
  const { me } = useAuth();
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle("Users & Roles"); return () => setTitle(""); }, [setTitle]);
  const [tab, setTab] = useState<TabKey>("access");
  const [opened, setOpened] = useState<Set<TabKey>>(() => new Set<TabKey>(["access"]));
  const open = (v: string) => { const t = v as TabKey; setTab(t); setOpened((s) => (s.has(t) ? s : new Set(s).add(t))); };

  // Each tab's reads, made the first time it is opened and kept while the page is open.
  const sessions = useApi<Session[]>("/sessions");
  const [staffQ, setStaffQ] = useState("");
  const [staffType, setStaffType] = useState("");
  const [staffRole, setStaffRole] = useState("");
  const qs = new URLSearchParams();
  if (staffQ) qs.set("q", staffQ);
  if (staffType) qs.set("actor_type", staffType);
  if (staffRole) qs.set("role", staffRole);
  const staffQuery = qs.toString();
  const staff = useApi<StaffRow[]>(opened.has("staff") ? `/actors${staffQuery ? `?${staffQuery}` : ""}` : null);
  const registry = useApi<RegistryOrg[]>(opened.has("admins") ? "/platform/organisations" : null);

  if (!me) return null;
  return (
    <div className="space-y-6">
      <RegistryHeading kicker="Zoiko platform administration" title="Users & Roles">
        <p>Your own access and sessions, the Zoiko staff accounts, every organisation&apos;s Organisation Admins, and the role catalogue.
          Organisation Admins are appointed from each organisation&apos;s record; every other role is provisioned by the server or administered
          by the organisation itself.</p>
      </RegistryHeading>
      <RegistryScope>
        <span className="font-semibold text-fg">Staff accounts and registry metadata only.</span> The people, evidence and assurance records of
        customer organisations are not reachable from this role.
      </RegistryScope>

      <div>
        <Tabs id="pu" label="Users & Roles" value={tab} onChange={open} items={tabItems} />
        <TabPanel id="pu" value={tab} className="pt-6 focus-visible:outline-offset-4">
          {tab === "access" && (
            <div className="space-y-6">
              <YourAccess onRefreshed={sessions.reload} />
              <YourSessions sessions={sessions} />
            </div>
          )}
          {tab === "staff" && (
            <StaffTab me={me} staff={staff} filters={{ q: staffQ, type: staffType, role: staffRole }}
              onFilters={(f) => { setStaffQ(f.q); setStaffType(f.type); setStaffRole(f.role); }} />
          )}
          {tab === "admins" && <OrganisationAdmins registry={registry} />}
          {tab === "roles" && <RolesTab />}
        </TabPanel>
      </div>
    </div>
  );
}

type Loaded<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void };

/* ------------------------------------------------------------------------------------------------- Zoiko Staff */
type StaffFilters = { q: string; type: string; role: string };
type StaffSort = "name" | "email" | "type" | "role";
const STAFF_SORTS: { key: StaffSort; label: string; sortValue: (r: StaffRow) => string }[] = [
  { key: "name", label: "Name", sortValue: (r) => r.display_name.toLocaleLowerCase() },
  { key: "email", label: "Email", sortValue: (r) => (r.email ?? "").toLocaleLowerCase() },
  { key: "type", label: "Type", sortValue: (r) => r.actor_type },
  { key: "role", label: "Platform role", sortValue: (r) => roleLabel(r.roles?.[0] ?? "").toLocaleLowerCase() },
];

function StaffTab({ me, staff, filters, onFilters }: { me: Me; staff: Loaded<StaffRow[]>; filters: StaffFilters; onFilters: (f: StaffFilters) => void }) {
  const [text, setText] = useState(filters.q);
  const [selected, setSelected] = useState<StaffRow | null>(null);
  const rows = useMemo(() => staff.data ?? [], [staff.data]);
  const { sorted, sort, toggle } = useSortedRows(rows, STAFF_SORTS);
  const dir = (k: StaffSort) => (sort?.key === k ? sort.dir : null);
  const filtering = !!(filters.q || filters.type || filters.role);
  const clear = () => { setText(""); onFilters({ q: "", type: "", role: "" }); };
  return (
    <div className="space-y-6">
      <Alert tone="info" title="Platform roles are not assigned here">
        Zoiko staff belong to no customer organisation. Their roles are provisioned by the server (the one-time bootstrap or the demo seed); no
        API grants them, so this list is read-only.
      </Alert>
      <RegistrySection title="Zoiko staff accounts" description="Platform and content-operations accounts. Only HUMAN accounts can sign in."
        aside={staff.data && <p className="type-caption text-fg-muted" aria-live="polite">{count(rows.length, "account")}{filtering ? " match" : ""}</p>}>
        <form role="search" aria-label="Filter staff accounts" className="flex flex-col gap-3 md:flex-row md:items-center"
          onSubmit={(e) => { e.preventDefault(); onFilters({ ...filters, q: text.trim() }); }}>
          <div className="min-w-0 md:flex-1">
            <SearchInput aria-label="Name or email" placeholder="Search name or email" maxLength={100} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <div className="md:w-40"><Select aria-label="Principal type" value={filters.type} onChange={(e) => onFilters({ ...filters, type: e.target.value })}
            options={[{ value: "", label: "Any type" }, { value: "HUMAN", label: "HUMAN" }]} /></div>
          <div className="md:w-60"><Select aria-label="Platform role" value={filters.role} onChange={(e) => onFilters({ ...filters, role: e.target.value })}
            options={[{ value: "", label: "Any platform role" }, ...PLATFORM_ROLES.map((r) => ({ value: r, label: roleLabel(r) }))]} /></div>
          <div className="flex gap-2">
            <Button type="submit" variant="secondary" icon={Search}>Search</Button>
            {filtering && <Button variant="ghost" onClick={clear}>Clear</Button>}
          </div>
        </form>
        <div className="mt-4">
          {!staff.data && staff.error ? <ErrorState error={staff.error} title="The staff accounts could not be loaded" onRetry={() => staff.reload()} />
            : !staff.data ? <ListLoading label="Loading the staff accounts" />
            : rows.length === 0 ? (
              filtering ? <EmptyState icon={Users} title="No staff accounts match" description="Change the search or the filters."
                action={<Button size="sm" variant="secondary" onClick={clear}>Clear filters</Button>} />
                : <EmptyState icon={Users} title="No staff accounts" />
            ) : (
              <>
                <div role="group" aria-label="Sort staff accounts" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
                  <span className="type-caption text-fg-muted">Sort by</span>
                  {STAFF_SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
                </div>
                <ul aria-label="Zoiko staff accounts" className="space-y-3 xl:hidden">
                  {sorted.map((r) => (
                    <li key={r.actor_id}>
                      <article aria-label={r.display_name} className="rounded-card border border-line bg-surface-card p-4 shadow-card">
                        <div className="flex items-start justify-between gap-3">
                          <StaffName row={r} you={r.actor_id === me.actor_id} />
                          <code className="shrink-0 rounded-chip border border-line bg-fg/5 px-1.5 type-code text-fg-secondary">{r.actor_type}</code>
                        </div>
                        <div className="mt-3"><StaffRoles row={r} /></div>
                        <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
                          <p className="min-w-0 type-caption text-fg-muted">Authority basis: <span className="text-fg-secondary">{r.authority_basis ?? "—"}</span></p>
                          <Button size="sm" variant="secondary" onClick={() => setSelected(r)}>Details<span className="sr-only"> for {r.display_name}</span></Button>
                        </div>
                      </article>
                    </li>
                  ))}
                </ul>
                <div className="hidden xl:block">
                  <Table caption="Zoiko staff accounts. The Name, Email, Type and Platform role column headings sort the list." minWidth="40rem">
                    <THead>
                      <tr>
                        <Th sort={dir("name")} onSort={() => toggle("name")}>Name</Th>
                        <Th sort={dir("email")} onSort={() => toggle("email")}>Email</Th>
                        <Th sort={dir("type")} onSort={() => toggle("type")}>Type</Th>
                        <Th sort={dir("role")} onSort={() => toggle("role")}>Platform role</Th>
                        <Th>Authority basis</Th>
                        <Th><span className="sr-only">Details</span></Th>
                      </tr>
                    </THead>
                    <TBody>
                      {sorted.map((r) => (
                        <Tr key={r.actor_id}>
                          <Td className="align-top"><StaffName row={r} you={r.actor_id === me.actor_id} hideEmail /></Td>
                          <Td className="align-top"><span className="break-words type-code"><Email value={r.email} /></span></Td>
                          <Td className="align-top"><code className="type-code">{r.actor_type}</code></Td>
                          <Td className="align-top"><StaffRoles row={r} /></Td>
                          <Td className="align-top text-fg-secondary">{r.authority_basis ?? "—"}</Td>
                          <Td className="align-top text-right"><Button size="sm" variant="secondary" onClick={() => setSelected(r)}>Details<span className="sr-only"> for {r.display_name}</span></Button></Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </div>
              </>
            )}
        </div>
      </RegistrySection>
      {selected && <StaffDetail row={selected} you={selected.actor_id === me.actor_id} onClose={() => setSelected(null)} />}
    </div>
  );
}

function StaffName({ row, you, hideEmail = false }: { row: StaffRow; you: boolean; hideEmail?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="break-words type-secondary font-semibold text-fg">
        {row.display_name}
        {you && <span className="ml-2 inline-flex rounded-chip border border-status-info-line bg-status-info-tint px-1.5 align-middle type-caption font-semibold text-status-info">You</span>}
      </p>
      {!hideEmail && <p className="break-words type-code text-fg-muted"><Email value={row.email} /></p>}
    </div>
  );
}

function StaffRoles({ row }: { row: StaffRow }) {
  const roles = row.roles ?? [];
  if (roles.length === 0) return <span className="text-fg-muted">No platform role recorded</span>;
  return (
    <ul className="space-y-1.5">
      {roles.map((r) => (
        <li key={r}>
          <p className="type-secondary font-medium text-fg">{roleLabel(r)} <code className="ml-1 type-code font-normal text-fg-muted">{r}</code></p>
          {roleAbout(r) && <p className="type-caption text-fg-secondary">{roleAbout(r)}</p>}
        </li>
      ))}
    </ul>
  );
}

/** One staff account, read-only, from the row already loaded: no further request. */
function StaffDetail({ row, you, onClose }: { row: StaffRow; you: boolean; onClose: () => void }) {
  return (
    <Drawer open onClose={onClose} title={row.display_name} width="max-w-xl">
      <div className="space-y-5">
        <DescriptionList columns={2} items={[
          ["Email", <span key="e" className="break-words type-code"><Email value={row.email} /></span>],
          ["Principal type", <code key="t" className="type-code">{row.actor_type}</code>],
          ["Account ID", <code key="i" className="break-all type-code">{row.actor_id}</code>],
          ["Authority basis", row.authority_basis ?? "—"],
          ["Workspace", "Zoiko platform operations · not a member of any customer organisation"],
          ["This account", you ? "Yours" : "Another Zoiko staff account"],
        ]} />
        <section aria-labelledby="pu-detail-roles">
          <h3 id="pu-detail-roles" className="mb-2 type-secondary font-semibold text-fg">Platform roles</h3>
          <StaffRoles row={row} />
        </section>
        <Alert tone="info">
          Platform roles are provisioned by the server and are never granted or revoked from this screen. This account&apos;s effective permissions
          are not shown: no endpoint serves another account&apos;s access to the Super Admin.
        </Alert>
      </div>
    </Drawer>
  );
}

/* ----------------------------------------------------------------------------------------- Organisation Admins */
type AdminSort = "name" | "status" | "admins";
const ADMIN_SORTS: { key: AdminSort; label: string; sortValue: (o: RegistryOrg) => string | number }[] = [
  { key: "name", label: "Organisation", sortValue: (o) => o.legal_name.toLocaleLowerCase() },
  { key: "status", label: "Status", sortValue: (o) => o.status },
  { key: "admins", label: "Organisation Admins", sortValue: (o) => o.admins.length },
];

/** Every organisation's Organisation Admins, from the one registry read this tab makes. Organisations the Action Queue
 *  lists as Action required (ACTIVE, no Organisation Admin) carry its classification; appointing is done on the
 *  organisation's own record, which each row links to. */
function OrganisationAdmins({ registry }: { registry: Loaded<RegistryOrg[]> }) {
  const orgs = useMemo(() => registry.data ?? [], [registry.data]);
  const flagged = useMemo(() => actionRequiredByOrganisation(orgs), [orgs]);
  const { sorted, sort, toggle } = useSortedRows(orgs, ADMIN_SORTS);
  const dir = (k: AdminSort) => (sort?.key === k ? sort.dir : null);
  const adminCount = orgs.reduce((n, o) => n + o.admins.length, 0);
  const loading = !registry.data && !registry.error;
  return (
    <div className="space-y-6">
      <ul aria-label="Organisation Admins summary" className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <li><KpiCard label="Organisations" icon={Building2} loading={loading} value={registry.data ? orgs.length : "—"} /></li>
        <li><KpiCard label="Organisation Admins" icon={KeyRound} loading={loading} value={registry.data ? adminCount : "—"} /></li>
        <li><KpiCard label="Active, with no Organisation Admin" icon={Info} tone={flagged.size ? "warning" : "neutral"} loading={loading}
          value={registry.data ? flagged.size : "—"} hint={registry.data ? "Listed in the Action Queue as Action required" : undefined} /></li>
      </ul>
      <RegistrySection title="Organisation Admins by organisation"
        description="Read from the organisation registry when you opened this tab; each registry read is recorded in the platform audit trail. To appoint an Organisation Admin, open the organisation."
        aside={registry.data && <p className="type-caption text-fg-muted">{count(orgs.length, "organisation")}</p>}>
        {!registry.data && registry.error ? <ErrorState error={registry.error} title="The organisation registry could not be loaded" onRetry={() => registry.reload()} />
          : loading ? <ListLoading label="Loading the organisation registry" />
          : orgs.length === 0 ? <EmptyState icon={Building2} title="No organisations yet" description="Organisations are created in Tenants & Entities."
              action={<Link href="/organisations" className={buttonClasses({ variant: "secondary", size: "sm" })}>Open Tenants &amp; Entities</Link>} />
          : (
            <>
              <div role="group" aria-label="Sort organisations" className="mb-3 flex flex-wrap items-center gap-2 xl:hidden">
                <span className="type-caption text-fg-muted">Sort by</span>
                {ADMIN_SORTS.map((s) => <SortChip key={s.key} label={s.label} dir={dir(s.key)} onSort={() => toggle(s.key)} />)}
              </div>
              <ul aria-label="Organisation Admins by organisation" className="space-y-3 xl:hidden">
                {sorted.map((o) => (
                  <li key={o.tenant_id}>
                    <article aria-label={o.legal_name} className="rounded-card border border-line bg-surface-card p-4 shadow-card">
                      <div className="flex items-start justify-between gap-3">
                        <OrgName org={o} />
                        <span className="shrink-0"><OrganisationStatus status={o.status} /></span>
                      </div>
                      <div className="mt-3"><AdminList org={o} flag={flagged.has(o.tenant_id)} /></div>
                      <div className="mt-3"><OpenOrganisation org={o} /></div>
                    </article>
                  </li>
                ))}
              </ul>
              <div className="hidden xl:block">
                <Table caption="Organisation Admins by organisation. The Organisation, Status and Organisation Admins column headings sort the list." minWidth="40rem">
                  <THead>
                    <tr>
                      <Th sort={dir("name")} onSort={() => toggle("name")}>Organisation</Th>
                      <Th sort={dir("status")} onSort={() => toggle("status")}>Status</Th>
                      <Th sort={dir("admins")} onSort={() => toggle("admins")}>Organisation Admins</Th>
                      <Th><span className="sr-only">Open</span></Th>
                    </tr>
                  </THead>
                  <TBody>
                    {sorted.map((o) => (
                      <Tr key={o.tenant_id}>
                        <Td className="align-top"><OrgName org={o} /></Td>
                        <Td className="align-top"><OrganisationStatus status={o.status} /></Td>
                        <Td className="align-top"><AdminList org={o} flag={flagged.has(o.tenant_id)} /></Td>
                        <Td className="align-top text-right"><OpenOrganisation org={o} /></Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            </>
          )}
      </RegistrySection>
    </div>
  );
}

function OrgName({ org }: { org: RegistryOrg }) {
  return (
    <div className="min-w-0">
      <Link href={`/organisations/${org.tenant_id}`} className="break-words rounded font-semibold text-accent-fg underline-offset-4 hover:underline">{org.legal_name}</Link>
      <p className="break-all type-code text-fg-muted">{org.tenant_id}</p>
    </div>
  );
}

function AdminList({ org, flag }: { org: RegistryOrg; flag: boolean }) {
  if (org.admins.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {flag && <ClassificationBadge classification="ACTION_REQUIRED" />}
        <span className={flag ? "type-caption font-medium text-fg-secondary" : "text-fg-muted"}>None appointed</span>
      </div>
    );
  }
  return (
    <div>
      <p className="type-caption font-semibold tabular-nums text-fg-secondary">{count(org.admins.length, "appointed", "appointed")}</p>
      <ul className="mt-1 space-y-1.5">
        {org.admins.map((a) => (
          <li key={a.actor_id} className="min-w-0">
            <p className="break-words type-secondary text-fg">{a.display_name}</p>
            <p className="break-words type-code text-fg-muted"><Email value={a.email} /></p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function OpenOrganisation({ org }: { org: RegistryOrg }) {
  return (
    <Link href={`/organisations/${org.tenant_id}`} className={`${buttonClasses({ variant: "secondary", size: "sm" })} whitespace-nowrap`}>
      Open organisation<span className="sr-only">: {org.legal_name}</span><ArrowRight size={14} aria-hidden />
    </Link>
  );
}

/* ------------------------------------------------------------------------------------------------------ Roles */
function RolesTab() {
  const groups: { plane: "platform" | "organisation"; title: string; description: string }[] = [
    { plane: "platform", title: "Platform roles", description: "Held by Zoiko staff accounts, which belong to no customer organisation." },
    { plane: "organisation", title: "Organisation roles", description: "Held by a customer organisation's own people, agents and services, within that organisation only." },
  ];
  const unclassified = ROLE_CODES.filter((r) => !ROLE_GRANT[r]);
  return (
    <div className="space-y-6">
      <Alert tone="info" title="Read-only role catalogue">
        The Super Admin cannot assign or revoke roles here; its only role-granting action is appointing an Organisation Admin from an
        organisation&apos;s record in Tenants &amp; Entities. Detailed permission mapping for each role is not currently exposed to the Super Admin,
        so it is not shown.
      </Alert>
      {groups.map((g) => {
        const roles = ROLE_CODES.filter((r) => ROLE_GRANT[r]?.plane === g.plane);
        return (
          <RegistrySection key={g.plane} title={g.title} description={g.description} aside={<p className="type-caption text-fg-muted">{count(roles.length, "role")}</p>}>
            <ul aria-label={g.title} className="grid gap-3 lg:grid-cols-2">
              {roles.map((r) => <li key={r} className="min-w-0"><RoleCard code={r} /></li>)}
            </ul>
          </RegistrySection>
        );
      })}
      {unclassified.length > 0 && (
        <RegistrySection title="Other roles" description="Roles in the catalogue whose granting path is not described here.">
          <ul className="grid gap-3 lg:grid-cols-2">{unclassified.map((r) => <li key={r}><RoleCard code={r} /></li>)}</ul>
        </RegistrySection>
      )}
    </div>
  );
}

function RoleCard({ code }: { code: string }) {
  const g = ROLE_GRANT[code];
  return (
    <article aria-label={roleLabel(code)} className="flex h-full flex-col gap-2 rounded-card border border-line bg-surface-card p-4">
      {/* From sm up the plane badge stays beside the title (a long title wraps in its own column); phones keep wrapping. */}
      <div className="flex flex-wrap items-start justify-between gap-2 sm:flex-nowrap">
        <div className="min-w-0">
          <h3 className="break-words type-secondary font-semibold text-fg">{roleLabel(code)}</h3>
          <code className="break-all type-code text-fg-muted">{code}</code>
        </div>
        {g && <span className="shrink-0"><PlaneBadge plane={g.plane === "platform" ? "platform" : "workspace"} size="sm" /></span>}
      </div>
      {roleAbout(code) && <p className="type-caption text-fg-secondary">{roleAbout(code)}</p>}
      {g && (
        <div className="mt-auto space-y-1.5 border-t border-line pt-2">
          <span className={`inline-flex rounded-chip border px-2 py-0.5 type-caption font-semibold ${GRANT[g.grant].tone}`}>{GRANT[g.grant].label}</span>
          <p className="type-caption text-fg-muted"><span className="font-semibold text-fg-secondary">Assigned by: </span>{g.assignedBy}</p>
        </div>
      )}
    </article>
  );
}

/* --------------------------------------------------------------------------------------------------- shared bits */
/** An email address that wraps after its @ rather than mid-word (a very long local part can still break). */
function Email({ value }: { value: string | null | undefined }) {
  if (!value) return <>No email recorded</>;
  const at = value.lastIndexOf("@");
  return at > 0 ? <>{value.slice(0, at + 1)}<wbr />{value.slice(at + 1)}</> : <>{value}</>;
}

function ListLoading({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="rounded-card border border-line bg-surface-card p-4">
          <Skeleton className="h-4 w-52" />
          <Skeleton className="mt-2 h-3.5 w-3/4" />
        </div>
      ))}
    </div>
  );
}
