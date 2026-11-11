"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy, X } from "lucide-react";
import { IconButton, STATUS_ICON, STATUS_TONE, useToast, type StatusKey } from "@/components/ds";
import { contrast, over, parseColor, type Rgba } from "@/lib/design-tokens";

/* Shared helpers for the Icons & Brand Kit. Colour values are never written here: they are read from the stylesheet
   (app/design-tokens.css, app/globals.css) through hidden probes, and artwork colours are measured from the logo files
   themselves, so this page cannot drift from either. */

export type ThemeName = "light" | "dark";
export type ThemeVals = Record<ThemeName, Record<string, string>>;

/** Each variable's value in BOTH themes, read from hidden `data-theme` probes (the same method as Export Library). */
export function useThemeVars(vars: readonly string[]): ThemeVals | null {
  const [vals, setVals] = useState<ThemeVals | null>(null);
  const key = vars.join("|");
  useEffect(() => {
    const read = (theme: ThemeName) => {
      const probe = document.createElement("div");
      probe.dataset.theme = theme;
      probe.hidden = true;
      document.body.appendChild(probe);
      const cs = getComputedStyle(probe);
      const out = Object.fromEntries(key.split("|").map((v) => [v, cs.getPropertyValue(v).trim()]));
      probe.remove();
      return out;
    };
    setVals({ light: read("light"), dark: read("dark") });
  }, [key]);
  return vals;
}

const h2 = (n: number) => Math.round(n).toString(16).padStart(2, "0");
/** A stylesheet colour as HEX (#RRGGBB, or #RRGGBBAA when translucent); null when it is not a plain colour. */
export function toHex(value: string | undefined): string | null {
  const c = value ? parseColor(value) : null;
  if (!c) return null;
  return `#${h2(c[0])}${h2(c[1])}${h2(c[2])}${c[3] < 1 ? h2(c[3] * 255) : ""}`.toUpperCase();
}

/** Contrast of `fg` over `bg`; translucent colours are composited over `base` (the card) first. */
export function ratio(fg: string | undefined, bg: string | undefined, base?: string): number | null {
  const f = fg ? parseColor(fg) : null, b = bg ? parseColor(bg) : null, c = base ? parseColor(base) : null;
  if (!f || !b) return null;
  const back: Rgba = b[3] < 1 && c ? over(b, c) : b;
  return contrast(f[3] < 1 ? over(f, back) : f, back);
}

/** Copies text with the async clipboard API, falling back to a hidden textarea (insecure origins, older browsers). */
export async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch { return false; }
}

/** An icon button that copies `text` and confirms with a toast. */
export function CopyButton({ text, label, size = "sm" }: { text: string; label: string; size?: "sm" | "md" }) {
  const toast = useToast();
  return <IconButton size={size} icon={Copy} label={`Copy ${label}`} data-copy={text}
    onClick={async () => { const ok = await copyText(text); toast(ok ? `Copied ${label}${label.includes(text) ? "" : `: ${text}`}` : `Could not copy ${label}. Select it and copy it manually.`, ok ? "good" : "warn"); }} />;
}

