import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Building2, ServerCog } from "lucide-react";

/* The two authority planes from Foundations, as reusable markers. The Platform Control Plane (Super Admin) is blue;
   the Customer Assurance Workspace (organisations) is orange. A plane marker is orientation only: which plane a
   screen belongs to, and what it can reach, is decided and enforced by the server. */

export type Plane = "platform" | "workspace";
export const PLANES: Record<Plane, { label: string; who: string; icon: LucideIcon; chip: string; edge: string; text: string }> = {
  platform: { label: "Platform Control Plane", who: "Super Admin", icon: ServerCog, chip: "border-accent-secondary/50 bg-accent-secondary/10 text-accent-secondary-fg", edge: "border-l-accent-secondary", text: "text-accent-secondary-fg" },
  workspace: { label: "Customer Assurance Workspace", who: "Organization", icon: Building2, chip: "border-accent/50 bg-accent/10 text-accent-fg", edge: "border-l-accent", text: "text-accent-fg" },
};

/** A chip naming the plane a screen or component belongs to. */
export function PlaneBadge({ plane, withWho = false, size = "md" }: { plane: Plane; withWho?: boolean; size?: "sm" | "md" }) {
  const p = PLANES[plane];
  const Icon = p.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-chip border font-semibold ${size === "sm" ? "px-1.5 type-caption" : "px-2 py-1 type-caption"} ${p.chip}`}>
      <Icon size={size === "sm" ? 12 : 13} aria-hidden />{p.label}{withWho && <span className="font-normal opacity-80">· {p.who}</span>}
    </span>
  );
}

/** A plane strip for the top of a plane's screens: the plane, and what it can and cannot reach. */
export function PlaneBanner({ plane, children }: { plane: Plane; children: ReactNode }) {
  return (
    <div className={`flex flex-wrap items-start gap-x-4 gap-y-2 rounded-card border border-l-4 border-line bg-surface-card/70 px-4 py-3 ${PLANES[plane].edge}`}>
      <PlaneBadge plane={plane} withWho />
      <div className="min-w-0 flex-1 type-caption text-fg-secondary">{children}</div>
    </div>
  );
}
