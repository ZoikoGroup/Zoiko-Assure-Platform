/* The Unified Design System token registry: what each foundation token is called, which utility applies it and what
   it is for. Values are deliberately absent. They live only in app/design-tokens.css and are read from the rendered
   page (getComputedStyle), so this file can never disagree with the CSS. */

export type CssVar = `--${string}`;
export type ColorToken = {
  name: string; cssVar: CssVar; utility: string; role: string;
  /** Backgrounds this colour is checked against, and the minimum ratio (4.5 text, 3 non-text). */
  contrastOn?: CssVar[]; min?: number;
};
export type Group<T> = { id: string; title: string; description: string; tokens: T[] };

const ON_SURFACES: CssVar[] = ["--za-surface-app", "--za-surface-card"];

export const COLOR_GROUPS: Group<ColorToken>[] = [
  { id: "brand", title: "Brand", description: "Orange/gold leads, blue supports. Navy and charcoal carry the dark surfaces.", tokens: [
    { name: "Primary · gold", cssVar: "--za-accent", utility: "bg-accent", role: "Primary actions, active navigation, key highlights" },
    { name: "Primary strong", cssVar: "--za-accent-strong", utility: "from-accent-strong", role: "Pressed state and gradient start" },
    { name: "Primary text", cssVar: "--za-accent-fg", utility: "text-accent-fg", role: "Accent-coloured text and icons on surfaces", contrastOn: ON_SURFACES },
    { name: "On primary", cssVar: "--za-on-accent", utility: "text-on-accent", role: "Text and icons on a primary fill (white fails contrast)", contrastOn: ["--za-accent", "--za-accent-strong"] },
    { name: "Secondary · blue", cssVar: "--za-accent-secondary", utility: "bg-accent-secondary", role: "Secondary emphasis; the platform control plane" },
    { name: "Secondary text", cssVar: "--za-accent-secondary-fg", utility: "text-accent-secondary-fg", role: "Blue text and icons on surfaces", contrastOn: ON_SURFACES },
    { name: "Dark navy", cssVar: "--color-midnight-950", utility: "bg-midnight-950", role: "Brand dark navy primitive" },
    { name: "Deep charcoal", cssVar: "--color-charcoal-900", utility: "bg-charcoal-900", role: "Brand deep charcoal primitive" },
  ] },
  { id: "background", title: "Background", description: "Surfaces step up in lightness as they rise towards the user.", tokens: [
    { name: "Application", cssVar: "--za-surface-app", utility: "bg-surface-app", role: "Page background behind everything" },
    { name: "Sidebar", cssVar: "--za-surface-sidebar", utility: "bg-surface-sidebar", role: "Primary navigation rail" },
    { name: "Elevated surface", cssVar: "--za-surface-raised", utility: "bg-surface-raised", role: "Panels and grouped sections" },
    { name: "Card", cssVar: "--za-surface-card", utility: "bg-surface-card", role: "Cards and tiles inside panels" },
    { name: "Modal", cssVar: "--za-surface-modal", utility: "bg-surface-modal", role: "Dialogs, drawers and popovers" },
    { name: "Control", cssVar: "--za-surface-control", utility: "bg-surface-control", role: "Inputs, selects and secondary buttons" },
  ] },
  { id: "text", title: "Text", description: "Checked against the application and card surfaces.", tokens: [
    { name: "Primary text", cssVar: "--za-fg", utility: "text-fg", role: "Headings and body copy", contrastOn: ON_SURFACES },
    { name: "Secondary text", cssVar: "--za-fg-secondary", utility: "text-fg-secondary", role: "Supporting copy and descriptions", contrastOn: ON_SURFACES },
    { name: "Muted text", cssVar: "--za-fg-muted", utility: "text-fg-muted", role: "Labels, captions and identifiers", contrastOn: ON_SURFACES },
    { name: "Disabled text", cssVar: "--za-fg-disabled", utility: "text-fg-disabled", role: "Inactive controls (exempt from contrast minimums)" },
    { name: "Inverse text", cssVar: "--za-fg-inverse", utility: "text-fg-inverse", role: "Text on a primary-text-coloured fill", contrastOn: ["--za-fg"] },
    { name: "Sidebar text", cssVar: "--za-sidebar-fg", utility: "text-sidebar-fg", role: "Navigation items", contrastOn: ["--za-surface-sidebar"] },
    { name: "Sidebar muted", cssVar: "--za-sidebar-muted", utility: "text-sidebar-muted", role: "Navigation group labels", contrastOn: ["--za-surface-sidebar"] },
  ] },
  { id: "line", title: "Borders & focus", description: "Hairlines separate; the focus ring must stand out (3:1).", tokens: [
    { name: "Border", cssVar: "--za-line", utility: "border-line", role: "Card, panel and divider hairlines" },
    { name: "Border strong", cssVar: "--za-line-strong", utility: "border-line-strong", role: "Controls and hover outlines" },
    { name: "Focus ring", cssVar: "--za-focus", utility: "outline-focus", role: "Keyboard focus indicator", contrastOn: ON_SURFACES, min: 3 },
  ] },
];