/* ---------------------------------------------------------------- provenance: what is official, derived or proposed */
export type Provenance = "official" | "derived" | "proposed" | "established" | "observed";
const PROV: Record<Provenance, { label: string; status: StatusKey; dashed?: boolean; title: string }> = {
  official: { label: "Official asset", status: "verified", title: "Supplied with the platform's initial implementation and used unchanged" },
  derived: { label: "Derived · in use", status: "warning", title: "Made from an official asset during this design work and used in the application; needs brand-owner approval" },
  proposed: { label: "Proposed", status: "info", dashed: true, title: "A recommendation or simulation: not an asset or rule of the application today" },
  established: { label: "Established", status: "verified", title: "Already implemented in the application; the source is named" },
  observed: { label: "Observed", status: "unknown", title: "Measured from the current code; a description of today, not a rule" },
};
export function ProvenanceChip({ kind, children }: { kind: Provenance; children?: ReactNode }) {
  const p = PROV[kind];
  const Icon = STATUS_ICON[p.status];
  return (
    <span title={p.title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border px-1.5 type-caption font-semibold ${STATUS_TONE[p.status]} ${p.dashed ? "border-dashed" : ""}`}>
      <Icon size={11} strokeWidth={2.5} aria-hidden />{children ?? p.label}
    </span>
  );
}

/** A do / don't verdict. It borrows the status colours but not the status vocabulary (no "Verified" or "Critical"). */
export function Verdict({ ok, children }: { ok: boolean; children: ReactNode }) {
  const Icon = ok ? Check : X;
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border px-1.5 type-caption font-semibold ${STATUS_TONE[ok ? "verified" : "critical"]}`}>
      <Icon size={11} strokeWidth={2.5} aria-hidden />{children}
    </span>
  );
}

/** A labelled group inside an area. */
export const Block = ({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) => (
  <section className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="type-label text-fg-muted">{title}</h3>{aside}</div>
    {children}
  </section>
);

/* ---------------------------------------------------------------- artwork colours, measured from the files */
export type Swatch = { hex: string; rgb: Rgba; share: number };

/** The dominant opaque colours of an image, measured in the browser (canvas). Near-white pixels are ignored (the
 *  JPEG mark has a white canvas). Pixels are grouped into 32-level buckets and each bucket reports its mean colour. */
export function useArtworkColours(src: string, count = 3): { colours: Swatch[] | null; error: boolean } {
  const [state, setState] = useState<{ colours: Swatch[] | null; error: boolean }>({ colours: null, error: false });
  useEffect(() => {
    let live = true;
    const img = new Image();
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
        ctx.drawImage(img, 0, 0);
        const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        const buckets = new Map<number, [number, number, number, number]>();
        let total = 0;
        for (let i = 0; i < d.length; i += 4) {
          const [r, g, b, a] = [d[i], d[i + 1], d[i + 2], d[i + 3]];
          if (a < 250 || (r > 225 && g > 225 && b > 225)) continue;
          const k = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
          const e = buckets.get(k) ?? [0, 0, 0, 0];
          e[0] += r; e[1] += g; e[2] += b; e[3] += 1;
          buckets.set(k, e);
          total++;
        }
        const top = [...buckets.values()].sort((a, b) => b[3] - a[3]).slice(0, count)
          .map(([r, g, b, n]) => { const rgb: Rgba = [r / n, g / n, b / n, 1]; return { rgb, hex: toHex(`rgb(${Math.round(rgb[0])}, ${Math.round(rgb[1])}, ${Math.round(rgb[2])})`)!, share: n / total }; });
        if (live) setState({ colours: top, error: false });
      } catch { if (live) setState({ colours: null, error: true }); }
    };
    img.onerror = () => { if (live) setState({ colours: null, error: true }); };
    img.src = src;
    return () => { live = false; };
  }, [src, count]);
  return state;
}

/** The token whose value is nearest to `rgb` (Euclidean RGB distance), with the largest channel difference. */
export function nearestToken(rgb: Rgba, candidates: Record<string, string>): { token: string; hex: string; delta: number } | null {
  let best: { token: string; hex: string; delta: number; dist: number } | null = null;
  for (const [token, value] of Object.entries(candidates)) {
    const c = parseColor(value);
    if (!c) continue;
    const dist = Math.hypot(c[0] - rgb[0], c[1] - rgb[1], c[2] - rgb[2]);
    if (!best || dist < best.dist) best = { token, hex: toHex(value)!, dist, delta: Math.round(Math.max(...[0, 1, 2].map((i) => Math.abs(c[i] - rgb[i])))) };
  }
  return best && { token: best.token, hex: best.hex, delta: best.delta };
}
