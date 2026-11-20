"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import type { Me } from "@/lib/actor";
import { fmtDate, fmtRange } from "@/lib/format";
import { Badge, KV } from "@/components/ui";
import { CODE, LINK, PANEL, PANEL_TITLE, codeWords, countriesOf, entitiesOf, plural, sentence, snapshotPeriods, stateOf, type Reads } from "./types";

/* A. Organisation information. The organisation record (GET /org/tenant: status, contracting region, isolation tier,
   registration date) is read only for the roles it is served to; everyone who sees the Overview gets the name and
   identifier from their own session and the legal entities from GET /dashboard. Assure keeps an assurance period per
   obligation snapshot, so the period shown is the one the latest snapshots cover, never an organisation-wide claim. */

const pending = <span role="status" aria-label="Loading" className="skeleton inline-block h-3.5 w-24 align-middle" />;
const muted = (text: string) => <span className="text-slate-500">{text}</span>;

export function OrganisationCard({ r, me, opens }: { r: Reads; me: Me | null; opens: (href: string) => boolean }) {
  const org = stateOf(r.org);
  const dash = stateOf(r.dash);
  const t = r.org.data?.tenant;
  const d = r.dash.data;
  // The same legal entities either way: the organisation record's when it is read, otherwise the dashboard's.
  const entities = entitiesOf(r);
  const regions = new Map((r.org.data?.cells ?? []).map((c) => [c.cell_id, c.region]));
  const countries = countriesOf(entities);
  const homes = entities ? [...new Set(entities.map((e) => e.home_cell_id))].sort() : [];
  const entityWait = org === "loading" || (dash === "loading" && !r.org.data);
  const fromEntities = (v: ReactNode) => (entities ? v : entityWait ? pending : muted("Unavailable"));
  const fromRecord = (v: () => ReactNode) => (t ? v() : pending);
  const p = d ? snapshotPeriods(d) : null;

  const items: [ReactNode, ReactNode][] = [
    ["Organisation ID", <span key="id" className="mono select-all break-all">{t?.tenant_id ?? me?.tenant_id ?? "—"}</span>],
    // Fields only the organisation record holds: shown while it loads or once read, and left out when it is not read.
    ...(org === "ready" || org === "loading" ? [
      ["Contracting region", fromRecord(() => t!.contracting_region)],
      ["Isolation tier", fromRecord(() => sentence(codeWords(t!.isolation_tier)))],
      ["Registered", fromRecord(() => <time dateTime={t!.created_at}>{fmtDate(t!.created_at)}</time>)],
    ] as [ReactNode, ReactNode][] : []),
    ["Legal entities", fromEntities(<>
      {plural(entities?.length ?? 0, "entity", "entities")}
      {opens("/entities") && <>{" · "}<Link href="/entities" className={LINK}>View</Link></>}
    </>)],
    ["Jurisdictions", fromEntities(countries.length ? countries.join(", ") : "—")],
    ["Home cells", fromEntities(homes.length ? homes.map((h) => (regions.get(h) ? `${h} · ${regions.get(h)}` : h)).join(", ") : "—")],
    ["Assurance snapshot period", !p ? (dash === "loading" ? pending : muted("Unavailable"))
      : p.adopted === 0 ? "No adopted obligations"
      : p.covered === 0 ? "No assurance snapshot yet"
      : (
        <span title="The period each adopted obligation's latest assurance snapshot covers. Periods are kept per obligation; there is no organisation-wide reporting period.">
          {p.distinct.length === 1 ? fmtRange(p.distinct[0]) : `${fmtDate(p.earliest)} → ${fmtDate(p.latest)}`}
          <span className="block text-[12px] text-slate-500">
            {p.distinct.length > 1 ? `${p.distinct.length} different periods · ` : ""}{p.covered} of {plural(p.adopted, "obligation")}
          </span>
        </span>
      )],
  ];

  const note = org === "skipped" || org === "denied"
    ? "Status, contracting region, isolation tier and registration date are in the organisation record, which your roles do not include."
    : org === "failed" ? "The organisation record could not be read, so its status, contracting region, isolation tier and registration date are not shown."
    : null;

  return (
    <section aria-labelledby="org-h" className={`${PANEL} px-5 pb-5 pt-5 sm:px-6`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="org-h" className={PANEL_TITLE}>Organisation profile</h2>
        {t ? <span className={CODE}><Badge code={t.status} title={`Organisation status: ${t.status}`} /></span> : org === "loading" ? pending : null}
      </div>
      {/* Three columns: the profile sits in the page's two-thirds column on desktops. */}
      <KV cols={3} items={items} />
      {note && <p className="mt-3 text-[12px] text-slate-500">{note}</p>}
    </section>
  );
}
