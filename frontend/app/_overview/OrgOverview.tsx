"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { canOpen } from "@/components/nav";
import SCREEN_ACCESS from "@/lib/screen-access.json";
import { ErrorBox, Notice, PageHeader, usePageTitle } from "@/components/ui";
import { OrganisationCard } from "./OrganisationCard";
import { OutstandingWork } from "./OutstandingWork";
import { WorkspaceSections, YourActions } from "./QuickNavigation";
import { RecentActivity } from "./RecentActivity";
import { AssurancePosition, MoreFigures, SummaryCards, keyFigures } from "./SummaryCards";
import {
  LINK, countriesOf, entitiesOf, holds, openRecords, stateOf, toRead, utcTime,
  type AnomalyRow, type CompensationRow, type ControlRow, type Dashboard, type ExceptionRow, type FactRequestRow,
  type IssueRow, type OrgRecord, type Read, type Reads,
} from "./types";

/* The Organisation Overview: the organisation's home screen ("/"). Every figure is read from the backend (dashboard
   projection, organisation record, controls, IPE anomalies, issues, exceptions, compensations, fact requests) and
   links to the screen where it is worked. Nothing here derives applicability or assurance state in the browser, nothing
   is a compliance score, and nothing is demonstration data: where the backend records no figure, none is shown.
   Its heading is "Overview", the organisation sidebar's name for it. */
const TITLE = "Overview";

/* Which of the Overview's reads this person's roles are served (lib/screen-access.json, generated from the backend
   guards). A read that would be refused is not made: every refusal is an audit record. */
export function OrgOverview() {
  const { me } = useAuth();
  const reader = holds(SCREEN_ACCESS.panels.overview.perms, me?.permissions);
  if (!reader) return <AccessAdminHome orgName={me?.tenant_name ?? ""} />;
  return <OverviewView />;
}

/** A role administrator (e.g. TENANT_SECURITY_ADMIN) administers access, not the regulatory record (ZA-IAM-001 §08),
 *  so the Overview's figures are not served to it. Its home says so and points to its own screen. */
function AccessAdminHome({ orgName }: { orgName: string }) {
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);
  return (
    <>
      <PageHeader kicker={orgName} title={TITLE} subtitle="Your organisation's current assurance position." />
      <Notice>Your roles administer who can access {orgName || "this organization"}, not its regulatory records, so there are no figures to show here.
        Manage people, roles and sessions in <Link href="/admin/users" className={LINK}>Team &amp; Access</Link>.</Notice>
    </>
  );
}

