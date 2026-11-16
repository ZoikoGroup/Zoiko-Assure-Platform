import { Fragment } from "react";
import { ToneBadge, type BadgeOpts } from "@/components/platformDemo";
import { CATEGORY, OUTCOME, SCOPE, type Category, type Outcome, type Scope } from "./model";

/* Administrative Audit badges (the shared ToneBadge with this module's vocabulary) and the display of a recorded field
   that tells a missing value from an empty one. */

export const ScopeBadge = ({ scope, ...o }: BadgeOpts & { scope: Scope }) => <ToneBadge meta={SCOPE[scope]} kind="Scope" {...o} />;
export const CategoryBadge = ({ category, ...o }: BadgeOpts & { category: Category }) => <ToneBadge meta={CATEGORY[category]} kind="Event category" {...o} />;
/** Demonstration-only: the audit model stores no outcome. */
export const OutcomeBadge = ({ outcome, ...o }: BadgeOpts & { outcome: Outcome }) => <ToneBadge meta={OUTCOME[outcome]} kind="Outcome (demonstration only)" {...o} />;

/** A recorded field's value: the value itself, "null · not recorded" when the record holds none, or "empty value" when
 *  it holds an empty string. `code` shows identifiers and codes in code type. */
export function FieldValue({ v, code = false }: { v: string | number | null; code?: boolean }) {
  if (v === null) return <span className="italic text-fg-muted">null · not recorded</span>;
  if (v === "") return <span className="italic text-fg-muted">empty value (&quot;&quot;)</span>;
  return code ? <code className="break-all type-code text-fg">{v}</code> : <span className="break-words">{v}</span>;
}

/** An action name in code type, allowed to break after its dots and underscores on narrow columns (<wbr> hints, which
 *  copied text does not include). */
export function ActionCode({ action }: { action: string }) {
  const parts = action.split(/(?<=[._])/);
  return (
    <code className="type-code text-accent-fg">
      {parts.map((p, i) => <Fragment key={i}>{p}{i < parts.length - 1 && <wbr />}</Fragment>)}
    </code>
  );
}
