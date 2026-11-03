"use client";

import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertCircle, Check, ChevronDown, Minus, Search, Upload, X } from "lucide-react";

/* Form controls on the foundation tokens. A control inside <Field> takes its id, description (helper text and error)
   and invalid state from the field, so the label, help and validation message are always programmatically linked.
   Native elements throughout, so keyboard behaviour, autofill and form submission work as the browser provides. */

type FieldState = { id: string; describedBy?: string; invalid: boolean; required?: boolean; disabled?: boolean };
const FieldContext = createContext<FieldState | null>(null);

const CONTROL = "w-full rounded-control border border-line-strong bg-surface-control px-3 type-secondary text-fg placeholder:text-fg-muted "
  + "transition-colors enabled:hover:border-fg-muted focus:border-focus disabled:cursor-not-allowed disabled:border-line disabled:text-fg-disabled "
  + "aria-invalid:border-status-critical";
const TEXT_ONLY = "read-only:bg-surface-raised";  // :read-only also matches <select>, so text controls only
const MARK = "cursor-pointer appearance-none border border-line-strong bg-surface-control transition-colors enabled:not-checked:hover:border-fg-muted "
  + "disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-status-critical";

type Described = { id?: string; required?: boolean; disabled?: boolean; "aria-describedby"?: string; "aria-invalid"?: ComponentProps<"input">["aria-invalid"] };
/** Merges a control's own props with the surrounding Field's id, description, invalid, required and disabled state. */
function useFieldProps(p: Described) {
  const f = useContext(FieldContext);
  return {
    id: p.id ?? f?.id,
    required: p.required ?? f?.required,
    disabled: p.disabled ?? f?.disabled,
    "aria-describedby": [p["aria-describedby"], f?.describedBy].filter(Boolean).join(" ") || undefined,
    "aria-invalid": p["aria-invalid"] ?? (f?.invalid ? true : undefined),
  };
}

/* ------------------------------------------------------------------------------------- labels and messages */
export function Label({ htmlFor, required, optional, children, className = "" }:
  { htmlFor?: string; required?: boolean; optional?: boolean; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={`type-secondary font-medium text-fg ${className}`}>
      {children}
      {required && <span aria-hidden className="ml-0.5 text-status-critical">*</span>}
      {optional && <span className="ml-1.5 type-caption text-fg-muted">(optional)</span>}
    </label>
  );
}

export function HelperText({ id, children }: { id?: string; children: ReactNode }) {
  return <p id={id} className="type-caption text-fg-muted">{children}</p>;
}

export function ErrorText({ id, children }: { id?: string; children: ReactNode }) {
  return <p id={id} className="flex items-start gap-1.5 type-caption font-medium text-status-critical"><AlertCircle size={13} aria-hidden className="mt-px shrink-0" />{children}</p>;
}

/** Label, control, helper text and validation message as one unit. A required field also marks its control required. */
export function Field({ label, hint, error, required, optional, disabled, id, className = "", children }: {
  label: ReactNode; hint?: ReactNode; error?: ReactNode; required?: boolean; optional?: boolean; disabled?: boolean; id?: string; className?: string; children: ReactNode;
}) {
  const auto = useId();
  const fid = id ?? auto;
  const hintId = hint ? `${fid}-hint` : undefined;
  const errorId = error ? `${fid}-error` : undefined;
  return (
    <div className={`grid content-start gap-1.5 ${className}`}>
      <Label htmlFor={fid} required={required} optional={optional}>{label}</Label>
      <FieldContext.Provider value={{ id: fid, describedBy: [errorId, hintId].filter(Boolean).join(" ") || undefined, invalid: !!error, required, disabled }}>
        {children}
      </FieldContext.Provider>
      {hint && <HelperText id={hintId}>{hint}</HelperText>}
      {error && <ErrorText id={errorId}>{error}</ErrorText>}
    </div>
  );
}

