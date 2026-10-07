"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, Bell, Bot, Braces, Building2, ChevronLeft, ClipboardCheck, Database, FileStack, Flag, HelpCircle, History,
  Inbox, KeyRound, LayoutDashboard, Link2, LogOut, Menu, Package, Plug, Scale, Search, Settings, SlidersHorizontal, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/actor";
import { ErrorBox, Modal, useAction } from "./ui";

type Item = { href: string; label: string; icon: React.ElementType };
const NAV: { group: string; items: Item[] }[] = [
  { group: "Overview", items: [{ href: "/", label: "Dashboard", icon: LayoutDashboard }] },
  { group: "Regulatory content", items: [{ href: "/regulatory", label: "Regulatory inbox", icon: Inbox }, { href: "/rules", label: "Rule authoring", icon: Braces }] },
  { group: "Applicability", items: [{ href: "/entities", label: "Entities & facts", icon: Building2 }, { href: "/applicability", label: "Applicability review", icon: Scale }, { href: "/fact-requests", label: "Fact requests", icon: HelpCircle }] },
  { group: "Assurance", items: [{ href: "/obligations", label: "Obligation dossiers", icon: ClipboardCheck }, { href: "/controls", label: "Controls & tests", icon: SlidersHorizontal }, { href: "/governance", label: "Exceptions & issues", icon: Flag }] },
  { group: "Evidence", items: [{ href: "/evidence", label: "Evidence vault", icon: Archive }, { href: "/ipe", label: "Source runs & IPE", icon: Database }, { href: "/connectors", label: "Connectors", icon: Plug }, { href: "/ledger", label: "Ledger & checkpoints", icon: Link2 }, { href: "/packages", label: "Audit packages", icon: Package }] },
  { group: "AI & agents", items: [{ href: "/ai", label: "AI / agent assets", icon: Bot }] },
  { group: "Trust", items: [{ href: "/history", label: "Historical reconstruction", icon: History }, { href: "/admin", label: "Administration", icon: Settings }] },
];
const ALL = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.group })));
const isActive = (href: string, path: string) => (href === "/" ? path === "/" : path.startsWith(href));
const initials = (n?: string) => (n ?? "?").replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
const shortName = (n?: string) => (n ?? "").replace(/\s*\(.*\)/, "");

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { me, ready } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);

  useEffect(() => { try { setCollapsed(localStorage.getItem("za-nav-collapsed") === "1"); } catch {} }, []);
  const toggle = () => { const v = !collapsed; setCollapsed(v); try { localStorage.setItem("za-nav-collapsed", v ? "1" : "0"); } catch {} };
  useEffect(() => setMobile(false), [path]);

  if (path === "/login") return <>{children}</>;
  if (!ready || !me) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-slate-brand">Checking your session…</div>;
  }
  const current = ALL.find((i) => isActive(i.href, path));

  return (
    <div className="min-h-screen">
      {mobile && <div className="fixed inset-0 z-40 bg-navy-950/40 lg:hidden" onClick={() => setMobile(false)} />}
      <Sidebar collapsed={collapsed} mobile={mobile} toggle={toggle} path={path} onClose={() => setMobile(false)} />
      <div className={`flex min-h-screen flex-col transition-[padding] duration-300 ${collapsed ? "lg:pl-[104px]" : "lg:pl-[284px]"}`}>
        <Topbar title={current?.label ?? "Page"} group={current?.group ?? "Pages"} onMenu={() => setMobile(true)} />
        <main key={me.actor_id} className="w-full max-w-[1500px] flex-1 px-4 pb-10 pt-2 sm:px-6">{children}</main>
      </div>
    </div>
  );
}

export function Logo({ collapsed = false }: { collapsed?: boolean }) {
  // logo.png is a 1000px square with the mark centred; crop it with background positioning
  // logo-mark.jpg is a 1000px square with the app icon centred (≈322–678px); crop to the icon
  return collapsed ? (
    <span role="img" aria-label="Zoiko Assure" className="block h-11 w-11"
      style={{ backgroundImage: "url(/logo-mark.jpg)", backgroundSize: "124px 124px", backgroundPosition: "-40px -40px", backgroundRepeat: "no-repeat" }} />
  ) : (
    <span role="img" aria-label="Zoiko Assure" className="block h-[56px] w-[184px]"
      style={{ backgroundImage: "url(/logo.png)", backgroundSize: "300px 300px", backgroundPosition: "-58px -120px", backgroundRepeat: "no-repeat" }} />
  );
}

