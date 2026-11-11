"use client";

import { cloneElement, isValidElement, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CSSProperties, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Inbox, Loader2, RotateCw, X } from "lucide-react";
import { ToastContext, Tooltip as BaseTooltip, useAction, useDialog, useToastQueue, type ToastTone } from "@/components/ui";
import { ApiError, errText } from "@/lib/api";
import { Button, IconButton } from "./Button";
import { STATUS_ICON, STATUS_SURFACE, STATUS_TEXT, StatusMark, statusLabel, type StatusKey } from "./Status";

/* Feedback on the foundation tokens. Behaviour is shared with the application kit (components/ui.tsx): dialogs use its
   useDialog (focus trap, Escape for the topmost overlay, focus restored), confirmations its useAction, toasts its
   queue and context (so useToast() works under either provider), and Tooltip its hover/focus/Escape logic. */

export { useToast } from "@/components/ui";

/* -------------------------------------------------------------------------------------------------- Alert */
export type AlertTone = Exclude<StatusKey, "unknown">;

/** An inline message. `live` announces it when it appears: "assertive" for errors that block, "polite" otherwise. */
export function Alert({ tone = "info", title, children, onDismiss, live = false, className = "" }:
  { tone?: AlertTone; title?: ReactNode; children?: ReactNode; onDismiss?: () => void; live?: false | "polite" | "assertive"; className?: string }) {
  const Icon = STATUS_ICON[tone];
  return (
    <div role={live === "assertive" ? "alert" : live === "polite" ? "status" : undefined}
      className={`flex items-start gap-3 rounded-control border px-3.5 py-3 ${STATUS_SURFACE[tone]} ${className}`}>
      <Icon size={17} aria-hidden className={`mt-0.5 shrink-0 ${STATUS_TEXT[tone]}`} />
      <div className="min-w-0 flex-1 type-secondary">
        <span className="sr-only">{statusLabel(tone)}: </span>
        {title && <p className="font-semibold text-fg">{title}</p>}
        {children && <div className="text-fg-secondary">{children}</div>}
      </div>
      {onDismiss && <IconButton label="Dismiss" icon={X} size="sm" onClick={onDismiss} className="-my-1 -mr-1.5" />}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------ Modal, confirm */
/** A modal dialog. It renders into its nearest themed ancestor inside <body> (or <body> itself, which follows the
 *  theme on <html>), so a transformed or filtered parent cannot trap it, and it keeps that ancestor's theme. */
export function Modal({ open, onClose, title, description, size = "md", children, footer }:
  { open: boolean; onClose: () => void; title: string; description?: ReactNode; size?: "sm" | "md" | "lg"; children: ReactNode; footer?: ReactNode }) {
  const anchor = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [host, setHost] = useState<Element | null>(null);
  const titleId = useId();
  const descId = useId();
  useLayoutEffect(() => { setHost(anchor.current?.closest("body [data-theme]") ?? document.body); }, []);
  useDialog(open && !!host, onClose, panel);  // waits for the host, so a dialog mounted open still gets initial focus
  const w = size === "sm" ? "max-w-md" : size === "lg" ? "max-w-3xl" : "max-w-xl";
  return <>
    <span ref={anchor} hidden />
    {open && host && createPortal(
    <div className="fixed inset-0 z-60 flex items-start justify-center overflow-y-auto bg-scrim p-4 pt-[10vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descId : undefined} tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()} className={`w-full ${w} rounded-modal border border-line bg-surface-modal text-fg shadow-modal`}>
        <header className="flex items-start justify-between gap-4 border-b border-line px-card py-4">
          <div className="min-w-0">
            <h2 id={titleId} className="type-card text-fg">{title}</h2>
            {description && <p id={descId} className="mt-0.5 type-caption text-fg-muted">{description}</p>}
          </div>
          <IconButton label="Close" icon={X} size="sm" onClick={onClose} className="-mr-1.5" />
        </header>
        {/* Initial focus goes to the first control here (the footer's Cancel when the body has none), not to Close. */}
        <div data-dialog-body>
          <div className="px-card py-5 type-secondary text-fg-secondary">{children}</div>
          {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-card py-4">{footer}</footer>}
        </div>
      </div>
    </div>, host)}
  </>;
}

/** Confirmation for a consequential action. Cancel, Escape, Close and the backdrop send nothing. While the action runs
 *  both buttons are inert and the dialog stays open; an error is shown in place for a retry. */
export function ConfirmDialog({ title, children, confirmLabel, cancelLabel = "Cancel", busyLabel = "Working…", tone = "default", onConfirm, onClose }: {
  title: string; children: ReactNode; confirmLabel: string; cancelLabel?: string; busyLabel?: string; tone?: "default" | "danger";
  onConfirm: () => Promise<unknown>; onClose: () => void;
}) {
  const a = useAction();
  const busy = useRef(false);  // blocks a second confirm in the same tick, before `a.busy` re-renders
  const close = () => { if (!busy.current) onClose(); };
  const submit = () => {
    if (busy.current) return;
    busy.current = true;
    a.run(async () => {
      try { await onConfirm(); } finally { busy.current = false; }
      onClose();
    });
  };
  return (
    <Modal open onClose={close} title={title} size="sm" footer={<>
      <Button variant="secondary" onClick={close} disabled={a.busy}>{cancelLabel}</Button>
      <Button variant={tone === "danger" ? "danger" : "primary"} loading={a.busy} onClick={submit}>{a.busy ? busyLabel : confirmLabel}</Button>
    </>}>
      <div className="space-y-2">{children}</div>
      {!!a.error && <Alert tone="critical" live="assertive" className="mt-4">{errText(a.error)}</Alert>}
    </Modal>
  );
}

/* --------------------------------------------------------------------------------------------- Tooltip */
/** A short supplementary hint on hover or focus, linked to its single child with aria-describedby. The child's own
 *  `title` is dropped so the browser's native tooltip does not appear on top of this one. */
export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" }) {
  return (
    <BaseTooltip content={content} side={side} describe
      bubbleClassName="w-max max-w-xs rounded-chip bg-fg px-2.5 py-1.5 type-caption font-medium text-fg-inverse shadow-modal">
      {isValidElement<{ title?: string }>(children) ? cloneElement(children, { title: undefined }) : children}
    </BaseTooltip>
  );
}

/* ---------------------------------------------------------------------------------------------- Toasts */
const TOAST_STATUS: Record<ToastTone, StatusKey> = { good: "verified", warn: "warning", bad: "critical", info: "info" };

/** Themed toast viewport. Under it, useToast() (from here or components/ui) shows messages in the active theme. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const { items, push, dismiss } = useToastQueue();
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-70 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2">
        {items.map((t) => {
          const s = TOAST_STATUS[t.tone];
          return (
            <div key={t.id} className="pointer-events-auto flex items-start gap-3 rounded-card border border-line bg-surface-modal px-4 py-3 shadow-modal">
              <StatusMark status={s} size="sm" />
              <p className="min-w-0 flex-1 pt-0.5 type-secondary text-fg"><span className="sr-only">{statusLabel(s)}: </span>{t.text}</p>
              <IconButton label="Dismiss notification" icon={X} size="sm" onClick={() => dismiss(t.id)} className="-my-1 -mr-2" />
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/* ------------------------------------------------------------------------------- loading, empty, error */
export function Spinner({ size = "md", label = "Loading", showLabel = false }: { size?: "sm" | "md" | "lg"; label?: string; showLabel?: boolean }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-fg-secondary">
      <Loader2 size={size === "sm" ? 14 : size === "lg" ? 28 : 18} aria-hidden className="shrink-0 text-accent motion-safe:animate-spin" />
      <span className={showLabel ? "type-secondary" : "sr-only"}>{label}</span>
    </span>
  );
}

/** A placeholder block. Decorative; wrap groups in SkeletonText (or set aria-busy on the region) for assistive tech. */
export function Skeleton({ className = "h-4 w-full", style }: { className?: string; style?: CSSProperties }) {
  return <span aria-hidden style={style} className={`block rounded-chip bg-line-strong motion-safe:animate-pulse ${className}`} />;
}

const BAR: Record<"accent" | "verified" | "critical", string> = { accent: "bg-linear-to-r from-accent-strong to-accent", verified: "bg-status-verified", critical: "bg-status-critical" };

/** Determinate progress (role="progressbar" with its value). `tone` switches to verified or critical when it finishes. */
export function ProgressBar({ value, label, tone = "accent", showValue = true, detail }:
  { value: number; label: string; tone?: "accent" | "verified" | "critical"; showValue?: boolean; detail?: ReactNode }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className="grid gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="type-secondary font-medium text-fg">{label}</span>
        {showValue && <span className="type-caption tabular-nums text-fg-muted">{v}%</span>}
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} className="h-2 overflow-hidden rounded-full bg-line">
        <div className={`h-full rounded-full transition-[width] duration-300 ${BAR[tone]}`} style={{ width: `${v}%` }} />
      </div>
      {detail && <p className="type-caption text-fg-muted">{detail}</p>}
    </div>
  );
}