/** A group of checkboxes or radios under one legend, with optional help and error text. */
export function Fieldset({ legend, hint, error, required, children, className = "" }:
  { legend: ReactNode; hint?: ReactNode; error?: ReactNode; required?: boolean; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <fieldset aria-describedby={[error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(" ") || undefined} className={`grid gap-2.5 ${className}`}>
      <legend className="mb-1 type-secondary font-medium text-fg">{legend}{required && <span aria-hidden className="ml-0.5 text-status-critical">*</span>}</legend>
      {hint && <HelperText id={`${id}-hint`}>{hint}</HelperText>}
      {children}
      {error && <ErrorText id={`${id}-error`}>{error}</ErrorText>}
    </fieldset>
  );
}

/* --------------------------------------------------------------------------------------------- text entry */
export type TextInputProps = ComponentProps<"input"> & { leadingIcon?: LucideIcon; trailing?: ReactNode };

export function TextInput({ leadingIcon: Icon, trailing, className = "", ...rest }: TextInputProps) {
  const field = useFieldProps(rest);
  const input = <input {...rest} {...field} className={`${CONTROL} ${TEXT_ONLY} h-10 ${Icon ? "pl-9" : ""} ${trailing ? "pr-10" : ""} ${className}`} />;
  if (!Icon && !trailing) return input;
  return (
    <div className="relative">
      {Icon && <Icon size={15} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />}
      {input}
      {trailing && <div className="absolute inset-y-0 right-1 flex items-center">{trailing}</div>}
    </div>
  );
}

/** A search box. With `onClear`, a clear button appears once there is text, and Escape clears too. */
export function SearchInput({ onClear, className = "", onKeyDown, ...rest }: Omit<TextInputProps, "leadingIcon" | "trailing" | "type"> & { onClear?: () => void }) {
  const showClear = !!onClear && rest.value !== undefined && rest.value !== "";
  return (
    <TextInput type="search" {...rest} leadingIcon={Search} className={`[&::-webkit-search-cancel-button]:appearance-none ${className}`}
      onKeyDown={(e) => { if (e.key === "Escape" && onClear) onClear(); onKeyDown?.(e); }}
      trailing={showClear ? (
        <button type="button" onClick={onClear} aria-label="Clear search"
          className="flex h-8 w-8 items-center justify-center rounded-control text-fg-muted hover:bg-fg/5 hover:text-fg"><X size={14} aria-hidden /></button>
      ) : undefined} />
  );
}

export function Textarea({ className = "", rows = 4, ...rest }: ComponentProps<"textarea">) {
  const field = useFieldProps(rest);
  return <textarea rows={rows} {...rest} {...field} className={`${CONTROL} ${TEXT_ONLY} min-h-20 py-2 ${className}`} />;
}

/** A file picker that also accepts drag and drop. The control is a native file input, so keyboard, screen reader and
 *  the browser's file dialog work as usual; the dashed area is its label. Inside <Field> it takes the field's error. */
export function FileDropzone({ onFiles, accept, multiple = false, disabled, formats, id }:
  { onFiles: (files: File[]) => void; accept?: string; multiple?: boolean; disabled?: boolean; formats?: ReactNode; id?: string }) {
  const field = useFieldProps({ id, disabled });
  const auto = useId();
  const fid = field.id ?? auto;
  const [over, setOver] = useState(false);
  return (
    <div onDragOver={(e) => { e.preventDefault(); if (!field.disabled) setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (!field.disabled) onFiles(Array.from(e.dataTransfer.files).slice(0, multiple ? undefined : 1)); }}>
      <input type="file" {...field} id={fid} accept={accept} multiple={multiple} className="peer sr-only"
        onChange={(e) => { onFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      <label htmlFor={fid} className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed px-6 py-8 text-center transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus peer-disabled:cursor-not-allowed peer-disabled:opacity-50 peer-aria-invalid:border-status-critical ${over
        ? "border-accent bg-accent/10" : "border-line-strong bg-surface-control/50 hover:border-fg-muted"}`}>
        <Upload size={22} aria-hidden className="text-accent" />
        <span className="type-secondary font-semibold text-fg">Drop a file here or <span className="text-accent-fg underline underline-offset-2">choose a file</span></span>
        {formats && <span className="type-caption text-fg-muted">{formats}</span>}
      </label>
    </div>
  );
}

/* ----------------------------------------------------------------------------------------------- select */
export type SelectOption = { value: string; label: string; disabled?: boolean };

/** A native select (the most robust choice for keyboard, screen reader and touch), styled on the tokens. */
export function Select({ options, placeholder, leadingIcon: Icon, className = "", ...rest }:
  ComponentProps<"select"> & { options: SelectOption[]; placeholder?: string; leadingIcon?: LucideIcon }) {
  const field = useFieldProps(rest);
  return (
    <div className="relative">
      {Icon && <Icon size={15} aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" />}
      <select {...rest} {...field} className={`${CONTROL} h-10 cursor-pointer appearance-none pr-9 disabled:cursor-not-allowed ${Icon ? "pl-9" : ""} ${className}`}>
        {placeholder && <option value="" disabled>{placeholder}</option>}
        {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
      </select>
      <ChevronDown size={16} aria-hidden className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-fg-muted" />
    </div>
  );
}

/* --------------------------------------------------------------------------------- checkbox, radio, switch */
type Choice = { label: ReactNode; description?: ReactNode; hideLabel?: boolean };

function ChoiceText({ id, label, description, disabled, hideLabel }: Choice & { id: string; disabled?: boolean }) {
  return (
    <span className={`min-w-0 ${hideLabel ? "sr-only" : ""}`}>
      <label htmlFor={id} className={`type-secondary font-medium ${disabled ? "cursor-not-allowed text-fg-disabled" : "cursor-pointer text-fg"}`}>{label}</label>
      {description && <span id={`${id}-desc`} className="block type-caption text-fg-muted">{description}</span>}
    </span>
  );
}

/** A native checkbox. `indeterminate` shows the mixed state (for example a "select all" with some selected). */
export function Checkbox({ label, description, hideLabel, indeterminate = false, id, className = "", ...rest }:
  Omit<ComponentProps<"input">, "type"> & Choice & { indeterminate?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const auto = useId();
  const cid = id ?? auto;
  useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate; }, [indeterminate]);
  return (
    <div className={`flex items-start gap-2.5 ${className}`}>
      <span className="relative mt-px flex h-4.5 w-4.5 shrink-0">
        <input type="checkbox" id={cid} aria-describedby={description ? `${cid}-desc` : undefined} {...rest} ref={ref}
          className={`peer h-4.5 w-4.5 rounded-chip ${MARK} checked:border-accent checked:bg-accent indeterminate:border-accent indeterminate:bg-accent`} />
        <Check size={13} strokeWidth={3} aria-hidden className="pointer-events-none absolute inset-0 m-auto hidden text-on-accent peer-checked:block peer-indeterminate:hidden" />
        <Minus size={13} strokeWidth={3} aria-hidden className="pointer-events-none absolute inset-0 m-auto hidden text-on-accent peer-indeterminate:block" />
      </span>
      <ChoiceText id={cid} label={label} description={description} disabled={rest.disabled} hideLabel={hideLabel} />
    </div>
  );
}

export type RadioOption = { value: string; label: ReactNode; description?: ReactNode; disabled?: boolean };

/** Native radios under a fieldset: arrow keys move the selection, as browsers provide. */
export function RadioGroup({ legend, name, value, onChange, options, hint, error, required, orientation = "vertical" }: {
  legend: ReactNode; name: string; value: string; onChange: (v: string) => void; options: RadioOption[];
  hint?: ReactNode; error?: ReactNode; required?: boolean; orientation?: "vertical" | "horizontal";
}) {
  const base = useId();
  return (
    <Fieldset legend={legend} hint={hint} error={error} required={required}>
      <div className={orientation === "horizontal" ? "flex flex-wrap gap-x-6 gap-y-2.5" : "grid gap-2.5"}>
        {options.map((o) => {
          const id = `${base}-${o.value}`;
          return (
            <div key={o.value} className="flex items-start gap-2.5">
              <input type="radio" id={id} name={name} value={o.value} checked={value === o.value} disabled={o.disabled} required={required}
                aria-describedby={o.description ? `${id}-desc` : undefined} aria-invalid={error ? true : undefined} onChange={() => onChange(o.value)}
                className={`mt-px h-4.5 w-4.5 shrink-0 rounded-full ${MARK} checked:border-[5px] checked:border-accent`} />
              <ChoiceText id={id} label={o.label} description={o.description} disabled={o.disabled} />
            </div>
          );
        })}
      </div>
    </Fieldset>
  );
}

/** An on/off setting that takes effect immediately (role="switch"). Use a checkbox for choices submitted with a form. */
export function Switch({ checked, onChange, label, description, disabled, id }:
  { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean; id?: string }) {
  const auto = useId();
  const sid = id ?? auto;
  return (
    <div className="flex items-start justify-between gap-4">
      <ChoiceText id={sid} label={label} description={description} disabled={disabled} />
      <button type="button" role="switch" id={sid} aria-checked={checked} aria-describedby={description ? `${sid}-desc` : undefined} disabled={disabled}
        onClick={() => onChange(!checked)}
        className="group inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border border-line-strong bg-surface-control transition-colors aria-checked:border-accent aria-checked:bg-accent disabled:cursor-not-allowed disabled:opacity-50">
        <span aria-hidden className="ml-0.5 h-4.5 w-4.5 rounded-full bg-fg-muted shadow-card transition-transform group-aria-checked:translate-x-5 group-aria-checked:bg-on-accent" />
      </button>
    </div>
  );
}
