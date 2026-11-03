"use client";

import { useState } from "react";
import type { LucideIcon } from "lucide-react";
import { Archive, ArrowRight, CalendarRange, PenLine, Scale, SlidersHorizontal, UserCheck } from "lucide-react";
import { Button, Card, DescriptionList, Field, KpiCard, ProgressBar, Select, StateBadge, TabPanel, Tabs, Timeline, type KpiTone } from "@/components/ds";
import { fmtDate, fmtRange } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";
import { ACTIVITY, CELLS, COMPENSATIONS, CONTROLS, DETERMINATIONS, ENTITIES, EVIDENCE, EXCEPTIONS, OBLIGATIONS, ORG, entityName } from "./data";
import { Proposed } from "./shared";

/* Organization overview, mirroring the Organization Command Center (app/page.tsx → GET /dashboard, GET /org/tenant).
   The dashboard counts ADOPTED obligations by their latest snapshot, reports UNCERTAIN applicability separately and
   never states compliance. Choosing a reporting period is a proposal: today's dashboard has no period selector. Its
   recent events carry no actor and no screen shows them yet. */

export const PERIODS = [
  { value: "2026-07-01", label: "Q3 2026", range: ["2026-07-01", "2026-10-01"] as [string, string] },
  { value: "2026-04-01", label: "Q2 2026", range: ["2026-04-01", "2026-07-01"] as [string, string] },
  { value: "2026-10-01", label: "Q4 2026 (in progress)", range: ["2026-10-01", "2027-01-01"] as [string, string] },
];
const STATES: { code: string; label: string; tone: KpiTone }[] = [
  { code: "PROVEN", label: "Proven", tone: "verified" }, { code: "NOT_PROVEN", label: "Not proven", tone: "warning" },
  { code: "FAILING", label: "Failing", tone: "critical" }, { code: "REQUIRES_REVIEW", label: "Requires review", tone: "warning" },
];
const EVENT_TEXT: Record<string, string> = {
  "evidence.captured": "Evidence captured", "test.run_recorded": "Test run recorded", "control.evaluated": "Control evaluated",
  "exception.approved": "Exception approved", "determination.challenged": "Determination challenged", "assurance.snapshot_created": "Assurance snapshot frozen",
};

/** The entity an exception or compensation concerns, through its obligation or control. */
export const entityOfTarget = (key: string) => OBLIGATIONS.find((o) => o.key === key)?.entity ?? CONTROLS.find((c) => c.key === key)?.entity;
/** Obligations in force for at least part of a period (end exclusive). */
export const inForce = (period: [string, string]) => OBLIGATIONS.filter((o) => o.lifecycle === "ADOPTED" ? o.validFrom < period[1] : (o.validTo ?? "") > period[0] && o.validFrom < period[1]);

export function Overview({ go }: { go: (id: string) => void }) {
  return (
    <DemoFrame id="ow-overview" plane="workspace" title="Organization overview"
      intro="Where the organization stands for a reporting period: who it is, which entities are in scope, how much of what applies has been assessed, and what is waiting for someone."
      app={{ screen: "Organization Command Center", route: "/", action: "GET /dashboard · GET /org/tenant", permission: "read",
        enforced: ["Every figure is scoped by the server to your organization", "Counts cover ADOPTED obligations; UNCERTAIN applicability is reported separately, never silently excluded", "States are evidential (PROVEN, NOT_PROVEN, FAILING, REQUIRES_REVIEW), never an unqualified compliance claim"] }}
      availability={{ level: "partial", note: "The Command Center shows assurance totals, evidence conditions, open issues and determinations awaiting adoption. A reporting-period selector and a recent-activity list are proposals: the dashboard returns recent events but no screen shows them." }}
      empty={{ title: "Nothing to report yet", description: "Register a legal entity and adopt the obligations that apply to it to see the organization's position." }}>
      <Body go={go} />
    </DemoFrame>
  );
}

