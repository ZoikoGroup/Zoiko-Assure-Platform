"use client";

import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { AlertCircle, ArrowLeft, ArrowRight, Building2, ChevronRight, Eye, EyeOff, Info, Loader2, Lock, Mail, User } from "lucide-react";
import { API, ApiError } from "@/lib/api";
import { personaOf, useAuth, type Me } from "@/lib/actor";
import { safeNextPath } from "@/lib/redirect";
import { canOpen } from "@/components/nav";

// Demo/dev only: the account list is requested only when this frontend build opts in, so a normal sign-in page never
// calls /auth/demo-accounts (the backend answers 404 there unless ZA_DEMO_MODE=1).
const DEMO_LIST = process.env.NEXT_PUBLIC_ZA_DEMO_MODE === "1";

type Demo = { accounts: { email: string; display_name: string; roles: string[]; actor_type: string; tenant_name: string }[] };

const EMAIL_FORMAT = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** The hero background. The repository has no licensed photograph, so this is an original illustrated scene (sunset
 *  skyline through office glass). To use a photograph, add it under public/ (landscape, at least 2400x1500, with the
 *  bright sky on the left and a dark foreground at the bottom, so the overlays keep the text readable) and point here. */
const HERO_IMAGE = "/login-hero.svg";

const PILLARS = ["People", "Data", "Compliance", "Resilience"];

const TABS = [{ id: "login", label: "Login" }, { id: "register", label: "Register" }, { id: "reset", label: "Reset Password" }] as const;
type Tab = (typeof TABS)[number]["id"];

/** The shortest viewport the desktop page fits without scrolling (a 1366x768 laptop's browser window is about 630-660px).
 *  From lg the page is exactly one viewport tall, but never shorter than this: a shorter window scrolls the page normally
 *  instead of clipping it. Keep it equal to the `lg:min-h-155` (620px) on the page root. */
const FIT_MIN = 620;
/** The viewport height at which every size reaches the reference design (1536x1024). */
const FIT_FULL = 1024;

/** A length that grows linearly with the viewport height, from `min` at FIT_MIN to `max` at FIT_FULL. Every vertical
 *  size of the card follows it, so the card's height is linear in the viewport height too: it fits at both ends (about
 *  597px of 620 and 1004px of 1024, with a two-line footer), so it fits at every height in between. */
const fluid = (min: number, max: number) =>
  `clamp(${min}px, calc(${min}px + (100svh - ${FIT_MIN}px) * ${+((max - min) / (FIT_FULL - FIT_MIN)).toFixed(5)}), ${max}px)`;

const LAYOUT = {
  // Card rhythm (every width)
  "--lg-tab-top": fluid(4, 8),
  "--lg-tab-h": fluid(34, 54),
  "--lg-pad-top": fluid(4, 24),
  "--lg-logo-w": fluid(80, 270),
  "--lg-gap-xs": fluid(4, 8),
  "--lg-gap-sm": fluid(5, 16),
  "--lg-gap-md": fluid(6, 22),
  "--lg-foot-y": fluid(5, 14),
  "--lg-h1": fluid(20, 34),
  "--lg-sub": fluid(13, 17),
  "--lg-text": fluid(13, 15.5),
  "--lg-sso-h": fluid(30, 46),
  "--lg-field-h": fluid(38, 48),
  "--lg-btn-h": fluid(42, 54),
  // Two-column composition (from md): sizes follow the width and are capped by the height, so the hero fits beside the card.
  // The card is 37% of the width (--card-w, set on the page root: 380px minimum on tablets, 410px from lg so a sign-in
  // error stays on two lines).
  // 35px at 1536 as in the reference, tighter on narrower cards so the footer stays on one line from 1280px
  "--card-px": "clamp(20px, calc(4.5vw - 34px), 36px)",
  "--card-right": "clamp(16px, 2.1vw, 40px)",
  "--hero-x": "clamp(24px, 4.9vw, 96px)",
  "--hero-gap": "clamp(24px, 4vw, 80px)",
  "--hero-top": "clamp(24px, 8.6svh, 96px)",
  "--hero-bottom": "clamp(20px, 4.8svh, 56px)",
  "--hero-logo-w": "clamp(170px, min(25vw, 38svh), 430px)",
  "--hero-pillar": "clamp(9px, min(0.92vw, 1.4svh), 15px)",
  "--hero-h": "clamp(30px, min(4.1vw, 6.3svh), 76px)",
  "--hero-sub": "clamp(15px, min(1.45vw, 2.2svh), 24px)",
  "--hero-tile": "clamp(52px, min(5.8vw, 8.6svh), 96px)",
  "--hero-label": "clamp(12px, min(1.05vw, 1.6svh), 17px)",
  "--hero-tag": "clamp(10.5px, min(0.85vw, 1.3svh), 14px)",
} as React.CSSProperties;

/** The backend's password policy (`password_policy_ok` in backend/app/auth.py), checked here because there is no
 *  registration endpoint yet to check it. */
