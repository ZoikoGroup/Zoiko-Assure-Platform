import type { ComponentProps } from "react";
import type { LucideIcon } from "lucide-react";
import { Loader2 } from "lucide-react";

/* Buttons on the foundation tokens. Hover and press styles apply only to enabled buttons; every variant shares one
   disabled look. Focus uses the theme's focus ring (design-tokens.css), so no variant removes the outline. */

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const BASE = "inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-button border font-semibold "
  + "transition-[background-color,border-color,color,box-shadow,filter] disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-control "
  + "disabled:bg-none disabled:text-fg-disabled disabled:shadow-none aria-busy:cursor-progress";
const SIZE: Record<ButtonSize, string> = { sm: "h-8 px-3 type-caption", md: "h-10 px-4 type-secondary", lg: "h-12 px-5 type-body" };
const SQUARE: Record<ButtonSize, string> = { sm: "h-8 w-8", md: "h-10 w-10", lg: "h-12 w-12" };
const VARIANT: Record<ButtonVariant, string> = {
  primary: "border-transparent bg-linear-to-r from-accent-strong to-accent text-on-accent shadow-glow enabled:hover:brightness-105 enabled:active:brightness-95",
  secondary: "border-line-strong bg-surface-control text-fg enabled:hover:border-fg-muted enabled:hover:bg-surface-raised enabled:active:bg-surface-card",
  outline: "border-accent bg-transparent text-accent-fg enabled:hover:bg-accent/10 enabled:active:bg-accent/20",
  ghost: "border-transparent bg-transparent text-fg-secondary enabled:hover:bg-fg/5 enabled:hover:text-fg enabled:active:bg-fg/10",
  danger: "border-transparent bg-status-critical text-fg-inverse enabled:hover:brightness-110 enabled:active:brightness-95",
};

/** Button styling for elements that are not <Button>, such as a Next.js Link that should look like one. */
export function buttonClasses({ variant = "secondary", size = "md", fullWidth = false, square = false }:
  { variant?: ButtonVariant; size?: ButtonSize; fullWidth?: boolean; square?: boolean } = {}) {
  return `${BASE} ${square ? SQUARE[size] : SIZE[size]} ${VARIANT[variant]} ${fullWidth ? "w-full" : ""}`;
}

export type ButtonProps = ComponentProps<"button"> & {
  variant?: ButtonVariant; size?: ButtonSize; icon?: LucideIcon; iconRight?: LucideIcon; fullWidth?: boolean;
  /** Busy: shows a spinner and ignores clicks, but stays focusable and keeps its look (aria-busy, aria-disabled). */
  loading?: boolean;
};

export function Button({ variant = "secondary", size = "md", icon: Icon, iconRight: IconRight, loading = false, fullWidth, className = "", children, type = "button", onClick, ...rest }: ButtonProps) {
  const px = size === "sm" ? 14 : 16;
  return (
    <button type={type} {...rest} aria-busy={loading || undefined} aria-disabled={loading || rest["aria-disabled"] || undefined}
      onClick={loading ? (e) => e.preventDefault() : onClick} className={`${buttonClasses({ variant, size, fullWidth })} ${className}`}>
      {loading ? <Loader2 size={px} aria-hidden className="motion-safe:animate-spin" /> : Icon && <Icon size={px} aria-hidden />}
      {children}
      {!loading && IconRight && <IconRight size={px} aria-hidden />}
    </button>
  );
}

/** An icon-only button. `label` is its accessible name and its hover title. */
export function IconButton({ label, icon: Icon, variant = "ghost", size = "md", className = "", type = "button", ...rest }:
  Omit<ButtonProps, "children" | "icon" | "iconRight" | "loading" | "fullWidth"> & { label: string; icon: LucideIcon }) {
  return (
    <button type={type} aria-label={label} title={label} {...rest} className={`${buttonClasses({ variant, size, square: true })} ${className}`}>
      <Icon size={size === "sm" ? 15 : 17} aria-hidden />
    </button>
  );
}
