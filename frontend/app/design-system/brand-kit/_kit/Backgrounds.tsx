"use client";

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Callout, TabPanel, Tabs } from "@/components/ds";
import { Logo } from "@/components/Shell";
import { ASSETS } from "./Logos";
import { Block, ProvenanceChip, Verdict, ratio, toHex, useArtworkColours } from "./shared";

/* B. Logo backgrounds and usage. Backgrounds are design-token surfaces rendered in a fixed theme; their colours are
   read from the rendered element and compared with the colours measured from the logo file. Misuse examples are CSS
   transforms and filters on the unchanged files. */

type Bg = { id: string; label: string; theme: "light" | "dark"; cls: string; token: string };
const BACKGROUNDS: Bg[] = [
  { id: "white", label: "White", theme: "light", cls: "bg-white", token: "white" },
  { id: "light", label: "Light surface", theme: "light", cls: "bg-surface-app", token: "--za-surface-app (light)" },
  { id: "navy", label: "Navy sidebar", theme: "light", cls: "bg-surface-sidebar", token: "--za-surface-sidebar (light)" },
  { id: "dark", label: "Dark surface", theme: "dark", cls: "bg-surface-app", token: "--za-surface-app (dark)" },
  { id: "brand", label: "Brand orange", theme: "light", cls: "bg-accent", token: "--za-accent (light)" },
];
/** Established in code: which file the application puts on which background. */
const FITS: Record<string, string[]> = { wordmark: ["white", "light"], "on-dark": ["navy", "dark"], mark: ["white", "light", "navy", "dark"] };

const Mark = ({ id, width = 160 }: { id: string; width?: number }) => (id === "mark" ? <Logo mark /> : <Logo width={width} onDark={id === "on-dark"} />);