const PASSWORD_POLICY = "At least 10 characters, with upper- and lower-case letters and a digit.";
const meetsPasswordPolicy = (pw: string) => pw.length >= 10 && pw.toLowerCase() !== pw && pw.toUpperCase() !== pw && /\d/.test(pw);

/** Where to go once signed in: the requested `?next=` page when this account can use it, otherwise "/", which shows
 *  the account's own dashboard (Organisation Dashboard, Super Admin Dashboard or the content workspace). The role comes
 *  from the server's sign-in response; this only routes, and the backend authorises every request. */
function destination(me: Me): string {
  const next = safeNextPath(new URLSearchParams(window.location.search).get("next"), window.location.origin);
  return canOpen(personaOf(me), new URL(next, window.location.origin).pathname, me.permissions) ? next : "/";
}

/** Fixed wording for every outcome; the server's text is never shown. A wrong password or unknown account (401) and a
 *  locked account (423) read the same: only an existing account can be locked, so naming the lockout would confirm the
 *  account exists (audit FA-31). The 403 comes only after the password is verified, so naming the suspension tells
 *  nothing to someone without valid credentials. The status code itself still differs until the backend changes. */
const CREDENTIALS_REFUSED = "Unable to sign in with the provided credentials. Please check your details or contact your administrator.";

function signInError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Couldn't reach the Zoiko Assure server. Check your connection and try again.";
    if (e.status >= 500) return "Sign-in is unavailable right now because of a server problem. Please try again shortly.";
    if (e.status === 422) return "Enter a valid work email address and your password.";
    if (e.status === 401 || e.status === 423) return CREDENTIALS_REFUSED;
    if (e.status === 403) return "Sign-in is disabled because your organisation's access is suspended. Contact your administrator.";
  }
  return "Sign-in didn't complete. Please try again.";
}

const emailError = (email: string) =>
  !email.trim() ? "Enter your work email." : !EMAIL_FORMAT.test(email.trim()) ? "Enter a valid email address, such as name@company.com." : undefined;

function validate(email: string, password: string) {
  const errors: { email?: string; password?: string } = { email: emailError(email) };
  if (!password) errors.password = "Enter your password.";
  return errors;
}

type Registration = { name: string; email: string; password: string; confirm: string };

