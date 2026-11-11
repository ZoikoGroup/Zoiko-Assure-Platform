"use client";

import { useEffect, useState } from "react";
import { Callout, Card, DescriptionList, TBody, THead, TabPanel, Table, Tabs, Td, Th, Tr } from "@/components/ds";
import { Logo } from "@/components/Shell";
import { Block, CopyButton, ProvenanceChip, useArtworkColours, type Provenance } from "./shared";

/* A. Logo library: the three image files in frontend/public, exactly as they are, and how the application uses them
   (components/Shell.tsx `Logo`). Nothing here edits or re-exports an asset; misuse and monochrome previews are CSS
   applied to the same files. */

export type Asset = {
  id: "wordmark" | "mark" | "on-dark"; file: string; title: string; kind: Provenance; origin: string;
  background: string; usage: string; appSizes: string; note?: string;
};
export const ASSETS: Asset[] = [
  { id: "wordmark", file: "logo.png", title: "Primary logo · horizontal wordmark", kind: "official", origin: "Initial implementation, 7 Oct 2026",
    background: "White and light surfaces", usage: "Staff sidebar, sign-in, forced password change, the white card in the Design Governance sidebar, and the browser tab icon (app/layout.tsx)",
    appSizes: "110–152 px wide (Logo component crop): sign-in 152, staff sidebar 140, Design Governance 124" },
  { id: "mark", file: "logo-mark.jpg", title: "Symbol mark · app icon", kind: "official", origin: "Initial implementation, 7 Oct 2026",
    background: "Any surface: it carries its own orange tile", usage: "Collapsed sidebars (both workspaces) in place of the wordmark",
    appSizes: "32 px square, cropped to the orange tile",
    note: "A JPEG on a white canvas, so it has no transparency: outside the tile the canvas is white. The 32 px crop shows the tile edge to edge." },
  { id: "on-dark", file: "logo-on-dark.png", title: "Wordmark for dark surfaces", kind: "derived", origin: "Derived 2 Nov 2026 (Command Center redesign), committed 3 Nov 2026",
    background: "Navy and other dark surfaces", usage: "Organization workspace sidebar (navy), expanded", appSizes: "110 px wide",
    note: "logo.png with its navy recoloured to white by a script; the orange and the transparency are unchanged, and the geometry is identical. It was not supplied by the brand owner and needs brand approval." },
];

/** Which file serves each logo variation the brief lists. */
const VARIATIONS: { variation: string; asset: string; kind: Provenance; note: string }[] = [
  { variation: "Primary logo", asset: "logo.png", kind: "official", note: "The wordmark: Z mark, verification badge, ZOIKO, ASSURE and the ™." },
  { variation: "Horizontal logo", asset: "logo.png", kind: "official", note: "The primary logo is already horizontal. No stacked or vertical version exists." },
  { variation: "Compact logo", asset: "logo-mark.jpg", kind: "official", note: "No separate compact file: collapsed sidebars use the symbol mark." },
  { variation: "Icon / symbol mark", asset: "logo-mark.jpg", kind: "official", note: "The badge on an orange rounded tile." },
  { variation: "Light-background version", asset: "logo.png", kind: "official", note: "The primary logo is the light-background version." },
  { variation: "Dark-background version", asset: "logo-on-dark.png", kind: "derived", note: "Recoloured from logo.png; awaiting brand approval." },
  { variation: "Monochrome version", asset: "None", kind: "proposed", note: "No file exists. The previews below simulate one with a CSS filter." },
];

const WORDMARK_SIZES = [90, 110, 124, 140, 152, 180];
const MARK_SIZES = [16, 24, 32, 48];
const IN_APP: Record<number, string> = { 110: "org sidebar", 124: "Design Governance", 140: "staff sidebar", 152: "sign-in", 32: "collapsed sidebar" };

/** The natural pixel size of an image file, read in the browser. */
function useImageSize(src: string) {
  const [size, setSize] = useState<string | null>(null);
  useEffect(() => { const i = new Image(); i.onload = () => setSize(`${i.naturalWidth} × ${i.naturalHeight} px`); i.src = src; }, [src]);
  return size;
}

