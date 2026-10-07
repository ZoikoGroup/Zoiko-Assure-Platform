"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Eye, EyeOff, Fingerprint, LockKeyhole, ScrollText, ShieldCheck } from "lucide-react";
import { API } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { Logo } from "@/components/Shell";

type Demo = { password: string; accounts: { email: string; display_name: string; roles: string[]; actor_type: string; tenant_name: string }[] };

export default function Login() {
  const { login, me, ready } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState<Demo | null>(null);

  const next = () => {
    const n = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("next") : null;
    return n && n.startsWith("/") && !n.startsWith("//") && n !== "/login" ? n : "/"; // same-origin paths only
  };

  useEffect(() => { if (ready && me) router.replace(next()); }, [ready, me, router]);
  useEffect(() => { fetch(`${API}/auth/demo-accounts`).then((r) => (r.ok ? r.json() : null)).then(setDemo).catch(() => {}); }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.replace(next());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen">
      {/* brand panel */}
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-gradient-to-br from-navy-700 via-navy-800 to-navy-950 p-12 text-white lg:flex">
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-brand-500/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 -left-20 h-96 w-96 rounded-full bg-navy-400/20 blur-3xl" />
        <div className="relative w-fit rounded-2xl bg-white px-4 py-2 shadow-lg"><Logo /></div>
        <div className="relative max-w-md">
          <h1 className="text-4xl font-bold leading-tight">Obligation &amp; proof,<br /><span className="text-brand-300">for every entity and period.</span></h1>
          <p className="mt-4 text-sm leading-relaxed text-navy-100">
            Deterministic applicability, period assurance, tamper-evident evidence and independently verifiable audit packages.
          </p>
          <ul className="mt-8 space-y-4 text-sm">
            {[[ScrollText, "Every determination traces to a signed rule version and frozen facts"],
              [ShieldCheck, "Evidential states only. Never an unqualified compliance claim"],
              [Fingerprint, "Hash-chained evidence ledger with offline package verification"]].map(([Icon, t], i) => {
              const I = Icon as React.ElementType;
              return <li key={i} className="flex items-start gap-3"><span className="rounded-lg bg-white/10 p-2"><I size={16} className="text-brand-300" /></span><span className="pt-1.5 text-navy-50">{t as string}</span></li>;
            })}
          </ul>
        </div>
        <p className="relative text-[11px] text-navy-200">© {new Date().getFullYear()} Zoiko Group · Zoiko Assure</p>
      </div>

      {/* form */}
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden"><Logo /></div>
          <div className="card p-8">
            <div className="mb-6 flex items-center gap-3">
              <span className="rounded-xl bg-gradient-to-br from-brand-400 to-brand-600 p-2.5 text-white shadow-md shadow-brand-600/30"><LockKeyhole size={20} /></span>
              <div>
                <h2 className="text-xl font-bold text-navy-900">Sign in</h2>
                <p className="text-xs text-slate-brand">Use your Zoiko Assure work account</p>
              </div>
            </div>
            {error && <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>}
            <form onSubmit={submit} className="grid gap-4">
              <div>
                <label className="label" htmlFor="email">Email</label>
                <input id="email" type="email" autoComplete="username" required className="input py-2.5" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
              </div>
              <div>
                <label className="label" htmlFor="password">Password</label>
                <div className="relative">
                  <input id="password" type={show ? "text" : "password"} autoComplete="current-password" required className="input py-2.5 pr-10" value={password} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-navy-700">
                    {show ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
              <button className="btn-primary justify-center bg-gradient-to-br from-navy-600 to-navy-900 py-2.5 shadow-md shadow-navy-900/20" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
            <p className="mt-4 text-center text-[11px] text-slate-500">Five failed attempts lock the account for 15 minutes. Production sign-in uses your organisation&apos;s SSO.</p>
          </div>

          {demo && demo.accounts.length > 0 && (
            <div className="card mt-4 p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wide text-slate-brand">Demo accounts</span>
                <span className="text-[11px] text-slate-500">password <span className="mono rounded bg-navy-50 px-1 text-navy-800">{demo.password}</span></span>
              </div>
              <div className="scroll-thin grid max-h-56 gap-1 overflow-y-auto overflow-x-hidden pr-1">
                {demo.accounts.map((a) => (
                  <button key={a.email} type="button" onClick={() => { setEmail(a.email); setPassword(demo.password); setError(null); }}
                    className={`flex min-w-0 items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-navy-50 ${email === a.email ? "bg-brand-50 ring-1 ring-brand-200" : ""}`}>
                    <span className="truncate font-medium text-navy-900">{a.display_name}</span>
                    <span className="mono max-w-[45%] shrink-0 truncate text-[10px] text-slate-500">{a.roles.join(",")}</span>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[10px] text-slate-400">Shown only while the API runs with ZA_DEMO_MODE=1.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