function validateRegistration(f: Registration) {
  const errors: Partial<Record<keyof Registration, string>> = { email: emailError(f.email) };
  if (!f.name.trim()) errors.name = "Enter your full name.";
  if (!f.password) errors.password = "Create a password.";
  else if (!meetsPasswordPolicy(f.password)) errors.password = "Use at least 10 characters, with upper- and lower-case letters and a digit.";
  if (!f.confirm) errors.confirm = "Confirm your password.";
  else if (f.confirm !== f.password) errors.confirm = "Passwords don't match.";
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
  // The page is prerendered, so the form is on screen before React attaches its submit handler. Until then "Log in"
  // stays disabled (a disabled default button also blocks Enter), so the browser can never submit the form natively.
  const [interactive, setInteractive] = useState(false);
  const submitting = useRef(false);  // blocks a second submit in the same tick, before `busy` disables the button
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Tab>("login");
  const [focusReset, setFocusReset] = useState(false);  // "Forgot password?" opened the reset view: focus its field
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const forgotRef = useRef<HTMLButtonElement>(null);

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

  // WAI-ARIA tabs: arrow keys, Home and End move between the tabs and select. While a sign-in is in flight the other
  // tabs are disabled, so the request can't complete behind a different panel.
  const selectTab = (next: Tab) => {
    if (busy) return;
    setTab(next);
    setFocusReset(false);
  };
  const onTabKey = (e: React.KeyboardEvent) => {
    const at = TABS.findIndex((t) => t.id === tab);
    const to = ({ ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: TABS.length - 1 } as Record<string, number>)[e.key];
    if (to === undefined || busy) return;
    e.preventDefault();
    const next = TABS[(to + TABS.length) % TABS.length].id;
    selectTab(next);
    tabRefs.current[next]?.focus();
  };
  // Links inside a panel hide that panel, so focus moves to what replaces them.
  const openReset = () => {
    if (busy) return;
    setTab("reset");
    setFocusReset(true);
  };
  const openRegister = () => {
    selectTab("register");
    requestAnimationFrame(() => tabRefs.current.register?.focus());
  };
  const backToLogin = () => {
    selectTab("login");
    requestAnimationFrame(() => forgotRef.current?.focus());
  };

  return (
    // Below lg the page grows with its content and scrolls. From lg it is exactly one viewport tall (100dvh, 100vh where
    // dvh is unsupported) and every size scales with the viewport height so the content fits (see FIT_MIN). Vertical
    // overflow is `auto`, not `hidden`: nothing overflows, so no scrollbar shows, but if a message ever needs more room on
    // a very short window the page scrolls instead of clipping it.
    <div style={LAYOUT} className="relative isolate min-h-dvh overflow-x-clip bg-[linear-gradient(180deg,#e9eff5_0%,#f3f6f9_100%)] text-[#172b3a] [--card-w:clamp(380px,37vw,620px)] lg:[--card-w:clamp(410px,37vw,620px)] lg:h-screen lg:min-h-155 lg:overflow-x-hidden lg:overflow-y-auto lg:supports-[height:100dvh]:h-dvh">
      <HeroBackdrop />

      {/* Phones stack the brand, the card, then the assurance story. From md the story fills the left column over the
          photograph, with the tagline at the bottom, and the card floats on the right, vertically centred. From lg the
          grid is the viewport's height and its rows may shrink (min-h-0), so no child can push the page taller. */}
      <div className="grid min-h-dvh grid-cols-1 content-start gap-y-5 px-4 pb-10 pt-5 sm:px-6 md:grid-cols-[minmax(0,1fr)_var(--card-w)] md:grid-rows-[auto_auto_minmax(0,1fr)_auto] md:content-stretch md:gap-x-(--hero-gap) md:gap-y-0 md:py-0 md:pl-(--hero-x) md:pr-(--card-right) lg:h-full lg:min-h-0">
        <header className="min-h-0 md:col-start-1 md:row-start-1 md:pt-(--hero-top)">
          <Wordmark label="Zoiko Assure" className="w-37.5 md:w-(--hero-logo-w)" />
          <p className="mt-3 flex flex-wrap items-center gap-x-[0.7em] gap-y-1 text-[9px] font-medium uppercase tracking-[0.16em] text-navy-900 md:mt-[calc(var(--hero-logo-w)*0.075)] md:text-(length:--hero-pillar) lg:gap-x-[0.85em] lg:tracking-[0.34em]">
            {PILLARS.map((p, i) => (
              <Fragment key={p}>
                {i > 0 && <span aria-hidden className="font-normal text-brand-500">|</span>}
                <span>{p}</span>
              </Fragment>
            ))}
          </p>
        </header>

        {/* Centring sets the card's visible margin; the 8px padding only keeps it off the edges when it nearly fills the
            viewport, so the card can use the whole height when a message needs room. */}
        <main className="min-h-0 md:col-start-2 md:row-span-4 md:row-start-1 md:self-center md:py-2">
          <div className="mx-auto w-full max-w-120 md:max-w-none">
            <div className="overflow-hidden rounded-[18px] bg-white shadow-[0_32px_80px_-28px_rgba(4,16,28,0.6),0_4px_14px_-4px_rgba(4,16,28,0.18)] ring-1 ring-navy-950/6">
              <div role="tablist" aria-label="Account access" onKeyDown={onTabKey}
                className="grid grid-cols-3 gap-0.5 border-b border-[#e3e8ee] bg-[linear-gradient(180deg,#f4f7fa,#edf1f5)] px-2 pt-(--lg-tab-top) sm:px-3 md:px-(--card-px)">
                {TABS.map(({ id, label }) => {
                  const selected = id === tab;
                  return (
                    <button key={id} ref={(el) => { tabRefs.current[id] = el; }} type="button" role="tab" id={`tab-${id}`}
                      aria-selected={selected} aria-controls={`panel-${id}`} tabIndex={selected ? 0 : -1} disabled={busy && !selected} onClick={() => selectTab(id)}
                      className={`relative -mb-px h-(--lg-tab-h) whitespace-nowrap rounded-t-xl px-0 text-[12.5px] leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy-400 disabled:cursor-not-allowed disabled:opacity-50 min-[400px]:text-[13px] sm:text-[14px] md:text-[13px] xl:text-[15px] ${selected ? "bg-white font-semibold text-navy-900 shadow-[inset_1px_0_0_#e3e8ee,inset_-1px_0_0_#e3e8ee,inset_0_1px_0_#e3e8ee]" : "font-medium text-[#33475a] hover:bg-white/60 hover:text-navy-900"}`}>
                      {label}
                      {selected && <span aria-hidden className="absolute inset-x-[14%] bottom-0.75 h-0.75 rounded-full bg-brand-500" />}
                    </button>
                  );
                })}
              </div>

              <div className="px-5 pb-(--lg-gap-md) pt-(--lg-pad-top) md:px-(--card-px)">
                {/* Phones show the brand above the card, so the card repeats it only beside the hero. While a sign-in
                    error is shown the repeat steps aside: its slot is about the alert's height, so the alert doesn't push
                    the card past a short viewport, and the hero logo stays in view. */}
                {!(tab === "login" && error) && <Wordmark className="mx-auto mb-(--lg-gap-sm) hidden w-[min(var(--lg-logo-w),56%)] md:block" />}

                <div role="tabpanel" id="panel-login" aria-labelledby="tab-login" hidden={tab !== "login"}>
                  <ViewHeading title="Welcome back!" subtitle="Sign in to your Zoiko Assure account." />
                  <SsoOptions />

                  <div className="my-(--lg-gap-sm) flex items-center gap-3 text-[13px] leading-5 text-slate-500 xl:text-[14px]">
                    <span aria-hidden className="h-px flex-1 bg-[#dde3ea]" />or sign in with email<span aria-hidden className="h-px flex-1 bg-[#dde3ea]" />
                  </div>

                  {error && (
                    <div id="signin-error" role="alert" className="mb-(--lg-gap-sm) flex gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[12px] leading-4 text-red-800">
                      <AlertCircle size={15} aria-hidden className="mt-px shrink-0 text-red-600" />
                      <span>{error}</span>
                    </div>
                  )}

                  <form onSubmit={submit} method="post" noValidate aria-describedby={error ? "signin-error" : undefined} className="grid gap-(--lg-gap-sm)">
                    <div>
                      <label htmlFor="email" className={LABEL}>Work email address</label>
                      <div className="relative">
                        <Mail size={18} strokeWidth={1.6} aria-hidden className={FIELD_ICON} />
                        <input ref={emailRef} id="email" type="email" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false}
                          required aria-required="true" aria-invalid={!!fieldErrors.email} aria-describedby={fieldErrors.email ? "email-error" : undefined}
                          value={email} onChange={(e) => { setEmail(e.target.value); setError(null); }} placeholder="name@company.com" disabled={busy}
                          className={`${INPUT} pl-11 pr-3 ${fieldBorder(fieldErrors.email)}`} />
                      </div>
                      {fieldErrors.email && <FieldError id="email-error">{fieldErrors.email}</FieldError>}
                    </div>

                    <div>
                      <label htmlFor="password" className={LABEL}>Password</label>
                      <div className="relative">
                        <Lock size={18} strokeWidth={1.6} aria-hidden className={FIELD_ICON} />
                        <input ref={passwordRef} id="password" type={show ? "text" : "password"} autoComplete="current-password"
                          required aria-required="true" aria-invalid={!!fieldErrors.password}
                          aria-describedby={[fieldErrors.password && "password-error", capsLock && "caps-lock"].filter(Boolean).join(" ") || undefined}
                          value={password} onChange={(e) => { setPassword(e.target.value); setError(null); }} placeholder="Enter your password" disabled={busy}
                          onKeyDown={(e) => setCapsLock(e.getModifierState("CapsLock"))} onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))}
                          onBlur={() => setCapsLock(false)}
                          className={`${INPUT} pl-11 pr-12 ${fieldBorder(fieldErrors.password)}`} />
                        <RevealButton shown={show} onToggle={() => setShow(!show)} controls="password" label="password" disabled={busy} />
                      </div>
                      {/* The password's error and the Caps Lock warning share the row under the field with "Forgot
                          password?" (where the reference shows "Remember me"), so they don't add to the card's height.
                          No "Remember me": sessions last a fixed, server-set time (ZA_SESSION_HOURS) and /auth/login takes
                          no persistence option, so a checkbox would promise something the backend doesn't do. */}
                      <div className="mt-(--lg-gap-xs) flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          {fieldErrors.password && <FieldError id="password-error" className="mt-0">{fieldErrors.password}</FieldError>}
                          {capsLock && <p id="caps-lock" className="text-[12px] leading-5 text-amber-800">Caps Lock is on.</p>}
                        </div>
                        <button ref={forgotRef} type="button" onClick={openReset} disabled={busy} className={`${ORANGE_LINK} shrink-0`}>Forgot password?</button>
                      </div>
                    </div>

                    <button type="submit" disabled={busy || !interactive} aria-busy={busy} className={PRIMARY}>
                      {busy
                        ? <><Loader2 size={18} aria-hidden className="motion-safe:animate-spin" />Logging in…</>
                        : <>Log in<ArrowRight size={21} strokeWidth={2.25} aria-hidden /></>}
                    </button>
                  </form>

                  <p className="mt-(--lg-gap-sm) text-center text-[14px] leading-[1.4] text-slate-600 md:text-(length:--lg-text)">
                    New to Zoiko Assure?{" "}
                    <button type="button" onClick={openRegister} disabled={busy} className={`${ORANGE_LINK} font-semibold`}>Register</button>
                  </p>
                </div>

                <div role="tabpanel" id="panel-register" aria-labelledby="tab-register" hidden={tab !== "register"}>
                  <RegisterForm />
                </div>

                <div role="tabpanel" id="panel-reset" aria-labelledby="tab-reset" hidden={tab !== "reset"}>
                  {tab === "reset" && <ResetPassword initialEmail={email} autoFocus={focusReset} onBack={backToLogin} />}
                </div>
              </div>

              <CardFooter />
            </div>

            {demo && demo.accounts.length > 0 && tab === "login" && (
              <div className="mt-4 rounded-xl border border-[#dfe6ed] bg-white p-3">
                <div className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Demo accounts</div>
                <ul className="scroll-thin grid max-h-52 gap-px overflow-y-auto">
                  {demo.accounts.map((a) => (
                    <li key={a.email}>
                      <button type="button" onClick={() => { setEmail(a.email); setError(null); passwordRef.current?.focus(); }}
                        className={`flex w-full min-w-0 items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-[12px] hover:bg-slate-50 ${email === a.email ? "bg-navy-50" : ""}`}>
                        <span className="truncate text-slate-800">{a.display_name}</span>
                        <span className="mono max-w-[45%] shrink-0 truncate text-[11px] text-slate-600">{a.roles.join(",")}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5 px-1 text-[11px] text-slate-500">Shown only while the API runs with ZA_DEMO_MODE=1. Use the locally configured demo password.</p>
              </div>
            )}
          </div>
        </main>

        <section aria-labelledby="hero-heading" className="min-h-0 pt-4 md:col-start-1 md:row-start-2 md:pt-0">
          <h2 id="hero-heading" className="text-[30px] font-bold leading-[1.04] tracking-[-0.035em] text-navy-950 md:mt-[calc(var(--hero-h)*0.3)] md:text-(length:--hero-h)">
            <span className="block">Assured</span>
            <span className="block">compliance for a</span>
            {/* #cd6c19 keeps large-text contrast (3:1) on the light wash; the brand orange itself measures below it */}
            <span className="block text-[#cd6c19]">stronger tomorrow.</span>
          </h2>
          <p className="mt-3 text-[15px] leading-snug text-slate-600 md:mt-[calc(var(--hero-h)*0.24)] md:text-(length:--hero-sub)">
            <span className="lg:block">AI-powered compliance. Real-time insights.</span> Complete assurance.
          </p>
          <ul className="mt-6 grid grid-cols-4 gap-x-2 md:mt-[calc(var(--hero-tile)*0.36)] md:w-fit md:grid-cols-[repeat(2,calc(var(--hero-tile)*1.2))] md:gap-x-[calc(var(--hero-tile)*0.45)] md:gap-y-4 lg:grid-cols-[repeat(4,calc(var(--hero-tile)*1.2))]">
            {FEATURES.map(({ label, icon: Icon }) => (
              <li key={label} className="flex flex-col items-center text-center">
                <span className="flex size-14 items-center justify-center rounded-xl border border-white bg-white/80 shadow-[0_10px_24px_-14px_rgba(12,42,62,0.45)] backdrop-blur-sm md:size-(--hero-tile)">
                  <Icon className="size-[54%]" />
                </span>
                <span className="mt-2 text-[11.5px] font-medium leading-tight text-navy-950 md:mt-[calc(var(--hero-tile)*0.08)] md:text-(length:--hero-label)">{label}</span>
              </li>
            ))}
          </ul>
        </section>

        <p className="mt-4 min-h-0 border-l-[3px] border-brand-500 pl-4 text-[11px] font-medium uppercase leading-[1.75] tracking-[0.26em] text-navy-900 md:col-start-1 md:row-start-4 md:mb-(--hero-bottom) md:mt-4 md:ml-[calc(-0.3*var(--hero-x))] md:pl-[calc(var(--hero-x)*0.28)] md:text-(length:--hero-tag) md:text-white">
          <span className="block">Trusted compliance.</span>
          <span className="block">Real world impact.</span>
        </p>
      </div>
    </div>
  );
}

function ViewHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="text-center">
      <h1 className="text-(length:--lg-h1) font-bold leading-[1.2] tracking-[-0.02em] text-navy-950">{title}</h1>
      <p className="mt-0.5 text-(length:--lg-sub) leading-[1.4] text-slate-600">{subtitle}</p>
    </div>
  );
}