/** Transparent areas shown as a checkerboard drawn from the border token. */
export const CHECKER = { backgroundImage: "conic-gradient(var(--za-line) 25%, transparent 0 50%, var(--za-line) 0 75%, transparent 0)", backgroundSize: "16px 16px" };

export function Logos() {
  return (
    <div className="space-y-section">
      <Callout tone="info" title="Only real files are shown as logos">
        Three image files exist in <code className="type-code">frontend/public</code>. Two came with the platform; the dark-surface wordmark was derived from the primary
        during this design work. A monochrome logo does not exist, so it appears only as a labelled proposal. No minimum size or clear space has been set for
        the brand: this page reports the sizes the application uses today and marks any recommendation as proposed.
      </Callout>

      <Block title="Variations and the files that serve them">
        <Table caption="Logo variations mapped to asset files" minWidth="40rem">
          <THead><Tr><Th>Variation</Th><Th>File</Th><Th>Status</Th><Th>Note</Th></Tr></THead>
          <TBody>{VARIATIONS.map((v) => (
            <Tr key={v.variation}><Td className="font-medium text-fg">{v.variation}</Td><Td><code className="type-code">{v.asset}</code></Td>
              <Td><ProvenanceChip kind={v.kind} /></Td><Td className="text-fg-secondary">{v.note}</Td></Tr>
          ))}</TBody>
        </Table>
      </Block>

      <div className="grid gap-stack xl:grid-cols-3">
        {ASSETS.map((a) => <AssetCard key={a.id} asset={a} />)}
      </div>

      <SizePreview />
      <Monochrome />
    </div>
  );
}

function AssetCard({ asset }: { asset: Asset }) {
  const dims = useImageSize(`/${asset.file}`);
  const { colours } = useArtworkColours(`/${asset.file}`, asset.id === "mark" ? 3 : 3);
  const dark = asset.id === "on-dark";
  return (
    <Card title={asset.title} description={<code className="type-code">{asset.file}</code>} actions={<ProvenanceChip kind={asset.kind} />} bodyClassName="p-card space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <figure className="space-y-1.5">
          <div data-theme={dark ? "dark" : "light"} className={`flex h-24 items-center justify-center rounded-control border border-line ${dark ? "bg-surface-sidebar" : "bg-white"}`}>
            {asset.id === "mark" ? <Logo mark /> : <Logo width={124} onDark={dark} />}
          </div>
          <figcaption className="type-caption text-fg-muted">As the application shows it</figcaption>
        </figure>
        <figure className="space-y-1.5">
          <div className="flex h-24 items-center justify-center rounded-control border border-line" style={dark ? { ...CHECKER, backgroundColor: "var(--color-midnight-950)" } : CHECKER}>
            {/* eslint-disable-next-line @next/next/no-img-element -- the raw file, unprocessed */}
            <img src={`/${asset.file}`} alt={`${asset.file}, the whole file`} className="h-24 w-24 object-contain" />
          </div>
          <figcaption className="type-caption text-fg-muted">The whole file (checkerboard = transparent)</figcaption>
        </figure>
      </div>
      <DescriptionList columns={1} items={[
        ["File", <span key="f" className="flex items-center gap-1.5"><code className="type-code">/{asset.file}</code><CopyButton text={`/${asset.file}`} label={`path of ${asset.file}`} /></span>],
        ["Format", `${asset.file.endsWith(".jpg") ? "JPEG, no transparency" : "PNG, transparent background"}${dims ? ` · ${dims}` : ""}`],
        ["Origin", asset.origin],
        ["Recommended background", asset.background],
        ["Where it is used", asset.usage],
        ["Sizes in the application", asset.appSizes],
        ["Minimum size", <span key="m">Not established. <ProvenanceChip kind="observed">Smallest use today: {asset.id === "mark" ? "32 px" : asset.id === "on-dark" ? "110 px" : "124 px"}</ProvenanceChip></span>],
        ["Clear space", <span key="c">Not established. <ProvenanceChip kind="observed">{asset.id === "mark" ? "The crop has none: the tile fills the 32 px box" : "The app's crop leaves about 4 px each side at 140 px"}</ProvenanceChip></span>],
        ["Artwork colours (measured from the file)", colours ? <span key="a" className="flex flex-wrap gap-2">{colours.map((c, i) => (
          <span key={i} className="inline-flex items-center gap-1.5 type-code"><span aria-hidden className="h-4 w-4 rounded-chip border border-line" style={{ backgroundColor: c.hex }} />{c.hex}</span>
        ))}</span> : "Measuring…"],
      ]} />
      {asset.note && <p className="type-caption text-fg-secondary">{asset.note}</p>}
    </Card>
  );
}

