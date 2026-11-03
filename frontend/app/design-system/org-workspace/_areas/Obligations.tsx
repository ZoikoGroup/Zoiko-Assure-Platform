"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Button, Card, DataTable, DescriptionList, Field, SearchInput, Select, StateBadge, TabPanel, Tabs, type DataColumn } from "@/components/ds";
import { fmtDate, fmtRange } from "@/lib/format";
import { DemoFrame, Sample, Section } from "../../_shared/demo";
import { CONTROLS, ENTITIES, OBLIGATIONS, entityName, type Obligation } from "./data";
import { AreaLink, Proposed, ProposedHeader, SPLIT, useDetailPanel, useSelection } from "./shared";

/* Obligations, mirroring Obligation Dossiers (app/obligations → GET /obligations, GET /obligations/{lineage}). An
   obligation has a key, title, schedule kind, subject, lifecycle (ADOPTED or CLOSED here), validity and its latest
   frozen snapshot; its owner is the person who adopted it. The regulatory source and its effective date belong to the
   requirement version (GET /catalog/requirements/{id}), which the dossier does not show today. Search, filters and
   sorting are proposals: today's list has none. */

const STATE_TABS = ["ALL", "PROVEN", "NOT_PROVEN", "FAILING", "REQUIRES_REVIEW", "NONE"];
const stateOf = (o: Obligation) => o.state ?? "NONE";

export function Obligations({ go }: { go: (id: string) => void }) {
  return (
    <DemoFrame id="ow-obligations" plane="workspace" title="Obligations management"
      intro="Every obligation the organization has adopted: where it comes from, which entity it binds, how long it applies, who owns it and what its latest frozen assessment says."
      app={{ screen: "Obligation Dossiers", route: "/obligations", action: "GET /obligations · GET /obligations/{lineage}", permission: "read",
        enforced: ["Only your organization's obligations are returned", "An obligation exists only once a determination is adopted by attestation", "Snapshots are frozen: a period's recorded state never changes afterwards"] }}
      availability={{ level: "partial", note: "The list and the dossier (overview, period assurance, controls, occurrences, exceptions, legal basis, revisions) exist. Search, filters and sorting are proposals; the regulatory source and its effective date come from the requirement and are not shown in the dossier today." }}
      empty={{ title: "No obligations adopted yet", description: "Obligations appear once an applicability determination is attested and adopted." }}>
      <Body go={go} />
    </DemoFrame>
  );
}

