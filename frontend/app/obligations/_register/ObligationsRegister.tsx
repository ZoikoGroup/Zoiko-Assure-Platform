"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Building2, Clock, Lock, RefreshCw, RotateCcw } from "lucide-react";
import { errText, useApi } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { fmtDate } from "@/lib/format";
import { rolesText } from "@/lib/roles";
import { canOpen } from "@/components/nav";
import { Card, EmptyState, ErrorState, LoadingSkeleton, PageHeader } from "@/components/ui";
import { ASSURANCE_MEANING, LIFECYCLE_MEANING, NO_FILTERS, figures, filtersFromQuery, isFiltered, matches, writeFiltersToQuery, type Filters } from "./model";
import { RegisterSummary, type SummaryStatus } from "./RegisterSummary";
import { RegisterTable } from "./RegisterTable";
import { RegisterToolbar } from "./RegisterToolbar";
import type { ObligationRecord } from "./types";

/* The Obligations Register: the "Current Obligations" tab of Obligations (app/obligations/page.tsx). It makes one read,
   GET /obligations (read or audit_read, as lib/screen-access.json lists for this screen), so every role offered the tab
   is served and nothing it opens records a refusal. Search, filters, sorting and the summary figures are worked out in
   the browser over that full list; nothing is sample data, and a failed read is shown as unavailable, never as zero.
   Each row opens the existing obligation dossier (/obligations/{lineage}). */

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function ObligationsRegister() {
  const { me } = useAuth();
  const { data, error, loading, reload } = useApi<ObligationRecord[]>("/obligations");
  const params = useSearchParams();
  // The search and filters start from the address, so Back from a dossier, a refresh or a shared link reopens this view.
  const [filters, setFilters] = useState<Filters>(() => filtersFromQuery(params));
  const [retrievedAt, setRetrievedAt] = useState<string | null>(null);
  const searchId = useId();
  const results = useRef<HTMLDivElement>(null);

  // A body that is not a list is a failure, never an empty register.
  const rows = Array.isArray(data) ? data : null;
  const failure = error ?? (data != null && !rows ? new Error("The server's reply was not an obligations list, so nothing is shown.") : null);
  // A refusal is shown once and not requested again on its own (each refusal is an audit record).
  const denied = error?.status === 403;
  useEffect(() => { if (rows) setRetrievedAt(new Date().toISOString()); }, [rows]);
  // ...and are written back to it in place (no history entry per keystroke), beside the hub's `tab` and anything else
  // there. Also re-run when the address changes, so a link to this same page (the sidebar entry) cannot leave it out of
  // step. `null` state lets Next.js keep useSearchParams in step with the new address.
  useEffect(() => {
    const url = new URL(window.location.href);
    writeFiltersToQuery(url.searchParams, filters);
    if (url.href !== window.location.href) window.history.replaceState(null, "", url);
  }, [filters, params]);

  const shown = useMemo(() => (rows ?? []).filter((o) => matches(o, filters)), [rows, filters]);
  const figs = useMemo(() => (rows ? figures(rows) : null), [rows]);
  const summary: SummaryStatus = figs ? { kind: "ready", figures: figs } : failure ? { kind: "unavailable" } : { kind: "loading" };

  // Clearing disables the button that did it, so focus moves to the search rather than being lost.
  const clear = useCallback(() => { setFilters(NO_FILTERS); document.getElementById(searchId)?.focus(); }, [searchId]);
  // Retry replaces the error panel it sits in, so focus moves to the results area that stays.
  const retry = () => { results.current?.focus(); reload(); };

  const resultText = !rows || rows.length === 0 ? ""
    : isFiltered(filters) ? `${shown.length} of ${plural(rows.length, "obligation")} match`
    : `Showing all ${plural(rows.length, "obligation")}`;

  return (
    <>
      <PageHeader title="Obligations" spec="ZA-STA-001 · WF-STA-01"
        subtitle="Your organisation's obligation inventory: each requirement it adopted for one of its subjects through a signed applicability attestation, with its validity and latest frozen assessment."
        help={<RegisterHelp />}
        actions={!denied && (
          <button type="button" className="btn-ghost" aria-busy={loading || undefined} onClick={() => { if (!loading) reload(); }}>
            <RefreshCw size={14} aria-hidden className={loading ? "motion-safe:animate-spin" : ""} />Refresh
          </button>
        )} />
      <RegisterContext org={me?.tenant_name?.trim() ?? ""} retrievedAt={rows ? retrievedAt : null} />

      {denied ? (
        <div className="card">
          <EmptyState icon={Lock} title="Your roles can't read this organisation's obligations"
            description={<>{errText(error)} The server checked your roles ({rolesText(me) || "none"}) and refused the list, so nothing was loaded.
              Ask an Organisation Admin if you need access.</>} />
        </div>
      ) : (
        <>
          <RegisterSummary status={summary} />
          <Card bodyClassName="">
            <RegisterToolbar rows={rows ?? []} filters={filters} onChange={setFilters} onClear={clear} searchId={searchId}
              disabled={!rows || rows.length === 0} resultText={resultText} />
            <div ref={results} tabIndex={-1} aria-label="Obligations register" className="focus:outline-none">
              {rows ? (
                rows.length === 0 ? <EmptyRegister canDecide={canOpen(personaOf(me), "/applicability", me?.permissions)} />
                  : <RegisterTable rows={shown} onClear={clear} />
              ) : failure ? (
                <div className="p-4">
                  <ErrorState error={failure} title="The obligations register could not be loaded" />
                  <div className="flex flex-wrap items-center gap-3">
                    <button type="button" className="btn-ghost" onClick={retry}><RotateCcw size={14} aria-hidden />Retry</button>
                    <span className="text-[12px] text-slate-500">No figures or rows are shown until the whole list loads.</span>
                  </div>
                </div>
              ) : <LoadingSkeleton rows={6} />}
            </div>
          </Card>
          <p className="mt-3 text-[11px] leading-4 text-slate-500">
            Figures, search and filters cover the whole list the server returned for your organisation; the list is not paged.
            Assessment periods are end-exclusive. An approved exception is shown beside an assessment and never changes it.
          </p>
        </>
      )}
    </>
  );
}