function SizePreview() {
  const [kind, setKind] = useState("wordmark");
  return (
    <Block title="Size preview" aside={<ProvenanceChip kind="observed">Sizes marked “in app” are used today</ProvenanceChip>}>
      <Tabs id="bk-logo-size" label="Logo to preview" variant="pills" value={kind} onChange={setKind}
        items={[{ value: "wordmark", label: "Wordmark" }, { value: "mark", label: "Symbol mark" }]} />
      <TabPanel id="bk-logo-size" value={kind} className="">
        <div className="flex flex-wrap items-end gap-4 rounded-card border border-line bg-white p-card" data-theme="light">
          {(kind === "wordmark" ? WORDMARK_SIZES : MARK_SIZES).map((w) => (
            <figure key={w} className="flex flex-col items-center gap-2">
              {kind === "wordmark" ? <Logo width={w} /> : w === 32 ? <Logo mark /> : <MarkAt size={w} />}
              <figcaption className="type-caption text-fg-secondary">{w} px{IN_APP[w] && kind === (w === 32 ? "mark" : "wordmark") ? <span className="ml-1 font-semibold text-fg">· in app ({IN_APP[w]})</span> : null}</figcaption>
            </figure>
          ))}
        </div>
      </TabPanel>
      <p className="type-caption text-fg-muted">
        ASSURE is about 8 px tall at 140 px, 6 px at 110 px (the smallest the application uses) and 5 px at 90 px. The mark is shown with the application&apos;s
        own crop at each size. See the proposed minimums in Guidelines.
      </p>
    </Block>
  );
}

/** The symbol mark at another size: the same crop as `Logo mark` (components/Shell.tsx), scaled from its 32 px geometry. */
function MarkAt({ size }: { size: number }) {
  const k = size / 32;
  return <span role="img" aria-label="Zoiko Assure" className="block" style={{ width: size, height: size, backgroundImage: "url(/logo-mark.jpg)",
    backgroundSize: `${90 * k}px ${90 * k}px`, backgroundPosition: `${-29 * k}px ${-29 * k}px`, backgroundRepeat: "no-repeat" }} />;
}

function Monochrome() {
  return (
    <Block title="Monochrome" aside={<ProvenanceChip kind="proposed">Proposed variation · no file exists</ProvenanceChip>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <figure className="space-y-1.5">
          <div className="flex h-24 items-center justify-center rounded-control border border-dashed border-line-strong bg-white" data-theme="light">
            <span style={{ filter: "brightness(0)" }}><Logo width={140} /></span>
          </div>
          <figcaption className="type-caption text-fg-muted">Single-colour dark, simulated with <code className="type-code">filter: brightness(0)</code></figcaption>
        </figure>
        <figure className="space-y-1.5">
          <div className="flex h-24 items-center justify-center rounded-control border border-dashed border-line-strong bg-surface-sidebar" data-theme="light">
            <span style={{ filter: "brightness(0) invert(1)" }}><Logo width={140} /></span>
          </div>
          <figcaption className="type-caption text-fg-muted">Single-colour light, simulated with <code className="type-code">brightness(0) invert(1)</code></figcaption>
        </figure>
      </div>
      <p className="type-caption text-fg-muted">For fax, embossing or single-ink print. A real monochrome file would have to come from the brand owner; these previews are not assets.</p>
    </Block>
  );
}