function Sidebar({ collapsed, mobile, toggle, path, onClose }: { collapsed: boolean; mobile: boolean; toggle: () => void; path: string; onClose: () => void }) {
  const { me, logout } = useAuth();
  const narrow = collapsed && !mobile;
  return (
    <aside
      className={`fixed bottom-4 left-4 top-4 z-50 flex flex-col rounded-2xl border border-navy-100 bg-white shadow-[0_8px_30px_rgba(12,42,62,0.08)] transition-all duration-300
        ${narrow ? "w-[80px]" : "w-[252px]"} ${mobile ? "translate-x-0" : "-translate-x-[120%] lg:translate-x-0"}`}
      aria-label="Primary navigation">
      <div className={`flex h-20 shrink-0 items-center border-b border-navy-50 ${narrow ? "justify-center" : "justify-between px-4"}`}>
        <Link href="/" aria-label="Dashboard"><Logo collapsed={narrow} /></Link>
        {mobile && <button className="rounded-lg p-1.5 text-slate-500 hover:bg-navy-50" onClick={onClose} aria-label="Close navigation"><X size={18} /></button>}
      </div>

      <button onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand" : "Collapse"}
        className="absolute -right-3.5 top-[66px] z-10 hidden h-7 w-7 items-center justify-center rounded-full border border-navy-100 bg-white text-navy-700 shadow-md transition hover:bg-brand-500 hover:text-white lg:flex">
        <ChevronLeft size={16} className={`transition-transform duration-300 ${collapsed ? "rotate-180" : ""}`} />
      </button>

      <nav className={`flex-1 overflow-y-auto overflow-x-hidden px-3 py-3 ${narrow ? "no-scrollbar" : "scroll-thin"}`}>
        {NAV.map((g, gi) => (
          <div key={g.group} className="mb-1.5">
            {narrow ? (gi > 0 && <div className="mx-3 my-2 border-t border-navy-50" />) : (
              <div className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">{g.group}</div>
            )}
            {g.items.map(({ href, label, icon: Icon }) => {
              const active = isActive(href, path);
              return (
                <Link key={href} href={href} title={narrow ? label : undefined} aria-current={active ? "page" : undefined}
                  className={`group mb-0.5 flex items-center gap-3 rounded-xl text-sm transition ${narrow ? "justify-center px-0 py-2.5" : "px-3 py-2.5"}
                    ${active ? "bg-gradient-to-br from-navy-600 to-navy-900 font-semibold text-white shadow-md shadow-navy-900/20" : "text-navy-800 hover:bg-navy-50"}`}>
                  <Icon size={18} className={active ? "text-brand-300" : "text-slate-500 group-hover:text-navy-700"} />
                  {!narrow && <span className="truncate">{label}</span>}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-navy-50 p-3">
        {narrow ? (
          <div className="flex flex-col items-center gap-2">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-brand-400 to-brand-600 text-sm font-bold text-white" title={me?.display_name}>{initials(me?.display_name)}</span>
            <button onClick={logout} title="Sign out" aria-label="Sign out" className="rounded-xl p-2.5 text-slate-500 hover:bg-red-50 hover:text-red-700"><LogOut size={18} /></button>
          </div>
        ) : (
          <>
            <div className="mb-3 flex items-center gap-3 rounded-xl bg-navy-50/70 p-2.5">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-400 to-brand-600 text-sm font-bold text-white">{initials(me?.display_name)}</span>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-navy-900">{shortName(me?.display_name)}</div>
                <div className="truncate text-[11px] text-slate-brand">{me?.roles.join(", ")}</div>
              </div>
            </div>
            <button onClick={logout} className="btn-ghost w-full justify-center"><LogOut size={16} />Sign out</button>
          </>
        )}
      </div>
    </aside>
  );
}

function Topbar({ title, group, onMenu }: { title: string; group: string; onMenu: () => void }) {
  const { me, logout } = useAuth();
  const router = useRouter();
  const [wm, setWm] = useState<{ cell_id: string; record_seq: number } | null>(null);
  const [alerts, setAlerts] = useState<{ issues: number; facts: number } | null>(null);
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);
  const [pw, setPw] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = () => api("/watermark").then(setWm).catch(() => setWm(null));
    t();
    const h = setInterval(t, 10000);
    return () => clearInterval(h);
  }, []);
  useEffect(() => {
    if (!me?.tenant_id) return;
    api("/dashboard").then((d) => setAlerts({ issues: d.open_issues.total, facts: d.open_fact_requests })).catch(() => {});
  }, [me]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const hits = useMemo(() => (q ? ALL.filter((i) => `${i.label} ${i.group}`.toLowerCase().includes(q.toLowerCase())).slice(0, 6) : []), [q]);
  const pending = (alerts?.issues ?? 0) + (alerts?.facts ?? 0);

  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-3 bg-[#f0f3f6]/85 px-4 py-4 backdrop-blur sm:px-6">
      <button className="rounded-xl border border-navy-100 bg-white p-2 text-navy-800 lg:hidden" onClick={onMenu} aria-label="Open navigation"><Menu size={18} /></button>
      <div className="min-w-0">
        <nav className="truncate text-sm text-slate-500" aria-label="Breadcrumb">{group} <span className="mx-1.5 text-slate-300">/</span> <span className="font-semibold text-navy-900">{title}</span></nav>
      </div>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <div className="relative hidden md:block">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to…" aria-label="Jump to page"
            onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) { router.push(hits[0].href); setQ(""); } if (e.key === "Escape") setQ(""); }}
            className="input w-56 rounded-xl bg-white py-2 pl-9" />
          {hits.length > 0 && (
            <div className="absolute right-0 mt-1 w-64 overflow-hidden rounded-xl border border-navy-100 bg-white shadow-lg">
              {hits.map(({ href, label, group: g, icon: Icon }) => (
                <Link key={href} href={href} onClick={() => setQ("")} className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-navy-50">
                  <Icon size={15} className="text-slate-500" />{label}<span className="ml-auto text-[10px] text-slate-400">{g}</span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {wm && (
          <span className="hidden items-center gap-1.5 rounded-xl border border-navy-100 bg-white px-3 py-2 text-[11px] text-navy-700 xl:flex" title="Current record watermark (knowledge time)">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /><span className="mono">{wm.cell_id} · seq {wm.record_seq}</span>
          </span>
        )}

        <Link href="/governance" title={alerts ? `${alerts.issues} open issue(s), ${alerts.facts} open fact request(s)` : "Notifications"}
          className="relative rounded-xl border border-navy-100 bg-white p-2 text-navy-700 hover:text-brand-600" aria-label="Notifications">
          <Bell size={18} />
          {pending > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-500 px-1 text-[10px] font-bold text-white">{pending}</span>}
        </Link>

        <div className="relative" ref={menuRef}>
          <button onClick={() => setMenu(!menu)} aria-expanded={menu} aria-haspopup="menu"
            className="flex items-center gap-2 rounded-xl border border-navy-100 bg-white py-1 pl-1 pr-3 hover:border-navy-200">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-navy-600 to-navy-900 text-xs font-bold text-white">{initials(me?.display_name)}</span>
            <span className="hidden text-left sm:block">
              <span className="block max-w-[150px] truncate text-xs font-semibold text-navy-900">{shortName(me?.display_name)}</span>
              <span className="block text-[10px] text-slate-500">{me?.roles[0]}</span>
            </span>
          </button>
          {menu && (
            <div role="menu" className="absolute right-0 mt-2 w-72 overflow-hidden rounded-2xl border border-navy-100 bg-white shadow-xl">
              <div className="bg-gradient-to-br from-navy-700 to-navy-900 p-4 text-white">
                <div className="text-sm font-semibold">{me?.display_name}</div>
                <div className="text-xs text-navy-100">{me?.email}</div>
                <div className="mt-2 text-[11px] text-brand-200">{me?.tenant_name}</div>
                <div className="mt-1 flex flex-wrap gap-1">{me?.roles.map((r) => <span key={r} className="mono rounded bg-white/10 px-1.5 py-0.5 text-[10px]">{r}</span>)}</div>
              </div>
              <button role="menuitem" onClick={() => { setMenu(false); router.push("/admin"); }} className="flex w-full items-center gap-2 px-4 py-2.5 text-sm hover:bg-navy-50"><FileStack size={16} className="text-slate-500" />My permissions</button>
              <button role="menuitem" onClick={() => { setMenu(false); setPw(true); }} className="flex w-full items-center gap-2 px-4 py-2.5 text-sm hover:bg-navy-50"><KeyRound size={16} className="text-slate-500" />Change password</button>
              <button role="menuitem" onClick={logout} className="flex w-full items-center gap-2 border-t border-navy-50 px-4 py-2.5 text-sm text-red-700 hover:bg-red-50"><LogOut size={16} />Sign out</button>
            </div>
          )}
        </div>
      </div>
      {pw && <ChangePassword onClose={() => setPw(false)} />}
    </header>
  );
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [conf, setConf] = useState("");
  const a = useAction();
  return (
    <Modal open onClose={onClose} title="Change password">
      <ErrorBox error={a.error} />
      {a.result ? (
        <p className="text-sm text-emerald-800">Password changed. Your other sessions have been signed out.</p>
      ) : (
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); a.run(() => api("/auth/password", { method: "POST", json: { current_password: cur, new_password: nw } })); }}>
          <div><label className="label" htmlFor="pw-cur">Current password</label><input id="pw-cur" type="password" autoComplete="current-password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} /></div>
          <div><label className="label" htmlFor="pw-new">New password</label><input id="pw-new" type="password" autoComplete="new-password" className="input" value={nw} onChange={(e) => setNw(e.target.value)} />
            <p className="mt-1 text-[11px] text-slate-500">At least 10 characters, with upper- and lower-case letters and a digit.</p></div>
          <div><label className="label" htmlFor="pw-conf">Confirm new password</label><input id="pw-conf" type="password" autoComplete="new-password" className="input" value={conf} onChange={(e) => setConf(e.target.value)} />
            {conf && conf !== nw && <p className="mt-1 text-[11px] text-red-700">Passwords don&apos;t match.</p>}</div>
          <div className="flex justify-end"><button className="btn-primary" disabled={!cur || !nw || nw !== conf || a.busy}>Update password</button></div>
        </form>
      )}
    </Modal>
  );
}