export type StatusKey = "verified" | "warning" | "critical" | "unknown" | "info";
export type Status = { key: StatusKey; label: string; token: string; meaning: string; current: string };

/** The canonical status vocabulary. Visual only: backend codes and their meanings are unchanged. `current` names the
 *  existing StatusPill tone (components/ui.tsx) that shows the same family today. */
export const STATUSES: Status[] = [
  { key: "verified", label: "Verified", token: "status.verified", meaning: "Proven by current, verified evidence.", current: "good" },
  { key: "warning", label: "Needs Review", token: "status.warning", meaning: "A person must look before it can be relied on.", current: "warn" },
  { key: "critical", label: "Critical", token: "status.critical", meaning: "Failing, invalid or refused. Act now.", current: "bad" },
  { key: "unknown", label: "Stale / Unknown", token: "status.unknown", meaning: "Not evaluated, out of date or unrecognised.", current: "neutral, unknown" },
  { key: "info", label: "Informational", token: "status.info", meaning: "Context with no success or failure meaning.", current: "info" },
];
export const statusVars = (k: StatusKey) => ({ fg: `--za-status-${k}` as CssVar, tint: `--za-status-${k}-tint` as CssVar, line: `--za-status-${k}-line` as CssVar });

export type TypeRole = { name: string; utility: string; sample: string; usage: string };
export const TYPE_ROLES: TypeRole[] = [
  { name: "Display heading", utility: "type-display", sample: "Unified Design System", usage: "One per workspace landing page" },
  { name: "Page heading", utility: "type-page", sample: "Organization Command Center", usage: "The page title (h1)" },
  { name: "Section heading", utility: "type-section", sample: "Architecture / two authority planes", usage: "Panel and section titles" },
  { name: "Card heading", utility: "type-card", sample: "Platform Control Plane", usage: "Card and dialog titles" },
  { name: "Body", utility: "type-body", sample: "Shared tokens, shared components, different policy gates.", usage: "Paragraphs and descriptions" },
  { name: "Secondary text", utility: "type-secondary", sample: "Official Journal of the EU · 28 Oct 2026", usage: "Supporting text, table cells, metadata" },
  { name: "Label", utility: "type-label", sample: "Design governance", usage: "Navigation groups, field labels, eyebrows" },
  { name: "Caption", utility: "type-caption", sample: "Recorded 09:42 UTC by the server", usage: "Footnotes, timestamps, helper text" },
  { name: "Monospace identifier", utility: "type-code", sample: "status.verified · ZA-DS-FIG-003", usage: "Codes, token names, record IDs, digests" },
];