type Glyph = (props: { className?: string }) => React.ReactElement;

const SSO: { id: string; label: string; icon: Glyph }[] = [
  { id: "microsoft", label: "Continue with Microsoft", icon: MicrosoftLogo },
  { id: "google", label: "Continue with Google", icon: GoogleLogo },
  { id: "apple", label: "Continue with Apple", icon: AppleLogo },
  { id: "sso", label: "Continue with Company SSO", icon: ({ className }) => <Building2 strokeWidth={1.75} className={`text-navy-800 ${className}`} /> },
];

/** Microsoft, Google, Apple and company (OIDC/SAML) sign-in don't exist yet: the backend signs in with email and
 *  password only (backend/app/auth.py). The options are shown disabled, with the reason, so they can't be mistaken for
 *  working ones. Enable one only by connecting it to a real federation endpoint. */
function SsoOptions() {
  return (
    <div className="mt-(--lg-gap-md)">
      <ul className="grid gap-(--lg-gap-xs)">
        {SSO.map(({ id, label, icon: Icon }) => (
          <li key={id}>
            <button type="button" disabled aria-describedby="sso-unavailable"
              className="flex h-(--lg-sso-h) w-full cursor-not-allowed items-center gap-3 rounded-lg border border-[#dfe4ea] bg-[#fafbfc] pl-4 pr-3 text-left text-(length:--lg-text) font-medium text-slate-500">
              <span aria-hidden className="flex w-6 shrink-0 justify-center opacity-60"><Icon className="size-5" /></span>
              <span className="min-w-0 flex-1 truncate">{label}</span>
              <ChevronRight size={18} aria-hidden className="shrink-0 text-slate-400" />
            </button>
          </li>
        ))}
      </ul>
      <p id="sso-unavailable" className="mt-(--lg-gap-xs) flex items-start gap-1.5 text-[12px] leading-4 text-slate-600">
        <Info size={14} aria-hidden className="mt-0.5 shrink-0 text-slate-500" />
        Single sign-on isn&apos;t configured yet.
      </p>
    </div>
  );
}