function Body({ go }: { go: (id: string) => void }) {
  const [periodId, setPeriodId] = useState(PERIODS[0].value);
  const [entity, setEntity] = useState("all");
  const period = PERIODS.find((p) => p.value === periodId)!;
  const inScope = <T extends { entity?: string }>(rows: T[]) => rows.filter((r) => entity === "all" || r.entity === entity);

  const obligations = inScope(inForce(period.range).filter((o) => o.lifecycle === "ADOPTED"));
  const assessed = obligations.filter((o) => o.period?.[0] === period.range[0] && o.state);
  const controls = inScope(CONTROLS);
  const evidence = inScope(EVIDENCE);
  const operating = controls.filter((c) => c.state === "OPERATING").length;
  const notCurrent = evidence.filter((e) => e.condition !== "CURRENT" && e.condition !== "ERASED");
  const target = <T extends { target?: string; obligation?: string }>(rows: T[]) => rows.filter((r) => entity === "all" || entityOfTarget(r.target ?? r.obligation ?? "") === entity);

  const pending: { area: string; icon: LucideIcon; label: string; count: number; detail: string }[] = [
    { area: "decisions", icon: PenLine, label: "Determinations awaiting attestation", count: inScope(DETERMINATIONS).filter((d) => d.state === "PROPOSED" || d.state === "IN_REVIEW").length, detail: "Proposed or in review; someone with the attest permission adopts or rejects each." },
    { area: "approvals", icon: UserCheck, label: "Exceptions awaiting approval", count: target(EXCEPTIONS).filter((x) => x.state === "PENDING_APPROVAL").length, detail: "Time-bounded; an approved exception never changes an assurance state." },
    { area: "approvals", icon: Scale, label: "Compensations awaiting approval", count: target(COMPENSATIONS).filter((c) => c.status === "PENDING_APPROVAL").length, detail: "Approval adds a new version of the obligation's satisfaction expression." },
    { area: "controls", icon: SlidersHorizontal, label: "Controls not operating", count: controls.length - operating, detail: "From the 24-hour preview of each control's state." },
    { area: "evidence", icon: Archive, label: "Evidence not CURRENT", count: notCurrent.length, detail: "Stale or invalid; erasure is a governed act, not a gap." },
    { area: "reporting", icon: CalendarRange, label: `Obligations not assessed for ${period.label}`, count: obligations.length - assessed.length, detail: "No frozen snapshot covers this period yet." },
  ];

  return (
    <div className="space-y-stack">
      <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title={<>{ORG.name}<Sample /></>} description="Organization identity" actions={<StateBadge code={ORG.status} size="sm" />}>
          <DescriptionList items={[
            ["Organization ID", <code key="i" className="type-code">{ORG.id}</code>], ["Contracting region", ORG.region],
            ["Isolation tier", <code key="t" className="type-code">{ORG.tier}</code>], ["Legal entities", ENTITIES.length],
            ["Home cell", [...new Set(ENTITIES.map((e) => e.cell))].join(", ")], ["Registered", fmtDate(ORG.created)],
          ]} />
        </Card>
        <Card title="Reporting scope" description="What the figures below cover">
          <div className="space-y-4">
            <Field label={<>Reporting period<Proposed /></>} hint={`${fmtRange(period.range)}, end exclusive. Today the dashboard shows each obligation's latest snapshot, whatever its period.`}>
              <Select value={periodId} onChange={(e) => setPeriodId(e.target.value)} options={PERIODS.map((p) => ({ value: p.value, label: p.label }))} />
            </Field>
            <div className="space-y-2">
              <p className="type-label text-fg-muted">Entity scope</p>
              <Tabs id="ow-ov-entity" label="Entity scope" variant="pills" value={entity} onChange={setEntity}
                items={[{ value: "all", label: "All entities" }, ...ENTITIES.map((e) => ({ value: e.id, label: `${e.name} (${e.country})` }))]} />
            </div>
            <p className="type-caption text-fg-muted">Served from {CELLS.find((c) => c.id === ENTITIES[0].cell)?.region}; records stay in each entity&apos;s home cell.</p>
          </div>
        </Card>
      </div>

      <TabPanel id="ow-ov-entity" value={entity} className="space-y-stack">
        <Section title={`Assurance position · ${period.label} · ${entity === "all" ? "all entities" : entityName(entity)}`}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {STATES.map((s) => <KpiCard key={s.code} label={s.label} tone={s.tone} value={assessed.filter((o) => o.state === s.code).length} hint={<code className="type-code">{s.code}</code>} />)}
            <KpiCard label="Not assessed" value={obligations.length - assessed.length} hint="no snapshot for this period" />
          </div>
          <p className="type-caption text-fg-muted">Of {obligations.length} ADOPTED obligations in force during the period. An exception is shown beside a state and never changes it.</p>
        </Section>

        <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Card title="Assurance coverage" description="How much of what applies is assessed and backed">
            <div className="space-y-4">
              <ProgressBar label="Obligations with a frozen snapshot for this period" value={obligations.length ? (assessed.length / obligations.length) * 100 : 0} detail={`${assessed.length} of ${obligations.length}`} />
              <ProgressBar label="Controls operating (24-hour preview)" tone="verified" value={controls.length ? (operating / controls.length) * 100 : 0} detail={`${operating} of ${controls.length}`} />
              <ProgressBar label="Evidence CURRENT" tone="verified" value={evidence.length ? ((evidence.length - notCurrent.length) / evidence.length) * 100 : 0} detail={`${evidence.length - notCurrent.length} of ${evidence.length}`} />
              <p className="type-caption text-fg-muted">Coverage says how much has been assessed, not that the organization complies.</p>
            </div>
          </Card>
          <Card title="Pending actions" description="Waiting for a person, by area">
            <ul className="divide-y divide-line">
              {pending.map((p) => { const Icon = p.icon; return (
                <li key={p.label} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                  <Icon size={16} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
                  <div className="min-w-0 flex-1">
                    <p className="type-secondary font-medium text-fg">{p.label} <span className={`tabular-nums ${p.count ? "text-accent-fg" : "text-fg-muted"}`}>· {p.count || "none"}</span></p>
                    <p className="type-caption text-fg-muted">{p.detail}</p>
                  </div>
                  <Button size="sm" variant="ghost" iconRight={ArrowRight} onClick={() => go(p.area)}>Open<span className="sr-only"> {p.label}</span></Button>
                </li>
              ); })}
            </ul>
          </Card>
        </div>

        <Card title={<>Recent activity<Proposed /></>} description="The latest recorded events, newest first">
          <Timeline label="Recent activity, newest first" items={ACTIVITY.map((a) => ({
            id: String(a.seq), title: EVENT_TEXT[a.event] ?? a.event,
            meta: <><code className="type-code">{a.event}</code> · #{a.seq} · {fmtDate(a.at, true)}</>, description: <>{a.aggregate} <code className="type-code">{a.id}</code></>,
          }))} />
          <p className="mt-3 type-caption text-fg-muted">Events carry no actor; who acted is in the audit trail. Sample events<Sample /> for both entities.</p>
        </Card>
      </TabPanel>
    </div>
  );
}
