"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertCircle, Bot, Check, ChevronRight, Eye, EyeOff, FileCheck2, Loader2, ScrollText, ShieldCheck } from "lucide-react";
import { API, ApiError } from "@/lib/api";
import { personaOf, useAuth, type Me } from "@/lib/actor";
import { safeNextPath } from "@/lib/redirect";
import { canOpen } from "@/components/nav";
import { Logo } from "@/components/Shell";

// Demo/dev only: the account list is requested only when this frontend build opts in, so a normal sign-in page never
// calls /auth/demo-accounts (the backend answers 404 there unless ZA_DEMO_MODE=1).
const DEMO_LIST = process.env.NEXT_PUBLIC_ZA_DEMO_MODE === "1";

type Demo = { accounts: { email: string; display_name: string; roles: string[]; actor_type: string; tenant_name: string }[] };

const EMAIL_FORMAT = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const HIGHLIGHTS: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: ScrollText, title: "Regulatory Intelligence", text: "Requirements encoded from authoritative sources and traced to their provisions." },
  { icon: FileCheck2, title: "Evidence & Assurance", text: "Controls proven by tamper-evident evidence and assessed for named periods." },
  { icon: Bot, title: "AI Governance", text: "AI assets and agents act only under explicit, auditable authority." },
];

const CHAIN = ["Source", "Applicability", "Obligation", "Control", "Evidence", "Assurance"];

/** Where to go once signed in: the requested `?next=` page when this account can use it, otherwise "/", which shows
 *  the account's own dashboard (Organisation Dashboard, Super Admin Dashboard or the content workspace). The role comes
 *  from the server's sign-in response; this only routes, and the backend authorises every request. */
function destination(me: Me): string {
  const next = safeNextPath(new URLSearchParams(window.location.search).get("next"), window.location.origin);
  return canOpen(personaOf(me), new URL(next, window.location.origin).pathname) ? next : "/";
}

/** The backend's own wording for refusals it explains (401 wrong credentials, 423 temporarily locked, 403 organisation
 *  suspended); fixed wording for everything else, so no server internals reach the page. */
function signInError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Couldn't reach the Zoiko Assure server. Check your connection and try again.";
    if (e.status >= 500) return "Sign-in is unavailable right now because of a server problem. Please try again shortly.";
    if (e.status === 422) return "Enter a valid work email address and your password.";
    if (typeof e.detail === "string" && e.detail) return e.detail;
  }
  return "Sign-in didn't complete. Please try again.";
}

function validate(email: string, password: string) {
  const errors: { email?: string; password?: string } = {};
  if (!email.trim()) errors.email = "Enter your work email.";
  else if (!EMAIL_FORMAT.test(email.trim())) errors.email = "Enter a valid email address, such as name@company.com.";
  if (!password) errors.password = "Enter your password.";
  return errors;
}