/** Policy and help pages don't exist yet, so these stay plain text; make each a link once its page is published. */
const FOOTER_ITEMS = ["Privacy", "Terms", "Security", "Accessibility", "Help"];

function CardFooter() {
  return (
    <footer className="mx-5 flex flex-wrap items-center justify-center gap-x-2.5 gap-y-0.5 border-t border-[#e6eaef] py-(--lg-foot-y) text-[10px] leading-4 text-slate-500 sm:justify-between md:mx-(--card-px) 2xl:text-[10.5px]">
      <ul aria-label="Policies and help" className="flex flex-wrap items-center">
        {FOOTER_ITEMS.map((item, i) => (
          <li key={item} className="flex items-center">
            {i > 0 && <span aria-hidden className="mx-1 h-3 w-px bg-slate-300" />}
            {item}
          </li>
        ))}
      </ul>
      <p>© {new Date().getFullYear()} Zoiko Assure. All rights reserved.</p>
    </footer>
  );
}

function FieldError({ id, className = "mt-1", children }: { id: string; className?: string; children: React.ReactNode }) {
  return (
    <p id={id} className={`flex items-start gap-1.5 text-[12px] leading-5 text-red-700 ${className}`}>
      <AlertCircle size={14} aria-hidden className="mt-0.5 shrink-0" />{children}
    </p>
  );
}

