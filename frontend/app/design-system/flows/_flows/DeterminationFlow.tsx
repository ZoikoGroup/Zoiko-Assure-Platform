"use client";

import { useState } from "react";
import { ArrowRight, Flag, Wrench } from "lucide-react";
import {
  Alert, Button, Card, ConfirmDialog, DescriptionList, Field, Select, StateBadge, Textarea, Timeline, useToast, type TimelineItem,
} from "@/components/ds";
import { fmtDate } from "@/lib/format";
import { FlowFrame, Sample, Section, delay, now, useYou } from "../../_shared/demo";

/* Determination review, mirroring Applicability Review and the determination detail (frontend/app/applicability/*):
   states PROPOSED / IN_REVIEW / ADOPTED / REJECTED, conflicting facts making an evaluation UNCERTAIN (FACT_CONFLICT),
   challenges (POST …/challenge) with the server's fixed disposition per class, and append-only review history.
   The application has no general comment thread: recorded challenges are the reviewers' comments. */

const DISPOSITION: Record<string, string> = {
  FACT_WRONG: "Record a corrected FactAssertion; a new evaluation will be created.",
  FACT_MISSING: "Resolve the linked FactRequest.",
  RULE_WRONG: "Routed to regulatory-content operations; released rule unchanged until corrected via governance.",
  INTERPRETATION: "Requires governed legal disposition; evaluation remains unchanged.",
};
type Challenge = { id: string; cls: string; detail: string; by: string; at: string };
type Revision = { id: string; state: string; outcome: string; basis: string; by: string; records: string };

export function DeterminationFlow({ onAttest }: { onAttest: () => void }) {
  return (
    <FlowFrame id="determination" title="Determination review"
      intro="See what the latest evaluation concluded and why, where the facts disagree, and what reviewers have raised. Challenge it, or, once the outcome is definite, move on to attestation."
      app={{ screen: "Applicability Review › determination", route: "/applicability", action: "POST /applicability/determinations/{lineage}/challenge", permission: "challenge",
        enforced: ["Conflicting facts make the evaluation UNCERTAIN (FACT_CONFLICT)", "UNCERTAIN outcomes cannot be adopted", "A challenge on a PROPOSED determination moves it to IN_REVIEW", "History is append-only: revisions are added, never edited"] }}
      empty={{ title: "No determinations need review", description: "Determinations appear here when an evaluation proposes or flags one." }}>
      <Review onAttest={onAttest} />
    </FlowFrame>
  );
}

