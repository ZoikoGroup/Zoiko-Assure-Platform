"use client";

import { useEffect, useId } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { STATUS_TONE, type StatusKey } from "@/components/ds";

/* Small pieces shared by the Super Admin screens that run on demonstration data (Source Integrity, JIT Access,
   Administrative Audit): a labelled status badge, the "Synthetic" marker, the muted "no value" text, UTC time
   formatting, a titled drawer section and a hook that keeps a selected tab in view. Each status badge pairs its colour
   with an icon and words, so no status depends on colour alone. */

/** One value of a status vocabulary: its words, visual family, icon and one-sentence meaning (the badge's tooltip). */
export type BadgeMeta = { label: string; tone: StatusKey; icon: LucideIcon; meaning: string };

/** `labelled`: a visible label beside the badge already names what it is (a description-list term), so the badge does
 *  not repeat it to screen readers. Elsewhere the badge says what it is ("Integrity status: Verified"). */
export type BadgeOpts = { size?: "sm" | "md"; labelled?: boolean };

export function ToneBadge({ meta, kind, size = "md", labelled = false }: BadgeOpts & { meta: BadgeMeta; kind: string }) {
  const Icon = meta.icon;
  return (
    <span title={`${meta.label}: ${meta.meaning}`}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-chip border type-caption font-semibold ${size === "sm" ? "px-1.5" : "px-2 py-0.5"} ${STATUS_TONE[meta.tone]}`}>
      <Icon size={12} strokeWidth={2.5} aria-hidden className="shrink-0" />{!labelled && <span className="sr-only">{kind}: </span>}{meta.label}
    </span>
  );
}

/** Marks demonstration figures, as the header's "Synthetic demo data" badge describes them. */
export function DemoTag({ className = "" }: { className?: string }) {
  return (
    <span title="Demonstration data: fictional, not read from this deployment"
      className={`inline-flex shrink-0 items-center rounded-chip border border-dashed border-line-strong bg-fg/5 px-1.5 text-[10px] font-semibold uppercase leading-4 tracking-[0.06em] text-fg-secondary ${className}`}>
      Synthetic<span className="sr-only"> demonstration data</span>
    </span>
  );
}

/** A value the record holds, or a muted note when it holds none. */
export function Missing({ children }: { children: ReactNode }) {
  return <span className="text-fg-muted">{children}</span>;
}

/** The time of day in UTC, the platform's operating time zone ("06:00 UTC"). */
export const fmtTimeUtc = (iso: string) =>
  `${new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC`;

/** A titled section inside a detail drawer (the drawer's own title is the h3, so sections use h4). */
export function DrawerSection({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <div className="mb-2 flex flex-wrap items-center gap-2"><h4 id={id} className="type-secondary font-semibold text-fg">{title}</h4>{aside}</div>
      {children}
    </section>
  );
}

/** Keeps the selected tab of a design-system Tabs row (`id`) fully visible. On narrow screens the row scrolls sideways
 *  inside itself, and a partly visible tab is not scrolled further when it takes focus, so this scrolls the row (only
 *  the row, never the page) whenever the selection changes. The tab element id follows components/ds/Navigation.tsx. */
export function useTabInView(id: string, value: string) {
  useEffect(() => {
    const tab = document.getElementById(`${id}-tab-${value.replace(/[^A-Za-z0-9]+/g, "-")}`);
    const row = tab?.closest<HTMLElement>('[role="tablist"]');
    if (!tab || !row) return;
    const t = tab.getBoundingClientRect(), r = row.getBoundingClientRect();
    if (t.right > r.right) row.scrollLeft += t.right - r.right;
    else if (t.left < r.left) row.scrollLeft -= r.left - t.left;
  }, [id, value]);
}