function RevealButton({ shown, onToggle, controls, label, disabled }: { shown: boolean; onToggle: () => void; controls: string; label: string; disabled?: boolean }) {
  return (
    <button type="button" onClick={onToggle} aria-pressed={shown} aria-controls={controls} aria-label={`Show ${label}`} disabled={disabled}
      className="absolute right-2 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 disabled:opacity-50">
      {shown ? <EyeOff size={19} strokeWidth={1.6} aria-hidden /> : <Eye size={19} strokeWidth={1.6} aria-hidden />}
    </button>
  );
}

// Orange text on white uses #b05d12 (4.7:1, WCAG AA for small text); the brand orange itself is too light for text.
const ORANGE_LINK = "rounded text-[14px] font-medium leading-[1.4] text-[#b05d12] hover:text-brand-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 disabled:opacity-50 md:text-(length:--lg-text)";
const LINK = "inline-flex items-center gap-1.5 rounded text-[14px] font-medium text-navy-700 hover:text-navy-900 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400 disabled:opacity-50";
// White bold 19px text is large text; the gradient behind it stays above 3:1.
const PRIMARY = "mt-(--lg-gap-xs) inline-flex h-(--lg-btn-h) w-full items-center justify-center gap-2.5 rounded-[10px] bg-linear-to-r from-[#e0842c] to-[#c4651a] text-[19px] font-bold text-white shadow-[0_12px_24px_-12px_rgba(196,101,26,0.8)] transition-[filter] hover:brightness-95 active:brightness-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:brightness-100";
const LABEL = "mb-(--lg-gap-xs) block text-[14px] font-medium leading-[1.4] text-[#172b3a] md:text-(length:--lg-text)";
const INPUT = "input h-(--lg-field-h) rounded-lg text-(length:--lg-text) placeholder:text-slate-500";
const FIELD_ICON = "pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400";
const fieldBorder = (error?: string) => (error ? "border-red-500 focus:border-red-600 focus:ring-red-100" : "border-[#8494a7]");

type InputProps = { id: string; label: string; value: string; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void; error?: string };

function TextField({ id, label, error, icon: Icon, type = "text", ...input }:
  InputProps & { icon: LucideIcon; type?: "text" | "email"; autoComplete: string; placeholder?: string }) {
  const email = type === "email";
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      <div className="relative">
        <Icon size={18} strokeWidth={1.6} aria-hidden className={FIELD_ICON} />
        <input id={id} type={type} inputMode={email ? "email" : undefined} autoCapitalize={email ? "none" : undefined} spellCheck={email ? false : undefined}
          required aria-required="true" aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} {...input}
          className={`${INPUT} pl-11 pr-3 ${fieldBorder(error)}`} />
      </div>
      {error && <FieldError id={`${id}-error`}>{error}</FieldError>}
    </div>
  );
}

function PasswordField({ id, label, error, hint, ...input }: InputProps & { hint?: string }) {
  const [show, setShow] = useState(false);
  const hintShown = !!hint && !error;  // the policy error repeats the hint, so only one shows
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      <div className="relative">
        <Lock size={18} strokeWidth={1.6} aria-hidden className={FIELD_ICON} />
        <input id={id} type={show ? "text" : "password"} autoComplete="new-password" required aria-required="true" aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : hintShown ? `${id}-hint` : undefined} {...input}
          className={`${INPUT} pl-11 pr-12 ${fieldBorder(error)}`} />
        <RevealButton shown={show} onToggle={() => setShow(!show)} controls={id} label={label.toLowerCase()} />
      </div>
      {hintShown && <p id={`${id}-hint`} className="mt-1.5 text-[12px] leading-5 text-slate-600">{hint}</p>}
      {error && <FieldError id={`${id}-error`}>{error}</FieldError>}
    </div>
  );
}