/** Which organisation the register belongs to (from the signed-in session, so no extra read) and when this browser
 *  received the list. The list itself carries no as-of time, so none is claimed. */
function RegisterContext({ org, retrievedAt }: { org: string; retrievedAt: string | null }) {
  return (
    <dl className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-1.5 text-[12px] leading-5">
      <div className="flex min-w-0 items-center gap-1.5">
        <dt className="flex shrink-0 items-center gap-1.5 text-slate-500"><Building2 size={13} aria-hidden />Organisation</dt>
        <dd className="min-w-0 break-words font-medium text-slate-800">{org || "Not reported by your session"}</dd>
      </div>
      {retrievedAt && (
        <div className="flex items-center gap-1.5">
          <dt className="flex shrink-0 items-center gap-1.5 text-slate-500"><Clock size={13} aria-hidden />List retrieved</dt>
          <dd className="font-medium text-slate-800"><time dateTime={retrievedAt}>{fmtDate(retrievedAt, true)}</time></dd>
        </div>
      )}
    </dl>
  );
}

function EmptyRegister({ canDecide }: { canDecide: boolean }) {
  return (
    <EmptyState title="No obligations adopted yet"
      description="An obligation appears here once an applicability determination for one of your organisation's subjects is adopted through a signed attestation."
      action={canDecide ? <Link href="/applicability" className="btn-ghost">Open Decisions &amp; Attestations</Link> : undefined} />
  );
}

function Legend({ title, meanings }: { title: string; meanings: Record<string, string> }) {
  return (
    <div>
      <p className="font-medium text-slate-700">{title}</p>
      <dl className="mt-1 grid gap-x-3 gap-y-1 text-[12px] sm:grid-cols-[max-content_minmax(0,1fr)]">
        {Object.entries(meanings).map(([code, meaning]) => (
          <Fragment key={code}><dt className="mono text-slate-700">{code}</dt><dd>{meaning}</dd></Fragment>
        ))}
      </dl>
    </div>
  );
}

function RegisterHelp() {
  return (
    <>
      <p>An obligation is a requirement your organisation adopted for one subject by attesting its applicability determination.
        Open one to see why it applies, its controls and evidence, occurrences, exceptions and compensation, legal basis and revisions.</p>
      <p>Latest assessment is the state of the most recently frozen period snapshot, computed by the backend for a named period and
        record cutoff. States are evidential, never &lsquo;compliant&rsquo;. Freeze, explain or reproduce a snapshot from the
        obligation&apos;s Period assurance tab.</p>
      <p>&ldquo;List retrieved&rdquo; is when this browser received the list. The register&apos;s response carries no as-of time
        of its own, so none is shown.</p>
      <Legend title="Assessment states" meanings={ASSURANCE_MEANING} />
      <Legend title="Obligation lifecycle" meanings={LIFECYCLE_MEANING} />
    </>
  );
}