export function Backgrounds() {
  const [asset, setAsset] = useState<string>("wordmark");
  const [bgId, setBgId] = useState("white");
  const bg = BACKGROUNDS.find((b) => b.id === bgId)!;
  const file = ASSETS.find((a) => a.id === asset)!.file;
  const { colours } = useArtworkColours(`/${file}`);
  const stage = useRef<HTMLDivElement>(null);
  const [bgColour, setBgColour] = useState<string>("");
  useEffect(() => { if (stage.current) setBgColour(getComputedStyle(stage.current).backgroundColor); }, [bgId]);
  const recommended = FITS[asset].includes(bgId);

  return (
    <div className="space-y-section">
      <Block title="Background compatibility" aside={<ProvenanceChip kind="established">Pairings follow components/Shell.tsx</ProvenanceChip>}>
        <div className="flex flex-wrap gap-3">
          <Tabs id="bk-bg-asset" label="Logo file" variant="pills" value={asset} onChange={setAsset}
            items={ASSETS.map((a) => ({ value: a.id, label: a.file }))} />
          <Tabs id="bk-bg-surface" label="Background" variant="pills" value={bgId} onChange={setBgId}
            items={BACKGROUNDS.map((b) => ({ value: b.id, label: b.label }))} />
        </div>
        <TabPanel id="bk-bg-surface" value={bgId} className="">
          <div className="grid gap-stack lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div ref={stage} data-theme={bg.theme} className={`flex min-h-44 items-center justify-center rounded-card border border-line ${bg.cls}`}>
              <Mark id={asset} width={200} />
            </div>
            <div className="space-y-3 rounded-card border border-line bg-surface-card p-card">
              <p className="type-secondary text-fg"><code className="type-code">{file}</code> on <span className="font-semibold">{bg.label}</span></p>
              <Verdict ok={recommended}>{recommended ? "Used this way in the app" : "Not a pairing the app uses"}</Verdict>
              <p className="type-caption text-fg-muted">Background {bg.token}: <code className="type-code">{toHex(bgColour) ?? "…"}</code></p>
              {asset === "mark" ? <p className="type-caption text-fg-secondary">The mark carries its own orange tile, so it reads on any of these surfaces.</p> : (
                <ul className="space-y-1.5" aria-label="Contrast of each artwork colour against this background">
                  {(colours ?? []).map((c, i) => {
                    const r = ratio(c.hex, bgColour);
                    return (
                      <li key={i} className="flex items-center justify-between gap-2 type-caption">
                        <span className="inline-flex items-center gap-1.5"><span aria-hidden className="h-3.5 w-3.5 rounded-chip border border-line" style={{ backgroundColor: c.hex }} /><code className="type-code">{c.hex}</code></span>
                        {r !== null && <span className={`font-semibold tabular-nums ${r >= 3 ? "text-status-verified" : "text-status-critical"}`}>{r.toFixed(2)}:1 {r >= 3 ? "legible" : "too faint"}</span>}
                      </li>
                    );
                  })}
                </ul>
              )}
              <p className="type-caption text-fg-muted">Logos are exempt from WCAG contrast minimums; 3:1 (the graphics threshold) is used here as a legibility guide.</p>
            </div>
          </div>
        </TabPanel>
      </Block>

      <Block title="Correct and incorrect use" aside={<ProvenanceChip kind="proposed">Illustrations · the files are unchanged</ProvenanceChip>}>
        <div className="grid gap-stack sm:grid-cols-2 xl:grid-cols-4">
          <Example ok title="Wordmark on white" why="The pairing the staff sidebar and sign-in use." bg="bg-white"><Mark id="wordmark" /></Example>
          <Example ok title="Dark-surface wordmark on navy" why="The organization sidebar's pairing." bg="bg-surface-sidebar"><Mark id="on-dark" /></Example>
          <Example ok title="Symbol mark on a dark surface" why="The tile keeps its own contrast." bg="bg-surface-app" theme="dark"><Mark id="mark" /></Example>
          <Example ok title="Clear space kept" why="Room around the artwork; nothing touches it." bg="bg-white"><span className="p-4"><Mark id="wordmark" width={130} /></span></Example>
          <Example title="Primary wordmark on navy" why="The navy letters disappear. Use the dark-surface file." bg="bg-surface-sidebar"><Mark id="wordmark" /></Example>
          <Example title="Dark-surface file on white" why="The white letters disappear. Use logo.png." bg="bg-white"><Mark id="on-dark" /></Example>
          <Example title="On the brand orange" why="ASSURE and the Z stroke merge into the fill." bg="bg-accent"><Mark id="wordmark" /></Example>
          <Example title="Stretched" why="Never scale one axis: the proportions are part of the mark." bg="bg-white" art={{ transform: "scaleX(1.45)" }}><Mark id="wordmark" width={120} /></Example>
          <Example title="Squashed" why="The same distortion vertically." bg="bg-white" art={{ transform: "scaleY(0.6)" }}><Mark id="wordmark" /></Example>
          <Example title="Recoloured" why="Brand colours are fixed; never tint or filter the artwork." bg="bg-white" art={{ filter: "hue-rotate(150deg) saturate(1.4)" }}><Mark id="wordmark" /></Example>
          <Example title="Insufficient clear space" why="Text and edges crowd the artwork." bg="bg-white" tight>
            <span className="flex items-center gap-0.5"><Mark id="wordmark" width={120} /><span className="type-card font-bold text-fg">Portal</span></span>
          </Example>
          <Example title="Effects added" why="No shadows, outlines or glows on the artwork." bg="bg-white" art={{ filter: "drop-shadow(3px 3px 0 rgb(0 0 0 / 0.55))" }}><Mark id="wordmark" /></Example>
        </div>
      </Block>

      <Callout tone="warning" title="Symbol mark on dark surfaces: check the corners">
        <code className="type-code">logo-mark.jpg</code> has no transparency. The application&apos;s 32 px crop shows the orange tile edge to edge, so the white canvas
        can show at the tile&apos;s rounded corners on dark sidebars. A transparent PNG or SVG of the mark from the brand owner would remove this; see Known limitations.
      </Callout>
    </div>
  );
}

function Example({ ok = false, title, why, bg, theme = "light", art, tight = false, children }: {
  ok?: boolean; title: string; why: string; bg: string; theme?: "light" | "dark"; art?: CSSProperties; tight?: boolean; children: ReactNode;
}) {
  return (
    <figure className="flex flex-col overflow-hidden rounded-card border border-line bg-surface-card">
      <div data-theme={theme} className={`flex h-28 items-center justify-center overflow-hidden ${bg} ${tight ? "outline-2 -outline-offset-8 outline-fg" : ""}`}>
        <span style={art} className="inline-flex">{children}</span>
      </div>
      <figcaption className="space-y-1 p-3">
        <Verdict ok={ok}>{ok ? "Do" : "Don't"}</Verdict>
        <p className="type-secondary font-semibold text-fg">{title}</p>
        <p className="type-caption text-fg-secondary">{why}</p>
      </figcaption>
    </figure>
  );
}