export default function Login() {
  const { login, me, ready } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);
  const [demo, setDemo] = useState<Demo | null>(null);
  // The page is prerendered, so the form is on screen before React attaches its submit handler. Until then "Sign in"
  // stays disabled (a disabled default button also blocks Enter), so the browser can never submit the form natively.
  const [interactive, setInteractive] = useState(false);
  const submitting = useRef(false);  // blocks a second submit in the same tick, before `busy` disables the button
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  // Signed in (including just now): leave for the account's destination. A temporary password is handled by the app
  // shell, which shows the forced password change before any page.
  useEffect(() => { if (ready && me) router.replace(destination(me)); }, [ready, me, router]);
  useEffect(() => setInteractive(true), []);
  useEffect(() => {
    if (!DEMO_LIST) return;
    fetch(`${API}/auth/demo-accounts`).then((r) => (r.ok ? r.json() : null)).then(setDemo).catch(() => {});
  }, []);

  const fieldErrors = attempted ? validate(email, password) : {};

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current) return;
    setAttempted(true);
    const errors = validate(email, password);
    if (errors.email || errors.password) {
      (errors.email ? emailRef : passwordRef).current?.focus();
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);  // on success the effect above navigates; stay busy until it does
    } catch (err) {
      setError(signInError(err));
      submitting.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#f4f7fa] text-[#172b3a] md:grid md:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      {/* Brand and assurance story */}
      <section aria-labelledby="brand-headline"
        className="border-b border-[#dfe6ed] bg-white px-5 pb-6 pt-6 sm:px-8 md:flex md:flex-col md:border-b-0 md:border-r md:px-10 md:py-10 lg:px-14 lg:py-12 xl:px-20">
        <Logo width={152} />
        <div className="mt-6 md:my-auto md:max-w-xl md:py-10">
          <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-700">
            <span aria-hidden className="h-px w-6 bg-brand-500" />Global regulatory assurance infrastructure
          </p>
          <h2 id="brand-headline" className="mt-3 text-[26px] font-semibold leading-tight tracking-tight text-navy-900 md:mt-4 md:text-[34px] lg:text-[42px]">
            Make every obligation provable.
          </h2>
          <p className="mt-3 max-w-lg text-[14px] leading-6 text-slate-600 md:mt-4 md:text-[15px] md:leading-7">
            Connect regulatory requirements, controls and verifiable evidence to achieve trusted, audit-ready assurance.
          </p>
          <ul className="mt-8 hidden gap-5 md:grid lg:mt-10">
            {HIGHLIGHTS.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3.5">
                <span className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-navy-50 text-navy-700 ring-1 ring-inset ring-navy-100">
                  <Icon size={19} strokeWidth={1.75} aria-hidden />
                  <span aria-hidden className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-500 ring-2 ring-white" />
                </span>
                <span className="min-w-0">
                  <span className="block text-[14px] font-semibold text-navy-900">{title}</span>
                  <span className="mt-0.5 block text-[13px] leading-5 text-slate-600">{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <AssuranceChain />
      </section>

      {/* Sign-in */}
      <main className="flex flex-col px-5 pb-6 pt-6 sm:px-8 md:min-h-screen md:px-8 md:py-10 lg:px-12">
        <div className="flex flex-1 items-start justify-center md:items-center">
          <div className="w-full max-w-[400px]">
            <div className="rounded-xl border border-[#dfe6ed] bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_32px_-12px_rgba(11,41,66,0.18)] sm:p-8">
              <h1 className="text-[22px] font-semibold tracking-tight text-navy-900">Welcome back</h1>
              <p className="mt-1 text-[14px] text-slate-600">Sign in to your Zoiko Assure workspace.</p>

              {error && (
                <div id="signin-error" role="alert" className="mt-5 flex gap-2.5 rounded-lg border border-red-200 bg-red-50 px-3.5 py-3 text-[13px] leading-5 text-red-800">
                  <AlertCircle size={16} aria-hidden className="mt-0.5 shrink-0 text-red-600" />
                  <span>{error}</span>
                </div>
              )}

              <form onSubmit={submit} method="post" noValidate aria-describedby={error ? "signin-error" : undefined} className="mt-6 grid gap-4">
                <div>
                  <label htmlFor="email" className="mb-1.5 block text-[13px] font-medium text-[#172b3a]">Work email</label>
                  <input ref={emailRef} id="email" type="email" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false}
                    required aria-required="true" aria-invalid={!!fieldErrors.email} aria-describedby={fieldErrors.email ? "email-error" : undefined}
                    value={email} onChange={(e) => { setEmail(e.target.value); setError(null); }} placeholder="name@company.com" disabled={busy}
                    className={`input h-11 px-3 text-[14px] placeholder:text-slate-500 ${fieldErrors.email ? "border-red-500 focus:border-red-600 focus:ring-red-100" : "border-[#8494a7]"}`} />
                  {fieldErrors.email && <FieldError id="email-error">{fieldErrors.email}</FieldError>}
                </div>

                <div>
                  <label htmlFor="password" className="mb-1.5 block text-[13px] font-medium text-[#172b3a]">Password</label>
                  <div className="relative">
                    <input ref={passwordRef} id="password" type={show ? "text" : "password"} autoComplete="current-password"
                      required aria-required="true" aria-invalid={!!fieldErrors.password}
                      aria-describedby={[fieldErrors.password && "password-error", capsLock && "caps-lock"].filter(Boolean).join(" ") || undefined}
                      value={password} onChange={(e) => { setPassword(e.target.value); setError(null); }} disabled={busy}
                      onKeyDown={(e) => setCapsLock(e.getModifierState("CapsLock"))} onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))}
                      onBlur={() => setCapsLock(false)}
                      className={`input h-11 pl-3 pr-11 text-[14px] ${fieldErrors.password ? "border-red-500 focus:border-red-600 focus:ring-red-100" : "border-[#8494a7]"}`} />
                    <button type="button" onClick={() => setShow(!show)} aria-pressed={show} aria-controls="password" aria-label="Show password" disabled={busy}
                      className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 disabled:opacity-50">
                      {show ? <EyeOff size={17} strokeWidth={1.75} aria-hidden /> : <Eye size={17} strokeWidth={1.75} aria-hidden />}
                    </button>
                  </div>
                  {fieldErrors.password && <FieldError id="password-error">{fieldErrors.password}</FieldError>}
                  {capsLock && <p id="caps-lock" className="mt-1.5 text-[12px] text-amber-800">Caps Lock is on.</p>}
                </div>

                <button type="submit" disabled={busy || !interactive} aria-busy={busy}
                  className="btn-primary mt-1 h-11 w-full justify-center gap-2 rounded-lg text-[14px] font-semibold shadow-sm">
                  {busy && <Loader2 size={16} aria-hidden className="motion-safe:animate-spin" />}
                  {busy ? "Signing in…" : "Sign in"}
                </button>
              </form>

              <p className="mt-5 flex items-start gap-2 border-t border-slate-100 pt-4 text-[12px] leading-5 text-slate-600">
                <ShieldCheck size={15} strokeWidth={1.75} aria-hidden className="mt-0.5 shrink-0 text-navy-600" />
                Sign-in activity is recorded in the Zoiko Assure audit trail.
              </p>
            </div>

            {demo && demo.accounts.length > 0 && (
              <div className="mt-4 rounded-xl border border-[#dfe6ed] bg-white p-3">
                <div className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Demo accounts</div>
                <ul className="scroll-thin grid max-h-52 gap-px overflow-y-auto">
                  {demo.accounts.map((a) => (
                    <li key={a.email}>
                      <button type="button" onClick={() => { setEmail(a.email); setError(null); passwordRef.current?.focus(); }}
                        className={`flex w-full min-w-0 items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-[12px] hover:bg-slate-50 ${email === a.email ? "bg-navy-50" : ""}`}>
                        <span className="truncate text-slate-800">{a.display_name}</span>
                        <span className="mono max-w-[45%] shrink-0 truncate text-[10px] text-slate-500">{a.roles.join(",")}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5 px-1 text-[10px] text-slate-500">Shown only while the API runs with ZA_DEMO_MODE=1. Use the locally configured demo password.</p>
              </div>
            )}
          </div>
        </div>
        <footer className="mt-8 text-center text-[12px] text-slate-600">© {new Date().getFullYear()} Zoiko Tech Inc. All rights reserved.</footer>
      </main>
    </div>
  );
}

function FieldError({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <p id={id} className="mt-1.5 flex items-start gap-1.5 text-[12px] leading-5 text-red-700">
      <AlertCircle size={14} aria-hidden className="mt-0.5 shrink-0" />{children}
    </p>
  );
}

/** The governing chain the platform proves, drawn as a quiet brand visual (hidden on phones). */
function AssuranceChain() {
  return (
    <figure className="relative mt-2 hidden overflow-hidden rounded-xl bg-navy-900 px-5 py-5 md:block lg:px-6 lg:py-6">
      <svg aria-hidden viewBox="0 0 320 120" className="pointer-events-none absolute -right-6 -top-4 h-36 w-auto opacity-[0.16]" fill="none">
        <g stroke="#aecadc" strokeWidth="1">
          <path d="M20 90 L90 40 L170 70 L240 25 L300 60" /><path d="M90 40 L130 105 L170 70" /><path d="M240 25 L260 95 L300 60" />
        </g>
        <g fill="#aecadc">
          <circle cx="20" cy="90" r="3" /><circle cx="90" cy="40" r="3.5" /><circle cx="130" cy="105" r="2.5" />
          <circle cx="170" cy="70" r="3.5" /><circle cx="260" cy="95" r="2.5" /><circle cx="300" cy="60" r="3" />
        </g>
        <circle cx="240" cy="25" r="4.5" fill="#d9822b" />
      </svg>
      <figcaption className="relative text-[11px] font-semibold uppercase tracking-[0.14em] text-navy-200">From source to assurance</figcaption>
      <ol className="relative mt-4 flex flex-wrap items-center gap-x-1.5 gap-y-2" aria-label="How an obligation is proven">
        {CHAIN.map((step, i) => {
          const last = i === CHAIN.length - 1;
          return (
            <li key={step} className="flex items-center gap-1.5">
              <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${last ? "bg-brand-500/15 text-brand-200 ring-brand-400/60" : "bg-white/[0.06] text-white/85 ring-white/15"}`}>
                {last && <Check size={12} strokeWidth={2.25} aria-hidden className="text-brand-300" />}{step}
              </span>
              {!last && <ChevronRight size={13} aria-hidden className="text-white/35" />}
            </li>
          );
        })}
      </ol>
    </figure>
  );
}