/** Says up front that a form is not connected to the backend, so it can't be mistaken for a working flow. */
function NotConnected({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <p id={id} className="mt-(--lg-gap-sm) flex gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-left text-[13px] leading-5 text-amber-900">
      <Info size={16} aria-hidden className="mt-0.5 shrink-0 text-amber-700" />
      <span>{children}</span>
    </p>
  );
}

/** Confirms after a valid submit that nothing was sent. The live region stays mounted so screen readers announce it. */
function Unsent({ show, children }: { show: boolean; children: React.ReactNode }) {
  return <div role="status">{show && <p className="mt-(--lg-gap-sm) text-[13px] leading-5 text-slate-700">{children}</p>}</div>;
}

/** Self-service registration has no backend yet (README: Users & Roles), so this form validates what it is given and
 *  says plainly that nothing was sent. Connect the registration request in `submit` once an endpoint exists. */
function RegisterForm() {
  const [form, setForm] = useState<Registration>({ name: "", email: "", password: "", confirm: "" });
  const [attempted, setAttempted] = useState(false);
  const [checked, setChecked] = useState(false);  // the details were valid, but there is nowhere to send them
  const errors: Partial<Record<keyof Registration, string>> = attempted ? validateRegistration(form) : {};
  const field = (key: keyof Registration) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => { const value = e.target.value; setForm((f) => ({ ...f, [key]: value })); setChecked(false); },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    const invalid = (["name", "email", "password", "confirm"] as const).find((k) => validateRegistration(form)[k]);
    setChecked(!invalid);
    if (invalid) document.getElementById(`reg-${invalid}`)?.focus();
  };

  return (
    <>
      <ViewHeading title="Create your account" subtitle="Register for access to a Zoiko Assure workspace." />
      <NotConnected id="register-unavailable">
        Registration isn&apos;t connected yet, so this form can&apos;t create an account. Contact your organisation administrator for access.
      </NotConnected>

      <form onSubmit={submit} method="post" noValidate aria-describedby="register-unavailable" className="mt-(--lg-gap-sm) grid gap-(--lg-gap-sm)">
        <TextField id="reg-name" label="Full name" icon={User} autoComplete="name" error={errors.name} {...field("name")} />
        <TextField id="reg-email" label="Work email address" icon={Mail} type="email" autoComplete="username" placeholder="name@company.com" error={errors.email} {...field("email")} />
        <PasswordField id="reg-password" label="Password" hint={PASSWORD_POLICY} error={errors.password} {...field("password")} />
        <PasswordField id="reg-confirm" label="Confirm password" error={errors.confirm} {...field("confirm")} />
        <button type="submit" className={PRIMARY}>Register</button>
      </form>
      <Unsent show={checked}>Your details are complete, but nothing was sent and no account was created.</Unsent>
    </>
  );
}

/** Password reset has no backend yet: the only password endpoint changes the password of a signed-in account. This
 *  view validates the email and says plainly that nothing was sent. */
function ResetPassword({ initialEmail, autoFocus, onBack }: { initialEmail: string; autoFocus: boolean; onBack: () => void }) {
  const [email, setEmail] = useState(initialEmail);
  const [attempted, setAttempted] = useState(false);
  const [checked, setChecked] = useState(false);
  // Opened from "Forgot password?", the field takes focus; opened from its tab, focus stays on the tab.
  useEffect(() => { if (autoFocus) document.getElementById("reset-email")?.focus(); }, [autoFocus]);
  const error = attempted ? emailError(email) : undefined;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    const invalid = !!emailError(email);
    setChecked(!invalid);
    if (invalid) document.getElementById("reset-email")?.focus();
  };

  return (
    <>
      <ViewHeading title="Reset your password" subtitle="Enter the work email for your Zoiko Assure account." />
      <NotConnected id="reset-unavailable">
        Password reset isn&apos;t connected yet, so no reset link can be sent. Contact your organisation administrator to regain access.
      </NotConnected>

      <form onSubmit={submit} method="post" noValidate aria-describedby="reset-unavailable" className="mt-(--lg-gap-sm) grid gap-(--lg-gap-sm)">
        <TextField id="reset-email" label="Work email address" icon={Mail} type="email" autoComplete="username" placeholder="name@company.com" error={error}
          value={email} onChange={(e) => { setEmail(e.target.value); setChecked(false); }} />
        <button type="submit" className={PRIMARY}>Send reset link</button>
      </form>
      <Unsent show={checked}>Nothing was sent. A reset link can&apos;t be sent until password reset is connected.</Unsent>

      <div className="mt-(--lg-gap-sm) flex justify-center">
        <button type="button" onClick={onBack} className={LINK}><ArrowLeft size={15} aria-hidden />Back to log in</button>
      </div>
    </>
  );
}

