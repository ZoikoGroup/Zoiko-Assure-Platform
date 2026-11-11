"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Button, Callout, EmptyState, Field, SearchInput, TabPanel, Tabs } from "@/components/ds";
import { CATEGORIES, ICONS, SIZE_SCALE } from "./iconRegistry";
import { CopyButton, ProvenanceChip } from "./shared";

/* D. Icon library: a searchable gallery of the lucide-react icons the application already uses (./iconRegistry.ts). */

/** A category as a tab value: tab values become element ids, which must not contain spaces. */
const slug = (c: string) => c.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-");

export function Icons() {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("all");
  const [size, setSize] = useState("20");
  const query = q.trim().toLowerCase();
  const matches = useMemo(() => ICONS.filter((i) => !query || `${i.name} ${i.usage} ${i.category} ${i.where}`.toLowerCase().includes(query)), [query]);
  const shown = matches.filter((i) => cat === "all" || slug(i.category) === cat);
  const px = Number(size);

  return (
    <div className="space-y-stack">
      <Callout tone="info" title="lucide-react, the application's only icon library">
        Version 1.52.0, already a dependency: nothing was installed. The gallery lists the {ICONS.length} icons the application already uses, each with the job it
        does today and where. Every lucide icon is a scalable SVG, so each is available at every size in the scale.
      </Callout>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 sm:max-w-sm"><Field label="Search icons"><SearchInput value={q} onChange={(e) => setQ(e.target.value)} onClear={() => setQ("")} placeholder="Name, use or screen" /></Field></div>
        <div>
          <p className="mb-1.5 type-secondary font-medium text-fg">Preview size <ProvenanceChip kind="proposed">Proposed scale</ProvenanceChip></p>
          <Tabs id="bk-icon-size" label="Preview size" variant="pills" value={size} onChange={setSize} items={SIZE_SCALE.map((s) => ({ value: String(s), label: `${s}px` }))} />
        </div>
      </div>
      <Tabs id="bk-icon-cat" label="Icon category" variant="pills" value={cat} onChange={setCat}
        items={[{ value: "all", label: "All", count: matches.length }, ...CATEGORIES.map((c) => ({ value: slug(c), label: c, count: matches.filter((i) => i.category === c).length }))]} />
      <TabPanel id="bk-icon-cat" value={cat} className="">
        <TabPanel id="bk-icon-size" value={size} className="">
          <p role="status" className="mb-3 type-caption text-fg-muted">{shown.length} icon{shown.length === 1 ? "" : "s"} shown at {px}px</p>
          {shown.length === 0 ? (
            <EmptyState icon={Search} title="No icons match" description="Try another word, such as evidence, approval or sort."
              action={<Button size="sm" onClick={() => { setQ(""); setCat("all"); }}>Clear search and filter</Button>} />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" aria-label="Icons">
              {shown.map(({ name, Icon, category, usage, where }) => (
                <li key={name} className="flex gap-3 rounded-card border border-line bg-surface-card/80 p-3">
                  <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-control border border-line bg-surface-raised text-fg"><Icon size={px} aria-hidden /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-1">
                      <code className="truncate type-code font-semibold text-fg">{name}</code>
                      <CopyButton text={name} label={`icon name ${name}`} />
                    </div>
                    <p className="type-caption text-fg-muted">{category}</p>
                    <p className="mt-1 type-caption text-fg-secondary">{usage}</p>
                    <p className="mt-1 type-caption text-fg-muted">Used in: {where}</p>
                    <p className="mt-1 type-caption text-fg-muted">Sizes: {SIZE_SCALE.join(" · ")} px</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </TabPanel>
      </TabPanel>
      <p className="type-caption text-fg-muted">Import: <code className="type-code">{`import { ShieldCheck } from "lucide-react";`}</code> Use the copied name in place of ShieldCheck.</p>
    </div>
  );
}