function OverviewView() {
  const { me } = useAuth();
  const perms = me?.permissions;
  // The organisation record and the fact-request list are served to `read` only (not to audit_read): skipped otherwise.
  const orgReader = holds(SCREEN_ACCESS.shell.entity_scope.perms, perms);
  const factReader = holds(SCREEN_ACCESS.panels.overview_fact_requests.perms, perms);
  const dash = useApi<Dashboard>("/dashboard");
  const org = useApi<OrgRecord>(orgReader ? "/org/tenant" : null);
  const controls = useApi<ControlRow[]>("/controls");
  const anomalies = useApi<AnomalyRow[]>("/ipe/anomalies");
  const issues = useApi<IssueRow[]>("/issues");
  const exceptions = useApi<ExceptionRow[]>("/exceptions");
  const compensations = useApi<CompensationRow[]>("/compensations");
  const facts = useApi<FactRequestRow[]>(factReader ? "/fact-requests" : null);
  const { setTitle } = usePageTitle();
  useEffect(() => { setTitle(TITLE); return () => setTitle(""); }, [setTitle]);

  const r: Reads = {
    dash: toRead(true, dash), org: toRead(orgReader, org), controls: toRead(true, controls), anomalies: toRead(true, anomalies),
    issues: toRead(true, issues), exceptions: toRead(true, exceptions), compensations: toRead(true, compensations),
    facts: toRead(factReader, facts),
  };
  const open = openRecords(r);
  const figures = keyFigures(r, open);
  const opens = (href: string) => canOpen("tenant", href, perms);

  const d = r.dash.data;
  // "Now" for due dates is the moment the dashboard was read (its overdue count uses the same clock).
  const readTime = d ? Date.parse(d.as_of.valid_at) : NaN;
  const now = isNaN(readTime) ? Date.now() : readTime;
  const orgName = r.org.data?.tenant.legal_name ?? me?.tenant_name ?? "";
  const jurisdictions = countriesOf(entitiesOf(r));
  // When the projection was read, and the home-cell record watermark it reflects.
  const readAt = d ? `read ${fmtDate(d.as_of.valid_at, true)}, known as of ${d.as_of.cell_id}#${d.as_of.record_seq}` : "";
  // Failures are reported (refusals are shown in place as "not available to your role"); Retry re-reads what failed.
  const failed = (Object.values(r) as Read<unknown>[]).filter((x) => stateOf(x) === "failed");
  const retry = () => failed.forEach((x) => x.reload());

  /* The opening view: heading, the six key figures, then what needs attention beside the assurance position and the
     person's own actions. Below it: recent activity and the organisation's profile beside the further figures and the
     workspace's sections. */
  return (
    <div>
      {/* Whose records, the page, and when its figures were read. */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0">
          <h1 className="text-[26px] font-semibold leading-tight text-slate-800 sm:text-[28px]">{TITLE}</h1>
          <p className="mt-1 text-[14px] text-slate-600">What needs your team&apos;s attention, from your organisation&apos;s own records.</p>
        </div>
        <div className="min-w-0 sm:max-w-[40%] sm:pt-1 sm:text-right">
          <div title={`Organization ${orgName}`} className="truncate text-[15px] font-bold uppercase tracking-[0.04em] text-teal-700">{orgName}</div>
          <div title={readAt ? `Dashboard ${readAt}` : undefined} className="mt-1 text-[13px] text-slate-600">
            {d ? `As of ${utcTime(d.as_of.valid_at)} UTC` : r.dash.error ? "Records unavailable" : "Reading records…"}
            {jurisdictions.length ? ` · ${jurisdictions.join(", ")}` : ""}
            {opens("/entities") && <>{" · "}<Link href="/entities" className={LINK}>Entities</Link></>}
          </div>
        </div>
      </div>

      <ErrorBox error={failed[0]?.error ?? null} />
      {failed.length > 0 && (
        <div className="-mt-1 mb-3"><button type="button" className="btn-ghost" onClick={retry}>Retry</button></div>
      )}

      <SummaryCards kpis={figures.key} opens={opens} />

      {/* Two thirds and one third on desktops; the two columns end level, whichever is taller. */}
      <div className="grid grid-cols-1 gap-x-4 gap-y-6 xl:grid-cols-3">
        <OutstandingWork r={r} open={open} now={now} opens={opens} onRetry={retry} className="xl:col-span-2" />
        <div className="grid min-w-0 content-start gap-6 md:grid-cols-2 xl:flex xl:flex-col">
          <AssurancePosition dash={r.dash} opens={opens} />
          <YourActions className="xl:flex-1" />
        </div>
      </div>

      {/* Below the opening view, in the same proportions: recent activity and the organisation's profile beside the
          further figures and the workspace's sections, the two columns again ending level. */}
      <div className="mt-6 grid grid-cols-1 gap-x-4 gap-y-6 xl:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-6 xl:col-span-2">
          <RecentActivity dash={r.dash} opens={opens} className="xl:flex-1" />
          <OrganisationCard r={r} me={me} opens={opens} />
        </div>
        <div className="flex min-w-0 flex-col gap-6">
          <MoreFigures kpis={figures.more} opens={opens} />
          <WorkspaceSections className="xl:flex-1" />
        </div>
      </div>

      {(readAt || d?.language_note) && (
        <p className="mt-6 max-w-4xl text-[12px] leading-5 text-slate-500">
          Figures are your organisation&apos;s records{readAt ? ` (${readAt})` : ""}. {d?.language_note}
        </p>
      )}
    </div>
  );
}