function Body({ go }: { go: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [entity, setEntity] = useState("all");
  const [lifecycle, setLifecycle] = useState("all");
  const [state, setState] = useState("ALL");
  const [selected, setSelected] = useSelection("obligations", OBLIGATIONS[0].key);
  const [tab, setTab] = useState("overview");
  const { ref, reveal } = useDetailPanel();

  const base = useMemo(() => OBLIGATIONS.filter((o) => (entity === "all" || o.entity === entity) && (lifecycle === "all" || o.lifecycle === lifecycle)
    && (!q.trim() || `${o.title} ${o.key} ${o.source.title} ${o.source.locator} ${o.owner}`.toLowerCase().includes(q.trim().toLowerCase()))), [q, entity, lifecycle]);
  const shown = base.filter((o) => state === "ALL" || stateOf(o) === state);
  const current = OBLIGATIONS.find((o) => o.key === selected);
  const clear = () => { setQ(""); setEntity("all"); setLifecycle("all"); setState("ALL"); };

  const columns: DataColumn<Obligation>[] = [
    { key: "title", header: "Obligation", sortValue: (o) => o.title, render: (o) => <><span className="block font-medium text-fg">{o.title}</span><code className="type-code text-fg-muted">{o.key} · {o.schedule}</code></> },
    { key: "entity", header: "Entity · owner", sortValue: (o) => entityName(o.entity), render: (o) => <><span className="block">{entityName(o.entity)}</span><span className="type-caption text-fg-muted">{o.owner}</span></> },
    { key: "source", header: <ProposedHeader>Source</ProposedHeader>, sortValue: (o) => o.source.title, render: (o) => <><span className="block">{o.source.title.split(",")[0]}</span><span className="type-caption text-fg-muted">{o.source.locator} · {o.source.jurisdiction}</span></> },
    { key: "valid", header: "Valid", sortValue: (o) => o.validFrom, render: (o) => <span className="tabular-nums"><span className="block whitespace-nowrap">{fmtDate(o.validFrom)}</span><span className="block whitespace-nowrap type-caption text-fg-muted">→ {o.validTo ? fmtDate(o.validTo) : "open"}</span></span> },
    { key: "state", header: "Lifecycle · snapshot", sortValue: (o) => stateOf(o), render: (o) => (
      <span className="flex flex-col items-start gap-1"><StateBadge code={o.lifecycle} size="sm" />{o.state ? <StateBadge code={o.state} size="sm" /> : <span className="type-code text-fg-muted">NO SNAPSHOT</span>}</span>
    ) },
    { key: "open", header: <span className="sr-only">Details</span>, align: "right", render: (o) => (
      <Button size="sm" variant={o.key === selected ? "secondary" : "ghost"} aria-pressed={o.key === selected} onClick={() => { setSelected(o.key); setTab("overview"); reveal(); }}>
        {o.key === selected ? "Viewing" : "View"}<span className="sr-only"> {o.title}</span>
      </Button>
    ) },
  ];

  return (
    <div className={SPLIT}>
      <div className="min-w-0 space-y-3">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
          <Field label={<>Search obligations<Proposed /></>}><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Title, key, source or owner" /></Field>
          <Field label="Entity"><Select value={entity} onChange={(e) => setEntity(e.target.value)} options={[{ value: "all", label: "All entities" }, ...ENTITIES.map((e) => ({ value: e.id, label: e.name }))]} /></Field>
          <Field label="Lifecycle"><Select value={lifecycle} onChange={(e) => setLifecycle(e.target.value)} options={[{ value: "all", label: "All" }, { value: "ADOPTED", label: "ADOPTED" }, { value: "CLOSED", label: "CLOSED" }]} /></Field>
        </div>
        <Tabs id="ow-obl-state" label="Filter by latest snapshot state" variant="pills" value={state} onChange={setState}
          items={STATE_TABS.map((s) => ({ value: s, label: s === "ALL" ? "All" : s === "NONE" ? "No snapshot" : s, count: base.filter((o) => s === "ALL" || stateOf(o) === s).length }))} />
        <TabPanel id="ow-obl-state" value={state} className="">
          <DataTable caption={`Obligations (sample): ${shown.length} shown`} columns={columns} rows={shown} rowKey={(o) => o.key} defaultSort={{ key: "title", dir: "asc" }} minWidth="48rem"
            empty={{ title: "No obligations match", description: "Change the search or the filters.", action: <Button size="sm" onClick={clear}>Clear filters</Button> }} />
        </TabPanel>
        <p className="type-caption text-fg-muted">Owner is the person who adopted the obligation (the dossier&apos;s Owner). Snapshot periods are end-exclusive.</p>
      </div>

      <div ref={ref} className="scroll-mt-6">
        {current ? (
          <Card title={current.title} description={<code className="type-code">{current.key}</code>} actions={current.state ? <StateBadge code={current.state} size="sm" /> : undefined} bodyClassName="p-card space-y-4">
            <Tabs id="ow-obl-detail" label="Obligation detail" value={tab} onChange={setTab}
              items={[{ value: "overview", label: "Overview" }, { value: "basis", label: "Legal basis" }, { value: "controls", label: "Controls", count: current.controls.length }, { value: "period", label: "Period" }]} />
            <TabPanel id="ow-obl-detail" value={tab} className="space-y-4">
              {tab === "overview" && <DescriptionList columns={1} items={[
                ["Entity", entityName(current.entity)], ["Schedule", <code key="s" className="type-code">{current.schedule}</code>],
                ["Lifecycle", <StateBadge key="l" code={current.lifecycle} size="sm" />], ["Valid", fmtRange([current.validFrom, current.validTo ?? null])],
                ["Owner", <span key="o">{current.owner} <span className="type-caption text-fg-muted">adopted it</span></span>],
                ["Approved exceptions", current.exceptions ? `${current.exceptions} APPROVED` : "None"],
              ]} />}
              {tab === "basis" && <>
                <DescriptionList columns={1} items={[
                  ["Regulatory source", current.source.title], ["Issued by", current.source.authority], ["Jurisdiction", current.source.jurisdiction],
                  ["Locator", <code key="l" className="type-code">{current.source.locator}</code>],
                  ["Source effective date", <span key="e">{fmtDate(current.source.effective)}<Proposed>Not in the dossier today</Proposed></span>],
                ]} />
                <p className="type-caption text-fg-muted">The source&apos;s effective date is when the law took effect; the obligation&apos;s own validity starts when it was adopted for this entity.</p>
              </>}
              {tab === "controls" && <ul className="divide-y divide-line rounded-card border border-line">
                {current.controls.map((k) => { const c = CONTROLS.find((x) => x.key === k); return (
                  <li key={k} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
                    <span className="min-w-0"><span className="block type-secondary font-medium text-fg">{c?.title ?? k}</span><AreaLink id={k} area="controls" go={go} label={`Open control ${k} in Controls & tests`} /></span>
                    {c && <StateBadge code={c.state} size="sm" />}
                  </li>
                ); })}
              </ul>}
              {tab === "period" && (current.state ? <DescriptionList columns={1} items={[
                ["State", <StateBadge key="s" code={current.state} size="sm" />], ["Period", `${fmtRange(current.period)} (end exclusive)`],
                ["Perspective", <code key="p" className="type-code">{current.perspective}</code>], ["Record cutoff", <code key="c" className="type-code">#{current.cutoff}</code>],
                ["Frozen", fmtDate(current.frozen, true)],
              ]} /> : <p className="type-secondary text-fg-muted">No snapshot has been frozen yet. Freeze one from the dossier to assess a period.</p>)}
            </TabPanel>
            <Section title="In the application">
              <Link href="/obligations" className="inline-flex items-center gap-1 type-secondary font-medium text-accent-fg underline-offset-4 hover:underline">Open Obligation Dossiers<ArrowUpRight size={14} aria-hidden /></Link>
            </Section>
            <p className="type-caption text-fg-muted">Sample obligation<Sample />. Nothing here changes an obligation.</p>
          </Card>
        ) : <Card><p className="type-secondary text-fg-muted">Choose an obligation to see its detail.</p></Card>}
      </div>
    </div>
  );
}
