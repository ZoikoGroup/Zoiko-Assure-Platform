"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import { Bell, Building, ChevronDown, HelpCircle, KeyRound, Loader2, LogOut, Menu, PanelLeftClose, PanelLeftOpen, ShieldCheck, UserCog } from "lucide-react";
import { api } from "@/lib/api";
import { personaOf, useAuth } from "@/lib/actor";
import { roleLabel, rolesText, workContext } from "@/lib/roles";
import { jumpTargets, navFor, routeMeta } from "./nav";
import {
  Breadcrumb, Drawer, Dropdown, DropdownItem, ErrorBox, Field, IconButton, Modal, PageTitleProvider, SearchInput, ToastProvider,
  Tooltip, useAction, usePageTitle, useToast,
} from "./ui";

const initials = (n?: string) => (n ?? "?").replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((x) => x[0]).join("").toUpperCase();
const shortName = (n?: string) => (n ?? "").replace(/\s*\(.*\)/, "");

export default function Shell({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <PageTitleProvider>
        <Frame>{children}</Frame>
      </PageTitleProvider>
    </ToastProvider>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { me, ready } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);

  useEffect(() => { try { setCollapsed(localStorage.getItem("za-nav-collapsed") === "1"); } catch {} }, []);
  const toggle = () => { const v = !collapsed; setCollapsed(v); try { localStorage.setItem("za-nav-collapsed", v ? "1" : "0"); } catch {} };
  useEffect(() => setMobile(false), [path]);

  if (path === "/login") return <>{children}</>;
  if (!ready || !me) {
    return <div role="status" className="flex min-h-screen items-center justify-center text-[13px] text-slate-500">Checking your session…</div>;
  }
  if (me.must_change_password) return <ForcedPasswordChange />;

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only z-[80] rounded-md bg-navy-700 px-3 py-2 text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
      <aside aria-label="Primary navigation"
        className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-slate-200 bg-white transition-[width] duration-200 lg:flex ${collapsed ? "w-16" : "w-[232px]"}`}>
        <SidebarBody narrow={collapsed} path={path} onToggle={toggle} />
      </aside>
      <Drawer open={mobile} onClose={() => setMobile(false)} title="Navigation" side="left" width="max-w-[260px]">
        <div className="-m-4 flex h-[calc(100vh-49px)] flex-col"><SidebarBody narrow={false} path={path} showContext /></div>
      </Drawer>
      <div className={`flex min-h-screen flex-col transition-[padding] duration-200 ${collapsed ? "lg:pl-16" : "lg:pl-[232px]"}`}>
        <Header path={path} onMenu={() => setMobile(true)} />
        <main id="main" key={me.actor_id} className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-5 sm:px-6">{children}</main>
      </div>
    </div>
  );
}

export function Logo({ mark = false, width = 140 }: { mark?: boolean; width?: number }) {
  // logo.png is a 1000px square with the wordmark centred; logo-mark.jpg is a 1000px square with the app icon centred.
  // `width` scales the wordmark crop proportionally (140px is the sidebar size).
  const s = width / 140;
  return mark ? (
    <span role="img" aria-label="Zoiko Assure" className="block h-8 w-8"
      style={{ backgroundImage: "url(/logo-mark.jpg)", backgroundSize: "90px 90px", backgroundPosition: "-29px -29px", backgroundRepeat: "no-repeat" }} />
  ) : (
    <span role="img" aria-label="Zoiko Assure" className="block"
      style={{ width, height: 43 * s, backgroundImage: "url(/logo.png)", backgroundSize: `${228 * s}px ${228 * s}px`,
        backgroundPosition: `${-44 * s}px ${-91 * s}px`, backgroundRepeat: "no-repeat" }} />
  );
}

/** Where the signed-in person is working, kept visually separate from who they are (the account menu). Zoiko staff
 *  belong to no customer organisation, so they see their own workspace here, never a customer's name. */
function WorkContext() {
  const { me } = useAuth();
  const c = workContext(me);
  const Icon = c.kind === "organisation" ? Building : ShieldCheck;
  const title = c.kind === "organisation" ? `Organisation ${c.name} (${c.id}). Records on every page belong to this organisation.`
    : c.kind === "platform" ? "Zoiko platform administration. You belong to no customer organisation and see registry metadata only."
    : "Zoiko content operations. You belong to no customer organisation and have no access to customer data.";
  return (
    <span data-za="org-context" title={title} className="flex min-w-0 items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1">
      <Icon size={14} aria-hidden className="shrink-0 text-slate-400" />
      <span className="min-w-0 leading-tight">
        <span className="block text-[9px] font-semibold uppercase tracking-[0.08em] text-slate-400">{c.label}</span>
        <span className="block truncate text-[12px] font-medium text-slate-800">{c.name}</span>
      </span>
    </span>
  );
}

/** A sidebar item's icon, swapped for a spinner while that item's navigation is pending, so the click is acknowledged
 *  at once even when the next page takes a moment (prefetched routes in a production build skip the pending state). */
function NavIcon({ icon: Icon, active }: { icon: LucideIcon; active: boolean }) {
  const { pending } = useLinkStatus();
  return pending ? (
    <>
      <Loader2 size={16} aria-hidden className="text-navy-600 motion-safe:animate-spin" />
      <span className="sr-only">Loading</span>
    </>
  ) : <Icon size={16} aria-hidden className={active ? "text-navy-700" : "text-slate-400"} />;
}

/** Account menu: the organisation this account belongs to (or, for Zoiko staff, that it belongs to none). */
function AccountContext() {
  const { me } = useAuth();
  const c = workContext(me);
  return (
    <div className="border-b border-slate-100 px-3 py-2 text-[12px]">
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{c.kind === "organisation" ? "Member of organisation" : c.label}</div>
      <div className="font-medium text-slate-800">{c.name}</div>
      {c.kind !== "organisation" && <div className="text-[11px] text-slate-500">Not a member of any customer organisation</div>}
    </div>
  );
}

function SidebarBody({ narrow, path, onToggle, showContext }: { narrow: boolean; path: string; onToggle?: () => void; showContext?: boolean }) {
  const { me, logout } = useAuth();
  const activeHref = routeMeta(path)?.item.href;
  // Keep the current page's item visible in the scrollable sidebar.
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest" }); }, [activeHref]);
  return (
    <>
      <div className={`flex h-14 shrink-0 items-center border-b border-slate-200 ${narrow ? "justify-center" : "justify-between pl-4 pr-2"}`}>
        <Link href="/" aria-label="Dashboard" className="rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400">
          <Logo mark={narrow} />
        </Link>
        {onToggle && !narrow && <IconButton label="Collapse sidebar" icon={PanelLeftClose} onClick={onToggle} />}
      </div>
      {onToggle && narrow && <div className="flex justify-center pt-2"><IconButton label="Expand sidebar" icon={PanelLeftOpen} onClick={onToggle} /></div>}
      {showContext && <div className="flex shrink-0 border-b border-slate-200 px-2.5 py-2 [&>span]:w-full"><WorkContext /></div>}

      <nav ref={navRef} className={`flex-1 overflow-y-auto overflow-x-hidden py-2 ${narrow ? "no-scrollbar px-2" : "scroll-thin px-2.5"}`}>
        {navFor(personaOf(me)).map((g, gi) => {
          const headingId = `nav-group-${gi}`;
          return (
          <div key={g.group} role="group" aria-labelledby={narrow ? undefined : headingId} aria-label={narrow ? g.group : undefined} className="mb-2">
            {narrow ? (gi > 0 && <div className="mx-2 my-2 border-t border-slate-100" />) : (
              <div id={headingId} className="px-2.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{g.group}</div>
            )}
            {g.items.map(({ href, label, icon: Icon }) => {
              const active = activeHref === href;
              const link = (
                <Link href={href} aria-current={active ? "page" : undefined} aria-label={narrow ? label : undefined}
                  className={`relative flex items-center gap-2.5 rounded-md text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400
                    ${narrow ? "h-9 w-10 justify-center" : "h-8 px-2.5"}
                    ${active ? "bg-navy-50 font-medium text-navy-800" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                  {active && !narrow && <span aria-hidden className="absolute left-0 top-1.5 h-5 w-0.5 rounded-full bg-navy-700" />}
                  <NavIcon icon={Icon} active={active} />
                  {!narrow && <span className="truncate">{label}</span>}
                </Link>
              );
              return <div key={href} className="mb-px">{narrow ? <Tooltip content={label} side="right">{link}</Tooltip> : link}</div>;
            })}
          </div>
          );
        })}
      </nav>

      <div className="shrink-0 border-t border-slate-200 p-2.5">
        {narrow ? (
          <div className="flex flex-col items-center gap-1.5">
            <span title={`${me?.display_name ?? ""} · ${rolesText(me)}`} className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
            <IconButton label="Sign out" icon={LogOut} onClick={logout} />
          </div>
        ) : (
          <div className="flex items-center gap-2.5 px-1">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-slate-900">{shortName(me?.display_name)}</div>
              <div title={rolesText(me)} className="truncate text-[11px] text-slate-500">{rolesText(me)}</div>
            </div>
            <IconButton label="Sign out" icon={LogOut} onClick={logout} />
          </div>
        )}
      </div>
    </>
  );
}