/** The Zoiko Assure wordmark (public/logo.png), cropped to its artwork so it scales with its CSS width and its left edge
 *  lines up with the text beside it. logo.png is a 1001px square; the wordmark, ™ included, spans x 222-780 and
 *  y 424-577, so the image is drawn at 1001/558 of the box width and centred on the artwork. Without a label it is
 *  decorative (the card repeats the brand shown beside it). */
function Wordmark({ label, className = "" }: { label?: string; className?: string }) {
  return (
    <span role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}
      className={`block aspect-558/153 bg-[url(/logo.png)] bg-size-[179.39%_auto] bg-position-[50.11%_50%] bg-no-repeat ${className}`} />
  );
}

/** The hero photograph layer and the overlays that keep its text readable: a light wash behind the navy text on the
 *  left, a shade at the bottom for the white tagline and, on phones, a fade from the band into the page. Decorative. */
function HeroBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-75 overflow-hidden md:bottom-0 md:h-auto lg:fixed">
      {/* eslint-disable-next-line @next/next/no-img-element -- a static, decorative SVG; next/image adds nothing here */}
      {/* Phones draw it wider than the band so only the sky and skyline, not the window frame, sit behind the brand */}
      <img src={HERO_IMAGE} alt="" fetchPriority="high" className="absolute inset-y-0 left-0 h-full w-[220%] max-w-none object-cover object-left md:w-full" />
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(243,246,250,0.94)_0%,rgba(243,246,250,0.84)_30%,rgba(243,246,250,0.5)_46%,rgba(243,246,250,0)_62%)] md:mask-[linear-gradient(to_bottom,#000_0%,#000_60%,transparent_82%)]" />
      <div className="absolute inset-0 hidden bg-[linear-gradient(to_top,rgba(5,14,23,0.82)_0%,rgba(5,14,23,0.38)_16%,rgba(5,14,23,0)_30%)] md:block" />
      <div className="absolute inset-0 hidden bg-[linear-gradient(90deg,rgba(6,20,34,0)_55%,rgba(6,20,34,0.35)_100%)] md:block" />
      <div className="absolute inset-x-0 bottom-0 h-3/4 bg-[linear-gradient(to_bottom,rgba(233,239,245,0),#e9eff5_92%)] md:hidden" />
    </div>
  );
}

// Hero feature icons: outlined, in the brand orange and navy (decorative; each sits beside its text label).
const ICON_ORANGE = "#e07b22";
const ICON_NAVY = "#11395a";

const FEATURES: { label: string; icon: Glyph }[] = [
  { label: "Risk Management", icon: ({ className }) => (
    <svg viewBox="0 0 48 48" fill="none" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className}>
      <path d="M24 5.5 9.5 11v10.6c0 9.3 6.1 16.9 14.5 20.4 8.4-3.5 14.5-11.1 14.5-20.4V11L24 5.5Z" stroke={ICON_ORANGE} />
      <path d="m17.5 24.5 4.8 4.8 8.6-9.6" stroke={ICON_NAVY} strokeWidth={3} />
    </svg>
  ) },
  { label: "Compliance Insights", icon: ({ className }) => (
    <svg viewBox="0 0 48 48" fill="none" aria-hidden className={className}>
      <rect x="8" y="28" width="7" height="13" rx="1.5" fill={ICON_ORANGE} />
      <rect x="20.5" y="19" width="7" height="22" rx="1.5" fill={ICON_ORANGE} />
      <rect x="33" y="8" width="7" height="33" rx="1.5" fill={ICON_NAVY} />
    </svg>
  ) },
  { label: "Policies & Governance", icon: ({ className }) => (
    <svg viewBox="0 0 48 48" fill="none" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className}>
      <rect x="10" y="5.5" width="28" height="37" rx="3.5" stroke={ICON_ORANGE} />
      <path d="M16.5 15h8M16.5 22h15M16.5 29h15M16.5 36h9" stroke={ICON_NAVY} />
      <rect x="28.5" y="12" width="4" height="5" rx="1" fill={ICON_ORANGE} stroke="none" />
    </svg>
  ) },
  { label: "Global Assurance", icon: ({ className }) => (
    <svg viewBox="0 0 48 48" fill="none" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className}>
      <circle cx="24" cy="24" r="17" stroke={ICON_NAVY} />
      <path d="M24 7c-4.8 4.6-7.2 10.3-7.2 17s2.4 12.4 7.2 17c4.8-4.6 7.2-10.3 7.2-17S28.8 11.6 24 7Z" stroke={ICON_NAVY} />
      <path d="M8 18.5h32M8 29.5h32" stroke={ICON_NAVY} />
    </svg>
  ) },
];

// Provider marks for the (not yet available) sign-in options, drawn to each provider's published logo colours.
function MicrosoftLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 21 21" aria-hidden className={className}>
      <rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" /><rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

function GoogleLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden className={className}>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

function AppleLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={className}>
      <path fill="#111" d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701" />
    </svg>
  );
}