function Review({ onAttest }: { onAttest: () => void }) {
  const toast = useToast();
  const you = useYou();
  const [state, setState] = useState("PROPOSED");
  const [resolved, setResolved] = useState(false);
  const [cls, setCls] = useState("");
  const [detail, setDetail] = useState("");
  const [tried, setTried] = useState(false);
  const [confirming, setConfirming] = useState<"challenge" | "correct" | null>(null);
  const [challenges, setChallenges] = useState<Challenge[]>([
    { id: "c1", cls: "INTERPRETATION", detail: "Do contractors count towards the staff threshold?", by: "Legal Reviewer", at: "2026-10-29T14:10:00Z" },
  ]);
  const [history, setHistory] = useState<Revision[]>([
    { id: "r2", state: "PROPOSED", outcome: "UNCERTAIN", basis: "Refreshed by newer evaluation", by: "Applicability engine", records: "#4182 → current" },
    { id: "r1", state: "PROPOSED", outcome: "APPLIES", basis: "First evaluation", by: "Applicability engine", records: "#4011 → #4182" },
  ]);
  const outcome = resolved ? "APPLIES" : "UNCERTAIN";
  const errors = { cls: tried && !cls ? "Choose what is wrong." : undefined, detail: tried && !detail.trim() ? "Describe the problem so the owner can act on it." : undefined };
  const add = (r: Omit<Revision, "id" | "records">) => setHistory((h) => [{ ...r, id: `r${h.length + 1}`, records: "new revision → current" }, ...h]);

  const raise = async () => {
    await delay(700);
    setChallenges((c) => [...c, { id: `c${c.length + 1}`, cls, detail: detail.trim(), by: you.name, at: now() }]);
    if (state === "PROPOSED") { setState("IN_REVIEW"); add({ state: "IN_REVIEW", outcome, basis: `Challenged: ${cls}`, by: you.name }); }
    toast("Challenge raised (demo: nothing was recorded)", "info");
    setCls(""); setDetail(""); setTried(false);
  };
  const correct = async () => {
    await delay(700);
    setResolved(true);
    setState("PROPOSED");
    add({ state: "PROPOSED", outcome: "APPLIES", basis: "Refreshed by newer evaluation", by: "Applicability engine" });
    toast("New evaluation: APPLIES (demo)", "good");
  };

  return (
    <div className="grid gap-stack xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Card bodyClassName="p-card space-y-6">
        <Section title="Determination" aside={<span className="flex items-center gap-2"><StateBadge code={state} size="sm" /><StateBadge code={outcome} size="sm" /></span>}>
          <DescriptionList items={[
            ["Determination", <span key="d">DET-DEMO-22 · Major incident reporting<Sample /></span>], ["Requirement", "NIS2 Art. 23"],
            ["Subject", "Northwind B.V. (NL)"], ["Scope condition", "Applies to entities with 250 or more staff"],
            ["Latest outcome", <span key="o" className="flex flex-wrap items-center gap-2"><StateBadge code={outcome} size="sm" />{!resolved && <code className="type-code text-fg-muted">FACT_CONFLICT</code>}</span>],
            ["Rule", "Rule version 2.4"],
          ]} />
        </Section>
        <Section title="Supporting evidence">
          <ul className="divide-y divide-line rounded-card border border-line">
            {[["entity.essential_or_important", "Important entity", "Sector registration, Feb 2026"], ["entity.sector", "Digital infrastructure", "Entity record"]].map(([f, v, s]) => (
              <li key={f} className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
                <span><code className="block type-code text-fg-muted">{f}</code><span className="type-secondary font-medium text-fg">{v}</span><span className="block type-caption text-fg-muted">{s}</span></span>
                <StateBadge code="CURRENT" size="sm" />
              </li>
            ))}
          </ul>
        </Section>
        <Section title="Conflicting evidence">
          {resolved ? (
            <Alert tone="verified" title="Conflict resolved">The HR data owner recorded a corrected value (312); both sources now agree, so the new evaluation is definite.</Alert>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                {[["312 staff", "HR system export, 12 Oct 2026", "HR data owner"], ["248 staff", "Chamber of Commerce filing, 2025", "Legal"]].map(([v, s, by]) => (
                  <div key={s} className="rounded-card border border-status-warning-line bg-status-warning-tint p-4">
                    <code className="type-code text-fg-muted">entity.staff_count</code>
                    <p className="type-card text-fg">{v}</p>
                    <p className="type-caption text-fg-secondary">{s} · asserted by {by}</p>
                  </div>
                ))}
              </div>
              <p className="type-caption text-fg-secondary">The values fall on either side of the 250 threshold, so the evaluation cannot decide: it is UNCERTAIN, reported separately, never treated as “does not apply”.</p>
            </>
          )}
        </Section>
        <Section title="Review history">
          <Timeline label="Determination revisions, newest first" items={history.map((r): TimelineItem => ({
            id: r.id, title: <span className="flex flex-wrap items-center gap-2"><StateBadge code={r.state} size="sm" /><StateBadge code={r.outcome} size="sm" /></span>,
            meta: `${r.basis} · ${r.by} · records ${r.records}`,
          }))} />
        </Section>
      </Card>

      <div className="space-y-stack">
        <Card title="Reviewer comments" description="Recorded challenges and their dispositions" bodyClassName="p-card">
          <ul className="space-y-3">
            {challenges.map((c) => (
              <li key={c.id} className="rounded-card border border-line bg-surface-raised p-3">
                <div className="flex flex-wrap items-center justify-between gap-2"><code className="type-code text-accent-fg">{c.cls}</code><StateBadge code="OPEN" size="sm" /></div>
                <p className="mt-1 type-secondary text-fg">{c.detail}</p>
                <p className="mt-1 type-caption text-fg-muted">{c.by} · {fmtDate(c.at, true)}</p>
                <p className="mt-1.5 type-caption text-fg-secondary"><span className="font-semibold">Disposition:</span> {DISPOSITION[c.cls]}</p>
              </li>
            ))}
          </ul>
          <p className="mt-3 type-caption text-fg-muted">The application has no way to resolve a challenge yet; they stay OPEN.</p>
        </Card>

        <Card title="Review actions" bodyClassName="p-card space-y-4">
          <Field label="What is wrong" required error={errors.cls}>
            <Select value={cls} onChange={(e) => setCls(e.target.value)} placeholder="Choose a challenge class" options={Object.keys(DISPOSITION).map((k) => ({ value: k, label: k }))} />
          </Field>
          <Field label="Detail" required error={errors.detail} hint={cls ? `Disposition: ${DISPOSITION[cls]}` : undefined}>
            <Textarea rows={3} value={detail} onChange={(e) => setDetail(e.target.value)} placeholder="What is incorrect or missing, and where?" />
          </Field>
          <Button variant="secondary" icon={Flag} fullWidth onClick={() => { setTried(true); if (cls && detail.trim()) setConfirming("challenge"); }}>Raise challenge</Button>
          {!resolved && <Button variant="ghost" icon={Wrench} fullWidth onClick={() => setConfirming("correct")}>Simulate: fact owner corrects the value</Button>}
          <div className="border-t border-line pt-4">
            <Button variant="primary" iconRight={ArrowRight} fullWidth disabled={!resolved} onClick={onAttest}>Proceed to attestation</Button>
            {!resolved && <p className="mt-2 type-caption text-fg-muted">Unavailable while the outcome is UNCERTAIN: it cannot be adopted until the conflicting facts are resolved.</p>}
          </div>
        </Card>
      </div>

      {confirming === "challenge" && (
        <ConfirmDialog title="Raise this challenge?" confirmLabel="Raise challenge" busyLabel="Raising…" onClose={() => setConfirming(null)} onConfirm={raise}>
          <p><code className="type-code">{cls}</code>: {detail.trim()}</p>
          <p>{state === "PROPOSED" ? "The determination moves to IN_REVIEW." : "The determination stays IN_REVIEW."} Disposition recorded: {DISPOSITION[cls]}</p>
        </ConfirmDialog>
      )}
      {confirming === "correct" && (
        <ConfirmDialog title="Simulate a corrected fact?" confirmLabel="Record correction" busyLabel="Re-evaluating…" onClose={() => setConfirming(null)} onConfirm={correct}>
          <p>Stands in for the fact owner recording a corrected value. A new evaluation runs and the determination returns to PROPOSED (“Refreshed by newer evaluation”).</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