export type SizeToken = { name: string; cssVar: CssVar; utility: string; role: string };
export const SPACING: SizeToken[] = [
  { name: "Page gutter", cssVar: "--spacing-gutter", utility: "px-gutter", role: "Page side margins" },
  { name: "Page block", cssVar: "--spacing-page", utility: "py-page", role: "Space above and below page content" },
  { name: "Sidebar width", cssVar: "--spacing-sidebar", utility: "w-sidebar", role: "Primary navigation rail" },
  { name: "Sidebar inset", cssVar: "--spacing-sidebar-inset", utility: "p-sidebar-inset", role: "Padding inside the sidebar" },
  { name: "Header height", cssVar: "--spacing-header", utility: "h-header", role: "Top navigation bar" },
  { name: "Panel padding", cssVar: "--spacing-panel", utility: "p-panel", role: "Inside large panels" },
  { name: "Card padding", cssVar: "--spacing-card", utility: "p-card", role: "Inside cards and tiles" },
  { name: "Section gap", cssVar: "--spacing-section", utility: "gap-section", role: "Between page sections" },
  { name: "Stack gap", cssVar: "--spacing-stack", utility: "gap-stack", role: "Between cards inside a panel" },
  { name: "Form gap", cssVar: "--spacing-form", utility: "gap-form", role: "Between form fields" },
  { name: "Table cell (x)", cssVar: "--spacing-cell-x", utility: "px-cell-x", role: "Table cell horizontal padding" },
  { name: "Table cell (y)", cssVar: "--spacing-cell-y", utility: "py-cell-y", role: "Table cell vertical padding" },
];

export const RADII: SizeToken[] = [
  { name: "Chip", cssVar: "--radius-chip", utility: "rounded-chip", role: "Badges, chips, tags" },
  { name: "Control", cssVar: "--radius-control", utility: "rounded-control", role: "Inputs, selects, icon boxes" },
  { name: "Button", cssVar: "--radius-button", utility: "rounded-button", role: "Buttons" },
  { name: "Navigation", cssVar: "--radius-nav", utility: "rounded-nav", role: "Sidebar and tab items" },
  { name: "Card", cssVar: "--radius-card", utility: "rounded-card", role: "Cards and tiles" },
  { name: "Modal", cssVar: "--radius-modal", utility: "rounded-modal", role: "Dialogs and drawers" },
  { name: "Panel", cssVar: "--radius-panel", utility: "rounded-panel", role: "Large panels and the sidebar shell" },
];

export const SHADOWS: SizeToken[] = [
  { name: "Card", cssVar: "--za-shadow-card", utility: "shadow-card", role: "Resting cards" },
  { name: "Panel", cssVar: "--za-shadow-panel", utility: "shadow-panel", role: "Panels and the sidebar shell" },
  { name: "Modal", cssVar: "--za-shadow-modal", utility: "shadow-modal", role: "Dialogs above the page" },
  { name: "Accent glow", cssVar: "--za-shadow-glow", utility: "shadow-glow", role: "Active navigation and primary action only" },
];

/** Tailwind's default breakpoints, which the layout rules use unchanged. */
export const BREAKPOINTS = [
  { name: "sm", min: 640, rule: "Two-column card grids; tables replace stacked lists" },
  { name: "md", min: 768, rule: "Side-by-side panels; search appears in the header" },
  { name: "lg", min: 1024, rule: "Persistent sidebar replaces the mobile navigation" },
  { name: "xl", min: 1280, rule: "Dashboard grids reach full width (6 KPIs, 2:1 columns)" },
  { name: "2xl", min: 1536, rule: "Content stops at the 90rem maximum and centres" },
];

/* ----------------------------------------------------------------- colour maths (WCAG 2.2 relative luminance) */
export type Rgba = [number, number, number, number];

/** Parses the colour forms used in design-tokens.css: #rgb, #rrggbb(aa) and rgb()/rgba() in comma or space syntax. */
export function parseColor(value: string): Rgba | null {
  const s = value.trim().toLowerCase();
  const hex = s.match(/^#([0-9a-f]{3,8})$/);
  if (hex) {
    const h = hex[1].length <= 4 ? [...hex[1]].map((c) => c + c).join("") : hex[1];
    const n = (i: number) => (i < h.length ? parseInt(h.slice(i, i + 2), 16) : 255);
    return [n(0), n(2), n(4), n(6) / 255];
  }
  const fn = s.match(/^rgba?\(([^)]+)\)$/);
  if (!fn) return null;
  const p = fn[1].split(/[\s,/]+/).filter(Boolean);
  const a = p[3] === undefined ? 1 : p[3].endsWith("%") ? parseFloat(p[3]) / 100 : Number(p[3]);
  return [Number(p[0]), Number(p[1]), Number(p[2]), a];
}

/** A translucent colour as it appears over an opaque background. */
export const over = (fg: Rgba, bg: Rgba): Rgba => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1) as Rgba;

const channel = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const luminance = (c: Rgba) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
export function contrast(a: Rgba, b: Rgba): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
