"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, type ComponentType } from "react";
import { Lock } from "lucide-react";
import { personaOf, useAuth } from "@/lib/actor";
import { rolesText } from "@/lib/roles";
import { offeredTabs, openableScreens, orgEntry, orgTabOf, tabHref, type OrgNavItem, type OrgTab } from "./nav";
import { EmptyState, PageHeader, PageSection, TAB_CLASS } from "./ui";

/* A consolidated organisation page (components/nav.ts, an organisation sidebar entry with `tabs`): one heading, a row of
   tabs, and the selected tab's screen. Each tab is an existing screen component, unchanged, so its reads, actions,
   permission notes and loading, empty and error states are the ones it always had.

   The selected tab lives in the address (`?tab=<id>`; none = the first tab offered), so refresh, direct links and the
   browser's back and forward buttons all work, and the tabs are links. A tab is offered only when its screen's
   permissions (lib/screen-access.json) would be served; a tab named in the address that is not offered is reported,
   not loaded, so opening the page never records a refusal. The backend still authorises every request. */
export function WorkspaceHub({ href, panels }: { href: string; panels: Record<string, ComponentType> }) {
  const { me } = useAuth();
  const params = useSearchParams();
  const item = orgEntry(href);
  if (!item?.tabs) return null;
  const offered = offeredTabs(item, openableScreens(personaOf(me), me?.permissions));
  const named = item.tabs.find((t) => t.id === params.get("tab"));
  const tab = named ?? offered[0];
  const open = !!tab && offered.includes(tab);
  const Panel = open ? panels[tab.id] : undefined;
  return (
    <>
      <PageHeader title={item.label} documentTitle={open ? `${tab.label} · ${item.label}` : item.label} />
      {offered.length > 0 && <HubTabs item={item} tabs={offered} current={open ? tab : undefined} />}
      {Panel ? (
        <div className="za-fade-in" key={tab.id}><PageSection label={tab.label}><Panel /></PageSection></div>
      ) : (
        <div className="card">
          <EmptyState icon={Lock} title={`${named && !open ? named.label : item.label} isn't available to your roles`}
            description={<>Your roles ({rolesText(me) || "none"}) don&apos;t include reading it, so it was not loaded.
              {offered.length > 0 ? " The sections you can open are listed above." : " Ask an Organisation Admin if you need access."}</>} />
        </div>
      )}
    </>
  );
}

/** The consolidated page's tabs: links in a row that scrolls sideways on narrow screens (never the page), with the
 *  open tab marked by an underline and aria-current. */
function HubTabs({ item, tabs, current }: { item: OrgNavItem; tabs: OrgTab[]; current?: OrgTab }) {
  const list = useRef<HTMLUListElement>(null);
  // Keep the open tab in view where the row scrolls, without scrolling the page.
  useEffect(() => {
    const row = list.current, el = row?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!row || !el) return;
    if (el.offsetLeft < row.scrollLeft || el.offsetLeft + el.offsetWidth > row.scrollLeft + row.clientWidth) row.scrollLeft = el.offsetLeft - 16;
  }, [current]);
  return (
    <nav aria-label={`${item.label} sections`} className="mb-6">
      <ul ref={list} className="scroll-thin -mx-4 flex gap-6 overflow-x-auto px-4 shadow-[inset_0_-1px_0_var(--color-slate-200)] sm:mx-0 sm:px-0">
        {tabs.map((t) => {
          const on = t === current;
          return (
            <li key={t.id} className="shrink-0">
              <Link href={tabHref(item, t, tabs)} scroll={false} aria-current={on ? "page" : undefined} className={TAB_CLASS(on, false)}>
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** A former screen address that is now a tab of a consolidated page: it opens that tab in place of itself (replacing the
 *  history entry, so Back does not return here), keeping any other query parameters and the fragment. */
export function LegacyRedirect({ screen }: { screen: string }) {
  const router = useRouter();
  const target = orgTabOf(screen);
  const href = target?.href;
  useEffect(() => {
    if (!href) return;
    const [path, own] = href.split("?");
    const query = new URLSearchParams(own);
    new URLSearchParams(window.location.search).forEach((v, k) => { if (k !== "tab") query.append(k, v); });
    router.replace(`${path}?${query}${window.location.hash}`, { scroll: false });
  }, [href, router]);
  if (!target) return null;
  return (
    <p role="status" className="text-[13px] text-slate-500">
      {target.tab.label} is now part of {target.item.label}. Opening it… <Link href={target.href} className="font-medium text-navy-700 hover:underline">Open {target.item.label} › {target.tab.label}</Link>
    </p>
  );
}