export function SkeletonText({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2.5">
      {Array.from({ length: lines }, (_, i) => <Skeleton key={i} className="h-3.5" style={{ width: `${92 - (i % 3) * 16}%` }} />)}
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, description, action, compact = false }:
  { icon?: LucideIcon; title: string; description?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "px-4 py-8" : "px-6 py-12"}`}>
      <span className="flex h-11 w-11 items-center justify-center rounded-full border border-line bg-surface-raised text-fg-muted"><Icon size={20} aria-hidden /></span>
      <p className="mt-3 type-secondary font-semibold text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm type-caption text-fg-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** A failed load: the server's own explanation (errText), any troubleshooting reference it sent, and an optional retry. */
export function ErrorState({ error, title = "Something went wrong", onRetry, compact = false }:
  { error: unknown; title?: string; onRetry?: () => void; compact?: boolean }) {
  if (!error) return null;
  const reference = error instanceof ApiError ? error.reference : undefined;
  return (
    <div role="alert" className={`flex flex-col items-center justify-center text-center ${compact ? "px-4 py-8" : "px-6 py-12"}`}>
      <StatusMark status="critical" size="lg" />
      <p className="mt-3 type-secondary font-semibold text-fg">{title}</p>
      <p className="mt-1 max-w-md type-caption text-fg-secondary">{errText(error)}</p>
      {reference && <p className="mt-1 type-caption text-fg-muted">{reference.label}: <span className="select-all type-code">{reference.value}</span></p>}
      {onRetry && <Button variant="secondary" size="sm" icon={RotateCw} onClick={onRetry} className="mt-4">Try again</Button>}
    </div>
  );
}
