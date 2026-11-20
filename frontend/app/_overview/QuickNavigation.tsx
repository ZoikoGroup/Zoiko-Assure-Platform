"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { orgNav } from "@/components/nav";
import { useAuth } from "@/lib/actor";
import { useCan } from "@/lib/roles";
import { PANEL } from "./types";

/* E. Quick navigation. Both lists only point at existing screens: the actions come from the person's own
   permissions, and the sections are the organisation sidebar's entries (components/nav.ts orgNav), limited by the same
   generated permissions, so nothing is duplicated, invented or refused on arrival. */

/** The actions this person's roles allow, as shortcuts to the screens that perform them. `className` places the panel
 *  in the page's layout (e.g. growing to end level with the column beside it). */
export function YourActions({ className = "" }: { className?: string }) {
  const { can } = useCan();
  const shortcuts = [
    { perm: "submit_evidence", label: "Upload evidence", href: "/evidence" },
    { perm: "attest", label: "Review determinations", href: "/applicability" },
    { perm: "manage_facts", label: "Provide requested facts", href: "/fact-requests" },
    { perm: "manage_controls", label: "Implement a control", href: "/controls" },
    { perm: "manage_issues", label: "Work open issues", href: "/governance" },
    { perm: "approve_exception", label: "Decide exceptions", href: "/governance" },
    { perm: "package", label: "Build an audit package", href: "/packages" },
    { perm: "manage_ai", label: "Register an AI asset", href: "/ai" },
  ].filter((s) => can(s.perm));

  return (
    <section aria-labelledby="actions-h" className={`${PANEL} px-6 pb-3 pt-5 ${className}`}>
      <h2 id="actions-h" className="text-[18px] font-semibold leading-6 text-slate-900">Your actions</h2>
      {shortcuts.length ? (
        <ul className="mt-1 divide-y divide-slate-100">
          {shortcuts.map((q) => (
            <li key={q.label}>
              <Link href={q.href}
                className="group flex items-center justify-between gap-3 rounded py-3 text-[14px] text-slate-700 transition-colors duration-150 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600">
                {q.label}<ChevronRight size={16} aria-hidden className="shrink-0 text-slate-400 transition-[color,transform] duration-150 group-hover:translate-x-0.5 group-hover:text-teal-700" />
              </Link>
            </li>
          ))}
        </ul>
      ) : <p className="mt-2 pb-3 text-[13px] text-slate-500">Your role can view records but has no actions to take here.</p>}
    </section>
  );
}

/** The workspace's other sections this person can open, grouped as the sidebar groups them. */
export function WorkspaceSections({ className = "" }: { className?: string }) {
  const { me } = useAuth();
  const groups = orgNav(me?.permissions)
    .map((g) => ({ ...g, items: g.items.filter((i) => i.href !== "/") }))
    .filter((g) => g.items.length > 0);
  if (!groups.length) return null;
  return (
    <nav aria-labelledby="sections-h" className={`${PANEL} px-6 pb-4 pt-5 ${className}`}>
      <h2 id="sections-h" className="text-[18px] font-semibold leading-6 text-slate-900">Workspace sections</h2>
      <div className="mt-3 grid gap-x-6 gap-y-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-1">
        {groups.map((g) => (
          <div key={g.group} className="min-w-0">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-500">{g.group}</h3>
            <ul className="mt-1">
              {g.items.map(({ href, label, icon: Icon }) => (
                <li key={href}>
                  <Link href={href}
                    className="group flex items-center gap-2 rounded py-1.5 text-[14px] text-slate-700 transition-colors duration-150 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600">
                    <Icon size={15} aria-hidden className="shrink-0 text-slate-400 transition-colors duration-150 group-hover:text-teal-700" />
                    <span className="min-w-0 truncate">{label}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}
