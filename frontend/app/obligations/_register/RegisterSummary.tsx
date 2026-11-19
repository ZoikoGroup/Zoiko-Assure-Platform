"use client";

import type { ReactNode } from "react";
import { MetricCard, MetricGroup } from "@/components/ui";
import type { RegisterFigures } from "./model";

/** What the cards can show: figures, a load still running, or a list that could not be read. A failed read is never
 *  shown as zero. */
export type SummaryStatus = { kind: "ready"; figures: RegisterFigures } | { kind: "loading" } | { kind: "unavailable" };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The register's headline counts, over the whole register rather than the filtered view. */
export function RegisterSummary({ status }: { status: SummaryStatus }) {
  const f = status.kind === "ready" ? status.figures : null;
  const value = (n: number | null | undefined): ReactNode =>
    status.kind === "loading" ? <span aria-hidden className="skeleton my-1 inline-block h-5 w-10 align-middle" />
    : n == null ? <><span aria-hidden>—</span><span className="sr-only">Not available</span></>
    : n;
  const hint = (text: string) => (status.kind === "unavailable" ? "Unavailable" : status.kind === "loading" ? "Loading…" : text);
  return (
    <MetricGroup as="section" aria-label="Register summary" aria-busy={status.kind === "loading"} className="mb-5 grid-cols-2 lg:grid-cols-4">
      <MetricCard label="Total obligations" value={value(f?.total)}
        hint={hint("Current records in this organisation's register")} />
      <MetricCard label="Adopted" code="ADOPTED" value={value(f?.adopted)}
        hint={hint("Lifecycle ADOPTED: attestation satisfied")} />
      <MetricCard label="Requiring review" code="REQUIRES_REVIEW" value={value(f?.requiresReview)}
        hint={hint("Latest frozen snapshot is REQUIRES_REVIEW")} />
      <MetricCard label="Approved exceptions" tone="exception" value={value(f?.approvedExceptions)}
        hint={hint(f ? (f.approvedExceptions == null ? "Not reported for every obligation" : `On ${plural(f.obligationsWithExceptions, "obligation")}; never change a state`) : "")} />
    </MetricGroup>
  );
}