function Header({ path, onMenu }: { path: string; onMenu: () => void }) {
  const { me, logout } = useAuth();
  const router = useRouter();
  const { title } = usePageTitle();
  const [wm, setWm] = useState<{ cell_id: string; record_seq: number } | null>(null);
  const [alerts, setAlerts] = useState<{ issues: number; facts: number } | null>(null);
  const [q, setQ] = useState("");
  const [pw, setPw] = useState(false);

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

  const meta = routeMeta(path);
  const crumbs = meta
    ? [...(meta.group !== meta.item.label ? [{ label: meta.group }] : []), { label: meta.item.label, href: meta.item.href },
      ...(meta.isDetail && title ? [{ label: title }] : [])]
    : [{ label: "Zoiko Assure" }];
  const hits = useMemo(() => (q ? jumpTargets(personaOf(me)).filter((i) => `${i.label} ${i.group}`.toLowerCase().includes(q.toLowerCase())).slice(0, 7) : []), [q, me]);
  const pending = (alerts?.issues ?? 0) + (alerts?.facts ?? 0);

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
      <IconButton label="Open navigation" icon={Menu} onClick={onMenu} className="lg:hidden" />
      <div className="min-w-0 flex-1"><Breadcrumb items={crumbs} /></div>

      <div className="flex shrink-0 items-center gap-2">
        <div className="relative hidden md:block">
          <SearchInput value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to…" aria-label="Jump to page" inputClassName="w-52 py-1"
            onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) { router.push(hits[0].href); setQ(""); } if (e.key === "Escape") setQ(""); }} />
          {hits.length > 0 && (
            <ul role="listbox" aria-label="Pages" className="absolute right-0 z-40 mt-1 w-72 overflow-hidden rounded-md border border-slate-200 bg-white py-1 shadow-lg">
              {hits.map(({ href, label, group }) => (
                <li key={href}>
                  <Link href={href} onClick={() => setQ("")} className="flex items-center justify-between gap-2 px-3 py-1.5 text-[13px] text-slate-700 hover:bg-slate-50">
                    {label}<span className="text-[11px] text-slate-400">{group}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="hidden max-w-[240px] md:flex"><WorkContext /></div>

        {wm && (
          <span title={`Data cell ${wm.cell_id}. Views reflect everything recorded up to sequence ${wm.record_seq} (knowledge time).`}
            className="hidden items-center gap-1.5 rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 2xl:flex">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500" /><span className="mono">{wm.cell_id} · seq {wm.record_seq}</span>
          </span>
        )}

        <Dropdown label={pending ? `Notifications, ${pending} open item(s)` : "Notifications"} menuClassName="w-72"
          buttonClassName="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
          button={<><Bell size={16} aria-hidden />{pending > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-brand-500" aria-hidden />}</>}>
          {(close) => (
            <>
              <div className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Needs attention</div>
              {!me?.tenant_id ? <p className="px-3 py-3 text-[13px] text-slate-500">Notifications cover customer-organisation work, so Zoiko staff accounts have none.</p>
                : !alerts ? <p className="px-3 py-3 text-[13px] text-slate-500">No notifications.</p>
                : pending === 0 ? <p className="px-3 py-3 text-[13px] text-slate-500">Nothing open right now.</p> : (
                  <>
                    {alerts.issues > 0 && <DropdownItem href="/governance" onClick={close}>{alerts.issues} open issue(s)</DropdownItem>}
                    {alerts.facts > 0 && <DropdownItem href="/fact-requests" onClick={close}>{alerts.facts} open fact request(s)</DropdownItem>}
                  </>
                )}
            </>
          )}
        </Dropdown>

        <Dropdown label="Account menu" menuClassName="w-72"
          buttonClassName="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy-400"
          button={<>
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-navy-700 text-[11px] font-semibold text-white">{initials(me?.display_name)}</span>
            <span className="hidden text-left sm:block">
              <span className="block max-w-[160px] truncate text-[12px] font-medium text-slate-900">{shortName(me?.display_name)}</span>
              <span className="block max-w-[160px] truncate text-[10px] text-slate-500">{me?.roles[0] ? roleLabel(me.roles[0]) : ""}</span>
            </span>
            <ChevronDown size={14} aria-hidden className="text-slate-400" />
          </>}>
          {(close) => (
            <>
              <div className="border-b border-slate-100 px-3 py-2.5">
                <div className="text-[13px] font-medium text-slate-900">{me?.display_name}</div>
                <div className="text-[12px] text-slate-500">{me?.email}</div>
                <ul className="mt-1.5 space-y-0.5" aria-label="Your roles">
                  {me?.roles.map((r) => <li key={r} className="flex items-center justify-between gap-2 text-[12px] text-slate-700">{roleLabel(r)}<span className="mono text-[10px] text-slate-400">{r}</span></li>)}
                </ul>
              </div>
              <AccountContext />
              <DropdownItem icon={UserCog} href="/admin/users" onClick={close}>My access & permissions</DropdownItem>
              <DropdownItem icon={KeyRound} onClick={() => { close(); setPw(true); }}>Change password</DropdownItem>
              <DropdownItem icon={HelpCircle} href="/admin/dictionary" onClick={close}>Controlled dictionary</DropdownItem>
              <div className="my-1 border-t border-slate-100" />
              <DropdownItem icon={LogOut} danger onClick={logout}>Sign out</DropdownItem>
            </>
          )}
        </Dropdown>
      </div>
      {pw && <ChangePassword onClose={() => setPw(false)} />}
    </header>
  );
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const [done, setDone] = useState(false);
  const toast = useToast();
  return (
    <Modal open onClose={onClose} title="Change password" size="sm">
      {done ? <p className="text-[13px] text-emerald-800">Password changed. Your other sessions have been signed out.</p>
        : <PasswordForm onCancel={onClose} onChanged={async () => { toast("Password changed", "good"); setDone(true); }} />}
    </Modal>
  );
}

/** Shown instead of the app while the account still has a temporary password; the backend refuses every other call. */
function ForcedPasswordChange() {
  const { me, refresh, logout } = useAuth();
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex justify-center"><Logo /></div>
        <div className="card p-5">
          <h1 className="text-base font-semibold text-slate-900">Set your own password</h1>
          <p className="mb-4 mt-1 text-[13px] text-slate-600">{shortName(me?.display_name)}, you signed in with a temporary password. Choose a new one to continue.</p>
          <PasswordForm currentLabel="Temporary password" submitLabel="Set password and continue" onChanged={refresh} />
          <button type="button" className="btn-ghost mt-3 w-full justify-center" onClick={logout}>Sign out</button>
        </div>
      </div>
    </main>
  );
}

function PasswordForm({ onChanged, onCancel, submitLabel = "Update password", currentLabel = "Current password" }:
  { onChanged: () => Promise<void>; onCancel?: () => void; submitLabel?: string; currentLabel?: string }) {
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [conf, setConf] = useState("");
  const a = useAction();
  return (
    <>
      <ErrorBox error={a.error} />
      <form className="grid gap-3" onSubmit={(e) => {
        e.preventDefault();
        a.run(async () => { const r = await api("/auth/password", { method: "POST", json: { current_password: cur, new_password: nw } }); await onChanged(); return r; });
      }}>
        <Field label={currentLabel} htmlFor="pw-cur"><input id="pw-cur" type="password" autoComplete="current-password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="New password" htmlFor="pw-new" hint="At least 10 characters, with upper- and lower-case letters and a digit.">
          <input id="pw-new" type="password" autoComplete="new-password" className="input" value={nw} onChange={(e) => setNw(e.target.value)} />
        </Field>
        <Field label="Confirm new password" htmlFor="pw-conf" hint={conf && conf !== nw ? <span className="text-red-700">Passwords don&apos;t match.</span> : undefined}>
          <input id="pw-conf" type="password" autoComplete="new-password" className="input" value={conf} onChange={(e) => setConf(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          {onCancel && <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>}
          <button className="btn-primary" disabled={!cur || !nw || nw !== conf || a.busy}>{submitLabel}</button>
        </div>
      </form>
    </>
  );
}
